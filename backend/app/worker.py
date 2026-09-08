"""ARQ-воркер: снимает задачи из Redis и гоняет их через конвейер."""
from __future__ import annotations

import logging

from arq.connections import RedisSettings

from . import pipeline
from .config import settings
from .storage import Storage, make_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def process_job(ctx: dict, job_id: str) -> str:
    await pipeline.process(job_id, ctx["storage"])
    return job_id


async def cleanup(ctx: dict) -> None:
    removed = await ctx["storage"].purge_expired_files()
    if removed:
        logging.getLogger("transcriber.worker").info("удалено просроченных файлов: %s", removed)


async def startup(ctx: dict) -> None:
    ctx["redis_client"] = make_redis()
    ctx["storage"] = Storage(ctx["redis_client"])


async def shutdown(ctx: dict) -> None:
    await ctx["redis_client"].aclose()


class WorkerSettings:
    functions = [process_job]
    cron_jobs = []
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 2            # параллельные транскрипции: упирается в CPU/GPU ASR
    job_timeout = 3600      # час на длинную запись
    keep_result = 3600
