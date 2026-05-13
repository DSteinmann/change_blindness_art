"""fal.ai instruction-based image-edit client.

Uses ByteDance Seedream V5 Lite Edit by default. Instruction-only (no mask);
fal documents it as having strong editing consistency and supports multiple
input images per call.
"""
from __future__ import annotations

import base64
import io
import os

import httpx
from PIL import Image

FAL_BASE_URL = os.getenv("FAL_BASE_URL", "https://fal.run")
FAL_EDIT_MODEL = os.getenv("FAL_EDIT_MODEL", "fal-ai/bytedance/seedream/v5/lite/edit")


def _resolve_client(client: httpx.AsyncClient | None) -> tuple[httpx.AsyncClient, bool]:
    if client is not None:
        return client, False
    return httpx.AsyncClient(timeout=180.0), True


async def edit_image(
    image_b64: str,
    prompt: str,
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> Image.Image:
    """Run instruction-based image editing and return the result as a PIL Image.

    `image_b64` is a data URL of the input image. `prompt` is the natural-
    language instruction (must include where + what — Kontext has no mask).
    """
    if not api_key:
        raise ValueError("FAL_KEY not set")

    # Direct HTTP POSTs to fal.run use FLAT keys; the "input" wrapper is a
    # JS-SDK-only convention. Wrapping the body silently produces 422.
    # Seedream's edit schema uses `image_urls` (plural list).
    payload = {
        "image_urls": [image_b64],
        "prompt": prompt,
        "sync_mode": True,
    }

    owned, created = _resolve_client(client)
    try:
        response = await owned.post(
            f"{FAL_BASE_URL}/{FAL_EDIT_MODEL}",
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
