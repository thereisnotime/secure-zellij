# Fail2ban

Fail2ban watches the Traefik access log and bans IPs that rack up repeated 4xx responses — failed token attempts, scanners, probes. The alerter notifies you; fail2ban acts.

The filter and jail config live in `fail2ban/` in the repo. That's the source of truth — do not edit `/etc/fail2ban/` by hand.

## Requirements

- `fail2ban` installed on the host (`apt install fail2ban` / `dnf install fail2ban`)
- `iptables` available (see note below if using nftables)

## Install

```bash
just fail2ban-install
```

This copies `fail2ban/filter.d/traefik-zellij.conf` to `/etc/fail2ban/filter.d/`, resolves the Traefik log volume path, writes the substituted jail config to `/etc/fail2ban/jail.d/`, then reloads fail2ban.

## Update

Edit `fail2ban/filter.d/traefik-zellij.conf` or `fail2ban/jail.d/traefik-zellij.conf`, then re-run:

```bash
just fail2ban-install
```

## Status

```bash
just fail2ban-status
```

## Unban an IP

```bash
sudo fail2ban-client set traefik-zellij unbanip <IP>
```

## Uninstall

```bash
just fail2ban-uninstall
```

## Tuning

All tuning is done in `fail2ban/jail.d/traefik-zellij.conf`:

| Parameter  | Default | Meaning                                      |
|------------|---------|----------------------------------------------|
| `maxretry` | 10      | 4xx responses within `findtime` to trigger a ban |
| `findtime` | 60      | Sliding window in seconds                    |
| `bantime`  | 3600    | Ban duration in seconds (1 hour)             |

After editing, re-run `just fail2ban-install` to apply.

## nftables

If the host uses nftables instead of iptables, change the `action` line in `fail2ban/jail.d/traefik-zellij.conf`:

```ini
action = nftables-multiport[name=traefik-zellij, port="http,https"]
```
