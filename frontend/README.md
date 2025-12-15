# Blink Mirror Frontend

A minimal static UI that subscribes to the backend WebSocket, mirrors patches to the opposite side of wherever the user is looking, and swaps in a new asset only when the wearer blinks.

Rendering happens on a single `<canvas>` element for speed: each patch id is hashed into a simple geometric primitive and color so swaps remain lightweight while still logging patch metadata in the backend.

Use the sidebar controls to run the five-point calibration routine (corners + center). Each capture records the current gaze sample and solves an affine transform so the rendered cursor/patches match the corrected viewpoint. Calibration metadata lives in `localStorage` and can be reset at any time.

## Local Development
```bash
cd frontend/public
python -m http.server 8080
```
Then open http://localhost:8080 in a browser (Chrome recommended).

Set `window.API_ROOT` in the devtools console if the backend is not running on `http://localhost:8000`.
