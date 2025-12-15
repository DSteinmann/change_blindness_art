from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import msgpack  # type: ignore
import zmq
import zmq.asyncio  # type: ignore

from blink_utils import HysteresisBlinkDetector, clamp

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
    """Subscribe to a Pupil Core runtime (via Pupil Remote) and relay gaze samples."""

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

    sub_socket = ctx.socket(zmq.SUB)
    sub_socket.connect(f"tcp://{cfg.pupil_host}:{sub_port}")
    sub_socket.setsockopt_string(zmq.SUBSCRIBE, cfg.pupil_topic)
    print(
        f"[relay] Subscribed to topic prefix '{cfg.pupil_topic}' on tcp://{cfg.pupil_host}:{sub_port}",
        flush=True,
    )

    stop_event = threading.Event()
    done_fut: asyncio.Future[None] = loop.create_future()
    last_blink_state = "open"
    last_log = time.monotonic()
    samples_forwarded = 0
    pupil_blink_detector = HysteresisBlinkDetector(
        close_below=cfg.pupil_blink_close_confidence,
        open_above=cfg.pupil_blink_open_confidence,
        hold_ms=cfg.blink_hold_ms,
        ema_alpha=cfg.pupil_confidence_ema_alpha,
        initial_value=cfg.pupil_blink_open_confidence,
    )

    def close_sockets() -> None:
        with suppress(Exception):
            sub_socket.close(0)
        with suppress(Exception):
            request_socket.close(0)

    def run() -> None:
        nonlocal last_blink_state, last_log, samples_forwarded
        try:
            while not stop_event.is_set():
                try:
                    frames = sub_socket.recv_multipart(flags=zmq.NOBLOCK)
                except zmq.Again:
                    time.sleep(0.01)
                    continue
                if len(frames) < 2:
                    continue
                topic = (
                    frames[0].decode("utf-8", errors="ignore")
                    if isinstance(frames[0], (bytes, bytearray))
                    else str(frames[0])
                )
                if cfg.pupil_topic and not topic.startswith(cfg.pupil_topic):
                    continue
                sample_obj = msgpack.loads(frames[1], raw=False)
                if not isinstance(sample_obj, dict):
                    print(
                        f"[relay] Skipping unexpected payload type {type(sample_obj).__name__} from topic {topic}",
                        flush=True,
                    )
                    continue
                norm_pos = sample_obj.get("norm_pos") or (0.5, 0.5)
                x_norm = clamp(float(norm_pos[0]))
                y_norm = clamp(1.0 - float(norm_pos[1]))
                confidence_raw = float(sample_obj.get("confidence", 0.0))
                blink_inference = pupil_blink_detector.update(confidence_raw)
                blink_state = blink_inference.state
                filtered_confidence = blink_inference.filtered_value
                valid = filtered_confidence >= cfg.pupil_confidence_threshold
                ts_raw = sample_obj.get("timestamp") or sample_obj.get("timestamp_epoch")
                ts = float(ts_raw) if ts_raw is not None else time.time()
                gaze_payload = {"x_norm": x_norm, "y_norm": y_norm, "valid": valid}
                blink_payload = {"state": blink_state, "confidence": blink_inference.confidence}
                publish_async({"ts": ts, "event": "sample", "gaze": gaze_payload, "blink": blink_payload})
                if blink_state != last_blink_state:
                    last_blink_state = blink_state
                    publish_async({"ts": ts, "event": "blink", "state": blink_state})
                samples_forwarded += 1
                now = time.monotonic()
                if now - last_log >= 5:
                    print(
                        f"[relay] Forwarded {samples_forwarded} samples from topic '{topic}' (confidence {filtered_confidence:.2f})",
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
