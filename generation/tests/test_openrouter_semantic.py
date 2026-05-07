"""Tests for the semantic OpenRouter call using httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest

from openrouter import generate_with_openrouter_semantic
from semantic import build_prompt, parse_response


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
        parts = build_prompt(tiny_png_b64, tiny_png_b64, [], (0, 0, 10, 10), "TL")
        message = await generate_with_openrouter_semantic(parts, "fake-key", client=client)
        image, caption = parse_response(message)
    assert image is not None
    assert caption == "a monarch butterfly drifted in"


async def test_semantic_request_raises_on_500(tiny_png_b64):
    transport = _mock_transport(500, {"error": "boom"})
    async with httpx.AsyncClient(transport=transport) as client:
        parts = build_prompt(tiny_png_b64, tiny_png_b64, [], (0, 0, 10, 10), "TL")
        with pytest.raises(RuntimeError, match="OpenRouter API error: 500"):
            await generate_with_openrouter_semantic(parts, "fake-key", client=client)


async def test_semantic_request_rejects_missing_api_key(tiny_png_b64):
    transport = _mock_transport(200, {"choices": [{"message": {}}]})
    async with httpx.AsyncClient(transport=transport) as client:
        parts = build_prompt(tiny_png_b64, tiny_png_b64, [], (0, 0, 10, 10), "TL")
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY not set"):
            await generate_with_openrouter_semantic(parts, "", client=client)


async def test_semantic_payload_carries_two_images_and_instructions(tiny_png_b64):
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
    async with httpx.AsyncClient(transport=transport) as client:
        parts = build_prompt(tiny_png_b64, tiny_png_b64, ["prior edit"], (0, 0, 10, 10), "TL")
        await generate_with_openrouter_semantic(parts, "fake-key", client=client)

    content = captured["body"]["messages"][0]["content"]
    image_parts = [p for p in content if p["type"] == "image_url"]
    assert len(image_parts) == 2
    text_parts = [p for p in content if p["type"] == "text"]
    assert "prior edit" in text_parts[0]["text"]
