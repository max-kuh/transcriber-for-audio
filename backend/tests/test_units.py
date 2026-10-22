"""Юнит-тесты чистых функций: сеть и Redis не нужны.

Запуск:  cd backend && python -m pytest tests -q
"""
import asyncio
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


def test_unbroken_whisper_text_is_split():
    # Whisper отдаёт текст одной строкой — раньше он уходил в LLM целиком
    text = "так значит поехали у нас интервью " * 2000
    chunks = llm._split(text, size=5000)
    assert len(chunks) > 1
    assert all(len(c) <= 5000 for c in chunks)
    assert "".join(chunks) == text


def test_split_prefers_sentence_boundaries():
    text = ("Первое предложение про Kafka. " * 100) + ("Второе про Redis. " * 100)
    chunks = llm._split(text, size=1000)
    assert all(c.rstrip().endswith(".") for c in chunks)


# --- нормализация -----------------------------------------------------------
def test_normalize_keeps_timestamps_and_falls_back(monkeypatch):
    async def fake_chat(messages, **kwargs):
        assert "СТРОКИ:" in messages[-1]["content"]
        # вторую строку модель «потеряла», третью — вернула с мусором вокруг
        return "Вот результат:\n1| Middle-аналитик, привет.\n3| Пишем в Kafka.\nспасибо"

    monkeypatch.setattr(llm, "_chat", fake_chat)
    segments = [
        Segment(start=0, end=1, text="металл аналитик привет"),
        Segment(start=1, end=2, text="это не трогаем"),
        Segment(start=2, end=3, text="пишем в кавку"),
    ]
    fixed, text = asyncio.run(llm.normalize(segments, "игнорируется"))
    assert [s.text for s in fixed] == ["Middle-аналитик, привет.", "это не трогаем", "Пишем в Kafka."]
    assert [(s.start, s.end) for s in fixed] == [(0, 1), (1, 2), (2, 3)]
    assert text == "Middle-аналитик, привет. это не трогаем Пишем в Kafka."


def test_normalize_batch_failure_keeps_original(monkeypatch):
    async def broken_chat(messages, **kwargs):
        raise llm.LLMError("LLM 500")

    monkeypatch.setattr(llm, "_chat", broken_chat)
    segments = [Segment(start=0, end=1, text="кавка")]
    fixed, text = asyncio.run(llm.normalize(segments, "кавка"))
    assert fixed[0].text == "кавка" and text == "кавка"


def test_glossary_is_bundled():
    assert "Kafka" in llm.glossary()
    assert not any(line.startswith("#") for line in llm.glossary().splitlines())


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
    assert options.normalize is None


def test_options_rejects_unknown_action():
    with pytest.raises(ValueError):
        JobOptions.model_validate({"post_action": "нет-такого"})


# --- ASR: зацикливание и нарезка ---------------------------------------------
def test_loop_score_separates_loop_from_speech():
    from app import asr

    speech = (
        "Клиент оформляет возврат в личном кабинете, затем сдаёт товар в пункт выдачи. "
        "Система возвратов проверяет заявку, пишет событие в Kafka, а сервис уведомлений "
        "сообщает клиенту о решении. Деньги возвращает платёжный шлюз банка-эквайера. "
    )
    assert asr.loop_score(speech) < 4.0
    assert asr.loop_score(speech + "вот так, " * 200) > 4.0


def test_cut_points_follow_pauses():
    from app import audio

    pauses = [(595.0, 597.0), (1210.0, 1212.0), (1790.0, 1792.0)]
    assert audio.cut_points(2400, pauses, 600) == [596.0, 1211.0, 1791.0]
    # без пауз режем ровно по сетке; хвост короче 1.5×target не отрезается
    assert audio.cut_points(1000, [], 600) == [600.0]
    assert audio.cut_points(800, [], 600) == []


def test_normalize_rejects_inflated_line(monkeypatch):
    async def looping_chat(messages, **kwargs):
        return "1| " + "вот так, " * 100

    monkeypatch.setattr(llm, "_chat", looping_chat)
    fixed, _ = asyncio.run(llm.normalize([Segment(start=0, end=1, text="вот так")], ""))
    assert fixed[0].text == "вот так"


# --- диаризация --------------------------------------------------------------
def test_diarize_assigns_by_max_overlap_and_numbers_by_appearance():
    from app import diarize

    turns = [(0.0, 5.0, 7), (5.0, 12.0, 3)]  # номера из сервиса произвольные
    segments = [
        Segment(start=0, end=4, text="Расскажите о себе."),
        Segment(start=4, end=11, text="Работаю аналитиком."),  # 1 с у первого, 6 с у второго
        Segment(start=13, end=14, text="Понятно."),  # вне речи -> ближайший (второй)
    ]
    out = diarize.assign(segments, turns)
    assert [s.speaker for s in out] == ["Спикер 1", "Спикер 2", "Спикер 2"]


def test_diarize_assign_without_turns_is_noop():
    from app import diarize

    segments = [Segment(start=0, end=1, text="раз")]
    assert diarize.assign(segments, []) == segments


def test_dialogue_merges_consecutive_replies():
    from app import diarize

    segments = [
        Segment(start=0, end=1, text="Привет.", speaker="Спикер 1"),
        Segment(start=1, end=2, text="Как дела?", speaker="Спикер 1"),
        Segment(start=2, end=3, text="Нормально.", speaker="Спикер 2"),
    ]
    assert diarize.dialogue(segments) == "Спикер 1: Привет. Как дела?\n\nСпикер 2: Нормально."


def test_normalize_keeps_speakers(monkeypatch):
    async def fake_chat(messages, **kwargs):
        return "1| Привет."

    monkeypatch.setattr(llm, "_chat", fake_chat)
    fixed, _ = asyncio.run(llm.normalize([Segment(start=0, end=1, text="привет", speaker="Спикер 1")], ""))
    assert fixed[0].speaker == "Спикер 1"


def test_normalize_token_budget_scales_with_batch(monkeypatch):
    seen = {}

    async def fake_chat(messages, max_tokens=None):
        seen["max_tokens"] = max_tokens
        return ""

    monkeypatch.setattr(llm, "_chat", fake_chat)
    asyncio.run(llm._normalize_lines(["слово " * 1000]))
    assert seen["max_tokens"] > 2048  # крупная пачка не должна обрезаться лимитом LLM_MAX_TOKENS


# --- режим конвейера --------------------------------------------------------
class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.config import settings
    from app.storage import Storage

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return Storage(FakeRedis())


def test_pipeline_mode_falls_back_to_env_default(store, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "pipeline_mode", "n8n")
    assert asyncio.run(store.get_pipeline_mode()) == "n8n"


def test_pipeline_mode_switch_applies_without_restart(store):
    async def scenario():
        before = await store.get_pipeline_mode()
        await store.set_pipeline_mode("n8n")
        return before, await store.get_pipeline_mode()

    assert asyncio.run(scenario()) == ("direct", "n8n")


def test_pipeline_mode_ignores_garbage_in_redis(store):
    store.redis.data["transcriber:pipeline_mode"] = "chaos"
    assert asyncio.run(store.get_pipeline_mode()) == "direct"


def test_pipeline_uses_mode_frozen_in_job(store, monkeypatch):
    """Переключение не меняет режим уже поставленной задачи."""
    from app import pipeline
    from app.config import settings

    called = []

    async def fake_n8n(job, path):
        called.append(job.id)
        return JobResult(text="из n8n")

    monkeypatch.setattr(pipeline, "_run_n8n", fake_n8n)
    monkeypatch.setattr(settings, "pipeline_mode", "direct")

    async def scenario():
        job = make_job(pipeline_mode="n8n")
        await store.save(job)
        store.upload_path(job.id, job.filename).write_bytes(b"x")
        await pipeline.process(job.id, store)
        return await store.get(job.id)

    done = asyncio.run(scenario())
    assert called == ["test"]
    assert done.status == "done" and done.result.text == "из n8n"


@pytest.mark.parametrize(
    ("requested", "default", "expected"),
    [(None, 2, 2), (3, 2, 3), (0, 2, None), (None, 0, None)],
)
def test_num_speakers_default(monkeypatch, requested, default, expected):
    from app import pipeline

    monkeypatch.setattr(pipeline.settings, "diarize_num_speakers", default)
    assert pipeline._num_speakers(JobOptions(num_speakers=requested)) == expected
