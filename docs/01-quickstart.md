# 01. Локальный запуск, шаг за шагом

## Требования

* Docker Engine 24+ и Docker Compose v2 (`docker compose version`).
* 8 ГБ RAM минимум для `small`-модели Whisper + LLM 7B в int4. Для `large-v3` на CPU — 16 ГБ.
* ~15 ГБ на диске под образы и веса моделей.
* GPU не обязателен, но ускоряет распознавание в 5–15 раз.

## Шаг 1. Конфигурация

```bash
cp .env.example .env
```

Обязательно поменяйте:

| Переменная | Почему |
|---|---|
| `N8N_ENCRYPTION_KEY` | Без неё n8n не стартует; ею шифруются сохранённые credentials |
| `N8N_BASIC_AUTH_PASSWORD` | Иначе панель n8n открыта с паролем по умолчанию |
| `API_KEY` | Пусто = API без авторизации. Для локальной машины допустимо, для VPS — нет |

Сгенерировать секреты:

```bash
openssl rand -hex 32   # для N8N_ENCRYPTION_KEY
openssl rand -hex 24   # для API_KEY и N8N_WEBHOOK_TOKEN
```

## Шаг 2. Запуск стека

```bash
make up          # docker compose up -d --build
make ps          # все сервисы должны быть healthy/running
```

Первый запуск тянет ~6 ГБ образов. Ollama и Speaches стартуют без моделей — веса скачиваются на следующем шаге.

## Шаг 3. Модель распознавания

```bash
make warm-asr
```

Скрипт дожидается ASR, инициирует загрузку модели из `WHISPER__MODEL` и прогревает её односекундным сэмплом. Первый прогон `small` — 1–2 минуты, `large-v3` — 10–20 минут в зависимости от канала.

Проверка вручную:

```bash
curl -s http://localhost:8001/v1/models | python3 -m json.tool
```

## Шаг 4. Модель постобработки

```bash
make pull-llm    # тянет модель из LLM_MODEL, по умолчанию qwen2.5:7b-instruct
```

Проверка:

```bash
curl -s http://localhost:11434/v1/models | python3 -m json.tool
```

Если суммаризация не нужна — оставьте `post_action: none` в UI, сервис `ollama` можно выключить:
`docker compose stop ollama`.

## Шаг 5. Проверка backend

```bash
curl -s http://localhost:8000/api/v1/health      | python3 -m json.tool
curl -s http://localhost:8000/api/v1/health/deps | python3 -m json.tool   # {"asr": true, "llm": true}
```

Интерактивная документация API: <http://localhost:8000/docs>.

## Шаг 6. Сквозной тест

```bash
make smoke                       # на macOS сам синтезирует речь через `say`
bash scripts/smoke-test.sh my.mp3   # или на своём файле
```

Ожидаемый финал — JSON со `status: done` и непустым `result.text`.

## Шаг 7. Веб-интерфейс

<http://localhost:8080>

1. Перетащите файл в зону загрузки.
2. Выделите фрагмент мышью прямо на волне (или введите начало/конец вручную).
3. `✂ Оставить выделение` — обрезка; `⤫ Вырезать выделение` — удаление куска с зашивкой стыка.
4. `Fade in/out` убирает щелчки на краях, `Нормализовать` выравнивает громкость.
5. `⬇ Скачать WAV` — если нужен только результат обрезки, без расшифровки.
6. Ниже выберите язык, тип постобработки и получателей → `Отправить на расшифровку`.

Индикатор в шапке (`ASR ✓ LLM ✓`) показывает доступность зависимостей.

## Шаг 8. n8n (опционально)

```bash
make n8n-import
```

Дальше — [04-n8n-setup.md](04-n8n-setup.md).

## Тесты

```bash
make test    # 18 юнит-тестов чистых функций: субтитры, сплиттеры, промпты, схемы
```

Docker и сеть для них не нужны — только `pip install -r backend/requirements-dev.txt`.

## Остановка и очистка

```bash
make down     # остановить, данные и модели сохранятся
make clean    # удалить и тома тоже (модели придётся качать заново)
```
