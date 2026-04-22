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
