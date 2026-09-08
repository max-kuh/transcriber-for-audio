#!/usr/bin/env bash
# Сквозная проверка: синтезируем речь -> отправляем в API -> ждём расшифровку.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

API="http://localhost:${API_PORT:-8000}/api/v1"
AUTH=()
[ -n "${API_KEY:-}" ] && AUTH=(-H "X-API-Key: ${API_KEY}")

echo "→ /health"
curl -sf "${AUTH[@]}" "$API/health" | python3 -m json.tool

echo "→ /health/deps"
curl -sf "${AUTH[@]}" "$API/health/deps" | python3 -m json.tool

SAMPLE="${1:-}"
if [ -z "$SAMPLE" ]; then
  SAMPLE=$(mktemp -d)/sample.wav
  if command -v say >/dev/null; then                      # macOS
    say -o "${SAMPLE%.wav}.aiff" "Проверка связи. Это тестовая запись для расшифровки."
    ffmpeg -hide_banner -loglevel error -y -i "${SAMPLE%.wav}.aiff" -ar 16000 -ac 1 "$SAMPLE"
  else
    echo "  нет исходного файла: передайте путь первым аргументом — bash scripts/smoke-test.sh my.mp3"
    exit 1
  fi
fi

echo "→ Отправка $SAMPLE"
JOB=$(curl -sf "${AUTH[@]}" -X POST "$API/jobs" \
  -F "file=@$SAMPLE" \
  -F 'options={"language":"ru","post_action":"summary","destinations":[]}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "  job_id=$JOB"

for _ in $(seq 1 120); do
  RESP=$(curl -sf "${AUTH[@]}" "$API/jobs/$JOB")
  STATUS=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  echo "  статус: $STATUS"
  case "$STATUS" in
    done)   echo "$RESP" | python3 -m json.tool; exit 0 ;;
    failed) echo "$RESP" | python3 -m json.tool; exit 1 ;;
  esac
  sleep 3
done
echo "таймаут ожидания"; exit 1
