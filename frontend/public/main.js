import { API_ROOT, loadRuntimeConfig } from "./config.js";
import { resizeCanvas } from "./rendering.js";
import { GazeStream } from "./gaze.js";
import { FixationTracker } from "./fixation.js";
import { GenerationController } from "./generation.js";
import { openStream } from "./ws.js";

const DEFAULT_BASE_IMAGE = `${API_ROOT}/assets/generated/pexels-tbd-traveller-2149583744-30732757.jpg`;

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

  openStream({
    sample: (data) => data.gaze && gaze.ingest(data.gaze),
    blink: (data) => data.state && controller.handleBlink(data.state),
    session_started: async (data) => {
      console.log("session_started → reloading default base image", data?.session_id);
      controller.setSessionId(data?.session_id);
      controller.resetForNewSession();
      await loadDefaultBaseImage(controller);
    },
  });
  // Fetch the active session id once on load so the first /generate request
  // already carries a session_id and stale-tab writes can be rejected from
  // the very first call.
  try {
    const r = await fetch(`${config.generation_api}/session/current`);
    if (r.ok) {
      const j = await r.json();
      controller.setSessionId(j.session_id);
    }
  } catch (err) {
    console.warn("Could not fetch current session id:", err);
  }
  await loadDefaultBaseImage(controller);

  document.addEventListener("keydown", (event) => {
    if (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA") return;
    if (event.key === "d" || event.key === "D") gaze.setDebugMode(!gaze.debugMode);
  });

  console.log(`Debug mode: ${initialDebug ? "ON" : "OFF"} (press 'D' to toggle)`);
}

window.addEventListener("load", main);
