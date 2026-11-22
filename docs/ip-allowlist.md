# IP allowlist

Restrict who can reach the stack to a fixed set of IPs or CIDRs with Traefik's `ipAllowList` middleware. Useful when you always connect from a known network: a static IP, an office range, a VPN subnet. It's optional and not set up by default.

> [!WARNING]
> A wrong range locks you out of the web UI. Keep a way in that doesn't go through Traefik (SSH to the host) before you turn it on.

## How it behaves here

- pasta keeps real client addresses, so Traefik checks the client's own IP. No `X-Forwarded-For` handling is needed or trusted.
- Connections from the host itself (a browser on the host, or a tunnel such as `cloudflared` running on it) arrive with the host's own address. With podman 5 that is the host's LAN address, not `127.0.0.1` (see [Fail2ban](fail2ban.md#where-the-ban-happens)). Include it if you use either.
- A blocked client gets a 403. That is a 4xx, so after `maxretry` attempts within `findtime` (10 in 60 seconds by default) [fail2ban](fail2ban.md) bans it outright.

## 1. Define the middleware

Add it under `http.middlewares` in `traefik/dynamic/middlewares.yml`:

```yaml
    ip-allowlist:
      ipAllowList:
        sourceRange:
          - "192.168.1.0/24"   # LAN
          - "100.64.0.0/10"    # Tailscale
          - "203.0.113.5/32"   # a single IPv4 address
          - "2001:db8::1/128"  # a single IPv6 address
```

Replace the ranges with your own.

## 2. Apply it

### To everything (recommended)

Attach it to the HTTPS entrypoint in `traefik/traefik.yml`, so it covers zellij, `/stats` and the SSO routes in one place:

```yaml
  websecure:
    address: ":${HTTPS_PORT}"
    http:
      tls: {}
      middlewares:
        - ip-allowlist@file
```

The `web` entrypoint only redirects to HTTPS, so it doesn't need the middleware. Add the same `middlewares` block under `web` if you don't want unlisted clients to get even the redirect.

### To individual routers

To allow only some routes, add `ip-allowlist` to the `middlewares` list of each router you want to restrict. In `traefik/dynamic/routers.yml` those are `zellij` and `stats`. The SSO routers are in `traefik/sso/oauth2-proxy.yml` (`sso-oauth2`) and `traefik/sso/callback.yml` (`sso-callback`).

```yaml
    zellij:
      rule: "ZELLIJ_RULE"
      entryPoints:
        - websecure
      service: zellij
      middlewares:
        - ip-allowlist
        - security-headers
        - real-ip
        - rate-limit
        # SSO_MIDDLEWARE
      tls: {}
```

Put it first so blocked clients are turned away before anything else runs, including the SSO check. Keep the `ZELLIJ_RULE` and `# SSO_MIDDLEWARE` lines as they are: `traefik/entrypoint.sh` replaces them at start.

## 3. Restart Traefik

`traefik/dynamic/` isn't hot-reloaded. Traefik reads a copy of it that `traefik/entrypoint.sh` makes at startup, and it reads `traefik.yml` only at startup. Restart it after either change:

```bash
podman compose restart traefik
```

## Checking

From an address outside the list:

```bash
curl -sk -o /dev/null -w '%{http_code}\n' "https://$DOMAIN:$HTTPS_PORT/"   # 403
```

`just access` shows the request with `DownstreamStatus` 403 and the `ClientAddr` Traefik compared against the list.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Locked out, even from an allowed network | Your address isn't what you expected (NAT, IPv6 instead of IPv4). `just access` shows the `ClientAddr` Traefik saw. Add it, then restart Traefik. |
| Locked out, and now connections are refused | The 403s got you banned. `just fail2ban-unban <ip>` lets you back in. |
| Browser on the host is blocked | The host's own LAN address isn't in `sourceRange`. |
| No effect at all | Traefik wasn't restarted, or the middleware name in the router or entrypoint doesn't match. `just logs-traefik` reports unknown middlewares. |
