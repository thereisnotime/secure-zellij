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
from datetime import datetime, timezone

import requests

ACCESS_LOG = os.environ.get("ACCESS_LOG", "/var/log/traefik/access.log")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# Comma-separated list of webhook URLs
WEBHOOK_URLS = [u.strip() for u in os.environ.get("WEBHOOK_URLS", "").split(",") if u.strip()]
ALERT_ON_STATUS = int(os.environ.get("ALERT_ON_STATUS", "101"))
REQUEST_TIMEOUT = 10


def _extract_ip(entry: dict) -> str:
    """Return best-effort real client IP from a Traefik log entry."""
    for field in ("request_X-Real-Ip", "ClientAddr", "RequestAddr"):
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
    ts = entry.get("time", datetime.now(timezone.utc).isoformat())
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


def send_webhooks(payload: dict) -> None:
    for url in WEBHOOK_URLS:
        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[alerter] Webhook {url} failed: {exc}", file=sys.stderr)


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
    print(f"[alerter] Connection: {payload['client_ip']} → {payload['path']}", flush=True)
    send_telegram(payload)
    send_webhooks(payload)


def tail(path: str) -> None:
    print(f"[alerter] Waiting for {path} ...", flush=True)
    while not os.path.exists(path):
        time.sleep(5)
    print(f"[alerter] Watching {path}", flush=True)
    with open(path, encoding="utf-8", errors="replace") as fh:
        fh.seek(0, 2)  # start at end — don't replay history on boot
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.3)
                continue
            process_line(line)


if __name__ == "__main__":
    tail(ACCESS_LOG)
