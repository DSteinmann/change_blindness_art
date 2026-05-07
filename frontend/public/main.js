import { API_ROOT, WS_URL, loadRuntimeConfig } from "./config.js";
import { resizeCanvas } from "./rendering.js";
import { GazeStream } from "./gaze.js";
import { FixationTracker } from "./fixation.js";
import { GenerationController } from "./generation.js";

const DEFAULT_BASE_IMAGE = `${API_ROOT}/assets/generated/pexels-triemli-28578413.jpg`;

async function loadDefaultBaseImage(controller) {
  try {
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
    controller.setBaseImage(img, canvas.toDataURL("image/png"));
    console.log("Default base image loaded");
  } catch (err) {
    console.error("Failed to load default base image:", err);
  }
}

function connectWebSocket(onSample, onBlink) {
  const socket = new WebSocket(WS_URL);
  let pingInterval = null;
  socket.addEventListener("open", () => {
    console.log("WebSocket connected");
    pingInterval = setInterval(() => socket.readyState === 1 && socket.send("ping"), 10000);
  });
  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.event === "sample" && data.gaze) onSample(data.gaze);
    else if (data.event === "blink" && data.state) onBlink(data.state);
  });
  socket.addEventListener("close", () => {
    if (pingInterval !== null) clearInterval(pingInterval);
    console.log("WebSocket disconnected, reconnecting...");
    setTimeout(() => connectWebSocket(onSample, onBlink), 1000);
  });
  socket.addEventListener("error", () => socket.close());
}

async function main() {
  const config = await loadRuntimeConfig();
  const gaze = new GazeStream(config);
  const fixation = new FixationTracker(config);
  const controller = new GenerationController(config, gaze, fixation);

  const initialDebug = new URLSearchParams(window.location.search).get("debug") === "true";
  gaze.setDebugMode(initialDebug);

  gaze.addEventListener("sample", (e) => {
    fixation.update(e.detail.gaze, e.detail.smoothed, gaze.isStale());
  });

  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();

  connectWebSocket(
    (g) => gaze.ingest(g),
    (state) => controller.handleBlink(state),
  );
  await loadDefaultBaseImage(controller);

  document.addEventListener("keydown", (event) => {
    if (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA") return;
    if (event.key === "d" || event.key === "D") gaze.setDebugMode(!gaze.debugMode);
  });

  console.log(`Debug mode: ${initialDebug ? "ON" : "OFF"} (press 'D' to toggle)`);
}

window.addEventListener("load", main);
