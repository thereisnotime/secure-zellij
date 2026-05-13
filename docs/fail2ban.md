# Fail2ban

Fail2ban watches the Traefik access log and bans IPs that rack up repeated 4xx responses — failed token attempts, scanners, probes. The alerter notifies you; fail2ban acts.

It runs as a container in the compose stack. No host packages needed — it starts with the rest of the stack via `just up`.

## How it works

The `fail2ban` service mounts the shared `traefik-logs` volume read-only at `/var/log/traefik/` and reads `access.log`. When an IP exceeds `maxretry` 4xx responses within `findtime` seconds it is banned via iptables for `bantime` seconds.

Because fail2ban modifies host iptables, the container runs with `network_mode: host` and the `NET_ADMIN` + `NET_RAW` capabilities. This is intentional.

## Configuration (GitOps)

Configs live in `fail2ban/` in the repo and are mounted into the container read-only:

- `fail2ban/filter.d/traefik-zellij.conf` — regex that matches 4xx log lines
- `fail2ban/jail.d/traefik-zellij.conf` — ban thresholds and action

To change behaviour, edit the files and restart the container:

```bash
podman compose restart fail2ban
```

## Commands

```bash
# Jail status (banned count, total hits)
just fail2ban-status

# List currently banned IPs with ban times
just fail2ban-banned

# Unban a specific IP
just fail2ban-unban 1.2.3.4
```

## Tuning

All thresholds are in `fail2ban/jail.d/traefik-zellij.conf`:

| Parameter  | Default | Meaning                                          |
|------------|---------|--------------------------------------------------|
| `maxretry` | 10      | 4xx responses within `findtime` to trigger a ban |
| `findtime` | 60      | Sliding window in seconds                        |
| `bantime`  | 3600    | Ban duration in seconds (1 hour)                 |

After editing, apply with:

```bash
podman compose restart fail2ban
```

## nftables

If the host uses nftables instead of iptables, change the `action` line in `fail2ban/jail.d/traefik-zellij.conf`:

```ini
action = nftables-multiport[name=traefik-zellij, port="http,https"]
```
