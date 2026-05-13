"""fal.ai FLUX.1 Pro Fill client.

Unlike OpenRouter image-edit models (which take only image + text and may
re-render the entire canvas), FLUX Fill takes image + mask + prompt and
guarantees that pixels outside the mask are preserved byte-identically.
This eliminates the "model regenerated the whole scene" failure modes we
have been fighting with the OpenRouter backend.
"""
from __future__ import annotations

import base64
import io
import os

import httpx
from PIL import Image

FAL_BASE_URL = os.getenv("FAL_BASE_URL", "https://fal.run")
FAL_INPAINT_MODEL = os.getenv("FAL_INPAINT_MODEL", "fal-ai/flux-pro/v1/fill")


def _resolve_client(client: httpx.AsyncClient | None) -> tuple[httpx.AsyncClient, bool]:
    if client is not None:
        return client, False
    return httpx.AsyncClient(timeout=180.0), True


async def inpaint(
    image_b64: str,
    mask_b64: str,
    prompt: str,
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> Image.Image:
    """Run mask-based inpainting and return the result as a PIL Image.

    `image_b64` and `mask_b64` are data URLs (mask: white = edit, black = preserve).
    `prompt` describes the content to render inside the mask.
    """
    if not api_key:
        raise ValueError("FAL_KEY not set")

    payload = {
        "image_url": image_b64,
        "mask_url": mask_b64,
        "prompt": prompt,
        "sync_mode": True,
    }

    owned, created = _resolve_client(client)
    try:
        response = await owned.post(
            f"{FAL_BASE_URL}/{FAL_INPAINT_MODEL}",
            headers={
                "Authorization": f"Key {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"fal API error: {response.status_code} - {response.text}"
            )
        data = response.json()
        images = data.get("images") or []
        if not images:
            raise RuntimeError("fal returned no images")
        image_url = images[0].get("url")
        if not image_url:
            raise RuntimeError("fal image missing url field")
        if image_url.startswith("data:image"):
            payload_bytes = base64.b64decode(image_url.split(",", 1)[1])
        else:
            img_resp = await owned.get(image_url)
            img_resp.raise_for_status()
            payload_bytes = img_resp.content
        return Image.open(io.BytesIO(payload_bytes))
    finally:
        if created:
            await owned.aclose()
