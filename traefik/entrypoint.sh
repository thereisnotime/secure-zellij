#!/bin/sh
set -e

# Copy dynamic config to writable location and substitute DOMAIN
mkdir -p /etc/traefik/dynamic-live
cp /etc/traefik/dynamic/* /etc/traefik/dynamic-live/
sed -i "s/ZELLIJ_DOMAIN/${DOMAIN}/g" /etc/traefik/dynamic-live/routers.yml

exec traefik "$@"
