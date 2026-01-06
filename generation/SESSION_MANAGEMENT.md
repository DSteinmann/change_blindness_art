# Session Management - Reproducible Runs

The generation server now supports session recording and replay, allowing you to reproduce exact sequences of generated images.

## Overview

- **Recording Mode**: Automatically saves all generated images and their metadata to disk
- **Replay Mode**: Load and replay any previously recorded session
- **Sessions Directory**: All sessions stored in `generation/sessions/`

## Directory Structure

```
generation/sessions/
├── session_1736155234/
│   ├── metadata.json          # Session info, sequence of generations
│   ├── 0000_BR.png           # First generated image (Bottom-Right sector)
│   ├── 0001_TL.png           # Second generated image (Top-Left sector)
│   └── ...
└── session_1736155789/
    └── ...
```

## API Usage

### 1. Start Recording a Session

```bash
# Auto-generate session ID
curl -X POST http://localhost:8001/session/start

# Or provide custom ID
curl -X POST "http://localhost:8001/session/start?session_id=my_experiment"
```

**Response:**
```json
{
  "session_id": "session_1736155234",
  "status": "recording"
}
```

From this point, all `/generate` requests will be automatically saved to this session.

### 2. Generate Images (while recording)

Just use the normal generate endpoint - it will automatically save if a session is active:

```bash
curl -X POST http://localhost:8001/generate \
  -H "Content-Type: application/json" \
  -d @generation_request.json
```

Each generation is saved with:
- The generated image
- Target sector (e.g., "BR", "TL")
- Focus sector (where user was looking)
- Prompt used
- Timestamp

### 3. List Available Sessions

```bash
curl http://localhost:8001/session/list
```

**Response:**
```json
{
  "sessions": [
    "session_1736155234",
    "session_1736155789",
    "my_experiment"
  ]
}
```

### 4. Get Session Details

```bash
curl http://localhost:8001/session/session_1736155234
```

**Response:**
```json
{
  "session_id": "session_1736155234",
  "created_at": 1736155234.5,
  "sequence": [
    {
      "index": 0,
      "filename": "0000_BR.png",
      "target_sector": "BR",
      "focus_sector": "TL",
      "prompt": "add a small ladybug in the lower right corner...",
      "timestamp": 1736155240.2
    },
    ...
  ]
}
```

### 5. Replay a Session

Start replay mode:

```bash
curl -X POST http://localhost:8001/session/replay/session_1736155234
```

**Response:**
```json
{
  "session_id": "session_1736155234",
  "status": "replaying",
  "total_generations": 5
}
```

Get next image in sequence:

```bash
curl http://localhost:8001/session/replay/next > image.png
```

Returns the image with headers:
- `X-Sector`: Target sector
- `X-Prompt`: Prompt used
- `X-Index`: Sequence number

Keep calling `/session/replay/next` until you get:
```json
{"status": "complete", "image": null}
```

Stop replay:

```bash
curl -X POST http://localhost:8001/session/replay/stop
```

## Use Cases

### 1. Research - Reproducible Experiments

```bash
# Start recording
curl -X POST "http://localhost:8001/session/start?session_id=experiment_01"

# Run your experiment (user interacts with eye tracking)
# All generations automatically saved

# Later, replay exact sequence
curl -X POST http://localhost:8001/session/replay/experiment_01

# Get images one by one
for i in {1..10}; do
  curl http://localhost:8001/session/replay/next > "frame_$i.png"
done
```

### 2. Debugging - Reproduce Issues

```bash
# Save problematic session
curl -X POST "http://localhost:8001/session/start?session_id=bug_report_xyz"

# Reproduce the bug...

# Share session directory with developer
tar -czf bug_report_xyz.tar.gz generation/sessions/bug_report_xyz/
```

### 3. A/B Testing - Compare Different Prompts

```bash
# Run same eye movement pattern with different prompts
# Session 1: Default prompts
curl -X POST "http://localhost:8001/session/start?session_id=test_A"
# ... generate ...

# Session 2: Modified prompts
curl -X POST "http://localhost:8001/session/start?session_id=test_B"
# ... generate with different sector_prompts.json ...
```

## Metadata Format

Each `metadata.json` contains:

```json
{
  "session_id": "session_1736155234",
  "created_at": 1736155234.567,
  "sequence": [
    {
      "index": 0,
      "filename": "0000_BR.png",
      "target_sector": "BR",
      "focus_sector": "TL",
      "prompt": "add a small ladybug...",
      "timestamp": 1736155240.123
    }
  ]
}
```

## File Management

Sessions are stored permanently until manually deleted:

```bash
# List sessions
ls generation/sessions/

# Delete old session
rm -rf generation/sessions/session_1736155234

# Archive session
tar -czf experiment_backup.tar.gz generation/sessions/my_experiment/
```

## Notes

- **Automatic Recording**: Once a session is started, ALL `/generate` requests are saved
- **No Explicit Stop**: Sessions remain active until server restart or new session started
- **Storage**: Each image is ~500KB-2MB depending on size
- **Replay Stateless**: Replay doesn't affect recording; you can replay while recording a new session

## Integration with Frontend

The frontend can initiate sessions via JavaScript:

```javascript
// Start session
const response = await fetch('http://localhost:8001/session/start', {
  method: 'POST'
});
const { session_id } = await response.json();
console.log(`Recording to: ${session_id}`);

// All subsequent generations automatically saved!

// Later: replay
await fetch(`http://localhost:8001/session/replay/${session_id}`, {
  method: 'POST'
});

// Get each frame
const img = await fetch('http://localhost:8001/session/replay/next');
// Display img...
```
