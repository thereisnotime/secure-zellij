# Multi-domain and LAN Access

By default Traefik only accepts requests matching `DOMAIN`. Use `EXTRA_DOMAINS` to add additional hostnames or IPs — useful for LAN access, alternate DNS names, or accessing by IP directly.

## Configuration

```env
DOMAIN=zellij.example.com
EXTRA_DOMAINS=192.168.1.100,fenrir.lan,zellij.local
```

`EXTRA_DOMAINS` is comma-separated, no spaces. Traefik builds the router rule as:

```
Host(`zellij.example.com`) || Host(`192.168.1.100`) || Host(`fenrir.lan`) || Host(`zellij.local`)
```

Restart after changing: `just down && just up`

## LAN access by IP

The simplest approach — no DNS required. Add your machine's LAN IP to `EXTRA_DOMAINS`:

```env
EXTRA_DOMAINS=192.168.1.100
```

Clients on the same network connect to `https://192.168.1.100:8443`. They will get a self-signed cert warning since the cert has no SAN for an IP. Accept it or see [TLS docs](tls.md) for a proper cert.

## Local hostname (`.local` / `.lan`)

Add a hostname to `/etc/hosts` on the server:

```
192.168.1.100 fenrir.lan
```

Then add it to `EXTRA_DOMAINS`. On client machines, also add the entry to their `/etc/hosts`, or configure your router's local DNS.

## Router/DNS-based resolution

If your router supports local DNS (most do under "LAN hostname" or "static DNS"):

1. Add an A record: `fenrir.lan → 192.168.1.100`
2. All LAN clients resolve it automatically — no `/etc/hosts` edits per machine

Common setups:
- **Pi-hole**: add a local DNS record in Settings → Local DNS
- **AdGuard Home**: Filters → DNS rewrites
- **OpenWrt**: Network → DHCP and DNS → Hostnames
- **pfSense/OPNsense**: Services → DNS Resolver → Host Overrides

## Port forwarding for external access

To expose over the internet, forward ports 80 and 443 on your router to the server, use a proper domain with DNS pointing to your public IP, and switch to Let's Encrypt (see [TLS docs](tls.md)).

Alternatively use a tunnel (no port forwarding needed):

- **Cloudflare Tunnel** — `cloudflared tunnel` proxies to `https://127.0.0.1:8443`
- **Tailscale Funnel** — exposes over your tailnet with auto-TLS
- **ngrok** — quick but rate-limited on free tier
