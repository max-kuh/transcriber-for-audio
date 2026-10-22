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

N8N_ENCRYPTION_KEY=<openssl rand -hex 32>
N8N_WEBHOOK_TOKEN=<openssl rand -hex 24>

SITE_ADDRESS=transcriber.example.com
ACME_EMAIL=you@example.com
OAUTH2_PROXY_PROVIDER=google
OAUTH2_PROXY_CLIENT_ID=<из консоли провайдера>
OAUTH2_PROXY_CLIENT_SECRET=<из консоли провайдера>
OAUTH2_PROXY_COOKIE_SECRET=<openssl rand -base64 24 | tr -- '+/' '-_'>

RETENTION_HOURS=6
MAX_UPLOAD_MB=256
```

```bash
chmod 600 .env
cp deploy/oauth2-proxy/emails.txt.example deploy/oauth2-proxy/emails.txt   # или make init
```

В `deploy/oauth2-proxy/emails.txt` — e-mail тех, кому открыты `/workflows` и `/admin`, по одному в строке. Пустой или не заполненный список = не пустит никого.

## Шаг 3. DNS и TLS

Заведите A-запись `transcriber.example.com` на IP сервера. Домен и e-mail Caddy берёт из `SITE_ADDRESS` и `ACME_EMAIL` в `.env`, править Caddyfile не нужно. Отдельный поддомен `n8n.*` больше не используется: редактор n8n живёт на основном домене под `/workflows/`.

```bash
make up-prod
```

Оверрайд `docker-compose.prod.yml` поднимает Caddy на 80/443 (сертификаты Let's Encrypt выпускаются автоматически), oauth2-proxy и **снимает публикацию портов** с `api`, `asr`, `ollama`, `n8n` — они остаются доступны только внутри docker-сети. Это важно: без этого Ollama и Speaches торчали бы в интернет без авторизации.

Проверка:

```bash
curl -s https://transcriber.example.com/api/v1/health
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

## Вход через oauth2-proxy

Что за ним закрыто, а что нет:

| Маршрут | Доступ |
|---|---|
| `/`, `/api/v1/*` | как раньше: открыто, `/jobs*` — по `X-API-Key` |
| `/admin/`, `/api/v1/admin/*` | только сессия oauth2-proxy (+ `X-API-Key`, если задан). Без сессии страница редиректит на вход, API отвечает `401` |
| `/workflows/` (редактор n8n) | только сессия oauth2-proxy; дальше — собственный логин n8n |
| `/webhook/*`, `/webhook-test/*` | открыто намеренно: их вызывают backend и Telegram, защита — `X-Transcriber-Token` и секрет Telegram |

**Callback** — `<PUBLIC_BASE_URL>/oauth2/callback`; его нужно добавить в консоли провайдера (Google: *Credentials → OAuth client → Authorized redirect URIs*; GitHub: *OAuth Apps → Authorization callback URL*). Другой провайдер — `OAUTH2_PROXY_PROVIDER`; для `oidc` (Keycloak, Authentik) добавьте в `environment` сервиса `oauth2-proxy` переменную `OAUTH2_PROXY_OIDC_ISSUER_URL`.

### Проверка на localhost до публикации

В консоли провайдера заведите клиента с callback `http://localhost:8081/oauth2/callback` (Google и GitHub такой адрес разрешают) и в `.env` поставьте:

```ini
SITE_ADDRESS=:80
CADDY_HTTP_PORT=8081
PUBLIC_BASE_URL=http://localhost:8081
OAUTH2_PROXY_COOKIE_SECURE=false
```

```bash
make up-prod
```

Оверрайд заменяет dev-фронтенд на Caddy, поэтому стек будет на <http://localhost:8081> (а не 8080), а порты `api`/`n8n` на хосте закрываются. Проверьте:

1. <http://localhost:8081/admin/> без входа → редирект к провайдеру; после входа — страница переключателя режима;
2. e-mail не из `emails.txt` → `403 Forbidden` на `/oauth2/callback`;
3. <http://localhost:8081/workflows/> открывает редактор n8n;
4. переключите режим на `/admin` и отправьте файл — в `GET /jobs/{id}` поле `pipeline_mode` совпадёт с выбранным.

Для публикации верните значения VPS (`SITE_ADDRESS`, `CADDY_HTTP_PORT=80`, `PUBLIC_BASE_URL`, `OAUTH2_PROXY_COOKIE_SECURE=true`) и добавьте боевой callback у провайдера.

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
| Панель n8n и `/admin` | oauth2-proxy (список e-mail в `emails.txt`) + собственный логин n8n и `X-API-Key`; при желании ограничьте по IP в Caddy (`@allowed remote_ip …`) |
| Вебхуки n8n | общий секрет `X-Transcriber-Token`, проверяется первым Code-узлом |
| Внутренние сервисы | не публикуются наружу в prod-оверрайде |
| Данные | исходники удаляются сразу после обработки, метаданные — через `RETENTION_HOURS` |
| Обновления | `docker compose pull && make up-prod`; закрепите теги образов вместо `latest` перед продакшном |
| Секреты | `.env` с правами 600, вне git (`.gitignore`) |

Дополнительно стоит поставить fail2ban на SSH и ограничить размер тела запроса (уже сделано: 1024 МБ в nginx, 1024 МБ в Caddy, `MAX_UPLOAD_MB` в API).

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
