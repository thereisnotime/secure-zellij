# Alerting

The alerter sidecar tails Traefik's JSON access log and fires notifications whenever a WebSocket upgrade (HTTP 101) is detected — i.e. when someone establishes an actual terminal session, not just loads the page.

## How it works

1. Traefik writes JSON access logs to a shared volume (`traefik-logs`)
2. The alerter container reads from that volume, tailing the log file
3. On each new line it checks `DownstreamStatus == 101`
4. If matched, it extracts client IP, path, user agent, and timestamp and dispatches to all configured destinations

## Telegram

**1.** Create a bot via [@BotFather](https://t.me/BotFather) — send `/newbot` and follow the prompts. Copy the token.

**2.** Get your chat ID — send a message to the bot then visit:
```
https://api.telegram.org/bot<TOKEN>/getUpdates
```
The `chat.id` field in the response is your chat ID. For channels use the channel's numeric ID (starts with `-100`).

**3.** Set in `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=-1001234567890
```

Example alert:
```
🔌 Zellij connection
IP: `1.2.3.4`
Path: `/my-session`
UA: `Mozilla/5.0 (X11; Linux x86_64) ...`
Time: `2026-01-01T22:00:00+00:00`
```

## Discord

Set `DISCORD_WEBHOOK_URL` to a Discord channel webhook URL.

To get one: open your Discord server → **Server Settings** → **Integrations** → **Webhooks** → **New Webhook** → copy the URL.

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1234567890/xxxxxxxxxxxx
```

The alerter posts to Discord's native webhook format (`{"content": "..."}`), so no body template is needed. Example alert message:

```
🔌 Zellij connection
IP: `1.2.3.4`  Path: `/my-session`  Time: `2026-01-01T22:00:00+00:00`
```

## Generic webhooks

Any number of HTTP POST endpoints, comma-separated:

```env
WEBHOOK_URLS=https://n8n.example.com/webhook/abc,https://hooks.slack.com/services/xxx
```

Each URL receives a POST with this JSON body:

```json
{
  "event": "zellij_connect",
  "timestamp": "2026-01-01T22:00:00+00:00",
  "client_ip": "1.2.3.4",
  "x_forwarded_for": "",
  "user_agent": "Mozilla/5.0 ...",
  "path": "/my-session",
  "status": 101,
  "service": "zellij"
}
```

## Webhook body template

If the target service expects a different payload shape, set `WEBHOOK_BODY_TEMPLATE` to a JSON string with `{field}` placeholders:

```env
WEBHOOK_BODY_TEMPLATE={"text": "Zellij connection from {client_ip} on {path} at {timestamp}"}
```

Available placeholders:

| Placeholder | Example value |
|---|---|
| `{event}` | `zellij_connect` |
| `{timestamp}` | `2026-01-01T22:00:00+00:00` |
| `{client_ip}` | `1.2.3.4` |
| `{x_forwarded_for}` | `1.2.3.4, 10.0.0.1` |
| `{user_agent}` | `Mozilla/5.0 ...` |
| `{path}` | `/my-session` |
| `{status}` | `101` |
| `{service}` | `zellij` |

If the template is invalid JSON after substitution, or references an unknown placeholder, the alerter logs a warning and falls back to the default payload.

## Alert deduplication

`ALERT_COOLDOWN_SECONDS` (default: `60`) suppresses repeat alerts from the same IP within the configured window. This prevents notification spam if a client reconnects rapidly.

```env
ALERT_COOLDOWN_SECONDS=60   # suppress repeated alerts from same IP within 60s
```

Set to `0` to disable deduplication and receive every alert:

```env
ALERT_COOLDOWN_SECONDS=0
```

## Combining channels

All configured channels (Telegram, Discord, generic webhooks) fire simultaneously for each matching event. You can use any combination — set only the vars you need and leave the rest empty.

## Changing the trigger condition

By default alerts fire on HTTP 101 (WebSocket upgrade). Change via `.env`:

```env
ALERT_ON_STATUS=200   # alert on every successful page load instead
```

## Alerter logs

```bash
just logs-alerter
```

Each detected connection is printed to stdout:
```
[alerter] Connection: 1.2.3.4 → /my-session
```

Dispatch failures (network errors, bad tokens) are printed to stderr and do not crash the alerter.
