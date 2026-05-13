# Log Rotation

Without rotation, the Traefik access log grows unbounded and will eventually exhaust disk space. This setup rotates it daily and keeps 14 days of history.

## Install

```bash
just logrotate-install
```

This requires `sudo`. It reads the actual volume mount path from Podman, substitutes it into `logrotate.conf`, and writes the result to `/etc/logrotate.d/secure-zellij`.

## Test (dry-run)

```bash
just logrotate-run
```

Runs `logrotate --debug` — shows what would happen without touching any files.

## Apply immediately

```bash
just logrotate-run force=true
```

Forces an immediate rotation regardless of the schedule.

## What happens during rotation

- The current `access.log` is renamed and compressed (previous day's file is compressed with a one-rotation delay via `delaycompress`).
- Traefik receives `SIGUSR1`, which causes it to close and reopen its log file handle — no restart needed, no log entries lost.
- Logs older than 14 days are deleted automatically.
- Missing or empty log files are silently skipped (`missingok`, `notifempty`).

## Alerter behaviour

The alerter watches the log file by inode, not by path, so it handles rotation transparently without needing a restart.

## Uninstall

```bash
sudo rm /etc/logrotate.d/secure-zellij
```
