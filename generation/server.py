"""Generation service: thin FastAPI wrapper over OpenRouter + session replay."""
from __future__ import annotations

import argparse
import io
import os
import time
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from openrouter import IMAGE_MODEL, generate_with_openrouter
from prompts import PromptBank
from sectors import (
    calculate_opposite_region,
    calculate_sector_region,
    create_mask,
    decode_base64_image,
    sector_name,
)
from session_manager import ReplayManager, SessionManager

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
PUPIL_SURFACE_NAME = os.getenv("PUPIL_SURFACE_NAME", "screen")
PUPIL_CONFIDENCE_THRESHOLD = float(os.getenv("PUPIL_CONFIDENCE_THRESHOLD", "0.6"))
GRID_SIZE = int(os.getenv("GRID_SIZE", "3"))
PROMPTS_FILE = Path(__file__).parent / "prompts.txt"
SECTOR_PROMPTS_FILE = Path(__file__).parent / "sector_prompts.json"
SESSIONS_DIR = Path("/app/assets/sessions") if Path("/app/assets").exists() else Path(__file__).parent / "sessions"


def _runtime_snapshot() -> dict[str, Any]:
    return {
        "image_model": IMAGE_MODEL,
        "pupil_surface_name": PUPIL_SURFACE_NAME,
        "pupil_confidence_threshold": PUPIL_CONFIDENCE_THRESHOLD,
        "grid_size": GRID_SIZE,
        "prompts_file_sha": None,  # populated at startup once prompts load
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
def startup_event() -> None:
    prompt_bank.load()
    print(f"OpenRouter API Key: {'✓ Set' if OPENROUTER_API_KEY else '✗ Not set'}")
    print(f"Image model: {IMAGE_MODEL}")
    session_id = session_manager.start_new_session(runtime=_runtime_snapshot())
    print(f"Auto-started recording session: {session_id}")


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


class StartSessionRequest(BaseModel):
    session_id: Optional[str] = None
    participant_id: Optional[str] = None


class CalibrationRequest(BaseModel):
    samples: int
    accuracy: Optional[float] = None
    notes: Optional[str] = None


@app.post("/session/start")
async def start_session(req: Optional[StartSessionRequest] = None) -> dict:
    req = req or StartSessionRequest()
    sid = session_manager.start_new_session(
        session_id=req.session_id,
        participant_id=req.participant_id,
        runtime=_runtime_snapshot(),
    )
    return {"session_id": sid, "status": "recording", "participant_id": req.participant_id}


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

    latency_ms = (time.perf_counter() - t_start) * 1000
    if session_manager.current_session_id:
        session_manager.save_generation(
            generated_image, target, prompt, focus_sector, latency_ms=latency_ms,
        )

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
