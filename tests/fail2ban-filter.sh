#!/usr/bin/env bash
# Runs fail2ban/filter.d/traefik-zellij.conf over tests/fixtures/traefik-access.log
# with fail2ban-regex from the stack's own fail2ban image, and checks it bans
# exactly the IPs in traefik-access.expected: 4xx only (not 0/101/3xx/5xx),
# IPv4 and IPv6, and no IP smuggled in through header or path values.
# A broken filter fails silently in production (nothing gets banned), hence this.
#
#   tests/fail2ban-filter.sh
#   COMPOSE="docker compose" tests/fail2ban-filter.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=${COMPOSE:-podman compose}
IMAGE=$(cd "$REPO" && DOMAIN=x $COMPOSE --env-file /dev/null config --format json 2>/dev/null |
    python3 -I -c 'import json,sys; print(json.load(sys.stdin)["services"]["fail2ban"]["image"])')

got=$(podman run --rm --name sz-test-f2b-filter \
    -v "$REPO/fail2ban/filter.d:/f:ro,z" -v "$REPO/tests/fixtures:/t:ro,z" \
    --entrypoint fail2ban-regex "$IMAGE" -o ip /t/traefik-access.log /f/traefik-zellij.conf 2>/dev/null | sort)
want=$(sort "$REPO/tests/fixtures/traefik-access.expected")

if [ "$got" = "$want" ]; then
    echo "fail2ban filter: ok ($(echo "$want" | wc -l) bans expected and matched)"
else
    echo "fail2ban filter: MISMATCH"
    diff <(echo "$want") <(echo "$got") | sed 's/^</  missing:/; s/^>/  unexpected:/' | grep -v '^[0-9]'
    exit 1
fi
