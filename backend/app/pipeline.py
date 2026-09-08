"""Конвейер обработки задачи: ASR -> постобработка -> доставка.

Два режима (PIPELINE_MODE):
  direct — этот сервис сам дёргает ASR, LLM и назначения доставки;
  n8n    — файл целиком отдаётся в n8n webhook, вся логика живёт в workflow.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from . import asr, delivery, llm
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


async def process(job_id: str, storage: Storage) -> None:
    job = await storage.get(job_id)
    if job is None:
        log.warning("задача %s не найдена", job_id)
        return

    path = storage.upload_path(job.id, job.filename)
    try:
        if not path.exists():
            raise FileNotFoundError(f"файл задачи отсутствует: {path}")

        if settings.pipeline_mode == "n8n":
            await storage.update(job.id, status="transcribing", progress=10)
            result = await _run_n8n(job, path)
            await storage.update(job.id, status="done", progress=100, result=result)
            return

        # --- 1. Распознавание ---
        await storage.update(job.id, status="transcribing", progress=15)
        asr_result = await asr.transcribe(path, job.options)
        result = JobResult(
            text=asr_result["text"],
            segments=[Segment(**s) for s in asr_result["segments"]],
            language=asr_result["language"],
            duration=asr_result["duration"],
        )
        job = await storage.update(job.id, progress=60, result=result)

        # --- 2. Постобработка ---
        if job.options.post_action != "none":
            await storage.update(job.id, status="postprocessing", progress=70)
            result.post_output = await llm.postprocess(result.text, job.options)
            job = await storage.update(job.id, progress=85, result=result)

        # --- 3. Доставка ---
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
