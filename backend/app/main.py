"""HTTP API сервиса Transcriber."""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from . import asr, llm
from .config import settings
from .schemas import Job, JobCreated, JobOptions
from .storage import Storage, make_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("transcriber.api")

CHUNK = 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = make_redis()
    app.state.storage = Storage(app.state.redis)
    app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    log.info("API запущен, режим конвейера: %s", settings.pipeline_mode)
    yield
    await app.state.redis.aclose()
    await app.state.queue.aclose()


app = FastAPI(
    title="Transcriber API",
    version="1.0.0",
    description="Обрезка в браузере → Whisper → LLM-постобработка → Telegram/Webhook",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    """Если API_KEY задан в .env — все /jobs требуют заголовок X-API-Key."""
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="неверный или отсутствующий X-API-Key")


def storage() -> Storage:
    return app.state.storage


# ---------------------------------------------------------------------------
# Служебные
# ---------------------------------------------------------------------------
@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok", "pipeline_mode": settings.pipeline_mode}


@app.get("/api/v1/health/deps")
async def health_deps() -> dict:
    return {"asr": await asr.health(), "llm": await llm.health()}


@app.get("/api/v1/config")
async def public_config() -> dict:
    """То, что нужно фронтенду, чтобы нарисовать форму."""
    return {
        "max_upload_mb": settings.max_upload_mb,
        "default_model": settings.whisper__model,
        "llm_model": settings.llm_model,
        "pipeline_mode": settings.pipeline_mode,
        "auth_required": bool(settings.api_key),
        "telegram_configured": bool(settings.telegram_bot_token),
        "webhook_configured": bool(settings.outbound_webhook_url),
    }


# ---------------------------------------------------------------------------
# Задачи
# ---------------------------------------------------------------------------
@app.post("/api/v1/jobs", response_model=JobCreated, status_code=202, dependencies=[Depends(require_key)])
async def create_job(
    file: UploadFile = File(..., description="Аудио/видео, как правило уже обрезанное в браузере"),
    options: str = Form("{}", description="JSON с параметрами (см. схему JobOptions)"),
    store: Storage = Depends(storage),
) -> JobCreated:
    try:
        parsed = JobOptions.model_validate(json.loads(options or "{}"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"некорректные options: {exc}") from exc

    job_id = uuid.uuid4().hex
    path = store.upload_path(job_id, file.filename or "audio.wav")

    size = 0
    with path.open("wb") as out:
        while chunk := await file.read(CHUNK):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"файл больше {settings.max_upload_mb} МБ")
            out.write(chunk)

    if size == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(422, "пустой файл")

    now = datetime.now(timezone.utc)
    job = Job(
        id=job_id,
        filename=file.filename or path.name,
        size_bytes=size,
        options=parsed,
        created_at=now,
        updated_at=now,
    )
    await store.save(job)
    await app.state.queue.enqueue_job("process_job", job_id)
    return JobCreated(id=job_id, status=job.status)


@app.get("/api/v1/jobs/{job_id}", response_model=Job, dependencies=[Depends(require_key)])
async def get_job(job_id: str, store: Storage = Depends(storage)) -> Job:
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(404, "задача не найдена или срок хранения истёк")
    return job


@app.get("/api/v1/jobs", response_model=list[Job], dependencies=[Depends(require_key)])
async def list_jobs(limit: int = 50, store: Storage = Depends(storage)) -> list[Job]:
    return await store.list_recent(limit)


@app.get("/api/v1/jobs/{job_id}/export", dependencies=[Depends(require_key)])
async def export_job(
    job_id: str, fmt: str = "txt", store: Storage = Depends(storage)
) -> PlainTextResponse:
    """fmt: txt | md | srt | vtt"""
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(404, "задача не найдена")

    if fmt == "txt":
        body = job.result.text
    elif fmt == "md":
        body = f"# {job.filename}\n\n"
        if job.result.post_output:
            body += f"## Обработанный текст\n\n{job.result.post_output}\n\n"
        body += f"## Расшифровка\n\n{job.result.text}\n"
    elif fmt in ("srt", "vtt"):
        body = _subtitles(job, fmt)
    else:
        raise HTTPException(422, "fmt: txt | md | srt | vtt")

    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="{job_id}.{fmt}"'},
    )


@app.post("/api/v1/jobs/{job_id}/redeliver", dependencies=[Depends(require_key)])
async def redeliver(job_id: str, store: Storage = Depends(storage)) -> dict:
    """Повторная отправка готового результата в назначения (например, Telegram упал)."""
    from . import delivery

    job = await store.get(job_id)
    if job is None:
        raise HTTPException(404, "задача не найдена")
    if job.status != "done":
        raise HTTPException(409, f"задача в статусе {job.status}")
    report = await delivery.dispatch(job)
    job.result.delivery = report
    await store.save(job)
    return report


def _ts(seconds: float, fmt: str) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if fmt == "srt" else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _subtitles(job: Job, fmt: str) -> str:
    if not job.result.segments:
        raise HTTPException(409, "нет сегментов: включите timestamps при создании задачи")
    lines = ["WEBVTT", ""] if fmt == "vtt" else []
    for index, segment in enumerate(job.result.segments, 1):
        if fmt == "srt":
            lines.append(str(index))
        lines.append(f"{_ts(segment.start, fmt)} --> {_ts(segment.end, fmt)}")
        prefix = f"[{segment.speaker}] " if segment.speaker else ""
        lines.append(prefix + segment.text)
        lines.append("")
    return "\n".join(lines)
