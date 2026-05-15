"""OpenRouter image-generation client."""
from __future__ import annotations

import base64
import io
import os

import httpx
from PIL import Image

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
IMAGE_MODEL = os.getenv("OPENROUTER_IMAGE_MODEL", "google/gemini-3-pro-image-preview")
# Text model used to caption the model's autonomous edit. The image model
# routinely drops the text portion of `modalities: ["image", "text"]`, so we
# round-trip a cheaper text-capable model instead.
CAPTION_MODEL = os.getenv("OPENROUTER_CAPTION_MODEL", "google/gemini-2.5-flash")
# Vision-language model used to propose a scene-appropriate edit instruction
# before handing the image off to the inpainting/edit model. Same default as
# the captioner — gemini-2.5-flash is cheap, fast, and good at scene reasoning.
PLANNER_MODEL = os.getenv("OPENROUTER_PLANNER_MODEL", "google/gemini-2.5-flash")
# OpenRouter's image_config.image_size knob: "0.5K" | "1K" | "2K" (default) | "4K".
# 0.5K is fast-mode (gemini-3.1-flash-image-preview only); 2K trades latency
# and bandwidth for noticeably more detail in the model's full-frame regen.
IMAGE_SIZE = os.getenv("OPENROUTER_IMAGE_SIZE", "2K")


def _decode_data_url(url: str) -> Image.Image | None:
    if not url.startswith("data:image"):
        return None
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def _collect_images(message: dict) -> list[Image.Image]:
    """Pull every image attached to an OpenRouter chat-completion message."""
    found: list[Image.Image] = []
    for img_item in message.get("images", []) or []:
        if img_item.get("type") == "image_url":
            img = _decode_data_url(img_item.get("image_url", {}).get("url", ""))
            if img is not None:
                found.append(img)
    content = message.get("content", "")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url":
                img = _decode_data_url(item.get("image_url", {}).get("url", ""))
                if img is not None:
                    found.append(img)
    return found


def _extract_image(message: dict) -> Image.Image | None:
    """Return the largest image in the response.

    `gemini-3-pro-image-preview` sometimes returns multiple images per call
    (a low-res preview followed by a full-res render); naively taking the
    first one yields alternating 1K/2K outputs. Pick the largest by area.
    """
    images = _collect_images(message)
    if not images:
        return None
    if len(images) > 1:
        sizes = [im.size for im in images]
        print(f"OpenRouter returned {len(images)} images: {sizes} - using largest")
    return max(images, key=lambda im: im.size[0] * im.size[1])


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


def _image_config(aspect_ratio: str | None) -> dict:
    cfg: dict = {"image_size": IMAGE_SIZE}
    if aspect_ratio:
        cfg["aspect_ratio"] = aspect_ratio
    return cfg


async def generate_with_openrouter(
    image: Image.Image,
    prompt: str,
    region: tuple[int, int, int, int],
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
    aspect_ratio: str | None = None,
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
        "image_config": _image_config(aspect_ratio),
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


async def caption_edit(
    original_b64: str,
    current_b64: str,
    sector_name: str,
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Ask a text-capable model to describe the change between two images.

    Used because the image-generation model often returns no text content even
    when `modalities: ["image", "text"]` is requested. Returns None on any
    failure — caller should fall back to a synthesised caption.
    """
    payload = {
        "model": CAPTION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": original_b64}},
                {"type": "image_url", "image_url": {"url": current_b64}},
                {"type": "text", "text": (
                    "Compare these two images. The first is the scene BEFORE the "
                    "latest edit; the second is AFTER. Exactly ONE deliberate change "
                    f"was made somewhere in the {sector_name} region — something may "
                    "have been added, transformed, or removed. In one sentence, "
                    "describe ONLY what is different in the second image relative "
                    "to the first. Phrase removals naturally (e.g. \"the lamppost "
                    "is gone\"). Do not describe anything that is the same in both. "
                    "Reply with just the caption - no preface."
                )},
            ],
        }],
    }
    owned, created = _resolve_client(client)
    try:
        message = await _post_chat(payload, api_key, owned)
    except Exception as exc:
        print(f"caption_edit failed: {exc}")
        return None
    finally:
        if created:
            await owned.aclose()
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = (part.get("text") or "").strip()
                if text:
                    return text
        return None
    if isinstance(content, str):
        return content.strip() or None
    return None


async def plan_edit(
    image_b64: str,
    target_sector: str,
    focus_sector: str | None,
    prior_edits: list[str],
    api_key: str,
    *,
    salience: str = "subtle",
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Ask a vision-language model to propose a scene-appropriate edit
    instruction.

    `target_sector` and `focus_sector` should be human-readable descriptions
    ("upper-left", "lower-right", etc.) — the model otherwise has to guess what
    "BR" means.

    Returns a single-sentence prompt or None on failure.
    """
    if not api_key:
        return None
    salience_hint = {
        "subtle": (
            "Pick a SMALL, plausible, naturalistic element — the kind of "
            "detail a viewer could miss in peripheral vision."
        ),
        "moderate": (
            "Pick a small-to-medium element that still feels native to the scene."
        ),
        "bold": (
            "Pick a dramatic or atmospheric element that still fits the scene's mood."
        ),
    }.get(salience, "")
    history_block = ""
    if prior_edits:
        bullets = "\n".join(f"  - {e}" for e in prior_edits)
        history_block = (
            f"\n\nPrior edits already applied in this session (DO NOT repeat "
            f"any of these object classes or motifs):\n{bullets}"
        )
    gaze_block = ""
    if focus_sector:
        gaze_block = (
            f"\n\nThe participant is currently FIXATED on the {focus_sector} "
            f"area. The change must go in the OPPOSITE area — the "
            f"{target_sector} area — which is in their peripheral vision. "
            f"This is a change-blindness study: the goal is for the participant "
            f"to NOT notice the edit while they are looking elsewhere."
        )
    instruction = (
        "Look at this image. Identify the scene type (e.g. urban skyline at "
        "night, rural landscape, indoor still life, portrait), its visual "
        "style (photograph, painting, illustration), and the existing palette "
        "and lighting."
        f"{gaze_block}\n\n"
        f"Propose ONE edit to apply in the {target_sector} area of the image "
        f"(imagine the image divided into a 3x3 grid; you are picking "
        f"something for the {target_sector} cell). The edit MUST:\n"
        "  - Be an object, creature, or phenomenon that would plausibly "
        "appear in THIS kind of scene (no jellyfish in a city; no skyscrapers "
        "in a forest; no fantastical creatures in a real photograph).\n"
        "  - Match the existing visual style, palette, and lighting.\n"
        "  - Be ADDING something genuinely new. Look at what is already in "
        f"the {target_sector} area: if it already contains birds, propose "
        "something other than birds; if it already contains boats, propose "
        "something other than boats. Never propose adding more of something "
        "that is already plentiful there.\n"
        "  - Be fully self-contained within the "
        f"{target_sector} area. Describe it using only what is in that area. "
        "Do NOT reference landmarks, towers, or objects that may lie in a "
        "different part of the image (e.g. do not say 'above the tower' if "
        f"the tower is not in the {target_sector} area).\n"
        f"  - {salience_hint}"
        f"{history_block}\n\n"
        "Respond with ONLY the edit instruction as a single short sentence, "
        "phrased as a directive (e.g. 'add a small commercial airplane high "
        "in the sky' or 'place a worn paperback on the table edge'). "
        "Do not preface, explain, or include any other text."
    )
    payload = {
        "model": PLANNER_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_b64}},
                {"type": "text", "text": instruction},
            ],
        }],
    }
    owned, created = _resolve_client(client)
    try:
        message = await _post_chat(payload, api_key, owned)
    except Exception as exc:
        print(f"plan_edit failed: {exc}")
        return None
    finally:
        if created:
            await owned.aclose()
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = (part.get("text") or "").strip()
                if text:
                    return text
        return None
    if isinstance(content, str):
        return content.strip() or None
    return None


async def generate_with_openrouter_semantic(
    messages: list[dict],
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
    aspect_ratio: str | None = None,
) -> dict:
    """Semantic-mode multi-turn request. Caller is responsible for emitting
    a valid chat-completions `messages` array (see `semantic.build_messages`).
    Returns the raw `message` dict from the final assistant turn."""
    payload = {
        "model": IMAGE_MODEL,
        "messages": messages,
        "modalities": ["image", "text"],
        "image_config": _image_config(aspect_ratio),
    }
    owned, created = _resolve_client(client)
    try:
        return await _post_chat(payload, api_key, owned)
    finally:
        if created:
            await owned.aclose()
