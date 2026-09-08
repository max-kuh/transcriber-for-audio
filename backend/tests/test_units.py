"""Юнит-тесты чистых функций: сеть и Redis не нужны.

Запуск:  cd backend && python -m pytest tests -q
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app import llm, prompts
from app.delivery import _split_for_telegram
from app.main import _subtitles, _ts
from app.schemas import Job, JobOptions, JobResult, Segment


def make_job(**kwargs) -> Job:
    now = datetime.now(timezone.utc)
    return Job(id="test", created_at=now, updated_at=now, **kwargs)


# --- таймкоды ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("seconds", "fmt", "expected"),
    [
        (0, "srt", "00:00:00,000"),
        (1.5, "srt", "00:00:01,500"),
        (3661.25, "srt", "01:01:01,250"),
        (1.5, "vtt", "00:00:01.500"),
    ],
)
def test_timestamp_format(seconds, fmt, expected):
    assert _ts(seconds, fmt) == expected


# --- субтитры ---------------------------------------------------------------
def test_srt_structure():
    job = make_job(
        result=JobResult(
            text="привет мир",
            segments=[
                Segment(start=0, end=1.5, text="Привет"),
                Segment(start=1.5, end=3, text="мир", speaker="SPEAKER_01"),
            ],
        )
    )
    srt = _subtitles(job, "srt")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500\nПривет")
    assert "[SPEAKER_01] мир" in srt


def test_vtt_has_header():
    job = make_job(result=JobResult(segments=[Segment(start=0, end=1, text="раз")]))
    assert _subtitles(job, "vtt").startswith("WEBVTT")


def test_subtitles_without_segments_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _subtitles(make_job(), "srt")


# --- Telegram ---------------------------------------------------------------
def test_telegram_split_respects_limit():
    parts = _split_for_telegram("строка текста\n" * 2000)
    assert len(parts) > 1
    assert all(len(p) <= 4000 for p in parts)


def test_telegram_split_breaks_unbroken_line():
    parts = _split_for_telegram("x" * 10_000)
    assert all(len(p) <= 4000 for p in parts)
    assert sum(len(p) for p in parts) >= 10_000


# --- разбиение для LLM ------------------------------------------------------
def test_short_text_is_single_chunk():
    assert llm._split("короткий текст") == ["короткий текст"]


def test_long_text_split_keeps_all_content():
    text = "\n".join(f"абзац номер {i} " * 20 for i in range(400))
    chunks = llm._split(text, size=5000)
    assert len(chunks) > 1
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


# --- промпты ----------------------------------------------------------------
@pytest.mark.parametrize("action", ["summary", "minutes", "bullets", "translate", "custom"])
def test_every_action_builds_prompt(action):
    options = JobOptions(post_action=action, post_instruction="сделай таблицу", target_language="English")
    built = prompts.build(options, "исходный текст")
    assert "исходный текст" in built


# --- схема ------------------------------------------------------------------
def test_options_defaults():
    options = JobOptions()
    assert options.post_action == "none"
    assert options.destinations == []
    assert options.timestamps is False


def test_options_rejects_unknown_action():
    with pytest.raises(ValueError):
        JobOptions.model_validate({"post_action": "нет-такого"})
