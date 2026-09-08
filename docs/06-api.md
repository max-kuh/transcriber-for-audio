# 06. HTTP API

База: `http://localhost:8000/api/v1` (через фронтенд-прокси — `http://localhost:8080/api/v1`).
Интерактивная документация: <http://localhost:8000/docs>.

Если в `.env` задан `API_KEY`, все методы `/jobs*` требуют заголовок `X-API-Key`.

---

## `GET /health`

```json
{"status": "ok", "pipeline_mode": "direct"}
```

## `GET /health/deps`

```json
{"asr": true, "llm": true}
```

Используйте как liveness/readiness-проверку в мониторинге.

## `GET /config`

Публичная конфигурация для фронтенда: лимит загрузки, имена моделей, включена ли авторизация, настроены ли Telegram и webhook.

---

## `POST /jobs`

Создаёт задачу. `multipart/form-data`:

| Поле | Тип | Описание |
|---|---|---|
| `file` | binary | Аудио или видео. Как правило — уже обрезанный WAV из браузера |
| `options` | string | JSON, см. ниже. Необязательно |

**`options`:**

| Ключ | Тип | По умолчанию | Описание |
|---|---|---|---|
| `language` | string / null | null | `ru`, `en`… null = автоопределение |
| `model` | string / null | null | Переопределить модель Whisper |
| `prompt` | string / null | null | Подсказка распознавателю: имена, термины |
| `timestamps` | bool | false | Вернуть сегменты (нужно для SRT/VTT) |
| `post_action` | enum | `none` | `none` `summary` `minutes` `bullets` `translate` `custom` |
| `post_instruction` | string | null | Инструкция для `custom` |
| `target_language` | string | null | Для `translate` |
| `destinations` | string[] | `[]` | `telegram` `webhook` `n8n` |
| `telegram_chat_id` | string | null | Перекрывает `TELEGRAM_CHAT_ID` |
| `webhook_url` | string | null | Перекрывает `OUTBOUND_WEBHOOK_URL` |
| `meta` | object | `{}` | Произвольные данные, возвращаются в доставке |

**Ответ `202`:**

```json
{"id": "9f2c1d…", "status": "queued"}
```

Ошибки: `413` — файл больше `MAX_UPLOAD_MB`; `422` — пустой файл или битые `options`; `401` — нет/неверный `X-API-Key`.

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: $API_KEY" \
  -F "file=@meeting.wav" \
  -F 'options={"language":"ru","timestamps":true,"post_action":"minutes","destinations":["telegram"],"meta":{"deal_id":42}}'
```

## `GET /jobs/{id}`

Полное состояние задачи: `status`, `progress`, `result.text`, `result.segments`, `result.post_output`, `result.delivery`, `error`.

Опрашивайте раз в 1–2 секунды до `done` или `failed`. Задача живёт `RETENTION_HOURS` часов, затем возвращается `404`.

## `GET /jobs?limit=50`

Последние задачи, новые сверху.

## `GET /jobs/{id}/export?fmt=txt|md|srt|vtt`

Отдаёт файл с `Content-Disposition: attachment`. `srt` и `vtt` требуют, чтобы задача создавалась с `timestamps: true`, иначе `409`.

## `POST /jobs/{id}/redeliver`

Повторная отправка готового результата во все назначения задачи — например, если Telegram был недоступен. Возвращает отчёт доставки.

---

## Интеграция: приём результата своей ручкой

Укажите `destinations: ["webhook"]` и `webhook_url`. Ваш эндпоинт получит `POST application/json`:

```json
{
  "job_id": "9f2c1d…",
  "filename": "meeting.wav",
  "language": "ru",
  "duration": 612.4,
  "text": "…",
  "post_action": "minutes",
  "post_output": "## Принятые решения…",
  "segments": [{"start": 0.0, "end": 4.2, "text": "…", "speaker": null}],
  "meta": {"deal_id": 42}
}
```

Заголовок `Authorization: Bearer <OUTBOUND_WEBHOOK_TOKEN>`, если токен задан. Проверяйте его на своей стороне.

Минимальный приёмник:

```python
from fastapi import FastAPI, Header, HTTPException, Request
import os

app = FastAPI()

@app.post("/hook")
async def hook(request: Request, authorization: str = Header("")):
    if authorization != f"Bearer {os.environ['TOKEN']}":
        raise HTTPException(401)
    data = await request.json()
    print(data["job_id"], data["post_output"] or data["text"])
    return {"ok": True}
```

## Интеграция: скриптом без UI

```bash
#!/usr/bin/env bash
set -euo pipefail
API=http://localhost:8000/api/v1
KEY=$API_KEY

# нарезка на стороне скрипта (для UI это делает браузер)
ffmpeg -y -i "$1" -ss 00:01:30 -to 00:12:00 -ar 16000 -ac 1 /tmp/clip.wav

ID=$(curl -sf -H "X-API-Key: $KEY" -X POST "$API/jobs" \
  -F "file=@/tmp/clip.wav" \
  -F 'options={"language":"ru","post_action":"summary","destinations":["telegram"]}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

while :; do
  S=$(curl -sf -H "X-API-Key: $KEY" "$API/jobs/$ID" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  [ "$S" = done ] && break
  [ "$S" = failed ] && { echo "ошибка"; exit 1; }
  sleep 2
done

curl -sf -H "X-API-Key: $KEY" "$API/jobs/$ID/export?fmt=md" -o result.md
```
