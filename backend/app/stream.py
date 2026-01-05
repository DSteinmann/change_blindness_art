from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class StreamHub:
    """Fan-out hub that keeps track of websocket clients and latest telemetry."""

    def __init__(self, history_size: int = 512) -> None:
        self.clients: set[WebSocket] = set()
        self.latest_sample: dict[str, Any] | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.clients.add(websocket)
        await websocket.send_json({"event": "ready", "clients": len(self.clients)})

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        self.latest_sample = payload
        self.history.append(payload)
        dead_clients: list[WebSocket] = []
        for client in list(self.clients):
            try:
                await client.send_json(payload)
            except Exception as exc:  # fastapi raises WebSocketDisconnect
                logger.debug("websocket send failed: %s", exc)
                dead_clients.append(client)
        if dead_clients:
            async with self._lock:
                for client in dead_clients:
                    self.clients.discard(client)
