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


async def test_edit_request_uses_flat_payload():
    """The direct fal.run sync endpoint expects flat keys. Wrapping the body
    under "input" (a JS-SDK convention) produces 422."""
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
    assert "input" not in body, "payload must NOT be nested under 'input'"
    assert body["image_urls"] == [img]
    assert body["prompt"] == "a fern unfurls"
    assert body["sync_mode"] is True
    # No mask passed in this call → mask_url omitted.
    assert "mask_url" not in body
    assert captured["auth"] == "Key fake-key"


async def test_edit_request_includes_mask_url_when_supplied():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"images": [{"url": _png_data_url()}]})

    transport = _mock_transport(handler)
    img = _png_data_url()
    mask = _png_data_url(colour=(255, 255, 255))
    async with httpx.AsyncClient(transport=transport) as client:
        await edit_image(img, "x", "fake-key", mask_b64=mask, client=client)
    body = captured["body"]
    assert body["mask_url"] == mask
    assert body["image_urls"] == [img]
