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
const calibrationStatus = document.getElementById("calibration-status");
const startCalibrationBtn = document.getElementById("start-calibration");
const captureCalibrationBtn = document.getElementById("capture-calibration");
const resetCalibrationBtn = document.getElementById("reset-calibration");
const calibrationLayer = document.getElementById("calibration-layer");
const calibrationTarget = document.getElementById("calibration-target");
const calibrationInstructions = document.getElementById("calibration-instructions");

const PATCH_PREFETCH = 4;
const PATCH_SIZE = 200;
const LOCAL_STORAGE_KEY = "aria_calibration_state";
const DEFAULT_TRANSFORM = { a: 1, b: 0, c: 0, d: 0, e: 1, f: 0 };
const CALIBRATION_POINTS = [
  { label: "top-left", x_norm: 0.2, y_norm: 0.2 },
  { label: "top-right", x_norm: 0.8, y_norm: 0.2 },
  { label: "bottom-right", x_norm: 0.8, y_norm: 0.8 },
  { label: "bottom-left", x_norm: 0.2, y_norm: 0.8 },
  { label: "center", x_norm: 0.5, y_norm: 0.5 },
];

let latestGazeRaw = { x_norm: 0.5, y_norm: 0.5, valid: false };
let latestGazeCalibrated = { x_norm: 0.5, y_norm: 0.5, valid: false };
let lastBlinkState = "open";
let patchQueue = [];
let refillPromise = null;
let placing = false;
let currentPatchId = "n/a";
let activePatch = null;
let calibrationTransform = { ...DEFAULT_TRANSFORM };
let calibrationPointCount = 0;
let calibrationActive = false;
let calibrationSamples = [];
let calibrationIndex = 0;

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function loadCalibrationState() {
  try {
    const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (!stored) {
      calibrationTransform = { ...DEFAULT_TRANSFORM };
      calibrationPointCount = 0;
      return;
    }
    const parsed = JSON.parse(stored);
    if (parsed && parsed.transform) {
      calibrationTransform = {
        ...DEFAULT_TRANSFORM,
        ...parsed.transform,
      };
      calibrationPointCount = parsed.points || 0;
    }
  } catch (err) {
    console.warn("calibration state load failed", err);
    calibrationTransform = { ...DEFAULT_TRANSFORM };
    calibrationPointCount = 0;
  }
}

function persistCalibrationState() {
  try {
    localStorage.setItem(
      LOCAL_STORAGE_KEY,
      JSON.stringify({ transform: calibrationTransform, points: calibrationPointCount })
    );
  } catch (err) {
    console.warn("calibration state save failed", err);
  }
}

function resetCalibrationState() {
  calibrationTransform = { ...DEFAULT_TRANSFORM };
  calibrationPointCount = 0;
  localStorage.removeItem(LOCAL_STORAGE_KEY);
}

function updateCalibrationStatus() {
  if (calibrationActive) {
    calibrationStatus.textContent = `capturing ${calibrationIndex + 1}/${CALIBRATION_POINTS.length}`;
  } else if (calibrationPointCount > 0) {
    calibrationStatus.textContent = `custom (${calibrationPointCount} pts)`;
  } else {
    calibrationStatus.textContent = "identity";
  }
}

function applyCalibration(gaze) {
  const { a, b, c, d, e, f } = calibrationTransform;
  const x = a * gaze.x_norm + b * gaze.y_norm + c;
  const y = d * gaze.x_norm + e * gaze.y_norm + f;
  return {
    ...gaze,
    x_norm: clamp(x, 0, 1),
    y_norm: clamp(y, 0, 1),
  };
}

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
  latestGazeRaw = gaze;
  const calibrated = applyCalibration(gaze);
  latestGazeCalibrated = calibrated;
  const { x, y } = toCanvasPosition(calibrated);
  gazeCursor.style.left = `${x}px`;
  gazeCursor.style.top = `${y}px`;
  gazeStatus.textContent = `${calibrated.x_norm.toFixed(2)}, ${calibrated.y_norm.toFixed(2)}`;
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

async function recordPatchUse(patch, calibratedGaze, placement) {
  try {
    await fetch(`${API_ROOT}/patch/use`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        patch,
        gaze: calibratedGaze,
        raw_gaze: latestGazeRaw,
        placement,
        reason: "blink-opposite",
      }),
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
    await placePatchAtMirror(latestGazeCalibrated);
  }
  blinkStatus.textContent = state;
  lastBlinkState = state;
}

function setCalibrationOverlay(active) {
  calibrationLayer.classList.toggle("calibration-hidden", !active);
  calibrationLayer.classList.toggle("active", active);
}

function showCalibrationTarget(point) {
  const { x, y } = toCanvasPosition(point);
  calibrationTarget.style.left = `${x}px`;
  calibrationTarget.style.top = `${y}px`;
  calibrationInstructions.textContent = `Point ${calibrationIndex + 1}/${CALIBRATION_POINTS.length}: ${point.label}`;
}

function updateCalibrationButtons() {
  startCalibrationBtn.disabled = calibrationActive;
  captureCalibrationBtn.disabled = !calibrationActive;
}

function startCalibration() {
  calibrationActive = true;
  calibrationSamples = [];
  calibrationIndex = 0;
  setCalibrationOverlay(true);
  showCalibrationTarget(CALIBRATION_POINTS[calibrationIndex]);
  updateCalibrationButtons();
  updateCalibrationStatus();
}

function addCalibrationSample() {
  if (!calibrationActive) {
    return;
  }
  calibrationSamples.push({
    measured: { ...latestGazeRaw },
    target: CALIBRATION_POINTS[calibrationIndex],
  });
  calibrationIndex += 1;
  if (calibrationIndex >= CALIBRATION_POINTS.length) {
    finishCalibration();
  } else {
    showCalibrationTarget(CALIBRATION_POINTS[calibrationIndex]);
    updateCalibrationStatus();
  }
}

function solve3x3(matrix, vector) {
  const m = matrix.map((row) => row.slice());
  const b = vector.slice();
  for (let i = 0; i < 3; i += 1) {
    let pivotRow = i;
    for (let r = i + 1; r < 3; r += 1) {
      if (Math.abs(m[r][i]) > Math.abs(m[pivotRow][i])) {
        pivotRow = r;
      }
    }
    if (Math.abs(m[pivotRow][i]) < 1e-8) {
      return null;
    }
    if (pivotRow !== i) {
      [m[i], m[pivotRow]] = [m[pivotRow], m[i]];
      [b[i], b[pivotRow]] = [b[pivotRow], b[i]];
    }
    const pivot = m[i][i];
    for (let c = i; c < 3; c += 1) {
      m[i][c] /= pivot;
    }
    b[i] /= pivot;
    for (let r = 0; r < 3; r += 1) {
      if (r === i) continue;
      const factor = m[r][i];
      for (let c = i; c < 3; c += 1) {
        m[r][c] -= factor * m[i][c];
      }
      b[r] -= factor * b[i];
    }
  }
  return b;
}

function computeAffineTransform(samples) {
  if (samples.length < 3) {
    return null;
  }
  let s_xx = 0;
  let s_xy = 0;
  let s_x = 0;
  let s_yy = 0;
  let s_y = 0;
  let count = 0;
  let sx_tx = 0;
  let sy_tx = 0;
  let s_tx = 0;
  let sx_ty = 0;
  let sy_ty = 0;
  let s_ty = 0;
  samples.forEach(({ measured, target }) => {
    const x = measured.x_norm;
    const y = measured.y_norm;
    s_xx += x * x;
    s_xy += x * y;
    s_x += x;
    s_yy += y * y;
    s_y += y;
    sx_tx += x * target.x_norm;
    sy_tx += y * target.x_norm;
    s_tx += target.x_norm;
    sx_ty += x * target.y_norm;
    sy_ty += y * target.y_norm;
    s_ty += target.y_norm;
    count += 1;
  });
  const mat = [
    [s_xx, s_xy, s_x],
    [s_xy, s_yy, s_y],
    [s_x, s_y, count],
  ];
  const coeffX = solve3x3(mat, [sx_tx, sy_tx, s_tx]);
  const coeffY = solve3x3(mat, [sx_ty, sy_ty, s_ty]);
  if (!coeffX || !coeffY) {
    return null;
  }
  return {
    a: coeffX[0],
    b: coeffX[1],
    c: coeffX[2],
    d: coeffY[0],
    e: coeffY[1],
    f: coeffY[2],
  };
}

function finishCalibration() {
  const transform = computeAffineTransform(calibrationSamples);
  if (transform) {
    calibrationTransform = transform;
    calibrationPointCount = calibrationSamples.length;
    persistCalibrationState();
  } else {
    console.warn("calibration solve failed; keeping previous transform");
  }
  calibrationActive = false;
  setCalibrationOverlay(false);
  calibrationSamples = [];
  calibrationIndex = 0;
  updateCalibrationButtons();
  updateCalibrationStatus();
  updateGazeCursor(latestGazeRaw);
  renderActivePatch();
}

function cancelCalibration() {
  calibrationActive = false;
  calibrationSamples = [];
  calibrationIndex = 0;
  setCalibrationOverlay(false);
  updateCalibrationButtons();
  updateCalibrationStatus();
}

function resetCalibration() {
  resetCalibrationState();
  cancelCalibration();
  updateCalibrationStatus();
  updateGazeCursor(latestGazeRaw);
  renderActivePatch();
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
        updateMirrorPreview(latestGazeCalibrated);
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
  updateGazeCursor(latestGazeRaw);
  renderActivePatch();
  if (calibrationActive) {
    showCalibrationTarget(CALIBRATION_POINTS[calibrationIndex] || CALIBRATION_POINTS[0]);
  }
});

window.addEventListener("load", () => {
  loadCalibrationState();
  updateCalibrationStatus();
  updateCalibrationButtons();
  updatePatchStatus();
  connectWebSocket();
});

startCalibrationBtn?.addEventListener("click", () => {
  startCalibration();
});

captureCalibrationBtn?.addEventListener("click", () => {
  addCalibrationSample();
});

resetCalibrationBtn?.addEventListener("click", () => {
  resetCalibration();
});
