# 08. Типовые проблемы

## Браузер

**«Не удалось декодировать файл».** Браузер не понимает кодек. Поддерживаются mp3, wav, ogg/opus, flac, aac/m4a, webm, mp4/mov. Решения: сконвертировать заранее (`ffmpeg -i in.mkv -vn -ar 16000 -ac 1 out.wav`) или подключить `ffmpeg.wasm` — см. ниже.

**Вкладка виснет на большом файле.** `decodeAudioData` держит несжатый буфер в памяти: час стерео-44.1 кГц ≈ 600 МБ. Для многочасовых записей режьте файл заранее либо переходите на потоковую обработку.

**Волна не рисуется.** Проверьте консоль: файлы из `frontend/vendor/` должны отдаваться с `Content-Type: text/javascript`. nginx из образа делает это корректно; при раздаче через `python3 -m http.server` возможны проблемы с MIME для `.esm.js` — используйте nginx-контейнер.

**Нет звука при воспроизведении выделения.** Браузер блокирует автовоспроизведение до первого клика по странице — нажмите «Воспроизвести» один раз.

## API и воркер

**`413 файл больше N МБ`.** Поднимите `MAX_UPLOAD_MB` в `.env` и `client_max_body_size` в `deploy/nginx.conf` (и `max_size` в Caddyfile для prod).

**Задача вечно в `queued`.** Не работает воркер:

```bash
docker compose ps worker
docker compose logs --tail=50 worker
docker compose restart worker
```

Частая причина — недоступный Redis: проверьте `REDIS_URL` и `docker compose exec redis redis-cli ping`.

**`404` при опросе статуса.** Задача старше `RETENTION_HOURS` либо Redis перезапустился без сохранения. Увеличьте `RETENTION_HOURS`.

**`ASR 500` / `ASR 400`.** Смотрите `docker compose logs asr`. Обычно: модель ещё качается (подождите, `make warm-asr`), не хватает памяти (возьмите модель поменьше или `int8`), либо файл пустой.

**Транскрипция очень медленная.** Проверьте, что не запущена `large` на CPU: `grep WHISPER__MODEL .env`. Для CPU — `small` + `int8`. При наличии GPU — `make up-gpu` и `docker compose exec asr nvidia-smi`.

**LLM отвечает таймаутом.** Модель не скачана (`make pull-llm`) или слишком велика для RAM. Проверка: `docker compose exec ollama ollama list`.

**Пустой `result.text`.** В фрагменте нет речи, либо неверно указан язык. Попробуйте `language: null` (автоопределение) или проверьте фрагмент на слух в редакторе.

## Telegram

**`400 Bad Request: chat not found`.** Бот не состоит в чате или `chat_id` неверный. Для групп id отрицательный. Перепроверьте через `getUpdates` ([05-telegram.md](05-telegram.md)).

**`400 can't parse entities`.** Текст содержит символы, ломающие HTML-разметку. Уберите `"parse_mode": "HTML"` в `backend/app/delivery.py` или экранируйте `<`, `>`, `&`.

**Бот не реагирует на голосовые.** (1) Workflow не активирован; (2) `WEBHOOK_URL` указывает не на публичный HTTPS-адрес; (3) в триггере выключен `Download`; (4) в группе включён privacy mode — `/setprivacy` → `Disable` у BotFather.

## n8n

**Вебхук отвечает `404`.** Workflow не активирован. Test URL (`/webhook-test/...`) живёт только пока открыт редактор; production URL (`/webhook/...`) требует активации.

**`Неверный X-Transcriber-Token`.** Значение `N8N_WEBHOOK_TOKEN` в `.env` должно совпадать у backend и у контейнера n8n. После правки `.env`: `docker compose up -d n8n api worker`.

**В узле ASR приходит пустой файл.** Между webhook и HTTP Request стоит Code-узел, который не пробросил `binary`. Возвращайте `{ json: {...}, binary: item.binary }`.

**`$env.X` пустой.** Переменная не проброшена в контейнер n8n. В нашем compose проброшен весь `.env` через `env_file`; после добавления новой переменной нужен `docker compose up -d n8n`.

**Память растёт при больших файлах.** Проверьте `N8N_DEFAULT_BINARY_DATA_MODE=filesystem` — в режиме `default` бинарники держатся в памяти.

**История выполнений раздувает диск.** Включите prune (см. [04-n8n-setup.md](04-n8n-setup.md), раздел «Эксплуатация»).

## Docker

**`n8n` не стартует.** Почти всегда — отсутствует `N8N_ENCRYPTION_KEY`. Compose явно требует её (`:?set N8N_ENCRYPTION_KEY`).

**Порт занят.** Поменяйте `FRONTEND_PORT`, `API_PORT`, `ASR_PORT`, `N8N_PORT` в `.env`.

**Модель Whisper качается заново после пересоздания контейнера.** Кэш HuggingFace монтируется томом `hf-cache` в `/home/ubuntu/.cache/huggingface`. Если в вашей версии образа Speaches домашний каталог другой, посмотрите его (`docker compose exec asr sh -c 'echo $HOME'`) и поправьте путь тома в `docker-compose.yml`.

**Мало места.** `docker system df`, затем `docker image prune -a`. Веса моделей лежат в томах `hf-cache` и `ollama-data` — их `prune` не трогает.

---

## Приложение: подключение ffmpeg.wasm

Если нужны экзотические контейнеры или экспорт в MP3 прямо в браузере:

1. Скачайте UMD-сборку в `frontend/vendor/`:

```bash
cd frontend/vendor
curl -sL https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.12/dist/umd/ffmpeg.js -o ffmpeg.js
curl -sL https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12/dist/umd/ffmpeg-core.js -o ffmpeg-core.js
curl -sL https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12/dist/umd/ffmpeg-core.wasm -o ffmpeg-core.wasm
```

2. Заголовки COOP/COEP уже выставлены в `deploy/nginx.conf` и в Caddyfile — они обязательны для многопоточного `SharedArrayBuffer`.
3. Вызывайте после декодирования: `ffmpeg.exec(['-i','in.mkv','-vn','-ar','16000','-ac','1','out.wav'])`.

Учтите: ядро весит ~30 МБ и обработка идёт медленнее нативного Web Audio API. Основной путь (WebAudio → WAV) быстрее и покрывает большинство форматов, поэтому ffmpeg.wasm подключён как опция, а не по умолчанию.
