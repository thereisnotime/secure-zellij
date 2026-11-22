# shellcheck shell=bash
# Shared helpers for the test and bench scripts. Source it, don't run it.

# compose_json [compose global args...]: the resolved compose model as JSON on
# stdout, with every service's environment as a {KEY: value} object.
# docker-compose can print JSON itself. podman-compose only prints YAML, so
# that output goes through PyYAML.
#
# Only the --env-file passed in counts. The host's real settings must not leak
# in, so compose runs from a copy of compose.yaml in an empty directory
# (podman-compose reads the project's .env too), with every variable the file
# interpolates unset (`just` exports .env through dotenv-load, and compose
# prefers the environment over --env-file).
compose_json() {
    local out dir
    local -a vars
    dir=$(mktemp -d)
    cp "$REPO/compose.yaml" "$dir/"
    # shellcheck disable=SC2016  # a literal ${ to find in compose.yaml
    mapfile -t vars < <(grep -o '\${[A-Za-z_][A-Za-z0-9_]*' "$REPO/compose.yaml" | cut -c3- | sort -u)
    if ! out=$(cd "$dir" && unset "${vars[@]}" && $COMPOSE "$@" config --format json 2>/dev/null) || [ "${out:0:1}" != "{" ]; then
        out=$(cd "$dir" && unset "${vars[@]}" && $COMPOSE "$@" config 2>/dev/null |
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
