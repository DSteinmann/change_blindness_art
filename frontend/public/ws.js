import { WS_URL } from "./config.js";

export function openStream(handlers) {
  const socket = new WebSocket(WS_URL);
  let pingInterval = null;
  socket.addEventListener("open", () => {
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
    setTimeout(() => openStream(handlers), 1000);
  });
  socket.addEventListener("error", () => socket.close());
}
