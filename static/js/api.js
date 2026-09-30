// Shared API + realtime store for the KAP{F}ELA dashboard.

const listeners = new Set();
const instrumentNames = ["guitar", "bass", "drums"];
const commandVersions = {};
let nextCommandVersion = 0;

export const store = {
  player: { status: "stopped", position: 0, index: 0, current: null },
  queue: [],
  library: [],
  instruments: { guitar: "idle", bass: "idle", drums: "idle" },
  instrumentCommandState: { guitar: "ready", bass: "ready", drums: "ready" },
  instrumentTimes: {},
  instrumentWifi: {},
  timeSync: { skew: {}, max_skew: 0, in_sync: false },
  serverTime: 0,
  serverTimeReceivedAt: 0,
  mqtt: { connected: false, simulation: false, host: "", port: 0 },
  instrumentsConfig: null,
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) fn(store);
}

function trackCommand(names, send) {
  const version = ++nextCommandVersion;
  for (const name of names) {
    commandVersions[name] = version;
    store.instrumentCommandState[name] = "pending";
  }
  emit();

  const settle = (ack) => {
    const acknowledgements = ack?.acknowledged || {};
    for (const name of names) {
      if (commandVersions[name] !== version) continue;
      store.instrumentCommandState[name] =
        acknowledgements[name] === true ? "ready" : "error";
    }
    emit();
  };

  return send().then((response) => {
    settle(response.command_ack || response.ack);
    return response;
  }).catch((error) => {
    settle(error.payload?.ack);
    throw error;
  });
}

// Merge an incoming WebSocket/REST snapshot into the local store.
export function applySnapshot(snap) {
  if (snap.player) store.player = snap.player;
  if (snap.queue) store.queue = snap.queue;
  if (snap.library) store.library = snap.library;
  if (snap.instruments) store.instruments = snap.instruments;
  if (snap.instrument_times) store.instrumentTimes = snap.instrument_times;
  if (snap.instrument_wifi) store.instrumentWifi = snap.instrument_wifi;
  if (snap.time_sync) store.timeSync = snap.time_sync;
  if (snap.server_time) {
    store.serverTime = snap.server_time;
    store.serverTimeReceivedAt = Date.now();
  }
  if (snap.instruments_config) store.instrumentsConfig = snap.instruments_config;
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
  const payload = await res.json();
  if (!res.ok) {
    const error = new Error(`${method} ${url} -> ${res.status}`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

export const api = {
  getState: () => request("GET", "/api/state"),
  getSettings: () => request("GET", "/api/settings"),
  saveSettings: (data) => request("PUT", "/api/settings", data),
  getInstruments: () => request("GET", "/api/instruments"),
  saveInstruments: (data) => request("PUT", "/api/instruments", data),
  testInstruments: () => trackCommand(
    instrumentNames, () => request("POST", "/api/instruments/test")
  ),
  syncTime: (time) => request("POST", "/api/time/sync", { time }),
  player: (command, body) => {
    const send = () => request("POST", `/api/player/${command}`, body ?? {});
    return command === "queue" ? send() : trackCommand(instrumentNames, send);
  },
  queueSong: (id) => request("POST", "/api/player/queue", { id }),
  playSong: (id) => trackCommand(
    instrumentNames, () => request("POST", "/api/player/play-song", { id })
  ),
  instrument: (name, command) =>
    trackCommand([name], () => request("POST", `/api/instrument/${name}/${command}`)),
};

// --- WebSocket ------------------------------------------------------------
export function connectSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws`;

  function open() {
    const ws = new WebSocket(url);
    ws.onopen = () => {
      // Offer the browser's clock to the Pi (used when the Pi is offline).
      api.syncTime(Date.now() / 1000).catch(() => {});
    };
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
