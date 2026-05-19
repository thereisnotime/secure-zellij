#!/usr/bin/env python3
"""
Tails Traefik JSON access logs and fires alerts on successful zellij connections.

A "connection" is detected by HTTP 101 (WebSocket upgrade) — the handshake
zellij's web terminal uses. Each event ships to all configured destinations:
Telegram bot and/or a list of generic webhook URLs.

Also serves a live stats dashboard on STATS_PORT (default 8083).
"""

import base64
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import requests

ACCESS_LOG = os.environ.get("ACCESS_LOG", "/var/log/traefik/access.log")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBHOOK_URLS = [u.strip() for u in os.environ.get("WEBHOOK_URLS", "").split(",") if u.strip()]
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
# Traefik logs WebSocket connections as DownstreamStatus=0 (hijacked connections
# don't carry a status code through the proxy layer). Set to 0 to alert on
# every terminal session close, or leave at 101 to effectively disable alerts.
ALERT_ON_STATUS = int(os.environ.get("ALERT_ON_STATUS", "0"))
WEBHOOK_BODY_TEMPLATE = os.environ.get("WEBHOOK_BODY_TEMPLATE", "")
ALERT_COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "60"))
REQUEST_TIMEOUT = 10

DB_PATH = os.environ.get("STATS_DB", "/data/stats.db")
STATS_PORT = int(os.environ.get("STATS_PORT", "8083"))
STATS_USERNAME = os.environ.get("STATS_USERNAME", "")
STATS_PASSWORD = os.environ.get("STATS_PASSWORD", "")

_last_alert: dict[str, float] = {}
_db_lock = threading.Lock()

# ── HTML template ──────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <base href="/stats/">
  <title>Zellij Stats</title>
  <style>
    :root {
      --bg: #1a1b26; --surface: #24283b; --border: #414868;
      --text: #c0caf5; --dim: #565f89; --accent: #7aa2f7;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace; font-size: 13px; padding: 1.5rem; }
    h1 { color: var(--accent); margin-bottom: 1.5rem; font-size: 1.2rem; display: flex; align-items: center; gap: 0.75rem; }
    h2 { color: var(--dim); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 0.6rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-bottom: 2rem; }
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 0.9rem 1rem; }
    .card .val { font-size: 1.8rem; color: var(--accent); font-weight: 700; line-height: 1; }
    .card .lbl { color: var(--dim); font-size: 0.72rem; margin-top: 0.3rem; }
    .section { margin-bottom: 2rem; }
    table { width: 100%; border-collapse: collapse; }
    th { color: var(--dim); text-align: left; padding: 0.35rem 0.6rem; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid var(--border); }
    td { padding: 0.35rem 0.6rem; border-bottom: 1px solid var(--border); color: var(--text); max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--surface); }
    .chart-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem 0.5rem 0.4rem; }
    .bar-chart { display: flex; align-items: flex-end; gap: 2px; height: 56px; }
    .bar { background: var(--accent); opacity: 0.65; flex: 1; min-width: 4px; border-radius: 2px 2px 0 0; transition: opacity .15s; cursor: default; }
    .bar:hover { opacity: 1; }
    .chart-labels { display: flex; justify-content: space-between; color: var(--dim); font-size: 0.65rem; margin-top: 0.3rem; padding: 0 0.1rem; }
    #status { font-size: 0.72rem; color: var(--dim); margin-left: auto; }
    .empty { color: var(--dim); padding: 0.5rem 0.6rem; font-style: italic; }
  </style>
</head>
<body>
  <h1>&#9889; Zellij Dashboard <span id="status">loading&hellip;</span></h1>

  <div class="grid">
    <div class="card"><div class="val" id="c-active">&#x2014;</div><div class="lbl">Active Sessions (5m)</div></div>
    <div class="card"><div class="val" id="c-ips1h">&#x2014;</div><div class="lbl">Unique IPs (1h)</div></div>
    <div class="card"><div class="val" id="c-today">&#x2014;</div><div class="lbl">Connections Today</div></div>
    <div class="card"><div class="val" id="c-total">&#x2014;</div><div class="lbl">All-Time</div></div>
  </div>

  <div class="section">
    <h2>Connections &mdash; last 24h</h2>
    <div class="chart-wrap">
      <div class="bar-chart" id="chart"></div>
      <div class="chart-labels"><span id="chart-start"></span><span id="chart-end"></span></div>
    </div>
  </div>

  <div class="section">
    <h2>Sessions</h2>
    <table><thead><tr><th>Session</th><th>Last Seen</th><th>Today</th><th>Total</th></tr></thead>
    <tbody id="tb-sessions"><tr><td colspan="4" class="empty">loading&hellip;</td></tr></tbody></table>
  </div>

  <div class="section">
    <h2>Recent Connections</h2>
    <table><thead><tr><th>Time</th><th>IP</th><th>Session</th><th>User Agent</th></tr></thead>
    <tbody id="tb-recent"><tr><td colspan="4" class="empty">loading&hellip;</td></tr></tbody></table>
  </div>

  <script>
    function esc(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function fmtTs(s) {
      try { return new Date(s).toLocaleString(); } catch(_) { return s; }
    }
    async function refresh() {
      try {
        const r = await fetch('api');
        if (!r.ok) { document.getElementById('status').textContent = 'error ' + r.status; return; }
        const d = await r.json();
        const s = d.summary;
        document.getElementById('c-active').textContent = s.active_sessions;
        document.getElementById('c-ips1h').textContent  = s.connected_ips_1h;
        document.getElementById('c-today').textContent  = s.connections_today;
        document.getElementById('c-total').textContent  = s.connections_total;
        document.getElementById('status').textContent   = 'updated ' + new Date().toLocaleTimeString();

        const hc = d.hourly_chart;
        const maxV = Math.max(...hc.map(h => h.count), 1);
        document.getElementById('chart').innerHTML = hc.map(h => {
          const pct = Math.max(Math.round(h.count / maxV * 100), h.count > 0 ? 4 : 0);
          return '<div class="bar" style="height:' + pct + '%" title="' + esc(h.hour) + ': ' + h.count + '"></div>';
        }).join('');
        if (hc.length) {
          document.getElementById('chart-start').textContent = hc[0].hour.slice(11,16);
          document.getElementById('chart-end').textContent   = 'now';
        }

        document.getElementById('tb-sessions').innerHTML = d.sessions.length
          ? d.sessions.map(s =>
              '<tr><td>' + (s.name ? esc(s.name) : '<em>welcome</em>') + '</td>' +
              '<td>' + fmtTs(s.last_seen) + '</td>' +
              '<td>' + s.connections_today + '</td>' +
              '<td>' + s.connections_total + '</td></tr>'
            ).join('')
          : '<tr><td colspan="4" class="empty">no sessions yet</td></tr>';

        document.getElementById('tb-recent').innerHTML = d.recent_connections.length
          ? d.recent_connections.map(c =>
              '<tr><td>' + fmtTs(c.ts) + '</td>' +
              '<td>' + esc(c.ip) + '</td>' +
              '<td>' + (c.session ? esc(c.session) : '<em>welcome</em>') + '</td>' +
              '<td title="' + esc(c.user_agent) + '">' + esc(c.user_agent.substring(0,55)) + '</td></tr>'
            ).join('')
          : '<tr><td colspan="4" class="empty">no connections yet</td></tr>';
      } catch(e) {
        document.getElementById('status').textContent = 'fetch error';
        console.error(e);
      }
    }
    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>"""

# ── DB helpers ─────────────────────────────────────────────────────────────────


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS connections (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT NOT NULL,
                ip         TEXT NOT NULL,
                session    TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                path       TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON connections(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ip ON connections(ip)")
        conn.commit()


def _parse_session(path: str) -> str | None:
    """Return session name from /ws/terminal[/name], or None to skip storage."""
    path = path.split("?")[0]  # strip query string
    if path in ("/ws/terminal", "/ws/terminal/"):
        return ""
    if path.startswith("/ws/terminal/"):
        return path[len("/ws/terminal/") :].strip("/")
    return None


def store_event(ip: str, session: str, user_agent: str, path: str, ts: str | None = None) -> None:
    if ts is None:
        ts = datetime.now(UTC).isoformat()
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO connections (ts, ip, session, user_agent, path) VALUES (?,?,?,?,?)",
            (ts, ip, session, user_agent, path),
        )
        conn.commit()


def _fill_hourly(rows: list, hours: int = 24) -> list[dict]:
    now = datetime.now(UTC)
    by_hour: dict[str, int] = {r[0]: r[1] for r in rows}
    return [
        {
            "hour": (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:00Z"),
            "count": by_hour.get((now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:00Z"), 0),
        }
        for i in range(hours - 1, -1, -1)
    ]


def get_stats() -> dict:
    now = datetime.now(UTC)
    today_start = now.strftime("%Y-%m-%dT00:00:00")
    ago_1h = (now - timedelta(hours=1)).isoformat()
    ago_5m = (now - timedelta(minutes=5)).isoformat()
    ago_24h = (now - timedelta(hours=24)).isoformat()

    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        active = conn.execute(
            "SELECT COUNT(DISTINCT session) FROM connections WHERE ts>=?", (ago_5m,)
        ).fetchone()[0]
        ips_1h = conn.execute(
            "SELECT COUNT(DISTINCT ip) FROM connections WHERE ts>=?", (ago_1h,)
        ).fetchone()[0]
        c_today = conn.execute(
            "SELECT COUNT(*) FROM connections WHERE ts>=?", (today_start,)
        ).fetchone()[0]
        c_total = conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0]

        sessions = conn.execute(
            """
            SELECT session, MAX(ts) as last_seen,
                   SUM(CASE WHEN ts>=? THEN 1 ELSE 0 END) as connections_today,
                   COUNT(*) as connections_total
            FROM connections
            GROUP BY session ORDER BY last_seen DESC LIMIT 25
        """,
            (today_start,),
        ).fetchall()

        recent = conn.execute(
            "SELECT ts, ip, session, user_agent FROM connections ORDER BY ts DESC LIMIT 50"
        ).fetchall()

        hourly_rows = conn.execute(
            """
            SELECT strftime('%Y-%m-%dT%H:00Z', ts) as hour, COUNT(*) as cnt
            FROM connections WHERE ts>=? GROUP BY hour ORDER BY hour
        """,
            (ago_24h,),
        ).fetchall()

    return {
        "generated_at": now.isoformat(),
        "summary": {
            "active_sessions": active,
            "connected_ips_1h": ips_1h,
            "connections_today": c_today,
            "connections_total": c_total,
        },
        "sessions": [
            {
                "name": r["session"],
                "last_seen": r["last_seen"],
                "connections_today": r["connections_today"],
                "connections_total": r["connections_total"],
            }
            for r in sessions
        ],
        "recent_connections": [
            {"ts": r["ts"], "ip": r["ip"], "session": r["session"], "user_agent": r["user_agent"]}
            for r in recent
        ],
        "hourly_chart": _fill_hourly([(r["hour"], r["cnt"]) for r in hourly_rows]),
    }


# ── HTTP stats server ──────────────────────────────────────────────────────────


def _check_auth(auth_header: str) -> bool:
    if not STATS_USERNAME:
        return True
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        username, _, password = decoded.partition(":")
    except Exception:
        return False
    return secrets.compare_digest(
        username.encode(), STATS_USERNAME.encode()
    ) and secrets.compare_digest(password.encode(), STATS_PASSWORD.encode())


def _make_app():
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _auth(request: Request):
        if not _check_auth(request.headers.get("Authorization", "")):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Zellij Stats"'},
                content="Unauthorized",
            )
        return None

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/")
    async def stats_page(request: Request):
        denied = _auth(request)
        if denied:
            return denied
        return HTMLResponse(_HTML)

    @app.get("/api")
    async def stats_api(request: Request):
        denied = _auth(request)
        if denied:
            return denied
        return JSONResponse(get_stats())

    return app


def _run_stats_server() -> None:
    import uvicorn

    uvicorn.run(_make_app(), host="0.0.0.0", port=STATS_PORT, log_level="warning")


# ── Alert helpers ──────────────────────────────────────────────────────────────


def _extract_ip(entry: dict) -> str:
    """Return the real TCP-layer client IP from a Traefik log entry."""
    for field in ("ClientAddr", "RequestAddr"):
        val = entry.get(field, "")
        if val:
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
    auth_label = f"(user={STATS_USERNAME})" if STATS_USERNAME else "(no auth)"
    print("[alerter] Channels:", flush=True)
    print(f"[alerter]   Telegram  {tg_check}  {tg_status}", flush=True)
    print(f"[alerter]   Discord   {discord_status}{discord_label}", flush=True)
    print(f"[alerter]   Webhooks  {webhook_status}  {webhook_label}", flush=True)
    print(f"[alerter]   Cooldown  {ALERT_COOLDOWN_SECONDS}s", flush=True)
    print(f"[alerter]   Stats     +  :{STATS_PORT} {auth_label}", flush=True)


# ── Log processing ─────────────────────────────────────────────────────────────


def process_line(line: str) -> None:
    line = line.strip()
    if not line:
        return
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return

    status = entry.get("DownstreamStatus")

    # Traefik logs WebSocket connections as DownstreamStatus=0 (connection hijacked,
    # status never sent back through Traefik's response layer). The log entry is
    # written when the connection closes; StartUTC is when it opened.
    path = entry.get("RequestPath", "")
    if status == 0 and _parse_session(path) is not None:
        payload = _build_payload(entry)
        session = _parse_session(payload["path"])
        # Use StartUTC (connection open time) rather than when the log was written
        ts_open = entry.get("StartUTC", entry.get("time", datetime.now(UTC).isoformat()))
        store_event(payload["client_ip"], session, payload["user_agent"], payload["path"], ts_open)

    if status != ALERT_ON_STATUS:
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


# ── Main loop ──────────────────────────────────────────────────────────────────


def _backfill(path: str) -> None:
    """Replay existing log to seed the DB with historical WebSocket connections."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            count = 0
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = entry.get("RequestPath", "")
                if entry.get("DownstreamStatus") == 0 and _parse_session(p) is not None:
                    payload = _build_payload(entry)
                    session = _parse_session(payload["path"])
                    ts_open = entry.get(
                        "StartUTC", entry.get("time", datetime.now(UTC).isoformat())
                    )
                    store_event(
                        payload["client_ip"],
                        session,
                        payload["user_agent"],
                        payload["path"],
                        ts_open,
                    )
                    count += 1
        print(f"[alerter] Backfilled {count} historical connections", flush=True)
    except FileNotFoundError:
        pass


def tail(path: str) -> None:
    init_db()
    t = threading.Thread(target=_run_stats_server, daemon=True)
    t.start()

    print(f"[alerter] Waiting for {path} ...", flush=True)
    while not os.path.exists(path):
        time.sleep(5)
    print(f"[alerter] Watching {path}", flush=True)
    _backfill(path)
    _validate_config()
    _print_config()
    while True:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                fh.seek(0, 2)
                inode = os.fstat(fh.fileno()).st_ino
                while True:
                    line = fh.readline()
                    if not line:
                        time.sleep(0.3)
                        try:
                            st = os.stat(path)
                        except FileNotFoundError:
                            break
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
