# Multiple domains and LAN access

Traefik only answers requests whose `Host` header matches `DOMAIN`. This page covers adding more names or IPs with `EXTRA_DOMAINS` (a LAN IP, a `.lan` hostname), making those names resolve, and reaching the stack from outside your network.

## EXTRA_DOMAINS

```dotenv
DOMAIN=zellij.example.com
EXTRA_DOMAINS=192.168.1.100,fenrir.lan
```

Comma-separated, no spaces. `traefik/entrypoint.sh` turns the list into one router rule, used by every router (zellij, `/stats`, the HTTP redirect and the SSO routes):

```text
Host(`zellij.example.com`) || Host(`192.168.1.100`) || Host(`fenrir.lan`)
```

A request for any other host gets a 404 from Traefik. 404 is a 4xx, so a client that keeps trying a wrong name eventually gets banned by [fail2ban](fail2ban.md).

Apply a change with `just restart`. The variable is read when the Traefik container is created, so a plain restart of the container isn't enough. `just status` prints the resulting URLs.

## LAN access by IP

Add the host's LAN IP to `EXTRA_DOMAINS` and connect to `https://192.168.1.100:8443` (or whatever `HTTPS_PORT` is). No DNS needed.

Expect a certificate warning. The default certificate is self-signed, and a Let's Encrypt certificate can't cover a private IP. If you want no warning on the LAN, use a hostname and a certificate from your own CA ([TLS: your own certificate](tls.md#your-own-certificate)).

## LAN hostnames

Pick a name, add it to `EXTRA_DOMAINS`, and make it resolve on the clients. Either add it to `/etc/hosts` on each client:

```text
192.168.1.100 fenrir.lan
```

or add a local DNS record on the router or resolver, so every LAN client picks it up:

| Resolver | Where |
|---|---|
| Pi-hole | Local DNS, DNS Records |
| AdGuard Home | Filters, DNS rewrites |
| OpenWrt | Network, DHCP and DNS, Hostnames |
| pfSense / OPNsense | Services, DNS Resolver, Host Overrides |

## Extra domains with SSO

With [SSO](sso.md) on, every name in `EXTRA_DOMAINS` also has to work with the IdP:

- **oauth2-proxy mode:** register `https://<name>:<port>/oauth2/callback` for each name with the IdP (no `:<port>` when it is 443), and list the names in `SSO_EXTRA_REDIRECT_DOMAINS` so oauth2-proxy will redirect back to them after login.
- **forward-auth mode:** the IdP sets its session cookie for its own domain, so names outside that domain won't stay logged in. Authelia can't set a cookie for an IP at all.

Logging in by bare IP doesn't work in either mode. Use a hostname.

## Access from outside the LAN

**Port forwarding.** Forward ports 80 and 443 on the router to the host's `HTTP_PORT` and `HTTPS_PORT` (8080 and 8443 by default), point a public DNS name at your IP, set it as `DOMAIN`, and switch to [Let's Encrypt](tls.md#lets-encrypt-http-01). Clients connect directly, so Traefik logs, alerts on and bans their real IPs.

**VPN.** With [Tailscale](https://tailscale.com) or WireGuard, clients reach the host on its VPN address. Add that address, or the MagicDNS name, to `EXTRA_DOMAINS`. Traefik sees each client's VPN address, so bans and alerts stay per client.

**Tunnels and proxies on the host** (Cloudflare Tunnel, `tailscale serve`/`tailscale funnel`, ngrok) proxy to `https://127.0.0.1:${HTTPS_PORT}`. This works, with two catches:

- Every request arrives from the host itself. Traefik doesn't trust `X-Forwarded-For`, so the access log, alerts and the `/stats` dashboard show the host's address for every client.
- fail2ban ignores the host's own addresses (`ignoreself`), so nothing that comes through the tunnel ever gets banned. Rate limiting is per source IP too, so all tunnel users share one bucket.

If you use a tunnel, put the tunnel provider's own access controls (Cloudflare Access, a tailnet ACL) in front of it, or enable [SSO](sso.md).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| 404 on a new name | `EXTRA_DOMAINS` changed but Traefik wasn't recreated (`just restart`), or a typo or space in the list. `podman exec secure-zellij-traefik grep rule /etc/traefik/dynamic-live/routers.yml` shows the rule Traefik is using. |
| Connection refused from another machine | The host firewall blocks `HTTPS_PORT` (firewalld, ufw), or you are using the default 443 instead of the configured port. |
| Connection refused from one client only, others are fine | That client is banned. Check `just fail2ban-banned`, and see [Fail2ban](fail2ban.md). |
| SSO login loops on the extra name | The IdP has no redirect URI for that name, or the name is an IP. See [Extra domains with SSO](#extra-domains-with-sso). |
