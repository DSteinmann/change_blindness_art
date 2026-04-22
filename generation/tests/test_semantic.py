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
