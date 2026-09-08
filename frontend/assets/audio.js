/**
 * Вся обработка звука выполняется в браузере пользователя:
 * файл никуда не уходит, пока пользователь сам не нажмёт «Отправить».
 */

/** Декодирует любой контейнер, который умеет браузер (mp3/wav/ogg/m4a/webm/mp4/mov). */
export async function decode(file) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try {
    return await ctx.decodeAudioData(await file.arrayBuffer());
  } finally {
    ctx.close();
  }
}

function emptyLike(buffer, length, sampleRate = buffer.sampleRate) {
  const ctx = new OfflineAudioContext(buffer.numberOfChannels, length, sampleRate);
  return ctx.createBuffer(buffer.numberOfChannels, length, sampleRate);
}

/** Оставить только участок [start, end] (секунды). */
export function crop(buffer, start, end) {
  const rate = buffer.sampleRate;
  const from = Math.max(0, Math.floor(start * rate));
  const to = Math.min(buffer.length, Math.ceil(end * rate));
  const length = Math.max(1, to - from);
  const out = emptyLike(buffer, length);
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    out.copyToChannel(buffer.getChannelData(ch).subarray(from, to), ch);
  }
  return out;
}

/** Вырезать участок [start, end], склеив остаток. */
export function cut(buffer, start, end) {
  const rate = buffer.sampleRate;
  const from = Math.max(0, Math.floor(start * rate));
  const to = Math.min(buffer.length, Math.ceil(end * rate));
  const length = Math.max(1, buffer.length - (to - from));
  const out = emptyLike(buffer, length);
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    const src = buffer.getChannelData(ch);
    const dst = out.getChannelData(ch);
    dst.set(src.subarray(0, from), 0);
    dst.set(src.subarray(to), from);
  }
  return out;
}

/** Линейные fade in/out по краям (секунды) — убирает щелчки на стыках. */
export function fade(buffer, fadeIn = 0.02, fadeOut = 0.02) {
  const rate = buffer.sampleRate;
  const inN = Math.min(buffer.length, Math.floor(fadeIn * rate));
  const outN = Math.min(buffer.length, Math.floor(fadeOut * rate));
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < inN; i++) data[i] *= i / inN;
    for (let i = 0; i < outN; i++) data[buffer.length - 1 - i] *= i / outN;
  }
  return buffer;
}

/** Пиковая нормализация до -1 dBFS. */
export function normalize(buffer, targetDb = -1) {
  let peak = 0;
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i++) {
      const v = Math.abs(data[i]);
      if (v > peak) peak = v;
    }
  }
  if (peak === 0) return buffer;
  const gain = Math.pow(10, targetDb / 20) / peak;
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i++) data[i] *= gain;
  }
  return buffer;
}

/**
 * Приводит к 16 кГц моно — родной формат Whisper.
 * Даёт файл в ~10 раз легче стерео-44.1 кГц, качество распознавания не страдает.
 */
export async function toWhisperFormat(buffer, sampleRate = 16000) {
  const length = Math.max(1, Math.ceil(buffer.duration * sampleRate));
  const ctx = new OfflineAudioContext(1, length, sampleRate);
  const source = ctx.createBufferSource();

  if (buffer.numberOfChannels > 1) {
    // Сводим каналы в моно вручную: downmix средним по каналам
    const mono = ctx.createBuffer(1, buffer.length, buffer.sampleRate);
    const dst = mono.getChannelData(0);
    for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
      const src = buffer.getChannelData(ch);
      for (let i = 0; i < src.length; i++) dst[i] += src[i] / buffer.numberOfChannels;
    }
    source.buffer = mono;
  } else {
    source.buffer = buffer;
  }

  source.connect(ctx.destination);
  source.start();
  return ctx.startRendering();
}

/** Кодирует AudioBuffer в WAV (PCM 16 бит) — понимают все ASR-серверы. */
export function encodeWav(buffer) {
  const channels = buffer.numberOfChannels;
  const rate = buffer.sampleRate;
  const samples = buffer.length;
  const blockAlign = channels * 2;
  const dataSize = samples * blockAlign;
  const view = new DataView(new ArrayBuffer(44 + dataSize));

  const writeStr = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);          // размер fmt-чанка
  view.setUint16(20, 1, true);           // PCM
  view.setUint16(22, channels, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);          // бит на семпл
  writeStr(36, "data");
  view.setUint32(40, dataSize, true);

  const data = [];
  for (let ch = 0; ch < channels; ch++) data.push(buffer.getChannelData(ch));

  let offset = 44;
  for (let i = 0; i < samples; i++) {
    for (let ch = 0; ch < channels; ch++) {
      const s = Math.max(-1, Math.min(1, data[ch][i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([view], { type: "audio/wav" });
}

export function formatTime(seconds) {
  if (!Number.isFinite(seconds)) return "00:00.000";
  const ms = Math.floor((seconds % 1) * 1000);
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
  return h > 0 ? `${String(h).padStart(2, "0")}:${mm}` : mm;
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
}
