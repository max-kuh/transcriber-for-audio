"""Контракты API."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "transcribing", "postprocessing", "delivering", "done", "failed"]
PostAction = Literal["none", "summary", "minutes", "bullets", "translate", "custom"]
Destination = Literal["none", "telegram", "webhook", "n8n"]


class JobOptions(BaseModel):
    """Параметры обработки, приходят вместе с файлом (поле `options`, JSON-строка)."""

    language: str | None = Field(None, description="ISO-код языка (ru, en). None = автоопределение")
    model: str | None = Field(None, description="Переопределить модель Whisper")
    prompt: str | None = Field(None, description="initial_prompt — подсказка с терминами/именами")
    timestamps: bool = Field(False, description="Вернуть сегменты с таймкодами")
    diarize: bool = Field(False, description="Разделение по говорящим (если ASR поддерживает)")

    post_action: PostAction = "none"
    post_instruction: str | None = Field(None, description="Своя инструкция для post_action=custom")
    target_language: str | None = Field(None, description="Язык перевода для post_action=translate")

    destinations: list[Destination] = Field(default_factory=list)
    telegram_chat_id: str | None = None
    webhook_url: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict, description="Произвольные данные, пробрасываются в доставку")


class Segment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None = None


class JobResult(BaseModel):
    text: str = ""
    segments: list[Segment] = Field(default_factory=list)
    language: str | None = None
    duration: float | None = None
    post_output: str | None = None
    delivery: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    id: str
    status: JobStatus = "queued"
    progress: int = 0
    filename: str = ""
    size_bytes: int = 0
    options: JobOptions = Field(default_factory=JobOptions)
    result: JobResult = Field(default_factory=JobResult)
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobCreated(BaseModel):
    id: str
    status: JobStatus
