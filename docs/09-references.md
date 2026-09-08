# 09. Похожие реализации и источники

Что изучалось при проектировании и откуда взяты решения.

## Обрезка аудио в браузере

* [snipsound.com/ru/obrezka-audio](https://snipsound.com/ru/obrezka-audio/) — референс UX, заданный в задаче: обработка целиком на клиенте.
* [wavesurfer.js](https://github.com/katspaugh/wavesurfer.js) — отрисовка волны, плагины Regions и Timeline. Используется в проекте (вендорится в `frontend/vendor/`).
* [Обсуждение trim/cut в wavesurfer](https://github.com/katspaugh/wavesurfer.js/discussions/3557) — почему сам wavesurfer не режет звук и операции делаются над `AudioBuffer`.
* [ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm) — альтернатива Web Audio API для экзотических кодеков и MP3-экспорта; подключение описано в [08-troubleshooting.md](08-troubleshooting.md).
* Тема [audio-cutter на GitHub](https://github.com/topics/audio-cutter) — обзор аналогов.

## Speech-to-Text

* [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) — реимплементация Whisper на CTranslate2, ~4× быстрее на GPU и ~2× на CPU при той же точности.
* [Speaches](https://speaches.ai) ([github](https://github.com/speaches-ai/speaches), бывш. `fedirz/faster-whisper-server`) — OpenAI-совместимый сервер поверх faster-whisper. Используется как сервис `asr`.
* [hwdsl2/docker-whisper](https://github.com/hwdsl2/docker-whisper) — альтернативный образ с диаризацией, SSE-стримингом и multi-arch (amd64/arm64).
* [WhisperX](https://github.com/m-bain/whisperX) — пословные таймкоды и диаризация через wav2vec2, если нужен разбор по говорящим.
* [faster-whisper от LinuxServer.io](https://docs.linuxserver.io/images/docker-faster-whisper/) — вариант с протоколом Wyoming (интеграция с Home Assistant).

## n8n и готовые конвейеры

* [Alices5723/n8n-telegram-voice-transcription-bot](https://github.com/Alices5723/n8n-telegram-voice-transcription-bot) — транскрипция и суммаризация голосовых Telegram через n8n.
* [FullFran/Audio-transcription-bot-n8n](https://github.com/FullFran/Audio-transcription-bot-n8n) — Telegram-бот с расшифровкой и кратким содержанием.
* [stezan1/n8n-voice-transcriber-bot](https://github.com/stezan1/n8n-voice-transcriber-bot) — n8n + Whisper + Ollama, распознавание в структурированный JSON и запись через API. Ближайший аналог нашего режима `PIPELINE_MODE=n8n`.
* Шаблоны n8n: [Telegram + Groq Whisper](https://n8n.io/workflows/10037-audio-transcription-with-telegram-and-groq-whisper/), [расшифровка голосовых через Whisper](https://n8n.io/workflows/4528-transcribe-voice-messages-from-telegram-using-openai-whisper-1/), [Whisper + GPT → Notion](https://n8n.io/workflows/6139-transcribe-and-summarize-audio-with-whisper-and-gpt-from-google-drive-to-notion/), [локальная LLaMA + Telegram](https://n8n.io/workflows/6013-create-personal-notes-with-voice-transcription-using-local-llama-and-telegram/).
* [Документация HTTP Request node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest) — раздел про `multipart/form-data` и передачу бинарных полей.

## Агенты и skills для разработки

Каталог `.claude/` наполнен из открытых коллекций:

* [rohitg00/awesome-claude-code-toolkit](https://github.com/rohitg00/awesome-claude-code-toolkit) — источник загруженных агентов и skills.
* [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) — 1000+ skills, совместимых с Claude Code, Cursor, Codex, Gemini CLI.
* [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills) — курируемый список.
* [netresearch/claude-code-marketplace](https://github.com/netresearch/claude-code-marketplace) — открытый стандарт agentskills.io, портируемый между агентами.

Состав и назначение — в [.claude/README.md](../.claude/README.md).

## Альтернативные оркестраторы

* [Windmill](https://windmill.dev) (AGPLv3), [Node-RED](https://nodered.org) (Apache 2.0), [Activepieces](https://activepieces.com) (MIT), [Kestra](https://kestra.io) (Apache 2.0) — сравнение в [04-n8n-setup.md](04-n8n-setup.md).

Лицензия n8n — Sustainable Use License: свободна для внутреннего использования, но запрещает перепродажу как SaaS. Для коммерческого сервиса выбирайте `PIPELINE_MODE=direct` или один из аналогов выше.
