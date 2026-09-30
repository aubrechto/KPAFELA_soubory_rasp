// Player view: Spotify-style hero, transport, instrument cards, and queue.
import { api, store, subscribe, fmtTime, fmtClock, coverUrl } from "./api.js?v=1.6";

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg>',
  prev: '<svg viewBox="0 0 24 24"><path d="M6 6h2v12H6zm3.5 6L18 6v12z"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M16 6h2v12h-2zM6 6l8.5 6L6 18z"/></svg>',
};

const INSTRUMENTS = ["guitar", "bass", "drums"];

export function initPlayer() {
  const view = document.getElementById("view-player");
  view.innerHTML = `
    <div class="page-heading">
      <div>
        <h1 class="page-title">Now Playing</h1>
        <p class="page-sub">Control the KAP{F}ELA robotic band in real time.</p>
      </div>
      <div class="raspberry-clock"><span>Raspberry Pi</span><strong id="raspberry-time">--:--:--</strong></div>
    </div>

    <div class="player-hero">
      <img class="hero-cover" id="hero-cover" alt="Album cover" crossorigin="anonymous" />
      <div class="hero-info">
        <span class="hero-label">Song</span>
        <span class="hero-title" id="hero-title">&mdash;</span>
        <span class="hero-artist" id="hero-artist"></span>
      </div>
    </div>

    <div class="transport">
      <button class="control-btn" data-cmd="prev" title="Previous" aria-label="Previous">${ICON.prev}</button>
      <button class="control-btn primary" data-cmd="toggle" title="Play/Pause" aria-label="Play or pause" id="hero-toggle">${ICON.play}</button>
      <button class="control-btn" data-cmd="stop" title="Stop" aria-label="Stop">${ICON.stop}</button>
      <button class="control-btn" data-cmd="next" title="Next" aria-label="Next">${ICON.next}</button>
      <div class="progress-wrap">
        <span id="hero-elapsed">0:00</span>
        <div class="progress-track" id="hero-track"><div class="progress-fill" id="hero-fill"></div></div>
        <span id="hero-total">0:00</span>
      </div>
    </div>

    <h2 class="section-title">Instruments</h2>
    <div class="instrument-grid" id="instrument-grid"></div>

    <h2 class="section-title">Queue</h2>
    <div class="queue" id="queue"></div>
  `;

  // Build instrument cards once.
  const grid = view.querySelector("#instrument-grid");
  grid.innerHTML = INSTRUMENTS.map(
    (name) => `
    <div class="instrument-card" data-instrument="${name}">
      <img class="instrument-img" src="${coverUrl(name + ".png")}" alt="${name}" crossorigin="anonymous" />
      <div class="instrument-head">
        <span class="instrument-name">${name}</span>
        <span class="command-indicator is-live" data-command-indicator aria-label="Command ACK potvrzen" title="Command ACK potvrzen"><span class="dot"></span></span>
        <span class="status-pill idle" data-status>IDLE</span>
      </div>
      <div class="instrument-time" data-instrument-time></div>
      <div class="instrument-wifi" data-instrument-wifi>Wi-Fi: čekám na data</div>
      <div class="instrument-actions">
        <button class="btn btn-play" data-icmd="play">Play</button>
        <button class="btn btn-stop" data-icmd="stop">Stop</button>
        <button class="btn btn-ghost" data-icmd="reboot">Reboot</button>
      </div>
    </div>`
  ).join("");

  // Transport events
  view.querySelector(".transport").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-cmd]");
    if (!btn) return;
    const cmd = btn.dataset.cmd;
    if (cmd === "toggle") {
      void api.player(store.player.status === "playing" ? "pause" : "play").catch(() => {});
    } else {
      void api.player(cmd).catch(() => {});
    }
  });

  // Seek by clicking progress bar
  view.querySelector("#hero-track").addEventListener("click", (e) => {
    const cur = store.player.current;
    if (!cur) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    void api.player("seek", { position: ratio * cur.duration }).catch(() => {});
  });

  // Instrument events
  grid.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-icmd]");
    if (!btn) return;
    const name = btn.closest("[data-instrument]").dataset.instrument;
    void api.instrument(name, btn.dataset.icmd).catch(() => {});
  });

  // Queue events (delegated; rows built on first render)
  view.querySelector("#queue").addEventListener("click", (e) => {
    const row = e.target.closest("[data-index]");
    if (row) void api.player("select", { index: Number(row.dataset.index) }).catch(() => {});
  });

  subscribe(render);
  render();
  setInterval(render, 1000);
}

let queueBuilt = false;

function render() {
  const view = document.getElementById("view-player");
  if (!view) return;
  const { player, queue, instruments, instrumentTimes, instrumentWifi } = store;
  const cur = player.current;

  const raspberryNow = store.serverTime
    ? store.serverTime + (Date.now() - store.serverTimeReceivedAt) / 1000
    : Date.now() / 1000;
  view.querySelector("#raspberry-time").textContent = fmtClock(raspberryNow);

  // Hero
  view.querySelector("#hero-cover").src = coverUrl(cur?.cover);
  view.querySelector("#hero-title").textContent = cur ? cur.title : "No song loaded";
  view.querySelector("#hero-artist").textContent = cur ? cur.artist : "";
  view.querySelector("#hero-toggle").innerHTML =
    player.status === "playing" ? ICON.pause : ICON.play;

  const total = cur ? cur.duration : 0;
  const pct = total ? Math.min(100, (player.position / total) * 100) : 0;
  view.querySelector("#hero-fill").style.width = `${pct}%`;
  view.querySelector("#hero-elapsed").textContent = fmtTime(player.position);
  view.querySelector("#hero-total").textContent = fmtTime(total);

  // Instruments
  const skews = store.timeSync?.skew || {};
  view.querySelectorAll("[data-instrument]").forEach((card) => {
    const status = instruments[card.dataset.instrument] || "idle";
    const pill = card.querySelector("[data-status]");
    pill.textContent = status.toUpperCase();
    pill.className = `status-pill ${status}`;
    const commandIndicator = card.querySelector("[data-command-indicator]");
    const commandState = store.instrumentCommandState[card.dataset.instrument] || "ready";
    const commandReady = commandState === "ready";
    const commandFailed = commandState === "error";
    commandIndicator.classList.toggle("is-live", commandReady);
    commandIndicator.classList.toggle("is-error", commandFailed);
    commandIndicator.setAttribute(
      "aria-label",
      commandReady ? "ESP potvrdilo příkaz" :
        commandState === "pending" ? "Čekám na ACK od ESP" : "ESP příkaz nepotvrdilo"
    );
    commandIndicator.title = commandReady ? "ESP potvrdilo příkaz" :
      commandState === "pending" ? "Čekám na ACK od ESP" : "ESP příkaz nepotvrdilo";
    const timeEl = card.querySelector("[data-instrument-time]");
    const wifiEl = card.querySelector("[data-instrument-wifi]");
    const wifi = instrumentWifi[card.dataset.instrument];
    if (wifi) {
      const age = wifi.age_sec + Math.max(0, raspberryNow - store.serverTime);
      const quality = wifi.rssi >= -55 ? "velmi dobrý" :
        wifi.rssi >= -67 ? "dobrý" : wifi.rssi >= -75 ? "slabý" : "velmi slabý";
      const stale = age > 60;
      wifiEl.textContent = stale
        ? `Wi-Fi: poslední údaj před ${Math.round(age)} s (${wifi.rssi} dBm)`
        : `Wi-Fi: ${wifi.rssi} dBm (${quality}) · kanál ${wifi.channel ?? "?"} · reconnecty ${wifi.reconnects ?? "?"}`;
      wifiEl.classList.toggle("weak", wifi.rssi < -75 || stale);
      wifiEl.title = `RSSI ${wifi.rssi} dBm; údaj starý ${Math.round(age)} s`;
    } else {
      wifiEl.textContent = "Wi-Fi: čekám na data";
      wifiEl.classList.remove("weak");
      wifiEl.title = "ESP zatím neposlalo Wi-Fi diagnostiku";
    }
    const rawTime = instrumentTimes[card.dataset.instrument];
    // If ESP reports 0 (epoch), it means NTP is not synced yet.
    if (rawTime === 0 || rawTime === "0") {
      timeEl.textContent = "waiting for NTP...";
      timeEl.classList.add("out-of-sync");
      timeEl.title = "ESP is waiting for NTP sync from Raspberry Pi";
    } else {
      timeEl.textContent = fmtClock(rawTime);
      const skew = skews[card.dataset.instrument];
      const outOfSync = typeof skew === "number" && Math.abs(skew) > 0.5;
      timeEl.classList.toggle("out-of-sync", outOfSync);
      timeEl.title =
        typeof skew === "number"
          ? `Odchylka od Raspberry Pi: ${skew > 0 ? "+" : ""}${skew.toFixed(2)} s`
          : "";
    }
  });

  // Queue (build once, then update highlight)
  const q = view.querySelector("#queue");
  if (!queueBuilt && queue.length) {
    q.innerHTML = queue
      .map(
        (song, i) => `
      <div class="queue-row" data-index="${i}">
        <span class="queue-index">${i + 1}</span>
        <img class="queue-cover" src="${coverUrl(song.cover)}" alt="" crossorigin="anonymous" />
        <div class="queue-meta">
          <span class="queue-title">${song.title}</span>
          <span class="queue-artist">${song.artist}</span>
        </div>
        <span class="queue-dur">${fmtTime(song.duration)}</span>
      </div>`
      )
      .join("");
    queueBuilt = true;
  }
  q.querySelectorAll(".queue-row").forEach((row, i) => {
    row.classList.toggle("is-current", i === player.index);
  });
}
