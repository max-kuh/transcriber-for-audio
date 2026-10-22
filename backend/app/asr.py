"""Клиент ASR: любой OpenAI-совместимый /v1/audio/transcriptions
(Speaches, faster-whisper-server, whisper.cpp server, vLLM, облачный OpenAI/Groq)."""
from __future__ import annotations

import logging
import re
import shutil
import zlib
from collections.abc import Awaitable, Callable
from functools import lru_cache
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

# Типовые «титры», которые Whisper вставляет на тишине и шуме (выучены на русских субтитрах).
# Регистр не важен; свои шаблоны добавляются файлом ASR_HALLUCINATION_FILE
DEFAULT_HALLUCINATIONS = (
    r"Редактор субтитров(?:\s+[А-ЯЁA-Z]\.\s*[А-ЯЁ][а-яё]+)?",
    r"Корректор\s+[А-ЯЁA-Z]\.\s*[А-ЯЁ][а-яё]+",
    r"Субтитры (?:сделал|создавал|делал|подготовил)\s+DimaTorzok",
    r"Субтитры создавались сообществом Amara\.org",
    r"Продолжение следует",
    r"Спасибо за просмотр",
    r"Подписывайтесь на (?:наш )?канал",
)
# Петля — n-грамма из 1–4 слов, повторённая подряд
MAX_NGRAM = 4
_PUNCT_TAIL = re.compile(r"[^\w]*$")
_NON_WORD = re.compile(r"[^\w]+")
_LEADING_PUNCT = re.compile(r"^[\s,;:.!?…—–-]+")


class ASRError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Очистка результата: галлюцинации-«титры» и петли из коротких фраз
# ---------------------------------------------------------------------------
@lru_cache
def hallucination_pattern() -> re.Pattern[str] | None:
    """Встроенные шаблоны + строки файла ASR_HALLUCINATION_FILE (по регулярке на строку, # — комментарий)."""
    patterns = list(DEFAULT_HALLUCINATIONS)
    if settings.asr_hallucination_file:
        path = Path(settings.asr_hallucination_file)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            log.warning("файл галлюцинаций %s не найден — только встроенный список", path)
            lines = []
        for line in (line.strip() for line in lines):
            if not line or line.startswith("#"):
                continue
            try:
                re.compile(line)
            except re.error as exc:
                log.warning("шаблон галлюцинации %r пропущен: %s", line, exc)
                continue
            patterns.append(line)
    if not patterns:
        return None
    # фраза целиком, вместе с хвостовой пунктуацией («Продолжение следует...»)
    body = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(rf"\b(?:{body})(?!\w)[.!?…]*", re.IGNORECASE)


def drop_hallucinations(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Вырезает фразы-галлюцинации из сегментов; опустевший сегмент удаляется."""
    pattern = hallucination_pattern()
    if pattern is None:
        return segments
    out = []
    for segment in segments:
        text, hits = pattern.subn("", segment["text"])
        if hits:
            text = _LEADING_PUNCT.sub("", re.sub(r"\s{2,}", " ", text)).strip()
            if not re.search(r"\w", text):
                continue
            segment = {**segment, "text": text}
        out.append(segment)
    return out


def collapse_repeats(segments: list[dict[str, Any]], threshold: int, keep: int = 1) -> list[dict[str, Any]]:
    """Схлопывает петли: n-грамма (1–4 слова), повторённая подряд ≥ threshold раз, остаётся keep раз.

    Повтор ищется по словам всех сегментов подряд, поэтому ловится и петля через границы
    сегментов. Слова сравниваются без регистра и пунктуации. Таймкоды оставшихся сегментов
    не меняются; сегмент, из которого ушли все слова, удаляется.
    """
    if threshold < 2:
        return segments
    keep = max(1, min(keep, threshold - 1))
    words = [s["text"].split() for s in segments]
    flat = [(si, wi) for si, ws in enumerate(words) for wi in range(len(ws))]
    keys = [_NON_WORD.sub("", words[si][wi].lower()) for si, wi in flat]

    drop: set[int] = set()
    tails: dict[int, str] = {}  # слово -> новая хвостовая пунктуация
    i = 0
    while i < len(keys):
        best_n, best_k = 0, 0
        for n in range(1, MAX_NGRAM + 1):
            if i + n * threshold > len(keys):
                break
            gram = keys[i : i + n]
            if "" in gram:
                continue
            k = 1
            while keys[i + k * n : i + (k + 1) * n] == gram:
                k += 1
            if k >= threshold and k * n > best_k * best_n:
                best_n, best_k = n, k
        if not best_n:
            i += 1
            continue
        kept_end, run_end = i + keep * best_n, i + best_k * best_n
        drop.update(range(kept_end, run_end))
        # последнему оставленному слову — пунктуацию конца петли («REST API, … REST API.» → «REST API.»)
        si, wi = flat[run_end - 1]
        tails[kept_end - 1] = _PUNCT_TAIL.search(words[si][wi]).group()
        log.info("петля «%s» ×%d схлопнута до %d", " ".join(keys[i : i + best_n]), best_k, keep)
        i = run_end

    if not drop:
        return segments
    changed: dict[int, list[str]] = {}
    for index, (si, wi) in enumerate(flat):
        if index in tails:
            word = changed.setdefault(si, list(words[si]))[wi]
            changed[si][wi] = word[: len(word) - len(_PUNCT_TAIL.search(word).group())] + tails[index]
        if index in drop:
            changed.setdefault(si, list(words[si]))[wi] = ""
    out = []
    for si, segment in enumerate(segments):
        if si in changed:
            text = " ".join(w for w in changed[si] if w)
            if not text:
                continue
            segment = {**segment, "text": text}
        out.append(segment)
    return out


def clean(result: dict[str, Any]) -> dict[str, Any]:
    """Применяет фильтр галлюцинаций и схлопывание петель к результату ASR.

    Есть сегменты — чистятся они, текст пересобирается из них; нет — текст как один сегмент.
    """
    def run(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if settings.asr_hallucination_filter:
            segments = drop_hallucinations(segments)
        return collapse_repeats(segments, settings.asr_repeat_threshold, settings.asr_repeat_keep)

    if result["segments"]:
        segments = run(result["segments"])
        if segments == result["segments"]:
            return result
        return {**result, "segments": segments, "text": " ".join(s["text"] for s in segments)}
    if not result["text"]:
        return result
    cleaned = run([{"start": 0.0, "end": 0.0, "text": result["text"]}])
    text = cleaned[0]["text"] if cleaned else ""
    return result if text == result["text"] else {**result, "text": text}


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
        return clean(await _request_robust(path, options, verbose=verbose))

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

    # чистим после склейки: петля может переходить через границу кусков
    result = clean({"text": " ".join(t for t in texts if t), "segments": segments})
    return {
        "text": result["text"],
        "segments": result["segments"] if verbose else [],
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
