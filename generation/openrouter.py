"""OpenRouter image-generation client."""
from __future__ import annotations

import base64
import io
import os

import httpx
from PIL import Image

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
IMAGE_MODEL = os.getenv("OPENROUTER_IMAGE_MODEL", "google/gemini-3.1-flash-image-preview")


def _extract_image(message: dict) -> Image.Image | None:
    for img_item in message.get("images", []) or []:
        if img_item.get("type") == "image_url":
            url = img_item.get("image_url", {}).get("url", "")
            if url.startswith("data:image"):
                return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    content = message.get("content", "")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url":
                url = item.get("image_url", {}).get("url", "")
                if url.startswith("data:image"):
                    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    return None


def _resolve_client(client: httpx.AsyncClient | None) -> tuple[httpx.AsyncClient, bool]:
    """Return (client, owned). Caller must aclose() iff owned."""
    if client is not None:
        return client, False
    return httpx.AsyncClient(timeout=120.0), True


async def _post_chat(payload: dict, api_key: str, client: httpx.AsyncClient) -> dict:
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")
    response = await client.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ubicomp-capstone",
        },
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter API error: {response.status_code} - {response.text}")
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("No choices in OpenRouter response")
    return choices[0].get("message", {})


async def generate_with_openrouter(
    image: Image.Image,
    prompt: str,
    region: tuple[int, int, int, int],
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> Image.Image:
    """Cycling-mode generation: one image in, one image out."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    payload = {
        "model": IMAGE_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                {
                    "type": "text",
                    "text": (
                        f"Modify this image by adding or changing something in the region "
                        f"from pixel ({region[0]}, {region[1]}) to ({region[2]}, {region[3]}). "
                        f"The modification should be: {prompt}. Return the complete modified image."
                    ),
                },
            ],
        }],
        "modalities": ["image", "text"],
    }
    owned, created = _resolve_client(client)
    try:
        message = await _post_chat(payload, api_key, owned)
    finally:
        if created:
            await owned.aclose()
    image_out = _extract_image(message)
    if image_out is None:
        raise RuntimeError("Model did not return an image")
    return image_out


async def generate_with_openrouter_semantic(
    content_parts: list[dict],
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Semantic-mode request. Returns the raw `message` dict so the caller can
    parse image + caption itself."""
    payload = {
        "model": IMAGE_MODEL,
        "messages": [{"role": "user", "content": content_parts}],
        "modalities": ["image", "text"],
    }
    owned, created = _resolve_client(client)
    try:
        return await _post_chat(payload, api_key, owned)
    finally:
        if created:
            await owned.aclose()
