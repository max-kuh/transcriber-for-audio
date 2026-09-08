# 07. Развёртывание на VPS

## Требования к серверу

| Сценарий | CPU | RAM | Диск |
|---|---|---|---|
| Whisper `small` + LLM 3B, редкие задачи | 4 ядра | 8 ГБ | 40 ГБ SSD |
| Whisper `medium` + LLM 7B, команда 5–20 человек | 8 ядер | 16 ГБ | 80 ГБ SSD |
| Whisper `large-v3-turbo` на GPU | 8 ядер + RTX 3060 (12 ГБ) | 16 ГБ | 100 ГБ NVMe |

Без GPU расшифровка идёт медленнее реального времени на `large`. Для потока задач берите либо GPU-инстанс, либо `small`/`medium`.

## Шаг 1. Подготовка хоста

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Firewall: наружу только 80/443 и SSH
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable

# swap, если RAM < 16 ГБ — модели любят пиковые аллокации
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Шаг 2. Код и конфигурация

```bash
git clone <ваш-репозиторий> /opt/transcriber && cd /opt/transcriber
cp .env.example .env
```

Продакшн-значения `.env`:

```ini
PUBLIC_BASE_URL=https://transcriber.example.com
CORS_ORIGINS=https://transcriber.example.com
API_KEY=<openssl rand -hex 24>

N8N_HOST=n8n.example.com
N8N_PROTOCOL=https
WEBHOOK_URL=https://n8n.example.com/
N8N_ENCRYPTION_KEY=<openssl rand -hex 32>
N8N_BASIC_AUTH_PASSWORD=<длинный пароль>
N8N_WEBHOOK_TOKEN=<openssl rand -hex 24>

RETENTION_HOURS=6
MAX_UPLOAD_MB=256
```

```bash
chmod 600 .env
```

## Шаг 3. DNS и TLS

Заведите A-записи `transcriber.example.com` и `n8n.example.com` на IP сервера. Подставьте домены и e-mail в `deploy/caddy/Caddyfile`.

```bash
make up-prod
```

Оверрайд `docker-compose.prod.yml` поднимает Caddy на 80/443 (сертификаты Let's Encrypt выпускаются автоматически) и **снимает публикацию портов** с `api`, `asr`, `ollama`, `n8n` — они остаются доступны только внутри docker-сети. Это важно: без этого Ollama и Speaches торчали бы в интернет без авторизации.

Проверка:

```bash
curl -s https://transcriber.example.com/api/v1/health
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

## Шаг 4. Модели

```bash
make warm-asr
make pull-llm
```

Веса лежат в томах `hf-cache` и `ollama-data` и переживают пересборку контейнеров.

## Шаг 5. Автозапуск

`restart: unless-stopped` у всех сервисов достаточно, если docker включён в автозагрузку (`sudo systemctl enable docker`). Для явного контроля порядка — unit:

```ini
# /etc/systemd/system/transcriber.service
[Unit]
Description=Transcriber stack
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/transcriber
ExecStart=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.prod.yml down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now transcriber
```

## Безопасность

| Мера | Как |
|---|---|
| API закрыт ключом | `API_KEY` в `.env`, клиент вводит его в «Настройках» UI |
| Панель n8n | basic-auth + отдельный поддомен; при желании ограничьте по IP в Caddy (`@allowed remote_ip …`) |
| Вебхуки n8n | общий секрет `X-Transcriber-Token`, проверяется первым Code-узлом |
| Внутренние сервисы | не публикуются наружу в prod-оверрайде |
| Данные | исходники удаляются сразу после обработки, метаданные — через `RETENTION_HOURS` |
| Обновления | `docker compose pull && make up-prod`; закрепите теги образов вместо `latest` перед продакшном |
| Секреты | `.env` с правами 600, вне git (`.gitignore`) |

Дополнительно стоит поставить fail2ban на SSH и ограничить размер тела запроса (уже сделано: 512 МБ в nginx, 512 МБ в Caddy, `MAX_UPLOAD_MB` в API).

## Ресурсные лимиты

Чтобы Whisper не выел всю память, добавьте в оверрайд:

```yaml
services:
  asr:
    deploy:
      resources:
        limits: { cpus: "4.0", memory: 6g }
  ollama:
    deploy:
      resources:
        limits: { memory: 8g }
```

Параллелизм воркера регулируется `max_jobs` в `backend/app/worker.py` (по умолчанию 2). На слабом CPU ставьте 1 — иначе две транскрипции будут душить друг друга.

## Мониторинг

```bash
docker compose logs -f --tail=100 worker    # ход обработки задач
docker stats                                # потребление ресурсов
curl -s https://transcriber.example.com/api/v1/health/deps
```

Для внешнего мониторинга подойдёт Uptime Kuma с проверкой `/api/v1/health/deps` (ожидаемая строка `"asr": true`).

## Бэкап и восстановление

```bash
make backup      # ./backups/<дата>/: n8n-data, redis-data, копия .env
```

Восстановление тома:

```bash
docker run --rm -v transcriber_n8n-data:/dst -v $PWD/backups/<дата>:/src alpine \
  sh -c "rm -rf /dst/* && tar xzf /src/transcriber_n8n-data.tar.gz -C /dst"
```

`N8N_ENCRYPTION_KEY` храните отдельно: без него credentials из бэкапа не расшифруются.

## Обновление

```bash
cd /opt/transcriber
git pull
docker compose pull
make up-prod
docker image prune -f
```
