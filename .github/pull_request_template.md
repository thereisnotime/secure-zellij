## What and why

<!-- What does this change, and what problem does it solve? Link the issue if there is one. -->

## How I tested it

<!-- Commands you ran and what you saw. -->

- [ ] `just lint` and `just test`
- [ ] End-to-end tests that cover the change (`just test-filter`, `test-ban`, `test-unban`, `test-sso`)
- [ ] On an SELinux host in enforcing mode (if mounts, networking or labels changed)

## Checklist

- [ ] Still works rootless, no new root, `--privileged`, host networking or `label=disable`
- [ ] New variables are in `.env.example`, `compose.yaml` and `docs/configuration.md`
- [ ] Docs and `CHANGELOG.md` updated
