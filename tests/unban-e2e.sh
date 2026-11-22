#!/usr/bin/env bash
# End-to-end check of the dashboard unban button with throwaway podman
# containers: real fail2ban (dummy action, so no firewall changes), the
# fail2ban-unban helper locked down like in compose.yaml, and the alerter built
# from this repo. Never touches the real stack. Everything is removed on exit.
#
#   tests/unban-e2e.sh
#   COMPOSE="docker compose" tests/unban-e2e.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=${COMPOSE:-podman compose}
STATS=18784
# Throwaway basic-auth login for the test alerter
STATS_USER="admin"
STATS_PASS=$(openssl rand -hex 12)
ALERTER_IMAGE=localhost/sz-e2e-alerter:latest
NAMES=(sz-e2e-f2b sz-e2e-unban sz-e2e-alerter)
VOLUMES=(sz-e2e-f2b-run sz-e2e-unban-req)

WORK=$(mktemp -d)
cleanup() {
    podman rm -f -v --ignore "${NAMES[@]}" >/dev/null 2>&1 || true
    podman volume rm -f "${VOLUMES[@]}" >/dev/null 2>&1 || true
    podman rmi -f "$ALERTER_IMAGE" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

fails=0
pass() { echo "  ok   $*"; }
fail() { echo "  FAIL $*"; fails=$((fails + 1)); }
expect() { # expect <name> <actual> <pattern>
    if [[ $2 =~ $3 ]]; then pass "$1"; else fail "$1 (got: ${2:0:200})"; fi
}

# Same fail2ban image as the stack
(cd "$REPO" && DOMAIN=x $COMPOSE --env-file /dev/null config --format json 2>/dev/null) >"$WORK/cfg.json"
F2B_IMAGE=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["services"]["fail2ban"]["image"])' "$WORK/cfg.json")

mkdir -p "$WORK/log" "$WORK/db" "$WORK/jail.d" "$WORK/data"
touch "$WORK/log/access.log"
sed 's/^action .*/action    = dummy/' "$REPO/fail2ban/jail.d/traefik-zellij.conf" >"$WORK/jail.d/traefik-zellij.conf"
for v in "${VOLUMES[@]}"; do
    podman volume create --opt type=tmpfs --opt device=tmpfs --opt o=size=1m,mode=0700 "$v" >/dev/null
done

podman build -q -t "$ALERTER_IMAGE" "$REPO/alerter" >/dev/null
podman run -d --name sz-e2e-f2b \
    -v "$WORK/log:/var/log/traefik:ro,z" -v "$REPO/fail2ban/filter.d:/data/filter.d:ro,z" \
    -v "$WORK/jail.d:/data/jail.d:ro,z" -v "$WORK/db:/data/db:z" -v sz-e2e-f2b-run:/var/run/fail2ban \
    "$F2B_IMAGE" >/dev/null
# Locked down exactly like the fail2ban-unban service
podman run -d --name sz-e2e-unban --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --entrypoint python3 -e UNBAN_JAILS=traefik-zellij \
    -v "$REPO/fail2ban/unban-helper.py:/unban-helper.py:ro,z" \
    -v sz-e2e-f2b-run:/var/run/fail2ban -v sz-e2e-unban-req:/requests \
    "$F2B_IMAGE" -I /unban-helper.py >/dev/null
podman run -d --name sz-e2e-alerter --network host \
    -e STATS_PORT=$STATS -e STATS_DB=/data/stats.db -e "STATS_USERNAME=$STATS_USER" -e "STATS_PASSWORD=$STATS_PASS" \
    -e SSO_MODE=oauth2-proxy -e F2B_DB=/fail2ban/fail2ban.sqlite3 -e F2B_POLL_SECONDS=1 -e UNBAN_DIR=/unban \
    -v "$WORK/db:/fail2ban:ro,z" -v "$WORK/data:/data:z" -v "$WORK/log:/var/log/traefik:ro,z" \
    -v sz-e2e-unban-req:/unban "$ALERTER_IMAGE" >/dev/null

for _ in $(seq 60); do
    podman exec sz-e2e-f2b fail2ban-client ping >/dev/null 2>&1 &&
        curl -fs -o /dev/null -u "$STATS_USER:$STATS_PASS" "http://127.0.0.1:$STATS/api" && break
    sleep 1
done

# 12 failed logins by alice from one IP -> ban
for _ in $(seq 12); do
    printf '{"ClientAddr":"203.0.113.9:5555","DownstreamStatus":401,"RequestPath":"/command/login","request_X-Auth-Request-Preferred-Username":"alice","time":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$WORK/log/access.log"
done
for _ in $(seq 20); do
    [ -n "$(podman exec sz-e2e-f2b fail2ban-client get traefik-zellij banip)" ] && break
    sleep 1
done

bans() {
    curl -fs -u "$STATS_USER:$STATS_PASS" "http://127.0.0.1:$STATS/api" | python3 -I -c '
import json, sys
b = json.load(sys.stdin)["bans"]
print(b["active"], b["unban"], [(x["ip"], x["active"], x["sso_user"], x["unbanned_by"]) for x in b["recent"]])'
}
# unban <ip> <jail>; ORIGIN, XRW and AUTH override what the dashboard page sends
unban() {
    curl -s -u "${AUTH:-$STATS_USER:$STATS_PASS}" -H 'Content-Type: application/json' \
        -H "X-Requested-With: ${XRW-secure-zellij}" -H "Origin: ${ORIGIN:-http://127.0.0.1:$STATS}" \
        -w ' %{http_code}' -d "{\"ip\":\"$1\",\"jail\":\"$2\"}" "http://127.0.0.1:$STATS/api/unban"
}

echo "== ban shows up"
expect "dashboard lists the active ban with its SSO user" "$(bans)" "^1 True \[\('203.0.113.9', True, 'alice', ''\)\]$"
for _ in $(seq 10); do # the alerter polls fail2ban's DB every second
    podman logs sz-e2e-alerter 2>&1 | grep -q 'Ban: 203.0.113.9' && break
    sleep 1
done
expect "alerter announced the ban" "$(podman logs sz-e2e-alerter 2>&1)" "Ban: 203.0.113.9 \(alice\) in traefik-zellij"

echo "== guards"
expect "cross-site request refused" "$(ORIGIN=https://evil.example unban 203.0.113.9 traefik-zellij)" "cross-site request refused.* 403$"
expect "missing CSRF header refused" "$(XRW='' unban 203.0.113.9 traefik-zellij)" " 403$"
expect "wrong password refused" "$(AUTH="$STATS_USER:wrong" unban 203.0.113.9 traefik-zellij)" " 401$"
expect "jail outside UNBAN_JAILS refused by the helper" "$(unban 203.0.113.9 sshd)" "not in UNBAN_JAILS.* 200$"
expect "still banned after refused attempts" "$(podman exec sz-e2e-f2b fail2ban-client get traefik-zellij banip)" "203.0.113.9"

echo "== unban"
expect "unban succeeds" "$(unban 203.0.113.9 traefik-zellij)" '"ok":true.* 200$'
expect "fail2ban no longer bans it" "$(podman exec sz-e2e-f2b fail2ban-client get traefik-zellij banip)" "^$"
expect "dashboard keeps the row as unbanned by admin" "$(bans)" "^0 True \[\('203.0.113.9', False, 'alice', 'admin'\)\]$"
expect "second unban reports not banned" "$(unban 203.0.113.9 traefik-zellij)" "isn't banned.* 200$"
expect "helper has no network" "$(podman inspect sz-e2e-unban --format '{{.HostConfig.NetworkMode}}')" "^none$"
expect "request drop box is empty again" "$(podman exec sz-e2e-alerter ls -A /unban)" "^$"

echo
if [ "$fails" -eq 0 ]; then echo "all unban checks passed"; else echo "$fails unban check(s) failed"; exit 1; fi
