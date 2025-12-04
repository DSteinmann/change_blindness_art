const API_ROOT = window.API_ROOT || "http://localhost:8000";
const WS_URL = API_ROOT.replace("http", "ws") + "/ws/stream";

const wsStatus = document.getElementById("ws-status");
const blinkStatus = document.getElementById("blink-status");
const patchStatus = document.getElementById("patch-status");
const gazeStatus = document.getElementById("gaze-status");
const baseImage = document.getElementById("base-image");
const gazeCursor = document.getElementById("gaze-cursor");
const patchImage = document.getElementById("peripheral-patch");

let lastBlinkState = "open";
let latestGaze = { x_norm: 0.5, y_norm: 0.5 };
let preloadedPatch = null;

async function fetchNextPatch() {
  try {
    const response = await fetch(`${API_ROOT}/patch/next`);
    preloadedPatch = await response.json();
  } catch (err) {
    console.error("patch fetch failed", err);
  }
}

function updateGazeCursor(gaze) {
  latestGaze = gaze;
  const bounds = baseImage.getBoundingClientRect();
  const x = bounds.left + gaze.x_norm * bounds.width;
  const y = bounds.top + gaze.y_norm * bounds.height;
  gazeCursor.style.left = `${x}px`;
  gazeCursor.style.top = `${y}px`;
  gazeStatus.textContent = `${gaze.x_norm.toFixed(2)}, ${gaze.y_norm.toFixed(2)}`;
}

function positionPatch(assetUrl) {
  patchImage.src = assetUrl;
  const bounds = baseImage.getBoundingClientRect();
  const horizontalSide = latestGaze.x_norm < 0.5 ? bounds.right - 220 : bounds.left + 20;
  const verticalSide = latestGaze.y_norm < 0.5 ? bounds.bottom - 220 : bounds.top + 20;
  patchImage.style.left = `${horizontalSide}px`;
  patchImage.style.top = `${verticalSide}px`;
  patchImage.classList.remove("hidden");
  patchStatus.textContent = assetUrl;
}

async function applyPatchIfNeeded(blinkState) {
  if (lastBlinkState !== "closed" && blinkState === "closed") {
    if (!preloadedPatch) {
      await fetchNextPatch();
    }
    if (preloadedPatch) {
      positionPatch(`${API_ROOT}${preloadedPatch.url}`);
      await fetch(`${API_ROOT}/patch/use`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patch: preloadedPatch, gaze: latestGaze }),
      });
      preloadedPatch = null;
      void fetchNextPatch();
    }
    blinkStatus.textContent = "blink";
  } else if (blinkState === "open") {
    patchImage.classList.add("hidden");
    blinkStatus.textContent = "open";
  }
  lastBlinkState = blinkState;
}

function connectWebSocket() {
  const socket = new WebSocket(WS_URL);
  wsStatus.textContent = "connecting";

  socket.addEventListener("open", () => {
    wsStatus.textContent = "connected";
    fetchNextPatch();
    setInterval(() => socket.readyState === 1 && socket.send("ping"), 10000);
  });

  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.event === "sample") {
      if (data.gaze && data.gaze.valid) {
        updateGazeCursor(data.gaze);
      }
      if (data.blink) {
        void applyPatchIfNeeded(data.blink.state);
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

window.addEventListener("load", () => {
  connectWebSocket();
});
