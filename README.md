# Transcriber

Сервис для обрезки аудио **в браузере пользователя** (как snipsound.com), распознавания речи локальной моделью Whisper, постобработки текста LLM и доставки результата в Telegram или во внешнюю ручку (API). Конвейер можно выполнять как встроенным оркестратором, так и в **n8n** — визуальным open-source аналогом Zapier.

Всё поднимается локально одной командой и разворачивается на VPS в Docker без изменения кода.

---

## Что умеет

| Этап | Где выполняется | Технология |
|---|---|---|
| Обрезка, вырезание, fade, нормализация, скачивание WAV | Браузер пользователя, файл не покидает устройство | Web Audio API + wavesurfer.js 7 |
| Приведение к моно 16 кГц | Браузер (файл на сервер уходит в ~10× меньшего размера) | OfflineAudioContext |
| Speech-to-Text | Сервер | faster-whisper через Speaches (OpenAI-совместимый API) |
| Суммаризация / протокол / конспект / перевод / своя инструкция | Сервер | любой OpenAI-совместимый LLM: Ollama, vLLM, LM Studio, OpenAI |
| Доставка | Сервер | Telegram Bot API, произвольный webhook, n8n |
| Экспорт | Сервер | TXT, Markdown, SRT, VTT |

## Архитектура

```
Браузер                          Docker-сеть
┌───────────────────────┐        ┌──────────────────────────────────────────┐
│ wavesurfer + WebAudio │        │  api (FastAPI)  ──enqueue──►  redis       │
│  обрезка, mono 16 kHz │──POST─►│        │                        │         │
│                       │        │        │                   worker (arq)  │
└───────────────────────┘        │        │                        │        │
        ▲ polling статуса         │        └────── статус ──────────┘        │
        │                         │                                 │        │
        └─────────────────────────┤   ┌─────────┐  ┌────────┐  ┌───────────┐ │
                                  │   │   asr   │  │ ollama │  │    n8n    │ │
                                  │   │ whisper │  │  LLM   │  │ workflows │ │
                                  │   └─────────┘  └────────┘  └───────────┘ │
                                  └──────────────────────────────────────────┘
                                          │                          │
                                    Telegram Bot API           внешняя ручка
```

Два режима конвейера переключаются переменной `PIPELINE_MODE`:

* `direct` — Python-воркер сам вызывает ASR → LLM → доставку. Меньше движущихся частей, быстрее.
* `n8n` — backend отдаёт файл в webhook n8n, вся логика редактируется мышкой в UI n8n.

## Быстрый старт

```bash
cp .env.example .env          # или: make init
# отредактируйте .env: как минимум N8N_ENCRYPTION_KEY и TELEGRAM_BOT_TOKEN
make up                       # поднять стек (CPU)
make pull-llm                 # скачать модель для суммаризации
make warm-asr                 # предзагрузить Whisper
```

Откройте <http://localhost:8080>.

С видеокартой NVIDIA: `make up-gpu`. На VPS с доменом и TLS: `make up-prod`.

## Документация

| Файл | О чём |
|---|---|
| [docs/00-architecture.md](docs/00-architecture.md) | Как устроен сервис, потоки данных, форматы |
| [docs/01-quickstart.md](docs/01-quickstart.md) | Локальный запуск по шагам, проверка каждого сервиса |
| [docs/02-asr-whisper.md](docs/02-asr-whisper.md) | Выбор модели Whisper, GPU, качество и скорость, диаризация |
| [docs/03-llm-postprocessing.md](docs/03-llm-postprocessing.md) | Ollama и внешние LLM, промпты, длинные тексты |
| [docs/04-n8n-setup.md](docs/04-n8n-setup.md) | **Настройка n8n и разбор каждого узла workflow** |
| [docs/05-telegram.md](docs/05-telegram.md) | Бот, chat_id, группы, голосовой бот |
| [docs/06-api.md](docs/06-api.md) | HTTP API сервиса, примеры интеграции |
| [docs/07-vps-deploy.md](docs/07-vps-deploy.md) | Деплой на VPS: домен, TLS, firewall, systemd, бэкапы |
| [docs/08-troubleshooting.md](docs/08-troubleshooting.md) | Типовые ошибки и их устранение |
| [docs/09-references.md](docs/09-references.md) | Похожие открытые реализации и источники |

## Структура репозитория

```
.claude/            агенты и skills для разработки (Claude Code)
backend/            FastAPI + arq-воркер
frontend/           статика: редактор аудио и UI (без сборки, vendor-зависимости внутри)
n8n/workflows/      готовые workflow для импорта
deploy/             nginx, Caddy
docs/               инструкции
backend/tests/      юнит-тесты (make test, без Docker и сети)
scripts/            прогрев, smoke-тест, импорт workflow, бэкап
```

## Приватность

Исходный файл не покидает браузер до нажатия «Отправить». На сервер уходит только выбранный фрагмент, сведённый в моно 16 кГц. Загруженный файл удаляется сразу после обработки, метаданные задачи живут `RETENTION_HOURS` часов (по умолчанию 24). ASR и LLM работают локально — во внешние сервисы ничего не уходит, пока вы сами не пропишете внешний endpoint.
