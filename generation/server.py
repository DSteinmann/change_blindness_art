"""Generation service: thin FastAPI wrapper over OpenRouter + session replay."""
from __future__ import annotations

import argparse
import io
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from openrouter import (
    IMAGE_MODEL,
    generate_with_openrouter,
    generate_with_openrouter_semantic,
)
from prompts import PromptBank
from sectors import (
    calculate_opposite_region,
    calculate_sector_region,
    create_mask,
    decode_base64_image,
    sector_name,
)
from semantic import (
    SemanticHistory,
    build_prompt,
    degenerate_caption,
    parse_response,
)
from session_manager import ReplayManager, SessionManager

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
PUPIL_SURFACE_NAME = os.getenv("PUPIL_SURFACE_NAME", "screen")
PUPIL_CONFIDENCE_THRESHOLD = float(os.getenv("PUPIL_CONFIDENCE_THRESHOLD", "0.6"))
GRID_SIZE = int(os.getenv("GRID_SIZE", "3"))
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
GENERATION_MODE = os.getenv("GENERATION_MODE", "cycling").lower()
PROMPTS_FILE = Path(__file__).parent / "prompts.txt"
SECTOR_PROMPTS_FILE = Path(__file__).parent / "sector_prompts.json"
SESSIONS_DIR = Path("/app/assets/sessions") if Path("/app/assets").exists() else Path(__file__).parent / "sessions"


def _runtime_snapshot() -> dict[str, Any]:
    return {
        "image_model": IMAGE_MODEL,
        "mode": GENERATION_MODE,
        "pupil_surface_name": PUPIL_SURFACE_NAME,
        "pupil_confidence_threshold": PUPIL_CONFIDENCE_THRESHOLD,
        "grid_size": GRID_SIZE,
    }

app = FastAPI(title="Generation Server (OpenRouter)", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

prompt_bank = PromptBank(PROMPTS_FILE, SECTOR_PROMPTS_FILE)
session_manager = SessionManager(SESSIONS_DIR)
replay_manager = ReplayManager(session_manager)
semantic_history = SemanticHistory()

_backend_client: httpx.AsyncClient | None = None


async def _notify_backend(entry: dict) -> None:
    """Fire-and-forget: tell the backend a new image is on disk so observer /
    feed clients get a WS push instead of polling. Failures are debug-only."""
    if _backend_client is None or not session_manager.current_session_id:
        return
    try:
        await _backend_client.post(
            f"{BACKEND_URL}/events/generation",
            json={"session_id": session_manager.current_session_id, **entry},
            timeout=2.0,
        )
    except Exception as exc:
        print(f"generation relay failed: {exc}")


class GenerateRequest(BaseModel):
    image_base64: str
    focus_x: float
    focus_y: float
    target_row: Optional[int] = None
    target_col: Optional[int] = None
    grid_size: int = 3
    strength: float = 0.75
    peripheral_size: float = 0.3


@app.on_event("startup")
async def startup_event() -> None:
    global _backend_client, GENERATION_MODE
    _backend_client = httpx.AsyncClient()
    prompt_bank.load()
    print(f"OpenRouter API Key: {'✓ Set' if OPENROUTER_API_KEY else '✗ Not set'}")
    print(f"Image model: {IMAGE_MODEL}")
    if GENERATION_MODE == "semantic" and not OPENROUTER_API_KEY:
        print("GENERATION_MODE=semantic but OPENROUTER_API_KEY missing; coercing to cycling")
        GENERATION_MODE = "cycling"
    print(f"Generation mode: {GENERATION_MODE}")
    session_id = session_manager.start_new_session(runtime=_runtime_snapshot())
    print(f"Auto-started recording session: {session_id}")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if _backend_client is not None:
        await _backend_client.aclose()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "api_configured": bool(OPENROUTER_API_KEY),
        "image_model": IMAGE_MODEL,
        "prompts_loaded": len(prompt_bank.prompts),
        "sector_prompts_loaded": len(prompt_bank.sector_prompts),
        "current_prompt_index": prompt_bank.prompt_index,
    }


@app.get("/prompts")
async def get_prompts() -> dict:
    return {
        "prompts": prompt_bank.prompts,
        "current_index": prompt_bank.prompt_index,
        "total": len(prompt_bank.prompts),
    }


@app.post("/reset")
async def reset_prompt_index() -> dict:
    prompt_bank.reset()
    return {"message": "Prompt indices reset"}


class CalibrationRequest(BaseModel):
    samples: int
    accuracy: Optional[float] = None
    notes: Optional[str] = None


@app.post("/session/start")
async def start_session(
    session_id: Optional[str] = None,
    participant_id: Optional[str] = None,
) -> dict:
    sid = session_manager.start_new_session(
        session_id=session_id,
        participant_id=participant_id,
        runtime=_runtime_snapshot(),
    )
    return {"session_id": sid, "status": "recording", "participant_id": participant_id}


@app.post("/session/blink")
async def record_blink() -> dict:
    session_manager.record_blink()
    return {"ok": True}


@app.post("/session/calibration")
async def record_calibration(req: CalibrationRequest) -> dict:
    session_manager.record_calibration(req.model_dump())
    return {"ok": True}


@app.get("/session/list")
async def list_sessions() -> dict:
    return {"sessions": session_manager.list_sessions()}


@app.get("/session/{session_id}")
async def get_session(session_id: str) -> dict:
    try:
        return session_manager.load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@app.post("/session/replay/{session_id}")
async def start_replay(session_id: str) -> dict:
    try:
        replay_manager.start_replay(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    total = len(replay_manager.replay_metadata["sequence"]) if replay_manager.replay_metadata else 0
    return {"session_id": session_id, "status": "replaying", "total_generations": total}


@app.post("/session/replay/stop")
async def stop_replay() -> dict:
    replay_manager.stop_replay()
    return {"status": "stopped"}


@app.get("/session/replay/next")
async def replay_next() -> Response:
    if not replay_manager.is_replaying():
        raise HTTPException(status_code=400, detail="Not in replay mode")
    entry = replay_manager.get_next_generation()
    if not entry:
        return Response(status_code=204)
    buf = io.BytesIO()
    entry["image"].save(buf, format="PNG")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={
            "X-Sector": entry["target_sector"],
            "X-Prompt": entry["prompt"][:100],
            "X-Index": str(entry["index"]),
        },
    )


@app.post("/generate")
async def generate(request: GenerateRequest) -> Response:
    t_start = time.perf_counter()
    try:
        init_image = decode_base64_image(request.image_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to decode image: {e}")

    if request.target_row is not None and request.target_col is not None:
        region = calculate_sector_region(
            request.target_row, request.target_col,
            request.grid_size, init_image.width, init_image.height,
        )
        target = sector_name(request.target_row, request.target_col)
        prompt = prompt_bank.for_sector(request.target_row, request.target_col)
        focus_sector = sector_name(
            (request.grid_size - 1) - request.target_row,
            (request.grid_size - 1) - request.target_col,
        )
        print(f"Sector-based: modifying {target} with prompt: {prompt[:50]}...")
    else:
        region = calculate_opposite_region(
            request.focus_x, request.focus_y,
            init_image.width, init_image.height,
            request.peripheral_size,
        )
        prompt = prompt_bank.next_cycling()
        target = "legacy"
        focus_sector = "unknown"
        print(f"Legacy: focus at ({request.focus_x:.2f}, {request.focus_y:.2f})")

    # Mask is unused by OpenRouter but kept for any future local backend.
    _ = create_mask(init_image.size, region)

    caption: str | None = None
    duplicate_caption = False
    semantic_success = False
    generated_image = init_image

    if GENERATION_MODE == "semantic" and OPENROUTER_API_KEY:
        session_id = session_manager.current_session_id or "anon"
        semantic_history.set_original(session_id, request.image_base64)
        prior_captions = semantic_history.captions(session_id)
        parts = build_prompt(
            original_b64=semantic_history.original(session_id) or request.image_base64,
            current_b64=request.image_base64,
            captions=prior_captions,
            region=region,
            sector_name=target,
        )
        try:
            message = await generate_with_openrouter_semantic(parts, OPENROUTER_API_KEY)
        except Exception as first_err:
            print(f"semantic first attempt failed: {first_err} - retrying with terser prompt")
            parts[-1]["text"] = (
                "Propose one new small edit distinct from any prior edit, inside the "
                f"pixel rectangle (x1={region[0]}, y1={region[1]}, x2={region[2]}, "
                f"y2={region[3]}). Return the full modified image and a one-sentence "
                'CAPTION: describing what you changed.'
            )
            try:
                message = await generate_with_openrouter_semantic(parts, OPENROUTER_API_KEY)
            except Exception as retry_err:
                print(f"semantic retry failed: {retry_err}")
                message = None
        if message is not None:
            image_out, caption = parse_response(message)
            if image_out is not None:
                generated_image = image_out
                semantic_success = True
                prior_lower = [c.lower() for c in prior_captions]
                if caption and any(caption.lower() in p or p in caption.lower() for p in prior_lower):
                    duplicate_caption = True
                if caption is None:
                    caption = degenerate_caption(session_manager.sequence_index, target)

    if not semantic_success:
        if OPENROUTER_API_KEY:
            try:
                generated_image = await generate_with_openrouter(
                    init_image, prompt, region, OPENROUTER_API_KEY,
                )
            except Exception as api_err:
                print(f"OpenRouter API failed: {api_err}")
                generated_image = init_image
        else:
            print("No API key set - returning original image")
            generated_image = init_image
        caption = None
        duplicate_caption = False

    latency_ms = (time.perf_counter() - t_start) * 1000
    entry: dict = {}
    if session_manager.current_session_id:
        entry = session_manager.save_generation(
            generated_image, target, prompt, focus_sector,
            latency_ms=latency_ms, caption=caption,
            duplicate_caption=duplicate_caption,
        )
        await _notify_backend(entry)
        if semantic_success and caption:
            semantic_history.append(session_manager.current_session_id, caption)

    buf = io.BytesIO()
    generated_image.save(buf, format="PNG")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={
            "X-Prompt-Used": prompt[:100],
            "X-Prompt-Index": str(prompt_bank.prompt_index - 1),
            "X-Target-Sector": target,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
