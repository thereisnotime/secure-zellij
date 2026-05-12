set dotenv-load := true

# List all available recipes
default:
    @just --list --unsorted

# ── Stack ─────────────────────────────────────────────────────────────────────

# Start the full stack: Traefik proxy + Zellij web backend
start:
    podman compose up -d
    zellij web --start -d 2>/dev/null || true
    @just status

# Start the stack in the background
up:
    podman compose up -d

# Stop the stack
down:
    podman compose down

# Restart all services
restart:
    podman compose restart

# Rebuild all images (no cache)
build:
    podman compose build --no-cache

# Pull latest upstream images
pull:
    podman compose pull

# Destroy containers and volumes (destructive)
clean:
    podman compose down -v

# ── Logs ──────────────────────────────────────────────────────────────────────

# Follow logs for all services
logs:
    podman compose logs -f

# Follow Traefik logs only
logs-traefik:
    podman compose logs -f traefik

# Follow alerter logs only
logs-alerter:
    podman compose logs -f alerter

# Show last N access log entries (default: 20)
access n="20":
    @tail -{{ n }} ~/.local/share/containers/storage/volumes/secure-zellij_traefik-logs/_data/access.log \
        | python3 -c "import sys,json; [print(json.dumps({k:v for k,v in json.loads(l).items() if k in ['time','ClientAddr','RequestHost','RequestPath','DownstreamStatus','RouterName','request_User-Agent']},indent=2)) for l in sys.stdin if l.strip()]" 2>/dev/null

# ── Status ────────────────────────────────────────────────────────────────────

# Full status: zellij web server, sessions, containers, tokens
status:
    @echo "┌─ Zellij web server ──────────────────────────────────────"
    @zellij web --status 2>/dev/null || echo "  offline"
    @echo "│"
    @echo "├─ Access ─────────────────────────────────────────────────"
    @{ echo "${DOMAIN}"; [ -n "${EXTRA_DOMAINS:-}" ] && echo "${EXTRA_DOMAINS}" | tr ',' '\n'; } | awk '!seen[$0]++' | while read d; do echo "  https://${d}:${HTTPS_PORT:-443}"; done
    @echo "│"
    @echo "├─ Sessions ───────────────────────────────────────────────"
    @zellij list-sessions 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | awk -v base="https://${DOMAIN}:${HTTPS_PORT:-443}" '{print "  " $1 "  →  " base "/" $1}' || echo "  none"
    @echo "│"
    @echo "├─ Tokens ─────────────────────────────────────────────────"
    @zellij web --list-tokens 2>/dev/null | sed 's/^/  /' || echo "  none"
    @echo "│"
    @echo "└─ Containers ─────────────────────────────────────────────"
    @podman compose ps 2>/dev/null

# Open the primary URL in the default browser
open:
    xdg-open "https://${DOMAIN}:${HTTPS_PORT:-8443}"

# ── Zellij web server ─────────────────────────────────────────────────────────

# Start zellij web server in the background
web-start:
    zellij web --start -d
    @zellij web --status

# Stop zellij web server
web-stop:
    zellij web --stop

# ── Token management ──────────────────────────────────────────────────────────

# Create a new read-write login token (name not supported by zellij CLI)
token:
    zellij web --create-token

# Create a read-only login token
token-ro:
    zellij web --create-read-only-token

# List all tokens with creation dates
tokens:
    zellij web --list-tokens

# Revoke a token by name
revoke name:
    zellij web --revoke-token "{{ name }}"

# Revoke all tokens (destructive)
revoke-all:
    @echo "Revoking all tokens..."
    zellij web --revoke-all-tokens
    @echo "Done."

# ── Dev ───────────────────────────────────────────────────────────────────────

# Lint alerter Python code
lint:
    cd alerter && uv run ruff check .

# Validate compose file
validate:
    podman compose config --quiet
    @echo "compose.yaml OK"
