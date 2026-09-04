// Shared API + realtime store for the KAP{F}ELA dashboard.

const listeners = new Set();

export const store = {
  player: { status: "stopped", position: 0, index: 0, current: null },
  queue: [],
  library: [],
  instruments: { guitar: "idle", bass: "idle", drums: "idle" },
  instrumentTimes: {},
  serverTime: 0,
  serverTimeReceivedAt: 0,
  mqtt: { connected: false, simulation: false, host: "", port: 0 },
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) fn(store);
}

// Merge an incoming WebSocket/REST snapshot into the local store.
export function applySnapshot(snap) {
  if (snap.player) store.player = snap.player;
  if (snap.queue) store.queue = snap.queue;
  if (snap.library) store.library = snap.library;
  if (snap.instruments) store.instruments = snap.instruments;
  if (snap.instrument_times) store.instrumentTimes = snap.instrument_times;
  if (snap.server_time) {
    store.serverTime = snap.server_time;
    store.serverTimeReceivedAt = Date.now();
  }
  if (snap.mqtt) store.mqtt = { ...store.mqtt, ...snap.mqtt };
  emit();
}

// --- REST helpers ---------------------------------------------------------
async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${method} ${url} -> ${res.status}`);
  return res.json();
}

export const api = {
  getState: () => request("GET", "/api/state"),
  getSettings: () => request("GET", "/api/settings"),
  saveSettings: (data) => request("PUT", "/api/settings", data),
  getInstruments: () => request("GET", "/api/instruments"),
  saveInstruments: (data) => request("PUT", "/api/instruments", data),
  testInstruments: () => request("POST", "/api/instruments/test"),
  player: (command, body) => request("POST", `/api/player/${command}`, body ?? {}),
  queueSong: (id) => request("POST", "/api/player/queue", { id }),
  playSong: (id) => request("POST", "/api/player/play-song", { id }),
  instrument: (name, command) =>
    request("POST", `/api/instrument/${name}/${command}`),
};

// --- WebSocket ------------------------------------------------------------
export function connectSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws`;

  function open() {
    const ws = new WebSocket(url);
    ws.onmessage = (evt) => {
      try {
        applySnapshot(JSON.parse(evt.data));
      } catch (err) {
        console.log("[v0] bad ws message", err);
      }
    };
    ws.onclose = () => setTimeout(open, 1500); // auto-reconnect
    ws.onerror = () => ws.close();
  }
  open();
}

// --- formatting -----------------------------------------------------------
export function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtClock(value) {
  if (typeof value === "string" && /^\d{1,2}:\d{2}(:\d{2})?$/.test(value)) {
    return value;
  }
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function coverUrl(cover) {
  return cover ? `/static/images/${cover}` : "/static/images/placeholder.png";
}
