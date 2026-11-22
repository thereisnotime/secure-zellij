set dotenv-load := true

# zellij binary used for the web server, sessions and tokens. Set ZELLIJ_BIN in .env
# to pin a specific build, e.g. when running sessions are an older version than the
# zellij on PATH (the web UI only sees sessions of its own version as running).
zellij := if env("ZELLIJ_BIN", "") == "" { "zellij" } else { env("ZELLIJ_BIN") }

# The oauth2-proxy container only runs in SSO_MODE=oauth2-proxy (docs/sso.md)
# down/clean always include the profile so switching SSO_MODE off doesn't leave it running
compose_all := "podman compose --profile sso"
compose := if env("SSO_MODE", "off") == "oauth2-proxy" { "podman compose --profile sso" } else { "podman compose" }

# List all available recipes
default:
    @just --list --unsorted

# ── Stack ─────────────────────────────────────────────────────────────────────

# Start the full stack: Traefik proxy + Zellij web backend
start:
    {{ compose }} up -d
    {{ zellij }} web --start -d 2>/dev/null || true
    @just status

# Start the stack in the background
up:
    {{ compose }} up -d

# Stop the full stack: Traefik proxy + Zellij web backend
stop:
    {{ compose_all }} down
    {{ zellij }} web --stop 2>/dev/null || true

# Stop the stack
down:
    {{ compose_all }} down

# Traefik and fail2ban live in edge's network namespace, so containers can't be
# recreated in place: podman won't remove edge while they're joined. Stop all, start all.
# Restart all services (picks up .env and config changes)
restart:
    {{ compose_all }} down
    {{ compose }} up -d

# Rebuild all images (no cache)
build:
    {{ compose }} build --no-cache

# Plain `just up` keeps the old alerter image after a git pull and skips containers whose
# bind-mounted configs changed. Build first so the stack keeps serving meanwhile.
# Upgrade: git pull, rebuild the alerter, then stop and start everything
upgrade:
    git pull --ff-only
    {{ compose }} build
    {{ compose_all }} down
    {{ compose }} up -d

# Pull latest upstream images
pull:
    {{ compose }} pull

# Destroy containers and volumes (destructive)
clean:
    {{ compose_all }} down -v

# ── Logs ──────────────────────────────────────────────────────────────────────

# Follow logs for all services
logs:
    {{ compose }} logs -f

# Follow Traefik logs only
logs-traefik:
    {{ compose }} logs -f traefik

# Follow alerter logs only
logs-alerter:
    {{ compose }} logs -f alerter

# Follow oauth2-proxy logs (SSO_MODE=oauth2-proxy)
logs-sso:
    {{ compose }} logs -f oauth2-proxy

# Show last N access log entries (default: 20)
access n="20":
    @tail -{{ n }} "$(podman volume inspect secure-zellij_traefik-logs --format '{{{{.Mountpoint}}}}')/access.log" \
        | python3 -c "import sys,json; [print(json.dumps({k:v for k,v in json.loads(l).items() if k in ['time','ClientAddr','RequestHost','RequestPath','DownstreamStatus','RouterName','request_User-Agent']},indent=2)) for l in sys.stdin if l.strip()]" 2>/dev/null

# Install logrotate config (requires sudo)
logrotate-install:
    @LOG_PATH="$(podman volume inspect secure-zellij_traefik-logs --format '{{{{.Mountpoint}}}}')" && \
        sed "s|LOG_PATH|$LOG_PATH|g" logrotate.conf | sudo tee /etc/logrotate.d/secure-zellij > /dev/null
    @echo "Installed to /etc/logrotate.d/secure-zellij"

# Run logrotate immediately (dry-run by default, pass force=true to apply)
logrotate-run force="false":
    @if [ "{{ force }}" = "true" ]; then \
        sudo logrotate --force /etc/logrotate.d/secure-zellij; \
    else \
        sudo logrotate --debug /etc/logrotate.d/secure-zellij; \
    fi

# ── Fail2ban ──────────────────────────────────────────────────────────────────

# Show fail2ban jail status for this service
fail2ban-status:
    podman exec secure-zellij-fail2ban fail2ban-client status traefik-zellij

# List currently banned IPs
fail2ban-banned:
    podman exec secure-zellij-fail2ban fail2ban-client get traefik-zellij banip --with-time

# Unban a specific IP
fail2ban-unban ip:
    podman exec secure-zellij-fail2ban fail2ban-client set traefik-zellij unbanip {{ ip }}

# ── Status ────────────────────────────────────────────────────────────────────

# Full status: zellij web server, sessions, containers, tokens
status:
    @echo "┌─ Zellij web server ──────────────────────────────────────"
    @echo "  binary: $(command -v {{ zellij }} || echo '{{ zellij }}') ($({{ zellij }} --version 2>/dev/null || echo 'not found'))"
    @{{ zellij }} web --status 2>/dev/null || echo "  offline"
    @echo "│"
    @echo "├─ Access ─────────────────────────────────────────────────"
    @{ echo "${DOMAIN}"; [ -n "${EXTRA_DOMAINS:-}" ] && echo "${EXTRA_DOMAINS}" | tr ',' '\n'; } | awk '!seen[$0]++' | while read d; do echo "  https://${d}:${HTTPS_PORT:-443}"; done
    @echo "│"
    @echo "├─ Sessions ───────────────────────────────────────────────"
    @{{ zellij }} list-sessions 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | awk -v base="https://${DOMAIN}:${HTTPS_PORT:-443}" '{print "  " $1 "  →  " base "/" $1}' || echo "  none"
    @echo "│"
    @echo "├─ Tokens ─────────────────────────────────────────────────"
    @{{ zellij }} web --list-tokens 2>/dev/null | sed 's/^/  /' || echo "  none"
    @echo "│"
    @echo "└─ Containers ─────────────────────────────────────────────"
    @{{ compose }} ps 2>/dev/null

# Open the primary URL in the default browser
open:
    xdg-open "https://${DOMAIN}:${HTTPS_PORT:-8443}"

# ── Zellij web server ─────────────────────────────────────────────────────────

# Start zellij web server in the background
web-start:
    {{ zellij }} web --start -d
    @{{ zellij }} web --status

# Stop zellij web server
web-stop:
    {{ zellij }} web --stop

# ── Token management ──────────────────────────────────────────────────────────

# Create a new read-write login token (name not supported by zellij CLI)
token:
    {{ zellij }} web --create-token

# Create a read-only login token
token-ro:
    {{ zellij }} web --create-read-only-token

# List all tokens with creation dates
tokens:
    {{ zellij }} web --list-tokens

# Revoke a token by name
revoke name:
    {{ zellij }} web --revoke-token "{{ name }}"

# Revoke all tokens (destructive)
revoke-all:
    @echo "Revoking all tokens..."
    {{ zellij }} web --revoke-all-tokens
    @echo "Done."

# ── Dev ───────────────────────────────────────────────────────────────────────

# Lint and format-check the Python code (alerter, unban helper)
lint:
    cd alerter && uv run ruff check . ../fail2ban && uv run ruff format --check . ../fail2ban

# Format alerter Python code
format:
    cd alerter && uv run ruff format . ../fail2ban

# Run alerter unit tests
test:
    cd alerter && uv run pytest

# Validate compose file
validate:
    podman compose config --quiet
    podman compose --profile sso config --quiet
    @echo "compose.yaml OK"

# ── SSO ───────────────────────────────────────────────────────────────────────

# End-to-end SSO test with a mock IdP in throwaway containers (doesn't touch the stack)
test-sso:
    tests/sso-e2e.sh

# Check the fail2ban filter against sample Traefik log lines (4xx, IPv6, spoofing)
test-filter:
    tests/fail2ban-filter.sh

# End-to-end test that a ban really blocks the client (real Traefik + fail2ban, nftables)
test-ban:
    tests/ban-e2e.sh

# End-to-end test of the dashboard unban button (real fail2ban, throwaway containers)
test-unban:
    tests/unban-e2e.sh

# SELinux mode, labels of mounted paths, container labels and health (docs/selinux.md)
selinux-check:
    #!/usr/bin/env bash
    set -uo pipefail
    mode=$(getenforce 2>/dev/null || echo "not installed")
    echo "SELinux: $mode"
    if [ "$mode" != "not installed" ] && [ "$mode" != Disabled ]; then
        echo; echo "Mounted repo paths (want container_file_t after the first start):"
        for p in traefik/traefik.yml traefik/dynamic traefik/entrypoint.sh traefik/sso \
                 fail2ban/filter.d fail2ban/jail.d fail2ban/unban-helper.py; do
            label=$(ls -dZ "$p" | awk '{print $1}')
            case "$label" in *container_file_t*) ok=ok ;; *) ok="NOT RELABELLED (missing :z?)" ;; esac
            printf '  %-28s %s  %s\n' "$p" "$label" "$ok"
        done
    fi
    echo; echo "Containers:"
    for c in edge traefik fail2ban fail2ban-unban alerter oauth2-proxy; do
        n=secure-zellij-$c
        podman container exists "$n" 2>/dev/null || continue
        printf '  %-16s %-10s %-10s %s\n' "$c" \
            "$(podman inspect "$n" --format '{{{{.State.Status}}')" \
            "$(podman inspect "$n" --format '{{{{if .State.Health}}{{{{.State.Health.Status}}{{{{else}}-{{{{end}}')" \
            "$(podman inspect "$n" --format '{{{{.ProcessLabel}}')"
    done
    echo; printf 'fail2ban socket: '
    podman exec secure-zellij-fail2ban-unban fail2ban-client ping 2>&1 | tail -1 || true
    if [ "$mode" = Enforcing ] || [ "$mode" = Permissive ]; then
        echo; echo "Denials, if any: sudo ausearch -m AVC -ts recent | audit2why"
    fi

# Benchmarks from docs/performance.md in throwaway containers: all, stack or filter
bench what="all":
    tests/bench.sh {{ what }}

# Generate a cookie secret for SSO_COOKIE_SECRET (oauth2-proxy)
sso-cookie-secret:
    @openssl rand -base64 32 | tr -- '+/' '-_'

# Show the SSO mode and check the IdP's OIDC discovery document is reachable
sso-check:
    #!/usr/bin/env bash
    set -euo pipefail
    mode="${SSO_MODE:-off}"
    echo "SSO_MODE=${mode}"
    case "$mode" in
      off) echo "SSO disabled, zellij token only." ;;
      oauth2-proxy)
        for v in SSO_OIDC_ISSUER_URL SSO_CLIENT_ID SSO_CLIENT_SECRET SSO_COOKIE_SECRET; do
          [ -n "${!v:-}" ] || { echo "missing $v"; exit 1; }
        done
        len=$(printf '%s' "$SSO_COOKIE_SECRET" | wc -c)
        case "$len" in 16|24|32|44) ;; *) echo "SSO_COOKIE_SECRET must be 16, 24 or 32 bytes (got $len chars), use: just sso-cookie-secret"; exit 1 ;; esac
        url="${SSO_OIDC_ISSUER_URL%/}/.well-known/openid-configuration"
        echo "discovery: $url"
        curl -fsS "$url" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("issuer:", d["issuer"]); print("scopes:", " ".join(d.get("scopes_supported", [])))'
        echo "IdP redirect URI to register: https://${DOMAIN}$( [ "${HTTPS_PORT:-443}" = 443 ] || echo ":${HTTPS_PORT}" )/oauth2/callback"
        ;;
      forward-auth)
        [ -n "${SSO_FORWARD_AUTH_URL:-}" ] || { echo "missing SSO_FORWARD_AUTH_URL"; exit 1; }
        echo "forward-auth: ${SSO_FORWARD_AUTH_URL}"
        [ -z "${SSO_CALLBACK_PREFIX:-}" ] || echo "callback: ${SSO_CALLBACK_PREFIX} -> ${SSO_CALLBACK_URL:-MISSING}"
        ;;
      *) echo "unknown SSO_MODE '$mode'"; exit 1 ;;
    esac
