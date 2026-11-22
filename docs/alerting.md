# Alerting

The alerter sidecar tails Traefik's JSON access log and fires notifications whenever a WebSocket connection is detected, i.e. when someone establishes an actual terminal session, not just loads the page.

## How it works

1. Traefik writes JSON access logs to a shared volume (`traefik-logs`)
2. The alerter container reads from that volume, tailing the log file
3. On each new line it checks `DownstreamStatus == ALERT_ON_STATUS` (default `0`: Traefik logs hijacked WebSocket connections with status 0, and writes the entry when the connection closes)
4. If matched and the client IP is outside its cooldown window, it extracts client IP, path, user agent, timestamp and, with [SSO](sso.md) on, the signed-in user, and dispatches to all configured destinations

```mermaid
flowchart TD
    log[("📜 access.log line")] --> parse{"🧩 valid JSON?"}
    parse -- "❌ no" --> skip(["🗑️ ignore"])
    parse -- "✅ yes" --> ws{"🔌 status 0 and<br/>session path?"}
    ws -- "✅ yes" --> store[("🗄️ store in stats.db")]
    ws -- "❌ no" --> match
    store --> match{"🎯 status ==<br/>ALERT_ON_STATUS?"}
    match -- "❌ no" --> skip
    match -- "✅ yes" --> cool{"⏱️ IP alerted within<br/>cooldown?"}
    cool -- "✅ yes" --> quiet(["🔇 suppress duplicate"])
    cool -- "❌ no" --> send["🚨 dispatch alert"]
    send --> tg["✈️ Telegram"] & dc["🎮 Discord"] & wh["🪝 Webhooks"]

    classDef store fill:#f1f5f9,stroke:#64748b,stroke-width:2px,color:#1e293b
    classDef decision fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#3b0764
    classDef drop fill:#fee2e2,stroke:#dc2626,stroke-width:2px,color:#7f1d1d
    classDef alert fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#78350f

    class log,store store
    class parse,ws,match,cool decision
    class skip,quiet drop
    class send,tg,dc,wh alert
```

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
User: `alice <alice@example.com>`
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
  "status": 0,
  "service": "zellij",
  "sso_user": "alice",
  "sso_email": "alice@example.com"
}
```

`sso_user` and `sso_email` are empty strings when SSO is off.

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
| `{status}` | `0` |
| `{service}` | `zellij` |
| `{sso_user}` | `alice` (empty without SSO) |
| `{sso_email}` | `alice@example.com` (empty without SSO) |
| `{jail}`, `{bantime}`, `{failures}` | ban alerts only, see [Ban alerts](#ban-alerts) |

If the template is invalid JSON after substitution, or references an unknown placeholder, the alerter logs a warning and falls back to the default payload.

## Ban alerts

Every time fail2ban bans an IP, the same channels get an alert. The alerter mounts fail2ban's database (`fail2ban-data` volume) read-only and checks it every 5 seconds, so fail2ban itself needs no extra action. Bans already in the database when the alerter starts aren't announced again.

```
🚫 IP banned by fail2ban
User: `alice <alice@example.com>`
IP: `203.0.113.9`
Jail: `traefik-zellij` (10 failures, banned for 3600s)
Last path: `/command/login` (401)
UA: `Mozilla/5.0 ...`
Time: `2026-11-22T19:37:40+00:00`
```

The `User` line comes from the log lines fail2ban kept as evidence for the ban (up to 10), so it names every SSO account that produced the failures, comma-separated. It only shows up when someone signed in through SSO and then failed, typically by trying bad zellij tokens. People who aren't signed in get a redirect to the IdP, not a 4xx, so they never count towards a ban. Users refused by `SSO_ALLOWED_GROUPS` get a 403 before any identity is set, so those bans have no user.

Webhooks get the usual fields plus `jail`, `bantime` and `failures`:

```json
{
  "event": "fail2ban_ban",
  "timestamp": "2026-11-22T19:37:40+00:00",
  "client_ip": "203.0.113.9",
  "x_forwarded_for": "",
  "user_agent": "Mozilla/5.0 ...",
  "path": "/command/login",
  "status": 401,
  "service": "zellij",
  "sso_user": "alice",
  "sso_email": "alice@example.com",
  "jail": "traefik-zellij",
  "bantime": 3600,
  "failures": 10
}
```

A `WEBHOOK_BODY_TEMPLATE` is used for both events. Stick to the shared fields, or reference `{jail}`, `{bantime}` or `{failures}` knowing that connection alerts will then fall back to the default payload. Branch on `event` in the receiver if the two need different handling.

To turn ban alerts off:

```env
BAN_ALERTS=false
```

The alerter log prints `[alerter] Ban: 203.0.113.9 (alice) in traefik-zellij` for each one.

### On the stats dashboard

`/stats` shows a **Banned Now** and a **Bans (24h)** card plus a **fail2ban Bans** table: time, SSO user, IP, jail, failures, the last request that counted, and whether the ban is still active (and until when). It reads fail2ban's database directly, so it lists every ban fail2ban still keeps (`dbpurgeage`, 1 day by default), including ones from before the alerter started. `BAN_ALERTS=false` only stops the notifications; the dashboard section stays.

## SSO user

When [SSO](sso.md) is on, Traefik's forwardAuth middleware adds the signed-in identity to every request and the access log records it. The alerter reads it from there and puts it in connection and [ban](#ban-alerts) alerts, webhook payloads and the stats dashboard (a **User** column under Recent Connections and an **SSO Users (1h)** card).

| `SSO_MODE` | User taken from (first one set) | Email from |
|---|---|---|
| `oauth2-proxy` | `X-Auth-Request-Preferred-Username`, `X-Auth-Request-User`, `X-Auth-Request-Email` | `X-Auth-Request-Email` |
| `forward-auth` | `Remote-User` (Authelia), `X-Authentik-Username` (Authentik), then the email headers | `Remote-Email`, `X-Authentik-Email` |
| `off` | nothing | nothing |

The alerter gets `SSO_MODE` from the same `.env`, so there's nothing extra to configure. It only trusts the headers of the active mode: forwardAuth strips those from the client's request before adding the IdP's answer, while any other header could have been sent by the client. With SSO off a client can send any header it likes, so no user is shown.

`X-Auth-Request-User` is the OIDC `sub`, which for Keycloak and Authentik is an opaque ID. That's why the preferred username comes first.

## Alert deduplication

`ALERT_COOLDOWN_SECONDS` (default: `60`) suppresses repeat alerts from the same IP within the configured window. With SSO on the window is per user and IP, so two people behind the same NAT each get their own alert. This prevents notification spam if a client reconnects rapidly.

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

By default alerts fire on `DownstreamStatus` 0, which is how Traefik logs WebSocket connections. Change via `.env`:

```env
ALERT_ON_STATUS=200   # alert on every successful page load instead
```

## Alerter logs

```bash
just logs-alerter
```

Each detected connection is printed to stdout:
```
[alerter] Connection: 1.2.3.4 -> /my-session
[alerter] Connection: alice@1.2.3.4 -> /my-session     # with SSO
```

Dispatch failures (network errors, bad tokens) are printed to stderr and do not crash the alerter.
