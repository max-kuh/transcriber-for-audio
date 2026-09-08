"""Клиент ASR: любой OpenAI-совместимый /v1/audio/transcriptions
(Speaches, faster-whisper-server, whisper.cpp server, vLLM, облачный OpenAI/Groq)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .schemas import JobOptions, Segment

TIMEOUT = httpx.Timeout(connect=15.0, read=1800.0, write=600.0, pool=15.0)


class ASRError(RuntimeError):
    pass


async def transcribe(path: Path, options: JobOptions) -> dict[str, Any]:
    """Возвращает {text, segments, language, duration}."""
    url = settings.asr_base_url.rstrip("/") + "/audio/transcriptions"
    headers: dict[str, str] = {}
    if settings.asr_api_key:
        headers["Authorization"] = f"Bearer {settings.asr_api_key}"

    data: dict[str, str] = {
        "model": options.model or settings.whisper__model,
        # verbose_json даёт сегменты с таймкодами; обычный json — только текст
        "response_format": "verbose_json" if options.timestamps else "json",
    }
    if options.language:
        data["language"] = options.language
    if options.prompt:
        data["prompt"] = options.prompt

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


async def health() -> bool:
    url = settings.asr_base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return (await client.get(url)).status_code < 500
    except httpx.HTTPError:
        return False
