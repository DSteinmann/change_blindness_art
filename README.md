# Project Aria Blink-Opposite Patch Prototype

End-to-end scaffold for experimenting with gaze-contingent peripheral image patches using Meta Project Aria glasses. The UI keeps a patch visible at all times and swaps in a new asset on the *opposite* side of the screen whenever the wearer blinks.

## Repo Layout
- `relay/` – Python publisher that bridges Project Aria gaze/blink data to ZeroMQ (simulation mode included).
- `backend/` – FastAPI server that consumes the relay stream, manages patch assets, and exposes WebSocket + REST endpoints.
- `frontend/` – Static dashboard showing the base stimulus, current gaze cursor, and blink-triggered peripheral patches mirrored across the current gaze point.
- `assets/patches/` – Sample SVG patches plus a manifest consumed by the backend.
- `docs/` – Architecture notes.

## Environment Setup (Conda + Python 3.12)
1. Install [Miniconda or Mambaforge](https://docs.conda.io/en/latest/miniconda.html), then in the repo root:
   ```bash
   conda env create -f environment.yml
   conda activate ubicomp312
   ```
2. Install the **Project Aria Client SDK** wheel you obtained from Meta (it is not on PyPI):
   ```bash
   pip install /path/to/projectaria_client_sdk-1.2.1-cp312-*-macosx_*.whl
   ```
3. Verify the SDK CLI works:
   ```bash
   aria_device_manager --version
   ```
4. Keep this environment activated whenever you run the relay, backend, or helper scripts. Point custom tools at this interpreter via `PYTHON_BIN=$(which python)` if needed.
   - The helper scripts auto-detect `CONDA_PREFIX` and fall back to `python3` only if no conda env is active.

## Quickstart
1. **Connect your headset** and fetch its UUID via `aria_device_manager list`. Keep the glasses awake and on Wi-Fi.
2. **Launch the full stack locally** (relay + backend + static frontend) from the repo root:
   ```bash
   PYTHON_BIN=$(which python) \
   scripts/start_stack.sh --mode live --device-id <your-device-id> --endpoint tcp://127.0.0.1:5555
   ```
   - Default mode is `pupil`, which consumes gaze data from a Pupil Core headset (see "Using Pupil Core").
   - Use `--mode simulate` to replay synthetic gaze/blink data when hardware is unavailable.
   - `PYTHON_BIN` defaults to the active conda environment (`$CONDA_PREFIX/bin/python`) or `python3` if none is active.
   - Backend listens on `http://127.0.0.1:8000`, frontend serves at `http://127.0.0.1:8080`.
3. **Stop everything** with `Ctrl+C`. The script forwards signals and tears down relay/backend/frontend processes cleanly.

> Prefer running the relay on macOS directly instead of Docker because the Linux images lack the `aria.sdk_gen2` bindings bundled with the macOS SDK.

## Blink-Triggered Mirror Logic
- The frontend maintains a rolling buffer of patches fetched from `GET /patch/next` and records every placement through `POST /patch/use` with a `blink-opposite` reason.
- Gaze samples continually update the cursor and mirrored coordinate `⟨1 - x_norm, 1 - y_norm⟩`, but a new patch is only spawned when a blink transitions to `closed`.
- The prior patch remains visible until the next blink-induced swap, ensuring there is always a peripheral stimulus on screen.
- The sidebar surfaces the last blink state, buffer depth, current gaze, and the mirrored target so experimenters can verify stimuli and blinks in real time.

## Run Everything with Docker
1. Export your Aria device ID in `.env` (already seeded with a placeholder). The research kit unit on this project currently reports `ARIA_DEVICE_ID=d1bdfaf2-2dca-490f-be1b-73f792da9212`, so you can copy that value if you are using the same headset:
   ```bash
   echo "ARIA_DEVICE_ID=d1bdfaf2-2dca-490f-be1b-73f792da9212" > .env
   ```
2. Build and start the stack (relay live + backend + frontend):
   ```bash
   DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose up --build
   ```
   - Relay boots in **live** mode on port 5555 and expects the host ARK runtime to be
     reachable from inside the container (pass through USB/network interfaces as required by your host OS).
   - Backend becomes available at http://localhost:8000.
   - Frontend is served via NGINX at http://localhost:8080.
3. To use simulated data with Docker, override the relay command at launch time:
   ```bash
   docker compose run --rm relay python aria_stream_relay.py --mode simulate --endpoint tcp://0.0.0.0:5555
   ```
   (or edit `docker-compose.yml` temporarily).
4. Stop the containers:
   ```bash
   docker compose down
   ```
5. (Optional) Switch back to playback by editing `docker-compose.yml` and changing the relay command or by
   exporting `ARIA_RELAY_MODE=simulate` and referencing it inside the compose file.

## Moving to Real Hardware
- Install `projectaria-tools`, the Aria Research Kit, and the macOS Project Aria Client SDK wheel in the same conda environment.
- Use `aria_device_manager subscribe --stream gaze --device-id <uuid>` to validate device + Wi-Fi connectivity before launching the relay.
- The relay already publishes normalized gaze coordinates `[0,1]` and blink states (`open/closing/closed`); adjust smoothing thresholds in `relay/aria_stream_relay.py` if needed.
- Tune Wi-Fi and backend hosts to keep latency below the 150 ms target (See `docs/architecture.md`).

## Using Pupil Core
- Start Pupil Capture (or the new Pupil Player/Core runtime) with the **Pupil Remote** plugin enabled. Note the host/port the remote socket is listening on (defaults to `127.0.0.1:50020`).
- Launch the stack with the relay in Pupil mode:
   ```bash
   PYTHON_BIN=$(which python) \
   scripts/start_stack.sh --mode pupil \
      --pupil-host 127.0.0.1 \
      --pupil-port 50020 \
      --pupil-topic 'gaze.' \
      --pupil-confidence 0.6
   ```
- The relay subscribes to the chosen topic (e.g., `gaze`, `gaze.3d.0`) over ZeroMQ, converts `norm_pos` to `[0,1]` viewport coordinates, and maps low-confidence samples to blink events (threshold configurable via `--pupil-confidence`).
- Make sure Pupil Capture is publishing `norm_pos` in normalized image coordinates; adjust the confidence threshold or topic if you prefer other gaze streams.

## Latency Instrumentation
- Each relay payload includes a `ts` field (UNIX seconds). Add server/UX timestamps (e.g., `performance.now()` on the frontend) and push them to `/patch/use` to compute end-to-end latency.

## Extending
- Add Docker-compose to orchestrate relay/back-end stacks.
- Integrate Prometheus metrics and Grafana for experiment logging.
- Replace static patches with AI-generated ones by publishing into `assets/patches` and refreshing the manifest.
