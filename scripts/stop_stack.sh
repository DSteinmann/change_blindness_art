#!/usr/bin/env bash
set -euo pipefail

pids_killed=0
kill_pattern() {
  local pattern="$1"
  if pkill -f "$pattern" 2>/dev/null; then
    printf '[stopper] terminated processes matching "%s"\n' "$pattern"
    pids_killed=1
  fi
}

kill_pattern 'aria_stream_relay.py'
kill_pattern 'uvicorn app.main:app'
kill_pattern 'python -m http.server'

if [[ $pids_killed -eq 0 ]]; then
  echo '[stopper] nothing to stop'
fi
