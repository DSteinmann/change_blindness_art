import { API_ROOT } from "./config.js";
import { openStream } from "./ws.js";

const stage = document.getElementById("stage");
const waiting = document.getElementById("waiting");
const sectorEl = document.getElementById("sector");
const promptEl = document.getElementById("prompt");

let currentImg = null;

function clearStage() {
  if (currentImg) {
    currentImg.remove();
    currentImg = null;
  }
  waiting.style.display = "grid";
  sectorEl.textContent = "";
  promptEl.textContent = "";
}

function showGeneration({ session_id, filename, target_sector, prompt, caption }) {
  if (!session_id || !filename) return;
  const img = new Image();
  img.src = `${API_ROOT}/sessions/${session_id}/${filename}`;
  img.onload = () => {
    waiting.style.display = "none";
    if (currentImg) currentImg.classList.add("prev");
    stage.appendChild(img);
    requestAnimationFrame(() => {
      if (currentImg) currentImg.remove();
      currentImg = img;
    });
  };
  sectorEl.textContent = target_sector || "";
  promptEl.textContent = caption || prompt || "";
}

openStream({
  generation: showGeneration,
  session_started: clearStage,
});
