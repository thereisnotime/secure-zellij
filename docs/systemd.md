# Running as a systemd user service

Start the stack and the zellij web server automatically when you log in, or at boot with lingering enabled. Everything runs as your user: rootless podman, no system unit, no root.

## What the unit does

`just start` runs `podman compose up -d` and then `zellij web --start -d`, so one unit covers both the containers and the zellij web server on `127.0.0.1:8082`. `just stop` reverses it: `podman compose --profile sso down` and `zellij web --stop`.

The containers have `restart: unless-stopped`, but rootless podman has no daemon to restart them after a reboot. Without a unit like this one, nothing brings the stack back up.

## Service unit

Create `~/.config/systemd/user/secure-zellij.service`:

```ini
[Unit]
Description=secure-zellij (zellij web + Traefik stack)

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=%h/secure-zellij
Environment=PATH=%h/.local/bin:%h/.cargo/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/env just start
ExecStop=/usr/bin/env just stop
# Only run ExecStop on stop. The default would also kill everything left in
# the unit's cgroup, including zellij sessions started from the web UI.
KillMode=process

[Install]
WantedBy=default.target
```

Adjust these to your machine:

| Setting | Set it to |
|---|---|
| `WorkingDirectory` | Your clone of the repo. `just` reads `.env` from here. |
| `PATH` | Must include `just`, `zellij` and `podman`. Check with `command -v just zellij podman`. A user unit doesn't get your shell's `PATH`. To pin the zellij binary, set `ZELLIJ_BIN` in `.env`, not in the unit. |

There is no `After=network-online.target`: a user unit can't order itself against system targets, so it would have no effect. Once the images are pulled and the alerter is built, starting the stack doesn't need the network.

## Enable and start

```bash
systemctl --user daemon-reload
systemctl --user enable --now secure-zellij
```

The first start builds the alerter image, which can take a minute. A `oneshot` unit has no start timeout by default, so that's fine.

## Start at boot, without logging in

User services normally run only while you are logged in. Enable lingering to start your user manager at boot and keep it running after you log out:

```bash
loginctl enable-linger "$USER"
```

## Day to day

```bash
systemctl --user status secure-zellij
systemctl --user restart secure-zellij      # just stop, then just start
journalctl --user -u secure-zellij -f       # output of just start / just stop
```

The journal only has the output of `just start` and `just stop`. Container logs are in `just logs`, see the [Runbook](../RUNBOOK.md#logs).

Stopping the unit stops the containers and the zellij web server. Sessions keep running (because of `KillMode=process`) and show up again once the web server is back.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `status=127` and `env: 'just': No such file or directory` in the journal | `just` isn't on the unit's `PATH`. Fix `Environment=PATH=`. |
| Unit active, but `just status` says the zellij web server is offline | `zellij` isn't on the unit's `PATH`. `just start` ignores a failing `zellij web --start`, so the unit still succeeds. |
| The stack only starts once you log in | Lingering is off. `loginctl show-user "$USER" -p Linger` should say `yes`. |
| `podman` errors about `XDG_RUNTIME_DIR` or the cgroup manager | The unit is installed as a system unit or run with `sudo`. It belongs in `~/.config/systemd/user/` and is managed with `systemctl --user`. |
