#!/usr/bin/env bash
# Импортирует workflow-ы из n8n/workflows внутрь контейнера n8n.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ Импорт workflow-ов"
docker compose exec -u node n8n n8n import:workflow --separate --input=/workflows

echo "→ Перезапуск n8n, чтобы зарегистрировать вебхуки"
docker compose restart n8n

echo "готово. Откройте http://localhost:${N8N_PORT:-5678} и активируйте нужные workflow."
