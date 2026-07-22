// Instrument Preferences view: hardware mapping for guitar, bass and drums.
import { api, coverUrl } from "./api.js";

// Relative positions (%) of each drum on the generated kit image.
const DRUM_POSITIONS = {
  crash: { x: 22, y: 18 },
  hihat: { x: 18, y: 42 },
  ride: { x: 84, y: 18 },
  low_tom: { x: 42, y: 34 },
  high_tom: { x: 62, y: 30 },
  snare: { x: 34, y: 58 },
  floor: { x: 74, y: 62 },
  kick: { x: 50, y: 84 },
};

let config = null;

export async function initPreferences() {
  config = await api.getInstruments();
  const view = document.getElementById("view-preferences");
  view.innerHTML = `
    <h1 class="page-title">Instrument Preferences</h1>
    <p class="page-sub">Map every solenoid, servo and GPIO to the physical hardware.</p>

    <div class="prefs-grid">
      ${stringColumn("guitar", config.guitar, "Solenoid ID per string &amp; fret")}
      ${stringColumn("bass", config.bass, "Solenoid ID per string &amp; fret")}
      ${drumColumn(config.drums)}
    </div>

    <div class="save-row" style="margin-top:24px">
      <button type="button" class="btn-save" id="prefs-save">Save hardware map</button>
      <span class="save-note" id="prefs-note">Saved</span>
    </div>
  `;

  wireDrumHotspots(view);

  view.querySelector("#prefs-save").addEventListener("click", async () => {
    collectInto(config, view);
    await api.saveInstruments(config);
    const note = view.querySelector("#prefs-note");
    note.classList.add("show");
    setTimeout(() => note.classList.remove("show"), 1800);
  });
}

// ---------------------------------------------------- string instruments
function stringColumn(name, cfg, sub) {
  const names = cfg.string_names || [];
  let head = "<tr><th></th>";
  for (let f = 0; f <= cfg.frets; f++) {
    head += `<th>${f === 0 ? "Open" : f}</th>`;
  }
  head += "</tr>";

  let rows = "";
  for (let s = 0; s < cfg.strings; s++) {
    rows += `<tr><td class="string-name">${names[s] ?? s + 1}</td>`;
    for (let f = 0; f <= cfg.frets; f++) {
      const key = `s${s}-f${f}`;
      const val = cfg.solenoids?.[key] ?? "";
      rows += `<td class="fret-cell${f === 0 ? " open" : ""}">
        <input data-inst="${name}" data-key="${key}" value="${val}" placeholder="ID" inputmode="numeric" />
      </td>`;
    }
    rows += "</tr>";
  }

  const servos = cfg.string_servos || {};
  let servoRows = "";
  for (let s = 0; s < cfg.strings; s++) {
    const key = `s${s}`;
    const servo = servos[key] || {};
    servoRows += `
      <tr>
        <td>${names[s] ?? s + 1}</td>
        <td><input type="number" data-inst="${name}" data-key="${key}" data-subkey="servo_channel" value="${servo.servo_channel ?? 0}" min="0" max="31" /></td>
        <td><input type="number" data-inst="${name}" data-key="${key}" data-subkey="servo_up" value="${servo.servo_up ?? 60}" min="0" max="180" /></td>
        <td><input type="number" data-inst="${name}" data-key="${key}" data-subkey="servo_low" value="${servo.servo_low ?? 120}" min="0" max="180" /></td>
        <td><input type="number" data-inst="${name}" data-key="${key}" data-subkey="suppress_up_angle" value="${servo.suppress_up_angle ?? 60}" min="0" max="180" /></td>
        <td><input type="number" data-inst="${name}" data-key="${key}" data-subkey="suppress_down_angle" value="${servo.suppress_down_angle ?? 120}" min="0" max="180" /></td>
      </tr>`;
  }

  return `
  <div class="pref-col">
    <h3>${cap(name)}</h3>
    <p class="col-sub">${sub}</p>
    <div class="fretboard">
      <table class="fret-table"><thead>${head}</thead><tbody>${rows}</tbody></table>
    </div>
    <div class="pick-box">
      <h4>Servo control per string</h4>
      <div class="servo-table-wrap">
        <table class="servo-table">
          <thead>
            <tr>
              <th>String</th>
              <th>Servo channel</th>
              <th>Servo up</th>
              <th>Servo low</th>
              <th>Suppress up angle</th>
              <th>Suppress down angle</th>
            </tr>
          </thead>
          <tbody>
            ${servoRows}
          </tbody>
        </table>
      </div>
    </div>
  </div>`;
}

// ------------------------------------------------------------------ drums
function drumColumn(drums) {
  const pads = drums.pads || {};
  const fields = Object.entries(pads)
    .map(
      ([key, pad]) => `
    <div class="drum-field" data-field="${key}">
      <label>${pad.label}</label>
      <input data-drum-id="${key}" value="${pad.id ?? ""}" placeholder="Solenoid / GPIO" />
    </div>`
    )
    .join("");

  return `
  <div class="pref-col">
    <h3>Drums</h3>
    <p class="col-sub">Assign a solenoid / GPIO to each drum</p>
    <div class="drumkit">
      <img src="${coverUrl("drum-kit.png")}" alt="Drum kit" crossorigin="anonymous" />
      ${hotspots}
    </div>
    <div class="drum-fields">${fields}</div>
  </div>`;
}

function wireDrumHotspots(view) {
  view.querySelectorAll(".drum-hot").forEach((hot) => {
    hot.addEventListener("click", () => {
      const key = hot.dataset.drum;
      const input = view.querySelector(`[data-drum-id="${key}"]`);
      view.querySelectorAll(".drum-hot").forEach((h) => h.classList.remove("is-active"));
      hot.classList.add("is-active");
      input?.focus();
      input?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  });
}

// ------------------------------------------------------------- collecting
function collectInto(cfg, view) {
  // Fretboard solenoids and per-string servo mapping
  ["guitar", "bass"].forEach((inst) => {
    cfg[inst].solenoids = {};
    view.querySelectorAll(`[data-inst="${inst}"]`).forEach((input) => {
      if (input.dataset.subkey) return;
      const v = input.value.trim();
      if (v) cfg[inst].solenoids[input.dataset.key] = v;
    });

    cfg[inst].string_servos = {};
    view.querySelectorAll(`[data-inst="${inst}"][data-subkey]`).forEach((input) => {
      const key = input.dataset.key;
      const subkey = input.dataset.subkey;
      const v = input.value.trim();
      if (!cfg[inst].string_servos[key]) cfg[inst].string_servos[key] = {};
      if (v !== "") cfg[inst].string_servos[key][subkey] = Number(v);
    });
  });
  // Drums
  view.querySelectorAll("[data-drum-id]").forEach((input) => {
    const key = input.dataset.drumId;
    if (cfg.drums.pads[key]) cfg.drums.pads[key].id = input.value.trim();
  });
}

function cap(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
