"""Tests for the semantic OpenRouter call using httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest

from openrouter import caption_edit, generate_with_openrouter_semantic, plan_edit
from semantic import build_messages, parse_response


def _mock_transport(status: int, body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


async def test_semantic_request_returns_message_on_success(
    tiny_png_b64, fake_openrouter_response,
):
    response_body = fake_openrouter_response("a monarch butterfly drifted in")
    transport = _mock_transport(200, response_body)
    async with httpx.AsyncClient(transport=transport) as client:
        messages = build_messages(tiny_png_b64, [], "TL", (0, 0, 10, 10))
        message = await generate_with_openrouter_semantic(messages, "fake-key", client=client)
        image, caption = parse_response(message)
    assert image is not None
    assert caption == "a monarch butterfly drifted in"


async def test_semantic_request_raises_on_500(tiny_png_b64):
    transport = _mock_transport(500, {"error": "boom"})
    async with httpx.AsyncClient(transport=transport) as client:
        messages = build_messages(tiny_png_b64, [], "TL", (0, 0, 10, 10))
        with pytest.raises(RuntimeError, match="OpenRouter API error: 500"):
            await generate_with_openrouter_semantic(messages, "fake-key", client=client)


async def test_semantic_request_rejects_missing_api_key(tiny_png_b64):
    transport = _mock_transport(200, {"choices": [{"message": {}}]})
    async with httpx.AsyncClient(transport=transport) as client:
        messages = build_messages(tiny_png_b64, [], "TL", (0, 0, 10, 10))
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY not set"):
            await generate_with_openrouter_semantic(messages, "", client=client)


async def test_semantic_payload_is_single_user_turn_with_all_images(tiny_png_b64):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {
                "content": "CAPTION: x",
                "images": [{"type": "image_url", "image_url": {"url": tiny_png_b64}}],
            }}],
        })

    transport = httpx.MockTransport(handler)
    from semantic import SemanticTurn
    turns = [SemanticTurn("BR", tiny_png_b64, "a butterfly drifted in")]
    async with httpx.AsyncClient(transport=transport) as client:
        messages = build_messages(tiny_png_b64, turns, "TL", (0, 0, 10, 10))
        await generate_with_openrouter_semantic(messages, "fake-key", client=client)

    body = captured["body"]
    sent_messages = body["messages"]
    # All images and instructions are collapsed into one user message — keeps
    # OpenRouter from stripping assistant content[] in the multi-turn variant.
    assert len(sent_messages) == 1
    assert sent_messages[0]["role"] == "user"
    images = [p for p in sent_messages[0]["content"] if p["type"] == "image_url"]
    assert len(images) == 2  # original + 1 prior edit
    text = next(p["text"] for p in sent_messages[0]["content"] if p["type"] == "text")
    # The new prompt names sectors in natural language ("upper-left" for TL)
    # and deliberately avoids the raw sector code in the location-of-edit
    # phrasing (it appears only in prior-edit captions).
    assert "upper-left" in text
    assert "a butterfly drifted in" in text
    assert body["image_config"]["image_size"] == "2K"


async def test_caption_edit_returns_string_on_success(tiny_png_b64):
    body = {"choices": [{"message": {"content": "a small ladybug landed on the leaf"}}]}
    transport = _mock_transport(200, body)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await caption_edit(
            tiny_png_b64, tiny_png_b64, "TR", "fake-key", client=client,
        )
    assert result == "a small ladybug landed on the leaf"


async def test_caption_edit_returns_none_on_api_error(tiny_png_b64):
    transport = _mock_transport(500, {"error": "boom"})
    async with httpx.AsyncClient(transport=transport) as client:
        result = await caption_edit(
            tiny_png_b64, tiny_png_b64, "TR", "fake-key", client=client,
        )
    assert result is None


async def test_caption_edit_handles_list_content(tiny_png_b64):
    body = {"choices": [{"message": {"content": [
        {"type": "text", "text": "  a butterfly drifted in  "},
    ]}}]}
    transport = _mock_transport(200, body)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await caption_edit(
            tiny_png_b64, tiny_png_b64, "MC", "fake-key", client=client,
        )
    assert result == "a butterfly drifted in"


def test_extract_image_picks_largest_when_multiple_returned():
    """gemini-3-pro-image-preview sometimes returns preview + full-res; pick the largest."""
    import base64, io
    from openrouter import _extract_image
    from PIL import Image

    def _data_url(size, colour):
        img = Image.new("RGB", size, colour)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    small = _data_url((128, 64), (1, 2, 3))
    large = _data_url((512, 256), (4, 5, 6))
    message = {
        "images": [
            {"type": "image_url", "image_url": {"url": small}},
            {"type": "image_url", "image_url": {"url": large}},
        ],
    }
    out = _extract_image(message)
    assert out.size == (512, 256)


def test_extract_image_picks_largest_irrespective_of_order():
    import base64, io
    from openrouter import _extract_image
    from PIL import Image

    def _data_url(size):
        img = Image.new("RGB", size, (0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    # Large first, small second — both orderings must return the large one.
    message = {
        "images": [
            {"type": "image_url", "image_url": {"url": _data_url((400, 200))}},
            {"type": "image_url", "image_url": {"url": _data_url((100, 50))}},
        ],
    }
    assert _extract_image(message).size == (400, 200)


async def test_plan_edit_returns_proposed_instruction(tiny_png_b64):
    body = {"choices": [{"message": {"content": "add a small commercial airplane high in the sky"}}]}
    transport = _mock_transport(200, body)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await plan_edit(
            image_b64=tiny_png_b64,
            target_sector="upper-right",
            focus_sector="lower-left",
            prior_edits=[],
            api_key="fake-key",
            client=client,
        )
    assert result == "add a small commercial airplane high in the sky"


async def test_plan_edit_returns_none_without_api_key(tiny_png_b64):
    result = await plan_edit(
        image_b64=tiny_png_b64, target_sector="upper-left", focus_sector=None,
        prior_edits=[], api_key="",
    )
    assert result is None


async def test_plan_edit_returns_none_on_error(tiny_png_b64):
    transport = _mock_transport(500, {"error": "boom"})
    async with httpx.AsyncClient(transport=transport) as client:
        result = await plan_edit(
            image_b64=tiny_png_b64, target_sector="upper-left", focus_sector=None,
            prior_edits=[], api_key="fake-key", client=client,
        )
    assert result is None


async def test_plan_edit_payload_includes_focus_target_and_history(tiny_png_b64):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "do something"}}]
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await plan_edit(
            image_b64=tiny_png_b64,
            target_sector="upper-left",
            focus_sector="lower-right",
            prior_edits=["added a bird", "added a kite"],
            api_key="fake-key",
            client=client,
        )
    content = captured["body"]["messages"][0]["content"]
    image_parts = [p for p in content if p["type"] == "image_url"]
    text_parts = [p for p in content if p["type"] == "text"]
    assert len(image_parts) == 1
    text = text_parts[0]["text"]
    assert "upper-left" in text
    assert "lower-right" in text
    assert "peripheral" in text.lower()
    assert "added a bird" in text
    assert "added a kite" in text


async def test_plan_edit_omits_gaze_block_when_focus_unknown(tiny_png_b64):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "x"}}]
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await plan_edit(
            image_b64=tiny_png_b64,
            target_sector="centre",
            focus_sector=None,
            prior_edits=[],
            api_key="fake-key",
            client=client,
        )
    text = next(
        p["text"] for p in captured["body"]["messages"][0]["content"]
        if p["type"] == "text"
    )
    assert "FIXATED" not in text  # gaze block is omitted when focus is unknown
    assert "centre" in text
