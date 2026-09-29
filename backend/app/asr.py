"""Клиент ASR: любой OpenAI-совместимый /v1/audio/transcriptions
(Speaches, faster-whisper-server, whisper.cpp server, vLLM, облачный OpenAI/Groq)."""
from __future__ import annotations

import logging
import shutil
import zlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from . import audio
from .config import settings
from .schemas import JobOptions, Segment

log = logging.getLogger("transcriber.asr")

TIMEOUT = httpx.Timeout(connect=15.0, read=1800.0, write=600.0, pool=15.0)

# Температуры повторного распознавания зациклившегося куска
RETRY_TEMPERATURES = (0.4, 0.8)
LOOP_WINDOW = 400

ProgressCallback = Callable[[int, int], Awaitable[None]]


class ASRError(RuntimeError):
    pass


def loop_score(text: str) -> float:
    """Максимальный коэффициент сжатия по окнам текста.

    Обычная речь сжимается zlib в 2–2.6 раза, петля «вот так, вот так…» — в 10–20.
    """
    if len(text) < LOOP_WINDOW:
        windows = [text]
    else:
        step = LOOP_WINDOW // 2
        windows = [text[i : i + LOOP_WINDOW] for i in range(0, len(text) - step, step)]
    scores = [len(w.encode()) / len(zlib.compress(w.encode())) for w in windows if w.strip()]
    return max(scores, default=0.0)


async def _request(path: Path, options: JobOptions, *, verbose: bool, temperature: float | None = None,
                   use_hints: bool = True) -> dict[str, Any]:
    url = settings.asr_base_url.rstrip("/") + "/audio/transcriptions"
    headers: dict[str, str] = {}
    if settings.asr_api_key:
        headers["Authorization"] = f"Bearer {settings.asr_api_key}"

    data: dict[str, str] = {
        "model": options.model or settings.whisper__model,
        # verbose_json даёт сегменты с таймкодами; обычный json — только текст
        "response_format": "verbose_json" if verbose else "json",
    }
    if options.language:
        data["language"] = options.language
    if use_hints and (prompt := options.prompt or settings.asr_prompt):
        data["prompt"] = prompt
    if temperature is not None:
        data["temperature"] = str(temperature)
    # Не входят в контракт OpenAI — шлём, только если включены
    if settings.asr_vad_filter:
        data["vad_filter"] = "true"
    if use_hints and settings.asr_hotwords:
        data["hotwords"] = settings.asr_hotwords

    with path.open("rb") as fh:
        files = {"file": (path.name, fh, "application/octet-stream")}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(url, data=data, files=files, headers=headers)

    if response.status_code >= 400:
        raise ASRError(f"ASR {response.status_code}: {response.text[:500]}")

    payload = response.json()
    segments = [
        Segment(
            start=float(s.get("start", 0.0)),
            end=float(s.get("end", 0.0)),
            text=(s.get("text") or "").strip(),
            speaker=s.get("speaker"),
        ).model_dump()
        for s in payload.get("segments", []) or []
    ]
    return {
        "text": (payload.get("text") or "").strip(),
        "segments": segments,
        "language": payload.get("language"),
        "duration": payload.get("duration"),
    }


async def _request_robust(path: Path, options: JobOptions, *, verbose: bool) -> dict[str, Any]:
    """Распознаёт файл; если текст зациклился — повторяет с температурой, без prompt и hotwords.

    Speaches шлёт в faster-whisper одну температуру, и встроенный откат Whisper
    по compression_ratio не срабатывает, поэтому делаем его сами.
    """
    best = await _request(path, options, verbose=verbose)
    best_score = loop_score(best["text"])
    for temperature in RETRY_TEMPERATURES:
        if best_score <= settings.asr_loop_threshold:
            break
        log.warning("%s: зацикливание (%.1f), повтор с temperature=%s", path.name, best_score, temperature)
        retry = await _request(path, options, verbose=verbose, temperature=temperature, use_hints=False)
        if (score := loop_score(retry["text"])) < best_score:
            best, best_score = retry, score
    return best


async def transcribe(path: Path, options: JobOptions, *, with_segments: bool = False,
                     on_progress: ProgressCallback | None = None) -> dict[str, Any]:
    """Возвращает {text, segments, language, duration}.

    with_segments=True запрашивает таймкоды даже без options.timestamps (нужны нормализации).
    Запись длиннее 1.5 × ASR_CHUNK_SECONDS режется по паузам и распознаётся кусками.
    """
    verbose = options.timestamps or with_segments
    chunk = settings.asr_chunk_seconds
    total = await audio.duration(path) if chunk else None
    if not total or total <= chunk * 1.5:
        return await _request_robust(path, options, verbose=verbose)

    cuts = audio.cut_points(total, await audio.silences(path), chunk)
    bounds = list(zip([0.0, *cuts], [*cuts, total]))
    workdir = path.with_name(path.stem + ".chunks")
    workdir.mkdir(exist_ok=True)
    texts: list[str] = []
    segments: list[dict[str, Any]] = []
    language = None
    try:
        for index, (start, end) in enumerate(bounds):
            piece = await audio.extract(path, start, end, workdir / f"{index:03d}.ogg")
            # таймкоды нужны всегда: их надо сдвинуть на начало куска
            part = await _request_robust(piece, options, verbose=True)
            piece.unlink(missing_ok=True)
            texts.append(part["text"])
            language = language or part["language"]
            segments += [
                {**s, "start": s["start"] + start, "end": s["end"] + start} for s in part["segments"]
            ]
            if on_progress:
                await on_progress(index + 1, len(bounds))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return {
        "text": " ".join(t for t in texts if t),
        "segments": segments if verbose else [],
        "language": language,
        "duration": total,
    }


async def health() -> bool:
    url = settings.asr_base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return (await client.get(url)).status_code < 500
    except httpx.HTTPError:
        return False
