# SELinux

The stack runs with SELinux in **enforcing** mode (Fedora, RHEL, CentOS Stream, Alma, Rocky). Nothing in it needs SELinux switched off, `--privileged` or `label=disable`, and you shouldn't add any of those. Every mount carries the right label instead.

This page explains how the mounts are labelled, how to check them, which failures don't show up in `podman ps` without a healthcheck, and how to test on an SELinux host. On hosts without SELinux (Debian, Ubuntu, Arch) none of this has any effect.

The end-to-end tests pass on an enforcing host with podman 5.8 and podman-compose 1.5.

## How the stack is labelled

Rootless podman runs each container as `container_t` with its own MCS category pair, e.g. `s0:c207,c718`. A container can only read files labelled `container_file_t`, and only those its categories allow. Your files in `~` are labelled `user_home_t`, so the containers can't read them unless they're relabelled.

| What | Mount option | Why |
|---|---|---|
| Repo files: `traefik/traefik.yml`, `traefik/dynamic`, `traefik/entrypoint.sh`, `traefik/sso`, `fail2ban/filter.d`, `fail2ban/jail.d`, `fail2ban/unban-helper.py` | `:ro,z` | Podman relabels them to `container_file_t:s0` (shared, no categories) when the container starts. Without it Traefik can't read its config on a fresh clone. |
| Named volumes: `traefik-logs`, `letsencrypt`, `fail2ban-data`, `stats-data` | none needed | Podman labels named volumes itself, and several containers can share them. |
| tmpfs volumes: `fail2ban-run`, `unban-requests` | `:z` | A tmpfs volume is mounted with a default label. Without `:z` fail2ban can't create its control socket, and the unban helper can't reach it. |

`:z` (lowercase) gives a shared label that any container can use. `:Z` (uppercase) gives a label private to one container. The stack uses `:z` everywhere: some volumes are shared between containers, and the config files keep working when containers are recreated with new categories.

Relabelling only touches the files that get mounted, never the whole repo. Afterwards they show `container_file_t` in `ls -Z`. You can still edit them and use git as before.

The `edge` container has no mounts. fail2ban's nftables rules in its network namespace work under the default container policy; they need `NET_ADMIN` inside the user namespace and nothing else ([Fail2ban: where the ban happens](fail2ban.md#where-the-ban-happens)).

## Checking

```bash
just selinux-check
```

This prints the SELinux mode, the label of every mounted repo path, the status, health and process label of the `traefik`, `fail2ban`, `fail2ban-unban`, `alerter` and `oauth2-proxy` containers, and whether fail2ban answers on its socket (checked from the `fail2ban-unban` container). On a host without SELinux it only reports the container and socket part.

When something is denied, the audit log has the details (needs root):

```bash
sudo ausearch -m AVC -ts recent                  # raw denials
sudo ausearch -m AVC -ts recent | audit2why      # why each was denied
```

Look for `scontext=...container_t...`. The `tcontext` names the file or socket type the container was refused. `user_home_t` almost always means a mount without `:z`.

## Failures that look fine

Some failures leave the container running, so `podman ps` alone doesn't show them:

- **fail2ban can't create its socket.** The server exits but the container stays up, and nothing gets banned. fail2ban's healthcheck (`fail2ban-client ping`) turns this into `unhealthy`.
- **The unban helper can't reach the socket.** The dashboard's unban button reports an error. The helper's healthcheck (`fail2ban-client ping` through the shared socket) shows `unhealthy`.

Traefik, fail2ban, fail2ban-unban and the alerter have healthchecks, so `podman ps` and `just selinux-check` should show all four as `healthy`. `edge` and `oauth2-proxy` have none and only show their status.

## Testing on SELinux

CI runs on Ubuntu, which has no SELinux. Run the end-to-end tests on an SELinux host before a release or after changing mounts. They use their own throwaway `sz-*` containers and leave the running stack alone:

```bash
just test-filter && just test-ban && just test-unban && just test-sso
```

The test scripts label their mounts the same way as `compose.yaml`. `just test-ban` needs pasta (package `passt`).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Traefik doesn't start or can't read its config on a fresh clone | A repo path isn't relabelled. `just selinux-check` marks it `NOT RELABELLED`. Check that the mount in `compose.yaml` still has `:z`, then recreate the container (`just restart`). |
| fail2ban `unhealthy`, nothing gets banned | The `fail2ban-run` volume lost its `:z`. Check `sudo ausearch -m AVC -ts recent` for denials on the socket. |
| Unban button says *the unban helper didn't answer*, `fail2ban-unban` `unhealthy` | The helper can't reach the socket: `:z` missing on `fail2ban-run` or `unban-requests` in one of the services. |
| Files show `container_file_t` and you want the old labels back | `restorecon -Rv <path>`. Podman relabels them again on the next start. |
