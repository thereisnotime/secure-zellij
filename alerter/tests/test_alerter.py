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


def test_process_line_fires_on_101(mocker):
    send_tg = mocker.patch.object(a, "send_telegram")
    send_wh = mocker.patch.object(a, "send_webhooks")
    entry = {"DownstreamStatus": 101, "RequestPath": "/s", "ClientAddr": "1.1.1.1:9"}
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
    entry = json.dumps({"DownstreamStatus": 101, "RequestPath": "/s", "ClientAddr": "2.2.2.2:9"})
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


def test_dedup_allows_after_cooldown(mocker):
    a._last_alert.clear()
    send_tg = mocker.patch.object(a, "send_telegram")
    mocker.patch.object(a, "send_webhooks")
    mocker.patch.object(a, "send_discord")
    entry = json.dumps({"DownstreamStatus": 101, "RequestPath": "/s", "ClientAddr": "3.3.3.3:9"})
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
    entry = json.dumps({"DownstreamStatus": 101, "RequestPath": "/s", "ClientAddr": "4.4.4.4:9"})
    a.process_line(entry)
    send_dc.assert_called_once()
    a._last_alert.clear()
