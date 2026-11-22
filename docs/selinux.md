# SELinux

The stack runs with SELinux in **enforcing** mode (Fedora, RHEL, CentOS Stream,
Alma, Rocky). Nothing in it needs SELinux switched off, `--privileged`, or
`label=disable`, and you shouldn't add any of those. Every mount carries the
right label instead.

On hosts without SELinux (Debian, Ubuntu, Arch) everything here does nothing.

## How the stack is labelled

Rootless podman runs each container as `container_t` with its own MCS category
pair, e.g. `s0:c207,c718`. A container can only read files labelled
`container_file_t`, and only those its categories allow. Your files in `~`
are labelled `user_home_t`, so the containers can't read them unless they're
relabelled.

| What | Mount | Why |
|---|---|---|
| Repo config files (`./traefik/*`, `./fail2ban/filter.d`, `jail.d`, `unban-helper.py`) | `:ro,z` | Podman relabels them to `container_file_t:s0` (shared, no categories) when the container starts. Without it Traefik can't read its config on a fresh clone. |
| Named volumes (`traefik-logs`, `fail2ban-data`, `stats-data`, `letsencrypt`) | none needed | Podman labels named volumes itself, and several containers can share them. |
| tmpfs volumes (`fail2ban-run`, `unban-requests`) | `:z` | A tmpfs volume is mounted with a default label. Without `:z` fail2ban can't create its control socket, and the unban helper can't reach it. |

`:z` (lowercase) gives a shared label that any container can use. `:Z`
(uppercase) gives a label private to one container. This stack uses `:z`
everywhere, because some volumes are shared between containers, and the config
files keep working when containers are recreated with new categories.

Relabelling only touches the files that get mounted, never the whole repo.
Afterwards they show `container_file_t` in `ls -Z`. You can still edit them and
use git as before.

## Checking

```bash
just selinux-check
```

This shows the SELinux mode, the label of every mounted path, each container's
process label and health, and whether fail2ban answers on its socket. On a host
without SELinux it only reports the health part.

When something is denied, the audit log has the details (needs root):

```bash
sudo ausearch -m AVC -ts recent                  # raw denials
sudo ausearch -m AVC -ts recent | audit2why      # why each was denied
```

Look for `scontext=...container_t...`. The `tcontext` names the file or socket
type the container was refused. `user_home_t` almost always means a mount
without `:z`.

## Failures that look fine

Some failures leave the container running, so `podman ps` alone doesn't show
them:

- **fail2ban can't create its socket.** The server exits but the container
  stays up, and nothing gets banned. fail2ban's healthcheck (`fail2ban-client
  ping`) turns this into `unhealthy`.
- **The unban helper can't reach the socket.** The dashboard's unban button
  reports an error. The helper's healthcheck shows `unhealthy`.

That's why `just selinux-check` and `podman ps` should show every container
as `healthy` (the alerter, Traefik, fail2ban and fail2ban-unban have
healthchecks).

## Testing on SELinux

CI runs on Ubuntu, which has no SELinux. Run the end-to-end tests on an
SELinux host before a release, or after changing mounts. They use their own
throwaway containers and leave the running stack alone:

```bash
just test-filter && just test-unban && just test-sso
```

The test scripts label their mounts the same way as `compose.yaml`.
