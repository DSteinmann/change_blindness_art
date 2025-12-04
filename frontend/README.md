# Blink Mirror Frontend

A minimal static UI that subscribes to the backend WebSocket, mirrors patches to the opposite side of wherever the user is looking, and swaps in a new asset only when the wearer blinks.

## Local Development
```bash
cd frontend/public
python -m http.server 8080
```
Then open http://localhost:8080 in a browser (Chrome recommended).

Set `window.API_ROOT` in the devtools console if the backend is not running on `http://localhost:8000`.
