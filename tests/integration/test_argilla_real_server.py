#!/usr/bin/env python3
"""FTF-R006-P1b round 2 -- real-Argilla-server proof (PR #968's own review: "the fake server
never checks the API key ... the real-Argilla CI job is required in this slice; there is no
P1c split").

Everything else in this task's evidence (`tests/integration/test_argilla_identity_scope.py`)
runs against `tests/tasks/argilla_test_support.py`'s own real-socket fake server -- a faithful
re-implementation of the pinned Argilla v2.8.0 `/api/v1` surface, not the actual upstream binary
(which additionally needs a live Elasticsearch/OpenSearch search engine and Redis at startup for
endpoints this task never calls -- confirmed by a real `pip install argilla-server==2.8.0`
attempt during round 1's own development). This file is the evidence that closes the one gap a
fake test double can never close by construction, however carefully written: whether the REAL
argilla-server binary genuinely enforces its own `X-Argilla-Api-Key` header the way
`packages/integrations/argilla/client.py` assumes, and whether FTF-R002's generic,
already-certified `compute_health`/`validate_component_binding` -- the "/validate" contract
every component binding shares -- genuinely detects this real server's own health transitions,
not merely a fake's.

Requires a real argilla-server reachable at ARGILLA_BASE_URL (default http://127.0.0.1:6900),
bootstrapped with a real owner user whose API key is ARGILLA_OWNER_API_KEY -- see
`.github/workflows/ci.yml`'s `argilla-real-server-integration` job, which boots the real
argilla-server, Elasticsearch and Redis as real service containers and passes both.

Run: python3 tests/integration/test_argilla_real_server.py
"""

from __future__ import annotations

import ipaddress
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.integrations.argilla.client import (  # noqa: E402
    ArgillaClient,
    ArgillaRejectedError,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

from app.component_bindings.component_binding_crud import compute_health  # noqa: E402

FAILURES: list[str] = []
CID = "R006P1b-real"


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def _raises(exc: type[BaseException], fn: Any) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception as unexpected:  # noqa: BLE001
        print(f"  (unexpected {type(unexpected).__name__}: {unexpected})", file=sys.stderr)
        return False
    return False


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set (see this file's docstring)", file=sys.stderr)
        sys.exit(1)
    return value


def _wait_for_real_server(base_url: str, timeout_seconds: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310
                f"{base_url}/api/v1/status", timeout=2.0
            ) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        time.sleep(1.0)
    raise RuntimeError(f"real argilla-server never became reachable: {last_error}")


def _policy(port: int) -> ConnectionPolicy:
    return ConnectionPolicy(
        name="real-argilla-ci",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


def main() -> None:
    base_url = os.environ.get("ARGILLA_BASE_URL", "http://127.0.0.1:6900")
    owner_api_key = _required_env("ARGILLA_OWNER_API_KEY")
    port = urlsplit(base_url).port or 6900
    policy = _policy(port)

    _wait_for_real_server(base_url)

    real_client = ArgillaClient(base_url, policy, api_key=owner_api_key, timeout=10.0)
    status = real_client.get_status()
    check(
        "the real argilla-server's own /status reports a real, non-empty version",
        isinstance(status.get("version"), str) and status["version"] != "",
    )

    real_workspaces = real_client.list_workspaces_me()
    check(
        "the real server's own /me/workspaces responds in the exact shape ArgillaClient "
        "expects, authenticated with the real owner key",
        isinstance(real_workspaces, list),
    )

    wrong_client = ArgillaClient(base_url, policy, api_key="not-the-real-owner-key", timeout=10.0)
    check(
        "the real argilla-server genuinely rejects a wrong API key on a real authenticated "
        "route -- the exact gap a fake test double cannot prove either way",
        _raises(ArgillaRejectedError, wrong_client.list_workspaces_me),
    )
    missing_client = ArgillaClient(base_url, policy, api_key=None, timeout=10.0)
    check(
        "the real argilla-server genuinely rejects a missing API key on the same route",
        _raises(ArgillaRejectedError, missing_client.list_workspaces_me),
    )

    # FTF-R002's own generic, already-certified health check (`compute_health`, the pure core
    # of `validate_component_binding`), pointed at this real server's own `/status` -- the
    # "R002 /validate" proof PR #968 asked for: every component binding shares this one
    # mechanism, and this is the evidence that it genuinely detects a real Argilla server's own
    # health, not merely the fake's.
    health_result, health_detail = compute_health(
        deployment_mode="internal_service",
        approved_endpoint=f"{base_url}/api/v1/status",
        policy=policy,
    )
    check(
        "FTF-R002's generic compute_health reports 'healthy' against the real, running "
        f"argilla-server (detail: {health_detail})",
        health_result == "healthy",
    )

    # And the negative half of the same proof: once the real server is genuinely unreachable,
    # compute_health must report 'unhealthy', never a fabricated 'healthy' (Law 17). A bad port
    # on the same loopback address stands in for "stopped" without this test owning the real
    # server's own lifecycle (the CI job's own service container does).
    dead_policy = _policy(1)
    dead_result, dead_detail = compute_health(
        deployment_mode="internal_service",
        approved_endpoint="http://127.0.0.1:1/api/v1/status",
        policy=dead_policy,
    )
    check(
        f"compute_health reports 'unhealthy' (never a fabricated 'healthy') once the real "
        f"endpoint is unreachable (detail: {dead_detail})",
        dead_result == "unhealthy",
    )

    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("All FTF-R006-P1b real-Argilla-server checks passed.")


if __name__ == "__main__":
    main()
