SHELL := /bin/bash
COMPOSE := docker compose

.DEFAULT_GOAL := help

help: ## Список команд
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

init: ## Создать .env из шаблона
	@test -f .env || cp .env.example .env; echo "готово: отредактируйте .env"

up: ## Поднять весь стек (CPU)
	$(COMPOSE) up -d --build

up-gpu: ## Поднять стек с NVIDIA GPU
	$(COMPOSE) -f docker-compose.yml -f docker-compose.gpu.yml up -d --build

up-prod: ## Поднять на VPS за Caddy (TLS)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml up -d --build

down: ## Остановить
	$(COMPOSE) down

logs: ## Логи всех сервисов
	$(COMPOSE) logs -f --tail=100

ps: ## Статус
	$(COMPOSE) ps

pull-llm: ## Скачать модель LLM в Ollama (LLM_MODEL из .env)
	$(COMPOSE) exec ollama ollama pull $$(grep -E '^LLM_MODEL=' .env | cut -d= -f2)

warm-asr: ## Предзагрузить модель Whisper
	bash scripts/warm-asr.sh

smoke: ## Сквозной тест: файл -> текст -> саммари
	bash scripts/smoke-test.sh

test: ## Юнит-тесты backend (без Docker и сети)
	cd backend && python -m pytest tests -q

n8n-import: ## Импортировать workflow-ы в n8n
	bash scripts/n8n-import.sh

backup: ## Бэкап томов n8n/ollama/redis
	bash scripts/backup.sh

clean: ## Удалить контейнеры и тома (ДАННЫЕ БУДУТ ПОТЕРЯНЫ)
	$(COMPOSE) down -v

.PHONY: help init up up-gpu up-prod down logs ps pull-llm warm-asr smoke test n8n-import backup clean
