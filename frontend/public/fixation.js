import { gazeToSector, sectorName } from "./sectors.js";

/**
 * Tracks which sector the user is dwelling on and emits:
 *   - "sectorchange" — whenever the gaze moves to a different sector
 *   - "fixation"     — when dwell time on a sector exceeds config.fixation_duration_ms
 *
 * Call update(gaze, smoothed, isStale) on every gaze sample. Call reset() when
 * generation starts (so the same sector has to be re-entered to retrigger) and
 * clear() when gaze is lost/stale.
 */
export class FixationTracker extends EventTarget {
  constructor(config) {
    super();
    this.config = config;
    this.current = { row: Math.floor(config.grid_size / 2), col: Math.floor(config.grid_size / 2) };
    this.fixationStart = null;
    this.fixatedSector = null;
  }

  clear() {
    this.fixationStart = null;
    this.fixatedSector = null;
  }

  reset() {
    this.fixatedSector = null;
  }

  get lastFixated() {
    return this.fixatedSector;
  }

  update(gaze, smoothed, isStale) {
    if (!gaze?.valid || isStale) {
      this.clear();
      return;
    }

    const sector = gazeToSector(smoothed, this.config.grid_size);
    const changed = sector.row !== this.current.row || sector.col !== this.current.col;

    if (changed) {
      this.current = sector;
      this.fixationStart = Date.now();
      this.fixatedSector = null;
      this.dispatchEvent(new CustomEvent("sectorchange", { detail: { sector } }));
      console.log(`Sector changed to ${sectorName(sector)}`);
      return;
    }

    if (this.fixationStart === null) {
      this.fixationStart = Date.now();
      return;
    }

    const elapsed = Date.now() - this.fixationStart;
    if (elapsed >= this.config.fixation_duration_ms && !this.fixatedSector) {
      this.fixatedSector = sector;
      this.dispatchEvent(new CustomEvent("fixation", { detail: { sector } }));
    }
  }
}
