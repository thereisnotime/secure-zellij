# TLS Configuration

## Self-signed (default)

No configuration needed. Traefik generates a self-signed certificate automatically. The browser will show a security warning — accept it or import the cert into your trust store.

Suitable for: local development, LAN-only deployments, internal tools where you control all clients.

## Let's Encrypt

Requires a publicly reachable domain and port 80 open to the internet for the HTTP-01 challenge.

**1. `traefik/traefik.yml`** — uncomment the resolver block:

```yaml
certificatesResolvers:
  letsencrypt:
    acme:
      email: "you@example.com"
      storage: /letsencrypt/acme.json
      httpChallenge:
        entryPoint: web
```

**2. `traefik/dynamic/routers.yml`** — replace `tls: {}` with:

```yaml
tls:
  certResolver: letsencrypt
```

Do this for both the `zellij` and `zellij-http` routers.

**3. `.env`**:

```env
DOMAIN=zellij.example.com
ACME_EMAIL=you@example.com
HTTP_PORT=80
HTTPS_PORT=443
```

**4.** Restart: `just down && just up`

Traefik will obtain and auto-renew the certificate. The `letsencrypt/acme.json` file is stored in a named volume and persists across restarts.

## DNS-01 challenge (wildcard certs)

For wildcard certificates or deployments where port 80 is not reachable, use the DNS-01 challenge. Requires a supported DNS provider.

Add the provider config to the `acme` block:

```yaml
certificatesResolvers:
  letsencrypt:
    acme:
      email: "you@example.com"
      storage: /letsencrypt/acme.json
      dnsChallenge:
        provider: cloudflare   # or route53, digitalocean, etc.
        delayBeforeCheck: 30
```

Pass provider credentials via environment variables in `compose.yaml`:

```yaml
environment:
  - CF_API_EMAIL=you@example.com
  - CF_API_KEY=your-cloudflare-key
```

See the [Traefik ACME provider list](https://doc.traefik.io/traefik/https/acme/#providers) for all supported providers.

## Bring your own certificate

Mount your cert and key and reference them in the dynamic config.

**`compose.yaml`** — add volume mounts:

```yaml
volumes:
  - /path/to/cert.pem:/certs/cert.pem:ro
  - /path/to/key.pem:/certs/key.pem:ro
```

**`traefik/dynamic/tls.yml`** — add a certificate entry:

```yaml
tls:
  certificates:
    - certFile: /certs/cert.pem
      keyFile: /certs/key.pem
  options:
    default:
      minVersion: VersionTLS12
```

Remove `tls: {}` from the router and Traefik will use the manually loaded cert.

## TLS hardening options

Defined in `traefik/dynamic/tls.yml`. Defaults:

| Setting | Value | Notes |
|---|---|---|
| `minVersion` | `VersionTLS12` | TLS 1.0/1.1 disabled |
| `sniStrict` | `false` | Set to `true` once you have a valid cert for the domain |
| Cipher suites | ECDHE + AES-GCM / ChaCha20 | Forward secrecy enforced |

Enable `sniStrict: true` in production to reject connections without a matching SNI header.
