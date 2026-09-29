from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .pupil_source import PupilSource
from .stream import StreamHub

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("blinkpatch-backend")

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _relay_client
    logger.info("Starting backend...")
    _relay_client = httpx.AsyncClient()
    await pupil_source.start()
    yield
    logger.info("Stopping backend")
    await pupil_source.stop()
    await _relay_client.aclose()


app = FastAPI(title="BlinkPatch Backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.mount("/assets", StaticFiles(directory=str(settings.patch_dir)), name="assets")
app.mount("/sessions", StaticFiles(directory=str(settings.sessions_dir)), name="sessions")

stream_hub = StreamHub()

_relay_client: httpx.AsyncClient | None = None


_blink_relay_failures = 0


async def _relay_blink_onset(state: str) -> None:
    """Fire-and-forget POST so the generation service can increment its
    per-session blink counter. Swallow failures — blink recording is telemetry,
    not load-bearing — but log loudly enough to diagnose misconfiguration."""
    global _blink_relay_failures
    if _relay_client is None:
        return
    try:
        await _relay_client.post(
            f"{settings.generation_internal_url}/session/blink", timeout=2.0,
        )
    except Exception as exc:
        _blink_relay_failures += 1
        if _blink_relay_failures <= 3 or _blink_relay_failures % 50 == 0:
            logger.warning(
                "blink relay #%d to %s/session/blink failed: %s",
                _blink_relay_failures, settings.generation_internal_url, exc,
            )


pupil_source = PupilSource(settings, stream_hub.broadcast, on_blink_onset=_relay_blink_onset)


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
