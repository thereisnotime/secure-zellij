# Runbook

Operational reference for secure-zellij. For setup and configuration see [docs/](docs/README.md).

---

## Stack

```bash
just up                          # start all containers (detached)
just down                        # stop all containers
just start                       # start containers + zellij web server
just stop                        # stop containers + zellij web server
just restart                     # restart all containers
just status                      # full status: zellij, sessions, tokens, containers
just build                       # rebuild alerter image (no cache)
just clean                       # destroy containers AND volumes (destructive)
```

---

## Logs

```bash
just logs                        # follow all container logs
just logs-traefik                # follow Traefik only
just logs-alerter                # follow alerter only
just access                      # last 20 access log entries (pretty-printed)
just access 50                   # last 50 entries
```

---

## Tokens

```bash
just token                       # create a new read-write token
just token-ro                    # create a read-only token
just tokens                      # list all tokens with creation dates
just revoke <name>               # revoke a specific token
just revoke-all                  # revoke all tokens (destructive)
```

---

## Sessions

```bash
zellij list-sessions             # list running sessions
zellij attach <name>             # attach to a session
zellij kill-session <name>       # kill a specific session
zellij kill-all-sessions         # kill all sessions
```

---

## Fail2ban

```bash
just fail2ban-status             # jail stats: failures, bans, file list
just fail2ban-banned             # list currently banned IPs with ban times
just fail2ban-unban 1.2.3.4      # unban a specific IP
```

Manually ban an IP (e.g. for testing):
```bash
podman exec secure-zellij-fail2ban fail2ban-client set traefik-zellij banip 1.2.3.4
```

Check if a specific IP is banned:
```bash
podman exec secure-zellij-fail2ban fail2ban-client get traefik-zellij banip 1.2.3.4
```

Test filter regex against the live log:
```bash
podman exec secure-zellij-fail2ban fail2ban-regex \
  /var/log/traefik/access.log \
  /etc/fail2ban/filter.d/traefik-zellij.conf
```

Reload fail2ban config after editing `fail2ban/`:
```bash
podman compose restart fail2ban
```

---

## Certificates

Check current TLS cert expiry (self-signed):
```bash
echo | openssl s_client -connect localhost:8443 -servername zellij.local 2>/dev/null \
  | openssl x509 -noout -dates
```

Force Let's Encrypt renewal (if using ACME):
```bash
podman exec secure-zellij-traefik traefik healthcheck
# LE renews automatically; to force: just restart
```

---

## Log rotation

```bash
just logrotate-install           # install /etc/logrotate.d/secure-zellij (requires sudo)
just logrotate-run               # dry-run
just logrotate-run force=true    # force rotate now
```

Manual rotation without logrotate:
```bash
LOG=$(podman volume inspect secure-zellij_traefik-logs --format '{{.Mountpoint}}')/access.log
mv "$LOG" "$LOG.$(date +%Y%m%d)" && podman kill --signal USR1 secure-zellij-traefik
```

---

## Diagnostics

Container health:
```bash
podman compose ps
podman inspect --format '{{.State.Health.Status}}' secure-zellij-traefik
podman inspect --format '{{.State.Health.Status}}' secure-zellij-alerter
```

Check Traefik parsed config:
```bash
podman exec secure-zellij-traefik traefik healthcheck
```

Tail raw access log:
```bash
podman exec secure-zellij-traefik tail -f /var/log/traefik/access.log \
  | python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin if l.strip()]"
```

Check rate limiting is active:
```bash
# Should return 429 after burst is exhausted
for i in $(seq 1 60); do curl -sk -o /dev/null -w "%{http_code}\n" \
  --resolve "zellij.local:8443:127.0.0.1" https://zellij.local:8443/; done | sort | uniq -c
```

Check iptables ban rules:
```bash
podman exec secure-zellij-fail2ban iptables -L f2b-traefik-zellij -n 2>/dev/null
```
