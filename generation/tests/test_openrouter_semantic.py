"""Tests for the semantic OpenRouter call using httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest

from openrouter import caption_edit, generate_with_openrouter_semantic
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


async def test_semantic_payload_is_multi_turn_with_intro_and_replay(tiny_png_b64):
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
    # First message is user with the original image; last is the new instruction.
    assert sent_messages[0]["role"] == "user"
    first_user_images = [p for p in sent_messages[0]["content"] if p["type"] == "image_url"]
    assert len(first_user_images) == 1
    assert sent_messages[-1]["role"] == "user"
    assert "TL" in sent_messages[-1]["content"]
    # An assistant turn replays the prior butterfly edit.
    replayed_assistant = [
        m for m in sent_messages
        if m["role"] == "assistant" and isinstance(m["content"], list)
    ]
    assert len(replayed_assistant) == 1
    assert any(
        p.get("type") == "text" and p.get("text") == "a butterfly drifted in"
        for p in replayed_assistant[0]["content"]
    )
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
