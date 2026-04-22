"""Per-session state for the semantic generation mode."""
from __future__ import annotations

from dataclasses import dataclass, field

HISTORY_WINDOW = 5


@dataclass
class SemanticHistory:
    """In-memory, single-process state keyed by session id.

    Holds the most recent `HISTORY_WINDOW` edit captions plus the original
    (pre-edit) base image for each session.
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
