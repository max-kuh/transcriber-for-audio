# 00. Архитектура

## Компоненты

| Сервис | Образ / стек | Порт (хост) | Назначение |
|---|---|---|---|
| `frontend` | nginx + статика | 8080 | Редактор аудио и UI. Проксирует `/api/` на backend, поэтому CORS не нужен |
| `api` | FastAPI (Python 3.12) | 8000 | Приём файла, валидация, постановка в очередь, статус, экспорт |
| `worker` | тот же образ, `arq` | — | Долгие задачи: ASR → LLM → доставка |
| `redis` | redis:7 | — | Очередь задач и состояние (TTL = `RETENTION_HOURS`) |
| `asr` | `ghcr.io/speaches-ai/speaches` | 8001 | faster-whisper за OpenAI-совместимым API |
| `ollama` | `ollama/ollama` | 11434 | LLM для постобработки |
| `n8n` | `docker.n8n.io/n8nio/n8n` | 5678 | Визуальный оркестратор, альтернативный конвейер |

## Поток данных

1. **Браузер.** Файл читается через `FileReader` → `decodeAudioData`. Волна рисуется wavesurfer.js, выделение — плагином Regions. Все правки (`crop`, `cut`, `fade`, `normalize`) выполняются над `AudioBuffer` в памяти вкладки.
2. **Подготовка к отправке.** `OfflineAudioContext` пересчитывает буфер в 1 канал / 16 000 Гц — родной формат Whisper. Результат кодируется в WAV PCM16 (`encodeWav`). Часовая запись стерео-44.1 кГц (≈600 МБ WAV) превращается в ≈115 МБ, а после обрезки — обычно в единицы мегабайт.
3. **POST `/api/v1/jobs`.** Multipart: `file` (Blob) + `options` (JSON-строка). API стримит файл на диск чанками по 1 МБ, проверяя лимит `MAX_UPLOAD_MB`, создаёт запись задачи в Redis и кладёт `job_id` в очередь arq.
4. **Воркер.** См. `backend/app/pipeline.py`:
   - `direct`: `asr.transcribe()` → (опционально) `llm.postprocess()` → `delivery.dispatch()`;
   - `n8n`: файл целиком уходит в `N8N_WEBHOOK_URL`, workflow возвращает готовый JSON.
   В обеих ветках исходник удаляется в `finally`.
5. **Фронт** опрашивает `GET /api/v1/jobs/{id}` раз в 1.5 с, рисует прогресс, показывает текст, постобработку, сегменты и отчёт о доставке.

## Статусы задачи

```
queued → transcribing → postprocessing → delivering → done
                     ↘ (любой шаг) ─────────────────→ failed
```

`progress`: 15 → 60 (ASR) → 85 (LLM) → 100.

## Формат обмена

**Запрос** (`options`):

```json
{
  "language": "ru",
  "model": null,
  "prompt": "Кубернетес, ООО «Ромашка»",
  "timestamps": true,
  "post_action": "minutes",
  "post_instruction": null,
  "target_language": null,
  "destinations": ["telegram", "webhook"],
  "telegram_chat_id": "-1001234567890",
  "webhook_url": "https://example.com/hook",
  "meta": {"route": "crm", "deal_id": 42}
}
```

**Ответ доставки** (то, что прилетает в вашу ручку):

```json
{
  "job_id": "9f2c…",
  "filename": "meeting.wav",
  "language": "ru",
  "duration": 612.4,
  "text": "полная расшифровка…",
  "post_action": "minutes",
  "post_output": "## Принятые решения …",
  "segments": [{"start": 0.0, "end": 4.2, "text": "…", "speaker": null}],
  "meta": {"route": "crm", "deal_id": 42}
}
```

## Почему обрезка на клиенте

* **Приватность.** Черновая часть записи (то, что вы отрезали) никогда не покидает устройство.
* **Трафик и стоимость.** На VPS с ограниченным каналом загрузка часовых файлов ради 3 минут речи — главный источник тормозов.
* **Нагрузка.** Сервер не тратит CPU на декодирование и нарезку; ffmpeg в образе backend остаётся только как аварийный фолбэк.

Ограничение подхода: браузер декодирует только те кодеки, которые поддерживает сам (mp3, wav, ogg/opus, flac, aac/m4a, webm, mp4/mov). Экзотические контейнеры (mkv с редким кодеком, wma) нужно предварительно конвертировать — либо подключить `ffmpeg.wasm` (см. [08-troubleshooting.md](08-troubleshooting.md)).
