# 04. n8n: настройка и разбор workflow

n8n — open-source оркестратор с визуальным редактором. Здесь он играет две роли:

* **основной конвейер** (`PIPELINE_MODE=n8n`) — backend превращается в тонкий приёмник файлов, вся логика живёт в workflow;
* **маршрутизатор результата** (`PIPELINE_MODE=direct` + назначение `n8n`) — Python делает тяжёлую работу, n8n раскладывает готовый текст по системам.

---

## 1. Первый вход

1. Откройте <http://localhost:5678>.
2. n8n попросит создать локальную учётную запись владельца (это внутренняя учётка, не облако). Отказаться от облачной регистрации можно ссылкой «skip».
3. Панель дополнительно закрыта basic-auth из `.env` (`N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD`).

Важные переменные, уже проброшенные в контейнер (`docker-compose.yml`):

| Переменная | Зачем |
|---|---|
| `N8N_ENCRYPTION_KEY` | Ключ шифрования credentials. Потеряете — все сохранённые токены станут нечитаемыми |
| `WEBHOOK_URL` | Внешний адрес, который n8n подставляет в URL вебхуков. На VPS: `https://n8n.example.com/` |
| `N8N_DEFAULT_BINARY_DATA_MODE=filesystem` | Бинарники (аудио) хранятся на диске, а не в памяти — иначе большие файлы кладут процесс |
| `N8N_RUNNERS_ENABLED=true` | Code-узлы исполняются в изолированном runner-процессе |
| `GENERIC_TIMEZONE` | Таймзона расписаний |

Весь `.env` проброшен через `env_file`, поэтому в узлах доступны выражения вида `{{ $env.ASR_BASE_URL }}` — токены не хранятся в JSON workflow.

---

## 2. Импорт готовых workflow

```bash
make n8n-import
```

Скрипт выполняет `n8n import:workflow --separate --input=/workflows` внутри контейнера (каталог `n8n/workflows` смонтирован в `/workflows`) и перезапускает сервис, чтобы зарегистрировались вебхуки.

Альтернатива через UI: **Workflows → ⋯ → Import from File** для каждого файла из `n8n/workflows/`.

После импорта каждый workflow нужно **активировать** переключателем в правом верхнем углу — иначе production-вебхук не отвечает (работает только Test URL, и только пока открыт редактор).

---

## 3. Workflow `01-transcriber-pipeline` — основной конвейер

Принимает файл, распознаёт, обрабатывает LLM, рассылает и отвечает вызывающему.

### Узел 1. `Webhook — приём файла`

| Параметр | Значение | Комментарий |
|---|---|---|
| HTTP Method | `POST` | |
| Path | `transcriber` | Итоговый URL: `http://n8n:5678/webhook/transcriber` внутри сети, `http://localhost:5678/webhook/transcriber` снаружи |
| Respond | `Using 'Respond to Webhook' node` | Ответ формирует последний узел, а не webhook сразу |
| Raw body | выключено | Нужен разбор multipart, чтобы файл попал в `binary` |

n8n кладёт файл из multipart-поля `file` в `item.binary.file`, а текстовые поля — в `item.json.body`.

### Узел 2. `Разбор параметров` (Code)

Делает три вещи:

1. **Проверяет общий секрет.** Сравнивает заголовок `X-Transcriber-Token` с `$env.N8N_WEBHOOK_TOKEN`. Если токен задан и не совпал — исключение, выполнение останавливается. Не пропускайте этот шаг: production-вебхук n8n по умолчанию открыт для всех, кто знает URL.
2. **Разбирает `options`** — multipart передаёт его строкой, поэтому нужен `JSON.parse`.
3. **Находит имя бинарного поля** и проставляет значения по умолчанию из окружения.

Ключевой момент: Code-узел возвращает `{ json, binary: item.binary }` — без явного проброса `binary` файл потеряется на следующем шаге.

### Узел 3. `Whisper — распознавание` (HTTP Request)

| Параметр | Значение |
|---|---|
| Method | `POST` |
| URL | `={{ ($env.ASR_BASE_URL || 'http://asr:8000/v1').replace(/\/$/, '') }}/audio/transcriptions` |
| Send Body | ✓ |
| Body Content Type | `Form-Data (multipart)` |

Параметры тела:

| Name | Parameter Type | Значение |
|---|---|---|
| `file` | **n8n Binary File** | Input Data Field Name: `={{ $json.file_key }}` |
| `model` | Form Data | `={{ $json.options.model }}` |
| `response_format` | Form Data | `={{ $json.options.timestamps ? 'verbose_json' : 'json' }}` |
| `language` | Form Data | `={{ $json.options.language || '' }}` |
| `prompt` | Form Data | `={{ $json.options.prompt || '' }}` |

Options → Timeout: `1800000` (30 минут). Дефолтные 5 минут срежут любую длинную запись. Включён `Retry On Fail` (2 попытки, пауза 5 с) — спасает от таймаута холодного старта модели.

### Узел 4. `Сборка расшифровки` (Code)

Склеивает ответ ASR с исходными параметрами через `$('Разбор параметров').first().json` — обращение к данным предыдущего узла по имени. Нормализует сегменты к единой форме `{start, end, text, speaker}`.

### Узел 5. `Нужна постобработка?` (IF)

Условие: `{{ $json.options.post_action }}` **not equals** `none`.
Выход **true** → ветка LLM, выход **false** → сразу `Итог`.

### Узел 6. `Сборка промпта` (Code)

Хранит шаблоны промптов (те же, что в `backend/app/prompts.py`) и выбирает нужный по `post_action`. Правьте текст прямо здесь — это и есть преимущество n8n-режима: изменение промпта не требует пересборки образа.

### Узел 7. `LLM — постобработка` (HTTP Request)

| Параметр | Значение |
|---|---|
| URL | `={{ ($env.LLM_BASE_URL || 'http://ollama:11434/v1').replace(/\/$/, '') }}/chat/completions` |
| Header | `Authorization: Bearer {{ $env.LLM_API_KEY }}` |
| Body | JSON, собирается выражением `JSON.stringify({...})` |
| Timeout | `900000` |

Тело собирается одним выражением, а не полями формы: так модель, температура и лимит токенов берутся из окружения, а сообщения — из предыдущего узла.

> Если вы предпочитаете штатные AI-узлы n8n, замените этот HTTP Request на **Basic LLM Chain** + credential «Ollama» (Base URL `http://ollama:11434`). Функционально то же самое; HTTP-вариант выбран, чтобы workflow импортировался без ручного создания credentials.

### Узел 8. `Итог` (Code)

Работает в обеих ветках. Базу берёт из `$('Сборка расшифровки')`, а наличие `choices` во входе определяет, была ли постобработка. Формирует `delivery_text` = постобработка, если она есть, иначе сырая расшифровка.

### Узлы 9–12. Доставка и ответ

Из `Итог` выходят **три параллельные связи**:

* `Ответ вызывающему` (Respond to Webhook) — отдаёт JSON синхронно тому, кто прислал файл;
* `Отправлять в Telegram?` (IF: массив `destinations` содержит `telegram`) → `Telegram — отправка`;
* `Отправлять в ручку?` (IF: содержит `webhook`) → `Внешняя ручка (API)`.

У обоих HTTP-узлов доставки выставлен `onError: continueRegularOutput` — упавший Telegram не должен ронять всё выполнение.

Telegram-узел бьёт напрямую в `https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendMessage` и режет текст до 4000 символов (лимит API — 4096).

### Подключение backend к этому workflow

```ini
# .env
PIPELINE_MODE=n8n
N8N_WEBHOOK_URL=http://n8n:5678/webhook/transcriber
N8N_WEBHOOK_TOKEN=<тот же секрет, что проверяет узел 2>
```

```bash
docker compose up -d api worker
```

Теперь `/api/v1/jobs` кладёт файл в очередь, а воркер передаёт его в n8n и ждёт синхронный ответ. Прогресс по шагам в этом режиме грубее (n8n не сообщает промежуточные статусы), зато логика правится мышкой.

Проверить workflow отдельно, без backend:

```bash
curl -X POST http://localhost:5678/webhook/transcriber \
  -H "X-Transcriber-Token: $N8N_WEBHOOK_TOKEN" \
  -F "file=@sample.wav" \
  -F 'options={"language":"ru","post_action":"summary","destinations":["telegram"]}'
```

---

## 4. Workflow `02-telegram-voice-bot` — голосовой бот

Пользователь шлёт боту голосовое, кружок, аудио или документ — получает расшифровку ответом.

### Узел `Telegram Trigger`

Единственный узел, которому нужен **credential**:

1. **Credentials → Add credential → Telegram API**.
2. `Access Token` — из @BotFather (см. [05-telegram.md](05-telegram.md)).
3. Название — `Transcriber Bot` (совпадает с тем, что прописано в JSON).
4. В параметрах триггера: Updates = `message`, **Additional Fields → Download Image/File = ON** — без этого файл не скачается и в `binary` будет пусто.

n8n сам зарегистрирует webhook в Telegram при активации workflow. Для этого n8n должен быть доступен по HTTPS снаружи (см. [07-vps-deploy.md](07-vps-deploy.md)). Локально используйте `Test URL` и туннель (`cloudflared tunnel --url http://localhost:5678`), прописав его в `WEBHOOK_URL`.

### Узел `Это медиа?` (IF, комбинатор OR)

Проверяет наличие `message.voice`, `message.audio`, `message.video_note` или `message.document`. Текстовые сообщения уходят в ложную ветку и игнорируются.

### Узел `Подготовка` (Code)

Разбирает **подпись к сообщению** как команду:

| Хэштег в подписи | Результат |
|---|---|
| `#summary` / `#кратко` | краткое содержание |
| `#minutes` / `#протокол` | протокол встречи |
| `#bullets` / `#конспект` | конспект |
| нет хэштега | только расшифровка |

Дальше — те же узлы ASR и LLM, что и в основном конвейере, и ответ штатным Telegram-узлом с `reply_to_message_id`.

---

## 5. Workflow `03-result-router` — маршрутизация готового результата

Для режима `PIPELINE_MODE=direct`, когда в задаче выбрано назначение `n8n`. Backend шлёт JSON в `POST /webhook/transcriber-result`, узел `Нормализация` проверяет токен, а `Switch` разводит по `meta.route`:

| `meta.route` | Ветка |
|---|---|
| `telegram` | сообщение в чат `meta.chat_id` |
| `crm` | POST в `meta.crm_url` в формате вашей системы |
| `archive` (и всё остальное) | `Convert to File` → `Read/Write File` кладёт `.txt` в `/data/jobs/` |

Через UI это поле задаётся в запросе (`options.meta`), через API — любым JSON:

```json
{"meta": {"route": "crm", "crm_url": "https://crm.example.com/api/notes", "deal_id": 42}}
```

Добавить свою ветку: откройте `Switch`, `Add Routing Rule`, задайте `outputKey`, подключите к нему нужный узел (Google Sheets, Notion, Jira, S3 — в n8n 400+ интеграций).

---

## 6. Эксплуатация

**Просмотр выполнений.** Слева `Executions` — полная история с данными на входе/выходе каждого узла. Для отладки открывайте упавшее выполнение и смотрите узел с красной рамкой.

**Хранение истории.** По умолчанию n8n копит все выполнения. На VPS ограничьте:

```ini
EXECUTIONS_DATA_PRUNE=true
EXECUTIONS_DATA_MAX_AGE=168        # часов, 7 суток
EXECUTIONS_DATA_SAVE_ON_SUCCESS=none
```

**Экспорт изменений обратно в репозиторий:**

```bash
docker compose exec -u node n8n n8n export:workflow --all --separate --output=/workflows
```

(Каталог смонтирован только для чтения — снимите `:ro` в `docker-compose.yml`, если планируете экспортировать регулярно.)

**Бэкап.** `make backup` архивирует том `n8n-data` (там же SQLite с workflow и зашифрованными credentials). Храните `N8N_ENCRYPTION_KEY` отдельно от бэкапа.

---

## 7. Альтернативы n8n

Если n8n не подходит по лицензии (Sustainable Use License — нельзя перепродавать как сервис):

| Инструмент | Лицензия | Замечания |
|---|---|---|
| [Windmill](https://windmill.dev) | AGPLv3 | Скрипты на Python/TS + визуальные флоу, быстрее n8n на больших объёмах |
| [Node-RED](https://nodered.org) | Apache 2.0 | Проще, силён в потоковых сценариях, слабее в готовых интеграциях |
| [Activepieces](https://activepieces.com) | MIT (ядро) | Ближайший аналог n8n по UX |
| [Kestra](https://kestra.io) | Apache 2.0 | Декларативный YAML, оркестрация данных |

Контракт обмена (multipart на вход, JSON на выход) одинаков — переносится любой из них. Для полностью свободной лицензии проще оставить `PIPELINE_MODE=direct`: там оркестрация на обычном Python.
