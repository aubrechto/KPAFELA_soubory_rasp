// App bootstrap: navigation, now-playing bar, and realtime wiring.
import { api, store, subscribe, connectSocket, applySnapshot, fmtTime, coverUrl } from "./api.js";
import { initPlayer } from "./player.js";
import { initSettings } from "./settings.js";
import { initPreferences } from "./preferences.js";
import { initTerminal } from "./terminal.js";

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>',
  prev: '<svg viewBox="0 0 24 24"><path d="M6 6h2v12H6zm3.5 6L18 6v12z"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M16 6h2v12h-2zM6 6l8.5 6L6 18z"/></svg>',
};

function initNav() {
  const items = document.querySelectorAll(".nav-item");
  items.forEach((item) => {
    item.addEventListener("click", () => {
      const target = item.dataset.view;
      items.forEach((i) => i.classList.toggle("is-active", i === item));
      document.querySelectorAll(".view").forEach((v) => {
        v.classList.toggle("is-active", v.id === `view-${target}`);
      });
    });
  });
}

function initNowbar() {
  const bar = document.getElementById("nowbar");
  bar.innerHTML = `
    <div class="now-track">
      <img class="now-cover" id="now-cover" alt="" crossorigin="anonymous" />
      <div class="now-meta">
        <div class="now-title" id="now-title">&mdash;</div>
        <div class="now-artist" id="now-artist"></div>
      </div>
    </div>
    <div class="now-center">
      <div class="now-controls">
        <button class="now-btn" data-cmd="prev" aria-label="Previous">${ICON.prev}</button>
        <button class="now-btn play" data-cmd="toggle" aria-label="Play or pause" id="now-toggle">${ICON.play}</button>
        <button class="now-btn" data-cmd="next" aria-label="Next">${ICON.next}</button>
      </div>
      <div class="now-progress">
        <span id="now-elapsed">0:00</span>
        <div class="progress-track"><div class="progress-fill" id="now-fill"></div></div>
        <span id="now-total">0:00</span>
      </div>
    </div>
    <div class="now-right">
      <div class="now-instruments" id="now-instruments"></div>
    </div>
  `;

  bar.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-cmd]");
    if (!btn) return;
    const cmd = btn.dataset.cmd;
    if (cmd === "toggle") {
      api.player(store.player.status === "playing" ? "pause" : "play");
    } else {
      api.player(cmd);
    }
  });
}

function renderNowbar() {
  const { player, instruments } = store;
  const cur = player.current;
  const el = (id) => document.getElementById(id);

  el("now-cover").src = coverUrl(cur?.cover);
  el("now-title").textContent = cur ? cur.title : "Nothing playing";
  el("now-artist").textContent = cur ? cur.artist : "";
  el("now-toggle").innerHTML = player.status === "playing" ? ICON.pause : ICON.play;

  const total = cur ? cur.duration : 0;
  const pct = total ? Math.min(100, (player.position / total) * 100) : 0;
  el("now-fill").style.width = `${pct}%`;
  el("now-elapsed").textContent = fmtTime(player.position);
  el("now-total").textContent = fmtTime(total);

  el("now-instruments").innerHTML = Object.entries(instruments)
    .map(
      ([name, status]) =>
        `<span class="chip ${status === "playing" ? "playing" : ""}">${name}</span>`
    )
    .join("");
}

function renderMqtt() {
  const wrap = document.getElementById("mqtt-status");
  const label = document.getElementById("mqtt-label");
  const { connected, simulation, host, port } = store.mqtt;
  wrap.classList.remove("is-live", "is-sim");
  const origin = host && port ? ` (${host}:${port})` : "";
  if (connected) {
    wrap.classList.add("is-live");
    label.textContent = `MQTT connected${origin}`;
  } else if (simulation) {
    wrap.classList.add("is-sim");
    label.textContent = `Simulation mode${origin}`;
  } else {
    label.textContent = `MQTT offline${origin}`;
  }
}

async function main() {
  initNav();
  initNowbar();
  initPlayer();
  await initTerminal();
  await Promise.all([initSettings(), initPreferences()]);

  subscribe(() => {
    renderNowbar();
    renderMqtt();
  });

  // Seed state via REST, then keep it live over WebSocket.
  try {
    applySnapshot(await api.getState());
  } catch (err) {
    console.log("[v0] initial state fetch failed", err);
  }
  connectSocket();
}

main();
