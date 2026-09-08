# 02. Распознавание речи: Whisper

Сервис `asr` — это [Speaches](https://speaches.ai) (бывш. `faster-whisper-server`): обёртка над [faster-whisper](https://github.com/SYSTRAN/faster-whisper) с **OpenAI-совместимым** эндпоинтом `/v1/audio/transcriptions`. Совместимость означает, что тот же код и те же n8n-узлы работают с облачным OpenAI или Groq — меняется только `ASR_BASE_URL` и `ASR_API_KEY`.

## Выбор модели

| Модель (`WHISPER__MODEL`) | VRAM/RAM | Скорость на CPU (4 ядра) | Качество на русском |
|---|---|---|---|
| `Systran/faster-whisper-tiny` | ~0.5 ГБ | ~15× реального времени | черновое, много ошибок |
| `Systran/faster-whisper-base` | ~0.7 ГБ | ~8× | приемлемо для коротких заметок |
| `Systran/faster-whisper-small` | ~1.5 ГБ | ~3–4× | **разумный дефолт** |
| `Systran/faster-whisper-medium` | ~3 ГБ | ~1.2× | хорошо |
| `Systran/faster-whisper-large-v3` | ~5 ГБ | ~0.4× (медленнее реального времени) | лучшее |
| `deepdml/faster-whisper-large-v3-turbo-ct2` | ~3 ГБ | ~2–3× | почти как large-v3, **лучший выбор для GPU** |

«3× реального времени» = 10 минут аудио обрабатываются примерно за 3 минуты.

Смена модели:

```bash
sed -i '' 's|^WHISPER__MODEL=.*|WHISPER__MODEL=deepdml/faster-whisper-large-v3-turbo-ct2|' .env
docker compose up -d asr
make warm-asr
```

Модель можно переопределить и для одной задачи — поле «Модель Whisper» в UI или `options.model` в API.

## Точность вычислений

`WHISPER__COMPUTE_TYPE`:

* `int8` — CPU, минимум памяти, дефолт;
* `int8_float16` — GPU начального уровня;
* `float16` — GPU, оптимум скорость/качество;
* `float32` — эталон, редко нужен.

## GPU

```bash
# один раз на хосте
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker

make up-gpu
docker compose exec asr nvidia-smi   # убедиться, что карта видна
```

Оверрайд `docker-compose.gpu.yml` подменяет образ на `latest-cuda`, `compute_type` на `float16` и модель на turbo.

## Параметры запроса

| Поле | Что делает |
|---|---|
| `language` | Явный язык ускоряет распознавание и убирает ошибки автоопределения на коротких файлах. Для русского всегда ставьте `ru` |
| `prompt` | `initial_prompt` Whisper: список имён, терминов, аббревиатур. Резко снижает ошибки в специфичной лексике. Лимит — 224 токена |
| `response_format` | `json` (только текст) или `verbose_json` (сегменты с таймкодами). Второй нужен для SRT/VTT |
| `temperature` | Оставьте 0 — при повышении Whisper начинает «фантазировать» |

Пример прямого вызова:

```bash
curl -s http://localhost:8001/v1/audio/transcriptions \
  -F "file=@meeting.wav" \
  -F "model=Systran/faster-whisper-small" \
  -F "language=ru" \
  -F "prompt=Кубернетес, Postgres, ООО «Ромашка», Иван Петров" \
  -F "response_format=verbose_json" | python3 -m json.tool
```

## Галлюцинации на тишине

Whisper на длинных паузах склонен выдумывать фразы («Продолжение следует…», «Субтитры сделал DimaTorzok»). Средства борьбы:

1. Обрезать тишину в браузере — за этим и нужен редактор.
2. Ставить `language` явно.
3. На стороне ASR включить VAD (в Speaches он активен по умолчанию).
4. Отфильтровать типовой мусор в постобработке — LLM убирает такие вставки, если попросить.

## Диаризация (кто говорит)

Speaches отдаёт `speaker` в сегментах, если сборка собрана с диаризацией; поле уже поддержано в схеме `Segment` и в экспорте SRT (`[SPEAKER_01] текст`). Для полноценной диаризации по говорящим ставьте отдельный сервис на базе [WhisperX](https://github.com/m-bain/whisperX) или `pyannote.audio` и укажите его URL в `ASR_BASE_URL` — контракт тот же.

## Переход на облако

```ini
# .env
ASR_BASE_URL=https://api.groq.com/openai/v1
ASR_API_KEY=gsk_...
WHISPER__MODEL=whisper-large-v3
```

Ничего в коде и в n8n-узлах менять не нужно. Учтите: в этом случае аудио уходит стороннему провайдеру — противоречит приватному сценарию.
