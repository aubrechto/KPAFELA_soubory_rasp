import { api, applySnapshot } from "./api.js?v=1.11";

const TARGETS = ["guitar", "bass", "drums", "all"];
const TARGET_LABELS = {
  guitar: "Guitar",
  bass: "Bass",
  drums: "Drums",
  all: "All instruments",
};

export async function initSongUploads() {
  const view = document.getElementById("view-song-uploads");
  view.innerHTML = `
    <h1 class="page-title">Upload songs</h1>
    <p class="page-sub">Add MuseScore files, convert the library, then send songs to the ESP instruments.</p>

    <input class="song-file-input" id="song-files" type="file" accept=".mscz" multiple />
    <div class="song-dropzone" id="song-dropzone" role="button" tabindex="0" aria-label="Choose or drop MuseScore files">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0L7 9m5-5 5 5M4 14v5h16v-5" /></svg>
      <strong>Drop MuseScore files here</strong>
      <span>or choose .mscz files named Artist - Title</span>
      <button class="dropzone-browse" id="song-browse" type="button">Browse files</button>
    </div>

    <div class="song-source-summary">
      <div><strong id="source-count">0</strong><span>source files on Raspberry Pi</span></div>
      <ul class="song-source-list" id="song-source-list" aria-live="polite"></ul>
    </div>

    <div class="song-actions">
      <button class="btn-save" id="song-convert" type="button" disabled>Convert</button>
      <button class="btn-save song-upload-button" id="song-upload" type="button" disabled>Upload</button>
      <button class="btn-save song-delete-button" id="song-delete" type="button" disabled>Delete</button>
      <span class="song-work-status" id="song-work-status" aria-live="polite"></span>
    </div>
    <div class="upload-progress song-conversion-progress" id="conversion-progress" hidden>
      <div class="upload-progress-summary">
        <strong id="conversion-progress-count">0 / 0 songs processed</strong>
        <span id="conversion-progress-percent">0%</span>
      </div>
      <progress id="conversion-progress-bar" max="100" value="0" aria-label="Song conversion progress"></progress>
      <p class="upload-progress-instrument" id="conversion-progress-song"></p>
    </div>

    <dialog class="upload-dialog" id="upload-dialog">
      <div class="upload-dialog-head">
        <div><h2>Choose instruments</h2><p>Last successful upload to each target</p></div>
        <button class="dialog-close" id="upload-dialog-close" type="button" aria-label="Close">&times;</button>
      </div>
      <div class="upload-target-list" id="upload-target-list"></div>
      <div class="upload-progress" id="upload-progress" hidden>
        <div class="upload-progress-summary">
          <strong id="upload-progress-count">0 / 0 songs checked</strong>
          <span id="upload-progress-percent">0%</span>
        </div>
        <progress id="upload-progress-bar" max="100" value="0" aria-label="Song upload progress"></progress>
        <p id="upload-progress-instrument"></p>
      </div>
      <p class="upload-dialog-status" id="upload-dialog-status" aria-live="polite"></p>
    </dialog>

    <dialog class="upload-dialog song-delete-dialog" id="song-delete-dialog">
      <div class="upload-dialog-head">
        <div><h2>Delete song from Raspberry Pi</h2><p>ESP copies are not affected</p></div>
        <button class="dialog-close" id="song-delete-close" type="button" aria-label="Close">&times;</button>
      </div>
      <label class="song-delete-label" for="song-delete-select">Song</label>
      <select class="song-delete-select" id="song-delete-select"></select>
      <p class="upload-dialog-status">This removes the MuseScore source, converted files, and playlist entry from Raspberry Pi.</p>
      <p class="upload-dialog-status" id="song-delete-status" aria-live="polite"></p>
      <button class="btn-save song-delete-button song-delete-confirm" id="song-delete-confirm" type="button" disabled>Delete selected</button>
    </dialog>
  `;

  const fileInput = view.querySelector("#song-files");
  const dropzone = view.querySelector("#song-dropzone");
  const browseButton = view.querySelector("#song-browse");
  const convertButton = view.querySelector("#song-convert");
  const uploadButton = view.querySelector("#song-upload");
  const deleteButton = view.querySelector("#song-delete");
  const dialog = view.querySelector("#upload-dialog");
  const deleteDialog = view.querySelector("#song-delete-dialog");
  const deleteSelect = view.querySelector("#song-delete-select");
  const deleteConfirm = view.querySelector("#song-delete-confirm");
  const deleteStatus = view.querySelector("#song-delete-status");
  const status = view.querySelector("#song-work-status");
  const dialogStatus = view.querySelector("#upload-dialog-status");
  const progressPanel = view.querySelector("#upload-progress");
  const progressCount = view.querySelector("#upload-progress-count");
  const progressPercent = view.querySelector("#upload-progress-percent");
  const progressBar = view.querySelector("#upload-progress-bar");
  const progressInstrument = view.querySelector("#upload-progress-instrument");
  const conversionProgress = view.querySelector("#conversion-progress");
  const conversionProgressCount = view.querySelector("#conversion-progress-count");
  const conversionProgressPercent = view.querySelector("#conversion-progress-percent");
  const conversionProgressBar = view.querySelector("#conversion-progress-bar");
  const conversionProgressSong = view.querySelector("#conversion-progress-song");
  let tools = { source_files: [], converted_count: 0, needs_conversion: false, last_uploads: {} };
  let busy = false;
  let progressTimer = null;

  function renderUploadProgress(progress) {
    const total = Number(progress.overall_total) || 0;
    const processed = Number(progress.overall_count) || 0;
    const percent = total ? Math.min(100, Math.floor(processed / total * 100)) : 0;
    progressPanel.hidden = false;
    progressCount.textContent = `${processed} / ${total} songs checked`;
    progressPercent.textContent = `${percent}%`;
    progressBar.value = percent;
    progressBar.setAttribute("aria-valuetext", `${processed} of ${total} songs checked`);
    const instrument = TARGET_LABELS[progress.instrument] || "";
    const instrumentProcessed = Number(progress.processed_count) || 0;
    const instrumentUploaded = Number(progress.uploaded_count) || 0;
    const instrumentSkipped = Number(progress.skipped_count) || 0;
    const instrumentTotal = Number(progress.total_count) || 0;
    progressInstrument.textContent = instrument
      ? `${instrument}: ${instrumentProcessed} / ${instrumentTotal} checked (${instrumentUploaded} uploaded, ${instrumentSkipped} already present)`
      : "";
  }

  function renderConversionProgress(progress) {
    const total = Number(progress.total_count) || 0;
    const completed = Number(progress.completed_count) || 0;
    const percent = total ? Math.min(100, Math.floor(completed / total * 100)) : 0;
    conversionProgress.hidden = false;
    conversionProgressCount.textContent = `${completed} / ${total} songs processed`;
    conversionProgressPercent.textContent = `${percent}%`;
    conversionProgressBar.value = percent;
    conversionProgressBar.setAttribute("aria-valuetext", `${completed} of ${total} songs processed`);
    const converted = Number(progress.converted_count) || 0;
    const skipped = Number(progress.skipped_count) || 0;
    conversionProgressSong.textContent = progress.current_song
      ? `Converting: ${progress.current_song}`
      : `${converted} converted, ${skipped} unchanged`;
  }

  function showStatus(message, kind = "") {
    status.textContent = message;
    status.className = `song-work-status ${kind}`;
  }

  function render() {
    view.querySelector("#source-count").textContent = tools.source_files.length;
    const list = view.querySelector("#song-source-list");
    list.replaceChildren();
    tools.source_files.slice(-8).forEach((filename) => {
      const item = document.createElement("li");
      item.textContent = filename;
      list.append(item);
    });
    if (tools.source_files.length > 8) {
      const more = document.createElement("li");
      more.textContent = `and ${tools.source_files.length - 8} more`;
      list.append(more);
    }
    fileInput.disabled = busy;
    dropzone.classList.toggle("is-disabled", busy);
    convertButton.disabled = busy || tools.source_files.length === 0;
    deleteButton.disabled = busy || tools.source_files.length === 0;
    convertButton.textContent = busy && status.dataset.operation === "convert"
      ? "Converting..." : "Convert";
    uploadButton.disabled = busy || tools.converted_count === 0 || tools.needs_conversion;
    uploadButton.textContent = busy && status.dataset.operation === "upload"
      ? "Uploading..." : "Upload";
    uploadButton.title = tools.needs_conversion
      ? "Convert the updated source files before uploading"
      : "Choose one instrument or all instruments";
    renderTargets();
    renderDeleteOptions();
  }

  function formatUploadTime(value) {
    if (!value) return "Never uploaded";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  function renderTargets() {
    const list = view.querySelector("#upload-target-list");
    list.replaceChildren();
    TARGETS.forEach((target) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "upload-target";
      button.dataset.target = target;
      button.disabled = busy;
      const name = document.createElement("strong");
      name.textContent = TARGET_LABELS[target];
      const detail = document.createElement("span");
      detail.textContent = target === "all"
        ? TARGETS.slice(0, 3).map((instrument) =>
          `${TARGET_LABELS[instrument]}: ${formatUploadTime(tools.last_uploads?.[instrument])}`
        ).join(" · ")
        : formatUploadTime(tools.last_uploads?.[target]);
      button.append(name, detail);
      list.append(button);
    });
  }

  function renderDeleteOptions() {
    const selectedFilename = deleteSelect.value;
    deleteSelect.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = tools.source_files.length
      ? "Choose a song"
      : "No songs available";
    deleteSelect.append(placeholder);
    tools.source_files.forEach((filename) => {
      const option = document.createElement("option");
      option.value = filename;
      option.textContent = filename;
      deleteSelect.append(option);
    });
    deleteSelect.value = tools.source_files.includes(selectedFilename)
      ? selectedFilename : "";
    deleteSelect.disabled = busy || tools.source_files.length === 0;
    deleteConfirm.disabled = busy || !deleteSelect.value;
  }

  async function refreshTools() {
    tools = await api.getSongTools();
    render();
  }

  async function saveSources(fileList) {
    const files = Array.from(fileList);
    if (!files.length || busy) return;
    busy = true;
    status.dataset.operation = "sources";
    showStatus(`Saving ${files.length} source file${files.length === 1 ? "" : "s"}...`);
    render();
    try {
      const result = await api.uploadSongSources(files);
      tools = result;
      showStatus(`${result.saved.length} source file${result.saved.length === 1 ? "" : "s"} saved. Convert to refresh the playlist.`, "is-success");
      fileInput.value = "";
    } catch (error) {
      showStatus(error.payload?.error || error.message, "is-error");
    } finally {
      busy = false;
      status.dataset.operation = "";
      render();
    }
  }

  fileInput.addEventListener("change", () => void saveSources(fileInput.files));
  browseButton.addEventListener("click", () => fileInput.click());
  deleteButton.addEventListener("click", () => {
    if (busy || !tools.source_files.length) return;
    deleteStatus.textContent = "";
    renderDeleteOptions();
    if (!deleteDialog.open) deleteDialog.showModal();
  });
  deleteSelect.addEventListener("change", () => {
    deleteConfirm.disabled = busy || !deleteSelect.value;
  });
  view.querySelector("#song-delete-close").addEventListener("click", () => deleteDialog.close());
  deleteConfirm.addEventListener("click", async () => {
    const filename = deleteSelect.value;
    if (!filename || busy) return;
    busy = true;
    deleteStatus.textContent = `Deleting ${filename}...`;
    render();
    try {
      const result = await api.deleteSongSource(filename);
      tools = result;
      deleteDialog.close();
      showStatus(`Deleted ${result.deleted} from Raspberry Pi. ESP copies remain.`, "is-success");
    } catch (error) {
      deleteStatus.textContent = error.payload?.error || error.message;
      showStatus(`Delete failed: ${deleteStatus.textContent}`, "is-error");
    } finally {
      busy = false;
      render();
    }
  });
  dropzone.addEventListener("click", (event) => {
    if (!event.target.closest("button") && !busy) fileInput.click();
  });
  dropzone.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && !busy) {
      event.preventDefault();
      fileInput.click();
    }
  });
  ["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    if (!busy) dropzone.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragging");
    if (type === "drop") void saveSources(event.dataTransfer.files);
  }));

  convertButton.addEventListener("click", async () => {
    if (busy || !tools.source_files.length) return;
    busy = true;
    status.dataset.operation = "convert";
    showStatus("Converting all source files and rebuilding the playlist...");
    conversionProgress.hidden = false;
    renderConversionProgress({
      total_count: tools.source_files.length,
      completed_count: 0,
      converted_count: 0,
      skipped_count: 0,
    });
    conversionProgressSong.textContent = "Preparing conversion...";
    render();
    progressTimer = window.setInterval(async () => {
      try {
        const progress = await api.getSongConversionProgress();
        if (progress.state !== "idle") renderConversionProgress(progress);
      } catch {
        // The conversion request still reports failures if progress polling is unavailable.
      }
    }, 500);
    try {
      const result = await api.convertSongs();
      window.clearInterval(progressTimer);
      progressTimer = null;
      renderConversionProgress({
        total_count: result.source_files.length,
        completed_count: result.source_files.length,
        converted_count: result.converted_this_run,
        skipped_count: result.skipped_count,
      });
      tools = result;
      if (result.state) applySnapshot(result.state);
      showStatus(
        `Converted ${result.converted_this_run} new songs, skipped ${result.skipped_count} unchanged; playlist refreshed.`,
        "is-success",
      );
    } catch (error) {
      window.clearInterval(progressTimer);
      progressTimer = null;
      showStatus(error.payload?.error || error.message, "is-error");
    } finally {
      if (progressTimer) window.clearInterval(progressTimer);
      progressTimer = null;
      busy = false;
      status.dataset.operation = "";
      render();
    }
  });

  uploadButton.addEventListener("click", () => {
    dialogStatus.textContent = "Select a destination to upload the converted song library.";
    if (!dialog.open) dialog.showModal();
  });
  view.querySelector("#upload-dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", async (event) => {
    const targetButton = event.target.closest("[data-target]");
    if (!targetButton || busy) return;
    const target = targetButton.dataset.target;
    busy = true;
    status.dataset.operation = "upload";
    dialogStatus.textContent = `Uploading ${tools.converted_count} songs to ${TARGET_LABELS[target]}...`;
    progressPanel.hidden = false;
    progressCount.textContent = `0 / ${tools.converted_count * (target === "all" ? 3 : 1)} songs checked`;
    progressPercent.textContent = "0%";
    progressBar.value = 0;
    progressInstrument.textContent = "Preparing upload...";
    render();
    progressTimer = window.setInterval(async () => {
      try {
        const progress = await api.getSongUploadProgress();
        if (progress.target === target && progress.state !== "idle") {
          renderUploadProgress(progress);
        }
      } catch {
        // The upload request still reports failures if progress polling is unavailable.
      }
    }, 500);
    try {
      const result = await api.uploadSongs(target);
      window.clearInterval(progressTimer);
      progressTimer = null;
      const totalUploads = result.song_count * (target === "all" ? 3 : 1);
      renderUploadProgress({
        overall_count: totalUploads,
        overall_total: totalUploads,
        processed_count: result.song_count,
        uploaded_count: result.uploaded[target === "all" ? "drums" : target] || 0,
        skipped_count: result.skipped[target === "all" ? "drums" : target] || 0,
        total_count: result.song_count,
        instrument: target === "all" ? "drums" : target,
      });
      tools.last_uploads = result.last_uploads;
      const completed = Object.entries(result.uploaded)
        .map(([instrument, count]) =>
          `${TARGET_LABELS[instrument]}: ${count} uploaded, ${result.skipped[instrument] || 0} already present`
        )
        .join(", ");
      dialog.close();
      showStatus(`Song sync complete: ${completed}.`, "is-success");
    } catch (error) {
      window.clearInterval(progressTimer);
      progressTimer = null;
      const detail = error.payload?.error || error.message;
      dialogStatus.textContent = `Upload failed: ${detail}`;
      showStatus(`Upload failed: ${detail}`, "is-error");
      if (error.payload?.last_uploads) tools.last_uploads = error.payload.last_uploads;
    } finally {
      if (progressTimer) window.clearInterval(progressTimer);
      progressTimer = null;
      busy = false;
      status.dataset.operation = "";
      render();
    }
  });

  try {
    await refreshTools();
  } catch (error) {
    showStatus(`Could not load song tools: ${error.message}`, "is-error");
  }
}