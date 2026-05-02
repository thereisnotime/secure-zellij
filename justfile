set dotenv-load := true

# List all available recipes
default:
    @just --list --unsorted

# ── Stack ─────────────────────────────────────────────────────────────────────

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

# ── Zellij ────────────────────────────────────────────────────────────────────

# Generate a new zellij web login token
token:
    zellij web --create-token

# List existing login tokens
tokens:
    zellij web --list-tokens

# Show zellij web server + container status
status:
    @echo "=== Zellij web server ==="
    @zellij web --status || true
    @echo ""
    @echo "=== Containers ==="
    @podman compose ps

# ── Dev ───────────────────────────────────────────────────────────────────────

# Lint alerter Python code
lint:
    ruff check alerter/

# Validate compose file
validate:
    podman compose config --quiet
    @echo "compose.yaml OK"
