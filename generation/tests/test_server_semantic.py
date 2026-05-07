"""Integration tests for the /generate semantic branch."""
from __future__ import annotations

import base64
import io
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def _png_b64() -> str:
    img = Image.new("RGB", (24, 24), (9, 9, 9))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def semantic_app(monkeypatch, tmp_path):
    """Build a fresh FastAPI app in semantic mode with a tmp sessions dir."""
    monkeypatch.setenv("GENERATION_MODE", "semantic")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import importlib
    import server
    importlib.reload(server)
    server.SESSIONS_DIR = tmp_path
    server.session_manager.sessions_dir = tmp_path
    # Neutralise the backend relay — tests shouldn't hit the network.
    server._notify_backend = AsyncMock(return_value=None)
    yield server


def _message_with_caption(caption: str | None) -> dict:
    content = f"CAPTION: {caption}" if caption is not None else "noop"
    return {
        "content": content,
        "images": [{"type": "image_url", "image_url": {"url": _png_b64()}}],
    }


def _post_generate(client: TestClient):
    return client.post("/generate", json={
        "image_base64": _png_b64(),
        "focus_x": 0.5, "focus_y": 0.5,
        "target_row": 0, "target_col": 0, "grid_size": 3,
    })


def test_semantic_generate_saves_caption_and_advances_history(semantic_app):
    semantic_call = AsyncMock(return_value=_message_with_caption("added a rainbow"))
    with patch("server.generate_with_openrouter_semantic", semantic_call):
        with TestClient(semantic_app.app) as client:
            resp = _post_generate(client)
    assert resp.status_code == 200
    session_id = semantic_app.session_manager.current_session_id
    metadata = json.loads(
        (semantic_app.SESSIONS_DIR / session_id / "metadata.json").read_text()
    )
    assert metadata["sequence"][0]["caption"] == "added a rainbow"
    assert metadata["edit_history"] == ["added a rainbow"]
    assert semantic_app.semantic_history.captions(session_id) == ["added a rainbow"]
    assert semantic_call.await_count == 1


def test_semantic_generate_falls_back_to_cycling_on_repeated_failure(semantic_app):
    semantic_call = AsyncMock(side_effect=RuntimeError("OpenRouter down"))
    cycling_image = Image.new("RGB", (24, 24), (50, 50, 50))
    cycling_call = AsyncMock(return_value=cycling_image)
    with patch("server.generate_with_openrouter_semantic", semantic_call), \
         patch("server.generate_with_openrouter", cycling_call):
        with TestClient(semantic_app.app) as client:
            resp = _post_generate(client)
    assert resp.status_code == 200
    session_id = semantic_app.session_manager.current_session_id
    metadata = json.loads(
        (semantic_app.SESSIONS_DIR / session_id / "metadata.json").read_text()
    )
    assert metadata["sequence"][0]["caption"] is None
    assert metadata["edit_history"] == []
    assert semantic_call.await_count == 2  # original + terser retry
    assert cycling_call.await_count == 1


def test_semantic_generate_synthesises_caption_when_model_omits_it(semantic_app):
    semantic_call = AsyncMock(return_value=_message_with_caption(None))
    with patch("server.generate_with_openrouter_semantic", semantic_call):
        with TestClient(semantic_app.app) as client:
            _post_generate(client)
    session_id = semantic_app.session_manager.current_session_id
    metadata = json.loads(
        (semantic_app.SESSIONS_DIR / session_id / "metadata.json").read_text()
    )
    caption = metadata["sequence"][0]["caption"]
    assert caption is not None
    assert "TL" in caption


def test_session_start_clears_semantic_history(semantic_app):
    semantic_call = AsyncMock(return_value=_message_with_caption("first edit"))
    with patch("server.generate_with_openrouter_semantic", semantic_call):
        with TestClient(semantic_app.app) as client:
            _post_generate(client)
            old_sid = semantic_app.session_manager.current_session_id
            assert semantic_app.semantic_history.captions(old_sid) == ["first edit"]

            resp = client.post(
                "/session/start",
                json={"session_id": "rolled-over", "participant_id": "participant-42"},
            )
    assert resp.status_code == 200
    new_sid = semantic_app.session_manager.current_session_id
    assert new_sid != old_sid
    assert semantic_app.semantic_history.captions(old_sid) == []
    assert semantic_app.semantic_history.captions(new_sid) == []


async def test_idle_reset_rotates_session_when_stale(semantic_app, monkeypatch):
    # Drive the app through startup so _idle_lock and session exist.
    with TestClient(semantic_app.app):
        sm = semantic_app.session_manager
        first = sm.current_session_id
        now = time.time()
        semantic_app._last_generation_ts = now - 240
        semantic_app._last_session_started_ts = now - 240
        # Force a different auto-generated session id (session_<int-time>).
        monkeypatch.setattr("session_manager.time.time", lambda: now + 10)
        await semantic_app._maybe_idle_reset()
        assert sm.current_session_id != first


async def test_idle_reset_is_noop_when_recent(semantic_app):
    with TestClient(semantic_app.app):
        sm = semantic_app.session_manager
        first = sm.current_session_id
        now = time.time()
        semantic_app._last_generation_ts = now - 30
        semantic_app._last_session_started_ts = now - 30
        await semantic_app._maybe_idle_reset()
        assert sm.current_session_id == first


def test_duplicate_caption_is_flagged(semantic_app):
    semantic_call = AsyncMock(return_value=_message_with_caption("a monarch butterfly appeared"))
    with patch("server.generate_with_openrouter_semantic", semantic_call):
        with TestClient(semantic_app.app) as client:
            for _ in range(2):
                _post_generate(client)
    session_id = semantic_app.session_manager.current_session_id
    metadata = json.loads(
        (semantic_app.SESSIONS_DIR / session_id / "metadata.json").read_text()
    )
    assert metadata["sequence"][0].get("duplicate_caption") is not True
    assert metadata["sequence"][1].get("duplicate_caption") is True
