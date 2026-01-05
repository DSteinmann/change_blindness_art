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
BACKEND_PORT=8000
FRONTEND_PORT=8080
export PUPIL_HOST="${PUPIL_REMOTE_HOST:-127.0.0.1}"
export PUPIL_REMOTE_PORT="${PUPIL_REMOTE_PORT:-50020}"
export PUPIL_TOPIC="${PUPIL_TOPIC:-gaze.}"
export PUPIL_CONFIDENCE_THRESHOLD="${PUPIL_CONFIDENCE_THRESHOLD:-0.6}"

usage() {
  cat <<'EOF'
Usage: scripts/start_stack.sh [options]

Options:
  --pupil-host <host>       Pupil Remote host (default: 127.0.0.1)
  --pupil-port <port>       Pupil Remote command port (default: 50020)
  --pupil-topic <topic>     Pupil gaze topic (default: gaze.)
  --pupil-confidence <val>  Confidence threshold for valid gaze (default: 0.6)
  --backend-port <port>     Backend HTTP port (default: 8000)
  --frontend-port <port>    Frontend HTTP port (default: 8080)
  --help                    Show this help message

Environment:
  PYTHON_BIN                  Interpreter that has backend deps installed (default: python3)
  PUPIL_REMOTE_HOST           Used as fallback for --pupil-host
  PUPIL_REMOTE_PORT         Used as fallback for --pupil-port
  PUPIL_TOPIC                 Used as fallback for --pupil-topic
  PUPIL_CONFIDENCE_THRESHOLD  Used as fallback for --pupil-confidence

The script runs the backend (uvicorn) and static frontend server locally.
Press Ctrl+C to stop all services.
EOF
}

log() {
  printf '[launcher] %s\n' "$*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pupil-host)
      export PUPIL_HOST="$2"
      shift 2
      ;;
    --pupil-port)
      export PUPIL_REMOTE_PORT="$2"
      shift 2
      ;;
    --pupil-topic)
      export PUPIL_TOPIC="$2"
      shift 2
      ;;
    --pupil-confidence)
      export PUPIL_CONFIDENCE_THRESHOLD="$2"
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

log "Starting backend on port $BACKEND_PORT"
spawn "backend" bash -c "cd '$ROOT_DIR/backend' && PATCH_ASSETS_DIR='$ROOT_DIR/assets/patches' '$PYTHON_BIN' -m uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT"

log "Starting frontend on port $FRONTEND_PORT"
spawn "frontend" bash -c "cd '$ROOT_DIR/frontend/public' && '$PYTHON_BIN' -m http.server $FRONTEND_PORT"

exit_code=0
for pid in "${PROC_PIDS[@]}"; do
  if ! wait "$pid"; then
    exit_code=1
  fi
done

exit $exit_code
