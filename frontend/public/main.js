const API_ROOT = window.API_ROOT || "http://localhost:8000";
const WS_URL = API_ROOT.replace("http", "ws") + "/ws/stream";

const canvasWrapper = document.querySelector(".canvas-wrapper");
const wsStatus = document.getElementById("ws-status");
const blinkStatus = document.getElementById("blink-status");
const patchStatus = document.getElementById("patch-status");
const gazeStatus = document.getElementById("gaze-status");
const mirrorStatus = document.getElementById("mirror-status");
const sceneCanvas = document.getElementById("scene-canvas");
const sceneCtx = sceneCanvas.getContext("2d");
const calibrationStatus = document.getElementById("calibration-status");
const startCalibrationBtn = document.getElementById("start-calibration");
const captureCalibrationBtn = document.getElementById("capture-calibration");
const resetCalibrationBtn = document.getElementById("reset-calibration");
const calibrationLayer = document.getElementById("calibration-layer");
const calibrationTarget = document.getElementById("calibration-target");
const calibrationInstructions = document.getElementById("calibration-instructions");

const PATCH_PREFETCH = 8;
const LOCAL_STORAGE_KEY = "aria_calibration_state";
const DEFAULT_TRANSFORM = { a: 1, b: 0, c: 0, d: 0, e: 1, f: 0 };
const CALIBRATION_POINTS = [
  { label: "top-left", x_norm: 0.2, y_norm: 0.2 },
  { label: "top-right", x_norm: 0.8, y_norm: 0.2 },
  { label: "bottom-right", x_norm: 0.8, y_norm: 0.8 },
  { label: "bottom-left", x_norm: 0.2, y_norm: 0.8 },
  { label: "center", x_norm: 0.5, y_norm: 0.5 },
];
const SHAPE_TYPES = ["circle", "square", "triangle", "diamond"];
const COLOR_PALETTE = ["#ff5e5b", "#00c2ff", "#ffd166", "#06d6a0", "#c084fc", "#f28482"];

// Simple in-memory cache for patch images keyed by their URL.
const patchImageCache = new Map();

function resizeCanvas() {
  const rect = canvasWrapper.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvasMetrics = { width: rect.width, height: rect.height, dpr };
  sceneCanvas.width = rect.width * dpr;
  sceneCanvas.height = rect.height * dpr;
  sceneCanvas.style.width = `${rect.width}px`;
  sceneCanvas.style.height = `${rect.height}px`;
  sceneCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  renderScene();
  if (calibrationActive) {
    showCalibrationTarget(CALIBRATION_POINTS[calibrationIndex] || CALIBRATION_POINTS[0]);
  }
}

function hashString(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i += 1) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

function derivePatchStyle(patch) {
  const key = patch?.id || patch?.url || JSON.stringify(patch || {});
  const hash = hashString(key);
  return {
    shape: SHAPE_TYPES[hash % SHAPE_TYPES.length],
    fill: COLOR_PALETTE[hash % COLOR_PALETTE.length],
    stroke: "rgba(0,0,0,0.32)",
    scale: 0.22,
  };
}

function drawBackground() {
  const { width, height } = canvasMetrics;
  if (width === 0 || height === 0) {
    return;
  }
  const gradient = sceneCtx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#050d24");
  gradient.addColorStop(1, "#091737");
  sceneCtx.fillStyle = gradient;
  sceneCtx.fillRect(0, 0, width, height);
  sceneCtx.strokeStyle = "rgba(255,255,255,0.05)";
  const step = Math.max(40, Math.min(width, height) / 12);
  sceneCtx.lineWidth = 1;
  for (let x = 0; x <= width; x += step) {
    sceneCtx.beginPath();
    sceneCtx.moveTo(x, 0);
    sceneCtx.lineTo(x, height);
    sceneCtx.stroke();
  }
  for (let y = 0; y <= height; y += step) {
    sceneCtx.beginPath();
    sceneCtx.moveTo(0, y);
    sceneCtx.lineTo(width, y);
    sceneCtx.stroke();
  }
}

function drawPatchShape(entry) {
  if (!entry) {
    return;
  }
  const { placement, style } = entry;
  const { x, y } = normToCanvasPixels(placement);
  const size = Math.min(canvasMetrics.width, canvasMetrics.height) * style.scale;
  sceneCtx.save();
  sceneCtx.translate(x, y);
  sceneCtx.fillStyle = style.fill;
  sceneCtx.strokeStyle = style.stroke;
  sceneCtx.lineWidth = 4;
  if (style.shape === "circle") {
    sceneCtx.beginPath();
    sceneCtx.arc(0, 0, size / 2, 0, Math.PI * 2);
    sceneCtx.fill();
    sceneCtx.stroke();
  } else if (style.shape === "square") {
    sceneCtx.beginPath();
    sceneCtx.rect(-size / 2, -size / 2, size, size);
    sceneCtx.fill();
    sceneCtx.stroke();
  } else if (style.shape === "triangle") {
    sceneCtx.beginPath();
    sceneCtx.moveTo(0, -size / 2);
    sceneCtx.lineTo(size / 2, size / 2);
    sceneCtx.lineTo(-size / 2, size / 2);
    sceneCtx.closePath();
    sceneCtx.fill();
    sceneCtx.stroke();
  } else {
    sceneCtx.beginPath();
    sceneCtx.moveTo(0, -size / 2);
    sceneCtx.lineTo(size / 2, 0);
    sceneCtx.lineTo(0, size / 2);
    sceneCtx.lineTo(-size / 2, 0);
    sceneCtx.closePath();
    sceneCtx.fill();
    sceneCtx.stroke();
  }
  sceneCtx.restore();
}

function getPatchImage(patch) {
  if (!patch || !patch.url) {
    return null;
  }
  const rawUrl = patch.url;
  const url = /^https?:\/\//.test(rawUrl) ? rawUrl : `${API_ROOT}${rawUrl}`;
  let img = patchImageCache.get(url);
  if (!img) {
    img = new Image();
    img.onload = () => {
      // Once loaded, trigger a re-render so the image appears.
      renderScene();
    };
    img.onerror = (err) => {
      console.warn("failed to load patch image", url, err);
      patchImageCache.set(url, null);
    };
    img.src = url;
    patchImageCache.set(url, img);
  }
  if (img && img.complete && img.naturalWidth > 0 && img.naturalHeight > 0) {
    return img;
  }
  return null;
}

function drawPatch(entry) {
  if (!entry) {
    return;
  }
  const { patch } = entry;
  const img = getPatchImage(patch);

  if (img) {
    const canvasW = canvasMetrics.width || sceneCanvas.width;
    const canvasH = canvasMetrics.height || sceneCanvas.height;
    if (!canvasW || !canvasH) {
      return;
    }
    const iw = img.naturalWidth || 1;
    const ih = img.naturalHeight || 1;
    // Scale to fit entirely within the canvas while preserving aspect ratio.
    const scale = Math.min(canvasW / iw, canvasH / ih);
    const drawW = iw * scale;
    const drawH = ih * scale;
    const offsetX = (canvasW - drawW) / 2;
    const offsetY = (canvasH - drawH) / 2;
    sceneCtx.drawImage(img, offsetX, offsetY, drawW, drawH);
  } else {
    // If the image is not yet loaded, draw nothing; it will
    // appear on the next render once the onload handler fires.
  }
}

function drawMirrorIndicator() {
  const { x, y } = normToCanvasPixels(latestMirrorTarget);
  const size = Math.min(canvasMetrics.width, canvasMetrics.height) * 0.12;
  sceneCtx.save();
  sceneCtx.strokeStyle = "rgba(255,255,255,0.4)";
  sceneCtx.setLineDash([6, 6]);
  sceneCtx.lineWidth = 2;
  sceneCtx.beginPath();
  sceneCtx.arc(x, y, size / 2, 0, Math.PI * 2);
  sceneCtx.stroke();
  sceneCtx.restore();
}

function drawGazeCursor() {
  if (!latestGazeCalibrated?.valid) {
    return;
  }
  const { x, y } = normToCanvasPixels(latestGazeCalibrated);
  sceneCtx.save();
  sceneCtx.strokeStyle = "#00e5ff";
  sceneCtx.lineWidth = 2.5;
  sceneCtx.beginPath();
  sceneCtx.moveTo(x - 12, y);
  sceneCtx.lineTo(x + 12, y);
  sceneCtx.moveTo(x, y - 12);
  sceneCtx.lineTo(x, y + 12);
  sceneCtx.stroke();
  sceneCtx.beginPath();
  sceneCtx.arc(x, y, 10, 0, Math.PI * 2);
  sceneCtx.stroke();
  sceneCtx.restore();
}

function renderScene() {
  if (!canvasMetrics.width || !canvasMetrics.height) {
    return;
  }
  sceneCtx.clearRect(0, 0, canvasMetrics.width, canvasMetrics.height);
  drawBackground();
  drawPatch(activePatch);
  drawMirrorIndicator();
  drawGazeCursor();
}

let latestGazeRaw = { x_norm: 0.5, y_norm: 0.5, valid: false };
let latestGazeCalibrated = { x_norm: 0.5, y_norm: 0.5, valid: false };
let lastBlinkState = "open";
let patchQueue = [];
let refillPromise = null;
let placing = false;
let currentPatchId = "n/a";
let activePatch = null;
let latestMirrorTarget = { x_norm: 0.5, y_norm: 0.5 };
let canvasMetrics = { width: 0, height: 0, dpr: 1 };
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
  // Request only generated PNG patches (stimulus="generated") so we
  // skip any legacy SVG defaults defined in manifest.json.
  const response = await fetch(`${API_ROOT}/patch/next?stimulus=generated`);
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
        // Kick off image loading as soon as we know the URL so that
        // by the time the patch is used on a blink, the image is
        // already decoded and swap latency is minimized.
        try {
          void getPatchImage(patch);
        } catch (e) {
          // ignore cache errors; they'll be logged in getPatchImage
        }
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

function getCanvasRect() {
  return sceneCanvas.getBoundingClientRect();
}

function normToCanvasPixels(norm) {
  return {
    x: norm.x_norm * canvasMetrics.width,
    y: norm.y_norm * canvasMetrics.height,
  };
}

function normToViewportPosition(norm) {
  const rect = getCanvasRect();
  return {
    x: rect.left + norm.x_norm * rect.width,
    y: rect.top + norm.y_norm * rect.height,
  };
}

function updateGazeCursor(gaze) {
  latestGazeRaw = gaze;
  const calibrated = applyCalibration(gaze);
  latestGazeCalibrated = { ...calibrated, valid: Boolean(gaze?.valid) };
  gazeStatus.textContent = `${calibrated.x_norm.toFixed(2)}, ${calibrated.y_norm.toFixed(2)}`;
  updateMirrorPreview(calibrated);
  renderScene();
}

function updateMirrorPreview(gaze) {
  if (!gaze || Number.isNaN(gaze.x_norm) || Number.isNaN(gaze.y_norm)) {
    return latestMirrorTarget;
  }
  const mirror = {
    x_norm: clamp(1 - gaze.x_norm, 0, 1),
    y_norm: clamp(1 - gaze.y_norm, 0, 1),
  };
  latestMirrorTarget = mirror;
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
    activePatch = { patch, placement: mirror, style: derivePatchStyle(patch) };
    renderScene();
    updatePatchStatus();
    void recordPatchUse(patch, gaze, mirror);
    void ensurePatchBuffer();
  } finally {
    placing = false;
  }
}

async function handleBlink(state) {
  if (lastBlinkState !== "closed" && state === "closed" && latestGazeCalibrated?.valid) {
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
  const { x, y } = normToViewportPosition(point);
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
  renderScene();
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
  renderScene();
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
  resizeCanvas();
});

window.addEventListener("load", () => {
  loadCalibrationState();
  updateCalibrationStatus();
  updateCalibrationButtons();
  resizeCanvas();
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
