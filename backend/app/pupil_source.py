from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import suppress
from typing import Awaitable, Callable, Optional

import msgpack  # type: ignore
import zmq

from .config import Settings

logger = logging.getLogger(__name__)

PUPIL_REMOTE_TIMEOUT_MS = 2000
PUPIL_REMOTE_RETRY_SEC = 2.0


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _is_binocular(gaze_pt: dict) -> bool:
    """A gaze_on_surfaces entry is binocular when its underlying source fused
    both eyes. Pupil exposes this via either:
      - `base_data`: list of 2 pupil entries (one per eye) for binocular,
      - `topic`: ends in ".01." (e.g. "gaze.3d.01.") for binocular.
    """
    base = gaze_pt.get("base_data")
    if isinstance(base, list) and len(base) >= 2:
        return True
    topic = str(gaze_pt.get("topic", ""))
    return ".01" in topic


def average_gaze_points(
    gaze_pts: list[dict],
    confidence_threshold: float,
) -> tuple[dict, float] | None:
    """Collapse N gaze samples from a single world frame into one consolidated
    payload. Variance shrinks by sqrt(N) so the resulting cursor is much
    steadier than the per-sample 200 Hz stream while still tracking saccades.

    Returns (gaze_payload, timestamp) or None when no usable samples exist.
    `valid` is set when the *averaged* confidence clears the threshold; this
    smooths over single-sample confidence dips without admitting an entirely
    low-confidence frame.
    """
    if not gaze_pts:
        return None
    xs: list[float] = []
    ys: list[float] = []
    confs: list[float] = []
    latest_ts = 0.0
    for g in gaze_pts:
        norm = g.get("norm_pos") or [0.5, 0.5]
        xs.append(float(norm[0]))
        ys.append(float(norm[1]))
        confs.append(float(g.get("confidence") or 0.0))
        ts = g.get("timestamp")
        if ts is not None:
            latest_ts = max(latest_ts, float(ts))
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    mean_conf = sum(confs) / n
    payload = {
        "x_norm": clamp(mean_x),
        # Pupil's surface coords are bottom-left origin; flip Y to top-left.
        "y_norm": clamp(1.0 - mean_y),
        "valid": mean_conf >= confidence_threshold,
    }
    return payload, latest_ts


def filter_to_one_source(gaze_pts: list[dict]) -> list[dict]:
    """Pupil publishes binocular + both monocular gazes for the same timestamp.
    Forwarding all of them yanks the smoothed gaze cursor between disagreeing
    sources. Prefer binocular; fall back to the highest-confidence monocular
    per unique timestamp when binocular is unavailable.
    """
    if not gaze_pts:
        return []
    binocular = [g for g in gaze_pts if _is_binocular(g)]
    if binocular:
        return binocular
    # No binocular available — dedupe monocular by timestamp.
    by_ts: dict = {}
    for g in gaze_pts:
        ts = g.get("timestamp")
        if ts is None:
            continue
        prior = by_ts.get(ts)
        if prior is None or g.get("confidence", 0.0) > prior.get("confidence", 0.0):
            by_ts[ts] = g
    return list(by_ts.values())


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
    
    def __init__(
        self,
        settings: Settings,
        broadcast_callback: Callable,
        on_blink_onset: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self._settings = settings
        self._broadcast = broadcast_callback
        self._on_blink_onset = on_blink_onset
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pupil-core-source", daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        # Name of the surface defined in Pupil Capture's Surface Tracker
        self.surface_name = settings.pupil_surface_name

    async def start(self):
        logger.info("Starting Pupil Core source...")
        logger.info(f"Surface name for gaze mapping: '{self.surface_name}'")
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._thread.start()

    def _dispatch(self, payload: dict) -> None:
        """Schedule broadcast on the FastAPI event loop from this worker thread."""
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    def _dispatch_blink_onset(self, state: str) -> None:
        if self._on_blink_onset is None or self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._on_blink_onset(state), self._loop)

    async def stop(self):
        logger.info("Stopping Pupil Core source...")
        self._stop_event.set()
        self._thread.join(timeout=5)

    def _request_sub_port(self, ctx: zmq.Context) -> Optional[str]:
        """Ask Pupil Remote for its SUB port, retrying until Pupil Capture is up
        so the backend can be started before it. Returns None if stopped first."""
        remote_address = f"tcp://{self._settings.pupil_host}:{self._settings.pupil_remote_port}"
        logger.info(f"Connecting to Pupil Remote at {remote_address}")
        while not self._stop_event.is_set():
            request_socket = ctx.socket(zmq.REQ)
            request_socket.setsockopt(zmq.RCVTIMEO, PUPIL_REMOTE_TIMEOUT_MS)
            request_socket.setsockopt(zmq.LINGER, 0)
            request_socket.connect(remote_address)
            try:
                request_socket.send_string("SUB_PORT")
                return request_socket.recv_string()
            except zmq.Again:
                logger.warning(
                    f"Pupil Remote not reachable at {remote_address}. Is Pupil Capture "
                    f"running with the Pupil Remote plugin enabled? Retrying..."
                )
            finally:
                request_socket.close(0)
            self._stop_event.wait(PUPIL_REMOTE_RETRY_SEC)
        return None

    def _run(self) -> None:
        ctx = zmq.Context.instance()
        sub_port = self._request_sub_port(ctx)
        if sub_port is None:
            return
        logger.info(f"Received Pupil SUB_PORT={sub_port}")

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
        last_gaze_emit = time.monotonic()
        samples_forwarded = 0
        surface_samples = 0
        blinks_received = 0
        heartbeat_interval = 0.1  # Emit invalid sample if no gaze for this long

        while not self._stop_event.is_set():
            try:
                socks = dict(poller.poll(timeout=100))
            except zmq.ZMQError:
                break

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
                    # Ignore other surfaces defined in Surface Tracker; their gaze
                    # would otherwise be mixed into the screen's.
                    if isinstance(surface_obj, dict) and surface_obj.get("name") == self.surface_name:
                        surface_name = self.surface_name
                        if surface_samples < 5:
                            logger.info(f"Surface message: name='{surface_name}', keys={list(surface_obj.keys())}")

                        # Surface data structure from Pupil Core:
                        # - name: surface name
                        # - gaze_on_surfaces: list of [{norm_pos: [x,y], confidence: float, ...}]
                        gaze_on_surfaces = surface_obj.get("gaze_on_surfaces") or []
                        fixations_on_surfaces = surface_obj.get("fixations_on_surfaces") or []
                        # Pupil publishes binocular + per-eye monocular gaze at
                        # the same timestamps; forwarding all of them disagrees
                        # the smoothed cursor between sources. Keep one source.
                        raw_count = len(gaze_on_surfaces)
                        gaze_on_surfaces = filter_to_one_source(gaze_on_surfaces)
                        if surface_samples < 20 or surface_samples % 60 == 0:
                            kept = len(gaze_on_surfaces)
                            logger.info(
                                f"surface='{surface_name}' gaze_pts={kept}/{raw_count} "
                                f"fix_pts={len(fixations_on_surfaces)}"
                            )
                        # Collapse all samples from this world frame into one
                        # averaged emission. 200 Hz of microsaccade noise gets
                        # filtered to ~30 Hz of stable gaze with sqrt(N)
                        # variance reduction — much steadier than per-sample
                        # exponential smoothing on the frontend.
                        averaged = average_gaze_points(
                            gaze_on_surfaces, self._settings.pupil_confidence_threshold,
                        )
                        if averaged is not None:
                            gaze_payload, ts = averaged
                            self._dispatch({
                                "ts": ts or time.time(),
                                "event": "sample",
                                "gaze": gaze_payload,
                            })
                            samples_forwarded += 1
                            surface_samples += 1
                            last_gaze_emit = time.monotonic()

                        # If the surface message carried zero gaze points, the user's gaze
                        # is off-surface or the tracker lost the eye — emit an invalid
                        # sample so the frontend can reset its fixation state.
                        if not gaze_on_surfaces:
                            now = time.monotonic()
                            if now - last_gaze_emit >= heartbeat_interval:
                                self._dispatch({
                                    "ts": time.time(),
                                    "event": "sample",
                                    "gaze": {"x_norm": 0.5, "y_norm": 0.5, "valid": False},
                                })
                                last_gaze_emit = now

            if blink_socket in socks:
                frames = blink_socket.recv_multipart(flags=zmq.NOBLOCK)
                if len(frames) >= 2:
                    blink_obj = msgpack.loads(frames[1], raw=False)
                    if isinstance(blink_obj, dict):
                        blink_type = blink_obj.get("type")
                        blink_state = "closed" if blink_type == "onset" else "open"
                        ts_raw = blink_obj.get("timestamp") or blink_obj.get("timestamp_epoch")
                        ts = float(ts_raw) if ts_raw is not None else time.time()
                        blinks_received += 1
                        if blinks_received <= 5 or blinks_received % 20 == 0:
                            logger.info(
                                f"blink #{blinks_received} type={blink_type} "
                                f"state={blink_state} ts={ts}"
                            )
                        self._dispatch({"ts": ts, "event": "blink", "state": blink_state})
                        if blink_state == "closed":
                            self._dispatch_blink_onset(blink_state)

            now = time.monotonic()
            # Heartbeat: if no gaze sample has been emitted recently, push an
            # invalid one so the frontend can clear stale fixations when the
            # Surface Tracker stops publishing (eye lost, off-surface, etc.).
            if now - last_gaze_emit >= heartbeat_interval:
                self._dispatch({
                    "ts": time.time(),
                    "event": "sample",
                    "gaze": {"x_norm": 0.5, "y_norm": 0.5, "valid": False},
                })
                last_gaze_emit = now

            if now - last_log >= 5:
                if surface_samples > 0:
                    source = f"surface '{self.surface_name}' ({surface_samples} pts)"
                else:
                    source = (
                        f"no gaze on surface '{self.surface_name}' - check Surface Tracker "
                        f"setup and that the surface name matches PUPIL_SURFACE_NAME!"
                    )
                logger.info(
                    f"Forwarded {samples_forwarded} gaze samples - source: {source} "
                    f"| blinks received this session: {blinks_received}"
                )
                last_log = now
                samples_forwarded = 0
                surface_samples = 0

        with suppress(Exception):
            surface_socket.close(0)
        with suppress(Exception):
            blink_socket.close(0)
