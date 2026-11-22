#!/usr/bin/env bash
# Benchmarks behind docs/performance.md, in throwaway sz-bench-* containers on
# spare ports. Never touches the real stack; everything is removed on exit.
#
#   tests/bench.sh            # both
#   tests/bench.sh stack      # CPU/memory per container: idle, 20 req/s, burst
#   tests/bench.sh filter     # fail2ban-regex over a 50k-line log, checks matches
#   COMPOSE="docker compose" tests/bench.sh
#
# CPU comes from each container's cgroup (cpu.stat usage_usec over the window),
# memory is the peak of memory.current minus inactive_file, like podman stats.
# The edge container's pasta process (rootless networking, on the host) is
# measured from /proc. Laid out like compose.yaml: Traefik and fail2ban share
# edge's network namespace. Needs pasta.
# Needs rootless podman with cgroups v2, curl, python3 and openssl.
set -euo pipefail

WHAT=${1:-all}
REPO=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=${COMPOSE:-podman compose}
# shellcheck source=tests/lib.sh
. "$REPO/tests/lib.sh"
NAMES=(sz-bench-traefik sz-bench-f2b sz-bench-edge sz-bench-unban sz-bench-alerter sz-bench-o2p sz-bench-idp sz-bench-backend sz-bench-regex)
VOLUMES=(sz-bench-run sz-bench-req)
ALERTER_IMAGE=localhost/sz-bench-alerter:latest
BACKEND_IMAGE=docker.io/library/python:3.15-slim
IDP_IMAGE=ghcr.io/navikt/mock-oauth2-server:7.0.1
HTTPS=18443 BACKEND=18082 STATS=18083 IDP=18090 O2P=14180

WORK=$(mktemp -d)
cleanup() {
    # containers in edge's network namespace first, podman won't remove edge before them
    podman rm -f -v --ignore sz-bench-traefik sz-bench-f2b >/dev/null 2>&1 || true
    podman rm -f -v --ignore "${NAMES[@]}" >/dev/null 2>&1 || true
    podman volume rm -f "${VOLUMES[@]}" >/dev/null 2>&1 || true
    podman rmi -f "$ALERTER_IMAGE" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

echo DOMAIN=zellij.test >"$WORK/cfg.env"
compose_json --env-file "$WORK/cfg.env" --profile sso >"$WORK/cfg.json"
image() { python3 -I -c 'import json,sys; print(json.load(open(sys.argv[2]))["services"][sys.argv[1]]["image"])' "$1" "$WORK/cfg.json"; }
F2B_IMAGE=$(image fail2ban)

# ── stack ─────────────────────────────────────────────────────────────────────

# sample <label> <seconds>: CPU share of one core and peak memory per container,
# plus edge's pasta process, which runs on the host outside any container cgroup
sample() {
    local label=$1 secs=$2 n
    local -a paths=()
    for n in "${STACK[@]}"; do
        paths+=("$n=/sys/fs/cgroup$(podman inspect "$n" --format '{{.State.CgroupPath}}')")
    done
    paths+=("pasta=pid:$PASTA_PID")
    python3 -I - "$label" "$secs" "${paths[@]}" <<'PY'
import os, sys, time
label, secs, items = sys.argv[1], int(sys.argv[2]), [a.split("=", 1) for a in sys.argv[3:]]
TICK = os.sysconf("SC_CLK_TCK")
def cpu(p):  # microseconds of CPU used so far
    if p.startswith("pid:"):
        f = open(f"/proc/{p[4:]}/stat").read().rsplit(")", 1)[1].split()
        return (int(f[11]) + int(f[12])) * 1_000_000 // TICK
    return int(next(l.split()[1] for l in open(p + "/cpu.stat") if l.startswith("usage_usec")))
def mem(p):  # MiB: RSS for a process, like podman stats for a container
    if p.startswith("pid:"):
        kb = next(l.split()[1] for l in open(f"/proc/{p[4:]}/status") if l.startswith("VmRSS"))
        return int(kb) / 1024
    st = dict(l.split() for l in open(p + "/memory.stat"))
    return (int(open(p + "/memory.current").read()) - int(st["inactive_file"])) / 2**20
c0 = {n: cpu(p) for n, p in items}
peak = {n: 0.0 for n, _ in items}
t0 = time.time()
while time.time() - t0 < secs:
    for n, p in items:
        peak[n] = max(peak[n], mem(p))
    time.sleep(0.5)
dt = time.time() - t0
print(f"\n{label}\n")
print("| Container | CPU (% of a core) | Memory peak |\n|---|---|---|")
tc = tm = 0.0
for n, p in items:
    c = (cpu(p) - c0[n]) / 1e6 / dt * 100
    tc += c
    tm += peak[n]
    print(f"| {n.removeprefix('sz-bench-')} | {c:.2f}% | {peak[n]:.1f} MiB |")
print(f"| **total** | **{tc:.2f}%** | **{tm:.1f} MiB** |")
PY
}

bench_stack() {
    echo "## Stack"
    cp -r "$REPO/traefik" "$WORK/traefik"
    mkdir -p "$WORK/logs" "$WORK/f2b-db" "$WORK/data" "$WORK/jail.d"
    touch "$WORK/logs/access.log"
    sed -i "s/127.0.0.1:8082/127.0.0.1:$BACKEND/; s/127.0.0.1:8083/127.0.0.1:$STATS/" "$WORK/traefik/dynamic/routers.yml"
    sed -i "s/127.0.0.1:8099/127.0.0.1:18099/; s|filePath: /var/log/traefik/access.log|filePath: /logs/access.log|" "$WORK/traefik/traefik.yml"
    # the shipped jail, nftables action included (only 200s here, so no bans)
    cp "$REPO/fail2ban/jail.d/traefik-zellij.conf" "$WORK/jail.d/"
    for v in "${VOLUMES[@]}"; do
        podman volume create --opt type=tmpfs --opt device=tmpfs --opt o=size=1m,mode=0700 "$v" >/dev/null
    done
    podman build -q -t "$ALERTER_IMAGE" "$REPO/alerter" >/dev/null 2>&1

    cat >"$WORK/backend.py" <<'PY'
import http.server as h, sys
class H(h.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_GET(self):
        b = b"x" * 2048
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
h.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
    podman run -d --name sz-bench-backend --network host -v "$WORK/backend.py:/b.py:ro,Z" \
        "$BACKEND_IMAGE" python /b.py "$BACKEND" >/dev/null
    # laid out like compose.yaml: edge owns a pasta namespace and the ports,
    # Traefik and fail2ban join it
    podman run -d --name sz-bench-edge --network "pasta:-T,$BACKEND,-T,$STATS,-T,$O2P" \
        -p 18880:18880 -p "$HTTPS:$HTTPS" --read-only --cap-drop ALL --security-opt no-new-privileges:true \
        --entrypoint sh "$(image edge)" -c "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait \$!; done" >/dev/null
    PASTA_PID=$(pgrep -f "pasta .*-t $HTTPS-$HTTPS:" | head -1)
    [ -n "$PASTA_PID" ] || { echo "can't find edge's pasta process" >&2; exit 1; }
    podman run -d --name sz-bench-traefik --network container:sz-bench-edge -e DOMAIN=zellij.test -e HTTP_PORT=18880 -e HTTPS_PORT=$HTTPS \
        -v "$WORK/traefik/traefik.yml:/etc/traefik/traefik.yml:ro,Z" -v "$WORK/traefik/dynamic:/etc/traefik/dynamic:Z" \
        -v "$WORK/traefik/entrypoint.sh:/entrypoint.sh:ro,Z" -v "$WORK/traefik/sso:/etc/traefik/sso:ro,Z" \
        -v "$WORK/logs:/logs:z" --entrypoint /entrypoint.sh "$(image traefik)" >/dev/null
    podman run -d --name sz-bench-f2b --network container:sz-bench-edge --cap-add NET_ADMIN --cap-add NET_RAW \
        -v "$WORK/logs:/var/log/traefik:ro,z" \
        -v "$REPO/fail2ban/filter.d:/data/filter.d:ro,z" -v "$WORK/jail.d:/data/jail.d:ro,z" \
        -v "$WORK/f2b-db:/data/db:z" -v sz-bench-run:/var/run/fail2ban:z "$F2B_IMAGE" >/dev/null 2>&1
    podman run -d --name sz-bench-unban --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges:true --entrypoint python3 \
        -v "$REPO/fail2ban/unban-helper.py:/unban-helper.py:ro,z" -v sz-bench-run:/var/run/fail2ban:z \
        -v sz-bench-req:/requests:z "$F2B_IMAGE" -I /unban-helper.py >/dev/null
    podman run -d --name sz-bench-alerter --network host -e STATS_PORT=$STATS -e STATS_DB=/data/stats.db \
        -e ACCESS_LOG=/logs/access.log -e F2B_DB=/fail2ban/fail2ban.sqlite3 -e UNBAN_DIR=/unban \
        -v "$WORK/logs:/logs:ro,z" -v "$WORK/f2b-db:/fail2ban:ro,z" -v "$WORK/data:/data:Z" \
        -v sz-bench-req:/unban:z "$ALERTER_IMAGE" >/dev/null
    # optional SSO: oauth2-proxy against a mock IdP (the IdP isn't counted)
    podman run -d --name sz-bench-idp --network host -e SERVER_PORT=$IDP "$IDP_IMAGE" >/dev/null
    for _ in $(seq 60); do
        curl -fs -o /dev/null "http://127.0.0.1:$IDP/default/.well-known/openid-configuration" && break
        sleep 1
    done
    podman run -d --name sz-bench-o2p --network host -e OAUTH2_PROXY_HTTP_ADDRESS=127.0.0.1:$O2P \
        -e OAUTH2_PROXY_PROVIDER=oidc -e OAUTH2_PROXY_OIDC_ISSUER_URL=http://127.0.0.1:$IDP/default \
        -e OAUTH2_PROXY_CLIENT_ID=bench -e OAUTH2_PROXY_CLIENT_SECRET=bench \
        -e OAUTH2_PROXY_COOKIE_SECRET="$(openssl rand -base64 32 | tr -- '+/' '-_')" \
        -e OAUTH2_PROXY_EMAIL_DOMAINS='*' -e OAUTH2_PROXY_UPSTREAMS=static://202 "$(image oauth2-proxy)" >/dev/null

    STACK=(sz-bench-edge sz-bench-traefik sz-bench-f2b sz-bench-unban sz-bench-alerter sz-bench-o2p)
    local -a C=(curl -ks -o /dev/null --resolve "zellij.test:$HTTPS:127.0.0.1")
    for _ in $(seq 30); do "${C[@]}" -f "https://zellij.test:$HTTPS/" && break; sleep 1; done
    sleep 20

    sample "### Idle (30 s)" 30

    # one client at the Traefik rate limit, plus the dashboard polling every 2 s
    (for _ in $(seq 600); do "${C[@]}" "https://zellij.test:$HTTPS/" & sleep 0.05; done; wait) >/dev/null 2>&1 &
    local load=$!
    (for _ in $(seq 15); do curl -s -o /dev/null "http://127.0.0.1:$STATS/api"; sleep 2; done) &
    sleep 3
    sample "### One client at the rate limit (20 req/s, 20 s window)" 20
    wait "$load"

    # burst: rate limit removed, 5000 requests from 50 parallel clients
    sed -i '/- rate-limit/d' "$WORK/traefik/dynamic/routers.yml"
    podman restart -t 2 sz-bench-traefik >/dev/null
    for _ in $(seq 30); do "${C[@]}" -f "https://zellij.test:$HTTPS/" && break; sleep 1; done
    local t0 t1
    t0=$(date +%s.%N)
    # shellcheck disable=SC2016  # expanded by the inner sh: $0 is the port, "$@" the request numbers
    (seq 5000 | xargs -P 50 -n 100 sh -c 'for _ in "$@"; do curl -ks -o /dev/null --resolve "zellij.test:$0:127.0.0.1" "https://zellij.test:$0/"; done' "$HTTPS") &
    local burst=$!
    sleep 2
    sample "### Burst: 5000 requests, 50 parallel clients, no rate limit (8 s window)" 8
    wait "$burst"
    t1=$(date +%s.%N)
    python3 -I -c "import sys; d = float(sys.argv[2]) - float(sys.argv[1]); print(f'\n5000 requests in {d:.1f} s ({5000 / d:.0f} req/s)')" "$t0" "$t1"

    echo -e "\n### Images\n\n| Image | Size |\n|---|---|"
    for i in "$(image traefik)" "$F2B_IMAGE" "$(image oauth2-proxy)" "$ALERTER_IMAGE"; do
        podman image inspect "$i" --format '{{.Size}}' | python3 -I -c 'import sys; print(f"| {sys.argv[1]} | {int(sys.stdin.read()) / 1e6:.0f} MB |")' "$i"
    done
    podman rm -f -v --ignore sz-bench-traefik sz-bench-f2b >/dev/null 2>&1
    podman rm -f -v --ignore "${NAMES[@]}" >/dev/null 2>&1
}

# ── filter ────────────────────────────────────────────────────────────────────

bench_filter() {
    echo -e "\n## fail2ban filter\n"
    mkdir -p "$WORK/regex/filters"
    cp "$REPO/fail2ban/filter.d/traefik-zellij.conf" "$WORK/regex/filters/current.conf"
    sed 's/^failregex = .*/failregex = ^NEVER<ADDR>/' "$REPO/fail2ban/filter.d/traefik-zellij.conf" >"$WORK/regex/filters/floor.conf"
    # 50k lines built from the real-shaped fixture lines: random IPv4/IPv6
    # clients, a realistic status mix, 1% headers carrying fake fields
    python3 -I - "$REPO/tests/fixtures/traefik-access.log" "$WORK/regex" <<'PY'
import json, random, sys
src, out = sys.argv[1], sys.argv[2]
random.seed(7)
tmpl = [json.loads(l) for l in open(src)]
statuses = [200] * 80 + [0] * 5 + [101] * 2 + [304] * 3 + [401] * 4 + [403] + [404] * 3 + [429] + [502]
with open(f"{out}/bench.log", "w") as log, open(f"{out}/expected", "w") as exp:
    for i in range(50000):
        e = dict(random.choice(tmpl))
        v6 = random.random() < 0.15
        ip = f"2001:db8::{random.randint(1, 400):x}" if v6 else f"198.51.{random.randint(0, 3)}.{random.randint(1, 250)}"
        port = random.randint(1024, 65535)
        e["ClientAddr"] = f"[{ip}]:{port}" if v6 else f"{ip}:{port}"
        e["ClientHost"], e["ClientPort"] = ip, str(port)
        e["DownstreamStatus"] = st = random.choice(statuses)
        if random.random() < 0.01:
            e["request_User-Agent"] = 'x" "DownstreamStatus":401 "ClientAddr":"203.0.113.66:1"'
        mm, ss = divmod(i * 3600 // 50000, 60)
        e["time"] = f"2026-11-22T20:{mm:02d}:{ss:02d}Z"
        log.write(json.dumps(e, separators=(",", ":")) + "\n")
        if 400 <= st < 500:
            exp.write(ip + "\n")
PY
    sort "$WORK/regex/expected" >"$WORK/regex/expected.sorted"
    echo "50,000 lines ($(du -m "$WORK/regex/bench.log" | cut -f1) MB), $(wc -l <"$WORK/regex/expected") lines with a 4xx status."
    echo -e "\n| Filter | Time | Matches |\n|---|---|---|"
    local f t0 t1 secs same
    for f in current floor; do
        t0=$(date +%s.%N)
        podman run --rm --name sz-bench-regex -v "$WORK/regex:/b:ro,z" --entrypoint fail2ban-regex "$F2B_IMAGE" \
            -o ip /b/bench.log "/b/filters/$f.conf" 2>/dev/null | sort >"$WORK/regex/$f.out"
        t1=$(date +%s.%N)
        secs=$(python3 -I -c 'import sys; print(f"{float(sys.argv[2]) - float(sys.argv[1]):.1f} s")' "$t0" "$t1")
        if [ "$f" = floor ]; then
            same="none (cost without the regex: dates, I/O, startup)"
        elif cmp -s "$WORK/regex/$f.out" "$WORK/regex/expected.sorted"; then
            same="all $(wc -l <"$WORK/regex/expected") expected, nothing else"
        else
            same="**WRONG**: $(wc -l <"$WORK/regex/$f.out") matches"
        fi
        echo "| $f | $secs | $same |"
    done
}

case "$WHAT" in
    stack) bench_stack ;;
    filter) bench_filter ;;
    all) bench_stack; bench_filter ;;
    *) echo "usage: $0 [stack|filter|all]" >&2; exit 2 ;;
esac
