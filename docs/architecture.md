# Project Aria Blink-Patch Experience

## Goals
- Stream gaze vectors and blink state from Meta Project Aria glasses to a desktop host.
- While the user looks at a base stimulus image, swap in a peripheral patch instantly (<150 ms) whenever a blink is detected.
- Default deployment is a desktop browser; the host backend should also drive large external displays.

## High-Level Flow
1. **Project Aria glasses** capture gaze rays and eyelid pose via the Live Stream SDK.
2. **Aria Stream Relay (Python)** subscribes to the glasses using `projectaria-tools` live APIs and publishes normalized gaze/blink packets over ZeroMQ.
3. **Host Backend (FastAPI)** ingests the relay stream, persists the latest sample in shared memory, and fans out updates via WebSocket to any UI clients.
4. **Web UI (React/Vite)** renders the base image, tracks the current gaze point, and swaps a peripheral patch the moment a blink packet arrives.
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
- `event: "gaze"` – high-frequency gaze updates (~120 Hz)
- `event: "blink"` – emitted only when the relay reports `closing→closed`
- `event: "patch"` – delivers the asset id that should be rendered next

### REST Endpoints
- `GET /healthz`
- `POST /relay/register` – optional handshake if multiple glasses will connect
- `GET /patch/next?stimulus=scene_a` – deterministic patch progression
- `POST /patch/use` – log that the UI applied a patch (timestamp, gaze vector)

## Blink-Patch Logic
1. UI keeps rendering the current base image while listening for `blink` events.
2. When blink `state=closed` arrives:
   - Debounce to ensure the last blink was ≥250 ms ago.
   - Fetch next patch id (pre-fetched whenever idle to avoid REST latency).
   - Compute peripheral anchor point: use the latest gaze vector to determine the opposite side of the screen so the user perceives a change in the periphery.
   - Swap assets instantly and log `PATCH_APPLIED` with timestamps (front-end sends to backend for experiment bookkeeping).
3. When `blink` transitions back to `open`, resume watching without changing assets.

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
