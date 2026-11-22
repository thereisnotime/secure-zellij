#!/usr/bin/env python3
"""
Tails Traefik JSON access logs and fires alerts on successful zellij connections.

A "connection" is detected by HTTP 101 (WebSocket upgrade) — the handshake
zellij's web terminal uses. Each event ships to all configured destinations:
Telegram bot and/or a list of generic webhook URLs.

Also serves a live stats dashboard on STATS_PORT (default 8083).
"""

import asyncio
import base64
import contextlib
import ipaddress
import json
import os
import re
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
# fail2ban's own database (read-only); every new ban there becomes an alert
F2B_DB = os.environ.get("F2B_DB", "")
BAN_ALERTS = os.environ.get("BAN_ALERTS", "true").strip().lower() in ("1", "true", "yes", "on")
F2B_POLL_SECONDS = float(os.environ.get("F2B_POLL_SECONDS", "5"))
# Unban button: requests go to the fail2ban-unban helper through this directory
UNBAN_ENABLED = os.environ.get("UNBAN_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
UNBAN_DIR = os.environ.get("UNBAN_DIR", "")
UNBAN_TIMEOUT = float(os.environ.get("UNBAN_TIMEOUT", "10"))
_JAIL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# Which identity headers Traefik's forwardAuth sets, per SSO_MODE (docs/sso.md).
# forwardAuth deletes every header in its authResponseHeaders list before copying
# the IdP's answer in, so only these are trustworthy. With SSO off nothing is,
# since a client can send any header it likes.
SSO_MODE = os.environ.get("SSO_MODE", "off").strip() or "off"
_SSO_USER_HEADERS = {
    "oauth2-proxy": (
        "X-Auth-Request-Preferred-Username",
        "X-Auth-Request-User",
        "X-Auth-Request-Email",
    ),
    # Authelia, then Authentik
    "forward-auth": ("Remote-User", "X-Authentik-Username", "Remote-Email", "X-Authentik-Email"),
}
_SSO_EMAIL_HEADERS = {
    "oauth2-proxy": ("X-Auth-Request-Email",),
    "forward-auth": ("Remote-Email", "X-Authentik-Email"),
}

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
    .sso-col { display: none; }
    body.sso .sso-col { display: table-cell; }
    .sso-card { display: none; }
    body.sso .sso-card { display: block; }
    .ban-only { display: none; }
    body.bans .ban-only { display: block; }
    .card.ban-only .val { color: #f7768e; }
    .ban-active { color: #f7768e; }
    .unban { background: none; border: 1px solid #f7768e; color: #f7768e; border-radius: 4px; font: inherit; font-size: 0.72rem; padding: 0.1rem 0.5rem; cursor: pointer; }
    .unban-col { display: none; width: 1%; }
    body.can-unban .unban-col { display: table-cell; }
    .unban:hover { background: #f7768e; color: var(--bg); }
    .unban:disabled { opacity: 0.5; cursor: wait; }
    .unban-hint { color: var(--dim); font-size: 0.72rem; margin-top: 0.4rem; }
    #unban-msg { font-size: 0.75rem; margin-top: 0.4rem; min-height: 1em; }
    .empty { color: var(--dim); padding: 0.5rem 0.6rem; font-style: italic; }
  </style>
</head>
<body>
  <h1>&#9889; Zellij Dashboard <span id="status">loading&hellip;</span></h1>

  <div class="grid">
    <div class="card"><div class="val" id="c-active">&#x2014;</div><div class="lbl">Active Sessions (5m)</div></div>
    <div class="card"><div class="val" id="c-ips1h">&#x2014;</div><div class="lbl">Unique IPs (1h)</div></div>
    <div class="card sso-card"><div class="val" id="c-users1h">&#x2014;</div><div class="lbl">SSO Users (1h)</div></div>
    <div class="card ban-only"><div class="val" id="c-banned">&#x2014;</div><div class="lbl">Banned Now</div></div>
    <div class="card ban-only"><div class="val" id="c-bans24h">&#x2014;</div><div class="lbl">Bans (24h)</div></div>
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
    <table><thead><tr><th>Time</th><th class="sso-col">User</th><th>IP</th><th>Session</th><th>User Agent</th></tr></thead>
    <tbody id="tb-recent"><tr><td colspan="5" class="empty">loading&hellip;</td></tr></tbody></table>
  </div>

  <div class="section ban-only">
    <h2>fail2ban Bans</h2>
    <table><thead><tr><th>Time</th><th class="sso-col">User</th><th>IP</th><th>Jail</th><th>Failures</th><th>Last Request</th><th>Status</th><th class="unban-col"></th></tr></thead>
    <tbody id="tb-bans"><tr><td colspan="8" class="empty">loading&hellip;</td></tr></tbody></table>
    <div id="unban-msg"></div>
    <div class="unban-hint" id="unban-hint"></div>
  </div>

  <script>
    function esc(s) {
      // also quotes: values land in title="..." attributes and come from clients
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    }
    function fmtTs(s) {
      try { return new Date(s).toLocaleString(); } catch(_) { return s; }
    }
    function banStatus(b) {
      if (b.unbanned_by) return 'unbanned by ' + b.unbanned_by + ' at ' + fmtTs(b.unbanned_at);
      if (b.active) return 'banned ' + (b.until ? 'until ' + fmtTs(b.until) : 'permanently');
      return 'expired';
    }
    async function refresh() {
      try {
        const r = await fetch('api');
        if (!r.ok) { document.getElementById('status').textContent = 'error ' + r.status; return; }
        const d = await r.json();
        const s = d.summary;
        document.getElementById('c-active').textContent = s.active_sessions;
        document.getElementById('c-ips1h').textContent  = s.connected_ips_1h;
        document.getElementById('c-users1h').textContent = s.sso_users_1h;
        document.getElementById('c-today').textContent  = s.connections_today;
        document.getElementById('c-total').textContent  = s.connections_total;
        document.getElementById('status').textContent   = 'updated ' + new Date().toLocaleTimeString();
        document.body.classList.toggle('sso', d.sso_enabled);
        document.body.classList.toggle('bans', d.bans.enabled);
        document.body.classList.toggle('can-unban', d.bans.enabled && d.bans.unban);
        if (d.bans.enabled) {
          document.getElementById('c-banned').textContent  = d.bans.active;
          document.getElementById('c-bans24h').textContent = d.bans.last_24h;
          document.getElementById('tb-bans').innerHTML = !d.bans.available
            ? '<tr><td colspan="8" class="empty">fail2ban database not readable yet</td></tr>'
            : d.bans.recent.length
            ? d.bans.recent.map(b =>
                '<tr><td>' + fmtTs(b.ts) + '</td>' +
                '<td class="sso-col" title="' + esc(b.sso_user) + '">' + (b.sso_user ? esc(b.sso_user) : '<em>none</em>') + '</td>' +
                '<td>' + esc(b.ip) + '</td>' +
                '<td>' + esc(b.jail) + '</td>' +
                '<td>' + esc(b.failures) + '</td>' +
                '<td title="' + esc(b.user_agent) + '">' + esc(b.path) + (b.status !== '' ? ' (' + esc(b.status) + ')' : '') + '</td>' +
                '<td title="' + esc(banStatus(b)) + '"' + (b.active ? ' class="ban-active"' : '') + '>' + esc(banStatus(b)) + '</td>' +
                '<td class="unban-col">' + (b.active
                  ? '<button class="unban" data-ip="' + esc(b.ip) + '" data-jail="' + esc(b.jail) + '">unban</button>'
                  : '') + '</td></tr>'
              ).join('')
            : '<tr><td colspan="8" class="empty">no bans in the last 24h</td></tr>';
          document.getElementById('unban-hint').textContent = d.bans.unban ? '' : d.bans.unban_hint;
        }

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
              '<td class="sso-col">' + (c.sso_user ? esc(c.sso_user) : '<em>unknown</em>') + '</td>' +
              '<td>' + esc(c.ip) + '</td>' +
              '<td>' + (c.session ? esc(c.session) : '<em>welcome</em>') + '</td>' +
              '<td title="' + esc(c.user_agent) + '">' + esc(c.user_agent.substring(0,55)) + '</td></tr>'
            ).join('')
          : '<tr><td colspan="5" class="empty">no connections yet</td></tr>';
      } catch(e) {
        document.getElementById('status').textContent = 'fetch error';
        console.error(e);
      }
    }
    document.getElementById('tb-bans').addEventListener('click', async ev => {
      const btn = ev.target.closest('button.unban');
      if (!btn) return;
      const ip = btn.dataset.ip, jail = btn.dataset.jail;
      if (!confirm('Unban ' + ip + ' from ' + jail + '?')) return;
      const msg = document.getElementById('unban-msg');
      btn.disabled = true;
      msg.textContent = 'unbanning ' + ip + '\u2026';
      try {
        const r = await fetch('api/unban', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'secure-zellij' },
          body: JSON.stringify({ ip: ip, jail: jail }),
        });
        const res = await r.json().catch(() => ({ ok: false, error: 'HTTP ' + r.status }));
        msg.className = res.ok ? '' : 'ban-active';
        msg.textContent = res.ok ? ip + ' unbanned' : 'unban failed: ' + res.error;
      } catch (e) {
        msg.className = 'ban-active';
        msg.textContent = 'unban failed: ' + e;
      }
      refresh();
    });
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
                path       TEXT NOT NULL DEFAULT '',
                sso_user   TEXT NOT NULL DEFAULT ''
            )
        """)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(connections)")}
        if "sso_user" not in cols:  # databases from before SSO support
            conn.execute("ALTER TABLE connections ADD COLUMN sso_user TEXT NOT NULL DEFAULT ''")
        # fail2ban deletes a ban from its DB on unban, so the dashboard keeps its own record
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unbans (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                ip          TEXT NOT NULL,
                jail        TEXT NOT NULL,
                unbanned_by TEXT NOT NULL DEFAULT '',
                ban_ts      TEXT NOT NULL DEFAULT '',
                failures    INTEGER NOT NULL DEFAULT 0,
                sso_user    TEXT NOT NULL DEFAULT '',
                path        TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT '',
                user_agent  TEXT NOT NULL DEFAULT ''
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


def store_event(
    ip: str, session: str, user_agent: str, path: str, ts: str | None = None, sso_user: str = ""
) -> None:
    if ts is None:
        ts = datetime.now(UTC).isoformat()
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO connections (ts, ip, session, user_agent, path, sso_user)"
            " VALUES (?,?,?,?,?,?)",
            (ts, ip, session, user_agent, path, sso_user),
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


def get_bans(path: str, limit: int = 50) -> dict:
    """Recent bans straight from fail2ban's DB (it keeps them for dbpurgeage, 1d)."""
    result: dict = {"enabled": bool(path), "available": False, "active": 0, "last_24h": 0}
    result["recent"] = []
    if not path or not os.path.exists(path):
        return result
    now = int(time.time())
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            active = conn.execute(
                "SELECT COUNT(DISTINCT ip) FROM bans WHERE bantime < 0 OR timeofban + bantime > ?",
                (now,),
            ).fetchone()[0]
            last_24h = conn.execute(
                "SELECT COUNT(*) FROM bans WHERE timeofban >= ?", (now - 86400,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT jail, ip, timeofban, bantime, data FROM bans"
                " ORDER BY timeofban DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return result
    recent = []
    for jail, ip, timeofban, bantime, data in rows:
        p = _build_ban_payload(jail, ip or "unknown", timeofban, bantime, data)
        permanent = bantime < 0
        recent.append(
            {
                "ts": p["timestamp"],
                "ip": p["client_ip"],
                "jail": jail,
                "failures": p["failures"],
                "bantime": bantime,
                "sso_user": p["sso_user"],
                "path": p["path"],
                "status": p["status"],
                "user_agent": p["user_agent"],
                "active": permanent or timeofban + bantime > now,
                "unbanned_by": "",
                "unbanned_at": "",
                "until": ""
                if permanent
                else datetime.fromtimestamp(timeofban + bantime, UTC).isoformat(),
            }
        )
    result.update(available=True, active=active, last_24h=last_24h, recent=recent)
    return result


def find_ban(path: str, jail: str, ip: str) -> dict | None:
    """The newest ban fail2ban has for this jail and IP, in get_bans() shape."""
    if not path or not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "SELECT jail, ip, timeofban, bantime, data FROM bans"
                " WHERE jail = ? AND ip = ? ORDER BY timeofban DESC LIMIT 1",
                (jail, ip),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    p = _build_ban_payload(row[0], row[1], row[2], row[3], row[4])
    return {
        "ts": p["timestamp"],
        "failures": p["failures"],
        "sso_user": p["sso_user"],
        "path": p["path"],
        "status": p["status"],
        "user_agent": p["user_agent"],
    }


def record_unban(ip: str, jail: str, by: str, ban: dict | None, ts: str | None = None) -> None:
    ban = ban or {}
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO unbans (ts, ip, jail, unbanned_by, ban_ts, failures, sso_user, path,"
            " status, user_agent) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                ts or datetime.now(UTC).isoformat(),
                ip,
                jail,
                by,
                ban.get("ts", ""),
                int(ban.get("failures") or 0),
                ban.get("sso_user", ""),
                ban.get("path", ""),
                str(ban.get("status", "")),
                ban.get("user_agent", ""),
            ),
        )
        conn.commit()


def _merge_unbans(bans: dict, limit: int = 50) -> None:
    """Add unbans from stats.db to get_bans() output (fail2ban forgot them)."""
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM unbans WHERE ban_ts >= ? OR ts >= ? ORDER BY ts DESC LIMIT ?",
            (since, since, limit),
        ).fetchall()
    for r in rows:
        bans["recent"].append(
            {
                "ts": r["ban_ts"] or r["ts"],
                "ip": r["ip"],
                "jail": r["jail"],
                "failures": r["failures"],
                "bantime": 0,
                "sso_user": r["sso_user"],
                "path": r["path"],
                "status": r["status"],
                "user_agent": r["user_agent"],
                "active": False,
                "until": "",
                "unbanned_by": r["unbanned_by"],
                "unbanned_at": r["ts"],
            }
        )
        if r["ban_ts"] >= since:
            bans["last_24h"] += 1
    bans["recent"].sort(key=lambda b: b["ts"], reverse=True)
    del bans["recent"][limit:]


def unban_status() -> tuple[bool, str]:
    """Whether the unban button is offered, and why not."""
    if not UNBAN_ENABLED:
        return False, "Unbanning from the dashboard is off (UNBAN_ENABLED=false)."
    if not UNBAN_DIR or not os.path.isdir(UNBAN_DIR):
        return False, "Unbanning needs the fail2ban-unban container (see docs/fail2ban.md)."
    if not STATS_USERNAME and SSO_MODE == "off":
        return (
            False,
            "Unbanning needs a login on this page: set STATS_USERNAME/STATS_PASSWORD or enable SSO.",
        )
    return True, ""


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
        users_1h = conn.execute(
            "SELECT COUNT(DISTINCT sso_user) FROM connections WHERE ts>=? AND sso_user!=''",
            (ago_1h,),
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
            "SELECT ts, ip, session, user_agent, sso_user FROM connections"
            " ORDER BY ts DESC LIMIT 50"
        ).fetchall()

        hourly_rows = conn.execute(
            """
            SELECT strftime('%Y-%m-%dT%H:00Z', ts) as hour, COUNT(*) as cnt
            FROM connections WHERE ts>=? GROUP BY hour ORDER BY hour
        """,
            (ago_24h,),
        ).fetchall()

    bans = get_bans(F2B_DB)
    if bans["enabled"]:
        _merge_unbans(bans)
    bans["unban"], bans["unban_hint"] = unban_status()

    return {
        "generated_at": now.isoformat(),
        "sso_enabled": SSO_MODE != "off",
        "bans": bans,
        "summary": {
            "active_sessions": active,
            "connected_ips_1h": ips_1h,
            "sso_users_1h": users_1h,
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
            {
                "ts": r["ts"],
                "ip": r["ip"],
                "session": r["session"],
                "user_agent": r["user_agent"],
                "sso_user": r["sso_user"],
            }
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

    @app.post("/api/unban")
    async def unban(request: Request):
        denied = _auth(request)
        if denied:
            return denied
        allowed, why = unban_status()
        if not allowed:
            return JSONResponse({"ok": False, "error": why}, status_code=403)
        if not _same_origin(request.headers):
            return JSONResponse(
                {"ok": False, "error": "cross-site request refused"}, status_code=403
            )
        who = _request_identity(request.headers)
        if not who:
            return JSONResponse({"ok": False, "error": "no signed-in user"}, status_code=403)
        try:
            body = await request.json()
            jail = str(body["jail"])
            ip = str(ipaddress.ip_address(str(body["ip"])))
        except ValueError, KeyError, TypeError:
            return JSONResponse({"ok": False, "error": "need a jail and an IP"}, status_code=400)
        if not _JAIL_RE.match(jail):
            return JSONResponse({"ok": False, "error": "bad jail name"}, status_code=400)

        ban = find_ban(F2B_DB, jail, ip)  # fail2ban deletes it on unban
        result = await request_unban(jail, ip)
        # Logical failures stay 200: a 4xx here would count towards a ban of the admin
        if not result.get("ok"):
            print(f"[alerter] Unban of {ip} by {who} failed: {result.get('error')}", flush=True)
            return JSONResponse({"ok": False, "error": str(result.get("error", "failed"))})
        record_unban(ip, jail, who, ban)
        print(f"[alerter] Unban: {ip} in {jail} by {who}", flush=True)
        payload = _build_unban_payload(jail, ip, who, ban)
        threading.Thread(target=dispatch, args=(payload,), daemon=True).start()
        return JSONResponse({"ok": True, "ip": ip, "jail": jail})

    return app


def _same_origin(headers) -> bool:
    """CSRF guard for the unban POST.

    Browsers send basic-auth credentials and the SSO cookie along with
    cross-site requests, so check that the request came from our own page: the
    custom header and the JSON content type force a CORS preflight, which this
    app never answers, and Origin/Sec-Fetch-Site must match when present.
    """
    if headers.get("x-requested-with") != "secure-zellij":
        return False
    if not headers.get("content-type", "").startswith("application/json"):
        return False
    if headers.get("sec-fetch-site", "same-origin") != "same-origin":
        return False
    origin = headers.get("origin")
    if origin:
        from urllib.parse import urlsplit

        if urlsplit(origin).netloc != headers.get("host", ""):
            return False
    return True


def _request_identity(headers) -> str:
    """Who is unbanning: the SSO user Traefik vouched for, else the basic-auth user."""
    sso = _header(
        {f"request_{k}": v for k, v in headers.items()}, _SSO_USER_HEADERS.get(SSO_MODE, ())
    )
    if sso:
        return sso
    return STATS_USERNAME


async def request_unban(jail: str, ip: str) -> dict:
    """Hand the unban to the fail2ban-unban helper and wait for its answer."""
    rid = secrets.token_hex(16)
    req = os.path.join(UNBAN_DIR, f"{rid}.json")
    res = os.path.join(UNBAN_DIR, f"{rid}.result")
    tmp = os.path.join(UNBAN_DIR, f".{rid}.req")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"jail": jail, "ip": ip}, fh)
    os.replace(tmp, req)
    deadline = time.monotonic() + UNBAN_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        try:
            with open(res, encoding="utf-8") as fh:
                result = json.load(fh)
        except FileNotFoundError:
            continue
        except ValueError:
            result = {"ok": False, "error": "unreadable answer from the unban helper"}
        os.remove(res)
        return result if isinstance(result, dict) else {"ok": False, "error": "bad answer"}
    with contextlib.suppress(FileNotFoundError):
        os.remove(req)
    return {
        "ok": False,
        "error": "the unban helper didn't answer, is the fail2ban-unban container running?",
    }


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


def _header(entry: dict, names: tuple[str, ...]) -> str:
    """First non-empty request header from a Traefik log entry (case-insensitive)."""
    logged = {k.lower(): v for k, v in entry.items() if k.startswith("request_")}
    for name in names:
        val = logged.get(f"request_{name}".lower(), "")
        if isinstance(val, str) and val.strip() and val != "REDACTED":
            return val.strip()[:256]
    return ""


def _extract_sso(entry: dict, mode: str | None = None) -> tuple[str, str]:
    """Return (user, email) set by the SSO middleware, or empty strings."""
    mode = SSO_MODE if mode is None else mode
    return _header(entry, _SSO_USER_HEADERS.get(mode, ())), _header(
        entry, _SSO_EMAIL_HEADERS.get(mode, ())
    )


def _build_payload(entry: dict) -> dict:
    ip = _extract_ip(entry)
    forwarded = entry.get("request_X-Forwarded-For", "")
    user_agent = entry.get("request_User-Agent", "unknown")
    path = entry.get("RequestPath", "/")
    status = entry.get("DownstreamStatus", 0)
    ts = entry.get("time", datetime.now(UTC).isoformat())
    service = entry.get("ServiceName", "unknown")
    sso_user, sso_email = _extract_sso(entry)

    return {
        "event": "zellij_connect",
        "timestamp": ts,
        "client_ip": ip,
        "x_forwarded_for": forwarded,
        "user_agent": user_agent,
        "path": path,
        "status": status,
        "service": service,
        "sso_user": sso_user,
        "sso_email": sso_email,
    }


def _md_safe(val: str) -> str:
    return val.replace("`", "'")


def _user_line(payload: dict, code: bool) -> str:
    user = payload.get("sso_user", "")
    if not user:
        return ""
    email = payload.get("sso_email", "")
    who = f"{user} <{email}>" if email and email != user else user
    who = _md_safe(who)
    return f"User: `{who}`\n" if code else f"User: {who}\n"


def _message(payload: dict, code: bool) -> str:
    """Alert text. code=True is Telegram Markdown, code=False is Discord."""

    def q(val) -> str:
        return f"`{_md_safe(str(val))}`" if code else str(val)

    bold = "*" if code else "**"
    fwd = f"Forwarded-For: {q(payload['x_forwarded_for'])}\n" if payload["x_forwarded_for"] else ""
    ua = q(payload["user_agent"][:100])
    if payload.get("event") == "fail2ban_unban":
        banned_user = f"Banned user: {q(payload['sso_user'])}\n" if payload["sso_user"] else ""
        return (
            f"\u2705 {bold}IP unbanned{bold}\n"
            f"By: {q(payload['unbanned_by'])}\n"
            f"IP: {q(payload['client_ip'])}\n"
            f"Jail: {q(payload['jail'])}\n"
            f"{banned_user}"
            f"Time: {q(payload['timestamp'])}"
        )
    if payload.get("event") == "fail2ban_ban":
        return (
            f"\U0001f6ab {bold}IP banned by fail2ban{bold}\n"
            f"{_user_line(payload, code)}"
            f"IP: {q(payload['client_ip'])}\n"
            f"{fwd}"
            f"Jail: {q(payload['jail'])} ({payload['failures']} failures, "
            f"banned for {payload['bantime']}s)\n"
            f"Last path: {q(payload['path'])} ({payload['status']})\n"
            f"UA: {ua}\n"
            f"Time: {q(payload['timestamp'])}"
        )
    return (
        f"\U0001f50c {bold}Zellij connection{bold}\n"
        f"{_user_line(payload, code)}"
        f"IP: {q(payload['client_ip'])}\n"
        f"{fwd}"
        f"Path: {q(payload['path'])}\n"
        f"UA: {ua}\n"
        f"Time: {q(payload['timestamp'])}"
    )


def send_telegram(payload: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    text = _message(payload, code=True)
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
    text = _message(payload, code=False)
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
        "sso_user": "alice",
        "sso_email": "alice@example.com",
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
    if BAN_ALERTS and F2B_DB:
        print(f"[alerter]   Bans      +  ({F2B_DB})", flush=True)
    else:
        print("[alerter]   Bans      -  disabled", flush=True)
    print(f"[alerter]   SSO user  {'+' if SSO_MODE != 'off' else '-'}  ({SSO_MODE})", flush=True)


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
        store_event(
            payload["client_ip"],
            session,
            payload["user_agent"],
            payload["path"],
            ts_open,
            payload["sso_user"],
        )

    if status != ALERT_ON_STATUS:
        return

    payload = _build_payload(entry)
    now = time.monotonic()
    if ALERT_COOLDOWN_SECONDS > 0:
        cutoff = now - ALERT_COOLDOWN_SECONDS
        stale = [k for k, v in _last_alert.items() if v < cutoff]
        for k in stale:
            del _last_alert[k]
    # Different SSO users behind one IP (office NAT, VPN) each get their own alert
    who = f"{payload['sso_user']}@" if payload["sso_user"] else ""
    key = who + payload["client_ip"]
    last = _last_alert.get(key)
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
        print(f"[alerter] Suppressed duplicate alert for {key}", flush=True)
        return
    _last_alert[key] = now
    print(f"[alerter] Connection: {who}{payload['client_ip']} -> {payload['path']}", flush=True)
    dispatch(payload)


# ── fail2ban bans ──────────────────────────────────────────────────────────────


def _ban_log_entries(data: str | None) -> list[dict]:
    """Access log entries fail2ban kept as evidence for a ban (oldest first).

    fail2ban stores each matched line either as a string or as a list of the
    pieces around the timestamp it found, so join before parsing.
    """
    try:
        matches = json.loads(data or "{}").get("matches") or []
    except ValueError, AttributeError:
        return []
    entries = []
    for m in matches:
        line = "".join(m) if isinstance(m, list) else str(m)
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _build_ban_payload(jail: str, ip: str, timeofban: int, bantime: int, data: str | None) -> dict:
    entries = _ban_log_entries(data)
    payload = _build_payload(entries[-1] if entries else {})
    users: list[str] = []
    emails: list[str] = []
    for entry in entries:
        user, email = _extract_sso(entry)
        if user and user not in users:
            users.append(user)
        if email and email not in emails:
            emails.append(email)
    try:
        failures = json.loads(data or "{}").get("failures", len(entries))
    except ValueError, AttributeError:
        failures = len(entries)
    payload.update(
        event="fail2ban_ban",
        timestamp=datetime.fromtimestamp(timeofban, UTC).isoformat(),
        client_ip=ip,
        status=payload["status"] if entries else "",
        service=payload["service"] if entries else "",
        jail=jail,
        bantime=bantime,
        failures=failures,
        sso_user=", ".join(users),
        sso_email=", ".join(emails),
    )
    return payload


def _build_unban_payload(jail: str, ip: str, by: str, ban: dict | None) -> dict:
    ban = ban or {}
    payload = _build_payload({})
    payload.update(
        event="fail2ban_unban",
        timestamp=datetime.now(UTC).isoformat(),
        client_ip=ip,
        user_agent=ban.get("user_agent", ""),
        path=ban.get("path", ""),
        status=ban.get("status", ""),
        service="",
        jail=jail,
        bantime=0,
        failures=ban.get("failures", 0),
        sso_user=ban.get("sso_user", ""),
        sso_email="",
        unbanned_by=by,
    )
    return payload


def dispatch(payload: dict) -> None:
    send_telegram(payload)
    send_webhooks(payload)
    send_discord(payload)


class BanWatcher:
    """Polls fail2ban's sqlite DB and alerts on bans newer than the last one seen.

    Bans already in the DB when the alerter starts are skipped, so a restart
    doesn't replay the last day of bans. Tracking by time instead of rowid
    survives fail2ban purging old rows.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.cursor: int | None = None
        self.seen: set[tuple[str, str]] = set()  # (jail, ip) banned at self.cursor

    def _rows(self, since: int) -> list[tuple]:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=2)
        try:
            return conn.execute(
                "SELECT jail, ip, timeofban, bantime, data FROM bans"
                " WHERE timeofban >= ? ORDER BY timeofban",
                (since,),
            ).fetchall()
        finally:
            conn.close()

    def poll(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            if self.cursor is None:
                rows = self._rows(0)
                self.cursor = max((r[2] for r in rows), default=0)
                self.seen = {(r[0], r[1]) for r in rows if r[2] == self.cursor}
                return []
            rows = self._rows(self.cursor)
        except sqlite3.Error as exc:  # locked mid-write, or not created yet
            print(f"[alerter] fail2ban DB not readable yet: {exc}", file=sys.stderr, flush=True)
            return []
        payloads = []
        for jail, ip, timeofban, bantime, data in rows:
            if timeofban == self.cursor and (jail, ip) in self.seen:
                continue
            if timeofban > self.cursor:
                self.cursor, self.seen = timeofban, set()
            self.seen.add((jail, ip))
            payloads.append(_build_ban_payload(jail, ip or "unknown", timeofban, bantime, data))
        return payloads


def _watch_bans(path: str) -> None:
    watcher = BanWatcher(path)
    while True:
        for payload in watcher.poll():
            who = f" ({payload['sso_user']})" if payload["sso_user"] else ""
            print(f"[alerter] Ban: {payload['client_ip']}{who} in {payload['jail']}", flush=True)
            dispatch(payload)
        time.sleep(F2B_POLL_SECONDS)


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
                        payload["sso_user"],
                    )
                    count += 1
        print(f"[alerter] Backfilled {count} historical connections", flush=True)
    except FileNotFoundError:
        pass


def tail(path: str) -> None:
    init_db()
    t = threading.Thread(target=_run_stats_server, daemon=True)
    t.start()
    if BAN_ALERTS and F2B_DB:
        threading.Thread(target=_watch_bans, args=(F2B_DB,), daemon=True).start()

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
