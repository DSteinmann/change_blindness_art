# Project Aria Blink-Opposite Patch Prototype

A gaze-contingent peripheral stimulus system for Meta Project Aria glasses (with Pupil Core fallback). The system displays visual patches in the user's peripheral vision and **swaps them to the opposite side of the screen on blink**, enabling research into change blindness and peripheral perception.

## Quick Start (Docker)

The fastest way to get running:

### 1. Prerequisites
- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [Pupil Core](https://pupil-labs.com/products/core/) headset (or use simulation mode)
- OpenRouter API key for AI image generation (optional)

### 2. Set up environment variables
Create a `.env` file in the repo root:
```bash
# Required for AI image generation (get key from https://openrouter.ai)
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Optional: change the image generation model
OPENROUTER_IMAGE_MODEL=google/gemini-2.5-flash-image
```

### 3. Start the stack
```bash
# Start Pupil Capture first with Pupil Remote plugin enabled (port 50020)

# Then launch all services
docker compose up --build
```

### 4. Open the UI
- Frontend: http://localhost:8080
- Backend API: http://localhost:8000
- Generation API: http://localhost:8001

### 5. Calibrate
1. Click **Start Calibration** in the sidebar
2. Fixate on each of the 5 targets and click **Capture Sample**
3. The gaze cursor will now be calibrated to your display

## How It Works

```
┌─────────────────┐     ┌─────────────┐     ┌──────────────┐
│  Pupil Core /   │────▶│   Backend   │────▶│   Frontend   │
│  Aria Glasses   │     │  (FastAPI)  │     │   (Canvas)   │
└─────────────────┘     └─────────────┘     └──────────────┘
        │                      │                    │
    Gaze + Blink          WebSocket            Renders gaze
       data               streaming            cursor + patches
                               │                    │
                               ▼                    ▼
                    ┌─────────────────┐      On fixation:
                    │   Generation    │◀─────triggers AI
                    │    (OpenRouter) │      generation
                    └─────────────────┘
                               │
                               ▼
                    On blink: swap patch
                    to opposite side
```

**Core behavior:**
1. User looks at a base image
2. System detects which sector (3x3 grid) the user is fixating on
3. After 1 second of fixation, generates a modified image for the **opposite** sector
4. When the user blinks, the modified image is swapped in (change blindness paradigm)

## Services

| Service | Port | Description |
|---------|------|-------------|
| `frontend` | 8080 | Canvas-based UI with gaze visualization |
| `backend` | 8000 | FastAPI server, WebSocket streaming, patch management |
| `generation` | 8001 | AI image generation via OpenRouter API |

## Repo Layout

```
├── frontend/          # Static web UI (vanilla JS + Canvas)
├── backend/           # FastAPI server for gaze/blink streaming
├── generation/        # AI image generation server
├── relay/             # Bridges Aria/Pupil data to ZeroMQ
├── assets/
│   ├── patches/       # Base images and generated patches
│   └── sessions/      # Recorded generation sessions
├── docker/            # Dockerfiles for each service
├── scripts/           # Helper scripts
└── docs/              # Architecture documentation
```

## Running Without Docker

### Environment Setup (Conda)
```bash
# Create and activate environment
conda env create -f environment.yml
conda activate ubicomp312

# For Aria glasses: install the SDK wheel from Meta
pip install /path/to/projectaria_client_sdk-*.whl
```

### Start Services Manually

**Option A: Using the start script**
```bash
# With Pupil Core
scripts/start_stack.sh --mode pupil --pupil-host 127.0.0.1 --pupil-port 50020

# With Aria glasses
scripts/start_stack.sh --mode live --device-id <your-device-uuid>

# Simulation mode (no hardware)
scripts/start_stack.sh --mode simulate
```

**Option B: Run each service separately**
```bash
# Terminal 1: Backend
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2: Generation server
cd generation && python server.py --host 0.0.0.0 --port 8001

# Terminal 3: Frontend
cd frontend/public && python -m http.server 8080
```

## Configuration

### Generation Server

The generation server uses OpenRouter's API for AI image generation. Configure via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | (required) | Your OpenRouter API key |
| `OPENROUTER_IMAGE_MODEL` | `google/gemini-2.5-flash-image` | Model for image generation |

Prompts can be customized in:
- `generation/prompts.txt` - Cycling prompts for generation
- `generation/sector_prompts.json` - Sector-specific prompts (per grid cell)

### Session Recording

All generated images are automatically saved to `assets/sessions/<session-id>/` with metadata for reproducibility. Use the session API to replay previous runs:

```bash
# List sessions
curl http://localhost:8001/session/list

# Start replay
curl -X POST http://localhost:8001/session/replay/<session-id>

# Get next image in replay
curl http://localhost:8001/session/replay/next
```

## Calibration

The 5-point calibration (corners + center) maps raw gaze coordinates to screen space:

1. Open http://localhost:8080
2. Click **Start Calibration**
3. Fixate on each highlighted target and click **Capture Sample**
4. Calibration is stored in `localStorage` and persists across sessions
5. Click **Reset** to recalibrate

## Using Pupil Core

1. Start Pupil Capture with **Pupil Remote** plugin enabled (default: `127.0.0.1:50020`)
2. Enable the **Blink Detector** plugin for blink events
3. Launch the stack with `--mode pupil`:
   ```bash
   scripts/start_stack.sh --mode pupil \
     --pupil-host 127.0.0.1 \
     --pupil-port 50020 \
     --pupil-confidence 0.6
   ```

## Using Project Aria

1. Install the Project Aria Client SDK (macOS only, from Meta)
2. Connect glasses via Wi-Fi and get device UUID:
   ```bash
   aria_device_manager list
   ```
3. Launch with `--mode live`:
   ```bash
   scripts/start_stack.sh --mode live --device-id <uuid>
   ```

## API Reference

### Backend (port 8000)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/ws/stream` | WebSocket | Real-time gaze/blink stream |
| `/patch/next` | GET | Get next patch asset |
| `/patch/use` | POST | Log patch placement |
| `/telemetry/latest` | GET | Latest gaze/blink sample |

### Generation (port 8001)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check with API status |
| `/generate` | POST | Generate modified image |
| `/prompts` | GET | List current prompts |
| `/session/list` | GET | List recorded sessions |
| `/session/replay/{id}` | POST | Start session replay |

## Troubleshooting

**"No API key set"**: Create `.env` file with `OPENROUTER_API_KEY`

**"Model did not return an image"**: The model may not support image generation. Try `google/gemini-2.5-flash-image` or check OpenRouter for compatible models.

**Gaze cursor not showing**: Check WebSocket connection status in the sidebar. Ensure Pupil Capture is running with Pupil Remote enabled.

**Calibration seems off**: Reset calibration and recapture. Ensure you're fixating steadily on each target before capturing.

## License

Research prototype - see LICENSE file.
