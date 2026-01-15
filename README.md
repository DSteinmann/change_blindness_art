# Gaze-Contingent Change Blindness System

A real-time eye-tracking platform for studying **change blindness** and **peripheral perception**. The system tracks where you look, generates AI-modified images in your peripheral vision, and swaps them in during blinks—when your visual system is naturally suppressed.

Built for **researchers** studying visual perception and **artists** exploring gaze-reactive installations.

![Architecture](docs/architecture-diagram.png)

## What It Does

1. **Tracks your gaze** using Pupil Core or Meta Aria glasses
2. **Detects fixation** on a 3x3 grid of screen sectors
3. **Generates modified images** in the opposite sector (peripheral vision)
4. **Swaps images during blinks** when you can't perceive the change

This enables classic [change blindness experiments](https://en.wikipedia.org/wiki/Change_blindness) where participants fail to notice significant changes to images when the change occurs during a visual disruption.

---

## Quick Start

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [Pupil Core](https://pupil-labs.com/products/core/) eye tracker (or simulation mode)
- [OpenRouter API key](https://openrouter.ai) for AI image generation

### 1. Clone and configure
```bash
git clone https://github.com/your-repo/ubicomp_capstone.git
cd ubicomp_capstone

# Create environment file
cat > .env << EOF
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_IMAGE_MODEL=google/gemini-2.5-flash-image
EOF
```

### 2. Start the system
```bash
# Start Pupil Capture with Pupil Remote plugin enabled (port 50020)

# Launch all services
docker compose up --build
```

### 3. Open the interface
- **Frontend UI**: http://localhost:8080
- **Backend API**: http://localhost:8000
- **Generation API**: http://localhost:8001

### 4. Calibrate gaze tracking
1. Click **Start Calibration** in the sidebar
2. Look at each of the 5 targets and click **Capture Sample**
3. Your gaze cursor should now track accurately

---

## For Researchers

### Study Design

The system supports several experimental paradigms:

| Paradigm | Description | Configuration |
|----------|-------------|---------------|
| **Blink-contingent** | Changes occur during natural blinks | Default behavior |
| **Saccade-contingent** | Changes during eye movements | Modify `handleBlink()` in frontend |
| **Forced choice** | Present original vs. changed, measure detection | Add response buttons |
| **Threshold measurement** | Vary change magnitude, find detection limits | Use `change_magnitude` parameter |

### Data Collection

All generations are automatically logged to `assets/sessions/`:

```
assets/sessions/session_1704567890/
├── metadata.json          # Timestamps, prompts, sectors
├── 0000_TL.png           # Generated image 1
├── 0001_BR.png           # Generated image 2
└── ...
```

**Metadata includes:**
- Timestamp of each generation
- Which sector was modified
- Which sector user was fixating on
- Prompt used for generation
- Sequence index for ordering

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

### No Eye Tracker? Use Simulation

```bash
# Run with simulated gaze data
scripts/start_stack.sh --mode simulate
```

Or trigger generations manually via API:

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

In `frontend/public/main.js`:

```javascript
const GRID_SIZE = 3;              // 3x3 sector grid
const FIXATION_DURATION_MS = 1000; // Time before triggering generation
const SMOOTHING_FACTOR = 0.08;     // Gaze cursor smoothing (lower = smoother)
```

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
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (8080)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Gaze Cursor │  │ Base Image  │  │ Calibration Panel   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │ WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        Backend (8000)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Gaze Stream │  │ Blink Det.  │  │ Patch Management    │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
         │                                      │
         │ ZeroMQ                               │ HTTP
         ▼                                      ▼
┌─────────────────┐                ┌─────────────────────────┐
│   Eye Tracker   │                │   Generation API (8001) │
│  Pupil / Aria   │                │  OpenRouter / Local SD  │
└─────────────────┘                └─────────────────────────┘
```

---

## Running Without Docker

### Environment Setup
```bash
conda env create -f environment.yml
conda activate ubicomp312

# For Aria glasses (macOS only)
pip install /path/to/projectaria_client_sdk-*.whl
```

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

### Using Different Eye Trackers

**Pupil Core:**
```bash
scripts/start_stack.sh --mode pupil \
  --pupil-host 127.0.0.1 \
  --pupil-port 50020
```

**Meta Aria:**
```bash
aria_device_manager list  # Get device UUID
scripts/start_stack.sh --mode live --device-id <uuid>
```

**Simulation (no hardware):**
```bash
scripts/start_stack.sh --mode simulate
```

---

## API Reference

### Generation Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /generate` | Generate modified image for sector |
| `GET /health` | API status and configuration |
| `GET /prompts` | List current prompts and indices |
| `POST /reset` | Reset prompt cycling to beginning |
| `GET /session/list` | List recorded sessions |
| `POST /session/replay/{id}` | Start replaying a session |
| `GET /session/replay/next` | Get next image in replay |

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
| `WS /ws/stream` | Real-time gaze/blink stream |
| `GET /telemetry/latest` | Latest gaze sample |
| `GET /patch/next` | Get next patch asset |
| `POST /patch/use` | Log patch placement |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No API key set" | Create `.env` with `OPENROUTER_API_KEY` |
| "Model did not return image" | Check model supports image generation, try `google/gemini-2.5-flash-image` |
| Gaze cursor not showing | Ensure Pupil Capture running with Pupil Remote enabled |
| Calibration inaccurate | Recalibrate, ensure steady fixation on each target |
| High latency | Check network, consider local generation model |
| Generation fails | Check OpenRouter credit balance and API status |

---

## Citation

If you use this system in your research, please cite:

```bibtex
@software{gaze_contingent_change_blindness,
  title = {Gaze-Contingent Change Blindness System},
  year = {2024},
  url = {https://github.com/your-repo/ubicomp_capstone}
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

MIT License - See [LICENSE](LICENSE) for details.
