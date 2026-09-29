"""Постобработка текста через OpenAI-совместимый /v1/chat/completions
(Ollama, vLLM, LM Studio, llama.cpp server, OpenAI, OpenRouter)."""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import httpx

from . import prompts
from .config import settings
from .schemas import JobOptions, Segment

log = logging.getLogger("transcriber.llm")

TIMEOUT = httpx.Timeout(connect=15.0, read=900.0, write=60.0, pool=15.0)

DEFAULT_GLOSSARY = Path(__file__).with_name("glossary.txt")

# Конец предложения, после которого можно резать; и «слово + хвостовые пробелы»
_SENTENCE_END = re.compile(r"(?<=[.!?…])(?=\s)")
_WORD = re.compile(r"\S+\s*|\s+")
# Строка ответа нормализации: «12| текст»
_NUMBERED = re.compile(r"^\s*(\d+)\s*\|\s?(.*)$")


class LLMError(RuntimeError):
    pass


async def _chat(messages: list[dict[str, str]]) -> str:
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    body = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(url, json=body, headers=headers)
    if response.status_code >= 400:
        raise LLMError(f"LLM {response.status_code}: {response.text[:500]}")
    return response.json()["choices"][0]["message"]["content"].strip()


def _pieces(text: str, size: int) -> Iterator[str]:
    """Абзацы; слишком длинный абзац — по предложениям, затем по словам.

    Whisper отдаёт текст одной строкой без переводов, поэтому резать только
    по абзацам нельзя: часовая запись ушла бы в модель одним куском.
    """
    for paragraph in text.splitlines(keepends=True):
        if len(paragraph) <= size:
            yield paragraph
            continue
        for sentence in _SENTENCE_END.split(paragraph):
            if len(sentence) <= size:
                yield sentence
                continue
            for word in _WORD.findall(sentence):
                # слово без пробелов длиннее лимита — режем жёстко
                yield from (word[i : i + size] for i in range(0, len(word), size))


def _split(text: str, size: int | None = None) -> list[str]:
    """Делит текст на куски не длиннее size, не теряя ни одного символа."""
    size = size or settings.llm_chunk_chars
    if len(text) <= size:
        return [text]
    chunks, current = [], ""
    for piece in _pieces(text, size):
        if current and len(current) + len(piece) > size:
            chunks.append(current)
            current = ""
        current += piece
    if current.strip():
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Нормализация: пунктуация + исправление терминов по глоссарию
# ---------------------------------------------------------------------------
@lru_cache
def glossary() -> str:
    path = Path(settings.glossary_file) if settings.glossary_file else DEFAULT_GLOSSARY
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        log.warning("глоссарий %s не найден — нормализация без него", path)
        return ""
    return "\n".join(line for line in lines if line.strip() and not line.startswith("#"))


def _batches(texts: list[str], size: int) -> Iterator[list[int]]:
    """Группирует индексы сегментов в пачки примерно по size символов."""
    batch: list[int] = []
    length = 0
    for index, text in enumerate(texts):
        if batch and length + len(text) > size:
            yield batch
            batch, length = [], 0
        batch.append(index)
        length += len(text) + 8
    if batch:
        yield batch


def _plausible(original: str, fixed: str) -> bool:
    """Корректура почти не меняет длину. Раздувание — признак петли модели, сжатие — пересказа."""
    return 0.6 * len(original) - 20 <= len(fixed) <= 1.4 * len(original) + 30


async def _normalize_lines(lines: list[str]) -> list[str]:
    """Одна пачка. Строки, которые модель потеряла или сломала, остаются исходными."""
    numbered = "\n".join(f"{i}| {line}" for i, line in enumerate(lines, 1))
    answer = await _chat(
        [
            {"role": "system", "content": prompts.NORMALIZE_SYSTEM},
            {"role": "user", "content": prompts.build_normalize(numbered, glossary())},
        ]
    )
    result = list(lines)
    for row in answer.splitlines():
        match = _NUMBERED.match(row)
        if not match:
            continue
        index, text = int(match.group(1)) - 1, match.group(2).strip()
        if 0 <= index < len(result) and text and _plausible(result[index], text):
            result[index] = text
    return result


async def normalize(segments: list[Segment], text: str) -> tuple[list[Segment], str]:
    """Возвращает исправленные сегменты (таймкоды сохраняются) и собранный из них текст.

    Без сегментов нормализуется сплошной текст кусками.
    """
    size = settings.normalize_chunk_chars
    if not segments:
        parts = [(await _normalize_lines([chunk.strip()]))[0] for chunk in _split(text, size)]
        return [], " ".join(parts)

    texts = [s.text for s in segments]
    for batch in _batches(texts, size):
        try:
            fixed = await _normalize_lines([texts[i] for i in batch])
        except (LLMError, httpx.HTTPError) as exc:
            # одна неудачная пачка не должна валить всю задачу
            log.warning("нормализация пачки %s–%s пропущена: %s", batch[0], batch[-1], exc)
            continue
        for i, new_text in zip(batch, fixed):
            texts[i] = new_text

    normalized = [s.model_copy(update={"text": t}) for s, t in zip(segments, texts)]
    return normalized, " ".join(texts)


async def postprocess(text: str, options: JobOptions) -> str:
    """map-reduce: длинный текст обрабатывается частями, затем сводится."""
    if options.post_action == "none" or not text.strip():
        return ""

    chunks = _split(text)
    if len(chunks) == 1:
        return await _chat(
            [
                {"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": prompts.build(options, chunks[0])},
            ]
        )

    partials = []
    for index, chunk in enumerate(chunks, 1):
        partials.append(
            await _chat(
                [
                    {"role": "system", "content": prompts.SYSTEM},
                    {
                        "role": "user",
                        "content": f"Часть {index} из {len(chunks)}.\n"
                        + prompts.build(options, chunk),
                    },
                ]
            )
        )

    joined = "\n\n".join(partials)
    return await _chat(
        [
            {"role": "system", "content": prompts.SYSTEM},
            {
                "role": "user",
                "content": "Ниже — результаты обработки последовательных частей одной записи. "
                "Сведи их в единый непротиворечивый документ без повторов, "
                "сохранив исходную структуру.\n\n" + joined,
            },
        ]
    )


async def health() -> bool:
    url = settings.llm_base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return (await client.get(url)).status_code < 500
    except httpx.HTTPError:
        return False
