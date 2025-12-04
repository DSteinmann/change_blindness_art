#!/usr/bin/env python3
"""Quick diagnostic to verify Pupil Remote is publishing gaze data."""
import argparse
import sys
import time

import msgpack
import zmq


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Pupil Remote host")
    parser.add_argument("--port", type=int, default=50020, help="Pupil Remote command port")
    parser.add_argument(
        "--topic",
        default="gaze.",
        help="Topic prefix to subscribe to (use empty string to receive everything)",
    )
    parser.add_argument("--duration", type=float, default=15.0, help="Seconds to listen before exiting")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ctx = zmq.Context.instance()
    remote = ctx.socket(zmq.REQ)
    remote.connect(f"tcp://{args.host}:{args.port}")
    print(f"[diag] Connected REQ socket to tcp://{args.host}:{args.port}")

    remote.send_string("SUB_PORT")
    sub_port = remote.recv_string()
    print(f"[diag] SUB_PORT={sub_port}")

    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://{args.host}:{sub_port}")
    sub.setsockopt_string(zmq.SUBSCRIBE, args.topic)
    print(f"[diag] Subscribing to prefix '{args.topic}'")

    start = time.time()
    samples = 0
    try:
        while time.time() - start < args.duration:
            try:
                topic, payload = sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                time.sleep(0.05)
                continue
            obj = msgpack.loads(payload, raw=False)
            if not isinstance(obj, dict):
                print(f"[diag] Skipping non-dict payload ({type(obj).__name__})")
                continue
            message = obj
            samples += 1
            norm_pos = message.get("norm_pos")
            confidence = message.get("confidence")
            ts = message.get("timestamp") or message.get("timestamp_epoch")
            print(f"[diag] {topic.decode('utf-8', 'ignore')}: norm_pos={norm_pos} confidence={confidence} ts={ts}")
    except KeyboardInterrupt:
        pass
    finally:
        sub.close(0)
        remote.close(0)

    if samples == 0:
        print("[diag] No messages received. Verify Pupil Capture is streaming gaze and Remote is enabled.")
        sys.exit(1)
    print(f"[diag] Received {samples} messages in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
