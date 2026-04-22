const viewportGazeCursor = document.getElementById("viewport-gaze-cursor");

export class GazeStream extends EventTarget {
  constructor(config) {
    super();
    this.config = config;
    this.latestRaw = { x_norm: 0.5, y_norm: 0.5, valid: false };
    this.smoothed = { x_norm: 0.5, y_norm: 0.5 };
    this.lastValidTs = 0;
    this.debugMode = false;
  }

  setDebugMode(on) {
    this.debugMode = on;
    if (!viewportGazeCursor) return;
    if (on) {
      viewportGazeCursor.style.left = `${this.smoothed.x_norm * window.innerWidth}px`;
      viewportGazeCursor.style.top = `${this.smoothed.y_norm * window.innerHeight}px`;
      viewportGazeCursor.style.display = "block";
    } else {
      viewportGazeCursor.style.display = "none";
    }
  }

  isStale() {
    return Date.now() - this.lastValidTs > this.config.gaze_stale_ms;
  }

  ingest(gaze) {
    this.latestRaw = gaze;
    if (gaze?.valid) {
      const a = this.config.gaze_smoothing_factor;
      this.smoothed.x_norm += a * (gaze.x_norm - this.smoothed.x_norm);
      this.smoothed.y_norm += a * (gaze.y_norm - this.smoothed.y_norm);
      this.lastValidTs = Date.now();
    }
    this.#updateCursor(gaze);
    this.dispatchEvent(new CustomEvent("sample", { detail: { gaze, smoothed: this.smoothed } }));
  }

  #updateCursor(gaze) {
    if (!viewportGazeCursor) return;
    if (!this.debugMode || !gaze?.valid) {
      viewportGazeCursor.style.display = "none";
      return;
    }
    viewportGazeCursor.style.left = `${this.smoothed.x_norm * window.innerWidth}px`;
    viewportGazeCursor.style.top = `${this.smoothed.y_norm * window.innerHeight}px`;
    viewportGazeCursor.style.display = "block";
  }
}
