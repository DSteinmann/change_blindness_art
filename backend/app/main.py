from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .patch_manager import PatchManager
from .pupil_source import PupilSource
from .stream import StreamHub

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aria-backend")

settings = get_settings()
app = FastAPI(title="Aria Gaze Patch Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.mount("/assets", StaticFiles(directory=str(settings.patch_dir)), name="assets")

stream_hub = StreamHub(history_size=settings.telemetry_history)
patch_manager = PatchManager(settings.patch_dir)
pupil_source = PupilSource(settings, stream_hub.broadcast)
patch_usage_log: list[dict[str, Any]] = []


@app.on_event("startup")
async def _startup() -> None:
    logger.info("Starting backend...")
    await patch_manager.load()
    await pupil_source.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    logger.info("Stopping backend")
    await pupil_source.stop()


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
