# Aria Stream Relay

Publishes Meta Project Aria gaze/blink samples over ZeroMQ so the backend can fan them out to UIs.

## Usage
```bash
python aria_stream_relay.py --mode simulate
```

Key flags:
- `--endpoint` (default `tcp://*:5555`): ZeroMQ PUB endpoint.
- `--mode` `simulate` or `live`.
- `--hz`: sample frequency for simulated data.
- `--blink-interval`: seconds between synthetic blinks.
- `--device-id`: Aria Live Stream device id (required for `live` mode once you wire the SDK).
- `--mode pupil`: subscribe to a running Pupil Core + Remote instance.
- Blink tuning knobs (shared by `live` + `pupil`):
  - `--blink-hold-ms`: minimum time to keep a detected blink closed (debounces noise).
  - `--aria-blink-close-depth` / `--aria-blink-open-depth`: hysteresis thresholds (in meters) for depth-based blinks.
  - `--aria-depth-ema`: exponential smoothing factor for Aria depth samples.
  - `--pupil-blink-close-confidence` / `--pupil-blink-open-confidence`: hysteresis thresholds for Pupil confidence values.
  - `--pupil-confidence-ema`: exponential smoothing factor for Pupil confidence.

Code layout highlights:
- `aria_stream_relay.py` – CLI + simulator + mode dispatcher.
- `aria_live_source.py` – Project Aria SDK plumbing.
- `pupil_core_source.py` – Pupil Remote subscriber.
- `blink_utils.py` – shared EMA/hysteresis helper.

## Wiring to Project Aria
1. Install the Aria Research Kit and the Project Aria Client SDK (`pip install projectaria-client-sdk>=1.2`).
2. Pair the glasses, run `aria_doctor` once, and export your headset id via `ARIA_DEVICE_ID=<uuid>`.
3. Launch the relay in live mode (`python aria_stream_relay.py --mode live --device-id "$ARIA_DEVICE_ID"`). The script will start streaming over the default `mp_streaming_demo` profile and publish normalized gaze/blink events.
4. (Optional) If you are on an older research drop that still exposes `projectaria_tools.stream.gaze_stream`, the relay will automatically fall back to that client instead of the Gen2 SDK.

## Telemetry Payload
```json
{
  "ts": 1732928451.123,
  "event": "sample",
  "gaze": {"x_norm": 0.42, "y_norm": 0.58, "valid": true},
  "blink": {"state": "closed", "confidence": 0.91}
}
```

Send additional `{"event":"blink","state":"closed"}` frames if you want dedicated blink triggers.
