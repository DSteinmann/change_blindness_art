"""Relay Meta Project Aria gaze + blink events into the host stack via ZeroMQ."""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import time
from dataclasses import dataclass

import zmq
import zmq.asyncio  # type: ignore

from aria_live_source import live_source
from pupil_core_source import pupil_core_source


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
    blink_hold_ms: float = 120.0
    aria_blink_close_depth_m: float = 0.02
    aria_blink_open_depth_m: float = 0.04
    aria_depth_ema_alpha: float = 0.25
    pupil_blink_close_confidence: float = 0.45
    pupil_blink_open_confidence: float = 0.65
    pupil_confidence_ema_alpha: float = 0.3


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
    parser.add_argument("--blink-hold-ms", type=float, default=120.0, help="Minimum time (ms) to keep a blink closed once detected")
    parser.add_argument("--aria-blink-close-depth", type=float, default=0.02, help="Depth (m) below which Aria depth implies closed eyes")
    parser.add_argument("--aria-blink-open-depth", type=float, default=0.04, help="Depth (m) above which Aria depth implies open eyes")
    parser.add_argument("--aria-depth-ema", type=float, default=0.25, help="EMA alpha applied to Aria depth before blink inference")
    parser.add_argument(
        "--pupil-blink-close-confidence",
        type=float,
        default=0.45,
        help="Confidence level below which Pupil samples imply closed eyes",
    )
    parser.add_argument(
        "--pupil-blink-open-confidence",
        type=float,
        default=0.65,
        help="Confidence level above which Pupil samples imply open eyes",
    )
    parser.add_argument(
        "--pupil-confidence-ema",
        type=float,
        default=0.3,
        help="EMA alpha applied to Pupil confidence values",
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
        blink_hold_ms=args.blink_hold_ms,
        aria_blink_close_depth_m=args.aria_blink_close_depth,
        aria_blink_open_depth_m=args.aria_blink_open_depth,
        aria_depth_ema_alpha=args.aria_depth_ema,
        pupil_blink_close_confidence=args.pupil_blink_close_confidence,
        pupil_blink_open_confidence=args.pupil_blink_open_confidence,
        pupil_confidence_ema_alpha=args.pupil_confidence_ema,
    )


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
