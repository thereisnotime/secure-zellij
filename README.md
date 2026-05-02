# secure-zellij

Expose [zellij](https://zellij.dev) web terminal sessions over HTTPS via [Traefik](https://traefik.io), with real-time connection alerts to Telegram and/or any webhook.

Runs as a podman compose stack. Zellij itself runs on the host; Traefik terminates TLS and proxies to it. An alerter sidecar watches Traefik access logs and fires notifications on every successful WebSocket connection (real client IP, path, user agent, timestamp).

## Architecture

```
Browser → Traefik (TLS) → host:8082 (zellij web)
                  ↓
          access.log (JSON)
                  ↓
            alerter → Telegram / webhooks
```

## Requirements

- [podman](https://podman.io) + [podman-compose](https://github.com/containers/podman-compose)
- [zellij](https://zellij.dev) installed and running on the host (`zellij web --start -d`)

## Quick start

```bash
cp .env.example .env
# edit .env — set DOMAIN at minimum
make up
make token   # generates a zellij web login token
```

Open `https://<DOMAIN>` in a browser and paste the token.

## TLS modes

### Self-signed (default)

No configuration needed. Traefik auto-generates a self-signed certificate.
The browser will show a warning — accept it or add the cert to your trust store.

### Let's Encrypt

1. Uncomment the `certificatesResolvers` block in `traefik/traefik.yml` and set your email.
2. In `traefik/dynamic/routers.yml`, replace `tls: {}` with:
   ```yaml
   tls:
     certResolver: letsencrypt
   ```
3. Set `ACME_EMAIL` in `.env`.
4. Port 80 must be reachable from the internet for the HTTP-01 challenge.

## Alerting

Set any combination of the following in `.env`:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Chat or channel ID to send alerts to |
| `WEBHOOK_URLS` | Comma-separated list of HTTP POST webhook URLs |

Alert payload (JSON):
```json
{
  "event": "zellij_connect",
  "timestamp": "2026-01-01T00:00:00+00:00",
  "client_ip": "1.2.3.4",
  "x_forwarded_for": "",
  "user_agent": "Mozilla/5.0 ...",
  "path": "/my-session",
  "status": 101,
  "service": "zellij"
}
```

Alerts fire on HTTP 101 (WebSocket upgrade) — the moment a terminal session is established, not just a page load.

## Commands

| Command | Action |
|---|---|
| `make up` | Start the stack |
| `make down` | Stop the stack |
| `make restart` | Restart all services |
| `make build` | Rebuild images (no cache) |
| `make logs` | Follow all logs |
| `make token` | Generate a zellij web login token |
| `make status` | Show zellij web server + container status |

## File structure

```
.
├── compose.yaml
├── .env.example
├── Makefile
├── traefik/
│   ├── traefik.yml          # static config (entrypoints, providers, access log)
│   └── dynamic/
│       ├── routers.yml      # routing rules + backend (host:8082)
│       ├── middlewares.yml  # security headers, real-IP
│       └── tls.yml          # TLS options (min version, ciphers)
└── alerter/
    ├── Dockerfile
    ├── alerter.py           # log tailer + alert dispatcher
    └── requirements.txt
```

## Security notes

- Zellij token auth is handled by the zellij web server itself — Traefik does not add additional auth.
- TLS is enforced; HTTP redirects to HTTPS automatically.
- `sniStrict: true` rejects connections without a matching SNI header.
- For production, use Let's Encrypt or a real certificate — self-signed exposes you to MITM if the browser warning is bypassed.
