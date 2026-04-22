const canvasWrapper = document.querySelector(".canvas-wrapper");
const sceneCanvas = document.getElementById("scene-canvas");
const sceneCtx = sceneCanvas.getContext("2d");

let canvasMetrics = { width: 0, height: 0, dpr: 1 };
let renderScheduled = false;
let activePatch = null;

export function setActivePatch(patch) {
  activePatch = patch;
  scheduleRender();
}

export function scheduleRender() {
  if (renderScheduled) return;
  renderScheduled = true;
  requestAnimationFrame(() => {
    renderScene();
    renderScheduled = false;
  });
}

export function resizeCanvas() {
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
