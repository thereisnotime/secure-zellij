# Integrations

Practical examples for connecting secure-zellij alerts to common third-party services via the generic webhook mechanism. See [Alerting](alerting.md) for the full payload schema and template syntax.

## n8n

Use an **HTTP Webhook** node as the trigger. The default payload fields map directly to n8n's `$json` object — no body template needed.

```env
WEBHOOK_URLS=https://n8n.example.com/webhook/your-path
```

In your workflow, reference fields as `{{ $json.client_ip }}`, `{{ $json.timestamp }}`, `{{ $json.path }}`, etc. Example workflow trigger node (JSON snippet):

```json
{
  "nodes": [{
    "name": "Zellij Alert",
    "type": "n8n-nodes-base.webhook",
    "parameters": { "path": "your-path", "responseMode": "onReceived" }
  }]
}
```

## Slack

Create an [Incoming Webhook](https://api.slack.com/messaging/webhooks) app in your Slack workspace and copy its URL. Slack expects `{"text": "..."}`, so set a body template:

```env
WEBHOOK_URLS=https://hooks.slack.com/services/T000/B000/xxxx
WEBHOOK_BODY_TEMPLATE={"text": ":electric_plug: Zellij connection\nIP: {client_ip}\nPath: {path}\nTime: {timestamp}"}
```

## Ntfy.sh

Push notifications via [ntfy.sh](https://ntfy.sh). Point `WEBHOOK_URLS` at your topic URL and use the ntfy payload format:

```env
WEBHOOK_URLS=https://ntfy.sh/your-topic
WEBHOOK_BODY_TEMPLATE={"topic": "your-topic", "message": "Zellij connection from {client_ip} on {path} at {timestamp}"}
```

For self-hosted ntfy, replace the host: `https://ntfy.example.com/your-topic`.

## Home Assistant

Use a [webhook trigger](https://www.home-assistant.io/docs/automation/trigger/#webhook-trigger) in an automation, or a REST command.

**Webhook automation trigger:**

```env
WEBHOOK_URLS=https://homeassistant.example.com/api/webhook/zellij-alert
```

No body template needed — Home Assistant exposes the raw JSON as `trigger.json` in the automation. Access fields via `{{ trigger.json.client_ip }}` in templates.

**REST command alternative:**

```yaml
# configuration.yaml
rest_command:
  zellij_alert:
    url: "https://homeassistant.example.com/api/webhook/zellij-alert"
    method: POST
    content_type: "application/json"
```
