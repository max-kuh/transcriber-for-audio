# 03. Постобработка текста LLM

Постобработка — второй, независимый этап: расшифровка уже готова, модель работает только с текстом.

## Готовые действия

| `post_action` | Что делает | Где менять |
|---|---|---|
| `none` | ничего, только расшифровка | — |
| `summary` | краткое содержание + тезисы | `backend/app/prompts.py` |
| `minutes` | протокол встречи: вопросы, решения, задачи со сроками | там же |
| `bullets` | структурированный конспект списками | там же |
| `translate` | перевод на `target_language` | там же |
| `custom` | произвольная инструкция из поля UI | `post_instruction` |

Промпты вынесены в один файл `backend/app/prompts.py` — правка текста требует только `docker compose restart worker`.

## Выбор модели

| `LLM_MODEL` | RAM | Комментарий |
|---|---|---|
| `qwen2.5:3b-instruct` | ~3 ГБ | быстро, хватает для коротких заметок |
| `qwen2.5:7b-instruct` | ~6 ГБ | **дефолт**, хорошо держит русский |
| `llama3.1:8b-instruct-q4_K_M` | ~6 ГБ | альтернатива |
| `qwen2.5:14b-instruct` | ~10 ГБ | заметно лучше на протоколах |
| `gemma2:9b` | ~7 ГБ | хорош в кратких пересказах |

```bash
docker compose exec ollama ollama pull qwen2.5:14b-instruct
sed -i '' 's|^LLM_MODEL=.*|LLM_MODEL=qwen2.5:14b-instruct|' .env
docker compose up -d api worker
```

## Внешний провайдер

Любой OpenAI-совместимый endpoint:

```ini
# OpenAI
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini

# OpenRouter
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-...
LLM_MODEL=anthropic/claude-3.5-haiku

# vLLM / LM Studio на другой машине
LLM_BASE_URL=http://192.168.1.50:8000/v1
```

## Длинные записи

`backend/app/llm.py` реализует map-reduce: текст длиннее `CHUNK_CHARS` (12 000 символов ≈ 1.5 часа речи) режется по абзацам, каждая часть обрабатывается отдельно, затем результаты сводятся вторым запросом. Так обходится ограничение контекста локальных моделей без потери структуры.

Если у вашей модели контекст 128k и вы хотите обрабатывать целиком — поднимите `CHUNK_CHARS` в `llm.py`.

## Настройка качества

* `LLM_TEMPERATURE=0.2` — для протоколов и выжимок. Выше 0.5 модель начинает додумывать факты.
* `LLM_MAX_TOKENS=2048` — потолок ответа. Для часовых встреч поднимите до 4096.
* Системный промпт (`prompts.SYSTEM`) прямо запрещает выдумывать факты — не убирайте эту фразу.

## Проверка вручную

```bash
curl -s http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:7b-instruct","messages":[{"role":"user","content":"Скажи «работает»"}],"stream":false}' \
  | python3 -m json.tool
```
