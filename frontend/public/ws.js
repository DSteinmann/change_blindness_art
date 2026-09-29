import { WS_URL } from "./config.js";

const MAX_RETRY_MS = 10000;

export function openStream(handlers, retryMs = 1000) {
  const socket = new WebSocket(WS_URL);
  let pingInterval = null;
  socket.addEventListener("open", () => {
    retryMs = 1000;
    pingInterval = setInterval(() => socket.readyState === 1 && socket.send("ping"), 10000);
  });
  socket.addEventListener("message", (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch { return; }
    const fn = handlers[data.event];
    if (fn) fn(data);
  });
  socket.addEventListener("close", () => {
    if (pingInterval !== null) clearInterval(pingInterval);
    setTimeout(() => openStream(handlers, Math.min(retryMs * 2, MAX_RETRY_MS)), retryMs);
  });
  socket.addEventListener("error", () => socket.close());
}
