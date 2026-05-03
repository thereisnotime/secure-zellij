#!/bin/sh
set -e

# Build Traefik Host rule from DOMAIN + optional EXTRA_DOMAINS (comma-separated)
# e.g. DOMAIN=zellij.local EXTRA_DOMAINS=192.168.77.228,fenrir.lan
# → Host(`zellij.local`) || Host(`192.168.77.228`) || Host(`fenrir.lan`)
rule="Host(\`${DOMAIN}\`)"
if [ -n "${EXTRA_DOMAINS}" ]; then
    for d in $(echo "${EXTRA_DOMAINS}" | tr ',' ' '); do
        rule="${rule} || Host(\`${d}\`)"
    done
fi

mkdir -p /etc/traefik/dynamic-live
cp /etc/traefik/dynamic/* /etc/traefik/dynamic-live/

# awk handles backticks in the replacement cleanly unlike sed
awk -v r="${rule}" '{gsub(/ZELLIJ_RULE/, r)}1' \
    /etc/traefik/dynamic-live/routers.yml > /tmp/routers.yml \
    && mv /tmp/routers.yml /etc/traefik/dynamic-live/routers.yml

exec traefik "$@"
