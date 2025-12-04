# Project Aria Blink-Opposite Experience

## Goals
- Stream gaze vectors (plus blink confidence for analytics) from Meta Project Aria or Pupil Core glasses to a desktop host.
- While the user looks at a base stimulus image, keep a peripheral patch visible and swap it with a new asset on the *opposite* side of the screen whenever a blink is detected (<150 ms latency budget).
- Default deployment is a desktop browser; the host backend should also drive large external displays.

## High-Level Flow
1. **Project Aria (or Pupil Core) glasses** capture gaze rays and eyelid pose via the live streaming SDKs.
2. **Aria Stream Relay (Python)** subscribes to the glasses and publishes normalized gaze/blink packets over ZeroMQ.
3. **Host Backend (FastAPI)** ingests the relay stream, persists the latest sample in shared memory, and fans out updates via WebSocket to any UI clients.
4. **Web UI (Vanilla JS)** renders the base image, tracks the current gaze point, and mirrors patches across gaze whenever a blink occurs.
5. **Patch Service** keeps a pre-generated queue of peripheral images (e.g., AI-generated textures). It exposes `GET /patch/next` for deterministic experiments or `GET /patch/random` for exploratory use.

```
[Aria Glasses]
    │  Live Stream SDK (Wi‑Fi)
    ▼
[Python Stream Relay]
    │  ZeroMQ PUB/SUB
    ▼
[FastAPI Backend]
    │  WebSocket + REST
    ▼
[React Frontend]
```

## Data Contracts
### Relay → Backend (ZeroMQ JSON)
```json
{
  "event": "sample",
  "ts": 1732928451.123,
  "gaze": {
    "x_norm": 0.42,
    "y_norm": 0.58,
    "valid": true
  },
  "blink": {
    "state": "closing", // "open", "closing", "closed"
    "confidence": 0.91
  }
}
```

### Backend → UI (WebSocket JSON)
- Samples from the relay are forwarded verbatim, so the UI simply listens for `event: "sample"` frames that contain both gaze and blink metadata.

### REST Endpoints
- `GET /healthz`
- `POST /relay/register` – optional handshake if multiple glasses will connect
- `GET /patch/next?stimulus=scene_a` – deterministic patch progression
- `POST /patch/use` – log that the UI applied a patch (timestamp, gaze vector)

## Blink-Opposite Logic
1. UI maintains a small buffer of patches fetched from `GET /patch/next` so that swap latency is dominated by rendering, not REST.
2. Every valid gaze sample updates the cursor and pre-computes the mirrored coordinate `⟨1 - x_norm, 1 - y_norm⟩`.
3. When blink `state=closed` arrives, the UI pops the next patch, renders it at the mirrored coordinate based on the latest gaze, and POSTs `/patch/use` with the original gaze + mirrored placement.
4. The active patch stays onscreen until the next blink, ensuring the periphery is always filled.

## Latency Budget
- Aria Live SDK → Relay: 15–30 ms over Wi‑Fi 6.
- Relay processing + ZeroMQ hop: <10 ms.
- Backend WebSocket fan-out: 5–20 ms.
- UI patch swap + render: 10–40 ms (depends on GPU/compositor).
- Total target: 40–100 ms typical, upper bound 150 ms.

## Desktop + Wall Display Support
- Desktop mode: browser running locally subscribes to WebSocket `ws://localhost:8000/ws`.
- Wall display: run the front-end in kiosk mode (`yarn build && serve -s dist`) on the display machine; point it at the same backend URL over LAN.

## Dev & Test Strategy
- **Simulated data**: backend exposes `/debug/simulate?pattern=blink_every_2s` to run without glasses.
- **Recording playback**: store Aria Live Stream recordings (HDF5) and use the relay to publish them at original timestamps for repeatable trials.
- **Latency probes**: add timestamps at every hop and emit them via `/metrics` (Prometheus) to confirm end-to-end timing.

## Next Steps
1. Scaffold the repository with `backend/`, `relay/`, `frontend/`, and `docs/` directories.
2. Implement the stream relay prototype (simulated mode first, then hook to real glasses).
3. Build the FastAPI/WebSocket server with patch asset management.
4. Create the React desktop UI with gaze visualization and patch swapping.
5. Add scripts for running the full stack locally via `docker-compose` or `make`.
