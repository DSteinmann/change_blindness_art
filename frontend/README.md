# Frontend

Static ES-module UI with three pages:

- `index.html`: participant view. Shows the base image with AprilTag markers in the corners, tracks fixations on the sector grid, requests an edit for the opposite sector, and swaps the new image in on the next blink.
- `observer.html`: operator tablet with the gaze cursor, counters, edit history, and a **New Participant** button.
- `feed.html`: full-screen reveal of each new generation.

Runtime settings (grid size, fixation duration, smoothing, generation URL) are fetched from the backend's `/config` endpoint.

## Local Development
```bash
cd frontend/public
python -m http.server 8080
```
Then open http://localhost:8080 (add `?debug=true` or press `D` for the gaze cursor and stats).

Set `window.API_ROOT` before the modules load if the backend is not running on `http://localhost:8000`.
