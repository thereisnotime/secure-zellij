#!/usr/bin/env bash
# End-to-end check that a fail2ban ban really blocks the client: real Traefik
# and fail2ban (the repo's filter and jail, nftables action) in an edge
# container's pasta network namespace, laid out like compose.yaml, and a dummy
# backend that answers 401 on /bad. Throwaway sz-ban-* containers on spare
# ports; the real stack is not touched. Needs pasta (podman's rootless network).
#
#   tests/ban-e2e.sh
#   COMPOSE="docker compose" tests/ban-e2e.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=${COMPOSE:-podman compose}
# shellcheck source=tests/lib.sh
. "$REPO/tests/lib.sh"
BACKEND_IMAGE=docker.io/library/python:3.15-slim
HTTP=18881 HTTPS=18444 BACKEND=18085
NAMES=(sz-ban-edge sz-ban-traefik sz-ban-f2b sz-ban-backend)

WORK=$(mktemp -d)
cleanup() {
    # containers that joined edge's namespace first, podman won't remove edge before them
    podman rm -f -v --ignore sz-ban-traefik sz-ban-f2b sz-ban-backend >/dev/null 2>&1 || true
    podman rm -f -v --ignore "${NAMES[@]}" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

fails=0
pass() { echo "  ok   $*"; }
fail() { echo "  FAIL $*"; fails=$((fails + 1)); }
expect() { # expect <name> <actual> <pattern>
    if [[ $2 =~ $3 ]]; then pass "$1"; else fail "$1 (got: ${2:0:200})"; fi
}

command -v pasta >/dev/null || { echo "pasta is not installed (package passt)" >&2; exit 1; }

echo DOMAIN=zellij.test >"$WORK/cfg.env"
compose_json --env-file "$WORK/cfg.env" >"$WORK/cfg.json"
image() { python3 -I -c 'import json,sys; print(json.load(open(sys.argv[2]))["services"][sys.argv[1]]["image"])' "$1" "$WORK/cfg.json"; }

cp -r "$REPO/traefik" "$WORK/traefik"
mkdir -p "$WORK/logs" "$WORK/jail.d" "$WORK/db"
touch "$WORK/logs/access.log"
sed -i "s/127.0.0.1:8082/127.0.0.1:$BACKEND/; s/127.0.0.1:8083/127.0.0.1:18086/; /- rate-limit/d" "$WORK/traefik/dynamic/routers.yml"
sed -i "s/127.0.0.1:8099/127.0.0.1:18098/; s|filePath: /var/log/traefik/access.log|filePath: /logs/access.log|; s/bufferingSize: 100/bufferingSize: 0/" "$WORK/traefik/traefik.yml"
# The shipped jail and its nftables action, with a lower threshold, and the
# test client (local, so normally ignored) allowed to be banned
sed 's/^maxretry .*/maxretry  = 5/' "$REPO/fail2ban/jail.d/traefik-zellij.conf" >"$WORK/jail.d/traefik-zellij.conf"
printf 'ignoreself = false\nignoreip =\n' >>"$WORK/jail.d/traefik-zellij.conf"

cat >"$WORK/backend.py" <<'PY'
import http.server as h, sys
class H(h.BaseHTTPRequestHandler):
    def do_GET(self):
        code = 401 if self.path.startswith("/bad") else 200
        self.send_response(code); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
h.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY

podman run -d --name sz-ban-backend --network host -v "$WORK/backend.py:/b.py:ro,z" \
    "$BACKEND_IMAGE" python /b.py "$BACKEND" >/dev/null
# Same shape as the edge service: pasta, forwarded host-loopback port, published ports
podman run -d --name sz-ban-edge --network "pasta:-T,$BACKEND" -p "$HTTP:$HTTP" -p "$HTTPS:$HTTPS" \
    --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --entrypoint sh "$(image edge)" -c "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait \$!; done" >/dev/null
podman run -d --name sz-ban-traefik --network container:sz-ban-edge \
    -e DOMAIN=zellij.test -e HTTP_PORT=$HTTP -e HTTPS_PORT=$HTTPS \
    -v "$WORK/traefik/traefik.yml:/etc/traefik/traefik.yml:ro,z" -v "$WORK/traefik/dynamic:/etc/traefik/dynamic:ro,z" \
    -v "$WORK/traefik/entrypoint.sh:/entrypoint.sh:ro,z" -v "$WORK/traefik/sso:/etc/traefik/sso:ro,z" \
    -v "$WORK/logs:/logs:z" --entrypoint /entrypoint.sh "$(image traefik)" >/dev/null
podman run -d --name sz-ban-f2b --network container:sz-ban-edge --cap-add NET_ADMIN --cap-add NET_RAW \
    -v "$WORK/logs:/var/log/traefik:ro,z" -v "$REPO/fail2ban/filter.d:/data/filter.d:ro,z" \
    -v "$WORK/jail.d:/data/jail.d:ro,z" -v "$WORK/db:/data/db:z" "$(image fail2ban)" >/dev/null 2>&1

C=(curl -ks -o /dev/null -w '%{http_code}' --max-time 4 --resolve "zellij.test:$HTTPS:127.0.0.1")
URL="https://zellij.test:$HTTPS"
for _ in $(seq 30); do [ "$("${C[@]}" "$URL/ok")" = 200 ] && break; sleep 1; done
for _ in $(seq 30); do podman exec sz-ban-f2b fail2ban-client ping >/dev/null 2>&1 && break; sleep 1; done

echo "== before the ban"
expect "Traefik answers through the edge namespace" "$("${C[@]}" "$URL/ok")" "^200$"
expect "backend on the host's loopback reached via pasta -T" "$("${C[@]}" "$URL/ok")" "^200$"

echo "== 4xx from the client"
for _ in $(seq 6); do "${C[@]}" "$URL/bad" >/dev/null || true; done
banned=""
for _ in $(seq 20); do
    banned=$(podman exec sz-ban-f2b fail2ban-client get traefik-zellij banip 2>/dev/null || true)
    [ -n "$banned" ] && break
    sleep 1
done
expect "fail2ban banned the client" "$banned" "."
expect "nftables rule in the shared namespace" "$(podman exec sz-ban-f2b nft list ruleset 2>&1)" "addr-set-traefik-zellij"
expect "banned client is blocked (connection times out)" "$("${C[@]}" "$URL/ok" || true)" "^000$"

echo "== Traefik restart"
podman restart -t 2 sz-ban-traefik >/dev/null
sleep 3
expect "still blocked after Traefik restarts (namespace belongs to edge)" "$("${C[@]}" "$URL/ok" || true)" "^000$"

echo "== unban"
podman exec sz-ban-f2b fail2ban-client set traefik-zellij unbanip "$banned" >/dev/null
ok=""
for _ in $(seq 15); do ok=$("${C[@]}" "$URL/ok" || true); [ "$ok" = 200 ] && break; sleep 1; done
expect "client gets through again after unban" "$ok" "^200$"

echo
if [ "$fails" -eq 0 ]; then echo "all ban checks passed"; else echo "$fails ban check(s) failed"; exit 1; fi
