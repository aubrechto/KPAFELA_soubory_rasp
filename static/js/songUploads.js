import { api, applySnapshot } from "./api.js?v=1.9";

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
      <span class="song-work-status" id="song-work-status" aria-live="polite"></span>
    </div>

    <dialog class="upload-dialog" id="upload-dialog">
      <div class="upload-dialog-head">
        <div><h2>Choose instruments</h2><p>Last successful upload to each target</p></div>
        <button class="dialog-close" id="upload-dialog-close" type="button" aria-label="Close">&times;</button>
      </div>
      <div class="upload-target-list" id="upload-target-list"></div>
      <div class="upload-progress" id="upload-progress" hidden>
        <div class="upload-progress-summary">
          <strong id="upload-progress-count">0 / 0 song uploads</strong>
          <span id="upload-progress-percent">0%</span>
        </div>
        <progress id="upload-progress-bar" max="100" value="0" aria-label="Song upload progress"></progress>
        <p id="upload-progress-instrument"></p>
      </div>
      <p class="upload-dialog-status" id="upload-dialog-status" aria-live="polite"></p>
    </dialog>
  `;

  const fileInput = view.querySelector("#song-files");
  const dropzone = view.querySelector("#song-dropzone");
  const browseButton = view.querySelector("#song-browse");
  const convertButton = view.querySelector("#song-convert");
  const uploadButton = view.querySelector("#song-upload");
  const dialog = view.querySelector("#upload-dialog");
  const status = view.querySelector("#song-work-status");
  const dialogStatus = view.querySelector("#upload-dialog-status");
  const progressPanel = view.querySelector("#upload-progress");
  const progressCount = view.querySelector("#upload-progress-count");
  const progressPercent = view.querySelector("#upload-progress-percent");
  const progressBar = view.querySelector("#upload-progress-bar");
  const progressInstrument = view.querySelector("#upload-progress-instrument");
  let tools = { source_files: [], converted_count: 0, needs_conversion: false, last_uploads: {} };
  let busy = false;
  let progressTimer = null;

  function renderUploadProgress(progress) {
    const total = Number(progress.overall_total) || 0;
    const uploaded = Number(progress.overall_count) || 0;
    const percent = total ? Math.min(100, Math.floor(uploaded / total * 100)) : 0;
    progressPanel.hidden = false;
    progressCount.textContent = `${uploaded} / ${total} song uploads`;
    progressPercent.textContent = `${percent}%`;
    progressBar.value = percent;
    progressBar.setAttribute("aria-valuetext", `${uploaded} of ${total} song uploads`);
    const instrument = TARGET_LABELS[progress.instrument] || "";
    const instrumentCount = Number(progress.uploaded_count) || 0;
    const instrumentTotal = Number(progress.total_count) || 0;
    progressInstrument.textContent = instrument
      ? `${instrument}: ${instrumentCount} / ${instrumentTotal} songs`
      : "";
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
    convertButton.textContent = busy && status.dataset.operation === "convert"
      ? "Converting..." : "Convert";
    uploadButton.disabled = busy || tools.converted_count === 0 || tools.needs_conversion;
    uploadButton.textContent = busy && status.dataset.operation === "upload"
      ? "Uploading..." : "Upload";
    uploadButton.title = tools.needs_conversion
      ? "Convert the updated source files before uploading"
      : "Choose one instrument or all instruments";
    renderTargets();
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
    render();
    try {
      const result = await api.convertSongs();
      tools = result;
      if (result.state) applySnapshot(result.state);
      showStatus(`Converted ${result.converted_count} songs; playlist refreshed.`, "is-success");
    } catch (error) {
      showStatus(error.payload?.error || error.message, "is-error");
    } finally {
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
    progressCount.textContent = `0 / ${tools.converted_count * (target === "all" ? 3 : 1)} song uploads`;
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
        uploaded_count: result.song_count,
        total_count: result.song_count,
        instrument: target === "all" ? "drums" : target,
      });
      tools.last_uploads = result.last_uploads;
      const completed = Object.entries(result.uploaded)
        .map(([instrument, count]) => `${count} to ${TARGET_LABELS[instrument]}`)
        .join(", ");
      dialog.close();
      showStatus(`Upload complete: ${completed}.`, "is-success");
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