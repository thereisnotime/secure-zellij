# shellcheck shell=bash
# Shared helpers for the test and bench scripts. Source it, don't run it.

# compose_json [compose global args...]: the resolved compose model as JSON on
# stdout, with every service's environment as a {KEY: value} object.
# docker-compose can print JSON itself. podman-compose only prints YAML, so
# that output goes through PyYAML. Resolved from a copy of compose.yaml in an
# empty directory: podman-compose also reads the project's .env, and lets it
# win over --env-file, which would leak the host's real settings into tests.
compose_json() {
    local out dir
    dir=$(mktemp -d)
    cp "$REPO/compose.yaml" "$dir/"
    if ! out=$(cd "$dir" && $COMPOSE "$@" config --format json 2>/dev/null) || [ "${out:0:1}" != "{" ]; then
        out=$(cd "$dir" && $COMPOSE "$@" config 2>/dev/null |
            python3 -I -c 'import json, sys, yaml; print(json.dumps(yaml.safe_load(sys.stdin)))') || {
            echo "compose_json: '$COMPOSE config' gave neither JSON nor YAML that PyYAML could read" >&2
            rm -rf "$dir"
            return 1
        }
    fi
    rm -rf "$dir"
    printf '%s' "$out" | python3 -I -c '
import json, sys
cfg = json.load(sys.stdin)
for svc in cfg.get("services", {}).values():
    env = svc.get("environment")
    if isinstance(env, list):
        svc["environment"] = dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
print(json.dumps(cfg))'
}
