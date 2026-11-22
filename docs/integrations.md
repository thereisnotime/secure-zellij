# Integrations

Examples for sending secure-zellij alerts to n8n, Slack, ntfy and Home Assistant through the generic webhook. Telegram and Discord have native support and don't need any of this, see [Alerting](alerting.md). That page also has the full payload and the list of placeholders.

## How webhooks work

Every URL in `WEBHOOK_URLS` gets an HTTP `POST` with a JSON body for each alert. There are three kinds, told apart by the `event` field:

| `event` | Sent when |
|---|---|
| `zellij_connect` | A terminal session (WebSocket) was opened |
| `fail2ban_ban` | fail2ban banned an IP (with `BAN_ALERTS=true`, the default) |
| `fail2ban_unban` | Someone unbanned an IP from the `/stats` dashboard |

Without `WEBHOOK_BODY_TEMPLATE` the body is the payload as is, which suits receivers that take any JSON (n8n, Home Assistant). Receivers that expect a fixed shape (Slack, ntfy) need a template.

## Writing a body template

Templates trip people up in three ways, so read this before copying the examples:

1. **Double the JSON braces.** The template is filled in with Python's `str.format`, where `{name}` is a placeholder and a literal brace is written `{{` or `}}`. A template that starts with a single `{` fails, and the alerter sends the default payload instead.
2. **Wrap the value in single quotes in `.env`.** `just` reads `.env` too, and without quotes it either refuses to parse the line (every recipe then fails) or strips the double quotes out of the JSON.
3. **Use only fields every event has:** `event`, `timestamp`, `client_ip`, `status`, `service`, `sso_user`, `sso_email`, `path`, `user_agent`, `x_forwarded_for`. One template is used for all three events, and a ban-only field such as `{jail}` makes connection alerts fall back to the default payload.

Values are JSON-escaped before they go in, so a `"` or `\` in a client-controlled value (`user_agent`, `path`, `x_forwarded_for`) can't break the JSON or add fields. Put placeholders inside JSON strings: `"{path}"`.

```dotenv
WEBHOOK_BODY_TEMPLATE='{{"text": "secure-zellij {event} from {client_ip} at {timestamp}"}}'
```

The template applies to every URL in `WEBHOOK_URLS`. To feed services that want different shapes, send the default payload to one relay (n8n, Home Assistant) and fan out from there.

Apply changes to `.env` with `just up`, which recreates the alerter. `just logs-alerter` shows a `WEBHOOK_BODY_TEMPLATE is invalid` warning at startup if the template doesn't render, and a `Webhook <url> failed` line for each failed delivery.

## n8n

Add a **Webhook** node as the trigger, set **HTTP Method** to `POST` (the node defaults to `GET`) and pick a path. No body template needed.

```dotenv
WEBHOOK_URLS=https://n8n.example.com/webhook/secure-zellij
```

Use the production URL (`/webhook/...`), which works while the workflow is active. The test URL (`/webhook-test/...`) only listens while the editor is waiting for a test event.

The Webhook node puts the request body under `body`, so the fields are `{{ $json.body.client_ip }}`, `{{ $json.body.event }}`, `{{ $json.body.sso_user }}` and so on. A **Switch** node on `{{ $json.body.event }}` separates connections from bans.

```json
{
  "nodes": [
    {
      "name": "secure-zellij alert",
      "type": "n8n-nodes-base.webhook",
      "parameters": { "httpMethod": "POST", "path": "secure-zellij", "responseMode": "onReceived" }
    }
  ]
}
```

## Slack

Create an [incoming webhook](https://api.slack.com/messaging/webhooks) for the channel and copy its URL. Slack needs a `text` field and rejects anything else, so set a template:

```dotenv
WEBHOOK_URLS=https://hooks.slack.com/services/T000/B000/xxxx
WEBHOOK_BODY_TEMPLATE='{{"text": ":electric_plug: secure-zellij {event}\nIP: {client_ip}\nUser: {sso_user}\nTime: {timestamp}"}}'
```

`\n` inside the single quotes reaches the alerter as is and becomes a newline when the JSON is parsed.

## ntfy

[ntfy](https://ntfy.sh) takes JSON when you post to the server's root URL with the topic in the body. Posting to the topic URL (`https://ntfy.sh/<topic>`) also works, but then the whole JSON payload shows up as the message text.

```dotenv
WEBHOOK_URLS=https://ntfy.sh
WEBHOOK_BODY_TEMPLATE='{{"topic": "secure-zellij-k3x9q2", "title": "secure-zellij {event}", "message": "{client_ip} at {timestamp}"}}'
```

On the public ntfy.sh server anyone who knows the topic name can read it, so pick one that is hard to guess. For a self-hosted server, use its root URL, e.g. `https://ntfy.example.com`.

## Home Assistant

Create an automation with a [webhook trigger](https://www.home-assistant.io/docs/automation/trigger/#webhook-trigger). No body template needed: the JSON body is available as `trigger.json`.

```dotenv
WEBHOOK_URLS=https://homeassistant.example.com/api/webhook/secure-zellij-f81c2a
```

```yaml
# automations.yaml
- alias: secure-zellij alert
  triggers:
    - trigger: webhook
      webhook_id: secure-zellij-f81c2a
      allowed_methods: [POST]
      local_only: true
  actions:
    - action: notify.notify
      data:
        title: "secure-zellij: {{ trigger.json.event }}"
        message: "{{ trigger.json.client_ip }} {{ trigger.json.sso_user }} at {{ trigger.json.timestamp }}"
```

The webhook ID works as a password, so make it long and random. Keep `local_only: true` when the secure-zellij host and Home Assistant are on the same network. With it, Home Assistant refuses requests from outside the local network.

## Testing a receiver

The alerter has no "send test alert" command. To check a receiver and a template without waiting for a real login, post a sample payload yourself:

```bash
curl -fsS -X POST -H 'Content-Type: application/json' \
  -d '{"event":"zellij_connect","timestamp":"2026-01-01T00:00:00+00:00","client_ip":"203.0.113.7","x_forwarded_for":"","user_agent":"curl","path":"/main","status":0,"service":"zellij@file","sso_user":"","sso_email":""}' \
  https://n8n.example.com/webhook/secure-zellij
```

For an end-to-end check, open a terminal session in the browser. Traefik logs the WebSocket when it closes, so the alert arrives after you close the tab.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Every `just` recipe fails with `failed to load environment file` | An unquoted template in `.env`. Wrap the value in single quotes. |
| Slack returns 400 `no_text`, or ntfy shows raw JSON | The template didn't render and the default payload went out. Check the startup warning in `just logs-alerter`, the doubled braces and the field names. |
| Bans arrive in the right shape, connections don't | The template uses a ban-only field such as `{jail}`. Use only the common fields. |
| Some alerts arrive in the default shape | A client-controlled value (`path`, `user_agent`, `x_forwarded_for`) contained a quote or backslash. Leave those out of the template. |
| Nothing arrives at all | The URL isn't in `WEBHOOK_URLS` of the running alerter (`just up` after editing `.env`), or the receiver isn't reachable from the host. `just logs-alerter` shows the HTTP error. |
