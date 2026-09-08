import WaveSurfer from "../vendor/wavesurfer.esm.js";
import RegionsPlugin from "../vendor/regions.esm.js";
import TimelinePlugin from "../vendor/timeline.esm.js";
import * as A from "./audio.js";
import { api, pollJob } from "./api.js";

const $ = (id) => document.getElementById(id);

const state = {
  file: null,        // исходный File
  original: null,    // исходный AudioBuffer (для «Сбросить»)
  buffer: null,      // текущий AudioBuffer
  history: [],       // стек для «Отменить»
  region: null,
  ws: null,
  regions: null,
  jobId: null,
  objectUrl: null,
};

// ---------------------------------------------------------------------------
// Инициализация
// ---------------------------------------------------------------------------
init();

async function init() {
  bindDropzone();
  bindEditor();
  bindTranscribe();
  bindResult();
  bindSettings();

  try {
    const deps = await api.deps();
    const bad = Object.entries(deps).filter(([, ok]) => !ok).map(([k]) => k.toUpperCase());
    const el = $("deps");
    el.textContent = bad.length ? `недоступно: ${bad.join(", ")}` : "ASR ✓ LLM ✓";
    el.className = `deps ${bad.length ? "err" : "ok"}`;
  } catch {
    $("deps").textContent = "API недоступен";
    $("deps").className = "deps err";
  }
}

// ---------------------------------------------------------------------------
// Шаг 1: загрузка файла
// ---------------------------------------------------------------------------
function bindDropzone() {
  const zone = $("dropzone");
  const input = $("file-input");

  zone.addEventListener("click", (e) => {
    if (e.target.tagName !== "LABEL") input.click();
  });
  input.addEventListener("change", () => input.files[0] && loadFile(input.files[0]));

  ["dragenter", "dragover"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.remove("drag");
    })
  );
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) loadFile(file);
  });
}

async function loadFile(file) {
  state.file = file;
  setEditStatus("Декодирование…");
  try {
    state.original = await A.decode(file);
  } catch (err) {
    setEditStatus(`Не удалось декодировать файл: ${err.message}`, true);
    return;
  }
  state.buffer = cloneBuffer(state.original);
  state.history = [];

  $("file-info").classList.remove("hidden");
  $("file-info").innerHTML = `
    <span><strong>${escapeHtml(file.name)}</strong></span>
    <span>${A.formatBytes(file.size)}</span>
    <span>${A.formatTime(state.original.duration)}</span>
    <span>${state.original.numberOfChannels === 1 ? "моно" : "стерео"} · ${state.original.sampleRate} Гц</span>`;

  $("step-edit").classList.remove("hidden");
  $("step-transcribe").classList.remove("hidden");
  await renderWave();
  setEditStatus("Выделите фрагмент прямо на волне — перетаскиванием.");
}

// ---------------------------------------------------------------------------
// Шаг 2: редактор
// ---------------------------------------------------------------------------
async function renderWave() {
  if (state.ws) state.ws.destroy();
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);

  const blob = A.encodeWav(state.buffer);
  state.objectUrl = URL.createObjectURL(blob);

  state.regions = RegionsPlugin.create();
  state.ws = WaveSurfer.create({
    container: "#waveform",
    height: 128,
    waveColor: "#4c8dff88",
    progressColor: "#4c8dff",
    cursorColor: "#e6eaf0",
    url: state.objectUrl,
    plugins: [state.regions, TimelinePlugin.create({ container: "#timeline" })],
  });

  state.ws.on("ready", () => {
    state.regions.enableDragSelection({ color: "rgba(76,141,255,.18)" });
    selectAll();
    updateTime();
  });
  state.ws.on("audioprocess", updateTime);
  state.ws.on("interaction", updateTime);
  state.ws.on("finish", () => ($("btn-play").textContent = "▶ Воспроизвести"));

  state.regions.on("region-created", (region) => {
    // Держим ровно одну область выделения
    state.regions.getRegions().forEach((r) => r !== region && r.remove());
    state.region = region;
    syncRegionInputs();
  });
  state.regions.on("region-updated", (region) => {
    state.region = region;
    syncRegionInputs();
  });
}

function selectAll() {
  state.regions.clearRegions();
  state.region = state.regions.addRegion({
    start: 0,
    end: state.buffer.duration,
    color: "rgba(76,141,255,.18)",
    drag: true,
    resize: true,
  });
  syncRegionInputs();
}

function syncRegionInputs() {
  if (!state.region) return;
  $("region-start").value = state.region.start.toFixed(3);
  $("region-end").value = state.region.end.toFixed(3);
  $("region-length").textContent =
    `длительность выделения: ${A.formatTime(state.region.end - state.region.start)}`;
}

function updateTime() {
  if (!state.ws) return;
  $("time").textContent =
    `${A.formatTime(state.ws.getCurrentTime())} / ${A.formatTime(state.buffer.duration)}`;
}

function bindEditor() {
  $("btn-play").addEventListener("click", () => {
    state.ws.playPause();
    $("btn-play").textContent = state.ws.isPlaying() ? "⏸ Пауза" : "▶ Воспроизвести";
  });
  $("btn-play-region").addEventListener("click", () => state.region?.play());
  $("btn-select-all").addEventListener("click", selectAll);

  $("zoom").addEventListener("input", (e) => state.ws?.zoom(Number(e.target.value)));

  for (const id of ["region-start", "region-end"]) {
    $(id).addEventListener("change", () => {
      const start = clamp(parseFloat($("region-start").value) || 0, 0, state.buffer.duration);
      const end = clamp(parseFloat($("region-end").value) || 0, start, state.buffer.duration);
      state.region?.setOptions({ start, end });
      syncRegionInputs();
    });
  }

  $("btn-crop").addEventListener("click", () => applyEdit((b, r) => A.crop(b, r.start, r.end), "Оставлено выделение"));
  $("btn-cut").addEventListener("click", () => applyEdit((b, r) => A.cut(b, r.start, r.end), "Фрагмент вырезан"));
  $("btn-fade").addEventListener("click", () => applyEdit((b) => A.fade(cloneBuffer(b), 0.05, 0.05), "Применён fade"));
  $("btn-normalize").addEventListener("click", () => applyEdit((b) => A.normalize(cloneBuffer(b)), "Громкость нормализована"));

  $("btn-undo").addEventListener("click", async () => {
    const previous = state.history.pop();
    if (!previous) return setEditStatus("Отменять нечего.");
    state.buffer = previous;
    await renderWave();
    setEditStatus("Действие отменено.");
  });

  $("btn-reset").addEventListener("click", async () => {
    if (!state.original) return;
    state.history = [];
    state.buffer = cloneBuffer(state.original);
    await renderWave();
    setEditStatus("Возвращён исходный файл.");
  });

  $("btn-download").addEventListener("click", () => {
    const blob = A.encodeWav(state.buffer);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${baseName(state.file?.name || "audio")}-edited.wav`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  });
}

async function applyEdit(fn, message) {
  if (!state.buffer || !state.region) return;
  state.history.push(state.buffer);
  if (state.history.length > 20) state.history.shift();
  state.buffer = fn(state.buffer, state.region);
  await renderWave();
  setEditStatus(`${message}. Длительность: ${A.formatTime(state.buffer.duration)}`);
}

function setEditStatus(text, isError = false) {
  const el = $("edit-status");
  el.textContent = text;
  el.className = `muted small ${isError ? "err" : ""}`;
}

// ---------------------------------------------------------------------------
// Шаг 3: отправка на расшифровку
// ---------------------------------------------------------------------------
function bindTranscribe() {
  $("opt-post").addEventListener("change", (e) => {
    $("wrap-target-lang").classList.toggle("hidden", e.target.value !== "translate");
    $("wrap-custom").classList.toggle("hidden", e.target.value !== "custom");
  });

  $("btn-send").addEventListener("click", submit);
}

function collectOptions() {
  return {
    language: $("opt-language").value || null,
    model: $("opt-model").value.trim() || null,
    prompt: $("opt-prompt").value.trim() || null,
    timestamps: $("opt-timestamps").checked,
    post_action: $("opt-post").value,
    post_instruction: $("opt-instruction").value.trim() || null,
    target_language: $("opt-target-language").value.trim() || null,
    destinations: [...document.querySelectorAll(".dest:checked")].map((c) => c.value),
    telegram_chat_id: $("opt-tg-chat").value.trim() || null,
    webhook_url: $("opt-webhook").value.trim() || null,
    meta: { source: "web-ui", original_filename: state.file?.name || null },
  };
}

async function submit() {
  if (!state.buffer) return;
  const button = $("btn-send");
  button.disabled = true;
  $("progress-wrap").classList.remove("hidden");
  setSendStatus("Подготовка аудио (моно 16 кГц)…");

  try {
    const prepared = await A.toWhisperFormat(state.buffer);
    const blob = A.encodeWav(prepared);
    setSendStatus(`Отправка ${A.formatBytes(blob.size)}…`);

    const filename = `${baseName(state.file?.name || "audio")}.wav`;
    const created = await api.createJob(blob, filename, collectOptions());
    state.jobId = created.id;

    const job = await pollJob(created.id, (j) => {
      $("progress-bar").style.width = `${j.progress}%`;
      setSendStatus(statusLabel(j.status));
    });

    if (job.status === "failed") {
      setSendStatus(`Ошибка: ${job.error}`, true);
      return;
    }
    setSendStatus("Готово.");
    showResult(job);
  } catch (err) {
    setSendStatus(`Ошибка: ${err.message}`, true);
  } finally {
    button.disabled = false;
  }
}

function statusLabel(status) {
  return {
    queued: "В очереди…",
    transcribing: "Распознавание речи…",
    postprocessing: "Постобработка моделью…",
    delivering: "Отправка получателям…",
    done: "Готово.",
    failed: "Ошибка.",
  }[status] || status;
}

function setSendStatus(text, isError = false) {
  const el = $("send-status");
  el.textContent = text;
  el.className = isError ? "err" : "muted";
}

// ---------------------------------------------------------------------------
// Шаг 4: результат
// ---------------------------------------------------------------------------
function showResult(job) {
  $("step-result").classList.remove("hidden");
  $("out-text").textContent = job.result.text || "(пусто)";
  $("out-post").textContent = job.result.post_output || "(постобработка не запрашивалась)";
  $("out-segments").textContent = job.result.segments?.length
    ? job.result.segments
        .map((s) => `[${A.formatTime(s.start)} → ${A.formatTime(s.end)}] ${s.speaker ? s.speaker + ": " : ""}${s.text}`)
        .join("\n")
    : "(включите «Сегменты с таймкодами», чтобы получить разбивку)";
  $("out-delivery").textContent = JSON.stringify(job.result.delivery || {}, null, 2);

  for (const fmt of ["txt", "md", "srt", "vtt"]) {
    $(`dl-${fmt}`).href = api.exportUrl(job.id, fmt);
  }
  $("step-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function bindResult() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tabpanel").forEach((p) => p.classList.add("hidden"));
      tab.classList.add("active");
      $(`tab-${tab.dataset.tab}`).classList.remove("hidden");
    });
  });

  $("btn-copy").addEventListener("click", async () => {
    const visible = [...document.querySelectorAll(".tabpanel")].find((p) => !p.classList.contains("hidden"));
    await navigator.clipboard.writeText(visible?.textContent || "");
    $("btn-copy").textContent = "Скопировано";
    setTimeout(() => ($("btn-copy").textContent = "Скопировать"), 1500);
  });

  $("btn-redeliver").addEventListener("click", async () => {
    if (!state.jobId) return;
    try {
      const report = await api.redeliver(state.jobId);
      $("out-delivery").textContent = JSON.stringify(report, null, 2);
    } catch (err) {
      $("out-delivery").textContent = `Ошибка: ${err.message}`;
    }
  });
}

// ---------------------------------------------------------------------------
// Настройки
// ---------------------------------------------------------------------------
function bindSettings() {
  const dialog = $("settings");
  $("set-api-key").value = localStorage.getItem("transcriber.apiKey") || "";
  $("btn-settings").addEventListener("click", () => dialog.showModal());
  dialog.addEventListener("close", () => {
    if (dialog.returnValue === "save") {
      localStorage.setItem("transcriber.apiKey", $("set-api-key").value.trim());
    }
  });
}

// ---------------------------------------------------------------------------
// Утилиты
// ---------------------------------------------------------------------------
function cloneBuffer(buffer) {
  const ctx = new OfflineAudioContext(buffer.numberOfChannels, buffer.length, buffer.sampleRate);
  const copy = ctx.createBuffer(buffer.numberOfChannels, buffer.length, buffer.sampleRate);
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    copy.copyToChannel(buffer.getChannelData(ch).slice(), ch);
  }
  return copy;
}

const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
const baseName = (name) => name.replace(/\.[^.]+$/, "");
const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
