# Semantic Swaps with Cumulative Divergence

**Status:** Design
**Date:** 2026-04-22
**Owner:** DSteinmann

## Problem

Today every participant sees the same hand-curated prompts cycling in a fixed order. For a science-art fair we want every participant's session to feel unique, and we want the peripheral edits to interact semantically with whatever is already on screen rather than reading as pasted stickers.

## Non-goals

- Participant-supplied input (theme, mood, text). Scene-only semantics for v1.
- Per-visitor souvenir videos, printouts, or leaderboards. Separate feature.
- Replacing the gaze / fixation / blink pipeline. Unchanged.
- CI, Playwright end-to-end suite, or full test-automation rollout.

## Summary

A new `semantic` generation mode (selected by env var) sends each `/generate` call through a single multimodal OpenRouter request that reasons about the current scene, diverges from the last five in-session edits, and returns both the modified image and a one-sentence caption of what it changed. Captions are the unit of history that distinguishes one participant's trajectory from another's. Sessions are participant-scoped: an operator button on the observer tablet starts a new session, and a 3-minute idle timer auto-rotates if the operator forgets.

## Architecture

```
frontend /generate (unchanged request shape)
        |
        v
generation service (GENERATION_MODE=semantic)
    |-- sectors.py              (unchanged pixel bounds)
    |-- semantic.py              (NEW: history, prompt builder, response parser)
    |-- openrouter.py            (image+caption extraction)
    |-- session_manager.py       (stores caption per entry; clears per session)
    |-- /generate                (branches on mode; fallback chain for failures)
    |-- /session/start           (rotates session, clears SemanticHistory)
        |
        v
backend /events/generation       (carries caption alongside prompt)
backend /events/session_started  (new, broadcast on participant rollover)
        |
        v
observer.html  |  feed.html      (consume caption, reset on session_started)
```

No change to the frontend gaze / fixation / WebSocket pipeline. No change to sector geometry. The only cross-service surface additions are the `caption` field on `generation` events and the new `session_started` event.

## Prompt design

Each `/generate` call in semantic mode issues one OpenRouter chat-completion request with modalities `["image", "text"]`.

**User message content (in order):**

1. `image_url` — the original, unedited base image for the session.
2. `image_url` — the current evolved scene (what the frontend just posted).
3. `text` — instruction block, templated:

```
You are editing an image for a change-blindness installation. A participant is
about to briefly look away from the region you are modifying; they should only
notice the change if they come back to look at it directly.

IMAGE 1 is the ORIGINAL, unedited scene.
IMAGE 2 is the scene as it currently stands after several prior edits.

Prior edits applied in this session (most recent last):
  1. "<caption>"
  2. "<caption>"
  ... up to 5 ...

Your task:
- Propose ONE new edit, distinct in subject, scale, and style from every prior
  edit above. Do not repeat motifs, colours, or object classes that already
  appear.
- The edit MUST be visually contained within the pixel rectangle
  (x1=..., y1=..., x2=..., y2=...) - the <SECTOR_NAME> sector of a 3x3 grid.
- The edit should make semantic sense given what is already in the scene -
  it should feel like it belongs, not like a pasted sticker.
- Return the FULL modified image (not a crop), and a ONE-SENTENCE caption of
  exactly what you added or changed, prefixed with "CAPTION:". Example:
  CAPTION: a small paper boat now drifts across the puddle on the right.
```

**Response parsing:**

- `message.images[0]` yields the modified image (existing path in `openrouter.py`).
- `message.content` is scanned for a line starting with `CAPTION:`; the prefix is stripped. If absent, a degenerate caption is synthesised as `edit #N in <sector> at HH:MM:SS` so history still advances.

**History:** `SemanticHistory` is an in-memory `dict[str, list[str]]` keyed by `session_id`. `append(sid, caption)` pushes and slices to the last five. `clear(sid)` drops the key. Fine for single-process runtime; persistence is not required for fair operation.

**Original-image capture:** The generation service has no persistent notion of the base image today. On the first `/generate` call of a session, the incoming `image_base64` is cached in a new `SemanticHistory.originals: dict[str, str]` (keyed by `session_id`). Subsequent calls for that session pass both the cached original and the fresh incoming image into `build_prompt`. `clear(sid)` drops the original alongside the captions so participant rollover is atomic.

**Sliding window:** 5 captions. Large enough to prevent short-loop repetition, small enough to keep request size flat after long sessions.

## Session and participant lifecycle

A session equals one participant. Three triggers roll it over:

1. **Operator button** on the observer tablet. Sends `POST {GENERATION_API}/session/start` with an optional `participant_id`. The generation service creates a new `session_<timestamp>/` folder, clears `SemanticHistory[prev_session_id]`, and calls backend `POST /events/session_started`.
2. **Idle auto-rotate.** A background task in the generation service ticks every 30 s. If no `/generate` has fired for more than 3 min *and* the last `session_started` was more than 3 min ago, it fires `start_new_session()` internally, mirroring the operator path. Skipped if a generation is in flight.
3. **Startup.** Existing behaviour: one session is auto-started on process boot. No change.

Backend exposes `POST /events/session_started` → broadcasts `{event: "session_started", session_id, participant_id}` via the existing WebSocket. Observer and feed both listen and reset their local UI state (counters, thumbnail, edit-history list, feed image).

Future: a "first-valid-gaze after idle" auto-start in the backend. Out of scope for v1.

## Data and metadata

**`SessionManager.save_generation`** gains `caption: Optional[str] = None`. Stored under `caption` on the metadata entry alongside `prompt`.

**Session metadata root** gains:

- `mode: "semantic" | "cycling"` — written into `runtime` at session start so replays are reproducible.
- `edit_history: list[str]` — snapshot of the sliding window written on every `save_generation`. Cheap and keeps the metadata file self-describing.

**New module `generation/semantic.py`:**

- `class SemanticHistory` with `append(session_id, caption)`, `window(session_id, n=5) -> list[str]`, `clear(session_id)`.
- `build_prompt(original_b64, current_b64, captions, region, sector_name) -> list[dict]` — pure, unit-testable.
- `parse_response(message: dict) -> tuple[Image.Image | None, str | None]` — wraps the existing `_extract_image` and adds caption parsing.
- `generate_semantic(...)` — orchestrates the request + parse. Lives in `semantic.py` or `openrouter.py` (implementation choice during planning).

**`generation/server.py`:**

- Env var `GENERATION_MODE` (`semantic` | `cycling`, default `cycling`).
- `/generate` branches on mode. Only the payload builder and response parser differ; sector math, session save, backend notification are shared.
- `/session/start` clears the previous session's entry in `SemanticHistory` and calls backend `/events/session_started`.
- Idle background task (3-min threshold, 30-s tick).

**`backend/app/main.py`:** new `POST /events/session_started` endpoint that broadcasts to WS clients. No other changes.

**Event shape changes:**

- `generation` WS event adds a `caption` field.
- New `session_started` WS event.

**Backwards compatibility:** all new metadata fields are optional. Old session folders still load. Observer and feed tolerate missing `caption` by falling back to `prompt`.

## UI surfaces

**Feed (`feed.html` / `feed.js`)**

- Bottom-right italic line prefers `caption`; falls back to `prompt`.
- On `session_started`: clear the current image, re-show the "Waiting for first generation…" overlay.

**Observer (`observer.html` / `observer.js`)**

- Rename the "Last prompt" card to "Last edit"; show `caption` (or `prompt` fallback).
- New card **"Edit history"**: scrollable list, up to 10 captions, newest first.
- New sidebar button **"New Participant"** with a small text input for `participant_id` (blank = anonymous). Clicking sends `POST ${GENERATION_API}/session/start`.
- On `session_started`: reset generation / swap / caught counters, clear edit-history list, clear thumbnail and grid highlights, flash "New participant: <id>" banner for 2 s.

The generation service base URL is already delivered by `/config`; reuse it for the button.

## Failure modes

| Failure | Behaviour |
|---|---|
| OpenRouter HTTP error or timeout | Retry once with a terser prompt. If that also fails, fall back to the cycling-prompt path for this call only; session stays in semantic mode. |
| Response has text but no image | Same as above: terser retry, then cycling fallback. |
| Response has image but no `CAPTION:` line | Accept the image. Synthesise `edit #N in <sector> at HH:MM:SS` as the caption so history still advances. Log a warning. |
| Caption is a near-duplicate of a prior caption (case-insensitive substring against the 5-window) | Accept anyway and flag the entry with `duplicate_caption: true`. Do not attempt model-level de-duplication. |
| `GENERATION_MODE=semantic` but `OPENROUTER_API_KEY` missing at startup | Log a clear error and coerce the mode to `cycling`. Demo continues. |
| `SemanticHistory[current_session_id]` missing (process restart mid-session) | Treat as empty history for this call. Next save repopulates. No crash. |
| Idle-reset fires while a generation is in flight | Skip this tick; check again in 30 s. |

Caption and history are advanced only on genuine semantic successes. Fallbacks 1-2 do not grow the history.

No new exception classes. All fallbacks remain inside `/generate`; the HTTP contract is unchanged.

## Testing

**Unit tests** in a new `generation/tests/` directory:

- `test_semantic.py`
  - `build_prompt` with 0, 3, 5, and 8 captions (sliding-window behaviour).
  - `build_prompt` with varying sectors — verify rectangle text matches `calculate_sector_region`.
  - `parse_response` extracts caption from well-formed content; returns `None` on missing caption; still returns the image.
  - `SemanticHistory.clear()` on session rollover; isolation between session IDs.
- `test_sectors.py` — pure-function tests for the grid math extracted during the earlier refactor.
- `test_session_manager.py` — `save_generation` with a caption; backwards-compat with legacy `metadata.json` files that predate the field.

**Integration tests** using `httpx.MockTransport` to stub OpenRouter:

- Success path: image + caption, history appended, metadata saved.
- Image-only response: degenerate caption synthesised, `duplicate_caption` logic skipped.
- HTTP 500: retry fires, then cycling fallback kicks in; history length unchanged.
- Missing API key: mode coerced to cycling; no exception surfaces to the frontend.

**Manual QA checklist** to run during fair rehearsal:

- 10-swap session in semantic mode. Verify captions in `metadata.json`, no duplicate flags in the first 5 edits, observer edit-history list scrolls, feed shows captions.
- Hit "New Participant" mid-session. Verify feed clears, observer counters reset, new session folder exists, semantic history cleared.
- Leave the system idle for 3+ minutes. Verify a new session folder appears automatically.
- Remove the OpenRouter API key and run again. Verify silent fallback to cycling, no 5xx responses to the frontend.

**Out of scope for this spec:** CI workflow, Playwright end-to-end for the observer, load testing.

## Rollout

- `GENERATION_MODE=cycling` remains the default. Flip to `semantic` per environment when ready.
- Semantic can be A/B'd at a fair by alternating sessions between modes via the operator button + an env toggle on the tablet. Sessions are self-describing so analysis is straightforward.
- Revert path: flip the env var back to `cycling`. No schema migration, no data-loss risk.
