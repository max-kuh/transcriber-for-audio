#!/usr/bin/env bash
# Предзагружает модель Whisper, чтобы первая настоящая задача не ждала скачивания.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

MODEL="${WHISPER__MODEL:-Systran/faster-whisper-small}"
PORT="${ASR_PORT:-8001}"

echo "→ Ожидание ASR на localhost:${PORT}"
for _ in $(seq 1 60); do
  curl -sf "http://localhost:${PORT}/v1/models" >/dev/null && break
  sleep 5
done

echo "→ Загрузка модели ${MODEL} (первый раз может занять несколько минут)"
curl -sf -X POST "http://localhost:${PORT}/v1/models/${MODEL}" || \
  echo "  (endpoint загрузки недоступен — модель подтянется при первом запросе)"

echo "→ Прогрев коротким сэмплом"
TMP=$(mktemp -d)
ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=1" -ar 16000 -ac 1 "$TMP/warm.wav"
curl -sf -X POST "http://localhost:${PORT}/v1/audio/transcriptions" \
  -F "file=@$TMP/warm.wav" -F "model=${MODEL}" -F "response_format=json" | head -c 400
rm -rf "$TMP"
echo; echo "готово"
