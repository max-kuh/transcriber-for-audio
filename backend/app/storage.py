"""Хранилище задач: метаданные в Redis, файлы на диске."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import redis.asyncio as aioredis

from .config import settings
from .schemas import Job

JOB_PREFIX = "transcriber:job:"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Storage:
    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis
        self.root = Path(settings.data_dir)
        (self.root / "uploads").mkdir(parents=True, exist_ok=True)
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)

    # --- файлы ---------------------------------------------------------------
    def upload_path(self, job_id: str, filename: str) -> Path:
        suffix = Path(filename).suffix[:10] or ".bin"
        return self.root / "uploads" / f"{job_id}{suffix}"

    # --- задачи --------------------------------------------------------------
    async def save(self, job: Job) -> Job:
        job.updated_at = _now()
        ttl = settings.retention_hours * 3600
        await self.redis.set(JOB_PREFIX + job.id, job.model_dump_json(), ex=ttl)
        return job

    async def get(self, job_id: str) -> Job | None:
        raw = await self.redis.get(JOB_PREFIX + job_id)
        return Job.model_validate_json(raw) if raw else None

    async def update(self, job_id: str, **fields) -> Job | None:
        job = await self.get(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            setattr(job, key, value)
        return await self.save(job)

    async def list_recent(self, limit: int = 50) -> list[Job]:
        jobs: list[Job] = []
        async for key in self.redis.scan_iter(match=JOB_PREFIX + "*", count=200):
            raw = await self.redis.get(key)
            if raw:
                jobs.append(Job.model_validate_json(raw))
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    async def purge_expired_files(self) -> int:
        """Удаляет исходники старше RETENTION_HOURS."""
        cutoff = time.time() - settings.retention_hours * 3600
        removed = 0
        for path in (self.root / "uploads").iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed


def make_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)
