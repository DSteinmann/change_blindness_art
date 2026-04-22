export const API_ROOT = window.API_ROOT || "http://localhost:8000";
export const WS_URL = API_ROOT.replace("http", "ws") + "/ws/stream";

const FALLBACK = {
  grid_size: 3,
  fixation_duration_ms: 1000,
  gaze_smoothing_factor: 0.08,
  gaze_stale_ms: 250,
  generation_api: window.GENERATION_API || "http://localhost:8001",
  surface_name: "screen",
};

export async function loadRuntimeConfig() {
  try {
    const res = await fetch(`${API_ROOT}/config`);
    if (!res.ok) throw new Error(`status ${res.status}`);
    return { ...FALLBACK, ...(await res.json()) };
  } catch (err) {
    console.warn("Failed to load /config, using fallback:", err);
    return { ...FALLBACK };
  }
}
