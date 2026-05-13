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

# Always-applied clause that grounds the edit in the existing scene's content
# and rendering. Without this, the model treats "creative freedom" as licence
# to introduce wildly out-of-context elements (jellyfish in a city skyline).
SCENE_COHERENCE_CLAUSE = (
    "BEFORE making any change, infer the following from IMAGE 0:\n"
    "  - SCENE TYPE: e.g. urban skyline at night, rural landscape, indoor "
    "still life, portrait, forest, coastal seascape.\n"
    "  - VISUAL STYLE: e.g. photorealistic photograph, oil painting, "
    "watercolour, 3D render, illustration. Match the existing lens "
    "characteristics, grain, sharpness, and rendering technique exactly.\n"
    "  - COLOUR PALETTE and LIGHTING: the dominant colours, brightness "
    "range, time of day, and direction of light.\n\n"
    "Your edit MUST honour all three. Specifically:\n"
    "  - Only introduce objects, creatures, materials, or phenomena that "
    "would plausibly exist in THIS kind of scene. A lamppost, bird, plume "
    "of steam, or piece of signage belongs in a city; a flower, deer, or "
    "shaft of light belongs in a forest; tableware or a folded napkin "
    "belongs in a still life. Do NOT add things that violate the scene's "
    "genre (no jellyfish in a skyline, no skyscraper in a meadow, no "
    "magical creature in a photograph of a real place).\n"
    "  - Render in the SAME style as the rest of the image — same medium, "
    "sharpness, contrast, and post-processing.\n"
    "  - Use the SAME palette and brightness as the surrounding pixels. "
    "Do not introduce a glowing, neon, or strongly contrasting element "
    "unless similar elements already appear in the scene."
)

SALIENCE_PROMPTS = {
    "subtle": (
        "Make the change SMALL AND PLAUSIBLE — a minor naturalistic detail "
        "that fits the scene as if it had always been there. Think a single "
        "small object, a subtle weather or lighting effect, a delicate "
        "transformation. The change must NOT dominate the sector; it should "
        "be the kind of detail a viewer could plausibly miss in peripheral "
        "vision. Avoid prominent, fantastical, or surreal elements; avoid "
        "anything large or brightly luminous that contrasts strongly with "
        "its surroundings."
    ),
    "moderate": (
        "Make the change NOTICEABLE BUT BELIEVABLE — a small-to-medium "
        "object, atmospheric shift, or transformation that still feels native "
        "to the scene. Avoid anything that would dominate the full image or "
        "feel fantastical."
    ),
    "bold": (
        "The change can be DRAMATIC, ATMOSPHERIC, OR SURREAL — surprise the "
        "viewer with something striking or otherworldly. The result should "
        "still feel intentional within the scene's overall mood."
    ),
}


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
    salience: str = "subtle",
) -> list[dict]:
    """Build a chat-completions `messages` array.

    Single user turn carrying the original image plus up to `HISTORY_WINDOW`
    most-recent prior edits as labelled images. We avoid splitting prior edits
    across assistant turns because OpenRouter has been observed to strip
    assistant `content[]` for some Gemini models, breaking cumulative editing.
    """
    recent = turns[-HISTORY_WINDOW:]
    content: list[dict] = [
        {"type": "image_url", "image_url": {"url": original_b64}},
    ]
    for turn in recent:
        content.append({"type": "image_url", "image_url": {"url": turn.image_b64}})

    label_lines = ["IMAGE 0 is the ORIGINAL scene."]
    for i, turn in enumerate(recent, start=1):
        if turn.caption:
            label_lines.append(
                f"IMAGE {i} is the scene after a prior edit "
                f"(in the {turn.target_sector} sector): {turn.caption}"
            )
        else:
            label_lines.append(
                f"IMAGE {i} is the scene after a prior edit "
                f"in the {turn.target_sector} sector."
            )

    if recent:
        anchor = f"Produce a new image based on IMAGE {len(recent)} (the most recent state)."
    else:
        anchor = "Produce a new image based on IMAGE 0."

    x1, y1, x2, y2 = region
    salience_clause = SALIENCE_PROMPTS.get(salience, SALIENCE_PROMPTS["subtle"])
    instruction = (
        "\n".join(label_lines)
        + "\n\n"
        + anchor
        + f" Make ONE deliberate change in the {target_sector} sector "
        f"(pixel rectangle x1={x1}, y1={y1}, x2={x2}, y2={y2}). You may add a "
        "new element, transform or replace something already there, or remove "
        "something to reveal what lies behind it.\n\n"
        f"{SCENE_COHERENCE_CLAUSE}\n\n"
        f"{salience_clause}\n\n"
        "Keep every prior change visible and the rest of the image unchanged."
    )
    content.append({"type": "text", "text": instruction})

    return [{"role": "user", "content": content}]


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
