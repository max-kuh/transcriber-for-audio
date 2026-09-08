"""Постобработка текста через OpenAI-совместимый /v1/chat/completions
(Ollama, vLLM, LM Studio, llama.cpp server, OpenAI, OpenRouter)."""
from __future__ import annotations

import httpx

from . import prompts
from .config import settings
from .schemas import JobOptions

TIMEOUT = httpx.Timeout(connect=15.0, read=900.0, write=60.0, pool=15.0)

# Грубый лимит на объём, который отдаём модели за один заход (символы).
CHUNK_CHARS = 12_000


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


def _split(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Режет по абзацам, чтобы не рвать предложения посередине."""
    if len(text) <= size:
        return [text]
    chunks, current = [], ""
    for paragraph in text.split("\n"):
        if len(current) + len(paragraph) + 1 > size and current:
            chunks.append(current)
            current = ""
        current += paragraph + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


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
