"""Tests for generation/semantic.py."""
from __future__ import annotations

from PIL import Image

from semantic import (
    SemanticHistory,
    SemanticTurn,
    build_messages,
    degenerate_caption,
    parse_response,
)


def test_history_empty_for_unknown_session():
    h = SemanticHistory()
    assert h.turns("sess-a") == []
    assert h.captions("sess-a") == []
    assert h.original("sess-a") is None


def test_append_turn_records_and_returns_index():
    h = SemanticHistory()
    i0 = h.append_turn("sess-a", SemanticTurn("TL", "data:image/png;base64,AAA", "first edit"))
    i1 = h.append_turn("sess-a", SemanticTurn("BR", "data:image/png;base64,BBB", "second edit"))
    assert (i0, i1) == (0, 1)
    assert h.captions("sess-a") == ["first edit", "second edit"]
    assert [t.target_sector for t in h.turns("sess-a")] == ["TL", "BR"]


def test_sessions_are_isolated():
    h = SemanticHistory()
    h.append_turn("sess-a", SemanticTurn("TL", "x", "first"))
    h.append_turn("sess-b", SemanticTurn("BR", "y", "other"))
    assert h.captions("sess-a") == ["first"]
    assert h.captions("sess-b") == ["other"]


def test_update_caption_at_replaces_caption():
    h = SemanticHistory()
    idx = h.append_turn("sess-a", SemanticTurn("TL", "x", caption=None))
    assert h.update_caption_at("sess-a", idx, "a small bird appeared") is True
    assert h.turns("sess-a")[0].caption == "a small bird appeared"


def test_update_caption_at_returns_false_when_index_invalid():
    h = SemanticHistory()
    h.append_turn("sess-a", SemanticTurn("TL", "x"))
    assert h.update_caption_at("sess-a", 99, "anything") is False
    assert h.update_caption_at("nope", 0, "anything") is False


def test_set_original_is_idempotent_per_session():
    h = SemanticHistory()
    h.set_original("sess-a", "data:image/png;base64,AAA")
    h.set_original("sess-a", "data:image/png;base64,BBB")  # second call is a no-op
    assert h.original("sess-a") == "data:image/png;base64,AAA"


def test_clear_drops_turns_and_original():
    h = SemanticHistory()
    h.append_turn("sess-a", SemanticTurn("TL", "x", "edit"))
    h.set_original("sess-a", "data:image/png;base64,AAA")
    h.clear("sess-a")
    assert h.turns("sess-a") == []
    assert h.original("sess-a") is None


def test_build_messages_returns_single_user_turn_with_original_only(tiny_png_b64):
    messages = build_messages(
        original_b64=tiny_png_b64,
        turns=[],
        target_sector="TL",
        region=(0, 0, 100, 100),
    )
    assert len(messages) == 1
    msg = messages[0]
    assert msg["role"] == "user"
    image_parts = [p for p in msg["content"] if p["type"] == "image_url"]
    text_parts = [p for p in msg["content"] if p["type"] == "text"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == tiny_png_b64
    text = text_parts[0]["text"]
    assert "IMAGE 0" in text
    # Sector specified via natural language; pixel coords deliberately omitted.
    assert "upper-left" in text
    assert "x1=" not in text
    assert "pixel rectangle" not in text


def test_build_messages_includes_prior_edit_images_in_user_turn(tiny_png_b64):
    edit_a = tiny_png_b64.replace("AAA", "PRIOR_A") if "AAA" in tiny_png_b64 else tiny_png_b64
    edit_b = tiny_png_b64.replace("AAA", "PRIOR_B") if "AAA" in tiny_png_b64 else tiny_png_b64
    turns = [
        SemanticTurn("TR", edit_a, "a butterfly drifted in"),
        SemanticTurn("BL", edit_b, "a paper boat sailed"),
    ]
    messages = build_messages(
        original_b64=tiny_png_b64, turns=turns,
        target_sector="MC", region=(0, 0, 10, 10),
    )
    assert len(messages) == 1
    image_urls = [p["image_url"]["url"] for p in messages[0]["content"] if p["type"] == "image_url"]
    # original + 2 prior edits = 3 images.
    assert len(image_urls) == 3
    text = next(p["text"] for p in messages[0]["content"] if p["type"] == "text")
    assert "IMAGE 0 is the ORIGINAL" in text
    assert "IMAGE 1" in text and "TR" in text and "a butterfly drifted in" in text
    assert "IMAGE 2" in text and "BL" in text and "a paper boat sailed" in text
    assert "Take IMAGE 2" in text
    assert "SAME PHOTOGRAPH OR SCENE as IMAGE 2" in text


def test_build_messages_caps_replay_to_history_window(tiny_png_b64):
    turns = [SemanticTurn(f"S{i}", tiny_png_b64, f"edit {i}") for i in range(8)]
    messages = build_messages(
        original_b64=tiny_png_b64, turns=turns,
        target_sector="TL", region=(0, 0, 1, 1),
    )
    image_count = sum(1 for p in messages[0]["content"] if p["type"] == "image_url")
    # 1 original + HISTORY_WINDOW (=2) prior edit images.
    assert image_count == 3
    text = next(p["text"] for p in messages[0]["content"] if p["type"] == "text")
    # Last two edits are the ones replayed.
    assert "edit 6" in text
    assert "edit 7" in text
    assert "edit 5" not in text


def test_build_messages_uses_subtle_salience_by_default(tiny_png_b64):
    messages = build_messages(
        original_b64=tiny_png_b64, turns=[],
        target_sector="TL", region=(0, 0, 10, 10),
    )
    text = next(p["text"] for p in messages[0]["content"] if p["type"] == "text")
    assert "SMALL AND PLAUSIBLE" in text
    assert "peripheral vision" in text


def test_build_messages_forbids_drawing_rectangles_or_markers(tiny_png_b64):
    messages = build_messages(
        original_b64=tiny_png_b64, turns=[],
        target_sector="TR", region=(0, 0, 100, 100),
    )
    text = next(p["text"] for p in messages[0]["content"] if p["type"] == "text")
    assert "DO NOT draw any rectangle" in text
    assert "inset" in text
    assert "annotation" in text


def test_build_messages_uses_natural_sector_names(tiny_png_b64):
    """Sanity check that every sector resolves to a human-readable phrase."""
    for code, desc in (("TL", "upper-left"), ("MC", "centre"), ("BR", "lower-right")):
        messages = build_messages(
            original_b64=tiny_png_b64, turns=[],
            target_sector=code, region=(0, 0, 10, 10),
        )
        text = next(p["text"] for p in messages[0]["content"] if p["type"] == "text")
        assert desc in text


def test_build_messages_always_includes_scene_coherence_clause(tiny_png_b64):
    for salience in ("subtle", "moderate", "bold"):
        messages = build_messages(
            original_b64=tiny_png_b64, turns=[],
            target_sector="TL", region=(0, 0, 10, 10),
            salience=salience,
        )
        text = next(p["text"] for p in messages[0]["content"] if p["type"] == "text")
        assert "SCENE TYPE" in text
        assert "VISUAL STYLE" in text
        assert "jellyfish" in text  # the cautionary anti-example
        assert "SAME palette" in text


def test_build_messages_switches_salience_clause(tiny_png_b64):
    bold = build_messages(
        original_b64=tiny_png_b64, turns=[],
        target_sector="TL", region=(0, 0, 10, 10),
        salience="bold",
    )
    text = next(p["text"] for p in bold[0]["content"] if p["type"] == "text")
    assert "DRAMATIC" in text
    assert "SMALL AND PLAUSIBLE" not in text

    moderate = build_messages(
        original_b64=tiny_png_b64, turns=[],
        target_sector="TL", region=(0, 0, 10, 10),
        salience="moderate",
    )
    text2 = next(p["text"] for p in moderate[0]["content"] if p["type"] == "text")
    assert "NOTICEABLE BUT BELIEVABLE" in text2


def test_build_messages_omits_caption_label_when_missing(tiny_png_b64):
    turns = [SemanticTurn("TR", tiny_png_b64, caption=None)]
    messages = build_messages(
        original_b64=tiny_png_b64, turns=turns,
        target_sector="MC", region=(0, 0, 10, 10),
    )
    text = next(p["text"] for p in messages[0]["content"] if p["type"] == "text")
    # When the caption is missing, the label still mentions the sector but no
    # quoted description.
    assert "IMAGE 1" in text
    assert "TR" in text


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


def test_infer_aspect_ratio_snaps_to_supported_label():
    from sectors import infer_aspect_ratio

    assert infer_aspect_ratio(1024, 1024) == "1:1"
    assert infer_aspect_ratio(1024, 768) == "4:3"
    assert infer_aspect_ratio(768, 1024) == "3:4"
    assert infer_aspect_ratio(1920, 1080) == "16:9"
    assert infer_aspect_ratio(1080, 1920) == "9:16"
    assert infer_aspect_ratio(1500, 1000) == "3:2"
    # Off-list ratio: snap to nearest. 1234x567 = 2.18, nearest is 16:9 (1.78).
    assert infer_aspect_ratio(1234, 567) == "16:9"


def test_composite_sector_feathers_edges_when_feather_positive():
    from PIL import Image
    from sectors import composite_sector

    base = Image.new("RGB", (200, 200), (0, 0, 0))
    edit = Image.new("RGB", (200, 200), (255, 255, 255))
    out = composite_sector(base, edit, (50, 50, 150, 150), feather=10)

    # Center: fully patched
    assert out.getpixel((100, 100)) == (255, 255, 255)
    # Outside region: pure base
    assert out.getpixel((10, 10)) == (0, 0, 0)
    assert out.getpixel((190, 190)) == (0, 0, 0)
    # Just inside the region edge: blended (not pure base, not pure edit).
    edge_px = out.getpixel((52, 52))
    assert 0 < edge_px[0] < 255


def test_composite_sector_feather_zero_is_hard_edge():
    from PIL import Image
    from sectors import composite_sector

    base = Image.new("RGB", (100, 100), (10, 20, 30))
    edit = Image.new("RGB", (100, 100), (200, 200, 200))
    out = composite_sector(base, edit, (25, 25, 75, 75), feather=0)
    # First pixel inside region is the pure edit colour.
    assert out.getpixel((25, 25)) == (200, 200, 200)
    # Pixel just outside is pure base.
    assert out.getpixel((24, 24)) == (10, 20, 30)
