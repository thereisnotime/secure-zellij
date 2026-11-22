# Performance

How much CPU, memory and disk the stack uses, and the benchmark behind the
fail2ban filter. The numbers come from one machine, measured on 2026-11-23 with the
`edge` network namespace layout (Traefik and fail2ban behind pasta, bans in
nftables). Expect the same order of magnitude elsewhere, not the exact figures.

Rerun them yourself with `just bench` (or `just bench stack` / `just bench filter`).
It uses throwaway `sz-bench-*` containers on spare ports, leaves the real stack alone,
and prints the tables below as Markdown.

**Short version:** about **160 MiB of RAM and ~0% CPU when idle**. One client
at the rate limit (20 req/s) costs about **22% of one core**, nearly all of it
Traefik. Images take about **560 MB** of disk.

## Test setup

| | |
|---|---|
| CPU | Intel Xeon W-2295 @ 3.00 GHz, 36 threads |
| RAM | 62 GB |
| Kernel / runtime | Linux 6.17, rootless podman 4.9.3, cgroups v2 |
| Stack | Traefik v3.7.19, fail2ban 1.1.1, alerter (Python 3.15.2), oauth2-proxy v7.15.6 |

The measurements used a throwaway copy of the stack on spare ports, with the repo's
own configs and the same layout as `compose.yaml`: `edge` owns a pasta network
namespace and the published ports, and Traefik and fail2ban join it. The one
change was patching the backend ports. A small Python HTTP
server stood in for zellij, answering with 2 KB bodies, and a mock OIDC provider
stood in for the IdP. Neither one is counted.

- **CPU**: read from each container's cgroup `cpu.stat` (`usage_usec`) at the
  start and end of a window, and given as a percentage of one core. A
  single-shot `podman stats` averages CPU since the container started, which
  includes startup, so it isn't used here.
- **Memory**: the highest value seen during the window, computed the way `podman
  stats` does: `memory.current` minus `inactive_file` (page cache that can be
  reclaimed).
- **pasta**: the process behind `edge`'s network runs on the host, outside every
  container's cgroup, so `podman stats` never shows it. Its CPU and RSS come
  from `/proc/<pid>`.
- **Load**: `curl` over HTTPS from 127.0.0.1. "20 req/s" is one client at the
  Traefik rate limit (`average: 20`, `burst: 50`), which is the most a single
  client can push. The burst test removes the rate limit and sends 5,000
  requests from 50 parallel clients.

## Whole stack

| | Idle | 20 req/s (rate limit) | Burst: 50 parallel, no rate limit |
|---|---|---|---|
| Traefik | 0.0% · 19 MiB | 17.0% · 32 MiB | 183% · 62 MiB |
| pasta (edge's network, on the host) | 0.0% · 29 MiB | 2.0% · 29 MiB | 20% · 29 MiB |
| fail2ban | 0.0% · 15 MiB | 2.0% · 19 MiB | 18% · 21 MiB |
| alerter | 0.2% · 66 MiB | 0.6% · 68 MiB | 1.7% · 68 MiB |
| fail2ban-unban | 0.0% · 6.7 MiB | 0.0% · 6.5 MiB | 0.0% · 6.7 MiB |
| edge (sleep loop) | 0.0% · 0.4 MiB | 0.0% · 0.4 MiB | 0.0% · 0.4 MiB |
| oauth2-proxy (optional) | 0.0% · 22 MiB | not under load | not under load |
| **Total** | **0.3% · 158 MiB** | **21.7% · 177 MiB** | **222% · 210 MiB** |

CPU is a percentage of one core.

- **Burst throughput**: 5,000 requests took 10.5 s, about 477 req/s, with the
  rate limit off. That's the same as with host networking.

### What the edge namespace costs

Before the `edge` namespace, Traefik ran on the host network and fail2ban
couldn't actually block anyone (rootless podman has no access to the host
firewall). Real bans cost this much, measured with the same benchmark:

| | Host network (bans didn't block) | edge namespace (bans drop packets) |
|---|---|---|
| CPU at 20 req/s | 19.5% | 21.7% (+2.2, mostly pasta) |
| CPU in the burst | 189% | 222% (+33: pasta 20, Traefik 12) |
| Burst throughput | 478 req/s | 477 req/s |
| Memory, idle | 108 MiB | 158 MiB (+29 MiB pasta, see note on oauth2-proxy) |

pasta copies every connection between the host and the namespace, which is where
the extra CPU goes. Throughput doesn't change. Its RSS is mostly the buffers it
allocates at startup and stays flat under load. oauth2-proxy measured 4 MiB idle
in the earlier run and 22 MiB in this one. It got no traffic in either run, so
that's the Go runtime's startup variance, not the layout.

With the old fail2ban filter, fail2ban alone took 23% at 20 req/s and a full core
in the burst (see [below](#fail2ban-filter)).
- **After the burst**: CPU drops straight back to idle. Traefik keeps about
  54 MiB for a while, because Go doesn't hand memory back straight away.
- **oauth2-proxy** was only measured idle, because the load test didn't log in.
  It checks every request through forwardAuth, so expect it to add some CPU
  under load.
- **The alerter** uses the most memory: Python, FastAPI and uvicorn, plus the
  `uv run` process that stays alive as its parent. Its CPU stays low because it
  only parses lines that look like terminal sessions and does nothing on others.
- **Not counted**: the zellij web server runs on the host, outside the stack.

## Disk

| Image | Size |
|---|---|
| alerter (built locally) | 232 MB |
| Traefik v3.7.19 | 193 MB |
| fail2ban 1.1.1 (also used by fail2ban-unban) | 87 MB |
| oauth2-proxy v7.15.6 (only with `SSO_MODE=oauth2-proxy`) | 47 MB |

The access log grows by about 2 KB per request. See [Log Rotation](log-rotation.md).

## fail2ban filter

fail2ban runs its `failregex` on every line of the access log. Each line is about
2 KB of JSON, so the regex matters.

```ini
# old
failregex = .*"ClientAddr":"<HOST>:\d+".*"DownstreamStatus":4\d\d.*
# current
failregex = "ClientAddr":"<ADDR>:\d+".*?"DownstreamStatus":4\d\d,
```

Because of the leading `.*`, Python retried the match from every position in a
non-matching line, which is quadratic in the line length. The current regex has
no leading `.*`, uses a lazy gap between the two fields, and uses `<ADDR>` (IP
addresses only) instead of `<HOST>`. `ClientAddr` is always an IP.

### Live, real fail2ban tailing Traefik's log

| | Old | Current |
|---|---|---|
| CPU at 20 req/s (one in ten requests a 401) | 19.2% of a core | **1.75%** |
| CPU to process a 5,000-request burst | 16.7 CPU-s | **1.3 CPU-s** |
| fail2ban still catching up after the burst | 15.9 s | **0.3 s** |

### Offline, `fail2ban-regex` over a 50,000-line log (101 MB)

The log was built from real Traefik lines. It has 15% IPv6 clients, a realistic
mix of statuses (200, 0, 101, 304, 401, 403, 404, 429, 502) and 1% of
requests with fake `"DownstreamStatus":401` / `"ClientAddr"` text in the
User-Agent. Every candidate had to return exactly the 4,515 expected IPs.

| Filter | Time | Matches |
|---|---|---|
| old (leading `.*`, `<HOST>`) | 145 s | identical |
| **current** (unanchored, lazy, `<ADDR>`) | **7.8 s** | identical |
| anchored to start of line, `<HOST>` | 7.9 s | identical |
| anchored to start of line, `<ADDR>` | 7.2 s | identical |
| anchored, bounded field list instead of `.*?` | 6.5 s | identical |
| current + date pattern anchored to end of line | 7.0 s | identical |
| floor: regex that never matches | 6.0 s | none |

The floor is what fail2ban spends without the regex: date parsing, reading the
file, and about 0.8 s of container and Python startup. Measured directly, a
non-matching 2 KB line costs 3.1 ms with the old regex and 39 µs with the current
one.

### Why not the fastest candidate

The anchored variants are a bit faster. They rely on `ClientAddr` staying the
first key in Traefik's JSON, which holds today because Traefik sorts the keys.
If an update ever added a key that sorts before it, they would silently stop
matching, and nothing would get banned. Anchoring the date pattern to the end of
the line has the same problem with `"time"` being the last key, except that
fail2ban would then skip every line as undated. The current filter is within
about a second of the fastest and doesn't depend on field order.

`tests/fail2ban-filter.sh` (`just test-filter`, also in CI) checks the filter
against sample lines: 4xx are banned; 0, 101, 3xx and 5xx aren't; IPv6 works;
and no IP gets in through a header or path value.

## Spoofing

Traefik escapes quotes inside JSON values, so text like `"DownstreamStatus":401`
in a header or path shows up as `\"DownstreamStatus\":401`. Neither the old nor
the current regex matches that. Both the 50,000-line run and the filter test
include such lines, and none of them led to a ban.
