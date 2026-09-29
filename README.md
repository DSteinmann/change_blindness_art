# Gaze-Contingent Change Blindness System

A real-time eye-tracking platform for studying **change blindness** and **peripheral perception**. The system tracks where you look, generates AI-modified images in your peripheral vision, and swaps them in during blinks—when your visual system is naturally suppressed.

Built for **researchers** studying visual perception and **artists** exploring gaze-reactive installations.

## What It Does

1. **Tracks your gaze** using a Pupil Core eye tracker
2. **Detects fixation** on a 3x3 grid of screen sectors
3. **Generates modified images** in the opposite sector (peripheral vision) — either from a curated prompt library or autonomously by the model itself in **semantic mode**
4. **Captions each edit** via a separate text model so successive generations can build on (or diverge from) the prior cumulative state
5. **Swaps images during blinks** when you can't perceive the change

This enables classic [change blindness experiments](https://en.wikipedia.org/wiki/Change_blindness) where participants fail to notice significant changes to images when the change occurs during a visual disruption.

The pipeline runs in three Docker services:
- **`backend`** (FastAPI + ZeroMQ) — bridges Pupil Core to the frontend over WebSocket, fans out gaze and blink events, relays per-session blink counts to the generation service.
- **`generation`** (FastAPI + OpenRouter) — owns the model calls, the per-session edit history, the autonomous captioner, and session metadata.
- **`frontend`** (static ES modules) — runs the participant view, plus two read-only second-screen pages: **`/observer.html`** (operator tablet with gaze cursor, swap counter, edit history, "New Participant" button) and **`/feed.html`** (live full-screen view of each new generation as it lands).

---

## Quick Start

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [Pupil Core](https://pupil-labs.com/products/core/) eye tracker with Pupil Capture (the pipeline can also be driven manually via the API, see below)
- [OpenRouter API key](https://openrouter.ai) for AI image generation

### 1. Clone and configure
```bash
git clone https://github.com/DSteinmann/change_blindness_art.git
cd change_blindness_art

# Create environment file
cat > .env << EOF
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Image model (default if unset). The "pro" tier yields better detail at ~2x latency.
OPENROUTER_IMAGE_MODEL=google/gemini-3-pro-image-preview

# Output resolution: "0.5K" (fast, flash-image-preview only) | "1K" | "2K" | "4K"
OPENROUTER_IMAGE_SIZE=2K

# Text model used to caption each autonomous edit. Cheap + reliable.
OPENROUTER_CAPTION_MODEL=google/gemini-2.5-flash

# "semantic" = model decides each edit autonomously, captioner records what changed,
# successive calls receive the prior 2 generated images as context.
# "cycling"  = uses curated prompts from generation/sector_prompts.json.
GENERATION_MODE=semantic

# Auto-rotate to a new participant session after this many idle seconds.
IDLE_RESET_SEC=180
EOF
```

### 2. Start the system
```bash
# Start Pupil Capture with Pupil Remote plugin enabled (port 50020)

# Launch all services
docker compose up --build
```

### 3. Open the interface
- **Participant view**: http://localhost:8080 (add `?debug=true` to see the gaze cursor and stats)
- **Observer tablet**: http://localhost:8080/observer.html — gaze cursor, sector grid, generation/swap counters, "New Participant" button, edit history
- **Live generation feed**: http://localhost:8080/feed.html — full-screen reveal of each new generation
- **Backend API**: http://localhost:8000 (FastAPI docs at `/docs`)
- **Generation API**: http://localhost:8001 (FastAPI docs at `/docs`)

### 4. Set up gaze tracking in Pupil Capture
1. Calibrate the headset with Pupil Capture's built-in screen-marker calibration
2. Open the participant view full-screen; it draws four AprilTag markers (tag36h11, IDs 0–3) in the screen corners
3. Enable the **Surface Tracker** plugin and add a surface named `screen` from those four markers (override with `PUPIL_SURFACE_NAME`)
4. Enable the **Blink Detector** and **Pupil Remote** (port 50020) plugins
5. Open http://localhost:8080?debug=true and check that the gaze cursor follows your eyes

---

## For Researchers

### Study Design

The system supports several experimental paradigms:

| Paradigm | Description | Configuration |
|----------|-------------|---------------|
| **Blink-contingent** | Changes occur during natural blinks | Default behavior |
| **Saccade-contingent** | Changes during eye movements | Modify `handleBlink()` in frontend |
| **Forced choice** | Present original vs. changed, measure detection | Add response buttons |
| **Threshold measurement** | Vary change magnitude, find detection limits | Set `SEMANTIC_SALIENCE` (`subtle` / `moderate` / `bold`) |

### Data Collection

All generations are automatically logged to `assets/sessions/`. A new session folder rotates whenever the operator hits **New Participant** in the observer view, or after `IDLE_RESET_SEC` (default 180 s) of inactivity.

```
assets/sessions/session_1704567890/
├── metadata.json          # Per-session manifest (see below)
├── 0000_TL.png           # Generated image 1
├── 0001_BR.png           # Generated image 2
└── ...
```

**`metadata.json` schema:**

```jsonc
{
  "session_id": "session_1704567890",
  "participant_id": null,                 // optional, set via observer button
  "created_at": 1704567890.123,
  "runtime": {                            // snapshot of config at session start
    "image_model": "google/gemini-3-pro-image-preview",
    "mode": "semantic",                   // "semantic" or "cycling"
    "pupil_surface_name": "screen",
    "pupil_confidence_threshold": 0.6,
    "grid_size": 3
  },
  "stats": {
    "blink_count": 47,                    // relayed from backend on each blink onset
    "frame_drops": 0
  },
  "calibration": null,                    // optional, set via /session/calibration
  "edit_history": [                       // last 5 captions (sliding window)
    "a small ladybug landed on the leaf",
    "..."
  ],
  "sequence": [
    {
      "index": 0,
      "filename": "0000_TR.png",
      "target_sector": "TR",              // sector the model was asked to modify
      "focus_sector": "BL",               // sector the participant was fixating
      "prompt": "semantic auto-edit",     // or the cycling prompt in cycling mode
      "caption": "a small ladybug landed on the leaf",  // from the captioner; null if it failed
      "duplicate_caption": false,         // set true if caption overlaps a prior one
      "timestamp": 1704567892.456,
      "latency_ms": 26431.0,              // measured wall time of /generate
      "generator": "openrouter-semantic"  // model path that produced the image:
                                          // "fal" | "openrouter-semantic" | "openrouter" | "none" (unchanged)
    }
  ]
}
```

### Generation Modes

Selected by the `GENERATION_MODE` env var; defaults to `cycling` for safe rollout.

**`semantic` (recommended)**
- Each `/generate` call sends the original scene plus the **2 most recent generated images** to the OpenRouter image model in a single user turn. The model is told to add ONE new element to the requested sector and keep everything else unchanged.
- After the response lands, a **background captioner** call (`OPENROUTER_CAPTION_MODEL`, default `gemini-2.5-flash`) compares the *prior cumulative state* with the new generation and writes a one-sentence description of the latest delta. The caption is appended to the in-memory edit history, so the next `/generate` call's "diverge from prior additions" instruction has accurate signal.
- No `sector_prompts.json` required; the file becomes optional. Each session's edit sequence is unique.

**`cycling`**
- Per-sector prompts are read from `generation/sector_prompts.json` (or fallback `generation/prompts.txt`); each sector advances independently. Useful when you want a tightly curated set of modifications across all participants.

**Per-participant lifecycle**
- The observer tablet has a **New Participant** button (`POST /session/start` to the generation service) that rotates the session, clears semantic history, and broadcasts a `session_started` WebSocket event so observer + feed pages reset.
- A 30-second background ticker auto-rotates after `IDLE_RESET_SEC` of no `/generate` and no `/session/start` traffic. Skipped while a generation is in flight.

**Race-resilience**
- A `BackgroundTasks` queue runs the captioner *after* the response is sent, so per-swap latency stays at the image-gen cost (~22–50 s for `gemini-3-pro-image-preview` at 2K).
- The frontend's `BLINK_GRACE_MS = 250` lets a swap fire immediately if generation completes while you're mid-blink, instead of waiting for the next onset.
- A 250 ms gaze-staleness window absorbs natural blinks without resetting the fixation timer.

### Models and Reproducibility

The generation service calls hosted models, which providers update or retire over time. Record the model IDs and the date range of your study alongside your data; each session's `metadata.json` also snapshots the image model in `runtime.image_model`.

| Role | Env var | Default |
|------|---------|---------|
| Image edit (OpenRouter backend) | `OPENROUTER_IMAGE_MODEL` | `google/gemini-3-pro-image-preview` |
| Edit planner (fal backend) | `OPENROUTER_PLANNER_MODEL` | `google/gemini-2.5-flash` |
| Captioner | `OPENROUTER_CAPTION_MODEL` | `google/gemini-2.5-flash` |
| Masked image edit (fal backend) | `FAL_EDIT_MODEL` | `openai/gpt-image-2/edit` |

If the fal backend fails for a request, the service falls back to an unmasked OpenRouter edit; such entries are recorded with `"generator": "openrouter"`, so filter on that field if your analysis requires the masked condition.

Python dependencies are pinned in `backend/requirements.txt` and `generation/requirements.txt`; the Docker images use Python 3.12.

### Replay Previous Sessions

```bash
# List available sessions
curl http://localhost:8001/session/list

# Replay a session (deterministic, same sequence)
curl -X POST http://localhost:8001/session/replay/session_1704567890

# Step through generations
curl http://localhost:8001/session/replay/next
```

### Latency Considerations

For change blindness studies, timing is critical:

| Stage | Typical Latency |
|-------|-----------------|
| Eye tracker → Backend | 15-30 ms |
| Backend → Frontend (WebSocket) | 5-20 ms |
| AI Generation | 2-5 seconds |
| Image swap on blink | 10-40 ms |

**Recommendation**: Pre-generate images for each sector during fixation, so swaps are instant when blinks occur. The system already does this—generation happens during fixation, swap happens on blink.

---

## For Artists

### Interactive Installations

Use gaze tracking to create reactive artworks:

```python
# Example: Trigger different effects based on where viewers look
sector_prompts = {
    "TL": "transform into watercolor style",
    "TR": "add surreal floating elements",
    "BL": "shift to noir black and white",
    "BR": "add bioluminescent glow"
}
```

### Custom Prompts

Edit `generation/sector_prompts.json` to define what happens in each region:

```json
{
  "sectors": {
    "TL": [
      "add ethereal light rays, dreamlike atmosphere",
      "transform textures into flowing fabric",
      "introduce subtle geometric patterns"
    ],
    "MC": [
      "enhance with golden hour lighting",
      "add reflection in imaginary water below"
    ]
  }
}
```

Each sector cycles through its prompts, creating evolving variations.

### No Eye Tracker? Trigger Generations via the API

There is no simulated gaze source; without Pupil Capture you can still exercise the generation pipeline directly:

```bash
# Generate for specific sector
curl -X POST http://localhost:8001/generate \
  -H "Content-Type: application/json" \
  -d '{
    "image_base64": "'$(base64 -i your-image.png)'",
    "focus_x": 0.2,
    "focus_y": 0.2,
    "target_row": 2,
    "target_col": 2
  }' --output result.png
```

---

## Customization

### Prompt System

The system uses a **cycling prompt system** with multiple prompts per sector:

```
generation/
├── sector_prompts.json    # Per-sector prompts (recommended)
└── prompts.txt            # Fallback cycling prompts
```

**sector_prompts.json structure:**
```json
{
  "default": ["fallback prompt 1", "fallback prompt 2"],
  "sectors": {
    "TL": ["prompt 1", "prompt 2", "prompt 3"],
    "TC": ["prompt 1", "prompt 2"],
    ...
  }
}
```

Sectors are named by position:
```
┌────┬────┬────┐
│ TL │ TC │ TR │  T = Top
├────┼────┼────┤  M = Middle
│ ML │ MC │ MR │  B = Bottom
├────┼────┼────┤
│ BL │ BC │ BR │  L/C/R = Left/Center/Right
└────┴────┴────┘
```

### Base Images

Place your stimulus images in `assets/patches/generated/`:

```bash
# The frontend loads this as the base image
assets/patches/generated/your-base-image.png
```

Update `DEFAULT_BASE_IMAGE` in `frontend/public/main.js` to point to your image.

### Fixation Parameters

The 3×3 grid, fixation duration, smoothing factor, gaze-staleness window, and the browser-facing generation URL are served by the backend at `GET /config` and consumed by the frontend on load. Override any of them via the env block above (`GRID_SIZE`, `FIXATION_DURATION_MS`, `GAZE_SMOOTHING_FACTOR`, `GAZE_STALE_MS`, `GENERATION_API`). No frontend rebuild required — restart the backend container.

### Debug Mode

The frontend includes a debug mode for development and calibration. When disabled (default), participants see only the stimulus image for a clean experiment view.

**Toggle debug mode:**
- **URL parameter**: `http://localhost:8080?debug=true`
- **Keyboard**: Press `D` to toggle on/off

| Element | Debug OFF (default) | Debug ON |
|---------|---------------------|----------|
| Gaze cursor | Hidden | Visible |
| Sidebar stats | Hidden | Visible |

**Note**: The center sector (MC) maps to a random corner when fixated, ensuring changes always occur in peripheral vision.

---

## Architecture

```
┌─ Frontend (8080) ───────────────────────────────────────────────┐
│  /                /observer.html             /feed.html          │
│  Participant      Operator tablet            Live generation     │
│  + gaze cursor    + counters / history       reveal              │
└─────────────────────────────────────────────────────────────────┘
        ▲ WebSocket (gaze, blink, generation, swap, session_started)
        │                                            ▲
        │              POST /events/* ───────────────┤
        ▼                                            │
┌─ Backend (8000) ─────────────────────────────────────────────────┐
│  Pupil ZMQ subscriber → WebSocket fan-out                       │
│  Static /assets, /sessions; /config; blink relay                │
└──────────────────────────────────────────────────────────────────┘
         ▲ ZeroMQ                                   ▲ HTTP (internal)
         │                                          │
         │                                          ▼
┌─ Pupil Capture ─┐         ┌─ Generation (8001) ────────────────┐
│ Surface Tracker │         │  /generate  (image model)          │
│ Blink Detector  │         │     ↓ background task              │
│ Pupil Remote    │         │  caption_edit (text model)         │
└─────────────────┘         │  /session/start, /session/blink    │
                            │  SemanticHistory  (per-session)    │
                            └────────────────────────────────────┘
                                   │ OpenRouter (image + text)
                                   ▼
                            google/gemini-3-pro-image-preview
                            google/gemini-2.5-flash (captioner)
```

**Per-swap flow (semantic mode):**

1. Frontend detects 1 s fixation in sector X → POSTs `/generate` to the generation service.
2. Generation service builds a single-user-turn `messages` array (original + last 2 generated images + instruction targeting the opposite sector) and calls OpenRouter.
3. Image model returns the new full-frame scene; service responds to the frontend, which queues it as `pendingSwap`.
4. Frontend swaps the canvas on the next blink onset (or immediately if mid-blink), then POSTs `/events/swap` so the observer counts it.
5. Generation service runs a background captioner call comparing the prior cumulative state to the new scene; the resulting one-sentence caption is appended to `SemanticHistory` and `metadata.json[edit_history]` so the next call has accurate divergence signal.

---

## Running Without Docker

### Environment Setup
```bash
conda env create -f environment.yml
conda activate change-blindness
cp example.env .env   # then add your OPENROUTER_API_KEY
```

### One-command startup
```bash
scripts/start_stack.sh                      # backend :8000, generation :8001, frontend :8080
scripts/start_stack.sh --pupil-host 192.0.2.10 --pupil-port 50020   # Pupil Capture on another machine
scripts/start_stack.sh --help               # all options
```
`start_stack.sh` loads `.env` from the repo root and writes sessions to `assets/sessions/`. Press Ctrl+C to stop everything.

### Manual Service Startup
```bash
# Terminal 1: Backend
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2: Generation
export OPENROUTER_API_KEY=sk-or-v1-xxx
cd generation && python server.py --host 0.0.0.0 --port 8001

# Terminal 3: Frontend
cd frontend/public && python -m http.server 8080
```

---

## API Reference

### Generation Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /generate` | Generate modified image for sector |
| `GET /health` | API status, mode, and current model |
| `GET /prompts` | List current cycling prompts and indices |
| `POST /reset` | Reset cycling-prompt indices |
| `POST /session/start` | Rotate to a new participant session (optional `participant_id`); clears semantic history |
| `POST /session/blink` | Increment blink counter for the active session (called by backend on each onset) |
| `POST /session/calibration` | Record calibration metadata for the active session |
| `GET /session/list` | List recorded sessions |
| `GET /session/{id}` | Load a session's metadata.json |
| `POST /session/replay/{id}` | Start replaying a session |
| `GET /session/replay/next` | Get next image in replay |
| `POST /session/replay/stop` | Stop replay mode |

### Generate Request
```json
{
  "image_base64": "data:image/png;base64,...",
  "focus_x": 0.3,
  "focus_y": 0.3,
  "target_row": 2,
  "target_col": 2,
  "grid_size": 3
}
```

### Backend Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `WS /ws/stream` | Real-time gaze, blink, generation, swap, and session_started events |
| `GET /config` | Runtime config consumed by the frontend on load (grid size, fixation duration, smoothing, gaze stale window, browser-facing generation URL) |
| `GET /telemetry/latest` | Latest gaze sample |
| `GET /healthz` | Backend liveness |
| `POST /events/generation` | Generation service relays new image events to all WS clients |
| `POST /events/swap` | Frontend relays swap events (called when an image actually swaps in) |
| `POST /events/session_started` | Generation service relays participant rollovers |
| Static `/assets/...` | Serves `assets/patches/` (base images and markers) |
| Static `/sessions/...` | Serves `assets/sessions/` (per-session generated PNGs) |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No API key set" | Create `.env` with `OPENROUTER_API_KEY`; in semantic mode the service falls back to `cycling` if the key is missing |
| "Model did not return image" | Verify the chosen image model supports image output via OpenRouter; try `google/gemini-3.1-flash-image-preview` or `google/gemini-3-pro-image-preview` |
| Gaze cursor doesn't move | Check `docker compose logs backend` — if you see `no surface data - check Surface Tracker setup!` repeatedly, AprilTags aren't visible to the world camera. Make sure the Surface Tracker plugin is enabled and all four markers have a green outline in Pupil Capture's World view |
| Many blinks "don't trigger swaps" | Confirm Pupil Capture's **Blink Detector** plugin is enabled. The new backend log line ends with `\| blinks received this session: N` — if N stays at 0, blinks aren't reaching the backend at all |
| `metadata.blink_count` stays 0 but blinks log on backend | The backend → generation relay is failing. Check `docker compose logs backend \| grep "blink relay"`; in docker-compose this requires `GENERATION_INTERNAL_URL=http://generation:8001` |
| Frontend errors `Fetch API cannot load http://generation:8001` | Browser cached an old `/config` response. Hard-refresh (`Cmd+Shift+R`) every open tab; the public URL must be `http://localhost:8001`, not the docker service name |
| Calibration inaccurate | Recalibrate in Pupil Capture, then check the Surface Tracker still sees all four markers |
| High latency | Lower `OPENROUTER_IMAGE_SIZE` to `1K` or `0.5K` (latter requires `gemini-3.1-flash-image-preview`) |
| Generation hangs at first call after rebuild | Idle reset auto-rotates after `IDLE_RESET_SEC` (default 180 s); fire a fixation within that window, or POST to `/session/start` to refresh manually |
| Captions show as `null` in metadata | Captioner round-trip failed (rate limit, network); the entry image is still saved. Check `docker compose logs generation \| grep caption_edit` |

---

## Citation

If you use this system in your research, please cite:

```bibtex
@software{gaze_contingent_change_blindness,
  title = {Gaze-Contingent Change Blindness System},
  author = {Steinmann, Dominik},
  year = {2026},
  url = {https://github.com/DSteinmann/change_blindness_art}
}
```

---

## Contributing

Contributions welcome! Areas of interest:
- Additional eye tracker support (Tobii, SMI)
- Local generation models (Stable Diffusion, FLUX)
- Analysis tools for session data
- Mobile/tablet support

---

## License

MIT License - See [LICENSE](LICENSE) for details. Third-party image credits are listed in [CREDITS.md](CREDITS.md).
