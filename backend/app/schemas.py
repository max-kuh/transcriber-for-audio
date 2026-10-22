"""Контракты API."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "transcribing", "postprocessing", "delivering", "done", "failed"]
PostAction = Literal["none", "summary", "minutes", "bullets", "translate", "custom"]
Destination = Literal["none", "telegram", "webhook", "n8n"]
PipelineMode = Literal["direct", "n8n"]


class JobOptions(BaseModel):
    """Параметры обработки, приходят вместе с файлом (поле `options`, JSON-строка)."""

    language: str | None = Field(None, description="ISO-код языка (ru, en). None = автоопределение")
    model: str | None = Field(None, description="Переопределить модель Whisper")
    prompt: str | None = Field(None, description="initial_prompt — подсказка с терминами/именами")
    timestamps: bool = Field(False, description="Вернуть сегменты с таймкодами")
    diarize: bool | None = Field(
        None, description="Разделение по говорящим (сервис diarizer). None = DIARIZE_DEFAULT"
    )
    num_speakers: int | None = Field(None, ge=1, le=20, description="Число говорящих, если известно")
    raw: bool = Field(False, description="Файл загружен как есть: сервер сам извлечёт и сожмёт звук")
    trim_start: float | None = Field(None, ge=0, description="Начало фрагмента, с (серверная обрезка)")
    trim_end: float | None = Field(None, ge=0, description="Конец фрагмента, с (серверная обрезка)")
    normalize: bool | None = Field(
        None, description="LLM-нормализация: пунктуация и термины по глоссарию. None = NORMALIZE_DEFAULT"
    )

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
    raw_text: str | None = Field(None, description="Текст ASR до нормализации (если она была)")
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
    pipeline_mode: PipelineMode | None = Field(
        None, description="Режим конвейера, зафиксированный при создании задачи"
    )
    result: JobResult = Field(default_factory=JobResult)
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobCreated(BaseModel):
    id: str
    status: JobStatus


class PipelineModeBody(BaseModel):
    mode: PipelineMode


class PipelineModeState(BaseModel):
    mode: PipelineMode = Field(description="Действующий режим для новых задач")
    default: PipelineMode = Field(description="PIPELINE_MODE из .env — используется, пока режим не переключали")
