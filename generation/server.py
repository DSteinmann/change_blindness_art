"""Generation service: thin FastAPI wrapper over OpenRouter + session replay."""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from openrouter import (
    IMAGE_MODEL,
    caption_edit,
    generate_with_openrouter,
    generate_with_openrouter_semantic,
)
from prompts import PromptBank
from sectors import (
    calculate_opposite_region,
    calculate_sector_region,
    create_mask,
    decode_base64_image,
    infer_aspect_ratio,
    sector_name,
    shrink_for_api,
)
from semantic import (
    SemanticHistory,
    SemanticTurn,
    build_messages,
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
IDLE_THRESHOLD_SEC = int(os.getenv("IDLE_RESET_SEC", "180"))
IDLE_TICK_SEC = 30
# The captioner doesn't need detail to describe a change; smaller inputs cut
# round-trip time roughly in half.
CAPTION_INPUT_MAX_EDGE = 512
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

_last_generation_ts: float = time.time()
_last_session_started_ts: float = time.time()
_idle_task: Optional[asyncio.Task] = None
_idle_lock: Optional[asyncio.Lock] = None  # constructed at startup on the running loop

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
    global _backend_client, GENERATION_MODE, _idle_task, _idle_lock
    _backend_client = httpx.AsyncClient()
    _idle_lock = asyncio.Lock()
    prompt_bank.load()
    print(f"OpenRouter API Key: {'✓ Set' if OPENROUTER_API_KEY else '✗ Not set'}")
    print(f"Image model: {IMAGE_MODEL}")
    if GENERATION_MODE == "semantic" and not OPENROUTER_API_KEY:
        print("GENERATION_MODE=semantic but OPENROUTER_API_KEY missing; coercing to cycling")
        GENERATION_MODE = "cycling"
    print(f"Generation mode: {GENERATION_MODE}")
    session_id = session_manager.start_new_session(runtime=_runtime_snapshot())
    print(f"Auto-started recording session: {session_id}")
    _idle_task = asyncio.create_task(_idle_watcher())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if _idle_task is not None:
        _idle_task.cancel()
    if _backend_client is not None:
        await _backend_client.aclose()


async def _maybe_idle_reset() -> None:
    """Rotate to a new session if no /generate and no /session/start has fired
    for IDLE_THRESHOLD_SEC. Skips if a generation is in flight (lock held)."""
    global _last_session_started_ts
    if _idle_lock is None or _idle_lock.locked():
        return
    async with _idle_lock:
        now = time.time()
        stale = (
            now - _last_generation_ts > IDLE_THRESHOLD_SEC
            and now - _last_session_started_ts > IDLE_THRESHOLD_SEC
        )
        if not stale:
            return
        prev_sid = session_manager.current_session_id
        sid = session_manager.start_new_session(runtime=_runtime_snapshot())
        if prev_sid and prev_sid != sid:
            semantic_history.clear(prev_sid)
        _last_session_started_ts = now
        if _backend_client is not None:
            try:
                await _backend_client.post(
                    f"{BACKEND_URL}/events/session_started",
                    json={"session_id": sid, "participant_id": None},
                    timeout=2.0,
                )
            except Exception as exc:
                print(f"idle session_started relay failed: {exc}")
        print(f"Idle rotation: new session {sid}")


async def _idle_watcher() -> None:
    while True:
        try:
            await asyncio.sleep(IDLE_TICK_SEC)
            await _maybe_idle_reset()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"idle watcher error: {exc}")


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


class StartSessionRequest(BaseModel):
    session_id: Optional[str] = None
    participant_id: Optional[str] = None


@app.post("/session/start")
async def start_session(req: Optional[StartSessionRequest] = None) -> dict:
    req = req or StartSessionRequest()
    prev_sid = session_manager.current_session_id
    sid = session_manager.start_new_session(
        session_id=req.session_id,
        participant_id=req.participant_id,
        runtime=_runtime_snapshot(),
    )
    if prev_sid and prev_sid != sid:
        semantic_history.clear(prev_sid)
    if _backend_client is not None:
        try:
            await _backend_client.post(
                f"{BACKEND_URL}/events/session_started",
                json={"session_id": sid, "participant_id": req.participant_id},
                timeout=2.0,
            )
        except Exception as exc:
            print(f"session_started relay failed: {exc}")
    global _last_session_started_ts
    _last_session_started_ts = time.time()
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
async def generate(request: GenerateRequest, background: BackgroundTasks) -> Response:
    if _idle_lock is None:
        return await _generate_impl(request, background)
    async with _idle_lock:
        return await _generate_impl(request, background)


async def _caption_after_response(
    session_id: str,
    entry_index: int,
    turn_index: int,
    original_b64: str,
    edit_b64: str,
    target: str,
    prior_captions_lower: list[str],
) -> None:
    """Run the captioner outside the request-response window and stitch the
    result into both the persisted entry and the in-memory SemanticHistory turn."""
    caption = await caption_edit(original_b64, edit_b64, target, OPENROUTER_API_KEY)
    if not caption:
        return
    duplicate = any(
        caption.lower() in p or p in caption.lower() for p in prior_captions_lower
    )
    if session_manager.update_caption(session_id, entry_index, caption, duplicate=duplicate):
        semantic_history.update_caption_at(session_id, turn_index, caption)


async def _generate_impl(request: GenerateRequest, background: BackgroundTasks) -> Response:
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

    # Snap the input's aspect to the closest model-supported ratio so the
    # model's output keeps the same proportions; otherwise sector compositing
    # maps the wrong pixels.
    aspect_ratio = infer_aspect_ratio(*init_image.size)

    caption: str | None = None
    duplicate_caption = False
    semantic_success = False
    generated_image = init_image
    pending_caption_args: dict | None = None

    if GENERATION_MODE == "semantic" and OPENROUTER_API_KEY:
        session_id = session_manager.current_session_id or "anon"
        # Bound payload size: OpenRouter rejects images >30MB and the multi-turn
        # message array carries the original + up to HISTORY_WINDOW prior edits.
        current_compressed = shrink_for_api(init_image)
        semantic_history.set_original(session_id, current_compressed)
        prior_turns = semantic_history.turns(session_id)
        messages = build_messages(
            original_b64=semantic_history.original(session_id) or current_compressed,
            turns=prior_turns,
            target_sector=target,
            region=region,
        )
        try:
            message = await generate_with_openrouter_semantic(
                messages, OPENROUTER_API_KEY, aspect_ratio=aspect_ratio,
            )
        except Exception as first_err:
            print(f"semantic first attempt failed: {first_err} - retrying with terser prompt")
            terser = (
                f"Add one new small element to the {target} sector "
                f"(pixel rectangle x1={region[0]}, y1={region[1]}, x2={region[2]}, "
                f"y2={region[3]}). Keep all prior additions and the rest of the "
                "image unchanged."
            )
            for part in messages[-1]["content"]:
                if part.get("type") == "text":
                    part["text"] = terser
                    break
            try:
                message = await generate_with_openrouter_semantic(
                    messages, OPENROUTER_API_KEY, aspect_ratio=aspect_ratio,
                )
            except Exception as retry_err:
                print(f"semantic retry failed: {retry_err}")
                message = None
        if message is not None:
            image_out, caption = parse_response(message)
            if image_out is not None:
                generated_image = image_out
                semantic_success = True
                prior_captions_lower = [c.lower() for c in semantic_history.captions(session_id)]
                # Replay the prior edit as an assistant turn next time, keeping
                # the cumulative-edit chain visible to the model.
                edit_b64 = shrink_for_api(image_out, max_edge=CAPTION_INPUT_MAX_EDGE)
                turn_index = semantic_history.append_turn(
                    session_id,
                    SemanticTurn(target_sector=target, image_b64=edit_b64, caption=caption),
                )
                if caption is not None:
                    # Rare: the image model actually included text. Use it as-is.
                    if any(caption.lower() in p or p in caption.lower() for p in prior_captions_lower):
                        duplicate_caption = True
                else:
                    # Common: image model dropped the text portion. Schedule
                    # a captioner call as a background task so the response
                    # returns immediately. We pass the prior cumulative state
                    # (current_compressed = the request's input image) as the
                    # "before" image so the captioner describes only THIS
                    # turn's new addition rather than every accumulated edit.
                    pending_caption_args = {
                        "session_id": session_id,
                        "turn_index": turn_index,
                        "original_b64": current_compressed,
                        "edit_b64": edit_b64,
                        "target": target,
                        "prior_captions_lower": prior_captions_lower,
                    }

    if not semantic_success:
        if OPENROUTER_API_KEY:
            try:
                generated_image = await generate_with_openrouter(
                    init_image, prompt, region, OPENROUTER_API_KEY,
                    aspect_ratio=aspect_ratio,
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
    # In semantic mode the model decided autonomously; the curated prompt
    # string was never seen by it. Record that honestly in metadata.
    recorded_prompt = "semantic auto-edit" if semantic_success else prompt
    if session_manager.current_session_id:
        entry = session_manager.save_generation(
            generated_image, target, recorded_prompt, focus_sector,
            latency_ms=latency_ms, caption=caption,
            duplicate_caption=duplicate_caption,
        )
        await _notify_backend(entry)
        if pending_caption_args is not None:
            # Schedule the captioner to run after the response is sent. It
            # will late-update the entry's caption + edit_history when it
            # completes, without blocking this request.
            background.add_task(
                _caption_after_response,
                entry_index=entry["index"],
                **pending_caption_args,
            )

    buf = io.BytesIO()
    generated_image.save(buf, format="PNG")
    buf.seek(0)
    global _last_generation_ts
    _last_generation_ts = time.time()
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
