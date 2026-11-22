# Log rotation

Traefik writes one JSON line per request to `access.log` on the `traefik-logs` volume, and nothing in the stack ever trims it. Install the bundled logrotate config to rotate it daily and keep 14 days. fail2ban and the alerter both read this file, so rotation has to happen in a way they can follow.

## Install

```bash
just logrotate-install
```

This needs `sudo`. It looks up the volume's mount point (with rootless podman, somewhere under `~/.local/share/containers/storage/volumes/secure-zellij_traefik-logs/_data`), puts it into `logrotate.conf` in place of `LOG_PATH`, and writes the result to `/etc/logrotate.d/secure-zellij`. The system's logrotate timer or cron job then runs it daily.

The volume exists only after the stack has been started once. Run `just up` first, and install again after `just clean`, which deletes the volume.

## Test and run

```bash
just logrotate-run              # dry run: logrotate --debug, changes nothing
just logrotate-run force=true   # rotate now, whatever the schedule says
```

## What the config does

| Directive | Effect |
|---|---|
| `daily`, `rotate 14` | One file per day, 14 kept, older ones deleted |
| `compress`, `delaycompress` | Rotated files are gzipped, except the most recent one (`access.log.1`) |
| `missingok`, `notifempty` | No error if the log is missing, no rotation if it is empty |
| `copytruncate` | Copy `access.log` to `access.log.1`, then truncate `access.log` in place |

`copytruncate` is there because logrotate runs as root, and root's podman can't see your rootless containers. The usual approach, renaming the file and sending Traefik `USR1` with `podman kill`, would never reach Traefik, and it would keep writing to the renamed file. With `copytruncate`, Traefik keeps its file handle. It writes with `O_APPEND`, so new lines start at the beginning of the emptied file.

The cost: lines Traefik writes between the copy and the truncate are lost. At normal traffic levels that is a few lines a day at most.

## fail2ban and the alerter

Both notice the truncation and carry on without a restart:

- **fail2ban** sees the file shrink, or its first line change, and reads it again from the start.
- **The alerter** checks the file's inode and size every 0.3 seconds when idle. On a new inode or a shorter file it reopens it and continues from the end, logging `[alerter] Log rotated, reopening ...`.

Connection history on the `/stats` dashboard is in the alerter's own database (`stats.db` on the `stats-data` volume), so rotating or deleting the access log doesn't remove it.

## Checking

```bash
ls -l "$(podman volume inspect secure-zellij_traefik-logs --format '{{.Mountpoint}}')"
just logs-alerter      # look for "Log rotated, reopening"
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `just logrotate-install` fails at `podman volume inspect` | The stack has never been started, or `just clean` removed the volume. Run `just up`, then install again. |
| Nothing is rotated | The system logrotate isn't running (`systemctl status logrotate.timer`, or cron), or the log is empty. On SELinux hosts, also check `sudo ausearch -m AVC -ts recent` for `logrotate_t` denials on the volume. |
| `access.log` is missing, `access.log.1` keeps growing, no bans or alerts | An older install that renamed the file and sent `USR1`, which never reached Traefik. Install again to get `copytruncate`, then `just restart` so Traefik opens a fresh `access.log`. |

## Uninstall

```bash
sudo rm /etc/logrotate.d/secure-zellij
```
