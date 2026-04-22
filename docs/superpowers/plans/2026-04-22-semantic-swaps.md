# Semantic Swaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace cycling prompts with a `GENERATION_MODE=semantic` path: each `/generate` call diverges from the prior 5 edits using a single multimodal OpenRouter request that returns both a modified image and a one-sentence caption; per-participant sessions rotate via an operator button or a 3-minute idle timer.

**Architecture:** A new `generation/semantic.py` owns the per-session state (captions sliding window + cached original image) and the request/response shape. `generation/openrouter.py` gains a semantic variant that accepts an injectable `httpx.AsyncClient` for testing. `generation/server.py` branches `/generate` on `GENERATION_MODE`, handles rotation through `/session/start`, and runs a background idle task. `backend/app/main.py` fans out a new `session_started` WebSocket event. Observer and feed pages gain a `caption` preference, an edit-history card, and a "New Participant" button.

**Tech Stack:** FastAPI, httpx (+ `httpx.MockTransport` for tests), Pillow, pytest, pytest-asyncio, vanilla ES-modules JavaScript.

**Spec:** `docs/superpowers/specs/2026-04-22-semantic-swaps-design.md`

---

## File map

| Path | Role | Action |
| --- | --- | --- |
| `generation/semantic.py` | `SemanticHistory`, `build_prompt`, `parse_response`, degenerate-caption helper | Create |
| `generation/openrouter.py` | Add `generate_with_openrouter_semantic`; make `client` injectable | Modify |
| `generation/session_manager.py` | `save_generation` gains `caption`; metadata root gains `mode` and `edit_history` | Modify |
| `generation/server.py` | Mode branch in `/generate`; rotation in `/session/start`; idle background task; notify backend | Modify |
| `generation/requirements.txt` | (unchanged) | — |
| `generation/requirements-dev.txt` | `pytest`, `pytest-asyncio` | Create |
| `generation/tests/__init__.py` | Package marker | Create |
| `generation/tests/conftest.py` | Fixtures: PNG bytes, fake OpenRouter response | Create |
| `generation/tests/test_semantic.py` | Unit tests for `SemanticHistory`, `build_prompt`, `parse_response` | Create |
| `generation/tests/test_openrouter_semantic.py` | `generate_with_openrouter_semantic` with `MockTransport` | Create |
| `generation/tests/test_session_manager.py` | `save_generation` with caption; backwards-compat | Create |
| `generation/tests/test_server_semantic.py` | Integration: `/generate` semantic branch + fallback | Create |
| `backend/app/main.py` | New `POST /events/session_started` | Modify |
| `frontend/public/observer.html` | Extra card + New Participant button | Modify |
| `frontend/public/observer.js` | Wire session_started handler, rotation button, edit history | Modify |
| `frontend/public/feed.js` | Prefer `caption`; reset on `session_started` | Modify |
| `example.env` | Add `GENERATION_MODE=cycling` with comment | Modify |
| `CLAUDE.md` | Mention semantic mode + idle reset | Modify |

---

## Task 1: Test scaffolding

**Files:**
- Create: `generation/requirements-dev.txt`
- Create: `generation/tests/__init__.py`
- Create: `generation/tests/conftest.py`
- Create: `generation/pytest.ini`

- [ ] **Step 1: Write `requirements-dev.txt`**

```
pytest>=8
pytest-asyncio>=0.23
```

- [ ] **Step 2: Write empty `generation/tests/__init__.py`**

```
```

- [ ] **Step 3: Write `generation/pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 4: Write `generation/tests/conftest.py`**

```python
"""Shared fixtures for generation-service tests."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image


def _tiny_png(color: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    img = Image.new("RGB", (16, 16), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def tiny_png_bytes() -> bytes:
    return _tiny_png()


@pytest.fixture
def tiny_png_b64(tiny_png_bytes: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(tiny_png_bytes).decode()}"


@pytest.fixture
def fake_openrouter_response(tiny_png_b64: str):
    """A minimal well-formed OpenRouter chat-completions payload."""
    def _factory(caption: str | None = "a new soap bubble in the corner") -> dict:
        content = f"CAPTION: {caption}" if caption is not None else "noop"
        return {
            "choices": [
                {
                    "message": {
                        "content": content,
                        "images": [
                            {"type": "image_url", "image_url": {"url": tiny_png_b64}}
                        ],
                    }
                }
            ]
        }

    return _factory
```

- [ ] **Step 5: Install dev deps locally for running tests**

Run: `pip install -r generation/requirements-dev.txt -r generation/requirements.txt`
Expected: clean install. If `fastapi`, `httpx`, `Pillow` already installed, pip reports `Requirement already satisfied`.

- [ ] **Step 6: Confirm pytest discovers the empty suite**

Run (from repo root): `cd generation && pytest -q`
Expected: `no tests ran in 0.0Xs`. Exit code 5 is acceptable (no tests collected).

- [ ] **Step 7: Commit**

```bash
git add generation/requirements-dev.txt generation/pytest.ini generation/tests/__init__.py generation/tests/conftest.py
git commit -m "test(generation): scaffold pytest suite"
```

---

## Task 2: `SemanticHistory` class

**Files:**
- Create: `generation/semantic.py`
- Create: `generation/tests/test_semantic.py`

- [ ] **Step 1: Write the failing tests for `SemanticHistory`**

Append to `generation/tests/test_semantic.py`:

```python
"""Tests for generation/semantic.py."""
from __future__ import annotations

from semantic import SemanticHistory


def test_history_empty_for_unknown_session():
    h = SemanticHistory()
    assert h.captions("sess-a") == []
    assert h.original("sess-a") is None


def test_append_captions_respects_window_of_five():
    h = SemanticHistory()
    for i in range(8):
        h.append("sess-a", f"edit {i}")
    assert h.captions("sess-a") == [f"edit {i}" for i in range(3, 8)]


def test_sessions_are_isolated():
    h = SemanticHistory()
    h.append("sess-a", "first")
    h.append("sess-b", "other")
    assert h.captions("sess-a") == ["first"]
    assert h.captions("sess-b") == ["other"]


def test_set_original_is_idempotent_per_session():
    h = SemanticHistory()
    h.set_original("sess-a", "data:image/png;base64,AAA")
    h.set_original("sess-a", "data:image/png;base64,BBB")  # second call is a no-op
    assert h.original("sess-a") == "data:image/png;base64,AAA"


def test_clear_drops_captions_and_original():
    h = SemanticHistory()
    h.append("sess-a", "edit")
    h.set_original("sess-a", "data:image/png;base64,AAA")
    h.clear("sess-a")
    assert h.captions("sess-a") == []
    assert h.original("sess-a") is None
```

- [ ] **Step 2: Run tests (expect ImportError)**

Run: `cd generation && pytest tests/test_semantic.py -q`
Expected: collection error / ImportError for `semantic`.

- [ ] **Step 3: Write minimal `generation/semantic.py`**

```python
"""Per-session state for the semantic generation mode."""
from __future__ import annotations

from dataclasses import dataclass, field

HISTORY_WINDOW = 5


@dataclass
class SemanticHistory:
    """In-memory, single-process state keyed by session id.

    Holds the most recent `HISTORY_WINDOW` edit captions plus the original
    (pre-edit) base image for each session. Persistence is explicitly not a
    requirement — a fair demo is single-process and a fresh session starts on
    process boot anyway.
    """

    _captions: dict[str, list[str]] = field(default_factory=dict)
    _originals: dict[str, str] = field(default_factory=dict)

    def captions(self, session_id: str) -> list[str]:
        return list(self._captions.get(session_id, []))

    def append(self, session_id: str, caption: str) -> None:
        bucket = self._captions.setdefault(session_id, [])
        bucket.append(caption)
        if len(bucket) > HISTORY_WINDOW:
            del bucket[: len(bucket) - HISTORY_WINDOW]

    def original(self, session_id: str) -> str | None:
        return self._originals.get(session_id)

    def set_original(self, session_id: str, image_b64: str) -> None:
        self._originals.setdefault(session_id, image_b64)

    def clear(self, session_id: str) -> None:
        self._captions.pop(session_id, None)
        self._originals.pop(session_id, None)
```

- [ ] **Step 4: Run tests**

Run: `cd generation && pytest tests/test_semantic.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add generation/semantic.py generation/tests/test_semantic.py
git commit -m "feat(generation): add SemanticHistory for per-session captions + originals"
```

---

## Task 3: `build_prompt`

**Files:**
- Modify: `generation/semantic.py`
- Modify: `generation/tests/test_semantic.py`

- [ ] **Step 1: Add failing tests**

Append to `generation/tests/test_semantic.py`:

```python
from semantic import build_prompt


def test_build_prompt_returns_two_images_then_text(tiny_png_b64):
    parts = build_prompt(
        original_b64=tiny_png_b64,
        current_b64=tiny_png_b64,
        captions=[],
        region=(0, 0, 100, 100),
        sector_name="TL",
    )
    assert len(parts) == 3
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"] == tiny_png_b64
    assert parts[1]["type"] == "image_url"
    assert parts[2]["type"] == "text"


def test_build_prompt_renders_region_bounds_and_sector(tiny_png_b64):
    parts = build_prompt(
        original_b64=tiny_png_b64,
        current_b64=tiny_png_b64,
        captions=[],
        region=(683, 0, 1024, 341),
        sector_name="TR",
    )
    text = parts[2]["text"]
    assert "(x1=683, y1=0, x2=1024, y2=341)" in text
    assert "TR" in text


def test_build_prompt_lists_captions_in_order(tiny_png_b64):
    parts = build_prompt(
        original_b64=tiny_png_b64,
        current_b64=tiny_png_b64,
        captions=["first edit", "second edit", "third edit"],
        region=(0, 0, 10, 10),
        sector_name="MC",
    )
    text = parts[2]["text"]
    assert '1. "first edit"' in text
    assert '2. "second edit"' in text
    assert '3. "third edit"' in text


def test_build_prompt_says_no_prior_edits_when_empty(tiny_png_b64):
    text = build_prompt(tiny_png_b64, tiny_png_b64, [], (0, 0, 1, 1), "TL")[2]["text"]
    assert "No prior edits yet" in text
```

- [ ] **Step 2: Run (expect failure)**

Run: `cd generation && pytest tests/test_semantic.py -q`
Expected: ImportError for `build_prompt`.

- [ ] **Step 3: Implement `build_prompt` in `generation/semantic.py`**

Append to `generation/semantic.py`:

```python
def build_prompt(
    original_b64: str,
    current_b64: str,
    captions: list[str],
    region: tuple[int, int, int, int],
    sector_name: str,
) -> list[dict]:
    """Assemble the `messages[0].content` payload for the semantic request."""
    if captions:
        history_block = "\n".join(
            f'  {i + 1}. "{caption}"' for i, caption in enumerate(captions)
        )
    else:
        history_block = "  (No prior edits yet.)"

    x1, y1, x2, y2 = region
    instruction = (
        "You are editing an image for a change-blindness installation. A "
        "participant is about to briefly look away from the region you are "
        "modifying; they should only notice the change if they come back to "
        "look at it directly.\n\n"
        "IMAGE 1 is the ORIGINAL, unedited scene.\n"
        "IMAGE 2 is the scene as it currently stands after several prior edits.\n\n"
        "Prior edits applied in this session (most recent last):\n"
        f"{history_block}\n\n"
        "Your task:\n"
        "- Propose ONE new edit, distinct in subject, scale, and style from every "
        "prior edit above. Do not repeat motifs, colours, or object classes that "
        "already appear.\n"
        "- The edit MUST be visually contained within the pixel rectangle "
        f"(x1={x1}, y1={y1}, x2={x2}, y2={y2}) - the {sector_name} sector of a "
        "3x3 grid.\n"
        "- The edit should make semantic sense given what is already in the "
        "scene - it should feel like it belongs, not like a pasted sticker.\n"
        "- Return the FULL modified image (not a crop), and a ONE-SENTENCE caption "
        'of exactly what you added or changed, prefixed with "CAPTION:". '
        "Example: CAPTION: a small paper boat now drifts across the puddle on the right."
    )

    return [
        {"type": "image_url", "image_url": {"url": original_b64}},
        {"type": "image_url", "image_url": {"url": current_b64}},
        {"type": "text", "text": instruction},
    ]
```

- [ ] **Step 4: Run tests**

Run: `cd generation && pytest tests/test_semantic.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add generation/semantic.py generation/tests/test_semantic.py
git commit -m "feat(generation): build_prompt for semantic swaps"
```

---

## Task 4: `parse_response` and degenerate caption

**Files:**
- Modify: `generation/semantic.py`
- Modify: `generation/tests/test_semantic.py`

- [ ] **Step 1: Add failing tests**

Append to `generation/tests/test_semantic.py`:

```python
from PIL import Image

from semantic import degenerate_caption, parse_response


def test_parse_response_extracts_image_and_caption(fake_openrouter_response):
    resp = fake_openrouter_response("added a rainbow glow")
    image, caption = parse_response(resp["choices"][0]["message"])
    assert isinstance(image, Image.Image)
    assert caption == "added a rainbow glow"


def test_parse_response_without_caption_returns_none_caption(
    fake_openrouter_response,
):
    resp = fake_openrouter_response(caption=None)  # content: "noop"
    image, caption = parse_response(resp["choices"][0]["message"])
    assert image is not None
    assert caption is None


def test_parse_response_missing_image_returns_none_image():
    image, caption = parse_response({"content": "CAPTION: nothing"})
    assert image is None
    assert caption == "nothing"


def test_parse_response_handles_list_content_with_caption(fake_openrouter_response):
    resp = fake_openrouter_response("the fern uncurled")
    message = resp["choices"][0]["message"]
    message["content"] = [{"type": "text", "text": "CAPTION: the fern uncurled"}]
    image, caption = parse_response(message)
    assert caption == "the fern uncurled"


def test_degenerate_caption_is_non_empty():
    c = degenerate_caption(index=4, sector_name="TL")
    assert "TL" in c
    assert "4" in c
```

- [ ] **Step 2: Run (expect failure)**

Run: `cd generation && pytest tests/test_semantic.py -q`
Expected: ImportError for `parse_response` / `degenerate_caption`.

- [ ] **Step 3: Implement in `generation/semantic.py`**

Append to `generation/semantic.py`:

```python
import time as _time
from typing import Tuple

from PIL import Image

from openrouter import _extract_image

CAPTION_PREFIX = "CAPTION:"


def _extract_caption(content) -> str | None:
    if isinstance(content, str):
        for line in content.splitlines():
            line = line.strip()
            if line.startswith(CAPTION_PREFIX):
                return line[len(CAPTION_PREFIX):].strip() or None
        return None
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                captured = _extract_caption(item.get("text", ""))
                if captured:
                    return captured
    return None


def parse_response(message: dict) -> Tuple[Image.Image | None, str | None]:
    """Return `(image_or_none, caption_or_none)` from a chat-completion message."""
    image = _extract_image(message)
    caption = _extract_caption(message.get("content", ""))
    return image, caption


def degenerate_caption(index: int, sector_name: str) -> str:
    """Fallback caption when the model returned an image but no CAPTION line."""
    return f"edit {index} in {sector_name} at {_time.strftime('%H:%M:%S')}"
```

- [ ] **Step 4: Run tests**

Run: `cd generation && pytest tests/test_semantic.py -q`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add generation/semantic.py generation/tests/test_semantic.py
git commit -m "feat(generation): parse_response and degenerate_caption helpers"
```

---

## Task 5: Injectable client + `generate_with_openrouter_semantic`

**Files:**
- Modify: `generation/openrouter.py`
- Create: `generation/tests/test_openrouter_semantic.py`

- [ ] **Step 1: Refactor `openrouter.py` to accept an optional client**

Replace the full body of `generate_with_openrouter` in `generation/openrouter.py` with:

```python
async def _post_chat(payload: dict, api_key: str, client: httpx.AsyncClient) -> dict:
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")
    response = await client.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ubicomp-capstone",
        },
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter API error: {response.status_code} - {response.text}")
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("No choices in OpenRouter response")
    return choices[0].get("message", {})


async def _with_client(client: httpx.AsyncClient | None):
    """Context manager yielding a client; owns cleanup only when we created it."""
    if client is not None:
        return client, False
    return httpx.AsyncClient(timeout=120.0), True


async def generate_with_openrouter(
    image: Image.Image,
    prompt: str,
    region: tuple[int, int, int, int],
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> Image.Image:
    """Cycling-mode generation: one image in, one image out."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    payload = {
        "model": IMAGE_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                {
                    "type": "text",
                    "text": (
                        f"Modify this image by adding or changing something in the region "
                        f"from pixel ({region[0]}, {region[1]}) to ({region[2]}, {region[3]}). "
                        f"The modification should be: {prompt}. Return the complete modified image."
                    ),
                },
            ],
        }],
        "modalities": ["image", "text"],
    }
    owned, created = await _with_client(client)
    try:
        message = await _post_chat(payload, api_key, owned)
    finally:
        if created:
            await owned.aclose()
    image_out = _extract_image(message)
    if image_out is None:
        raise RuntimeError("Model did not return an image")
    return image_out


async def generate_with_openrouter_semantic(
    content_parts: list[dict],
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Semantic-mode request. Returns the raw `message` dict so the caller can
    parse image + caption itself."""
    payload = {
        "model": IMAGE_MODEL,
        "messages": [{"role": "user", "content": content_parts}],
        "modalities": ["image", "text"],
    }
    owned, created = await _with_client(client)
    try:
        return await _post_chat(payload, api_key, owned)
    finally:
        if created:
            await owned.aclose()
```

- [ ] **Step 2: Write failing tests**

Write `generation/tests/test_openrouter_semantic.py`:

```python
"""Tests for the semantic OpenRouter call using httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest

from openrouter import generate_with_openrouter_semantic
from semantic import build_prompt, parse_response


def _mock_transport(status: int, body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_semantic_request_returns_message_on_success(
    tiny_png_b64, fake_openrouter_response,
):
    response_body = fake_openrouter_response("a monarch butterfly drifted in")
    transport = _mock_transport(200, response_body)
    async with httpx.AsyncClient(transport=transport) as client:
        parts = build_prompt(tiny_png_b64, tiny_png_b64, [], (0, 0, 10, 10), "TL")
        message = await generate_with_openrouter_semantic(parts, "fake-key", client=client)
        image, caption = parse_response(message)
    assert image is not None
    assert caption == "a monarch butterfly drifted in"


@pytest.mark.asyncio
async def test_semantic_request_raises_on_500(tiny_png_b64):
    transport = _mock_transport(500, {"error": "boom"})
    async with httpx.AsyncClient(transport=transport) as client:
        parts = build_prompt(tiny_png_b64, tiny_png_b64, [], (0, 0, 10, 10), "TL")
        with pytest.raises(RuntimeError, match="OpenRouter API error: 500"):
            await generate_with_openrouter_semantic(parts, "fake-key", client=client)


@pytest.mark.asyncio
async def test_semantic_request_rejects_missing_api_key(tiny_png_b64):
    transport = _mock_transport(200, {"choices": [{"message": {}}]})
    async with httpx.AsyncClient(transport=transport) as client:
        parts = build_prompt(tiny_png_b64, tiny_png_b64, [], (0, 0, 10, 10), "TL")
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY not set"):
            await generate_with_openrouter_semantic(parts, "", client=client)


@pytest.mark.asyncio
async def test_semantic_payload_carries_two_images_and_instructions(tiny_png_b64):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "CAPTION: x", "images": [{"type": "image_url", "image_url": {"url": tiny_png_b64}}]}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        parts = build_prompt(tiny_png_b64, tiny_png_b64, ["prior edit"], (0, 0, 10, 10), "TL")
        await generate_with_openrouter_semantic(parts, "fake-key", client=client)

    content = captured["body"]["messages"][0]["content"]
    image_parts = [p for p in content if p["type"] == "image_url"]
    assert len(image_parts) == 2
    assert "prior edit" in [p for p in content if p["type"] == "text"][0]["text"]
```

- [ ] **Step 3: Run**

Run: `cd generation && pytest tests/test_openrouter_semantic.py -q`
Expected: 4 passed.

- [ ] **Step 4: Sanity-check existing cycling tests still pass**

Run: `cd generation && pytest -q`
Expected: 18 passed (14 from task 4 + 4 here). No regressions.

- [ ] **Step 5: Commit**

```bash
git add generation/openrouter.py generation/tests/test_openrouter_semantic.py
git commit -m "feat(generation): semantic OpenRouter variant + injectable client"
```

---

## Task 6: Extend `SessionManager` with caption, mode, edit_history

**Files:**
- Modify: `generation/session_manager.py`
- Create: `generation/tests/test_session_manager.py`

- [ ] **Step 1: Write failing tests**

`generation/tests/test_session_manager.py`:

```python
"""Tests for SessionManager metadata extensions."""
from __future__ import annotations

import json

from PIL import Image

from session_manager import SessionManager


def _tiny_image() -> Image.Image:
    return Image.new("RGB", (8, 8), (1, 2, 3))


def test_start_session_records_mode_when_passed_in_runtime(tmp_path):
    sm = SessionManager(tmp_path)
    sid = sm.start_new_session(runtime={"mode": "semantic"})
    metadata = json.loads((tmp_path / sid / "metadata.json").read_text())
    assert metadata["runtime"]["mode"] == "semantic"
    assert metadata["edit_history"] == []


def test_save_generation_persists_caption(tmp_path):
    sm = SessionManager(tmp_path)
    sid = sm.start_new_session(runtime={"mode": "semantic"})
    entry = sm.save_generation(
        _tiny_image(), "TL", "raw prompt", "BR",
        caption="a bird landed on the ledge",
    )
    assert entry["caption"] == "a bird landed on the ledge"

    metadata = json.loads((tmp_path / sid / "metadata.json").read_text())
    assert metadata["sequence"][0]["caption"] == "a bird landed on the ledge"
    assert metadata["edit_history"] == ["a bird landed on the ledge"]


def test_edit_history_is_capped_at_five(tmp_path):
    sm = SessionManager(tmp_path)
    sm.start_new_session(runtime={"mode": "semantic"})
    for i in range(7):
        sm.save_generation(_tiny_image(), "TL", "p", "BR", caption=f"c{i}")
    assert sm.metadata["edit_history"] == ["c2", "c3", "c4", "c5", "c6"]


def test_save_generation_without_caption_still_records_entry(tmp_path):
    sm = SessionManager(tmp_path)
    sm.start_new_session(runtime={"mode": "cycling"})
    entry = sm.save_generation(_tiny_image(), "TL", "p", "BR")
    assert entry["caption"] is None
    assert sm.metadata["edit_history"] == []


def test_load_session_accepts_legacy_metadata_without_edit_history(tmp_path):
    sid = "legacy-session"
    folder = tmp_path / sid
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({
        "session_id": sid,
        "created_at": 0,
        "sequence": [{"index": 0, "filename": "x.png", "target_sector": "TL",
                       "focus_sector": "BR", "prompt": "p", "timestamp": 0}],
    }))
    sm = SessionManager(tmp_path)
    metadata = sm.load_session(sid)
    assert metadata["sequence"][0]["prompt"] == "p"
```

- [ ] **Step 2: Run (expect failure)**

Run: `cd generation && pytest tests/test_session_manager.py -q`
Expected: `caption` entries missing; `edit_history` missing → 4 failures.

- [ ] **Step 3: Modify `generation/session_manager.py`**

In `start_new_session`, replace the `self.metadata = { ... }` block with:

```python
        self.metadata = {
            "session_id": session_id,
            "created_at": time.time(),
            "participant_id": participant_id,
            "runtime": runtime or {},
            "stats": {"blink_count": 0, "frame_drops": 0},
            "calibration": None,
            "edit_history": [],
            "sequence": [],
        }
```

Replace `save_generation` signature and body with:

```python
    def save_generation(
        self,
        image: Image.Image,
        sector_name: str,
        prompt: str,
        focus_sector: str,
        latency_ms: Optional[float] = None,
        caption: Optional[str] = None,
    ) -> Dict:
        """Save a generated image and its metadata."""
        if not self.current_session_dir:
            raise ValueError("No active session. Call start_new_session() first.")

        filename = f"{self.sequence_index:04d}_{sector_name}.png"
        image_path = self.current_session_dir / filename
        image.save(image_path, "PNG")

        entry = {
            "index": self.sequence_index,
            "filename": filename,
            "target_sector": sector_name,
            "focus_sector": focus_sector,
            "prompt": prompt,
            "caption": caption,
            "timestamp": time.time(),
            "latency_ms": latency_ms,
        }

        self.metadata["sequence"].append(entry)
        if caption:
            history = self.metadata.setdefault("edit_history", [])
            history.append(caption)
            if len(history) > 5:
                del history[: len(history) - 5]
        self._save_metadata()

        self.sequence_index += 1
        lat = f", {latency_ms:.0f}ms" if latency_ms is not None else ""
        print(f"Saved generation {self.sequence_index}: {sector_name}{lat}")
        return entry
```

- [ ] **Step 4: Run tests**

Run: `cd generation && pytest tests/test_session_manager.py -q`
Expected: 5 passed.

- [ ] **Step 5: Run full suite**

Run: `cd generation && pytest -q`
Expected: 23 passed.

- [ ] **Step 6: Commit**

```bash
git add generation/session_manager.py generation/tests/test_session_manager.py
git commit -m "feat(generation): persist caption and edit_history in session metadata"
```

---

## Task 7: Wire the semantic branch in `/generate`

**Files:**
- Modify: `generation/server.py`
- Create: `generation/tests/test_server_semantic.py`

- [ ] **Step 1: Failing integration test**

Write `generation/tests/test_server_semantic.py`:

```python
"""Integration tests for the /generate semantic branch."""
from __future__ import annotations

import base64
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient


def _png_b64() -> str:
    from PIL import Image
    import io
    img = Image.new("RGB", (24, 24), (9, 9, 9))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def semantic_app(monkeypatch, tmp_path):
    """Build a fresh FastAPI app in semantic mode with a tmp sessions dir."""
    monkeypatch.setenv("GENERATION_MODE", "semantic")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    # Re-import so module-level env reads pick up the overrides.
    import importlib, server
    importlib.reload(server)
    server.SESSIONS_DIR = tmp_path
    server.session_manager.sessions_dir = tmp_path
    yield server


def _mock_transport(responses):
    calls = iter(responses)
    def handler(request):
        status, body = next(calls)
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


def test_semantic_generate_saves_caption_and_advances_history(
    semantic_app, fake_openrouter_response,
):
    transport = _mock_transport([(200, fake_openrouter_response("added a rainbow"))])
    with patch("openrouter.httpx.AsyncClient", lambda *a, **kw: httpx.AsyncClient(transport=transport)):
        with TestClient(semantic_app.app) as client:
            resp = client.post("/generate", json={
                "image_base64": _png_b64(),
                "focus_x": 0.5, "focus_y": 0.5,
                "target_row": 0, "target_col": 0, "grid_size": 3,
            })
    assert resp.status_code == 200
    session_id = semantic_app.session_manager.current_session_id
    metadata_path = semantic_app.SESSIONS_DIR / session_id / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    assert metadata["sequence"][0]["caption"] == "added a rainbow"
    assert metadata["edit_history"] == ["added a rainbow"]
    assert semantic_app.semantic_history.captions(session_id) == ["added a rainbow"]


def test_semantic_generate_falls_back_to_cycling_on_500(semantic_app):
    """Two back-to-back 500s trigger retry then cycling fallback; caption empty."""
    transport = _mock_transport([
        (500, {"error": "first"}),
        (500, {"error": "retry"}),
        # cycling fallback call:
        (200, {"choices": [{"message": {"images": [
            {"type": "image_url", "image_url": {"url": _png_b64()}}
        ]}}]}),
    ])
    with patch("openrouter.httpx.AsyncClient", lambda *a, **kw: httpx.AsyncClient(transport=transport)):
        with TestClient(semantic_app.app) as client:
            resp = client.post("/generate", json={
                "image_base64": _png_b64(),
                "focus_x": 0.5, "focus_y": 0.5,
                "target_row": 0, "target_col": 0, "grid_size": 3,
            })
    assert resp.status_code == 200
    session_id = semantic_app.session_manager.current_session_id
    metadata = json.loads((semantic_app.SESSIONS_DIR / session_id / "metadata.json").read_text())
    assert metadata["sequence"][0]["caption"] is None
    assert metadata["edit_history"] == []  # no semantic success → history unchanged


def test_semantic_generate_synthesises_caption_when_model_omits_it(
    semantic_app, fake_openrouter_response,
):
    transport = _mock_transport([(200, fake_openrouter_response(caption=None))])
    with patch("openrouter.httpx.AsyncClient", lambda *a, **kw: httpx.AsyncClient(transport=transport)):
        with TestClient(semantic_app.app) as client:
            client.post("/generate", json={
                "image_base64": _png_b64(),
                "focus_x": 0.5, "focus_y": 0.5,
                "target_row": 0, "target_col": 0, "grid_size": 3,
            })
    session_id = semantic_app.session_manager.current_session_id
    metadata = json.loads((semantic_app.SESSIONS_DIR / session_id / "metadata.json").read_text())
    caption = metadata["sequence"][0]["caption"]
    assert caption is not None
    assert "TL" in caption
```

- [ ] **Step 2: Run (expect failure — semantic branch doesn't exist yet)**

Run: `cd generation && pytest tests/test_server_semantic.py -q`
Expected: 3 failures. Typical error: `module 'server' has no attribute 'semantic_history'`.

- [ ] **Step 3: Add `GENERATION_MODE`, `SEMANTIC_HISTORY`, and branch to `server.py`**

Near the top of `server.py`, under the other env vars, add:

```python
GENERATION_MODE = os.getenv("GENERATION_MODE", "cycling").lower()
```

Next to the other singletons, add:

```python
from semantic import (
    SemanticHistory,
    build_prompt,
    degenerate_caption,
    parse_response,
)

semantic_history = SemanticHistory()
```

Update `_runtime_snapshot`:

```python
def _runtime_snapshot() -> dict[str, Any]:
    return {
        "image_model": IMAGE_MODEL,
        "mode": GENERATION_MODE,
        "pupil_surface_name": PUPIL_SURFACE_NAME,
        "pupil_confidence_threshold": PUPIL_CONFIDENCE_THRESHOLD,
        "grid_size": GRID_SIZE,
    }
```

In `startup_event`, add `global GENERATION_MODE` at the top of the function body and then, after the existing `session_id = ...` line, add:

```python
    global GENERATION_MODE
    if GENERATION_MODE == "semantic" and not OPENROUTER_API_KEY:
        print("GENERATION_MODE=semantic but OPENROUTER_API_KEY missing; coercing to cycling")
        GENERATION_MODE = "cycling"
```

Replace the body of `generate()` starting at the `if OPENROUTER_API_KEY:` block with the full branching implementation:

```python
    async def _run_semantic() -> tuple[Image.Image, str | None]:
        """Two-attempt semantic call; raises on terminal failure."""
        session_id = session_manager.current_session_id or "anon"
        semantic_history.set_original(session_id, request.image_base64)
        captions = semantic_history.captions(session_id)
        parts = build_prompt(
            original_b64=semantic_history.original(session_id) or request.image_base64,
            current_b64=request.image_base64,
            captions=captions,
            region=region,
            sector_name=target,
        )
        from openrouter import generate_with_openrouter_semantic
        try:
            message = await generate_with_openrouter_semantic(parts, OPENROUTER_API_KEY)
        except Exception as first_err:
            print(f"semantic first attempt failed: {first_err} - retrying with terser prompt")
            parts[-1]["text"] = (
                "Propose one new small edit distinct from any prior edit, inside the "
                f"pixel rectangle (x1={region[0]}, y1={region[1]}, x2={region[2]}, "
                f"y2={region[3]}). Return the full modified image and a one-sentence "
                'CAPTION: describing what you changed.'
            )
            message = await generate_with_openrouter_semantic(parts, OPENROUTER_API_KEY)
        image, caption = parse_response(message)
        if image is None:
            raise RuntimeError("Semantic response had no image")
        return image, caption

    caption: str | None = None
    semantic_success = False
    duplicate_caption = False
    if GENERATION_MODE == "semantic" and OPENROUTER_API_KEY:
        try:
            generated_image, caption = await _run_semantic()
            semantic_success = True
            session_id = session_manager.current_session_id or "anon"
            prior = [c.lower() for c in semantic_history.captions(session_id)]
            if caption and any(caption.lower() in p or p in caption.lower() for p in prior):
                duplicate_caption = True
            if caption is None:
                caption = degenerate_caption(session_manager.sequence_index, target)
        except Exception as sem_err:
            print(f"semantic mode failed; falling back to cycling: {sem_err}")

    if not semantic_success:
        if OPENROUTER_API_KEY:
            try:
                generated_image = await generate_with_openrouter(
                    init_image, prompt, region, OPENROUTER_API_KEY,
                )
            except Exception as api_err:
                print(f"OpenRouter API failed: {api_err}")
                generated_image = init_image
        else:
            print("No API key set - returning original image")
            generated_image = init_image
```

Update the save call to pass `caption`:

```python
    latency_ms = (time.perf_counter() - t_start) * 1000
    entry: dict = {}
    if session_manager.current_session_id:
        entry = session_manager.save_generation(
            generated_image, target, prompt, focus_sector,
            latency_ms=latency_ms, caption=caption,
            duplicate_caption=duplicate_caption,
        )
        await _notify_backend(entry)
        if semantic_success and caption:
            semantic_history.append(session_manager.current_session_id, caption)
```

Extend `SessionManager.save_generation` to accept `duplicate_caption: bool = False` and include it in the entry only when true. In `generation/session_manager.py`, update the signature and body of `save_generation`:

```python
    def save_generation(
        self,
        image: Image.Image,
        sector_name: str,
        prompt: str,
        focus_sector: str,
        latency_ms: Optional[float] = None,
        caption: Optional[str] = None,
        duplicate_caption: bool = False,
    ) -> Dict:
        # ... existing body up to `entry = {...}` ...
        entry = {
            "index": self.sequence_index,
            "filename": filename,
            "target_sector": sector_name,
            "focus_sector": focus_sector,
            "prompt": prompt,
            "caption": caption,
            "timestamp": time.time(),
            "latency_ms": latency_ms,
        }
        if duplicate_caption:
            entry["duplicate_caption"] = True
        # ... remainder unchanged ...
```

Add one more test for the flag to `generation/tests/test_server_semantic.py` (append, adjust `_mock_transport` list to deliver two successes with the same caption):

```python
def test_duplicate_caption_is_flagged(semantic_app, fake_openrouter_response):
    body = fake_openrouter_response("a monarch butterfly appeared")
    transport = _mock_transport([(200, body), (200, body)])
    with patch("openrouter.httpx.AsyncClient", lambda *a, **kw: httpx.AsyncClient(transport=transport)):
        with TestClient(semantic_app.app) as client:
            for _ in range(2):
                client.post("/generate", json={
                    "image_base64": _png_b64(),
                    "focus_x": 0.5, "focus_y": 0.5,
                    "target_row": 0, "target_col": 0, "grid_size": 3,
                })
    session_id = semantic_app.session_manager.current_session_id
    metadata = json.loads((semantic_app.SESSIONS_DIR / session_id / "metadata.json").read_text())
    assert metadata["sequence"][0].get("duplicate_caption") is not True
    assert metadata["sequence"][1].get("duplicate_caption") is True
```

Update the outgoing backend notification: entries now carry `caption`, so the existing `**entry` splat already forwards it. No change there.

- [ ] **Step 4: Run tests**

Run: `cd generation && pytest tests/test_server_semantic.py -q`
Expected: 4 passed.

- [ ] **Step 5: Run full suite**

Run: `cd generation && pytest -q`
Expected: 27 passed.

- [ ] **Step 6: Commit**

```bash
git add generation/server.py generation/tests/test_server_semantic.py
git commit -m "feat(generation): GENERATION_MODE=semantic branch with terse retry + cycling fallback"
```

---

## Task 8: `/session/start` rotates semantic history + notifies backend

**Files:**
- Modify: `generation/server.py`
- Modify: `backend/app/main.py`
- Modify: `generation/tests/test_server_semantic.py`

- [ ] **Step 1: Add failing test for rotation**

Append to `generation/tests/test_server_semantic.py`:

```python
def test_session_start_clears_semantic_history(semantic_app, fake_openrouter_response):
    transport = _mock_transport([(200, fake_openrouter_response("first edit"))])
    with patch("openrouter.httpx.AsyncClient", lambda *a, **kw: httpx.AsyncClient(transport=transport)):
        with TestClient(semantic_app.app) as client:
            client.post("/generate", json={
                "image_base64": _png_b64(),
                "focus_x": 0.5, "focus_y": 0.5,
                "target_row": 0, "target_col": 0, "grid_size": 3,
            })
            old_sid = semantic_app.session_manager.current_session_id
            assert semantic_app.semantic_history.captions(old_sid) == ["first edit"]

            resp = client.post(
                "/session/start",
                json={"participant_id": "participant-42"},
            )
    assert resp.status_code == 200
    new_sid = semantic_app.session_manager.current_session_id
    assert new_sid != old_sid
    assert semantic_app.semantic_history.captions(old_sid) == []
    assert semantic_app.semantic_history.captions(new_sid) == []
```

- [ ] **Step 2: Run (expect failure — old session's captions still present)**

Run: `cd generation && pytest tests/test_server_semantic.py::test_session_start_clears_semantic_history -q`
Expected: assertion error on the `captions(old_sid) == []` line.

- [ ] **Step 3: Modify `/session/start` in `server.py`**

Replace the existing `start_session` handler with:

```python
@app.post("/session/start")
async def start_session(req: Optional[StartSessionRequest] = None) -> dict:
    req = req or StartSessionRequest()
    prev_sid = session_manager.current_session_id
    sid = session_manager.start_new_session(
        session_id=req.session_id,
        participant_id=req.participant_id,
        runtime=_runtime_snapshot(),
    )
    if prev_sid and prev_sid != sid:
        semantic_history.clear(prev_sid)
    if _backend_client is not None:
        try:
            await _backend_client.post(
                f"{BACKEND_URL}/events/session_started",
                json={"session_id": sid, "participant_id": req.participant_id},
                timeout=2.0,
            )
        except Exception as exc:
            print(f"session_started relay failed: {exc}")
    return {"session_id": sid, "status": "recording", "participant_id": req.participant_id}
```

- [ ] **Step 4: Run test**

Run: `cd generation && pytest tests/test_server_semantic.py -q`
Expected: 4 passed.

- [ ] **Step 5: Add backend `/events/session_started` endpoint**

In `backend/app/main.py`, add next to the existing relay endpoints:

```python
@app.post("/events/session_started")
async def relay_session_started(event: dict[str, Any]) -> dict[str, Any]:
    """Generation service pings us on participant rollover so observer+feed can
    reset their local state."""
    await stream_hub.broadcast({"event": "session_started", **event})
    return {"ok": True}
```

- [ ] **Step 6: Syntax-check backend**

Run: `python3 -m py_compile backend/app/main.py`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add generation/server.py generation/tests/test_server_semantic.py backend/app/main.py
git commit -m "feat: rotate semantic history on /session/start + relay to backend"
```

---

## Task 9: Idle auto-reset background task

**Files:**
- Modify: `generation/server.py`
- Modify: `generation/tests/test_server_semantic.py`

- [ ] **Step 1: Failing test for idle reset**

Append to `generation/tests/test_server_semantic.py`:

```python
import time


async def test_idle_reset_rotates_session_when_stale(semantic_app):
    from server import _maybe_idle_reset
    sm = semantic_app.session_manager
    first = sm.current_session_id
    now = time.time()
    semantic_app._last_generation_ts = now - 240
    semantic_app._last_session_started_ts = now - 240

    await _maybe_idle_reset()
    assert sm.current_session_id != first


async def test_idle_reset_is_noop_when_recent(semantic_app):
    from server import _maybe_idle_reset
    sm = semantic_app.session_manager
    first = sm.current_session_id
    now = time.time()
    semantic_app._last_generation_ts = now - 30
    semantic_app._last_session_started_ts = now - 30
    await _maybe_idle_reset()
    assert sm.current_session_id == first
```

- [ ] **Step 2: Run (expect ImportError)**

Run: `cd generation && pytest tests/test_server_semantic.py::test_idle_reset_rotates_session_when_stale -q`
Expected: ImportError for `_maybe_idle_reset`.

- [ ] **Step 3: Add idle task plumbing to `server.py`**

Near the other module-level state add:

```python
IDLE_THRESHOLD_SEC = int(os.getenv("IDLE_RESET_SEC", "180"))
IDLE_TICK_SEC = 30

_last_generation_ts = time.time()
_last_session_started_ts = time.time()
_idle_task: asyncio.Task | None = None
_idle_lock = asyncio.Lock()
```

At the top of the file add `import asyncio` if not already present.

In `generate()`, update `_last_generation_ts` at the very end just before the `return Response(...)`:

```python
    global _last_generation_ts
    _last_generation_ts = time.time()
```

In the rewritten `start_session` (from Task 8), at the end before `return`, update `_last_session_started_ts`:

```python
    global _last_session_started_ts
    _last_session_started_ts = time.time()
```

Add the helper and background loop:

```python
async def _maybe_idle_reset() -> None:
    """If neither /generate nor /session/start has fired for IDLE_THRESHOLD_SEC,
    rotate to a new session. Skips if a generation is in flight (we can tell
    because the lock will be held by /generate)."""
    async with _idle_lock:
        now = time.time()
        stale = (
            now - _last_generation_ts > IDLE_THRESHOLD_SEC
            and now - _last_session_started_ts > IDLE_THRESHOLD_SEC
        )
        if not stale:
            return
        # Equivalent to POST /session/start with no participant_id:
        prev_sid = session_manager.current_session_id
        sid = session_manager.start_new_session(runtime=_runtime_snapshot())
        if prev_sid and prev_sid != sid:
            semantic_history.clear(prev_sid)
        global _last_session_started_ts
        _last_session_started_ts = now
        if _backend_client is not None:
            try:
                await _backend_client.post(
                    f"{BACKEND_URL}/events/session_started",
                    json={"session_id": sid, "participant_id": None},
                    timeout=2.0,
                )
            except Exception as exc:
                print(f"idle session_started relay failed: {exc}")
        print(f"Idle rotation: new session {sid}")


async def _idle_watcher() -> None:
    while True:
        await asyncio.sleep(IDLE_TICK_SEC)
        try:
            await _maybe_idle_reset()
        except Exception as exc:
            print(f"idle watcher error: {exc}")
```

In `startup_event`, start the task:

```python
    global _idle_task
    _idle_task = asyncio.create_task(_idle_watcher())
```

In `shutdown_event`:

```python
    if _idle_task is not None:
        _idle_task.cancel()
```

Wrap the pre-existing `generate()` body in `async with _idle_lock:` (entire `/generate` handler) so the idle task cannot fire mid-generation. The outermost structure becomes:

```python
@app.post("/generate")
async def generate(request: GenerateRequest) -> Response:
    async with _idle_lock:
        t_start = time.perf_counter()
        # ... existing body unchanged ...
        global _last_generation_ts
        _last_generation_ts = time.time()
        return Response(...)
```

- [ ] **Step 4: Run test**

Run: `cd generation && pytest tests/test_server_semantic.py -q`
Expected: 7 passed.

- [ ] **Step 5: Run full suite**

Run: `cd generation && pytest -q`
Expected: 29 passed.

- [ ] **Step 6: Commit**

```bash
git add generation/server.py generation/tests/test_server_semantic.py
git commit -m "feat(generation): 3-minute idle auto-reset background task"
```

---

## Task 10: Observer UI — New Participant button + Edit history + session_started reset

**Files:**
- Modify: `frontend/public/observer.html`
- Modify: `frontend/public/observer.js`

- [ ] **Step 1: Add UI elements to `observer.html`**

In the sidebar, rename "Last prompt" to "Last edit" and add two new cards + a button. Replace the existing `.sidebar` div contents with:

```html
    <div class="sidebar">
      <div class="card">
        <h3>Participant</h3>
        <div style="display:flex; gap:8px; align-items:center;">
          <input id="participant-input" type="text" placeholder="id (optional)"
                 style="flex:1; padding:8px; border-radius:6px; border:1px solid rgba(255,255,255,0.15); background:#05070f; color:inherit;" />
          <button id="new-participant-btn"
                  style="padding:8px 12px; border:0; border-radius:6px; background:#00e5ff; color:#05070f; font-weight:600; cursor:pointer;">
            New
          </button>
        </div>
        <div id="participant-banner" style="margin-top:8px; font-size:12px; opacity:0; transition:opacity 300ms;"></div>
      </div>
      <div class="card">
        <h3>Generations</h3>
        <div class="stat" id="stat-generations">0</div>
      </div>
      <div class="card">
        <h3>Swaps</h3>
        <div class="stat">
          <span id="stat-swaps">0</span>
          <span class="sub">caught <span id="stat-caught">0</span></span>
        </div>
      </div>
      <div class="card">
        <h3>Last edit</h3>
        <div class="prompt" id="last-caption">&mdash;</div>
      </div>
      <div class="card">
        <h3>Edit history</h3>
        <ol id="edit-history" style="margin:0; padding-left:20px; font-size:13px; line-height:1.5; max-height:180px; overflow-y:auto;"></ol>
      </div>
      <div class="card">
        <h3>Last generation</h3>
        <div class="mini" id="last-image"></div>
      </div>
    </div>
```

- [ ] **Step 2: Wire the button + history + reset in `observer.js`**

Replace the full body of `observer.js` with:

```javascript
import { API_ROOT, loadRuntimeConfig } from "./config.js";
import { openStream } from "./ws.js";
import { gazeToSector, sectorName } from "./sectors.js";

const DETECTION_WINDOW_MS = 1500;
const EDIT_HISTORY_MAX = 10;

const stage = document.getElementById("stage");
const grid = document.getElementById("grid");
const cursor = document.getElementById("cursor");
const lastCaptionEl = document.getElementById("last-caption");
const editHistoryEl = document.getElementById("edit-history");
const lastImageEl = document.getElementById("last-image");
const statGenerations = document.getElementById("stat-generations");
const statSwaps = document.getElementById("stat-swaps");
const statCaught = document.getElementById("stat-caught");
const participantInput = document.getElementById("participant-input");
const newParticipantBtn = document.getElementById("new-participant-btn");
const participantBanner = document.getElementById("participant-banner");

async function main() {
  const config = await loadRuntimeConfig();
  const cells = buildGrid(config.grid_size);
  let state = initialState();

  newParticipantBtn.addEventListener("click", async () => {
    const id = participantInput.value.trim() || null;
    try {
      await fetch(`${config.generation_api}/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ participant_id: id }),
      });
    } catch (err) {
      console.warn("New Participant POST failed", err);
    }
  });

  function resetForNewSession(participantId) {
    state = initialState();
    statGenerations.textContent = "0";
    statSwaps.textContent = "0";
    statCaught.textContent = "0";
    lastCaptionEl.textContent = "—";
    editHistoryEl.innerHTML = "";
    lastImageEl.style.backgroundImage = "";
    for (const row of cells) for (const cell of row) cell.classList.remove("focus", "modified");
    participantBanner.textContent = participantId
      ? `New participant: ${participantId}`
      : "New participant";
    participantBanner.style.opacity = "1";
    setTimeout(() => (participantBanner.style.opacity = "0"), 2000);
  }

  function setFocus(sector) {
    if (state.focusSector) cells[state.focusSector.row][state.focusSector.col].classList.remove("focus");
    state.focusSector = sector;
    if (state.focusSector) cells[state.focusSector.row][state.focusSector.col].classList.add("focus");
  }

  function flashModified(sector) {
    const cell = cells[sector.row][sector.col];
    cell.classList.add("modified");
    setTimeout(() => cell.classList.remove("modified"), 1200);
  }

  function positionCursor(smoothed, stale) {
    const rect = stage.getBoundingClientRect();
    cursor.style.left = `${smoothed.x_norm * rect.width}px`;
    cursor.style.top = `${smoothed.y_norm * rect.height}px`;
    cursor.style.display = "block";
    cursor.classList.toggle("stale", stale);
  }

  function pushEditHistory(caption) {
    if (!caption) return;
    const li = document.createElement("li");
    li.textContent = caption;
    editHistoryEl.prepend(li);
    while (editHistoryEl.children.length > EDIT_HISTORY_MAX) {
      editHistoryEl.lastChild.remove();
    }
  }

  openStream({
    sample: ({ gaze }) => {
      if (!gaze) return;
      if (gaze.valid) state.lastValidTs = Date.now();
      const stale = Date.now() - state.lastValidTs > config.gaze_stale_ms;
      if (!gaze.valid || stale) {
        cursor.style.display = "none";
        setFocus(null);
        return;
      }
      positionCursor(gaze, false);
      const sector = gazeToSector(gaze, config.grid_size);
      if (!state.focusSector
          || sector.row !== state.focusSector.row
          || sector.col !== state.focusSector.col) {
        setFocus(sector);
        if (state.pendingDetection
            && sector.row === state.pendingDetection.targetSector.row
            && sector.col === state.pendingDetection.targetSector.col
            && Date.now() < state.pendingDetection.deadline) {
          state.caught += 1;
          statCaught.textContent = state.caught;
          state.pendingDetection = null;
        }
      }
    },

    generation: ({ session_id, filename, prompt, caption }) => {
      state.generations += 1;
      statGenerations.textContent = state.generations;
      const label = caption || prompt || "";
      lastCaptionEl.textContent = label || "—";
      pushEditHistory(caption);
      if (session_id && filename) {
        lastImageEl.style.backgroundImage = `url(${API_ROOT}/sessions/${session_id}/${filename})`;
      }
    },

    swap: ({ target_row, target_col }) => {
      state.swaps += 1;
      statSwaps.textContent = state.swaps;
      if (typeof target_row === "number" && typeof target_col === "number") {
        const target = { row: target_row, col: target_col };
        flashModified(target);
        state.pendingDetection = {
          targetSector: target,
          deadline: Date.now() + DETECTION_WINDOW_MS,
        };
      }
    },

    session_started: ({ participant_id }) => {
      resetForNewSession(participant_id);
    },
  });
}

function initialState() {
  return {
    focusSector: null,
    generations: 0,
    swaps: 0,
    caught: 0,
    pendingDetection: null,
    lastValidTs: 0,
  };
}

function buildGrid(gridSize) {
  grid.style.gridTemplateColumns = `repeat(${gridSize}, 1fr)`;
  grid.style.gridTemplateRows = `repeat(${gridSize}, 1fr)`;
  grid.innerHTML = "";
  const cells = [];
  for (let r = 0; r < gridSize; r++) {
    cells[r] = [];
    for (let c = 0; c < gridSize; c++) {
      const el = document.createElement("div");
      el.className = "cell";
      el.textContent = sectorName({ row: r, col: c });
      grid.appendChild(el);
      cells[r][c] = el;
    }
  }
  return cells;
}

main();
```

- [ ] **Step 3: Manual verification**

Run the stack (`docker compose up --build` or `scripts/start_stack.sh`).
Open `http://localhost:8080/observer.html`.
Expected:
- Sidebar shows Participant card with an input and New button.
- Clicking **New** (empty input) posts to `/session/start`; a banner flashes; counters reset.
- Type `p1`, click **New**. Banner reads "New participant: p1". `assets/sessions/<new-session-id>/metadata.json` has `participant_id: "p1"`.
- Trigger a generation on the main page; observer shows caption under "Last edit" and prepends it to "Edit history". Thumbnail updates.

- [ ] **Step 4: Commit**

```bash
git add frontend/public/observer.html frontend/public/observer.js
git commit -m "feat(observer): new participant button + edit history + session reset"
```

---

## Task 11: Feed UI — caption preference + session reset

**Files:**
- Modify: `frontend/public/feed.js`

- [ ] **Step 1: Replace the full body of `feed.js`**

```javascript
import { API_ROOT } from "./config.js";
import { openStream } from "./ws.js";

const stage = document.getElementById("stage");
const waiting = document.getElementById("waiting");
const sectorEl = document.getElementById("sector");
const promptEl = document.getElementById("prompt");

let currentImg = null;

function clearStage() {
  if (currentImg) {
    currentImg.remove();
    currentImg = null;
  }
  waiting.style.display = "grid";
  sectorEl.textContent = "";
  promptEl.textContent = "";
}

function showGeneration({ session_id, filename, target_sector, prompt, caption }) {
  if (!session_id || !filename) return;
  const img = new Image();
  img.src = `${API_ROOT}/sessions/${session_id}/${filename}`;
  img.onload = () => {
    waiting.style.display = "none";
    if (currentImg) currentImg.classList.add("prev");
    stage.appendChild(img);
    requestAnimationFrame(() => {
      if (currentImg) currentImg.remove();
      currentImg = img;
    });
  };
  sectorEl.textContent = target_sector || "";
  promptEl.textContent = caption || prompt || "";
}

openStream({
  generation: showGeneration,
  session_started: clearStage,
});
```

- [ ] **Step 2: Manual verification**

Open `http://localhost:8080/feed.html` alongside a main page session. Trigger a generation — caption should appear in the bottom-right italic line (preferred over raw prompt). Click **New** on observer — feed clears and shows "Waiting for first generation…".

- [ ] **Step 3: Commit**

```bash
git add frontend/public/feed.js
git commit -m "feat(feed): prefer caption and reset on session_started"
```

---

## Task 12: Docs + example.env

**Files:**
- Modify: `example.env`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add env vars to `example.env`**

Append to `example.env`:

```bash
# Generation mode: "semantic" uses image-conditioned prompts with a 5-edit sliding
# window of captions; "cycling" uses the hand-authored prompts.txt / sector_prompts.json.
# Defaults to cycling for safe rollout.
GENERATION_MODE=cycling

# Idle reset window (seconds) before the generation service auto-rotates to a
# new participant session. Applies to both semantic and cycling modes.
IDLE_RESET_SEC=180
```

- [ ] **Step 2: Add a semantic-mode gotcha to `CLAUDE.md`**

Append under "Project-specific gotchas":

```markdown
- **Semantic mode state is in-memory**: `SemanticHistory` in `generation/semantic.py` lives per-process and per-session. A generation-service restart drops all captions and originals for the current session; next `/generate` repopulates. Replays rely on `metadata.json.edit_history`, not on the in-memory dict.
- **Idle auto-reset**: after 180 s of no `/generate` and no `/session/start`, the generation service auto-rotates to a new session. If you script the pipeline, either keep traffic flowing or bump `IDLE_RESET_SEC`.
```

- [ ] **Step 3: Commit**

```bash
git add example.env CLAUDE.md
git commit -m "docs: document semantic mode env vars and idle reset"
```

---

## Task 13: Final verification

- [ ] **Step 1: Full unit + integration suite**

Run: `cd generation && pytest -q`
Expected: 29 passed.

- [ ] **Step 2: Byte-compile all touched Python**

Run: `python3 -m py_compile generation/server.py generation/openrouter.py generation/semantic.py generation/session_manager.py backend/app/main.py`
Expected: no output.

- [ ] **Step 3: Smoke-test the stack**

1. Set `OPENROUTER_API_KEY` and `GENERATION_MODE=semantic` in `.env`.
2. `docker compose up --build`.
3. Open main page, observer, and feed.
4. Perform 5+ gaze fixations → verify:
   - `assets/sessions/<sid>/metadata.json` shows `runtime.mode = "semantic"`, each entry has a non-null `caption`, `edit_history` has up to 5 entries.
   - Observer "Edit history" lists the last ~5 captions.
   - Feed italic text mirrors the caption, not the raw prompt.
5. Click **New** on observer → new session folder appears, observer counters zeroed, feed clears.
6. Leave idle 3+ minutes → new session folder appears automatically; generation-service logs "Idle rotation: new session ...".
7. Unset `OPENROUTER_API_KEY`, restart generation service → logs coerce to cycling; `/generate` still returns 200 with the original image.

- [ ] **Step 4: Final commit for any lint / drift**

```bash
git status
# if clean: skip
# else:
git add -A
git commit -m "chore: final tidy-up after semantic-swaps rollout"
```

---

## Out of scope (covered by follow-up specs)

- Souvenir highlight-reel video (recommendation #1 from earlier).
- CI workflow (recommendation #2).
- Observer UI test automation.
- Semantic mode over multiple generation-service replicas (requires a shared store for `SemanticHistory`).
