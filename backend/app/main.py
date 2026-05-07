from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .patch_manager import PatchManager
from .pupil_source import PupilSource
from .stream import StreamHub

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("blinkpatch-backend")

settings = get_settings()
app = FastAPI(title="BlinkPatch Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.mount("/assets", StaticFiles(directory=str(settings.patch_dir)), name="assets")
app.mount("/sessions", StaticFiles(directory=str(settings.sessions_dir)), name="sessions")

stream_hub = StreamHub(history_size=settings.telemetry_history)
patch_manager = PatchManager(settings.patch_dir)
patch_usage_log: list[dict[str, Any]] = []

_relay_client: httpx.AsyncClient | None = None


async def _relay_blink_onset(state: str) -> None:
    """Fire-and-forget POST so the generation service can increment its
    per-session blink counter. Swallow failures — blink recording is telemetry,
    not load-bearing."""
    if _relay_client is None:
        return
    try:
        await _relay_client.post(f"{settings.generation_api}/session/blink", timeout=2.0)
    except Exception as exc:
        logger.debug("blink relay failed: %s", exc)


pupil_source = PupilSource(settings, stream_hub.broadcast, on_blink_onset=_relay_blink_onset)


@app.on_event("startup")
async def _startup() -> None:
    global _relay_client
    logger.info("Starting backend...")
    _relay_client = httpx.AsyncClient()
    await patch_manager.load()
    await pupil_source.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("Stopping backend")
    await pupil_source.stop()
    if _relay_client is not None:
        await _relay_client.aclose()


@app.get("/config")
async def runtime_config() -> dict[str, Any]:
    """Shared runtime spec consumed by the frontend on load."""
    return {
        "grid_size": settings.grid_size,
        "fixation_duration_ms": settings.fixation_duration_ms,
        "gaze_smoothing_factor": settings.gaze_smoothing_factor,
        "gaze_stale_ms": settings.gaze_stale_ms,
        "generation_api": settings.generation_api,
        "surface_name": settings.pupil_surface_name,
    }


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "latest_sample_ts": stream_hub.latest_sample.get("ts") if stream_hub.latest_sample else None,
        "connected_clients": len(stream_hub.clients),
    }


@app.get("/telemetry/latest")
async def latest_sample() -> JSONResponse:
    if not stream_hub.latest_sample:
        raise HTTPException(status_code=404, detail="No telemetry yet")
    return JSONResponse(stream_hub.latest_sample)


@app.get("/patch/next")
async def get_next_patch(stimulus: str | None = None) -> dict[str, Any]:
    return await patch_manager.next_patch(stimulus)


@app.post("/patch/use")
async def register_patch_use(event: dict[str, Any]) -> dict[str, Any]:
    record = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "payload": event,
    }
    patch_usage_log.append(record)
    return record


@app.post("/events/generation")
async def relay_generation(event: dict[str, Any]) -> dict[str, Any]:
    """Generation service pings us after each save so observer/feed pages
    can react without polling."""
    await stream_hub.broadcast({"event": "generation", **event})
    return {"ok": True}


@app.post("/events/swap")
async def relay_swap(event: dict[str, Any]) -> dict[str, Any]:
    """Main frontend pings us when a pending image is actually swapped in."""
    await stream_hub.broadcast({"event": "swap", **event})
    return {"ok": True}


@app.post("/events/session_started")
async def relay_session_started(event: dict[str, Any]) -> dict[str, Any]:
    """Generation service pings us on participant rollover so observer+feed can
    reset their local state."""
    await stream_hub.broadcast({"event": "session_started", **event})
    return {"ok": True}


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    await stream_hub.register(websocket)
    ping_task = asyncio.create_task(_ping_client(websocket))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        ping_task.cancel()
        await stream_hub.unregister(websocket)


async def _ping_client(websocket: WebSocket) -> None:
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"event": "ping"})
    except Exception:
        pass
