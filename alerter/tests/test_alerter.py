import json

import alerter as a  # noqa: E402 (local package, no install)

# ── _extract_ip ───────────────────────────────────────────────────────────────


def test_extract_ip_from_real_ip_header():
    # request_X-Real-Ip is a spoofable request header and is intentionally ignored;
    # an entry with only that field should fall back to "unknown".
    assert a._extract_ip({"request_X-Real-Ip": "1.2.3.4"}) == "unknown"


def test_extract_ip_strips_port():
    assert a._extract_ip({"ClientAddr": "1.2.3.4:54321"}) == "1.2.3.4"


def test_extract_ip_ipv6():
    assert a._extract_ip({"ClientAddr": "[::1]:54321"}) == "::1"


def test_extract_ip_falls_back_to_unknown():
    assert a._extract_ip({}) == "unknown"


def test_extract_ip_prefers_real_ip_over_client_addr():
    # ClientAddr is the authoritative TCP-layer IP; request_X-Real-Ip is ignored.
    entry = {"request_X-Real-Ip": "9.9.9.9", "ClientAddr": "127.0.0.1:1234"}
    assert a._extract_ip(entry) == "127.0.0.1"


# ── _build_payload ────────────────────────────────────────────────────────────


def test_build_payload_fields():
    entry = {
        "ClientAddr": "5.5.5.5:12345",
        "request_X-Forwarded-For": "5.5.5.5, 10.0.0.1",
        "request_User-Agent": "Mozilla/5.0",
        "RequestPath": "/my-session",
        "DownstreamStatus": 101,
        "ServiceName": "zellij",
        "time": "2026-01-01T00:00:00+00:00",
    }
    p = a._build_payload(entry)
    assert p["event"] == "zellij_connect"
    assert p["client_ip"] == "5.5.5.5"
    assert p["x_forwarded_for"] == "5.5.5.5, 10.0.0.1"
    assert p["user_agent"] == "Mozilla/5.0"
    assert p["path"] == "/my-session"
    assert p["status"] == 101
    assert p["service"] == "zellij"
    assert p["timestamp"] == "2026-01-01T00:00:00+00:00"


def test_build_payload_defaults_for_missing_fields():
    p = a._build_payload({})
    assert p["client_ip"] == "unknown"
    assert p["path"] == "/"
    assert p["service"] == "unknown"
    assert p["x_forwarded_for"] == ""


# ── build_webhook_body ────────────────────────────────────────────────────────


def test_build_webhook_body_no_template_returns_payload():
    payload = {"event": "zellij_connect", "client_ip": "1.2.3.4"}
    assert a.build_webhook_body(payload, "") is payload


def test_build_webhook_body_renders_template():
    payload = {
        "event": "zellij_connect",
        "client_ip": "1.2.3.4",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "x_forwarded_for": "",
        "user_agent": "curl/8.0",
        "path": "/session",
        "status": "101",
        "service": "zellij",
    }
    tmpl = '{{"text": "Connection from {client_ip} at {timestamp}"}}'
    result = a.build_webhook_body(payload, tmpl)
    assert result == {"text": "Connection from 1.2.3.4 at 2026-01-01T00:00:00+00:00"}


def test_build_webhook_body_bad_key_falls_back(capsys):
    payload = {"event": "zellij_connect", "client_ip": "1.2.3.4"}
    tmpl = '{{"text": "{nonexistent_field}"}}'
    result = a.build_webhook_body(payload, tmpl)
    assert result is payload
    captured = capsys.readouterr()
    assert "WEBHOOK_BODY_TEMPLATE error" in captured.err


def test_build_webhook_body_invalid_json_falls_back(capsys):
    payload = {
        "client_ip": "1.2.3.4",
        "event": "e",
        "timestamp": "t",
        "x_forwarded_for": "",
        "user_agent": "u",
        "path": "/",
        "status": "101",
        "service": "s",
    }
    tmpl = "not json at all {client_ip}"
    result = a.build_webhook_body(payload, tmpl)
    assert result is payload
    captured = capsys.readouterr()
    assert "WEBHOOK_BODY_TEMPLATE error" in captured.err


# ── process_line ──────────────────────────────────────────────────────────────


def test_process_line_skips_non_101(mocker):
    send_tg = mocker.patch.object(a, "send_telegram")
    send_wh = mocker.patch.object(a, "send_webhooks")
    a.process_line(json.dumps({"DownstreamStatus": 200, "RequestPath": "/"}))
    send_tg.assert_not_called()
    send_wh.assert_not_called()


def test_process_line_fires_on_alert_status(mocker):
    send_tg = mocker.patch.object(a, "send_telegram")
    send_wh = mocker.patch.object(a, "send_webhooks")
    entry = {"DownstreamStatus": a.ALERT_ON_STATUS, "RequestPath": "/s", "ClientAddr": "1.1.1.1:9"}
    a.process_line(json.dumps(entry))
    send_tg.assert_called_once()
    send_wh.assert_called_once()


def test_process_line_ignores_blank():
    # Should not raise
    a.process_line("")
    a.process_line("   ")


def test_process_line_ignores_invalid_json():
    a.process_line("{bad json")


# ── deduplication ─────────────────────────────────────────────────────────────


def test_dedup_suppresses_repeat_within_cooldown(mocker, capsys):
    a._last_alert.clear()
    send_tg = mocker.patch.object(a, "send_telegram")
    send_wh = mocker.patch.object(a, "send_webhooks")
    send_dc = mocker.patch.object(a, "send_discord")
    entry = json.dumps(
        {"DownstreamStatus": a.ALERT_ON_STATUS, "RequestPath": "/s", "ClientAddr": "2.2.2.2:9"}
    )
    # First call should fire
    a.process_line(entry)
    assert send_tg.call_count == 1
    # Second call within cooldown should be suppressed
    a.process_line(entry)
    assert send_tg.call_count == 1
    assert send_wh.call_count == 1
    assert send_dc.call_count == 1
    out = capsys.readouterr().out
    assert "Suppressed duplicate alert for 2.2.2.2" in out
    a._last_alert.clear()


def test_first_alert_fires_shortly_after_boot(mocker):
    # time.monotonic() counts from boot, so it can be below the cooldown on a fresh host
    a._last_alert.clear()
    mocker.patch("alerter.time.monotonic", return_value=5.0)
    send_tg = mocker.patch.object(a, "send_telegram")
    mocker.patch.object(a, "send_webhooks")
    mocker.patch.object(a, "send_discord")
    entry = json.dumps(
        {"DownstreamStatus": a.ALERT_ON_STATUS, "RequestPath": "/s", "ClientAddr": "5.5.5.5:9"}
    )
    a.process_line(entry)
    assert send_tg.call_count == 1
    a._last_alert.clear()


def test_dedup_allows_after_cooldown(mocker):
    a._last_alert.clear()
    send_tg = mocker.patch.object(a, "send_telegram")
    mocker.patch.object(a, "send_webhooks")
    mocker.patch.object(a, "send_discord")
    entry = json.dumps(
        {"DownstreamStatus": a.ALERT_ON_STATUS, "RequestPath": "/s", "ClientAddr": "3.3.3.3:9"}
    )
    # First call
    a.process_line(entry)
    assert send_tg.call_count == 1
    # Advance time past cooldown
    future = a._last_alert["3.3.3.3"] + a.ALERT_COOLDOWN_SECONDS + 1
    mocker.patch("alerter.time.monotonic", return_value=future)
    a.process_line(entry)
    assert send_tg.call_count == 2
    a._last_alert.clear()


# ── send_discord ──────────────────────────────────────────────────────────────

_DISCORD_PAYLOAD = {
    "client_ip": "1.2.3.4",
    "path": "/s",
    "user_agent": "curl",
    "timestamp": "t",
    "x_forwarded_for": "",
}


def test_send_discord_posts_content(mocker, monkeypatch):
    monkeypatch.setattr(a, "DISCORD_WEBHOOK_URL", "https://discord.example.com/webhook")
    mock_post = mocker.patch("alerter.requests.post")
    mock_post.return_value.raise_for_status = lambda: None
    a.send_discord(_DISCORD_PAYLOAD)
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "https://discord.example.com/webhook"
    assert "content" in call_kwargs[1]["json"]


def test_send_discord_skips_when_no_url(mocker, monkeypatch):
    monkeypatch.setattr(a, "DISCORD_WEBHOOK_URL", "")
    mock_post = mocker.patch("alerter.requests.post")
    a.send_discord(_DISCORD_PAYLOAD)
    mock_post.assert_not_called()


def test_process_line_calls_discord(mocker):
    a._last_alert.clear()
    mocker.patch.object(a, "send_telegram")
    mocker.patch.object(a, "send_webhooks")
    send_dc = mocker.patch.object(a, "send_discord")
    entry = json.dumps(
        {"DownstreamStatus": a.ALERT_ON_STATUS, "RequestPath": "/s", "ClientAddr": "4.4.4.4:9"}
    )
    a.process_line(entry)
    send_dc.assert_called_once()
    a._last_alert.clear()


# ── _last_alert pruning ───────────────────────────────────────────────────────


def test_last_alert_pruned_after_cooldown(mocker):
    a._last_alert.clear()
    stale_ip = "9.9.9.9"
    other_ip = "8.8.8.8"
    # Seed a stale entry that is past the cooldown window
    a._last_alert[stale_ip] = a.time.monotonic() - a.ALERT_COOLDOWN_SECONDS - 1
    mocker.patch.object(a, "send_telegram")
    mocker.patch.object(a, "send_webhooks")
    mocker.patch.object(a, "send_discord")
    # Fire with a different IP so no suppression is triggered
    entry = json.dumps(
        {"DownstreamStatus": a.ALERT_ON_STATUS, "RequestPath": "/s", "ClientAddr": f"{other_ip}:9"}
    )
    a.process_line(entry)
    assert stale_ip not in a._last_alert
    a._last_alert.clear()


# ── _validate_config ──────────────────────────────────────────────────────────


def test_validate_config_warns_on_bad_template(capsys):
    original = a.WEBHOOK_BODY_TEMPLATE
    a.WEBHOOK_BODY_TEMPLATE = "not json {client_ip}"
    try:
        a._validate_config()
    finally:
        a.WEBHOOK_BODY_TEMPLATE = original
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


def test_validate_config_silent_on_good_template(capsys):
    original = a.WEBHOOK_BODY_TEMPLATE
    a.WEBHOOK_BODY_TEMPLATE = '{{"text": "{client_ip}"}}'
    try:
        a._validate_config()
    finally:
        a.WEBHOOK_BODY_TEMPLATE = original
    captured = capsys.readouterr()
    assert captured.err == ""


def test_validate_config_silent_when_no_template(capsys):
    original = a.WEBHOOK_BODY_TEMPLATE
    a.WEBHOOK_BODY_TEMPLATE = ""
    try:
        a._validate_config()
    finally:
        a.WEBHOOK_BODY_TEMPLATE = original
    captured = capsys.readouterr()
    assert captured.err == ""


# ── SSO user ──────────────────────────────────────────────────────────────────

_O2P_ENTRY = {
    "ClientAddr": "6.6.6.6:9",
    "RequestPath": "/s",
    "request_X-Auth-Request-User": "c0ffee-sub",
    "request_X-Auth-Request-Preferred-Username": "alice",
    "request_X-Auth-Request-Email": "alice@example.com",
}


def test_sso_user_from_oauth2_proxy_headers():
    assert a._extract_sso(_O2P_ENTRY, "oauth2-proxy") == ("alice", "alice@example.com")


def test_sso_user_falls_back_to_email():
    entry = {"request_X-Auth-Request-Email": "bob@example.com"}
    assert a._extract_sso(entry, "oauth2-proxy") == ("bob@example.com", "bob@example.com")


def test_sso_user_from_authelia_and_authentik():
    assert a._extract_sso({"request_Remote-User": "carol"}, "forward-auth")[0] == "carol"
    # Traefik canonicalises header names, match case-insensitively anyway
    assert a._extract_sso({"request_X-authentik-username": "dave"}, "forward-auth")[0] == "dave"


def test_sso_user_ignored_when_sso_off():
    # Without the forwardAuth middleware a client can send these headers itself
    assert a._extract_sso(_O2P_ENTRY, "off") == ("", "")


def test_sso_user_ignores_headers_of_the_other_mode():
    # forward-auth doesn't strip X-Auth-Request-*, so they must not be trusted there
    assert a._extract_sso(_O2P_ENTRY, "forward-auth") == ("", "")


def test_sso_user_in_payload_and_alerts(mocker, monkeypatch):
    monkeypatch.setattr(a, "SSO_MODE", "oauth2-proxy")
    p = a._build_payload(_O2P_ENTRY)
    assert p["sso_user"] == "alice"
    assert p["sso_email"] == "alice@example.com"
    monkeypatch.setattr(a, "DISCORD_WEBHOOK_URL", "https://discord.example.com/webhook")
    monkeypatch.setattr(a, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(a, "TELEGRAM_CHAT_ID", "1")
    mock_post = mocker.patch("alerter.requests.post")
    a.send_discord(p)
    a.send_telegram(p)
    discord_text = mock_post.call_args_list[0][1]["json"]["content"]
    telegram_text = mock_post.call_args_list[1][1]["json"]["text"]
    assert "User: alice <alice@example.com>" in discord_text
    assert "User: `alice <alice@example.com>`" in telegram_text


def test_no_user_line_without_sso(mocker, monkeypatch):
    monkeypatch.setattr(a, "DISCORD_WEBHOOK_URL", "https://discord.example.com/webhook")
    mock_post = mocker.patch("alerter.requests.post")
    a.send_discord(_DISCORD_PAYLOAD)
    assert "User:" not in mock_post.call_args[1]["json"]["content"]


def test_cooldown_is_per_user_behind_one_ip(mocker, monkeypatch):
    a._last_alert.clear()
    monkeypatch.setattr(a, "SSO_MODE", "oauth2-proxy")
    send_tg = mocker.patch.object(a, "send_telegram")
    mocker.patch.object(a, "send_webhooks")
    mocker.patch.object(a, "send_discord")
    for user in ("alice", "bob", "alice"):
        a.process_line(
            json.dumps(
                {
                    "DownstreamStatus": a.ALERT_ON_STATUS,
                    "RequestPath": "/s",
                    "ClientAddr": "7.7.7.7:9",
                    "request_X-Auth-Request-Preferred-Username": user,
                }
            )
        )
    assert send_tg.call_count == 2
    a._last_alert.clear()


def test_stats_store_sso_user_and_migrate_old_db(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "stats.db"
    with sqlite3.connect(db) as conn:  # schema from before SSO support
        conn.execute(
            "CREATE TABLE connections (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
            " ip TEXT NOT NULL, session TEXT NOT NULL DEFAULT '',"
            " user_agent TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO connections (ts, ip) VALUES ('2020-01-01T00:00:00', '1.1.1.1')")
    monkeypatch.setattr(a, "DB_PATH", str(db))
    a.init_db()
    a.store_event("2.2.2.2", "s", "ua", "/ws/terminal/s", sso_user="alice")
    stats = a.get_stats()
    users = [c["sso_user"] for c in stats["recent_connections"]]
    assert users == ["alice", ""]
    assert stats["summary"]["sso_users_1h"] == 1


# ── fail2ban ban alerts ───────────────────────────────────────────────────────


def _ban_line(user="", email=""):
    entry = {
        "ClientAddr": "203.0.113.9:5555",
        "DownstreamStatus": 401,
        "RequestPath": "/command/login",
        "request_User-Agent": "curl/8",
        "time": "2026-11-22T19:35:42Z",
    }
    if user:
        entry["request_X-Auth-Request-Preferred-Username"] = user
    if email:
        entry["request_X-Auth-Request-Email"] = email
    return json.dumps(entry, separators=(",", ":"))


def _ban_data(*lines):
    # fail2ban 1.1 keeps some matches whole and some split around the timestamp
    matches = []
    for i, line in enumerate(lines):
        if i % 2 == 0:
            head, _, tail = line.partition('"time":')
            matches.append([head, '"time":' + tail[:-1], "}"])
        else:
            matches.append(line)
    return json.dumps({"matches": matches, "failures": len(lines)})


def _make_f2b_db(path):
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE bans(jail TEXT NOT NULL, ip TEXT, timeofban INTEGER NOT NULL,"
            " bantime INTEGER NOT NULL, bancount INTEGER NOT NULL default 1, data JSON)"
        )


def _add_ban(path, ip, ts, data="{}"):
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO bans (jail, ip, timeofban, bantime, data) VALUES (?,?,?,?,?)",
            ("traefik-zellij", ip, ts, 3600, data),
        )


def test_ban_log_entries_joins_split_matches():
    entries = a._ban_log_entries(_ban_data(_ban_line("alice"), _ban_line("alice")))
    assert len(entries) == 2
    assert entries[0]["RequestPath"] == "/command/login"


def test_ban_log_entries_tolerates_garbage():
    assert a._ban_log_entries(None) == []
    assert a._ban_log_entries("not json") == []
    assert a._ban_log_entries(json.dumps({"matches": ["nope", ["{", "x"]]})) == []


def test_ban_payload_carries_sso_users(monkeypatch):
    monkeypatch.setattr(a, "SSO_MODE", "oauth2-proxy")
    data = _ban_data(_ban_line("alice", "alice@example.com"), _ban_line("bob"), _ban_line("alice"))
    p = a._build_ban_payload("traefik-zellij", "203.0.113.9", 1795376142, 3600, data)
    assert p["event"] == "fail2ban_ban"
    assert p["client_ip"] == "203.0.113.9"
    assert p["sso_user"] == "alice, bob"
    assert p["sso_email"] == "alice@example.com"
    assert p["failures"] == 3
    assert p["path"] == "/command/login"
    assert p["status"] == 401
    assert p["timestamp"].startswith("2026-11-22T")


def test_ban_payload_without_sso(monkeypatch):
    monkeypatch.setattr(a, "SSO_MODE", "off")
    p = a._build_ban_payload("j", "1.2.3.4", 0, 60, _ban_data(_ban_line("mallory")))
    assert p["sso_user"] == ""


def test_ban_message_texts(monkeypatch):
    monkeypatch.setattr(a, "SSO_MODE", "oauth2-proxy")
    p = a._build_ban_payload(
        "traefik-zellij", "203.0.113.9", 0, 3600, _ban_data(_ban_line("alice", "alice@example.com"))
    )
    tg = a._message(p, code=True)
    dc = a._message(p, code=False)
    assert tg.startswith("\U0001f6ab *IP banned by fail2ban*")
    assert "User: `alice <alice@example.com>`" in tg
    assert "IP: `203.0.113.9`" in tg
    assert "Jail: traefik-zellij (1 failures, banned for 3600s)" in dc
    assert "User: alice <alice@example.com>" in dc


def test_ban_watcher_skips_old_bans_then_alerts_new(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "SSO_MODE", "oauth2-proxy")
    db = str(tmp_path / "fail2ban.sqlite3")
    w = a.BanWatcher(db)
    assert w.poll() == []  # no DB yet
    _make_f2b_db(db)
    _add_ban(db, "198.51.100.1", 100)
    assert w.poll() == []  # existing bans are history
    _add_ban(db, "198.51.100.2", 100, _ban_data(_ban_line("alice")))  # same second
    _add_ban(db, "198.51.100.3", 105, _ban_data(_ban_line("bob")))
    got = w.poll()
    assert [(p["client_ip"], p["sso_user"]) for p in got] == [
        ("198.51.100.2", "alice"),
        ("198.51.100.3", "bob"),
    ]
    assert w.poll() == []


def test_ban_watcher_survives_purge(tmp_path):
    import sqlite3

    db = str(tmp_path / "fail2ban.sqlite3")
    _make_f2b_db(db)
    _add_ban(db, "198.51.100.1", 100)
    w = a.BanWatcher(db)
    w.poll()
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM bans")
    _add_ban(db, "198.51.100.4", 200)
    assert [p["client_ip"] for p in w.poll()] == ["198.51.100.4"]


def test_ban_watcher_unreadable_db(tmp_path, capsys):
    db = tmp_path / "fail2ban.sqlite3"
    db.write_text("not a database")
    assert a.BanWatcher(str(db)).poll() == []
    assert "fail2ban DB not readable" in capsys.readouterr().err
