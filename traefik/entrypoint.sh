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

die() {
    echo "entrypoint: $*" >&2
    exit 1
}

live=/etc/traefik/dynamic-live
mkdir -p "${live}"
rm -f "${live}"/*
cp /etc/traefik/dynamic/* "${live}/"

# ── SSO ───────────────────────────────────────────────────────────────────────
# SSO_MODE=off           no SSO, zellij token only (default)
# SSO_MODE=oauth2-proxy  forwardAuth to the oauth2-proxy container (any OIDC IdP)
# SSO_MODE=forward-auth  forwardAuth straight to the IdP (Authelia, Authentik)
sso_mode="${SSO_MODE:-off}"
case "${sso_mode}" in
    off | "") sso_mode=off ;;
    oauth2-proxy)
        port="${SSO_PROXY_PORT:-4180}"
        sed "s|SSO_PROXY_PORT|${port}|g" /etc/traefik/sso/oauth2-proxy.yml > "${live}/sso.yml"
        ;;
    forward-auth)
        [ -n "${SSO_FORWARD_AUTH_URL}" ] || die "SSO_MODE=forward-auth needs SSO_FORWARD_AUTH_URL"
        sed "s|SSO_FORWARD_AUTH_URL|${SSO_FORWARD_AUTH_URL}|g" /etc/traefik/sso/forward-auth.yml > "${live}/sso.yml"
        ;;
    *) die "unknown SSO_MODE '${sso_mode}' (use off, oauth2-proxy or forward-auth)" ;;
esac

if [ -n "${SSO_CALLBACK_PREFIX}" ]; then
    [ "${sso_mode}" != off ] || die "SSO_CALLBACK_PREFIX is set but SSO_MODE=off"
    [ -n "${SSO_CALLBACK_URL}" ] || die "SSO_CALLBACK_PREFIX needs SSO_CALLBACK_URL"
    sed -e "s|SSO_CALLBACK_PREFIX|${SSO_CALLBACK_PREFIX}|g" -e "s|SSO_CALLBACK_URL|${SSO_CALLBACK_URL}|g" \
        /etc/traefik/sso/callback.yml > "${live}/sso-callback.yml"
fi

if [ "${sso_mode}" = off ]; then
    sso_line=""
else
    sso_line="- sso"
    echo "entrypoint: SSO enabled (${sso_mode})"
fi

# awk handles backticks in the replacement cleanly unlike sed
for f in "${live}"/*.yml; do
    awk -v r="${rule}" -v s="${sso_line}" '{gsub(/ZELLIJ_RULE/, r); gsub(/# SSO_MIDDLEWARE/, s)}1' \
        "${f}" > /tmp/dyn.yml && mv /tmp/dyn.yml "${f}"
done

# Traefik v3 does not expand ${VAR} in static config files — do it here
awk -v hp="${HTTP_PORT:-8080}" -v hps="${HTTPS_PORT:-8443}" \
    '{gsub(/\$\{HTTP_PORT\}/, hp); gsub(/\$\{HTTPS_PORT\}/, hps)}1' \
    /etc/traefik/traefik.yml > /tmp/traefik.yml

exec traefik --configFile=/tmp/traefik.yml "$@"
