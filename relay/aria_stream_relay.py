"""Relay Meta Project Aria gaze + blink events into the host stack via ZeroMQ."""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import threading
import time
import msgpack
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Callable

import zmq
import zmq.asyncio  # type: ignore

try:  # pragma: no cover - optional dependency
    from projectaria_tools.stream import gaze_stream  # type: ignore
except Exception:  # projectaria_tools may not be installed during scaffolding
    gaze_stream = None


@dataclass
class RelayConfig:
    endpoint: str = "tcp://*:5555"
    mode: str = "simulate"
    hz: float = 120.0
    blink_interval: float = 2.0
    device_id: str | None = None
    pupil_host: str = "127.0.0.1"
    pupil_remote_port: int = 50020
    pupil_topic: str = "gaze."
    pupil_confidence_threshold: float = 0.6


async def simulated_source(publisher: zmq.asyncio.Socket, cfg: RelayConfig) -> None:
    period = 1.0 / cfg.hz
    next_blink = time.monotonic() + cfg.blink_interval
    blink_state = "open"
    while True:
        now = time.time()
        if time.monotonic() >= next_blink:
            blink_state = "closed"
            next_blink = time.monotonic() + cfg.blink_interval
        else:
            blink_state = "open"
        gaze = {
            "x_norm": 0.5 + 0.25 * math.sin(now),
            "y_norm": 0.5 + 0.25 * math.cos(now * 0.8),
            "valid": True,
        }
        payload = {
            "ts": now,
            "event": "sample",
            "gaze": gaze,
            "blink": {
                "state": blink_state,
                "confidence": 0.8 + 0.2 * random.random(),
            },
        }
        await publisher.send_json(payload)
        if blink_state == "closed":
            await publisher.send_json({"event": "blink", "ts": now, "state": "closed"})
        await asyncio.sleep(period)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


async def live_source(publisher: zmq.asyncio.Socket, cfg: RelayConfig) -> None:
    """Stream live data from Project Aria via the client SDK and forward to ZeroMQ."""

    device_id = cfg.device_id or os.environ.get("ARIA_DEVICE_ID")
    if not device_id:
        raise ValueError(
            "Missing device identifier. Pass --device-id or export ARIA_DEVICE_ID before using --mode live."
        )

    # Prefer the legacy gaze_stream client if it is available (older research builds).
    if gaze_stream is not None:  # pragma: no cover - exercised only with vendor SDK
        await _run_legacy_gaze_stream(publisher, device_id)
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
        return _clamp(0.5 + 0.5 * _clamp(angle_rad / max_angle, -1.0, 1.0))

    def pitch_to_norm(angle_rad: float) -> float:
        # Positive pitch points down; invert to keep 0 at the top of the viewport.
        max_angle = math.radians(35.0)
        return _clamp(0.5 - 0.5 * _clamp(angle_rad / max_angle, -1.0, 1.0))

    last_blink_state = "open"

    def infer_blink_state(depth_m: float) -> tuple[str, float]:
        # Depth collapses towards zero when the SDK cannot triangulate gaze (often caused by blinks).
        if depth_m <= 0.02:
            return "closed", 0.4
        return "open", 0.9

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
        blink_state, blink_conf = infer_blink_state(depth)
        blink_payload = {"state": blink_state, "confidence": blink_conf}
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


async def pupil_core_source(publisher: zmq.asyncio.Socket, cfg: RelayConfig) -> None:
    """Subscribe to a Pupil Core runtime (via Pupil Remote) and relay gaze samples."""

    loop = asyncio.get_running_loop()

    async def _send(payload: dict[str, Any]) -> None:
        await publisher.send_json(payload)

    def publish_async(payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(_send(payload), loop)

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
                x_norm = _clamp(float(norm_pos[0]))
                y_norm = _clamp(1.0 - float(norm_pos[1]))
                confidence = float(sample_obj.get("confidence", 0.0))
                valid = confidence >= cfg.pupil_confidence_threshold
                blink_state = "open" if valid else "closed"
                ts_raw = sample_obj.get("timestamp") or sample_obj.get("timestamp_epoch")
                ts = float(ts_raw) if ts_raw is not None else time.time()
                gaze_payload = {"x_norm": x_norm, "y_norm": y_norm, "valid": valid}
                blink_payload = {"state": blink_state, "confidence": confidence}
                publish_async({"ts": ts, "event": "sample", "gaze": gaze_payload, "blink": blink_payload})
                if blink_state != last_blink_state:
                    last_blink_state = blink_state
                    publish_async({"ts": ts, "event": "blink", "state": blink_state})
                samples_forwarded += 1
                now = time.monotonic()
                if now - last_log >= 5:
                    print(
                        f"[relay] Forwarded {samples_forwarded} samples from topic '{topic}' (confidence {confidence:.2f})",
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


async def _run_legacy_gaze_stream(publisher: zmq.asyncio.Socket, device_id: str) -> None:
    """Fallback for older research drops that ship gaze_stream.LiveStreamClient."""

    if gaze_stream is None:  # pragma: no cover - enforced by caller, kept for type checkers
        raise RuntimeError("gaze_stream client is unavailable in this environment")
    assert gaze_stream is not None

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


async def main(cfg: RelayConfig) -> None:
    ctx = zmq.asyncio.Context.instance()
    socket = ctx.socket(zmq.PUB)
    socket.bind(cfg.endpoint)
    print(f"Relay publishing on {cfg.endpoint} in {cfg.mode} mode")
    try:
        if cfg.mode == "simulate":
            await simulated_source(socket, cfg)
        elif cfg.mode == "pupil":
            await pupil_core_source(socket, cfg)
        else:
            await live_source(socket, cfg)
    finally:
        socket.close(0)


def parse_args() -> RelayConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="tcp://*:5555", help="ZeroMQ PUB endpoint")
    parser.add_argument("--mode", choices=["simulate", "live", "pupil"], default="pupil")
    parser.add_argument("--hz", type=float, default=120.0, help="Samples per second in simulate mode")
    parser.add_argument("--blink-interval", type=float, default=2.0, help="Seconds between synthetic blinks")
    parser.add_argument("--device-id", help="Aria Live Stream device identifier")
    parser.add_argument("--pupil-host", default=os.environ.get("PUPIL_REMOTE_HOST", "127.0.0.1"), help="Pupil Remote host")
    parser.add_argument("--pupil-port", type=int, default=int(os.environ.get("PUPIL_REMOTE_PORT", "50020")), help="Pupil Remote command port")
    parser.add_argument("--pupil-topic", default=os.environ.get("PUPIL_TOPIC", "gaze.3d.0"), help="ZMQ topic to subscribe to")
    parser.add_argument(
        "--pupil-confidence-threshold",
        type=float,
        default=float(os.environ.get("PUPIL_CONFIDENCE_THRESHOLD", "0.6")),
        help="Confidence threshold for validating gaze samples",
    )
    args = parser.parse_args()
    return RelayConfig(
        endpoint=args.endpoint,
        mode=args.mode,
        hz=args.hz,
        blink_interval=args.blink_interval,
        device_id=args.device_id,
        pupil_host=args.pupil_host,
        pupil_remote_port=args.pupil_port,
        pupil_topic=args.pupil_topic,
        pupil_confidence_threshold=args.pupil_confidence_threshold,
    )


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
