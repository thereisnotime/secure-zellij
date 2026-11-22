# Contributing

Thanks for helping. Bug reports, fixes, docs and new integrations are all welcome.
For larger changes, open an issue first so we can agree on the approach.

## Development setup

You need the same things as for running the stack ([README](README.md#requirements))
plus:

- [uv](https://docs.astral.sh/uv/) for the alerter's Python tooling
- `shellcheck` for the shell scripts
- `python3`, `curl` and `openssl` for the end-to-end tests

Versions are pinned in `.tool-versions` (asdf or mise will pick them up).

```bash
cd alerter && uv sync          # dev dependencies: pytest, ruff
```

## Running the checks

```bash
just lint          # ruff check + format check (alerter, unban helper)
just test          # alerter unit tests
just validate      # compose.yaml parses, with and without the sso profile
just test-filter   # fail2ban regex against sample log lines
just test-ban      # a ban really drops the client
just test-unban    # dashboard unban path and guards
just test-sso      # SSO login flows with a mock IdP
shellcheck -x traefik/entrypoint.sh tests/*.sh
```

The end-to-end tests start throwaway containers named `sz-*` on spare ports and
remove them on exit. They never touch a running stack. CI runs all of the above on
Ubuntu, plus gitleaks.

If you change mounts, networking or anything SELinux could care about, also run the
end-to-end tests on an SELinux host in enforcing mode (Fedora, RHEL). CI can't
cover that. See [SELinux](docs/selinux.md#testing-on-selinux).

## Conventions

- **Containers**: rootless podman only. Nothing may need root, `--privileged`,
  host networking or `label=disable`. New mounts need an SELinux label (`:z`).
- **Images**: pin by version and digest in `compose.yaml`.
- **Python**: ruff decides formatting; `just format` fixes it. Keep the alerter's
  dependencies minimal.
- **Shell**: `set -euo pipefail`, shellcheck-clean.
- **Config**: every new variable goes into `.env.example`, `compose.yaml` and
  [docs/configuration.md](docs/configuration.md).
- **Tests**: a fix comes with a test that fails without it where that's practical.
  Changes to the fail2ban filter must keep `just test-filter` passing and should be
  measured with `just bench filter`.
- **Docs**: update the docs in the same pull request. Short, concrete, no
  marketing.

## Commits and pull requests

- Commit messages: imperative and specific, conventional-commit prefixes welcome
  (`fix(alerter): ...`, `docs: ...`). Say why, not only what.
- One topic per pull request. Fill in the template, including how you tested.
- Pull requests need green CI.

## Reporting security issues

Don't open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
