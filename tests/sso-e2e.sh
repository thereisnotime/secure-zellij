#!/usr/bin/env bash
# End-to-end check of the SSO wiring with throwaway podman containers:
# mock OIDC IdP + oauth2-proxy + Traefik (this repo's config and entrypoint) + a
# dummy backend standing in for zellij. Uses host networking on high ports and
# never touches the real stack. Everything is removed on exit.
#
#   tests/sso-e2e.sh                 # all scenarios
#   COMPOSE="docker compose" tests/sso-e2e.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=${COMPOSE:-podman compose}
IDP_IMAGE=ghcr.io/navikt/mock-oauth2-server:7.0.1
BACKEND_IMAGE=docker.io/library/python:3.15-slim
HTTPS=18443 IDP=18090 O2P=14180 BACKEND=18082
NAMES=(sz-e2e-idp sz-e2e-o2p sz-e2e-traefik sz-e2e-backend sz-e2e-bad)

WORK=$(mktemp -d)
cleanup() {
    podman rm -f -v --ignore "${NAMES[@]}" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

fails=0
pass() { echo "  ok   $*"; }
fail() { echo "  FAIL $*"; fails=$((fails + 1)); }
expect() { # expect <name> <actual> <pattern>
    if [[ $2 =~ $3 ]]; then pass "$1"; else fail "$1 (got: ${2:0:160})"; fi
}

# Traefik config copy with the backend ports moved out of the way
cp -r "$REPO/traefik" "$WORK/traefik"
sed -i "s/127.0.0.1:8082/127.0.0.1:${BACKEND}/; s/127.0.0.1:8083/127.0.0.1:18083/" "$WORK/traefik/dynamic/routers.yml"
sed -i "s/127.0.0.1:8099/127.0.0.1:18099/; s|filePath: /var/log/traefik/access.log|filePath: /tmp/access.log|" "$WORK/traefik/traefik.yml"

cat >"$WORK/idp.json" <<'JSON'
{"interactiveLogin": false, "httpServer": "NettyWrapper",
 "tokenCallbacks": [{"issuerId": "default", "tokenExpiry": 300,
   "requestMappings": [{"requestParam": "client_id", "match": "*",
     "claims": {"sub": "alice", "aud": ["secure-zellij"], "email": "alice@example.com",
                "email_verified": true, "preferred_username": "alice", "groups": ["zellij", "staff"]}}]}]}
JSON

# Echo backend: shows which identity headers reached "zellij"
cat >"$WORK/backend.py" <<'PY'
import http.server as h
import sys


class H(h.BaseHTTPRequestHandler):
    def do_GET(self):
        b = f"BACKEND user={self.headers.get('X-Auth-Request-User')} groups={self.headers.get('X-Auth-Request-Groups')}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


h.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY

podman run -d --name sz-e2e-backend --network host -v "$WORK/backend.py:/backend.py:ro,Z" \
    "$BACKEND_IMAGE" python /backend.py "$BACKEND" >/dev/null
podman run -d --name sz-e2e-idp --network host -e SERVER_PORT=$IDP -e JSON_CONFIG="$(cat "$WORK/idp.json")" \
    "$IDP_IMAGE" >/dev/null
for _ in $(seq 60); do curl -fs "http://127.0.0.1:$IDP/default/.well-known/openid-configuration" >/dev/null && break; sleep 1; done

C=(curl -ks --resolve "zellij.test:$HTTPS:127.0.0.1" --max-time 10)
URL="https://zellij.test:$HTTPS"

# start_stack <SSO_MODE> <SSO_ALLOWED_GROUPS> [extra env lines]
# Containers get exactly the environment compose.yaml resolves to.
start_stack() {
    podman rm -f -v --ignore sz-e2e-o2p sz-e2e-traefik >/dev/null
    {
        echo "DOMAIN=zellij.test"
        echo "HTTP_PORT=18880"
        echo "HTTPS_PORT=$HTTPS"
        echo "SSO_MODE=$1"
        echo "SSO_PROXY_PORT=$O2P"
        echo "SSO_OIDC_ISSUER_URL=http://127.0.0.1:$IDP/default"
        echo "SSO_CLIENT_ID=secure-zellij"
        echo "SSO_CLIENT_SECRET=dummy"
        echo "SSO_COOKIE_SECRET=$(openssl rand -base64 32 | tr -- '+/' '-_')"
        echo "SSO_ALLOWED_GROUPS=$2"
        printf '%b' "${3:-}"
    } >"$WORK/test.env"
    (cd "$REPO" && $COMPOSE --env-file "$WORK/test.env" --profile sso config --format json 2>/dev/null) >"$WORK/cfg.json"
    local o2p_env traefik_env
    mapfile -t o2p_env < <(python3 -I - oauth2-proxy "$WORK/cfg.json" <<'PY'
import json, sys
svc = json.load(open(sys.argv[2]))["services"][sys.argv[1]]
for k, v in svc["environment"].items():
    print("-e"); print(f"{k}={v or ''}")
PY
)
    mapfile -t traefik_env < <(python3 -I - traefik "$WORK/cfg.json" <<'PY'
import json, sys
svc = json.load(open(sys.argv[2]))["services"][sys.argv[1]]
for k, v in svc["environment"].items():
    print("-e"); print(f"{k}={v or ''}")
PY
)
    image() { python3 -I -c 'import json,sys; print(json.load(open(sys.argv[2]))["services"][sys.argv[1]]["image"])' "$1" "$WORK/cfg.json"; }

    # oauth2-proxy also serves as the stand-in IdP endpoint for forward-auth mode
    [ "$1" = off ] || podman run -d --name sz-e2e-o2p --network host "${o2p_env[@]}" "$(image oauth2-proxy)" >/dev/null
    podman run -d --name sz-e2e-traefik --network host "${traefik_env[@]}" \
        -v "$WORK/traefik/traefik.yml:/etc/traefik/traefik.yml:ro,Z" \
        -v "$WORK/traefik/dynamic:/etc/traefik/dynamic:ro,Z" \
        -v "$WORK/traefik/entrypoint.sh:/entrypoint.sh:ro,Z" \
        -v "$WORK/traefik/sso:/etc/traefik/sso:ro,Z" \
        --entrypoint /entrypoint.sh "$(image traefik)" >/dev/null
    for _ in $(seq 30); do "${C[@]}" -o /dev/null "$URL/" && break; sleep 1; done
    sleep 2
    rm -f "$WORK/jar"
}

anon() { "${C[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' "$URL/my-session"; }
anon_ws() {
    "${C[@]}" -o /dev/null -w '%{http_code}' -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
        -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "$URL/ws/terminal" # gitleaks:allow (RFC 6455 sample nonce)
}
login() { "${C[@]}" -L -c "$WORK/jar" -b "$WORK/jar" -o "$WORK/body" -w '%{http_code} %{url_effective}' "$URL/my-session"; }
again() { "${C[@]}" -b "$WORK/jar" -H 'X_Auth_Request_User: mallory' "$URL/other"; }

echo "== SSO_MODE=oauth2-proxy, allowed group present"
start_stack oauth2-proxy zellij
expect "anonymous request redirects to IdP with PKCE" "$(anon)" "^302 http://127.0.0.1:$IDP/default/authorize.*code_challenge_method=S256"
expect "anonymous websocket upgrade redirects (no 4xx for fail2ban)" "$(anon_ws)" "^302$"
expect "spoofed X-Forwarded-Host can't steer the redirect" \
    "$("${C[@]}" -o /dev/null -w '%{redirect_url}' -H 'X-Forwarded-Host: evil.example' "$URL/my-session")" "redirect_uri=https%3A%2F%2Fzellij.test%3A$HTTPS%2F"
expect "login flow lands on the original path" "$(login)" "^200 $URL/my-session$"
expect "backend sees SSO identity" "$(cat "$WORK/body")" "user=alice groups=zellij,staff"
expect "session cookie reused, aliased header ignored" "$(again)" "user=alice "
expect "Traefik routers carry the sso middleware" \
    "$(podman exec sz-e2e-traefik grep -c -- '- sso' /etc/traefik/dynamic-live/routers.yml)" "^2$"
expect "Cookie header redacted in access log" \
    "$(podman exec sz-e2e-traefik sh -c 'grep -o "\"request_Cookie\":\"[^\"]*\"" /tmp/access.log | sort -u')" '^"request_Cookie":"REDACTED"$'

echo "== SSO_MODE=oauth2-proxy, user not in allowed group"
start_stack oauth2-proxy admins
expect "login is refused" "$(login)" "^403 "

echo "== SSO_MODE=forward-auth with callback route"
start_stack forward-auth zellij \
    "SSO_FORWARD_AUTH_URL=http://127.0.0.1:$O2P/\nSSO_CALLBACK_PREFIX=/oauth2/\nSSO_CALLBACK_URL=http://127.0.0.1:$O2P\n"
expect "anonymous request redirects to IdP" "$(anon)" "^302 http://127.0.0.1:$IDP/default/authorize"
expect "login flow through the callback route" "$(login)" "^200 $URL/my-session$"
expect "callback route generated" "$(podman exec sz-e2e-traefik ls /etc/traefik/dynamic-live)" "sso-callback.yml"

echo "== SSO_MODE=off"
start_stack off ""
expect "no SSO, backend reachable directly" "$(anon)" "^200 "
expect "no sso middleware in routers" \
    "$(podman exec sz-e2e-traefik grep -c -- '- sso' /etc/traefik/dynamic-live/routers.yml || true)" "^0$"

echo "== misconfiguration"
out=$(podman run --rm --name sz-e2e-bad -e DOMAIN=x -e SSO_MODE=forward-auth \
    -v "$WORK/traefik/entrypoint.sh:/entrypoint.sh:ro,Z" -v "$WORK/traefik/dynamic:/etc/traefik/dynamic:ro,Z" \
    -v "$WORK/traefik/sso:/etc/traefik/sso:ro,Z" --entrypoint /entrypoint.sh \
    "$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["services"]["traefik"]["image"])' "$WORK/cfg.json")" 2>&1 || true)
expect "forward-auth without URL fails fast" "$out" "needs SSO_FORWARD_AUTH_URL"

echo
if [ "$fails" -eq 0 ]; then echo "all SSO checks passed"; else echo "$fails SSO check(s) failed"; exit 1; fi
