"""Tests for generation/semantic.py."""
from __future__ import annotations

from semantic import SemanticHistory, build_prompt


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
