#!/usr/bin/env python3
"""Batch-generate peripheral patch images via OpenRouter's image API."""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import sys
import time
from typing import Iterable, cast

import requests

OPENROUTER_CHAT_COMPLETIONS_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"


def _build_headers(api_key: str) -> dict[str, str]:
    """Standard headers for OpenRouter API calls.

    Accept JSON explicitly to avoid any HTML fallbacks.
    """

    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/DSteinmann/change_blindness_art",
        "X-Title": "Change Blindness Patch Generator",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def slugify(value: str, max_length: int = 48) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    if max_length:
        value = value[:max_length].rstrip("-")
    return value or "patch"


def _build_position_clause(object_description: str, positions: list[str]) -> str:
    """Construct a short instruction describing where to place objects.

    This keeps the wording consistent across prompts so that the only
    change is which sections of the canvas are populated.
    """

    if not positions:
        return ""
    if len(positions) == 1:
        return f"Place a single {object_description} in the {positions[0]} of the image."

    # Simple pluralisation; good enough for 'small banana'.
    count = len(positions)
    plural_desc = f"{object_description}s"

    if len(positions) == 2:
        pos_text = f"{positions[0]} and {positions[1]}"
    else:
        pos_text = ", ".join(positions[:-1]) + f" and {positions[-1]}"

    return (
        f"Place {count} {plural_desc} in the image: "
        f"one in the {pos_text}."
    )

def load_prompts(prompt_arg: str | None, prompt_file: pathlib.Path | None) -> list[tuple[str, str]]:
    prompts: list[tuple[str, str]] = []
    if prompt_file:
        for raw_line in prompt_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                label, prompt = line.split("|", 1)
                prompts.append((label.strip(), prompt.strip()))
            else:
                prompts.append((slugify(line), line))
    if prompt_arg:
        prompts.append((slugify(prompt_arg), prompt_arg))
    return prompts

def call_openrouter(
    *,
    model: str,
    prompt: str,
    size: str,
    n: int,
    api_key: str,
    guidance: float | None,
    seed: int | None,
    image_data_url: str | None = None,
) -> dict:
    """Call OpenRouter's chat/completions API and adapt images.

    This uses /api/v1/chat/completions with modalities=["image"] and
    converts the response into the {"data": [{"b64_json": ...}]} shape
    expected by save_images().
    """

    headers = _build_headers(api_key)
    # Build multimodal content: always include text; optionally include
    # a previous image as a data URL so the model can keep the scene
    # consistent while adding new objects.
    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": prompt,
        }
    ]
    if image_data_url is not None:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image_data_url,
                },
            }
        )

    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        # Request only image output; text is not needed for this script.
        "modalities": ["image"],
    }

    response = requests.post(
        OPENROUTER_CHAT_COMPLETIONS_ENDPOINT,
        headers=headers,
        data=json.dumps(payload),
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenRouter chat request failed ({response.status_code}): {response.text}")
    try:
        result = response.json()
    except ValueError as exc:
        snippet = response.text[:200].strip()
        raise RuntimeError(
            "OpenRouter chat response was not valid JSON. "
            f"Snippet: {snippet or '<empty response>'}"
        ) from exc

    # Adapt chat-style image data into the {"data": [{"b64_json": ...}]} shape.
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"No choices returned for prompt: {prompt}")
    message = choices[0].get("message") or {}
    images = message.get("images") or []
    adapted: dict[str, list[dict[str, str]]] = {"data": []}
    for img in images:
        image_url = (img.get("image_url") or {}).get("url")
        if not isinstance(image_url, str):
            continue
        # Expect a data URL: "data:image/png;base64,<data>".
        if "," in image_url:
            _prefix, b64_part = image_url.split(",", 1)
        else:
            # Fallback: treat the whole string as base64 if no comma.
            b64_part = image_url
        adapted["data"].append({"b64_json": b64_part})

    if not adapted["data"]:
        raise RuntimeError(f"No image data URLs returned for prompt: {prompt}")
    return adapted

def save_images(*, data: dict, dst_dir: pathlib.Path, label: str, prompt: str, seed: int | None) -> list[pathlib.Path]:
    saved: list[pathlib.Path] = []
    images = data.get("data") or []
    if not images:
        raise RuntimeError(f"No image payload returned for prompt: {prompt}")
    timestamp = int(time.time())
    for idx, entry in enumerate(images):
        b64 = entry.get("b64_json")
        if not b64:
            continue
        binary = base64.b64decode(b64)
        filename = f"{label}-{timestamp}-{idx:02d}.png"
        path = dst_dir / filename
        path.write_bytes(binary)
        meta = {
            "prompt": prompt,
            "label": label,
            "seed": seed,
            "index": idx,
            "timestamp": timestamp,
        }
        (dst_dir / f"{filename}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        saved.append(path)
    if not saved:
        raise RuntimeError("Image payload missing b64_json entries")
    return saved


def validate_api_key(api_key: str) -> None:
    """Probe OpenRouter to confirm the API key is accepted."""

    headers = _build_headers(api_key)
    try:
        response = requests.get(OPENROUTER_MODELS_ENDPOINT, headers=headers, timeout=20)
    except requests.RequestException as exc:
        raise RuntimeError("Unable to reach OpenRouter to validate API key") from exc
    if response.status_code == 401:
        raise RuntimeError("OpenRouter rejected the API key (401 Unauthorized)")
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter validation failed ({response.status_code}): {response.text[:200].strip()}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        snippet = response.text[:200].strip()
        raise RuntimeError(
            "OpenRouter validation returned non-JSON content. "
            f"Snippet: {snippet or '<empty response>'}"
        ) from exc

    if not isinstance(data, dict) or "data" not in data:
        raise RuntimeError("Unexpected response structure from OpenRouter /models endpoint")


def validate_model_is_image(api_key: str, model_id: str) -> None:
    """Ensure the requested model id exists for this key.

    We only check existence here and let OpenRouter enforce
    whether the model actually supports image generation.
    This avoids relying on internal "type" labels that may
    not match across accounts.
    """

    headers = _build_headers(api_key)
    try:
        response = requests.get(OPENROUTER_MODELS_ENDPOINT, headers=headers, timeout=20)
    except requests.RequestException as exc:
        raise RuntimeError("Unable to reach OpenRouter to inspect models") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        snippet = response.text[:200].strip()
        raise RuntimeError(
            "OpenRouter /models returned non-JSON content while checking model id. "
            f"Snippet: {snippet or '<empty response>'}"
        ) from exc

    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("Unexpected /models payload; cannot validate model id")

    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            # Found a matching model id; let the /images endpoint
            # itself determine whether the call is valid.
            return

    raise RuntimeError(
        f"Model '{model_id}' not found in OpenRouter /models list. "
        "Check the id spelling and that your key has access to it."
    )

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="google/nano-banana", help="OpenRouter model name (default: google/nano-banana)")
    parser.add_argument("--prompt", help="Single prompt to render", dest="prompt")
    parser.add_argument("--prompt-file", type=pathlib.Path, help="Text file with 'label|prompt' lines for batch generation")
    parser.add_argument("--output-dir", type=pathlib.Path, default=pathlib.Path("assets/patches/generated"), help="Directory to store outputs")
    parser.add_argument("--size", default="512x512", help="Image size supported by the model")
    parser.add_argument("--per-prompt", type=int, default=2, help="How many images to request per prompt")
    parser.add_argument("--seed", type=int, help="Optional deterministic seed")
    parser.add_argument("--seed-increment", type=int, default=1, help="Increment seed per prompt when --seed is set")
    parser.add_argument("--guidance", type=float, help="Guidance scale if the model supports it")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without calling the API")
    parser.add_argument(
        "--positions-mode",
        choices=["none", "corners"],
        default="none",
        help=(
            "If set to 'corners', generate a series of variants by placing "
            "the object into successive image corners (upper-left, upper-right, "
            "lower-right, lower-left)."
        ),
    )
    parser.add_argument(
        "--object-description",
        default="small banana",
        help=(
            "Short text describing the repeated object to place, e.g. "
            "'small banana' or 'tiny red square icon'."
        ),
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        help=(
            "Limit the number of positions used when positions-mode is not 'none'. "
            "Defaults to all configured positions."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    # Read API key from the environment for safety; do not hard-code secrets.
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key and not args.dry_run:
        parser.error("OPENROUTER_API_KEY env variable is required unless --dry-run is set")
    if api_key is None:
        api_key = ""
    typed_api_key = cast(str, api_key)
    if not args.dry_run:
        # First verify the key; model access/capabilities are enforced by the API
        # itself when we call the chat/completions endpoint.
        validate_api_key(typed_api_key)

    prompts = load_prompts(args.prompt, args.prompt_file)
    if not prompts:
        parser.error("Provide --prompt or --prompt-file with at least one prompt")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Precompute positions for incremental placement, if enabled.
    positions_sequence: list[str] = []
    if args.positions_mode == "corners":
        positions_sequence = [
            "upper left corner",
            "upper right corner",
            "lower right corner",
            "lower left corner",
        ]
    if args.max_positions is not None and args.max_positions > 0:
        positions_sequence = positions_sequence[: args.max_positions]

    current_seed = args.seed
    for label, base_prompt in prompts:
        # If no special positions mode is enabled, behave exactly as before.
        if not positions_sequence:
            prompt = base_prompt
            payload = {
                "model": args.model,
                "prompt": prompt,
                "size": args.size,
                "n": args.per_prompt,
                "guidance": args.guidance,
                "seed": current_seed,
            }
            if args.dry_run:
                print(json.dumps({"label": label, "payload": payload}, indent=2))
                if current_seed is not None:
                    current_seed += args.seed_increment
                continue
            response = call_openrouter(
                model=args.model,
                prompt=prompt,
                size=args.size,
                n=args.per_prompt,
                api_key=typed_api_key,
                guidance=args.guidance,
                seed=current_seed,
            )
            saved_paths = save_images(
                data=response,
                dst_dir=output_dir,
                label=label,
                prompt=prompt,
                seed=current_seed,
            )
            print(f"Saved {len(saved_paths)} images for '{label}' -> {saved_paths}")
            if current_seed is not None:
                current_seed += args.seed_increment
            continue

        # With a positions sequence, generate a series of variants that
        # progressively fill more sections of the canvas. For each base
        # prompt, we feed the previously generated image back in as an
        # additional input so the scene stays similar while objects are
        # added.
        previous_image_path: pathlib.Path | None = None
        for count in range(1, len(positions_sequence) + 1):
            used_positions = positions_sequence[:count]
            pos_clause = _build_position_clause(args.object_description, used_positions)
            prompt = f"{base_prompt.strip()} {pos_clause}".strip()
            variant_label = f"{label}-p{count}"
            # If we already have a previous image for this label, include it
            # as a data URL so the model can build on it.
            image_data_url: str | None = None
            if previous_image_path is not None:
                prev_bytes = previous_image_path.read_bytes()
                prev_b64 = base64.b64encode(prev_bytes).decode("ascii")
                image_data_url = f"data:image/png;base64,{prev_b64}"
            payload = {
                "model": args.model,
                "prompt": prompt,
                "size": args.size,
                "n": args.per_prompt,
                "guidance": args.guidance,
                "seed": current_seed,
            }
            if args.dry_run:
                debug_info = {"label": variant_label, "payload": payload}
                if previous_image_path is not None:
                    debug_info["image_from"] = str(previous_image_path)
                print(json.dumps(debug_info, indent=2))
                if current_seed is not None:
                    current_seed += args.seed_increment
                continue
            response = call_openrouter(
                model=args.model,
                prompt=prompt,
                size=args.size,
                n=args.per_prompt,
                api_key=typed_api_key,
                guidance=args.guidance,
                seed=current_seed,
                image_data_url=image_data_url,
            )
            saved_paths = save_images(
                data=response,
                dst_dir=output_dir,
                label=variant_label,
                prompt=prompt,
                seed=current_seed,
            )
            if saved_paths:
                previous_image_path = saved_paths[0]
            print(
                f"Saved {len(saved_paths)} images for '{variant_label}' "
                f"with positions {used_positions} -> {saved_paths}"
            )
            if current_seed is not None:
                current_seed += args.seed_increment

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
