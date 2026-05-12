# Running as a systemd user service

Start the full stack automatically on login (or boot with lingering enabled).

## Service unit

Create `~/.config/systemd/user/secure-zellij.service`:

```ini
[Unit]
Description=secure-zellij (Traefik + Zellij web)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=%h/Private/Projects/P/secure-zellij
ExecStart=/usr/bin/just start
ExecStop=/usr/bin/just stop
Environment=HOME=%h
Environment=PATH=/home/%u/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

Adjust `WorkingDirectory` if your project lives elsewhere.

## Enable and start

```bash
systemctl --user daemon-reload
systemctl --user enable --now secure-zellij
```

## Start on boot (without login)

By default user services only run while you're logged in. Enable lingering to start them at boot:

```bash
loginctl enable-linger $USER
```

## Useful commands

```bash
systemctl --user status secure-zellij
systemctl --user restart secure-zellij
journalctl --user -u secure-zellij -f
```

## Using xxzellij

If you have [xxzellij](https://github.com/thereisnotime/secure-zellij) installed via the tint belt, reference it in the unit:

```ini
ExecStart=/home/%u/.local/bin/xxzellij start
ExecStop=/home/%u/.local/bin/xxzellij stop
```
