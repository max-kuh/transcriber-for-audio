# 05. Telegram

## Шаг 1. Создать бота

1. Напишите [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Задайте имя и username (должен заканчиваться на `bot`).
3. Скопируйте токен вида `7123456789:AAF…` в `.env`:

```ini
TELEGRAM_BOT_TOKEN=7123456789:AAF...
```

Полезные команды BotFather: `/setdescription`, `/setcommands`, `/setprivacy` (для групп — см. ниже).

## Шаг 2. Узнать `chat_id`

**Личный чат.** Напишите боту любое сообщение, затем:

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
  | python3 -c 'import sys,json;[print(u["message"]["chat"]["id"], u["message"]["chat"].get("title") or u["message"]["chat"].get("username")) for u in json.load(sys.stdin)["result"] if "message" in u]'
```

**Группа.** Добавьте бота в группу, напишите там сообщение, повторите команду. `chat_id` группы отрицательный: `-1001234567890`.

**Канал.** Добавьте бота администратором, опубликуйте пост, посмотрите `channel_post.chat.id`.

```ini
TELEGRAM_CHAT_ID=-1001234567890
```

Значение из `.env` — дефолт; в UI поле «chat_id» перекрывает его для конкретной задачи.

## Шаг 3. Проверка

```bash
curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
  -d "chat_id=$TELEGRAM_CHAT_ID" -d "text=Transcriber на связи"
```

## Особенности отправки

* **Лимит 4096 символов** на сообщение. `backend/app/delivery.py` режет текст по строкам и шлёт несколькими сообщениями; в n8n-варианте текст обрезается до 4000 символов — при необходимости добавьте туда такой же сплиттер (узел Code с циклом по чанкам).
* **`parse_mode: HTML`** — заголовок оформляется жирным. Если расшифровка содержит `<` или `&`, Telegram вернёт 400. Для произвольного текста безопаснее убрать `parse_mode` в `delivery.py`.
* **Rate limit** — примерно 30 сообщений в секунду суммарно и 20 в минуту в одну группу. При массовой рассылке добавляйте паузу.
* Длинные протоколы удобнее слать файлом: `sendDocument` вместо `sendMessage`. В n8n это узел Telegram с операцией `Send Document` и входным бинарником из `Convert to File`.

## Голосовой бот (входящий сценарий)

Workflow `02-telegram-voice-bot` принимает голосовые прямо в боте. Условие работы — Telegram должен достучаться до вашего n8n по **HTTPS с валидным сертификатом**.

**На VPS:** поднимите n8n за Caddy (см. [07-vps-deploy.md](07-vps-deploy.md)), в `.env`:

```ini
N8N_HOST=n8n.example.com
N8N_PROTOCOL=https
WEBHOOK_URL=https://n8n.example.com/
```

**Локально:** временный туннель

```bash
cloudflared tunnel --url http://localhost:5678
# полученный адрес пропишите в WEBHOOK_URL и перезапустите: docker compose up -d n8n
```

**Приватность в группах.** По умолчанию бот в группе видит только сообщения с упоминанием и команды. Чтобы он получал все голосовые: BotFather → `/setprivacy` → `Disable`.

## Управление подписью

Бот понимает хэштеги в подписи к голосовому/аудио:

| Подпись | Результат |
|---|---|
| `#кратко` или `#summary` | краткое содержание + полная расшифровка |
| `#протокол` или `#minutes` | протокол встречи |
| `#конспект` или `#bullets` | структурированный конспект |
| без хэштега | только расшифровка |

Добавить свою команду: узел `Подготовка` в workflow, блок разбора `caption`, и соответствующая ветка в промпте узла `LLM — постобработка`.
