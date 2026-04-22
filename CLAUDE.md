# CLAUDE.md

Guidance for Claude when working in this repo. The project is a gaze-contingent change blindness system with three cooperating services: `backend/` (FastAPI + ZeroMQ, port 8000), `generation/` (FastAPI + OpenRouter, port 8001), and `frontend/` (static JS, port 8080). See `README.md` and `docs/architecture.md` for domain detail.

## Context preservation — read this first

Context is the scarcest resource in this repo. Services talk over WebSocket/ZeroMQ/HTTP, so understanding a bug usually means reading files across all three. Don't pull everything into the main window; delegate.

### Delegate to subagents for anything broad

Use the `Agent` tool with `subagent_type: "Explore"` whenever the question spans more than ~3 files or crosses service boundaries. The subagent reads the files, you get a summary. Rules:

- **Broad search or cross-service trace** → Explore subagent. Examples: "how does a blink travel from Pupil Capture to the frontend swap?", "where is the OpenRouter request built?", "what touches `sector_prompts.json`?"
- **Known file + known symbol** → `Read` / `Grep` directly. No subagent.
- **Planning a multi-step change** → `Plan` subagent. Return the plan, then execute inline.
- **Independent parallel questions** → dispatch Explore subagents in a single message with multiple tool calls.

Brief subagents like a new colleague: state the goal, the files/terms you already know, and cap the response ("under 200 words"). Never ask a subagent to synthesise a fix for you — do that yourself with the file paths they return.

### Read narrowly

- Prefer `Grep` → then targeted `Read` with `offset`/`limit` → over reading a file end-to-end.
- Don't re-read a file you just edited. The Edit tool errors on failure; trust it.
- Don't `cat` logs or large JSON dumps into context. Pipe through `head`/`tail` via Bash, or ask a subagent for a summary.

### Prefer memory and plans over scratch files

- Durable facts about the user or project go in `~/.claude/projects/.../memory/` via the auto-memory system.
- Multi-step work goes in `TaskCreate` / a written plan, not intermediate Markdown docs. Don't create `NOTES.md`, `PLAN.md`, etc. unless the user asks.

## Project-specific gotchas

- **Pupil Core surface gaze**: Pupil Capture uses OpenGL coords (origin bottom-left). `backend/app/pupil_source.py` flips Y — any new consumer of `norm_pos` must do the same.
- **Stale gaze = persistent fixation**: the backend only broadcasts when `gaze_on_surfaces` is non-empty. The frontend has no staleness timeout, so missing packets silently pin the last sector. If you touch fixation logic, verify both sides.
- **Thread/loop boundary**: `pupil_source.py` runs a worker thread and currently uses `asyncio.run` to invoke the async broadcast — this creates a fresh loop per call. New async work from that thread must use `asyncio.run_coroutine_threadsafe` against the FastAPI loop.
- **Center sector (MC)** maps to a *random* corner in `frontend/public/main.js` `getOppositeSector` so changes always land in peripheral vision. Don't "fix" this.
- **Session artefacts** land in `assets/sessions/<id>/` with `metadata.json`. Preserve the schema (`timestamp`, `sector`, `focus_sector`, `prompt`, `index`) — replay code depends on it.
- **Prompt cycling**: `generation/sector_prompts.json` is the source of truth; `generation/prompts.txt` is the fallback. Each sector advances independently — don't reset all indices when editing one sector.

## Running and testing

- Default startup: `docker compose up --build`. Service URLs: frontend `:8080`, backend `:8000`, generation `:8001`.
- No-hardware dev: `scripts/start_stack.sh --mode simulate`.
- Pupil Capture must have the **Pupil Remote** plugin enabled (port 50020) and the **Surface Tracker** configured with AprilTags at screen corners, otherwise surface gaze never arrives.
- For UI work: open `http://localhost:8080?debug=true` (or press `D`) to see the gaze cursor and stats. Verify the feature in the browser before calling it done — type-checks don't catch gaze-contingent bugs.

## Code style

- Follow existing conventions in the touched file. No comments unless the *why* is non-obvious.
- Don't add defensive validation at internal boundaries — trust the other service. Only validate at user-facing endpoints.
- Prefer editing existing files. Don't introduce new modules for one-off helpers.
- Keep feature scope tight: a fix is a fix, not a refactor.

## Before declaring done

- Run the actual code path you changed (browser for UI, `curl` for API, `scripts/test_pupil_remote.py` for the Pupil bridge).
- If you can't test it (no hardware, no API key), say so explicitly instead of claiming success.
- Never commit without the user asking. Never push, force-push, or amend without explicit consent.
