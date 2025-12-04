const API_ROOT = window.API_ROOT || "http://localhost:8000";
const WS_URL = API_ROOT.replace("http", "ws") + "/ws/stream";

const canvasWrapper = document.querySelector(".canvas-wrapper");
const wsStatus = document.getElementById("ws-status");
const blinkStatus = document.getElementById("blink-status");
const patchStatus = document.getElementById("patch-status");
const gazeStatus = document.getElementById("gaze-status");
const mirrorStatus = document.getElementById("mirror-status");
const baseImage = document.getElementById("base-image");
const gazeCursor = document.getElementById("gaze-cursor");
const patchImage = document.getElementById("peripheral-patch");

const PATCH_PREFETCH = 4;
const PATCH_SIZE = 200;

let latestGaze = { x_norm: 0.5, y_norm: 0.5 };
let lastBlinkState = "open";
let patchQueue = [];
let refillPromise = null;
let placing = false;
let currentPatchId = "n/a";
let activePatch = null;

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

async function fetchPatch() {
  const response = await fetch(`${API_ROOT}/patch/next`);
  if (!response.ok) {
    throw new Error(`patch/next failed (${response.status})`);
  }
  return response.json();
}

function updatePatchStatus() {
  patchStatus.textContent = `${currentPatchId} (queue ${patchQueue.length})`;
}

async function ensurePatchBuffer() {
  if (refillPromise) {
    return refillPromise;
  }
  refillPromise = (async () => {
    try {
      while (patchQueue.length < PATCH_PREFETCH) {
        const patch = await fetchPatch();
        patchQueue.push(patch);
        updatePatchStatus();
      }
    } catch (err) {
      console.error("patch prefetch failed", err);
    } finally {
      refillPromise = null;
    }
  })();
  return refillPromise;
}

function toCanvasPosition(norm) {
  const wrapperRect = canvasWrapper.getBoundingClientRect();
  const baseRect = baseImage.getBoundingClientRect();
  const offsetX = baseRect.left - wrapperRect.left;
  const offsetY = baseRect.top - wrapperRect.top;
  return {
    x: offsetX + norm.x_norm * baseRect.width,
    y: offsetY + norm.y_norm * baseRect.height,
    wrapperRect,
  };
}

function updateGazeCursor(gaze) {
  latestGaze = gaze;
  const { x, y } = toCanvasPosition(gaze);
  gazeCursor.style.left = `${x}px`;
  gazeCursor.style.top = `${y}px`;
  gazeStatus.textContent = `${gaze.x_norm.toFixed(2)}, ${gaze.y_norm.toFixed(2)}`;
}

function renderActivePatch() {
  if (!activePatch) {
    return;
  }
  const { patch, placement } = activePatch;
  const { x, y, wrapperRect } = toCanvasPosition(placement);
  const half = PATCH_SIZE / 2;
  const left = clamp(x - half, 0, wrapperRect.width - PATCH_SIZE);
  const top = clamp(y - half, 0, wrapperRect.height - PATCH_SIZE);
  patchImage.src = `${API_ROOT}${patch.url}`;
  patchImage.style.left = `${left}px`;
  patchImage.style.top = `${top}px`;
  patchImage.classList.remove("hidden");
}

function updateMirrorPreview(gaze) {
  const mirror = {
    x_norm: clamp(1 - gaze.x_norm, 0, 1),
    y_norm: clamp(1 - gaze.y_norm, 0, 1),
  };
  mirrorStatus.textContent = `${mirror.x_norm.toFixed(2)}, ${mirror.y_norm.toFixed(2)}`;
  return mirror;
}

async function recordPatchUse(patch, gaze, placement) {
  try {
    await fetch(`${API_ROOT}/patch/use`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patch, gaze, placement, reason: "blink-opposite" }),
    });
  } catch (err) {
    console.error("patch/use failed", err);
  }
}

async function placePatchAtMirror(gaze) {
  if (placing) {
    return;
  }
  placing = true;
  try {
    const mirror = updateMirrorPreview(gaze);
    if (!patchQueue.length) {
      await ensurePatchBuffer();
    }
    if (!patchQueue.length) {
      return;
    }
    const patch = patchQueue.shift();
    currentPatchId = patch.id || patch.url;
    activePatch = { patch, placement: mirror };
    renderActivePatch();
    updatePatchStatus();
    void recordPatchUse(patch, gaze, mirror);
    void ensurePatchBuffer();
  } finally {
    placing = false;
  }
}

async function handleBlink(state) {
  if (lastBlinkState !== "closed" && state === "closed") {
    await placePatchAtMirror(latestGaze);
  }
  blinkStatus.textContent = state;
  lastBlinkState = state;
}

function connectWebSocket() {
  const socket = new WebSocket(WS_URL);
  wsStatus.textContent = "connecting";

  socket.addEventListener("open", () => {
    wsStatus.textContent = "connected";
    void ensurePatchBuffer();
    setInterval(() => socket.readyState === 1 && socket.send("ping"), 10000);
  });

  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.event === "sample") {
      if (data.gaze && data.gaze.valid) {
        updateGazeCursor(data.gaze);
        updateMirrorPreview(data.gaze);
      }
      if (data.blink && data.blink.state) {
        void handleBlink(data.blink.state);
      }
    }
  });

  socket.addEventListener("close", () => {
    wsStatus.textContent = "disconnected";
    setTimeout(connectWebSocket, 1000);
  });

  socket.addEventListener("error", () => {
    wsStatus.textContent = "error";
    socket.close();
  });
}

window.addEventListener("resize", () => {
  updateGazeCursor(latestGaze);
  renderActivePatch();
});

window.addEventListener("load", () => {
  updatePatchStatus();
  connectWebSocket();
});
