import { sectorName, sectorToNormCenter, getOppositeSector, gazeToSector } from "./sectors.js";
import { setActivePatch } from "./rendering.js";

export class GenerationController {
  constructor(config, gazeStream, fixationTracker) {
    this.config = config;
    this.gaze = gazeStream;
    this.fixation = fixationTracker;
    this.capturedImageBase64 = null;
    this.pendingSwap = null;
    this.isGenerating = false;
    this.lastBlinkState = "open";

    fixationTracker.addEventListener("fixation", (e) => this.#onFixation(e.detail.sector));
  }

  setBaseImage(image, base64) {
    this.capturedImageBase64 = base64;
    setActivePatch({ image });
  }

  handleBlink(state) {
    if (this.lastBlinkState !== "closed" && state === "closed" && this.pendingSwap) {
      this.#attemptSwap();
    }
    this.lastBlinkState = state;
  }

  async #onFixation(focusSector) {
    if (this.isGenerating || this.pendingSwap) return;
    if (!this.capturedImageBase64) return;

    this.isGenerating = true;
    const targetSector = getOppositeSector(focusSector, this.config.grid_size);
    console.log(`Fixation ${sectorName(focusSector)} → modify ${sectorName(targetSector)}`);
    try {
      await this.#generate(focusSector, targetSector);
    } catch (err) {
      console.error("Generation error:", err);
    }
    setTimeout(() => { this.isGenerating = false; }, 1000);
  }

  async #generate(focusSector, targetSector) {
    const focusCenter = sectorToNormCenter(focusSector, this.config.grid_size);
    const response = await fetch(`${this.config.generation_api}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_base64: this.capturedImageBase64,
        focus_x: focusCenter.x_norm,
        focus_y: focusCenter.y_norm,
        target_row: targetSector.row,
        target_col: targetSector.col,
        grid_size: this.config.grid_size,
      }),
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

    this.pendingSwap = {
      image: img,
      base64: canvas.toDataURL("image/png"),
      targetSector,
      focusSector,
    };
    console.log("Generated image ready, waiting for safe blink...");
  }

  #attemptSwap() {
    const targetSector = this.pendingSwap.targetSector;
    const currentGazeSector = gazeToSector(this.gaze.smoothed, this.config.grid_size);
    const inTargetSector = currentGazeSector.row === targetSector.row && currentGazeSector.col === targetSector.col;

    if (inTargetSector) {
      console.log(`✗ Swap blocked - looking at ${sectorName(currentGazeSector)}`);
      return;
    }

    setActivePatch({ image: this.pendingSwap.image });
    this.capturedImageBase64 = this.pendingSwap.base64;
    console.log(`✓ Image swapped! Modified sector: ${sectorName(targetSector)}`);
    this.pendingSwap = null;
    this.fixation.reset();
  }
}
