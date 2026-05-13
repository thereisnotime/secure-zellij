# IP Allowlist

Traefik's built-in `ipAllowList` middleware lets you restrict access to specific IPs or CIDRs. This is an optional hardening measure — useful if you have a static IP, an office network, or a VPN exit node you always connect from.

**This is an example. Do not apply it unless you actually want IP restrictions** — locking yourself out requires direct server access to fix.

## 1. Add the middleware

In `traefik/dynamic/middlewares.yml`, add under `http.middlewares`:

```yaml
    ip-allowlist:
      ipAllowList:
        sourceRange:
          - "192.168.1.0/24"
          - "10.0.0.0/8"
          - "203.0.113.5/32"
```

Replace the ranges with your actual allowed IPs/CIDRs.

## 2. Add it to the zellij router

In `traefik/dynamic/routers.yml`, add `ip-allowlist` to the `zellij` router's middleware list:

```yaml
    zellij:
      middlewares:
        - security-headers
        - real-ip
        - rate-limit
        - ip-allowlist
```

## Notes

- Changes to `traefik/dynamic/` are hot-reloaded by Traefik — no restart needed.
- If `EXTRA_DOMAINS` includes a LAN IP or hostname, make sure the LAN CIDR is in `sourceRange`.
- To allow a single IP, use `/32` (IPv4) or `/128` (IPv6).
