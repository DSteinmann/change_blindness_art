"""Per-session state and prompt construction for the semantic generation mode.

The official Gemini Image docs (https://ai.google.dev/gemini-api/docs/image-generation)
recommend multi-turn chat for cumulative editing: each prior generation should
be replayed as an assistant turn so the model can reason over its own outputs
visually rather than from text descriptions alone.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Tuple

from PIL import Image

from openrouter import _extract_image

# How many prior turns to replay back to the model. Each turn adds one image to
# the request payload, so a tight window keeps requests well under OpenRouter's
# 30MB limit even at 1024 px input compression.
HISTORY_WINDOW = 2
CAPTION_PREFIX = "CAPTION:"


@dataclass
class SemanticTurn:
    """One prior edit replayed back to the model in subsequent calls."""

    target_sector: str
    image_b64: str
    caption: str | None = None


@dataclass
class SemanticHistory:
    """In-memory, single-process state keyed by session id.

    Holds an ordered list of prior turns plus the original (pre-edit) base
    image for each session.
    """

    _turns: dict[str, list[SemanticTurn]] = field(default_factory=dict)
    _originals: dict[str, str] = field(default_factory=dict)

    def turns(self, session_id: str) -> list[SemanticTurn]:
        return list(self._turns.get(session_id, []))

    def captions(self, session_id: str) -> list[str]:
        """Convenience accessor for callers that only need text."""
        return [t.caption for t in self._turns.get(session_id, []) if t.caption]

    def append_turn(self, session_id: str, turn: SemanticTurn) -> int:
        """Record a new turn. Returns the absolute index for later caption updates."""
        bucket = self._turns.setdefault(session_id, [])
        bucket.append(turn)
        return len(bucket) - 1

    def update_caption_at(self, session_id: str, index: int, caption: str) -> bool:
        bucket = self._turns.get(session_id, [])
        if 0 <= index < len(bucket):
            bucket[index].caption = caption
            return True
        return False

    def original(self, session_id: str) -> str | None:
        return self._originals.get(session_id)

    def set_original(self, session_id: str, image_b64: str) -> None:
        self._originals.setdefault(session_id, image_b64)

    def clear(self, session_id: str) -> None:
        self._turns.pop(session_id, None)
        self._originals.pop(session_id, None)


def build_messages(
    original_b64: str,
    turns: list[SemanticTurn],
    target_sector: str,
    region: tuple[int, int, int, int],
) -> list[dict]:
    """Build a multi-turn chat-completions `messages` array.

    The conversation has the shape:
      user      <original> + intro
      assistant ack
      user      "Add to <sector>" (per recent prior turn)
      assistant <prior edit image> + caption  (per recent prior turn)
      ...
      user      "Now add ONE new element to <sector>... keep the rest unchanged."
    """
    intro = (
        "We're going to edit this image iteratively. Each turn I will ask you "
        "to add ONE new element to a specific region of the scene. Always keep "
        "every prior addition and the rest of the scene unchanged. The new "
        "element should feel like it belongs in the scene — like it was always "
        "there, not like a pasted sticker."
    )
    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": original_b64}},
                {"type": "text", "text": intro},
            ],
        },
        {
            "role": "assistant",
            "content": "Understood. I'll add one new element per turn and keep everything else unchanged.",
        },
    ]

    for turn in turns[-HISTORY_WINDOW:]:
        messages.append({
            "role": "user",
            "content": (
                f"Add a new element to the {turn.target_sector} sector of the "
                "scene. Keep all prior additions and the rest of the scene unchanged."
            ),
        })
        assistant_content: list[dict] = [
            {"type": "image_url", "image_url": {"url": turn.image_b64}},
        ]
        if turn.caption:
            assistant_content.append({"type": "text", "text": turn.caption})
        messages.append({"role": "assistant", "content": assistant_content})

    x1, y1, x2, y2 = region
    final = (
        f"Now add ONE new element to the {target_sector} sector of the scene "
        f"(pixel rectangle x1={x1}, y1={y1}, x2={x2}, y2={y2}). It should be "
        "different from every prior addition above and feel native to the "
        "scene. Keep every prior addition and the rest of the image unchanged."
    )
    messages.append({"role": "user", "content": final})
    return messages


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
    return f"edit {index} in {sector_name} at {time.strftime('%H:%M:%S')}"
