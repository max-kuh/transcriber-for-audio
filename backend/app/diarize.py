"""Диаризация: кто говорит. Клиент сервиса diarizer/ и слияние его отрезков с сегментами ASR.

У диаризации нет общего OpenAI-контракта, поэтому у сервиса свой минимальный:
POST {DIARIZE_BASE_URL}/diarize (multipart: file, num_speakers) -> {"turns": [{start, end, speaker}]}.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from .config import settings
from .schemas import Segment

log = logging.getLogger("transcriber.diarize")

TIMEOUT = httpx.Timeout(connect=15.0, read=1800.0, write=600.0, pool=15.0)

Turn = tuple[float, float, int]  # начало, конец, номер говорящего (с 0)


def available() -> bool:
    return bool(settings.diarize_base_url)


async def turns(path: Path, num_speakers: int | None = None) -> list[Turn]:
    """Отрезки речи по говорящим. Любая ошибка сервиса -> пустой список (задача не падает)."""
    if not available():
        return []
    url = settings.diarize_base_url.rstrip("/") + "/diarize"
    data = {"num_speakers": str(num_speakers)} if num_speakers else {}
    try:
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "application/octet-stream")}
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(url, data=data, files=files)
        response.raise_for_status()
        return [(float(t["start"]), float(t["end"]), int(t["speaker"])) for t in response.json()["turns"]]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("диаризация пропущена: %s", exc)
        return []


def assign(segments: list[Segment], diarized: list[Turn]) -> list[Segment]:
    """Каждому сегменту — говорящий с наибольшим пересечением по времени.

    Сегмент в паузе диаризатора получает ближайшего по времени. Метки — «Спикер 1…»
    в порядке первого появления в записи.
    """
    if not diarized or not segments:
        return segments

    def pick(seg: Segment) -> int:
        overlap: dict[int, float] = {}
        for start, end, who in diarized:
            common = min(seg.end, end) - max(seg.start, start)
            if common > 0:
                overlap[who] = overlap.get(who, 0.0) + common
        if overlap:
            return max(overlap, key=overlap.get)
        mid = (seg.start + seg.end) / 2
        return min(diarized, key=lambda t: min(abs(mid - t[0]), abs(mid - t[1])))[2]

    order: dict[int, int] = {}
    result = []
    for seg in segments:
        who = pick(seg)
        order.setdefault(who, len(order) + 1)
        result.append(seg.model_copy(update={"speaker": f"Спикер {order[who]}"}))
    return result


def dialogue(segments: list[Segment]) -> str:
    """Реплики подряд одного говорящего склеиваются: «Спикер 1: …» с пустой строкой между репликами."""
    blocks: list[tuple[str | None, list[str]]] = []
    for seg in segments:
        if not seg.text:
            continue
        if blocks and blocks[-1][0] == seg.speaker:
            blocks[-1][1].append(seg.text)
        else:
            blocks.append((seg.speaker, [seg.text]))
    return "\n\n".join(
        (f"{speaker}: " if speaker else "") + " ".join(texts) for speaker, texts in blocks
    )


async def health() -> bool:
    if not available():
        return True  # не настроена — не ошибка
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return (await client.get(settings.diarize_base_url.rstrip("/") + "/health")).status_code < 500
    except httpx.HTTPError:
        return False
