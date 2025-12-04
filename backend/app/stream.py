from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from typing import Any, Awaitable, Callable

import zmq
import zmq.asyncio
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


class ZmqRelaySubscriber:
    """Consumes PUB frames from the relay and pushes them into the hub."""

    def __init__(self, endpoint: str, handler: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.endpoint = endpoint
        self.handler = handler
        self._ctx = zmq.asyncio.Context.instance()
        self._socket: zmq.asyncio.Socket | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task:
            return
        socket = self._ctx.socket(zmq.SUB)
        socket.setsockopt(zmq.SUBSCRIBE, b"")
        socket.connect(self.endpoint)
        self._socket = socket
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info("ZMQ subscriber connected to %s", self.endpoint)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._socket is not None:
            self._socket.close(0)
            self._socket = None

    async def _loop(self) -> None:
        assert self._socket is not None
        while not self._stop_event.is_set():
            try:
                frame = await self._socket.recv()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("ZMQ receive error: %s", exc)
                await asyncio.sleep(0.5)
                continue
            try:
                payload = json.loads(frame.decode("utf-8"))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed frame: %s", frame)
                continue
            await self.handler(payload)

    async def __aenter__(self) -> "ZmqRelaySubscriber":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()
