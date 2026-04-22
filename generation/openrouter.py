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


async def generate_with_openrouter(
    image: Image.Image, prompt: str, region: tuple[int, int, int, int], api_key: str
) -> Image.Image:
    """Ask OpenRouter's image model to rewrite `region` according to `prompt`."""
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/ubicomp-capstone",
            },
            json={
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
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter API error: {response.status_code} - {response.text}")

    choices = response.json().get("choices") or []
    if not choices:
        raise RuntimeError("No choices in OpenRouter response")

    image_out = _extract_image(choices[0].get("message", {}))
    if image_out is None:
        raise RuntimeError("Model did not return an image")
    return image_out
