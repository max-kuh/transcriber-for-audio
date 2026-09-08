"""Доставка результата: Telegram, произвольный webhook, n8n."""
from __future__ import annotations

from typing import Any

import httpx

from .config import settings
from .schemas import Job

TIMEOUT = httpx.Timeout(30.0)
TELEGRAM_LIMIT = 4096  # максимум символов в одном сообщении


def _split_for_telegram(text: str, limit: int = TELEGRAM_LIMIT - 100) -> list[str]:
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # аномально длинная строка без переносов
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current)
    return parts or [""]


async def to_telegram(job: Job, text: str) -> dict[str, Any]:
    token = settings.telegram_bot_token
    chat_id = job.options.telegram_chat_id or settings.telegram_chat_id
    if not token or not chat_id:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN или chat_id не задан"}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    header = f"🎙 <b>{job.filename or job.id}</b>"
    chunks = _split_for_telegram(text)
    sent = 0
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for index, chunk in enumerate(chunks):
            body = (header + "\n\n" + chunk) if index == 0 else chunk
            response = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": body,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if response.status_code >= 400:
                return {"ok": False, "error": response.text[:300], "sent": sent}
            sent += 1
    return {"ok": True, "messages": sent, "chat_id": chat_id}


async def to_webhook(job: Job, text: str) -> dict[str, Any]:
    url = job.options.webhook_url or settings.outbound_webhook_url
    if not url:
        return {"ok": False, "error": "webhook_url не задан"}

    headers = {"Content-Type": "application/json"}
    if settings.outbound_webhook_token:
        headers["Authorization"] = f"Bearer {settings.outbound_webhook_token}"

    payload = {
        "job_id": job.id,
        "filename": job.filename,
        "language": job.result.language,
        "duration": job.result.duration,
        "text": job.result.text,
        "post_action": job.options.post_action,
        "post_output": job.result.post_output,
        "segments": [s.model_dump() for s in job.result.segments],
        "meta": job.options.meta,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(url, json=payload, headers=headers)
    return {"ok": response.status_code < 400, "status": response.status_code, "url": url}


async def to_n8n(job: Job, text: str) -> dict[str, Any]:
    """Отдаёт готовый результат в n8n webhook (режим «n8n как постобработчик»)."""
    if not settings.n8n_webhook_url:
        return {"ok": False, "error": "N8N_WEBHOOK_URL не задан"}
    headers = {"Content-Type": "application/json"}
    if settings.n8n_webhook_token:
        headers["X-Transcriber-Token"] = settings.n8n_webhook_token
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            settings.n8n_webhook_url,
            json={
                "job_id": job.id,
                "filename": job.filename,
                "text": job.result.text,
                "post_output": job.result.post_output,
                "options": job.options.model_dump(),
                "meta": job.options.meta,
            },
            headers=headers,
        )
    return {"ok": response.status_code < 400, "status": response.status_code}


HANDLERS = {"telegram": to_telegram, "webhook": to_webhook, "n8n": to_n8n}


async def dispatch(job: Job) -> dict[str, Any]:
    """Отправляет результат во все выбранные назначения; ошибка одного не роняет остальные."""
    text = job.result.post_output or job.result.text
    report: dict[str, Any] = {}
    for destination in job.options.destinations:
        if destination == "none":
            continue
        handler = HANDLERS.get(destination)
        if handler is None:
            report[destination] = {"ok": False, "error": "неизвестное назначение"}
            continue
        try:
            report[destination] = await handler(job, text)
        except Exception as exc:  # noqa: BLE001 — доставка не должна ронять задачу
            report[destination] = {"ok": False, "error": str(exc)[:300]}
    return report
