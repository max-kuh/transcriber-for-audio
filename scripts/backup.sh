#!/usr/bin/env bash
# Бэкап именованных томов (n8n, ollama, redis) в ./backups/<дата>.
set -euo pipefail
cd "$(dirname "$0")/.."
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="backups/$STAMP"
mkdir -p "$OUT"

for VOL in transcriber_n8n-data transcriber_redis-data; do
  echo "→ $VOL"
  docker run --rm -v "$VOL":/src -v "$PWD/$OUT":/dst alpine \
    tar czf "/dst/${VOL}.tar.gz" -C /src .
done

cp .env "$OUT/env.backup" 2>/dev/null || true
echo "готово: $OUT"
