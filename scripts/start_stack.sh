#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
    PYTHON_BIN="${CONDA_PREFIX}/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi
export PYTHONUNBUFFERED=1
MODE="pupil"
RELAY_ENDPOINT="tcp://127.0.0.1:5555"
DEVICE_ID="${ARIA_DEVICE_ID:-}"
BACKEND_PORT=8000
FRONTEND_PORT=8080
PUPIL_HOST="${PUPIL_REMOTE_HOST:-127.0.0.1}"
PUPIL_PORT="${PUPIL_REMOTE_PORT:-50020}"
PUPIL_TOPIC="${PUPIL_TOPIC:-gaze.}"
PUPIL_CONFIDENCE="${PUPIL_CONFIDENCE_THRESHOLD:-0.6}"

usage() {
  cat <<'EOF'
Usage: scripts/start_stack.sh [options]

Options:
  --mode <simulate|live|pupil>    Relay mode (default: pupil)
  --device-id <uuid>        Aria device id (required for live mode)
  --pupil-host <host>       Pupil Remote host (default: 127.0.0.1)
  --pupil-port <port>       Pupil Remote command port (default: 50020)
  --pupil-topic <topic>     Pupil gaze topic (default: gaze.3d.0)
  --pupil-confidence <val>  Confidence threshold for valid gaze (default: 0.6)
  --endpoint <zmq>          ZeroMQ endpoint (default: tcp://127.0.0.1:5555)
  --backend-port <port>     Backend HTTP port (default: 8000)
  --frontend-port <port>    Frontend HTTP port (default: 8080)
  --help                    Show this help message

Environment:
  PYTHON_BIN   Interpreter that has relay + backend deps installed (default: python3)
  ARIA_DEVICE_ID  Used as fallback for --device-id in live mode

The script runs the relay, backend (uvicorn) and static frontend server locally.
Press Ctrl+C to stop all services.
EOF
}

log() {
  printf '[launcher] %s\n' "$*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --device-id)
      DEVICE_ID="$2"
      shift 2
      ;;
    --pupil-host)
      PUPIL_HOST="$2"
      shift 2
      ;;
    --pupil-port)
      PUPIL_PORT="$2"
      shift 2
      ;;
    --pupil-topic)
      PUPIL_TOPIC="$2"
      shift 2
      ;;
    --pupil-confidence)
      PUPIL_CONFIDENCE="$2"
      shift 2
      ;;
    --endpoint)
      RELAY_ENDPOINT="$2"
      shift 2
      ;;
    --backend-port)
      BACKEND_PORT="$2"
      shift 2
      ;;
    --frontend-port)
      FRONTEND_PORT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

log "Using interpreter: $PYTHON_BIN"

if [[ "$MODE" != "simulate" && "$MODE" != "live" && "$MODE" != "pupil" ]]; then
  echo "--mode must be simulate, live, or pupil" >&2
  exit 1
fi

if [[ "$MODE" == "live" && -z "$DEVICE_ID" ]]; then
  echo "--device-id (or ARIA_DEVICE_ID env) is required for live mode" >&2
  exit 1
fi

PROC_NAMES=()
PROC_PIDS=()

spawn() {
  local name="$1"
  shift
  ("$@" 2>&1 | while IFS= read -r line; do printf '[%s] %s\n' "$name" "$line"; done) &
  PROC_NAMES+=("$name")
  PROC_PIDS+=($!)
}

cleanup() {
  trap - INT TERM
  for pid in "${PROC_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

trap cleanup INT TERM

log "Starting relay in $MODE mode"
relay_cmd=("$PYTHON_BIN" "$ROOT_DIR/relay/aria_stream_relay.py" --mode "$MODE" --endpoint "$RELAY_ENDPOINT")
if [[ "$MODE" == "live" ]]; then
  relay_cmd+=(--device-id "$DEVICE_ID")
elif [[ "$MODE" == "pupil" ]]; then
  relay_cmd+=(--pupil-host "$PUPIL_HOST" --pupil-port "$PUPIL_PORT" --pupil-topic "$PUPIL_TOPIC" --pupil-confidence-threshold "$PUPIL_CONFIDENCE")
fi
spawn "relay" "${relay_cmd[@]}"

log "Starting backend on port $BACKEND_PORT"
spawn "backend" bash -c "cd '$ROOT_DIR/backend' && ARIA_ZMQ_ENDPOINT='$RELAY_ENDPOINT' PATCH_ASSETS_DIR='$ROOT_DIR/assets/patches' '$PYTHON_BIN' -m uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT"

log "Starting frontend on port $FRONTEND_PORT"
spawn "frontend" bash -c "cd '$ROOT_DIR/frontend/public' && '$PYTHON_BIN' -m http.server $FRONTEND_PORT"

exit_code=0
for pid in "${PROC_PIDS[@]}"; do
  if ! wait "$pid"; then
    exit_code=1
  fi
done

exit $exit_code
