"""Tests for generation/semantic.py."""
from __future__ import annotations

from PIL import Image

from semantic import SemanticHistory, build_prompt, degenerate_caption, parse_response


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


def test_parse_response_extracts_image_and_caption(fake_openrouter_response):
    resp = fake_openrouter_response("added a rainbow glow")
    image, caption = parse_response(resp["choices"][0]["message"])
    assert isinstance(image, Image.Image)
    assert caption == "added a rainbow glow"


def test_parse_response_without_caption_returns_none_caption(fake_openrouter_response):
    resp = fake_openrouter_response(caption=None)
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


def test_shrink_for_api_caps_max_edge_and_emits_png():
    from PIL import Image
    from sectors import shrink_for_api

    big = Image.new("RGB", (4096, 2048), (200, 100, 50))
    data_url = shrink_for_api(big, max_edge=512)
    assert data_url.startswith("data:image/png;base64,")

    import base64, io
    payload = base64.b64decode(data_url.split(",", 1)[1])
    decoded = Image.open(io.BytesIO(payload))
    assert max(decoded.size) <= 512


def test_shrink_for_api_passes_small_images_through():
    from PIL import Image
    from sectors import shrink_for_api

    small = Image.new("RGB", (200, 200), (0, 0, 0))
    data_url = shrink_for_api(small)
    import base64, io
    decoded = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    assert decoded.size == (200, 200)


def test_composite_sector_keeps_non_target_pixels_identical():
    from PIL import Image
    from sectors import composite_sector

    base = Image.new("RGB", (300, 300), (10, 20, 30))
    edit = Image.new("RGB", (300, 300), (200, 200, 200))
    region = (100, 100, 200, 200)  # central square

    out = composite_sector(base, edit, region)
    # Inside the region: edit's colour
    assert out.getpixel((150, 150)) == (200, 200, 200)
    # Just outside: base's colour, unchanged
    assert out.getpixel((50, 50)) == (10, 20, 30)
    assert out.getpixel((250, 250)) == (10, 20, 30)
    assert out.getpixel((99, 99)) == (10, 20, 30)


def test_composite_sector_handles_resolution_mismatch():
    from PIL import Image
    from sectors import composite_sector

    base = Image.new("RGB", (1000, 1000), (10, 20, 30))
    # Model returned a half-res output:
    edit = Image.new("RGB", (500, 500), (200, 200, 200))
    region = (200, 200, 600, 600)  # in base coordinates

    out = composite_sector(base, edit, region)
    assert out.size == base.size
    # Sampled inside the target sector → patched
    assert out.getpixel((400, 400)) == (200, 200, 200)
    # Sampled outside → base
    assert out.getpixel((50, 50)) == (10, 20, 30)
    assert out.getpixel((900, 900)) == (10, 20, 30)


def test_composite_sector_does_not_mutate_inputs():
    from PIL import Image
    from sectors import composite_sector

    base = Image.new("RGB", (100, 100), (10, 20, 30))
    edit = Image.new("RGB", (100, 100), (200, 200, 200))
    composite_sector(base, edit, (25, 25, 75, 75))
    assert base.getpixel((50, 50)) == (10, 20, 30)
    assert edit.getpixel((10, 10)) == (200, 200, 200)
