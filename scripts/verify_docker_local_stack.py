#!/usr/bin/env python3
"""FTF-0004 — Docker local stack: real integration + negative-acceptance proof.

Boots `infra/docker/docker-compose.yml` for real, waits for the health
endpoint (apps/api/app/config/docker_local_stack.py) to report every
dependency reachable, then proves a real fault (stopping the Postgres
container) actually flips readiness to unhealthy and a restart recovers
it with the container's own data intact — never a canned "healthy"
response (FTF-0004's negative_acceptance clause).

Requires a real Docker daemon with container-registry egress. This
repo's interactive dev sandbox does not have that (Docker Hub's CDN is
blocked by the session's egress policy — see docs/certifications/queue.md
for FTF-0004); GitHub Actions' hosted runner does, so this runs there
(.github/workflows/ci.yml, job `docker-local-stack-integration`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

COMPOSE_FILE = "infra/docker/docker-compose.yml"
HEALTH_URL = "http://localhost:8080/health"
MARKER_TABLE = "sovra_stack_marker"
MARKER_NOTE = "ftf-0004-restart-proof"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    return subprocess.run(args, check=check, capture_output=True, text=True)


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run("docker", "compose", "-f", COMPOSE_FILE, *args, check=check)


def psql(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return compose(
        "exec", "-T", "postgres", "psql", "-U", "sovra", "-d", "sovra_stack", *args, check=check
    )


def poll_health(expect_ready: bool, timeout_seconds: float = 120.0) -> dict:
    """`urlopen` raises `HTTPError` for the 503 the health endpoint returns
    when unhealthy — that response still carries the real JSON body (the
    whole point of this poll), so it must be read from the exception too,
    not treated as an opaque connection failure."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:  # noqa: S310
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            time.sleep(2)
            continue
        if body["ready"] == expect_ready:
            return body
        last_error = RuntimeError(f"unexpected body: {body}")
        time.sleep(2)
    raise TimeoutError(
        f"health endpoint never reported ready={expect_ready} (last error: {last_error})"
    )


def main() -> int:
    try:
        compose("up", "-d")

        ready = poll_health(expect_ready=True)
        print("stack ready:", ready)

        psql("-c", f"CREATE TABLE IF NOT EXISTS {MARKER_TABLE} (id serial primary key, note text);")
        psql("-c", f"INSERT INTO {MARKER_TABLE} (note) VALUES ('{MARKER_NOTE}');")

        compose("stop", "postgres")
        unhealthy = poll_health(expect_ready=False)
        if unhealthy["ready"] is not False:
            raise AssertionError("readiness did not flip to unhealthy after stopping postgres")
        postgres_entry = next(s for s in unhealthy["services"] if s["service"] == "postgres")
        if postgres_entry["healthy"] is not False:
            raise AssertionError(
                "postgres readiness entry stayed healthy while the container was stopped"
            )
        print("negative-acceptance proven: stopped postgres -> unhealthy:", unhealthy)

        compose("start", "postgres")
        recovered = poll_health(expect_ready=True)
        print("recovered:", recovered)

        marker = psql("-t", "-c", f"SELECT note FROM {MARKER_TABLE} WHERE note = '{MARKER_NOTE}';")
        if MARKER_NOTE not in marker.stdout:
            raise AssertionError(
                "marker row lost across postgres restart — state was not persistent"
            )
        print("persistence proven: marker row survived the restart")
    finally:
        compose("down", "-v", check=False)

    print("FTF-0004 docker local stack: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
