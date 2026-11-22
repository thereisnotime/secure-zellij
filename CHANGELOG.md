# Changelog

Notable changes to secure-zellij. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). There are no tagged
releases yet; everything is on `main`.

## [Unreleased]

### Upgrading

Run `just upgrade`. It pulls, rebuilds the alerter and recreates every container.
Two things to check first:

- **Ports**: `.env.example` now uses `8080`/`8443`. Rootless podman can't bind
  ports below 1024 unless the host allows it, see
  [Ports](docs/configuration.md#ports).
- **Forward-auth IdP on the host's loopback**: Traefik now runs in its own
  network namespace. If `SSO_FORWARD_AUTH_URL` points at `127.0.0.1`, add the port
  with `EDGE_PASTA_EXTRA` (e.g. `,-T,9091`).

### Security

- fail2ban bans now actually block. Rootless podman can't change the host
  firewall, so the iptables action never applied: bans were recorded and alerted
  while the client kept connecting. An `edge` container now owns a pasta network
  namespace and the published ports. Traefik and fail2ban join it, and fail2ban
  blocks banned IPs with nftables there.
- The access log no longer stores `Cookie`, `Authorization` and
  `Proxy-Authorization` headers.
- Headers that alias identity headers (e.g. `X_Auth_Request_User`) are dropped.
- The stats dashboard escapes quotes. A crafted User-Agent could previously add
  attributes to the page.
- Every repo bind mount and tmpfs volume carries an SELinux label (`:z`). On an
  enforcing host, fail2ban couldn't create its socket and silently stopped
  banning, and a fresh clone couldn't start Traefik.

### Added

- Optional SSO: `SSO_MODE=oauth2-proxy` (any OIDC IdP) or `forward-auth`
  (Authelia, Authentik), with guides for Authelia, Keycloak and Authentik.
- Ban and unban alerts, and the signed-in SSO user in every alert.
- Bans on the `/stats` dashboard, and an unban button that goes through a
  `fail2ban-unban` helper with no network that can only run `unbanip`.
- `just upgrade`, `just bench`, `just selinux-check`, `just test-ban`,
  `just test-unban`, `just test-filter`, `just test-sso`.
- End-to-end tests for bans, unbans and SSO, run in CI.
- Healthchecks on fail2ban, the unban helper and the alerter.
- Docs: configuration reference, SSO, SELinux, performance; this changelog,
  CONTRIBUTING and SECURITY.

### Changed

- The fail2ban filter is about 80 times cheaper per line and matches the same
  lines. At 20 req/s fail2ban drops from 19% to 2% of a core.
- `just restart` and `just upgrade` stop the whole stack and start it again.
  Containers in a shared network namespace can't be recreated in place.
- Traefik v3.7.19, Python 3.15, uv 0.13, current GitHub Actions; images pinned by
  digest.
- The test scripts work with podman-compose as well as docker-compose.

### Fixed

- Every alerter restart replayed the access log into `stats.db` again, so the
  dashboard's connection counts grew with each restart or upgrade. Existing
  duplicates are removed on the next start.
- The startup check of `WEBHOOK_BODY_TEMPLATE` warned about templates using ban
  or unban fields (`jail`, `bantime`, `failures`, `unbanned_by`).
- Template examples used single braces. Templates go through `str.format`, so
  JSON braces must be doubled: `{{"text": "{client_ip}"}}`.
- The first alert after a host boot was suppressed by the cooldown.
- `just up` after a pull kept the old alerter image. `just upgrade` rebuilds it.
- podman-compose dropped the alerter's healthcheck (it was in the Dockerfile). It
  now lives in `compose.yaml` and probes `/health`.

## 2026-05

First release and the months after it: Traefik in front of the zellij web server
with TLS and security headers, connection alerts to Telegram, Discord and
webhooks, multi-domain support, rate limiting, fail2ban as a container, the
`/stats` dashboard, log rotation and the `just` recipes.
