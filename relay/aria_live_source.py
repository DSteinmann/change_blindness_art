from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Callable

import zmq.asyncio  # type: ignore

from blink_utils import HysteresisBlinkDetector, clamp

try:  # pragma: no cover - optional dependency
    from projectaria_tools.stream import gaze_stream  # type: ignore
except Exception:  # projectaria_tools may not be installed during scaffolding
    gaze_stream = None

if TYPE_CHECKING:  # pragma: no cover - typing helper only
    from aria_stream_relay import RelayConfig


async def live_source(publisher: zmq.asyncio.Socket, cfg: "RelayConfig") -> None:
    """Stream live data from Project Aria via the client SDK and forward to ZeroMQ."""

    device_id = cfg.device_id or os.environ.get("ARIA_DEVICE_ID")
    if not device_id:
        raise ValueError(
            "Missing device identifier. Pass --device-id or export ARIA_DEVICE_ID before using --mode live."
        )

    # Prefer the legacy gaze_stream client if it is available (older research builds).
    if gaze_stream is not None:  # pragma: no cover - exercised only with vendor SDK
        await _run_legacy_gaze_stream(publisher, device_id, cfg)
        return

    try:  # pragma: no cover - requires Project Aria Client SDK runtime
        import aria.sdk_gen2 as sdk_gen2  # type: ignore
        import aria.stream_receiver as stream_receiver  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Live mode requires the Project Aria Client SDK (aria.sdk_gen2 + aria.stream_receiver). "
            "Install projectaria-client-sdk >= 1.2 and ensure the ARK services are running."
        ) from exc

    loop = asyncio.get_running_loop()

    async def _send(payload: dict[str, Any]) -> None:
        await publisher.send_json(payload)

    stop_event = threading.Event()
    worker_done: asyncio.Future[None] = loop.create_future()

    def publish_async(payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(_send(payload), loop)

    def yaw_to_norm(angle_rad: float) -> float:
        # Empirically yaw stays within +/-35 degrees for on-device streaming.
        max_angle = math.radians(35.0)
        return clamp(0.5 + 0.5 * clamp(angle_rad / max_angle, -1.0, 1.0))

    def pitch_to_norm(angle_rad: float) -> float:
        # Positive pitch points down; invert to keep 0 at the top of the viewport.
        max_angle = math.radians(35.0)
        return clamp(0.5 - 0.5 * clamp(angle_rad / max_angle, -1.0, 1.0))

    aria_blink_detector = HysteresisBlinkDetector(
        close_below=cfg.aria_blink_close_depth_m,
        open_above=cfg.aria_blink_open_depth_m,
        hold_ms=cfg.blink_hold_ms,
        ema_alpha=cfg.aria_depth_ema_alpha,
        initial_value=cfg.aria_blink_open_depth_m,
    )
    last_blink_state = "open"

    def eyegaze_callback(eye_sample: Any) -> None:
        nonlocal last_blink_state
        timestamp = getattr(eye_sample, "tracking_timestamp", None)
        ts = timestamp.total_seconds() if timestamp else time.time()
        yaw = float(getattr(eye_sample, "yaw", 0.0) or 0.0)
        pitch = float(getattr(eye_sample, "pitch", 0.0) or 0.0)
        depth = float(getattr(eye_sample, "depth", 0.0) or 0.0)
        gaze_payload = {
            "x_norm": yaw_to_norm(yaw),
            "y_norm": pitch_to_norm(pitch),
            "valid": True,
        }
        blink_inference = aria_blink_detector.update(depth)
        blink_state = blink_inference.state
        blink_payload = {"state": blink_state, "confidence": blink_inference.confidence}
        publish_async({"ts": ts, "event": "sample", "gaze": gaze_payload, "blink": blink_payload})
        if blink_state != last_blink_state:
            last_blink_state = blink_state
            publish_async({"ts": ts, "event": "blink", "state": blink_state})

    def run_streaming() -> None:
        stream = None
        device = None
        device_client = None
        try:
            device_client = sdk_gen2.DeviceClient()
            client_config = sdk_gen2.DeviceClientConfig()
            client_config.device_serial = device_id
            device_client.set_client_config(client_config)
            device = device_client.connect()

            streaming_config = sdk_gen2.HttpStreamingConfig()
            streaming_config.profile_name = "mp_streaming_demo"
            device.set_streaming_config(streaming_config)
            device.start_streaming()

            server_config = sdk_gen2.HttpServerConfig()
            server_config.address = "0.0.0.0"
            server_config.port = 6768

            stream = stream_receiver.StreamReceiver()
            stream.set_server_config(server_config)
            stream.register_eye_gaze_callback(eyegaze_callback)
            stream.start_server()

            while not stop_event.wait(0.2):
                continue
        except Exception as exc:  # pragma: no cover - requires hardware to exercise
            loop.call_soon_threadsafe(worker_done.set_exception, exc)
        else:
            loop.call_soon_threadsafe(worker_done.set_result, None)
        finally:
            stop_event.set()
            if stream is not None:
                stop_fn: Callable[[], None] | None = None
                if hasattr(stream, "stop_server"):
                    stop_fn = stream.stop_server
                elif hasattr(stream, "shutdown_server"):
                    stop_fn = stream.shutdown_server
                if stop_fn:
                    with suppress(Exception):
                        stop_fn()
            if device is not None:
                with suppress(Exception):
                    device.stop_streaming()
            if device_client is not None:
                with suppress(Exception):
                    device_client.disconnect()

    thread = threading.Thread(target=run_streaming, name="aria-live-stream", daemon=True)
    thread.start()

    try:
        await worker_done
    except asyncio.CancelledError:
        stop_event.set()
        thread.join(timeout=5)
        raise


async def _run_legacy_gaze_stream(
    publisher: zmq.asyncio.Socket, device_id: str, cfg: "RelayConfig"
) -> None:  # pragma: no cover - legacy path
    """Fallback for older research drops that ship gaze_stream.LiveStreamClient."""

    if gaze_stream is None:
        raise RuntimeError("gaze_stream client is unavailable in this environment")

    loop = asyncio.get_running_loop()

    async def _send(payload: dict[str, Any]) -> None:
        await publisher.send_json(payload)

    def publish(payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(_send(payload), loop)

    client = gaze_stream.LiveStreamClient(device_id=device_id)  # type: ignore[attr-defined]
    stop_event = threading.Event()
    done_fut: asyncio.Future[None] = loop.create_future()

    def on_sample(sample: Any) -> None:
        ts = getattr(sample, "timestamp", time.time())
        gaze = getattr(sample, "gaze", None)
        blink = getattr(sample, "blink", None)
        payload = {
            "ts": float(ts),
            "event": "sample",
            "gaze": {
                "x_norm": float(getattr(gaze, "x_norm", 0.5)),
                "y_norm": float(getattr(gaze, "y_norm", 0.5)),
                "valid": bool(getattr(gaze, "valid", True)),
            },
            "blink": {
                "state": getattr(blink, "state", "open"),
                "confidence": float(getattr(blink, "confidence", 0.0)),
            },
        }
        publish(payload)
        if blink is not None:
            blink_state = getattr(blink, "state", None)
            if blink_state:
                publish({"ts": float(ts), "event": "blink", "state": blink_state})

    def run() -> None:
        try:
            client.subscribe(callback=on_sample)
            while not stop_event.wait(0.2):
                continue
        except Exception as exc:  # pragma: no cover - requires hardware
            loop.call_soon_threadsafe(done_fut.set_exception, exc)
        else:
            loop.call_soon_threadsafe(done_fut.set_result, None)
        finally:
            stop_event.set()
            with suppress(Exception):
                client.close()

    thread = threading.Thread(target=run, name="aria-gaze-stream", daemon=True)
    thread.start()
    try:
        await done_fut
    except asyncio.CancelledError:
        stop_event.set()
        thread.join(timeout=5)
        raise
