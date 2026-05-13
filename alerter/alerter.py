#!/usr/bin/env python3
"""
Tails Traefik JSON access logs and fires alerts on successful zellij connections.

A "connection" is detected by HTTP 101 (WebSocket upgrade) — the handshake
zellij's web terminal uses. Each event ships to all configured destinations:
Telegram bot and/or a list of generic webhook URLs.
"""

import json
import os
import sys
import time
from datetime import UTC, datetime

import requests

ACCESS_LOG = os.environ.get("ACCESS_LOG", "/var/log/traefik/access.log")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# Comma-separated list of webhook URLs
WEBHOOK_URLS = [u.strip() for u in os.environ.get("WEBHOOK_URLS", "").split(",") if u.strip()]
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
ALERT_ON_STATUS = int(os.environ.get("ALERT_ON_STATUS", "101"))
# Optional JSON template with {field} placeholders. Available fields:
#   event, timestamp, client_ip, x_forwarded_for, user_agent, path, status, service
# Example: '{"text": "Connection from {client_ip} at {timestamp}"}'
WEBHOOK_BODY_TEMPLATE = os.environ.get("WEBHOOK_BODY_TEMPLATE", "")
ALERT_COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "60"))
REQUEST_TIMEOUT = 10

_last_alert: dict[str, float] = {}


def _extract_ip(entry: dict) -> str:
    """Return the real TCP-layer client IP from a Traefik log entry.

    ClientAddr / RequestAddr are set by Traefik from the actual TCP connection
    and cannot be spoofed by the client.  request_X-Real-Ip is an incoming
    request header that any client can forge, so it is intentionally ignored.
    """
    for field in ("ClientAddr", "RequestAddr"):
        val = entry.get(field, "")
        if val:
            # Strip port if present (IPv4 addr:port or [IPv6]:port)
            if val.startswith("["):
                return val.split("]")[0].lstrip("[")
            return val.rsplit(":", 1)[0]
    return "unknown"


def _build_payload(entry: dict) -> dict:
    ip = _extract_ip(entry)
    forwarded = entry.get("request_X-Forwarded-For", "")
    user_agent = entry.get("request_User-Agent", "unknown")
    path = entry.get("RequestPath", "/")
    status = entry.get("DownstreamStatus", 0)
    ts = entry.get("time", datetime.now(UTC).isoformat())
    service = entry.get("ServiceName", "unknown")

    return {
        "event": "zellij_connect",
        "timestamp": ts,
        "client_ip": ip,
        "x_forwarded_for": forwarded,
        "user_agent": user_agent,
        "path": path,
        "status": status,
        "service": service,
    }


def send_telegram(payload: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    ip = payload["client_ip"]
    fwd = f"\nForwarded-For: `{payload['x_forwarded_for']}`" if payload["x_forwarded_for"] else ""
    ua = payload["user_agent"][:100]
    text = (
        f"🔌 *Zellij connection*\n"
        f"IP: `{ip}`{fwd}\n"
        f"Path: `{payload['path']}`\n"
        f"UA: `{ua}`\n"
        f"Time: `{payload['timestamp']}`"
    )
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"[alerter] Telegram send failed: {exc}", file=sys.stderr)


def build_webhook_body(payload: dict, template: str = "") -> dict:
    """Return the body to POST. If a template is set, render it and parse as JSON."""
    if not template:
        return payload
    try:
        rendered = template.format(**{k: str(v) for k, v in payload.items()})
        return json.loads(rendered)
    except (KeyError, ValueError) as exc:
        print(f"[alerter] WEBHOOK_BODY_TEMPLATE error: {exc}", file=sys.stderr)
        return payload


def send_webhooks(payload: dict) -> None:
    body = build_webhook_body(payload, WEBHOOK_BODY_TEMPLATE)
    for url in WEBHOOK_URLS:
        try:
            resp = requests.post(url, json=body, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[alerter] Webhook {url} failed: {exc}", file=sys.stderr)


def send_discord(payload: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    ip = payload["client_ip"]
    fwd = f"\nForwarded-For: {payload['x_forwarded_for']}" if payload["x_forwarded_for"] else ""
    ua = payload["user_agent"][:100]
    text = (
        f"\U0001f50c **Zellij connection**\n"
        f"IP: {ip}{fwd}\n"
        f"Path: {payload['path']}\n"
        f"UA: {ua}\n"
        f"Time: {payload['timestamp']}"
    )
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": text}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[alerter] Discord send failed: {exc}", file=sys.stderr)


def _validate_config() -> None:
    if not WEBHOOK_BODY_TEMPLATE:
        return
    dummy = {
        "event": "test",
        "timestamp": "t",
        "client_ip": "1.2.3.4",
        "x_forwarded_for": "",
        "user_agent": "u",
        "path": "/",
        "status": "101",
        "service": "s",
    }
    try:
        rendered = WEBHOOK_BODY_TEMPLATE.format(**dummy)
        json.loads(rendered)
    except (KeyError, ValueError) as exc:
        print(
            f"[alerter] WARNING: WEBHOOK_BODY_TEMPLATE is invalid: {exc}",
            file=sys.stderr,
            flush=True,
        )


def _print_config() -> None:
    tg_status = (
        f"(chat_id={TELEGRAM_CHAT_ID})" if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else "disabled"
    )
    tg_check = "+" if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else "-"
    discord_status = "+" if DISCORD_WEBHOOK_URL else "-"
    discord_label = "" if DISCORD_WEBHOOK_URL else "  disabled"
    webhook_status = "+" if WEBHOOK_URLS else "-"
    webhook_label = f"({len(WEBHOOK_URLS)} URLs)" if WEBHOOK_URLS else "disabled"
    print("[alerter] Channels:", flush=True)
    print(f"[alerter]   Telegram  {tg_check}  {tg_status}", flush=True)
    print(f"[alerter]   Discord   {discord_status}{discord_label}", flush=True)
    print(f"[alerter]   Webhooks  {webhook_status}  {webhook_label}", flush=True)
    print(f"[alerter]   Cooldown  {ALERT_COOLDOWN_SECONDS}s", flush=True)


def process_line(line: str) -> None:
    line = line.strip()
    if not line:
        return
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return
    if entry.get("DownstreamStatus") != ALERT_ON_STATUS:
        return
    payload = _build_payload(entry)
    now = time.monotonic()
    if ALERT_COOLDOWN_SECONDS > 0:
        cutoff = now - ALERT_COOLDOWN_SECONDS
        stale = [k for k, v in _last_alert.items() if v < cutoff]
        for k in stale:
            del _last_alert[k]
    ip = payload["client_ip"]
    if now - _last_alert.get(ip, 0.0) < ALERT_COOLDOWN_SECONDS:
        print(f"[alerter] Suppressed duplicate alert for {ip}", flush=True)
        return
    _last_alert[ip] = now
    print(f"[alerter] Connection: {payload['client_ip']} -> {payload['path']}", flush=True)
    send_telegram(payload)
    send_webhooks(payload)
    send_discord(payload)


def tail(path: str) -> None:
    print(f"[alerter] Waiting for {path} ...", flush=True)
    while not os.path.exists(path):
        time.sleep(5)
    print(f"[alerter] Watching {path}", flush=True)
    _validate_config()
    _print_config()
    while True:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                fh.seek(0, 2)  # start at end — don't replay history on boot
                inode = os.fstat(fh.fileno()).st_ino
                while True:
                    line = fh.readline()
                    if not line:
                        time.sleep(0.3)
                        # Detect rotation: new inode or file truncated behind us
                        try:
                            st = os.stat(path)
                        except FileNotFoundError:
                            break  # file gone; outer loop will wait for it
                        if st.st_ino != inode or st.st_size < fh.tell():
                            print("[alerter] Log rotated, reopening ...", flush=True)
                            break
                        continue
                    process_line(line)
        except FileNotFoundError:
            print(f"[alerter] {path} disappeared, waiting ...", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    tail(ACCESS_LOG)
