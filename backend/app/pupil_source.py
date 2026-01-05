from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import suppress
from typing import Callable

import msgpack  # type: ignore
import zmq

from .blink_utils import clamp
from .config import Settings

logger = logging.getLogger(__name__)


class PupilSource:
    """
    Connects to Pupil Core and subscribes to:
    - surfaces.<surface_name> (gaze mapped to surface via Surface Tracker plugin)
    - blinks
    
    Requires Surface Tracker to be configured in Pupil Capture with AprilTags defining
    the screen surface. 
    
    Pupil Capture uses OpenGL coords: (0,0) = bottom-left, (1,1) = top-right
    We convert to screen coords:     (0,0) = top-left,    (1,1) = bottom-right
    """
    
    def __init__(self, settings: Settings, broadcast_callback: Callable):
        self._settings = settings
        self._broadcast = broadcast_callback
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pupil-core-source", daemon=True)
        # Name of the surface defined in Pupil Capture's Surface Tracker
        self.surface_name = settings.pupil_surface_name if hasattr(settings, 'pupil_surface_name') else "screen"

    async def start(self):
        logger.info("Starting Pupil Core source...")
        logger.info(f"Surface name for gaze mapping: '{self.surface_name}'")
        self._stop_event.clear()
        self._thread.start()

    async def stop(self):
        logger.info("Stopping Pupil Core source...")
        self._stop_event.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        ctx = zmq.Context.instance()
        request_socket = ctx.socket(zmq.REQ)
        remote_address = f"tcp://{self._settings.pupil_host}:{self._settings.pupil_remote_port}"
        request_socket.connect(remote_address)
        logger.info(f"Connecting to Pupil Remote at {remote_address}")

        try:
            request_socket.send_string("SUB_PORT")
            sub_port = request_socket.recv_string()
            logger.info(f"Received Pupil SUB_PORT={sub_port}")
        except Exception as exc:
            request_socket.close(0)
            raise RuntimeError(
                "Unable to reach the Pupil Remote plugin. Ensure Pupil Capture/Core is running "
                "with Remote enabled and that the host/port are correct."
            ) from exc

        sub_address = f"tcp://{self._settings.pupil_host}:{sub_port}"
        
        # Subscribe to surface gaze ONLY (from Marker Mapper / Surface Tracker)
        # No raw gaze subscription - we rely entirely on surface-mapped coordinates
        surface_socket = ctx.socket(zmq.SUB)
        surface_socket.connect(sub_address)
        surface_socket.setsockopt_string(zmq.SUBSCRIBE, "surface")
        logger.info(f"Subscribed to 'surface*' topics on {sub_address}")
        logger.info(f"Looking for surface named: '{self.surface_name}'")
        logger.info("Configure Surface Tracker in Pupil Capture with AprilTags at screen corners!")

        # Subscribe to blinks
        blink_socket = ctx.socket(zmq.SUB)
        blink_socket.connect(sub_address)
        blink_socket.setsockopt_string(zmq.SUBSCRIBE, "blinks")
        logger.info(f"Subscribed to blink topic 'blinks' on {sub_address}")

        poller = zmq.Poller()
        poller.register(surface_socket, zmq.POLLIN)
        poller.register(blink_socket, zmq.POLLIN)

        last_log = time.monotonic()
        samples_forwarded = 0
        surface_samples = 0

        while not self._stop_event.is_set():
            try:
                socks = dict(poller.poll(timeout=100))
            except zmq.ZMQError:
                break

            if not socks:
                continue

            # Prefer surface gaze data (already mapped to screen by Pupil Capture)
            if surface_socket in socks:
                frames = surface_socket.recv_multipart(flags=zmq.NOBLOCK)
                if len(frames) >= 1:
                    topic = frames[0].decode('utf-8', errors='ignore')
                    # Log first few surface messages to help diagnose
                    if surface_samples < 5:
                        logger.info(f"Surface topic received: '{topic}'")
                    
                if len(frames) >= 2:
                    surface_obj = msgpack.loads(frames[1], raw=False)
                    if isinstance(surface_obj, dict):
                        # Log what we received to understand the data structure
                        surface_name = surface_obj.get("name", "unknown")
                        if surface_samples < 5:
                            logger.info(f"Surface message: name='{surface_name}', keys={list(surface_obj.keys())}")
                        
                        # Surface data structure from Pupil Core:
                        # - name: surface name
                        # - gaze_on_surfaces: list of [{norm_pos: [x,y], confidence: float, ...}]
                        gaze_on_surfaces = surface_obj.get("gaze_on_surfaces", [])
                        for gaze_pt in gaze_on_surfaces:
                            norm_pos = gaze_pt.get("norm_pos", [0.5, 0.5])
                            confidence = float(gaze_pt.get("confidence", 0.0))
                            
                            # Pupil Capture Surface Tracker uses OpenGL convention:
                            # (0,0) = bottom-left, (1,1) = top-right
                            # Screen coords: (0,0) = top-left, (1,1) = bottom-right
                            # So we MUST flip Y!
                            x_norm = clamp(float(norm_pos[0]))
                            y_norm = clamp(1.0 - float(norm_pos[1]))  # Flip Y for screen coords
                            
                            valid = confidence >= self._settings.pupil_confidence_threshold
                            ts = float(gaze_pt.get("timestamp", time.time()))
                            
                            gaze_payload = {"x_norm": x_norm, "y_norm": y_norm, "valid": valid}
                            asyncio.run(self._broadcast({"ts": ts, "event": "sample", "gaze": gaze_payload}))
                            samples_forwarded += 1
                            surface_samples += 1

            if blink_socket in socks:
                frames = blink_socket.recv_multipart(flags=zmq.NOBLOCK)
                if len(frames) >= 2:
                    blink_obj = msgpack.loads(frames[1], raw=False)
                    if isinstance(blink_obj, dict):
                        blink_type = blink_obj.get("type")
                        blink_state = "closed" if blink_type == "onset" else "open"
                        ts_raw = blink_obj.get("timestamp") or blink_obj.get("timestamp_epoch")
                        ts = float(ts_raw) if ts_raw is not None else time.time()
                        asyncio.run(self._broadcast({"ts": ts, "event": "blink", "state": blink_state}))

            now = time.monotonic()
            if now - last_log >= 5:
                if surface_samples > 0:
                    source = f"surface '{self.surface_name}' ({surface_samples} pts)"
                else:
                    source = "no surface data - check Surface Tracker setup!"
                logger.info(f"Forwarded {samples_forwarded} gaze samples - source: {source}")
                last_log = now
                samples_forwarded = 0
                surface_samples = 0

        with suppress(Exception):
            surface_socket.close(0)
        with suppress(Exception):
            blink_socket.close(0)
        with suppress(Exception):
            request_socket.close(0)
