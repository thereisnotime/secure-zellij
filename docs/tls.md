# TLS

How Traefik gets the certificate it serves on `HTTPS_PORT`, and how to change it. There are four options: Traefik's built-in self-signed certificate (the default), Let's Encrypt with the HTTP-01 challenge, Let's Encrypt with DNS-01, or a certificate you bring yourself.

| Mode | Changes needed | Good for |
|---|---|---|
| [Self-signed](#self-signed-default) | none | LAN, testing, clients you control |
| [Let's Encrypt, HTTP-01](#lets-encrypt-http-01) | `traefik.yml`, router `tls` blocks, port 80 reachable from the internet | a public domain |
| [Let's Encrypt, DNS-01](#lets-encrypt-dns-01) | as above, plus DNS provider credentials | wildcard certs, no inbound port 80 |
| [Your own certificate](#your-own-certificate) | a mount and `tls.yml` | an internal CA, or a cert you already manage |

## Where TLS is configured

| File | What it holds |
|---|---|
| `traefik/traefik.yml` | Static config: entrypoints and the (commented out) `certificatesResolvers` block |
| `traefik/dynamic/routers.yml` | `tls:` on the `zellij` and `stats` routers |
| `traefik/sso/oauth2-proxy.yml`, `traefik/sso/callback.yml` | `tls:` on the SSO routers, only used with [SSO](sso.md) |
| `traefik/dynamic/tls.yml` | TLS options (minimum version, ciphers) and any certificates you load yourself |

Traefik doesn't read `traefik/dynamic/` directly. At start, `traefik/entrypoint.sh` copies it to `/etc/traefik/dynamic-live` inside the container, fills in the `ZELLIJ_RULE` and `# SSO_MIDDLEWARE` placeholders, and Traefik watches that copy. So after editing any of these files, restart Traefik:

```bash
podman compose restart traefik
```

The ban rules and published ports live in the `edge` container, so restarting Traefik on its own is safe (see [Fail2ban](fail2ban.md#where-the-ban-happens)).

## Self-signed (default)

Nothing to configure. The routers have `tls: {}` and no certificate is loaded, so Traefik serves its built-in `TRAEFIK DEFAULT CERT`. Browsers warn about it; accept the warning or use one of the other modes.

Traefik generates this certificate in memory at startup, so it changes every time Traefik restarts and there is nothing stable to add to a trust store. If you want clients to trust the LAN setup, issue a certificate from your own CA and [load it](#your-own-certificate).

## Let's Encrypt (HTTP-01)

Needs a public DNS name pointing at the host, and port 80 on that name reaching Traefik's `HTTP_PORT`. Let's Encrypt only ever connects to port 80 for this challenge.

**1. Reach port 80.** Either forward port 80 on your router to `HTTP_PORT` (8080 by default), or publish port 80 directly. Rootless podman can't bind ports below 1024 until you lower the limit:

```bash
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80
echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/90-unprivileged-ports.conf
```

```dotenv
DOMAIN=zellij.example.com
HTTP_PORT=80
HTTPS_PORT=443
```

**2. Enable the resolver** in `traefik/traefik.yml` by uncommenting the block at the bottom and putting your address in `email`:

```yaml
certificatesResolvers:
  letsencrypt:
    acme:
      email: "ACME_EMAIL_PLACEHOLDER"   # filled from ACME_EMAIL in .env
      storage: /letsencrypt/acme.json
      httpChallenge:
        entryPoint: web
```

Set `ACME_EMAIL` in `.env`. Traefik's entrypoint replaces `ACME_EMAIL_PLACEHOLDER` with it at startup, so leave the placeholder in the file.

**3. Use it on the routers.** Replace `tls: {}` on the `zellij` and `stats` routers in `traefik/dynamic/routers.yml`, and on `sso-oauth2` / `sso-callback` in `traefik/sso/` if you use SSO:

```yaml
      tls:
        certResolver: letsencrypt
        domains:
          - main: zellij.example.com
```

`domains` limits the request to `DOMAIN`. Without it Traefik asks for a certificate covering every `Host()` in the rule, including LAN IPs and `.lan` names from `EXTRA_DOMAINS`, which Let's Encrypt refuses. Leave `zellij-http` alone: it only redirects to HTTPS, and the ACME challenge is answered on the `web` entrypoint before that redirect.

**4. Restart Traefik** with `podman compose restart traefik` and watch `just logs-traefik` for the certificate request.

Certificates are stored in `acme.json` on the `letsencrypt` volume (`secure-zellij_letsencrypt`), survive restarts and upgrades, and are renewed by Traefik 30 days before they expire. `just clean` deletes that volume, and with it the certificate.

## Let's Encrypt (DNS-01)

Use this for a wildcard certificate, or when port 80 can't be reached from the internet. Traefik creates a TXT record through your DNS provider's API. The provider list and the variables each one needs are in the [Traefik ACME docs](https://doc.traefik.io/traefik/https/acme/#providers).

Replace `httpChallenge` in the resolver with `dnsChallenge` (Cloudflare as the example):

```yaml
certificatesResolvers:
  letsencrypt:
    acme:
      email: "ACME_EMAIL_PLACEHOLDER"   # filled from ACME_EMAIL in .env
      storage: /letsencrypt/acme.json
      dnsChallenge:
        provider: cloudflare
        propagation:
          delayBeforeChecks: 30s
```

Pass the credentials to the Traefik container with a `compose.override.yaml` next to `compose.yaml`. Compose picks it up automatically, and since it isn't tracked by git, `just upgrade` won't trip over it:

```yaml
services:
  traefik:
    environment:
      - CF_DNS_API_TOKEN=${CF_DNS_API_TOKEN}
```

```dotenv
CF_DNS_API_TOKEN=your-scoped-api-token
```

Then set `tls.certResolver` on the routers as in step 3 above, with `main: "*.example.com"` for a wildcard, and run `just restart` so Traefik is recreated with the new variable.

## Your own certificate

Mount the certificate and key into the Traefik container with a `compose.override.yaml`. Keep `:ro,z` so the files get the right label on SELinux hosts (see [SELinux](selinux.md)):

```yaml
services:
  traefik:
    volumes:
      - /path/to/fullchain.pem:/certs/cert.pem:ro,z
      - /path/to/privkey.pem:/certs/key.pem:ro,z
```

`:z` relabels the files themselves. If they live somewhere other services also read, such as `/etc/letsencrypt`, copy them somewhere of their own first.

Make it the default certificate in `traefik/dynamic/tls.yml`, next to the existing `options` block:

```yaml
tls:
  stores:
    default:
      defaultCertificate:
        certFile: /certs/cert.pem
        keyFile: /certs/key.pem
  options:
    default:
      # existing settings stay as they are
```

Keep `tls: {}` on the routers: it is what turns TLS on for them. With a default certificate in the store, Traefik serves it for every request, including clients that connect by IP and send no SNI. Run `just restart` to apply the new mounts.

## Hardening options

Set in `traefik/dynamic/tls.yml`:

| Setting | Value | Notes |
|---|---|---|
| `minVersion` | `VersionTLS12` | TLS 1.0 and 1.1 are refused |
| `cipherSuites` | ECDHE-RSA with AES-GCM or ChaCha20 | TLS 1.2 only, all with forward secrecy. Go doesn't let you configure TLS 1.3 suites, so the three TLS 1.3 entries in the list have no effect. |
| `sniStrict` | `false` | `true` rejects clients whose SNI matches no loaded certificate |

The TLS 1.2 list only has RSA suites. With an ECDSA certificate, TLS 1.2 clients have nothing to negotiate and only TLS 1.3 works. Add the `TLS_ECDHE_ECDSA_*` equivalents if you use one.

Turn on `sniStrict` only once you serve a real certificate for `DOMAIN`. It also blocks clients that connect by IP (an `EXTRA_DOMAINS` IP, for example), since they send no SNI.

## Checking the certificate

```bash
echo | openssl s_client -connect "127.0.0.1:${HTTPS_PORT:-8443}" -servername "$DOMAIN" 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

`TRAEFIK DEFAULT CERT` in the subject means Traefik fell back to the self-signed certificate.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Still `TRAEFIK DEFAULT CERT` after enabling Let's Encrypt | Traefik wasn't restarted after the edit, the router still has `tls: {}`, or the challenge failed. `just logs-traefik` shows the ACME error. |
| `rejectedIdentifier` or `Cannot issue for` in the log | The request includes an IP or private name from `EXTRA_DOMAINS`. Add `domains` to the router `tls` block. |
| HTTP-01 times out | Port 80 doesn't reach `HTTP_PORT` from the internet. Check the router forward, and that `curl http://$DOMAIN/` from outside gets a redirect. |
| `just up` fails with a permission error binding port 80 or 443 | Lower `net.ipv4.ip_unprivileged_port_start` as in step 1, or keep 8080/8443 and forward 80/443 on your router. |
| Traefik can't read `/certs/*.pem` (permission denied) | Missing `:z` on an SELinux host, or a key readable only by root. `just selinux-check` and `sudo ausearch -m AVC -ts recent` show it. |
