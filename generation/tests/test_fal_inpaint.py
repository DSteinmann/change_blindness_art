"""Tests for the fal.ai instruction-based edit client (FLUX.1 Kontext)."""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from fal_inpaint import edit_image


def _png_data_url(size: tuple[int, int] = (32, 32), colour=(7, 9, 11)) -> str:
    img = Image.new("RGB", size, colour)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _mock_transport(handler):
    return httpx.MockTransport(handler)


async def test_edit_returns_image_when_response_inlines_data_url():
    img_url = _png_data_url((64, 64))
    response_body = {"images": [{"url": img_url}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    transport = _mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await edit_image(img_url, "a small bird", "fake-key", client=client)
    assert isinstance(result, Image.Image)
    assert result.size == (64, 64)


async def test_edit_follows_external_image_url():
    out_img = _png_data_url((100, 50))
    out_bytes = base64.b64decode(out_img.split(",", 1)[1])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "fal.run" in str(request.url):
            return httpx.Response(200, json={
                "images": [{"url": "https://cdn.fal.ai/result.png"}]
            })
        return httpx.Response(200, content=out_bytes, headers={"content-type": "image/png"})

    transport = _mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await edit_image(out_img, "x", "fake-key", client=client)
    assert result.size == (100, 50)
    assert any("cdn.fal.ai" in c for c in calls)


async def test_edit_raises_on_missing_key():
    with pytest.raises(ValueError, match="FAL_KEY not set"):
        await edit_image("", "x", "")


async def test_edit_raises_on_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    transport = _mock_transport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="fal API error: 500"):
            await edit_image(_png_data_url(), "x", "fake-key", client=client)


async def test_edit_request_nests_payload_under_input_key():
    """Kontext silently ignores flat keys and runs no model. The body MUST be
    nested under "input" — this test guards that bug from regressing."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"images": [{"url": _png_data_url()}]})

    transport = _mock_transport(handler)
    img = _png_data_url()
    async with httpx.AsyncClient(transport=transport) as client:
        await edit_image(img, "a fern unfurls", "fake-key", client=client)
    body = captured["body"]
    assert "input" in body, "Kontext payload must be nested under 'input'"
    inner = body["input"]
    assert inner["image_url"] == img
    assert inner["prompt"] == "a fern unfurls"
    assert inner["sync_mode"] is True
    # Kontext is instruction-only; mask_url must not be in the payload.
    assert "mask_url" not in inner
    assert captured["auth"] == "Key fake-key"
