const API_ROOT = window.API_ROOT || "http://localhost:8000";
const WS_URL = API_ROOT.replace("http", "ws") + "/ws/stream";
const GENERATION_API = window.GENERATION_API || "http://localhost:8001";

// Debug mode - toggle with ?debug=true in URL or press 'D' key
let debugMode = new URLSearchParams(window.location.search).get('debug') === 'true';

// DOM Elements
const canvasWrapper = document.querySelector(".canvas-wrapper");
const wsStatus = document.getElementById("ws-status");
const blinkStatus = document.getElementById("blink-status");
const gazeStatus = document.getElementById("gaze-status");
const mirrorStatus = document.getElementById("mirror-status");
const sceneCanvas = document.getElementById("scene-canvas");
const sceneCtx = sceneCanvas.getContext("2d");
const viewportGazeCursor = document.getElementById("viewport-gaze-cursor");

// State
let renderScheduled = false;
let canvasMetrics = { width: 0, height: 0, dpr: 1 };
let activePatch = null;
let capturedImageBase64 = null;

// Gaze tracking
let latestGazeRaw = { x_norm: 0.5, y_norm: 0.5, valid: false };
let smoothedGaze = { x_norm: 0.5, y_norm: 0.5 };
const SMOOTHING_FACTOR = 0.08;  // Lower = smoother, less jitter (was 0.15)
let lastBlinkState = "open";

// 3x3 Grid sectors for robust fixation detection
const GRID_SIZE = 3;  // 3x3 = 9 sectors
let currentSector = { row: 1, col: 1 };  // Center
let lastSector = { row: 1, col: 1 };

// Fixation detection (per sector)
const FIXATION_DURATION_MS = 1000;  // Reduced since sectors are more stable
let fixationStartTime = null;
let fixatedSector = null;
let isGenerating = false;

// Pending image queue for blink-triggered swap
let pendingSwap = null;

// Default base image (served from backend at /assets which maps to assets/patches/)
const DEFAULT_BASE_IMAGE = `${API_ROOT}/assets/generated/a-single-banana-on-a-white-background-in-the-upp-p1-1765812893-00.png`;

const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

// ============================================
// CANVAS RENDERING
// ============================================

function scheduleRender() {
  if (renderScheduled) return;
  renderScheduled = true;
  requestAnimationFrame(() => {
    renderScene();
    renderScheduled = false;
  });
}

function resizeCanvas() {
  const rect = canvasWrapper.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvasMetrics = { width: rect.width, height: rect.height, dpr };
  sceneCanvas.width = rect.width * dpr;
  sceneCanvas.height = rect.height * dpr;
  sceneCanvas.style.width = `${rect.width}px`;
  sceneCanvas.style.height = `${rect.height}px`;
  sceneCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  scheduleRender();
}

function drawBackground() {
  const { width, height } = canvasMetrics;
  if (!width || !height) return;
  
  const gradient = sceneCtx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#050d24");
  gradient.addColorStop(1, "#091737");
  sceneCtx.fillStyle = gradient;
  sceneCtx.fillRect(0, 0, width, height);
}

function drawPatch(entry) {
  if (!entry?.image) return;
  
  const { image } = entry;
  const canvasW = canvasMetrics.width || 1;
  const canvasH = canvasMetrics.height || 1;
  const iw = image.naturalWidth || 1;
  const ih = image.naturalHeight || 1;
  
  const scale = Math.min(canvasW / iw, canvasH / ih);
  const drawW = iw * scale;
  const drawH = ih * scale;
  const offsetX = (canvasW - drawW) / 2;
  const offsetY = (canvasH - drawH) / 2;
  
  sceneCtx.drawImage(image, offsetX, offsetY, drawW, drawH);
}

function renderScene() {
  if (!canvasMetrics.width || !canvasMetrics.height) return;
  sceneCtx.clearRect(0, 0, canvasMetrics.width, canvasMetrics.height);
  drawBackground();
  drawPatch(activePatch);
}

// ============================================
// GAZE TRACKING
// ============================================

function updateViewportGazeCursor(gaze) {
  if (!viewportGazeCursor) return;

  // Always update smoothed gaze for internal tracking
  if (gaze?.valid) {
    smoothedGaze.x_norm += SMOOTHING_FACTOR * (gaze.x_norm - smoothedGaze.x_norm);
    smoothedGaze.y_norm += SMOOTHING_FACTOR * (gaze.y_norm - smoothedGaze.y_norm);

    // Only show cursor in debug mode
    if (debugMode) {
      viewportGazeCursor.style.left = `${smoothedGaze.x_norm * window.innerWidth}px`;
      viewportGazeCursor.style.top = `${smoothedGaze.y_norm * window.innerHeight}px`;
      viewportGazeCursor.style.display = "block";
    } else {
      viewportGazeCursor.style.display = "none";
    }
  } else {
    viewportGazeCursor.style.display = "none";
  }
}

function toggleDebugMode() {
  debugMode = !debugMode;
  console.log(`Debug mode: ${debugMode ? 'ON' : 'OFF'}`);

  // Update cursor visibility
  if (!debugMode && viewportGazeCursor) {
    viewportGazeCursor.style.display = "none";
  }

  // Update sidebar visibility
  const sidebar = document.querySelector('.sidebar');
  if (sidebar) {
    sidebar.style.display = debugMode ? 'flex' : 'none';
  }
}

function updateGazeCursor(gaze) {
  latestGazeRaw = gaze;
  updateViewportGazeCursor(gaze);
  
  gazeStatus.textContent = `${gaze.x_norm.toFixed(2)}, ${gaze.y_norm.toFixed(2)}`;
  
  // Show opposite (target) region
  const mirror = { x: 1 - gaze.x_norm, y: 1 - gaze.y_norm };
  mirrorStatus.textContent = `${mirror.x.toFixed(2)}, ${mirror.y.toFixed(2)}`;
  
  checkFixation(gaze);
  scheduleRender();
}

// ============================================
// SECTOR-BASED FIXATION DETECTION
// ============================================

// Convert normalized gaze to sector (0-2 for row and col)
function gazeToSector(gaze) {
  const col = Math.min(GRID_SIZE - 1, Math.floor(gaze.x_norm * GRID_SIZE));
  const row = Math.min(GRID_SIZE - 1, Math.floor(gaze.y_norm * GRID_SIZE));
  return { row, col };
}

// Get the opposite sector
function getOppositeSector(sector) {
  // Special case: center (MC) maps to a random corner
  // This avoids modifying the exact area the user is looking at
  if (sector.row === 1 && sector.col === 1) {
    const corners = [
      { row: 0, col: 0 },  // TL
      { row: 0, col: 2 },  // TR
      { row: 2, col: 0 },  // BL
      { row: 2, col: 2 }   // BR
    ];
    const randomCorner = corners[Math.floor(Math.random() * corners.length)];
    console.log(`Center fixation → random corner: ${sectorName(randomCorner)}`);
    return randomCorner;
  }

  return {
    row: (GRID_SIZE - 1) - sector.row,
    col: (GRID_SIZE - 1) - sector.col
  };
}

// Convert sector to normalized region center
function sectorToNormCenter(sector) {
  return {
    x_norm: (sector.col + 0.5) / GRID_SIZE,
    y_norm: (sector.row + 0.5) / GRID_SIZE
  };
}

// Sector name for display
function sectorName(sector) {
  const rowNames = ["T", "M", "B"];  // Top, Middle, Bottom
  const colNames = ["L", "C", "R"];  // Left, Center, Right
  return `${rowNames[sector.row]}${colNames[sector.col]}`;
}

function checkFixation(gaze) {
  // Debug: Log why we might skip
  if (!gaze?.valid) {
    blinkStatus.textContent = "no valid gaze";
    fixationStartTime = null;
    return;
  }
  if (isGenerating) {
    // Don't reset timer while generating, just skip check
    return;
  }
  if (!capturedImageBase64) {
    blinkStatus.textContent = "no base image";
    return;
  }
  
  // Determine which sector the gaze is in
  const sector = gazeToSector(smoothedGaze);  // Use smoothed for stability
  const sectorChanged = sector.row !== currentSector.row || sector.col !== currentSector.col;
  
  if (sectorChanged) {
    // Reset fixation timer when sector changes
    lastSector = currentSector;
    currentSector = sector;
    fixationStartTime = Date.now();
    fixatedSector = null;
    console.log(`Sector changed to ${sectorName(sector)}`);
  } else if (fixationStartTime !== null) {
    const elapsed = Date.now() - fixationStartTime;
    const progress = Math.min(elapsed / FIXATION_DURATION_MS, 1);
    
    const opposite = getOppositeSector(sector);
    blinkStatus.textContent = `${sectorName(sector)}→${sectorName(opposite)} ${(progress * 100).toFixed(0)}%`;
    
    // Debug: show what's blocking generation
    if (elapsed >= FIXATION_DURATION_MS) {
      if (fixatedSector) {
        blinkStatus.textContent = `${sectorName(sector)} already triggered`;
      } else if (pendingSwap) {
        blinkStatus.textContent = `pending swap for ${sectorName(pendingSwap.targetSector)}`;
      } else {
        // Trigger generation!
        console.log(`Triggering generation for sector ${sectorName(sector)}`);
        fixatedSector = sector;
        triggerSectorGeneration(sector);
      }
    }
  } else {
    fixationStartTime = Date.now();
    console.log(`Started fixation timer for ${sectorName(sector)}`);
  }
}

async function triggerSectorGeneration(focusSector) {
  if (isGenerating || pendingSwap) return;
  
  isGenerating = true;
  const oppositeSector = getOppositeSector(focusSector);
  blinkStatus.textContent = `generating ${sectorName(oppositeSector)}...`;
  
  console.log(`Fixation in ${sectorName(focusSector)}, will modify ${sectorName(oppositeSector)}`);
  
  try {
    await generateForSector(focusSector, oppositeSector);
    blinkStatus.textContent = pendingSwap ? `${sectorName(oppositeSector)} ready` : "ready";
  } catch (err) {
    console.error("Generation error:", err);
    blinkStatus.textContent = "error";
  }
  
  setTimeout(() => { isGenerating = false; }, 1000);
}

async function generateForSector(focusSector, targetSector) {
  if (!capturedImageBase64) return null;
  
  const focusCenter = sectorToNormCenter(focusSector);
  const targetCenter = sectorToNormCenter(targetSector);
  
  console.log(`Generating: focus=${sectorName(focusSector)} (${focusCenter.x_norm.toFixed(2)},${focusCenter.y_norm.toFixed(2)}), target=${sectorName(targetSector)}`);
  
  const response = await fetch(`${GENERATION_API}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      image_base64: capturedImageBase64,
      focus_x: focusCenter.x_norm,
      focus_y: focusCenter.y_norm,
      // Pass target sector info for more precise region
      target_row: targetSector.row,
      target_col: targetSector.col,
      grid_size: GRID_SIZE
    })
  });
  
  if (!response.ok) throw new Error(await response.text());
  
  const promptUsed = response.headers.get("X-Prompt-Used");
  if (promptUsed) console.log("Prompt:", promptUsed);
  
  const blob = await response.blob();
  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = URL.createObjectURL(blob);
  });
  
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);
  
  pendingSwap = {
    image: img,
    base64: canvas.toDataURL("image/png"),
    targetSector: targetSector,
    focusSector: focusSector
  };
  
  console.log("Generated image ready, waiting for safe blink...");
  return pendingSwap;
}

// ============================================
// GENERATION
// ============================================

async function loadDefaultBaseImage() {
  try {
    console.log("Loading default base image:", DEFAULT_BASE_IMAGE);
    const img = new Image();
    img.crossOrigin = "anonymous";
    
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = reject;
      img.src = DEFAULT_BASE_IMAGE;
    });
    
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d").drawImage(img, 0, 0);
    capturedImageBase64 = canvas.toDataURL("image/png");
    
    activePatch = { image: img };
    scheduleRender();
    console.log("Default base image loaded");
  } catch (err) {
    console.error("Failed to load default base image:", err);
  }
}

async function generateToPending(focusX, focusY, modifiedRegion) {
  if (!capturedImageBase64) return null;
  
  console.log(`Generating with focus at (${focusX.toFixed(2)}, ${focusY.toFixed(2)})`);
  
  const response = await fetch(`${GENERATION_API}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      image_base64: capturedImageBase64,
      focus_x: focusX,
      focus_y: focusY
    })
  });
  
  if (!response.ok) throw new Error(await response.text());
  
  const promptUsed = response.headers.get("X-Prompt-Used");
  if (promptUsed) console.log("Prompt:", promptUsed);
  
  const blob = await response.blob();
  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = URL.createObjectURL(blob);
  });
  
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);
  
  pendingSwap = {
    image: img,
    base64: canvas.toDataURL("image/png"),
    modifiedRegion
  };
  
  console.log("Generated image ready, waiting for safe blink...");
  return pendingSwap;
}

// ============================================
// BLINK HANDLING (SECTOR-BASED)
// ============================================

function isSafeToSwap() {
  if (!pendingSwap?.targetSector) return false;
  
  // Get current sector from smoothed gaze
  const currentGazeSector = gazeToSector(smoothedGaze);
  
  // Safe to swap if user is NOT in the target (modified) sector
  const inTargetSector = currentGazeSector.row === pendingSwap.targetSector.row && 
                         currentGazeSector.col === pendingSwap.targetSector.col;
  
  return !inTargetSector;
}

function attemptSwapOnBlink() {
  if (!pendingSwap) {
    console.log("No pending swap to apply");
    return false;
  }
  
  if (isSafeToSwap()) {
    const targetName = sectorName(pendingSwap.targetSector);
    activePatch = { image: pendingSwap.image };
    capturedImageBase64 = pendingSwap.base64;
    
    // Save to see what changed (optional - opens in new tab for inspection)
    // window.open(pendingSwap.base64, '_blank');
    
    pendingSwap = null;
    fixatedSector = null;  // Reset so we can generate again
    scheduleRender();
    console.log(`✓ Image swapped! Modified sector: ${targetName}`);
    blinkStatus.textContent = `swapped ${targetName}!`;
    return true;
  }
  
  const currentGazeSector = gazeToSector(smoothedGaze);
  console.log(`✗ Swap blocked - looking at ${sectorName(currentGazeSector)}, modified ${sectorName(pendingSwap.targetSector)}`);
  return false;
}

function handleBlink(state) {
  if (lastBlinkState !== "closed" && state === "closed") {
    if (pendingSwap) {
      const swapped = attemptSwapOnBlink();
      blinkStatus.textContent = swapped ? "swapped!" : "blocked";
    } else {
      blinkStatus.textContent = "no pending";
    }
  } else if (state === "open") {
    blinkStatus.textContent = pendingSwap ? "ready (pending)" : "ready";
  }
  lastBlinkState = state;
}

// ============================================
// WEBSOCKET
// ============================================

function connectWebSocket() {
  const socket = new WebSocket(WS_URL);
  wsStatus.textContent = "connecting";

  socket.addEventListener("open", () => {
    wsStatus.textContent = "connected";
    setInterval(() => socket.readyState === 1 && socket.send("ping"), 10000);
  });

  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.event === "sample" && data.gaze?.valid) {
      updateGazeCursor(data.gaze);
    } else if (data.event === "blink" && data.state) {
      handleBlink(data.state);
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

// ============================================
// INIT
// ============================================

window.addEventListener("resize", resizeCanvas);
window.addEventListener("load", () => {
  resizeCanvas();
  connectWebSocket();
  loadDefaultBaseImage();

  // Set initial debug mode visibility
  const sidebar = document.querySelector('.sidebar');
  if (sidebar && !debugMode) {
    sidebar.style.display = 'none';
  }
  if (viewportGazeCursor && !debugMode) {
    viewportGazeCursor.style.display = 'none';
  }

  console.log(`Debug mode: ${debugMode ? 'ON' : 'OFF'} (press 'D' to toggle, or add ?debug=true to URL)`);
});

// Toggle debug mode with 'D' key
window.addEventListener("keydown", (event) => {
  if (event.key === 'd' || event.key === 'D') {
    // Ignore if user is typing in an input field
    if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') return;
    toggleDebugMode();
  }
});
