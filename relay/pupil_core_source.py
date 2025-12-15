from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import msgpack  # type: ignore
import zmq
import zmq.asyncio  # type: ignore

from blink_utils import clamp

if TYPE_CHECKING:  # pragma: no cover - typing helper only
    from aria_stream_relay import RelayConfig


def _make_publishers(publisher: zmq.asyncio.Socket):
    loop = asyncio.get_running_loop()

    async def _send(payload: dict[str, Any]) -> None:
        await publisher.send_json(payload)

    def publish_async(payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(_send(payload), loop)

    return loop, publish_async


async def pupil_core_source(publisher: zmq.asyncio.Socket, cfg: "RelayConfig") -> None:
    """Subscribe to a Pupil Core runtime (via Pupil Remote) and relay gaze/blink samples."""

    loop, publish_async = _make_publishers(publisher)

    remote_address = f"tcp://{cfg.pupil_host}:{cfg.pupil_remote_port}"
    ctx = zmq.Context.instance()
    request_socket = ctx.socket(zmq.REQ)
    request_socket.connect(remote_address)
    print(f"[relay] Connecting to Pupil Remote at {remote_address}", flush=True)

    try:
        request_socket.send_string("SUB_PORT")
        sub_port = request_socket.recv_string()
        print(f"[relay] Received Pupil SUB_PORT={sub_port}", flush=True)
    except Exception as exc:  # pragma: no cover - requires hardware/runtime
        request_socket.close(0)
        raise RuntimeError(
            "Unable to reach the Pupil Remote plugin. Ensure Pupil Capture/Core is running "
            "with Remote enabled and that the host/port are correct."
        ) from exc

    sub_address = f"tcp://{cfg.pupil_host}:{sub_port}"
    gaze_socket = ctx.socket(zmq.SUB)
    gaze_socket.connect(sub_address)
    gaze_socket.setsockopt_string(zmq.SUBSCRIBE, cfg.pupil_topic)
    print(
        f"[relay] Subscribed to gaze topic prefix '{cfg.pupil_topic}' on {sub_address}",
        flush=True,
    )

    blink_socket = ctx.socket(zmq.SUB)
    blink_socket.connect(sub_address)
    blink_socket.setsockopt_string(zmq.SUBSCRIBE, "blinks")
    print(f"[relay] Subscribed to blink topic 'blinks' on {sub_address}", flush=True)

    poller = zmq.Poller()
    poller.register(gaze_socket, zmq.POLLIN)
    poller.register(blink_socket, zmq.POLLIN)

    stop_event = threading.Event()
    done_fut: asyncio.Future[None] = loop.create_future()
    last_log = time.monotonic()
    samples_forwarded = 0

    def close_sockets() -> None:
        with suppress(Exception):
            gaze_socket.close(0)
        with suppress(Exception):
            blink_socket.close(0)
        with suppress(Exception):
            request_socket.close(0)

    def run() -> None:
        nonlocal last_log, samples_forwarded
        try:
            while not stop_event.is_set():
                try:
                    socks = dict(poller.poll(timeout=100))
                except zmq.ZMQError:  # pragma: no cover
                    break  # context terminated

                if not socks:
                    continue

                if gaze_socket in socks:
                    frames = gaze_socket.recv_multipart(flags=zmq.NOBLOCK)
                    if len(frames) < 2:
                        continue
                    sample_obj = msgpack.loads(frames[1], raw=False)
                    if not isinstance(sample_obj, dict):
                        continue
                    norm_pos = sample_obj.get("norm_pos") or (0.5, 0.5)
                    x_norm = clamp(float(norm_pos[0]))
                    y_norm = clamp(1.0 - float(norm_pos[1]))
                    confidence = float(sample_obj.get("confidence", 0.0))
                    valid = confidence >= cfg.pupil_confidence_threshold
                    ts_raw = sample_obj.get("timestamp") or sample_obj.get("timestamp_epoch")
                    ts = float(ts_raw) if ts_raw is not None else time.time()
                    gaze_payload = {"x_norm": x_norm, "y_norm": y_norm, "valid": valid}
                    publish_async({"ts": ts, "event": "sample", "gaze": gaze_payload})
                    samples_forwarded += 1

                if blink_socket in socks:
                    frames = blink_socket.recv_multipart(flags=zmq.NOBLOCK)
                    if len(frames) < 2:
                        continue
                    blink_obj = msgpack.loads(frames[1], raw=False)
                    print(f"[relay][debug] Received blink object: {blink_obj}", flush=True)
                    if not isinstance(blink_obj, dict):
                        continue
                    blink_type = blink_obj.get("type")
                    blink_state = "closed" if blink_type == "onset" else "open"
                    ts_raw = blink_obj.get("timestamp") or blink_obj.get("timestamp_epoch")
                    ts = float(ts_raw) if ts_raw is not None else time.time()
                    publish_async({"ts": ts, "event": "blink", "state": blink_state})

                now = time.monotonic()
                if now - last_log >= 5:
                    print(
                        f"[relay] Forwarded {samples_forwarded} gaze samples",
                        flush=True,
                    )
                    last_log = now
                    samples_forwarded = 0
        except Exception as exc:  # pragma: no cover - requires hardware/runtime
            loop.call_soon_threadsafe(done_fut.set_exception, exc)
        else:
            loop.call_soon_threadsafe(done_fut.set_result, None)
        finally:
            stop_event.set()
            close_sockets()

    thread = threading.Thread(target=run, name="pupil-core-stream", daemon=True)
    thread.start()

    try:
        await done_fut
    except asyncio.CancelledError:
        stop_event.set()
        thread.join(timeout=5)
        raise
