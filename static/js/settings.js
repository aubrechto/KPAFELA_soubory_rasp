// Settings view: automatic playlist scheduling configuration.
import { api } from "./api.js";

export async function initSettings() {
  const view = document.getElementById("view-settings");
  view.innerHTML = `
    <h1 class="page-title">Settings</h1>
    <p class="page-sub">Automatic playlist control for scheduled performances.</p>

    <form class="card" id="settings-form">
      <label class="toggle">
        <input type="checkbox" name="auto_enabled" />
        <span class="toggle-track"></span>
        <span>Enable automatic scheduling</span>
      </label>

      <div class="field-row" style="margin-top:22px">
        <div class="field">
          <label for="start_time">Start time</label>
          <input type="time" id="start_time" name="start_time" />
          <span class="field-hint">When the band begins playing.</span>
        </div>
        <div class="field">
          <label for="stop_time">Stop time</label>
          <input type="time" id="stop_time" name="stop_time" />
          <span class="field-hint">When the band stops for the day.</span>
        </div>
      </div>

      <div class="field-row">
        <div class="field">
          <label for="songs_before_break">Songs before break</label>
          <input type="number" id="songs_before_break" name="songs_before_break" min="1" max="50" />
          <span class="field-hint">Number of songs played before a pause.</span>
        </div>
        <div class="field">
          <label for="break_minutes">Break duration (minutes)</label>
          <input type="number" id="break_minutes" name="break_minutes" min="0" max="240" />
          <span class="field-hint">Length of each scheduled break.</span>
        </div>
      </div>

      <div class="save-row">
        <button type="submit" class="btn-save">Save settings</button>
        <span class="save-note" id="settings-note">Saved</span>
      </div>
    </form>
  `;

  const form = view.querySelector("#settings-form");
  const note = view.querySelector("#settings-note");

  const settings = await api.getSettings();
  form.auto_enabled.checked = !!settings.auto_enabled;
  form.start_time.value = settings.start_time ?? "18:00";
  form.stop_time.value = settings.stop_time ?? "23:00";
  form.songs_before_break.value = settings.songs_before_break ?? 4;
  form.break_minutes.value = settings.break_minutes ?? 15;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      auto_enabled: form.auto_enabled.checked,
      start_time: form.start_time.value,
      stop_time: form.stop_time.value,
      songs_before_break: Number(form.songs_before_break.value),
      break_minutes: Number(form.break_minutes.value),
    };
    await api.saveSettings(payload);
    note.classList.add("show");
    setTimeout(() => note.classList.remove("show"), 1800);
  });
}
