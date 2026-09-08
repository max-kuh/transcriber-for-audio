# Агенты и skills проекта

Подключены из открытых коллекций (см. [docs/09-references.md](../docs/09-references.md)) под задачи этого сервиса. Claude Code подхватывает их автоматически: агентов — по `.claude/agents/`, skills — по `.claude/skills/*/SKILL.md`.

## Агенты (`.claude/agents/`)

| Агент | Когда полезен здесь |
|---|---|
| `backend-developer` | FastAPI, очередь arq, схемы Pydantic |
| `frontend-architect` | редактор аудио, Web Audio API, состояние UI |
| `api-designer` | контракт `/api/v1`, формат доставки во внешние ручки |
| `ai-engineer` | интеграция Whisper и LLM, обработка длинных текстов |
| `llm-architect` | выбор моделей, промпты, map-reduce для часовых записей |
| `nlp-engineer` | качество распознавания, борьба с галлюцинациями, диаризация |
| `deployment-engineer` | Docker Compose, оверрайды CPU/GPU/prod |
| `devops-engineer` | VPS, Caddy, systemd, бэкапы |
| `security-engineer` | API-ключи, секреты вебхуков, изоляция сервисов |
| `performance-engineer` | параллелизм воркера, ресурсные лимиты, скорость ASR |
| `code-reviewer` | ревью изменений |
| `documentation-engineer` | поддержка `docs/` в актуальном состоянии |

## Skills (`.claude/skills/`)

`docker-best-practices`, `devops-automation`, `ci-cd-pipelines`, `api-design-patterns`, `microservices-design`, `frontend-excellence`, `llm-integration`, `python-best-practices`, `security-hardening`, `testing-strategies`, `performance-optimization`, `monitoring-observability`.

## Обновление

```bash
RAW=https://raw.githubusercontent.com/rohitg00/awesome-claude-code-toolkit/main
curl -sfL "$RAW/agents/data-ai/ai-engineer.md" -o .claude/agents/ai-engineer.md
curl -sfL "$RAW/skills/docker-best-practices/SKILL.md" -o .claude/skills/docker-best-practices/SKILL.md
```

Каталоги агентов в источнике: `core-development`, `infrastructure`, `data-ai`, `developer-experience`, `quality-assurance`, `orchestration`, `language-experts`, `specialized-domains`, `research-analysis`, `business-product`.
