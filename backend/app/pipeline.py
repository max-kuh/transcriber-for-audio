"""Конвейер обработки задачи: ASR -> постобработка -> доставка.

Два режима (PIPELINE_MODE; режим фиксируется в задаче при создании, переключается в /admin):
  direct — этот сервис сам дёргает ASR, LLM и назначения доставки;
  n8n    — файл целиком отдаётся в n8n webhook, вся логика живёт в workflow.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from . import asr, audio, delivery, diarize, llm
from .config import settings
from .schemas import Job, JobResult, Segment
from .storage import Storage

log = logging.getLogger("transcriber.pipeline")

N8N_TIMEOUT = httpx.Timeout(connect=15.0, read=1800.0, write=600.0, pool=15.0)


async def _run_n8n(job: Job, path: Path) -> JobResult:
    """Отдаёт бинарник в n8n; workflow отвечает JSON вида {text, post_output, ...}."""
    headers = {}
    if settings.n8n_webhook_token:
        headers["X-Transcriber-Token"] = settings.n8n_webhook_token

    with path.open("rb") as fh:
        files = {"file": (job.filename or path.name, fh, "application/octet-stream")}
        data = {
            "job_id": job.id,
            "options": job.options.model_dump_json(),
        }
        async with httpx.AsyncClient(timeout=N8N_TIMEOUT) as client:
            response = await client.post(
                settings.n8n_webhook_url, data=data, files=files, headers=headers
            )
    if response.status_code >= 400:
        raise RuntimeError(f"n8n {response.status_code}: {response.text[:500]}")

    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text}
    if isinstance(payload, list) and payload:
        payload = payload[0]

    return JobResult(
        text=payload.get("text", ""),
        segments=[Segment(**s) for s in payload.get("segments", []) or []],
        language=payload.get("language"),
        duration=payload.get("duration"),
        post_output=payload.get("post_output") or payload.get("summary"),
        delivery=payload.get("delivery", {}),
    )


async def _no_turns() -> list:
    return []


async def process(job_id: str, storage: Storage) -> None:
    job = await storage.get(job_id)
    if job is None:
        log.warning("задача %s не найдена", job_id)
        return

    path = storage.upload_path(job.id, job.filename)
    try:
        if not path.exists():
            raise FileNotFoundError(f"файл задачи отсутствует: {path}")

        # job.pipeline_mode пуст у задач, поставленных до появления переключателя
        if (job.pipeline_mode or settings.pipeline_mode) == "n8n":
            await storage.update(job.id, status="transcribing", progress=10)
            result = await _run_n8n(job, path)
            await storage.update(job.id, status="done", progress=100, result=result)
            return

        normalize = (
            settings.normalize_default if job.options.normalize is None else job.options.normalize
        )
        want_diarize = (
            settings.diarize_default if job.options.diarize is None else job.options.diarize
        ) and diarize.available()

        # --- 0. Серверная подготовка: файл «как есть» / обрезка по времени ---
        source = path
        opts = job.options
        if opts.raw or opts.trim_start is not None or opts.trim_end is not None:
            await storage.update(job.id, status="transcribing", progress=5)
            prepared = path.with_name(path.stem + ".prepared.ogg")
            source = await audio.prepare(path, prepared, opts.trim_start, opts.trim_end)

        # --- 1. Распознавание (и диаризация параллельно) ---
        await storage.update(job.id, status="transcribing", progress=15)

        async def asr_progress(done: int, total: int) -> None:
            await storage.update(job.id, progress=15 + 35 * done // total)

        asr_result, turns = await asyncio.gather(
            asr.transcribe(
                source, job.options, with_segments=normalize or want_diarize, on_progress=asr_progress
            ),
            diarize.turns(source, opts.num_speakers) if want_diarize else _no_turns(),
        )
        result = JobResult(
            text=asr_result["text"],
            segments=[Segment(**s) for s in asr_result["segments"]],
            language=asr_result["language"],
            duration=asr_result["duration"],
        )
        if turns:
            result.segments = diarize.assign(result.segments, turns)
        job = await storage.update(job.id, progress=50, result=result)

        # --- 2. Нормализация: пунктуация и термины по глоссарию ---
        if normalize and result.text:
            await storage.update(job.id, status="postprocessing", progress=55)
            result.raw_text = result.text
            result.segments, result.text = await llm.normalize(result.segments, result.text)
            job = await storage.update(job.id, progress=70, result=result)
        if turns:
            # текст — это диалог по репликам; говорящие остаются и в сегментах (SRT/VTT)
            result.raw_text = result.raw_text or result.text
            result.text = diarize.dialogue(result.segments)
        if not job.options.timestamps:
            # сегменты запрашивались только ради нормализации/диаризации — в ответ не отдаём
            result.segments = []

        # --- 3. Постобработка ---
        if job.options.post_action != "none":
            await storage.update(job.id, status="postprocessing", progress=75)
            result.post_output = await llm.postprocess(result.text, job.options)
            job = await storage.update(job.id, progress=85, result=result)

        # --- 4. Доставка ---
        if job.options.destinations:
            await storage.update(job.id, status="delivering", progress=90)
            result.delivery = await delivery.dispatch(job)

        await storage.update(job.id, status="done", progress=100, result=result, error=None)

    except Exception as exc:  # noqa: BLE001 — ошибку показываем пользователю
        log.exception("задача %s провалилась", job_id)
        await storage.update(job.id, status="failed", error=str(exc)[:1000])
    finally:
        # Исходник больше не нужен: сервис не хранит аудио дольше необходимого
        path.unlink(missing_ok=True)
        path.with_name(path.stem + ".prepared.ogg").unlink(missing_ok=True)
