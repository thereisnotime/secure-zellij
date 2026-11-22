#!/usr/bin/env python3
"""
Unban requests from the stats dashboard, carried out next to fail2ban.

The alerter drops <id>.json files ({"jail": ..., "ip": ...}) into REQUEST_DIR.
This helper is the only container besides fail2ban that can reach fail2ban's
control socket, and the only thing it ever runs is

    fail2ban-client set <jail> unbanip <ip>

with a jail from UNBAN_JAILS and a parsed IP address. The answer goes back as
<id>.result. The alerter never sees the socket: whoever controls that socket
can reconfigure fail2ban, including the shell commands its actions run.
"""

import contextlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import time

REQUEST_DIR = os.environ.get("REQUEST_DIR", "/requests")
JAILS = {j.strip() for j in os.environ.get("UNBAN_JAILS", "traefik-zellij").split(",") if j.strip()}
MAX_AGE = 60  # seconds; the alerter gives up long before this
MAX_SIZE = 4096
REQUEST_NAME = re.compile(r"^[0-9a-f]{32}\.json$")
RESULT_NAME = re.compile(r"^[0-9a-f]{32}\.result$")


def log(msg: str) -> None:
    print(f"[unban-helper] {msg}", flush=True)


def handle(req: dict) -> dict:
    jail, ip = req.get("jail"), req.get("ip")
    if not isinstance(jail, str) or jail not in JAILS:
        return {"ok": False, "error": f"jail {jail!r} is not in UNBAN_JAILS"}
    try:
        ip = str(ipaddress.ip_address(str(ip)))
    except ValueError:
        return {"ok": False, "error": "not an IP address"}
    try:
        out = subprocess.run(
            ["fail2ban-client", "set", jail, "unbanip", ip],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"fail2ban-client failed: {exc}"}
    if out.returncode != 0:
        detail = (out.stderr or out.stdout).strip()[-300:]
        return {"ok": False, "error": detail or f"fail2ban-client exited with {out.returncode}"}
    if out.stdout.strip() == "0":
        return {"ok": False, "error": f"{ip} isn't banned in {jail}"}
    return {"ok": True, "jail": jail, "ip": ip}


def write_result(rid: str, result: dict) -> None:
    tmp = os.path.join(REQUEST_DIR, f".{rid}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    os.replace(tmp, os.path.join(REQUEST_DIR, f"{rid}.result"))


def remove(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def sweep() -> None:
    now = time.time()
    for name in sorted(os.listdir(REQUEST_DIR)):
        path = os.path.join(REQUEST_DIR, name)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        stale = now - st.st_mtime > MAX_AGE
        if RESULT_NAME.match(name):
            if stale:  # the alerter timed out and never picked it up
                remove(path)
            continue
        if not REQUEST_NAME.match(name):
            continue
        if stale or st.st_size > MAX_SIZE or not os.path.isfile(path) or os.path.islink(path):
            remove(path)
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                req = json.load(fh)
            if not isinstance(req, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            log(f"dropping unreadable request {name}: {exc}")
            remove(path)
            continue
        result = handle(req)
        log(f"unban {req.get('ip')!r} in {req.get('jail')!r}: {result}")
        write_result(name[: -len(".json")], result)
        remove(path)


def main() -> None:
    os.makedirs(REQUEST_DIR, exist_ok=True)
    log(f"watching {REQUEST_DIR}, jails: {', '.join(sorted(JAILS)) or 'none'}")
    while True:
        try:
            sweep()
        except OSError as exc:
            print(f"[unban-helper] {exc}", file=sys.stderr, flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
