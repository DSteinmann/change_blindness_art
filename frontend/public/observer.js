import { API_ROOT, loadRuntimeConfig } from "./config.js";
import { openStream } from "./ws.js";
import { gazeToSector, sectorName } from "./sectors.js";

const DETECTION_WINDOW_MS = 1500;
const EDIT_HISTORY_MAX = 10;

const stage = document.getElementById("stage");
const grid = document.getElementById("grid");
const cursor = document.getElementById("cursor");
const lastCaptionEl = document.getElementById("last-caption");
const editHistoryEl = document.getElementById("edit-history");
const lastImageEl = document.getElementById("last-image");
const statGenerations = document.getElementById("stat-generations");
const statSwaps = document.getElementById("stat-swaps");
const statCaught = document.getElementById("stat-caught");
const participantInput = document.getElementById("participant-input");
const newParticipantBtn = document.getElementById("new-participant-btn");
const participantBanner = document.getElementById("participant-banner");

function initialState() {
  return {
    focusSector: null,
    generations: 0,
    swaps: 0,
    caught: 0,
    pendingDetection: null,
    lastValidTs: 0,
  };
}

function buildGrid(gridSize) {
  grid.style.gridTemplateColumns = `repeat(${gridSize}, 1fr)`;
  grid.style.gridTemplateRows = `repeat(${gridSize}, 1fr)`;
  grid.innerHTML = "";
  const cells = [];
  for (let r = 0; r < gridSize; r++) {
    cells[r] = [];
    for (let c = 0; c < gridSize; c++) {
      const el = document.createElement("div");
      el.className = "cell";
      el.textContent = sectorName({ row: r, col: c });
      grid.appendChild(el);
      cells[r][c] = el;
    }
  }
  return cells;
}

async function main() {
  const config = await loadRuntimeConfig();
  const cells = buildGrid(config.grid_size);
  let state = initialState();

  newParticipantBtn.addEventListener("click", async () => {
    const id = participantInput.value.trim() || null;
    try {
      await fetch(`${config.generation_api}/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ participant_id: id }),
      });
    } catch (err) {
      console.warn("New Participant POST failed", err);
    }
  });

  function resetForNewSession(participantId) {
    state = initialState();
    statGenerations.textContent = "0";
    statSwaps.textContent = "0";
    statCaught.textContent = "0";
    lastCaptionEl.textContent = "—";
    editHistoryEl.innerHTML = "";
    lastImageEl.style.backgroundImage = "";
    for (const row of cells) for (const cell of row) cell.classList.remove("focus", "modified");
    participantBanner.textContent = participantId
      ? `New participant: ${participantId}`
      : "New participant";
    participantBanner.style.opacity = "1";
    setTimeout(() => (participantBanner.style.opacity = "0"), 2000);
  }

  function setFocus(sector) {
    if (state.focusSector) cells[state.focusSector.row][state.focusSector.col].classList.remove("focus");
    state.focusSector = sector;
    if (state.focusSector) cells[state.focusSector.row][state.focusSector.col].classList.add("focus");
  }

  function flashModified(sector) {
    const cell = cells[sector.row][sector.col];
    cell.classList.add("modified");
    setTimeout(() => cell.classList.remove("modified"), 1200);
  }

  function positionCursor(smoothed, stale) {
    const rect = stage.getBoundingClientRect();
    cursor.style.left = `${smoothed.x_norm * rect.width}px`;
    cursor.style.top = `${smoothed.y_norm * rect.height}px`;
    cursor.style.display = "block";
    cursor.classList.toggle("stale", stale);
  }

  function pushEditHistory(caption) {
    if (!caption) return;
    const li = document.createElement("li");
    li.textContent = caption;
    editHistoryEl.prepend(li);
    while (editHistoryEl.children.length > EDIT_HISTORY_MAX) {
      editHistoryEl.lastChild.remove();
    }
  }

  openStream({
    sample: ({ gaze }) => {
      if (!gaze) return;
      if (gaze.valid) state.lastValidTs = Date.now();
      const stale = Date.now() - state.lastValidTs > config.gaze_stale_ms;
      if (!gaze.valid || stale) {
        cursor.style.display = "none";
        setFocus(null);
        return;
      }
      positionCursor(gaze, false);
      const sector = gazeToSector(gaze, config.grid_size);
      if (!state.focusSector
          || sector.row !== state.focusSector.row
          || sector.col !== state.focusSector.col) {
        setFocus(sector);
        if (state.pendingDetection
            && sector.row === state.pendingDetection.targetSector.row
            && sector.col === state.pendingDetection.targetSector.col
            && Date.now() < state.pendingDetection.deadline) {
          state.caught += 1;
          statCaught.textContent = state.caught;
          state.pendingDetection = null;
        }
      }
    },

    generation: ({ session_id, filename, prompt, caption }) => {
      state.generations += 1;
      statGenerations.textContent = state.generations;
      const label = caption || prompt || "";
      lastCaptionEl.textContent = label || "—";
      pushEditHistory(caption);
      if (session_id && filename) {
        lastImageEl.style.backgroundImage = `url(${API_ROOT}/sessions/${session_id}/${filename})`;
      }
    },

    swap: ({ target_row, target_col }) => {
      state.swaps += 1;
      statSwaps.textContent = state.swaps;
      if (typeof target_row === "number" && typeof target_col === "number") {
        const target = { row: target_row, col: target_col };
        flashModified(target);
        state.pendingDetection = {
          targetSector: target,
          deadline: Date.now() + DETECTION_WINDOW_MS,
        };
      }
    },

    session_started: ({ participant_id }) => {
      resetForNewSession(participant_id);
    },
  });
}

main();
