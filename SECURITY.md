# Security policy

secure-zellij exposes a shell over the internet, so security reports get priority.

## Supported versions

The latest release and `main` are supported. There are no release branches:
fixes land on `main` and go into the next release. `just upgrade` follows `main`.

## Reporting a vulnerability

Please report privately through GitHub:
**[Report a vulnerability](https://github.com/thereisnotime/secure-zellij/security/advisories/new)**
(Security tab → Advisories → Report a vulnerability).

Include what you found, how to reproduce it, and the impact as you see it. Expect
an acknowledgement within a few days. Once a fix is out, the advisory is
published, with credit if you want it.

Please don't open public issues or pull requests for vulnerabilities before a fix
is available.

## Scope

In scope: everything in this repository. That covers the compose stack, Traefik
configuration, fail2ban filter and jail, the unban helper, the alerter and its
dashboard, the SSO wiring, and docs that would lead to an insecure setup if
followed.

Out of scope: vulnerabilities in zellij, Traefik, fail2ban, oauth2-proxy or your
IdP themselves. Report those upstream. A misconfiguration in this repo that makes
them exploitable is in scope.

## Hardening checklist

- Use a real certificate ([TLS](docs/tls.md)). Users who click through a
  self-signed warning can be intercepted.
- Put a login on `/stats`: `STATS_USERNAME`/`STATS_PASSWORD` or SSO.
- Consider [SSO](docs/sso.md) or an [IP allowlist](docs/ip-allowlist.md) so
  zellij's login page isn't reachable by everyone.
- Treat zellij tokens like SSH keys. Revoke unused ones (`just tokens`,
  `just revoke NAME`).
- Keep up to date: `just upgrade`.
