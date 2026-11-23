# Configuration

All settings live in `.env` next to `compose.yaml`. Start from the template:

```bash
cp .env.example .env
```

Only `DOMAIN` is required. Defaults below are the ones `compose.yaml` uses when a
variable is unset or empty. `.env.example` sets a few of them explicitly.

After changing `.env`, apply it with `just restart`. That recreates the
containers, which is needed because environment variables are only read at
creation.

## Core

| Variable | Default | Description |
|---|---|---|
| `DOMAIN` | required | Hostname Traefik serves, e.g. `zellij.example.com`. |
| `EXTRA_DOMAINS` | empty | More hostnames or IPs to accept, comma-separated, e.g. `192.168.1.100,fenrir.lan`. See [Multi-domain & LAN access](multi-domain.md). |
| `HTTP_PORT` | `8080` | Host port for HTTP. Only redirects to HTTPS. |
| `HTTPS_PORT` | `8443` | Host port for HTTPS. |
| `ACME_EMAIL` | empty | Contact address for Let's Encrypt. See [TLS](tls.md). |
| `ZELLIJ_BIN` | `zellij` on `PATH` | zellij binary the `just` recipes use for the web server and tokens. Pin it when your running sessions come from a different zellij version than the one on `PATH`; the web UI shows sessions of other versions as exited. |
| `TZ` | `UTC` | Timezone for fail2ban's log timestamps. |

### Ports

Rootless podman can't bind ports below 1024 unless the host allows it. pasta, which
publishes the ports, runs as your user. Two ways to serve on 80 and 443:

- **Forward on the router or firewall** (recommended): keep `8080`/`8443` and forward
  external 80 → 8080 and 443 → 8443.
- **Lower the limit on the host**: `sudo sysctl net.ipv4.ip_unprivileged_port_start=80`
  (persist it in `/etc/sysctl.d/`), then set `HTTP_PORT=80` and `HTTPS_PORT=443`.
  This lets any user on the host bind those ports.

Let's Encrypt's HTTP-01 challenge needs port 80 reachable from the internet, either
way.

## Network namespace

Traefik and fail2ban share the network namespace of the `edge` container (see
[Fail2ban: where the ban happens](fail2ban.md#where-the-ban-happens)). pasta forwards
these host-loopback ports into it: 8082 (zellij), 8083 (alerter) and
`SSO_PROXY_PORT` (oauth2-proxy).

| Variable | Default | Description |
|---|---|---|
| `EDGE_PASTA_EXTRA` | empty | Extra pasta options, starting with a comma. Use it when Traefik must reach something else on the host's loopback, e.g. `,-T,9091` for an Authelia on `127.0.0.1:9091`. |

## Alerts

See [Alerting](alerting.md) for message formats, payloads and templates.

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | empty | Bot token from [@BotFather](https://t.me/BotFather). |
| `TELEGRAM_CHAT_ID` | empty | Chat or channel ID. Both Telegram settings are needed. |
| `DISCORD_WEBHOOK_URL` | empty | Discord channel webhook URL. |
| `WEBHOOK_URLS` | empty | Comma-separated URLs that get a JSON `POST` per event. |
| `WEBHOOK_BODY_TEMPLATE` | empty | JSON template with `{field}` placeholders for the webhook body. |
| `ALERT_COOLDOWN_SECONDS` | `60` | Suppress repeat connection alerts from the same IP (and SSO user) for this long. `0` disables it. |
| `ALERT_ON_STATUS` | `0` | `DownstreamStatus` that counts as a connection. Traefik logs terminal WebSocket sessions as `0`. |
| `BAN_ALERTS` | `true` | Alert when fail2ban bans an IP. |

## Stats dashboard

The dashboard is at `https://<DOMAIN>/stats` and, without Traefik, at
`http://127.0.0.1:8083` on the host.

| Variable | Default | Description |
|---|---|---|
| `STATS_USERNAME` | empty | Basic-auth user for the dashboard. Empty means no login (fine behind SSO, otherwise set it). |
| `STATS_PASSWORD` | empty | Basic-auth password. |
| `UNBAN_ENABLED` | `true` | Show the unban button. It only appears when the page has a login (basic auth or SSO). See [Fail2ban](fail2ban.md#unbanning-from-the-dashboard). |

## fail2ban

Thresholds (`maxretry`, `findtime`, `bantime`) are in
`fail2ban/jail.d/traefik-zellij.conf`, not in `.env`. See [Fail2ban](fail2ban.md#tuning).

| Variable | Default | Description |
|---|---|---|
| `F2B_LOG_LEVEL` | `INFO` | fail2ban log level: `CRITICAL`, `ERROR`, `WARNING`, `NOTICE`, `INFO` or `DEBUG`. |
| `F2B_DB_PURGE_AGE` | `7d` | How long fail2ban keeps ban history. Repeat bans grow from it (`bantime.increment`), and the dashboard lists bans this far back. |

## SSO

Off by default. [SSO](sso.md) has the setup for Authelia, Keycloak and Authentik
and explains each variable.

| Variable | Default | Used by |
|---|---|---|
| `SSO_MODE` | `off` | `off`, `oauth2-proxy` or `forward-auth` |
| `SSO_PROVIDER` | `oidc` | oauth2-proxy (`keycloak-oidc` also works) |
| `SSO_PROVIDER_NAME` | `SSO` | oauth2-proxy |
| `SSO_OIDC_ISSUER_URL` | empty | oauth2-proxy |
| `SSO_CLIENT_ID` | empty | oauth2-proxy |
| `SSO_CLIENT_SECRET` | empty | oauth2-proxy |
| `SSO_COOKIE_SECRET` | empty | oauth2-proxy (`just sso-cookie-secret`) |
| `SSO_OIDC_SCOPE` | `openid email profile` | oauth2-proxy |
| `SSO_GROUPS_CLAIM` | `groups` | oauth2-proxy |
| `SSO_ALLOWED_GROUPS` | empty (anyone who can log in) | oauth2-proxy |
| `SSO_ALLOWED_EMAIL_DOMAINS` | `*` | oauth2-proxy |
| `SSO_ALLOW_UNVERIFIED_EMAIL` | `false` | oauth2-proxy |
| `SSO_COOKIE_EXPIRE` | `12h` | oauth2-proxy |
| `SSO_REDIRECT_URL` | built per request | oauth2-proxy |
| `SSO_EXTRA_REDIRECT_DOMAINS` | empty | oauth2-proxy |
| `SSO_SKIP_CA_VERIFY` | `false` | oauth2-proxy |
| `SSO_PROXY_PORT` | `4180` | oauth2-proxy listen port on 127.0.0.1 |
| `SSO_FORWARD_AUTH_URL` | empty | forward-auth |
| `SSO_CALLBACK_PREFIX` | empty | forward-auth (Authentik) |
| `SSO_CALLBACK_URL` | empty | forward-auth (Authentik) |

`just sso-check` validates these and prints the redirect URI to register with
your IdP.

## Files you might edit

| File | What for |
|---|---|
| `traefik/traefik.yml` | Entrypoints, access log, Let's Encrypt resolver |
| `traefik/dynamic/routers.yml` | Routes, TLS per router |
| `traefik/dynamic/middlewares.yml` | Security headers, rate limit (`average: 20`, `burst: 50` per client) |
| `traefik/dynamic/tls.yml` | TLS versions and ciphers |
| `fail2ban/jail.d/traefik-zellij.conf` | Ban thresholds |
| `fail2ban/filter.d/traefik-zellij.conf` | What counts as a failure (run `just test-filter` after changing it) |

Changes to these take effect after `just restart`.
