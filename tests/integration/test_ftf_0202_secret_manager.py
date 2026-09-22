#!/usr/bin/env python3
"""FTF-0202 -- real OpenBao secret-manager integration proof.

Proves this task's acceptance ("Store/resolve/rotate short-lived secret
refs through the actual selected secret backend") and negative_acceptance
("Unknown/expired/ref-denied secrets block call and are never returned to
browser or plaintext DB") against a real, live OpenBao server -- never a
mock or an in-memory stand-in. `tests/tasks/test_ftf_0202.py` already
proves every classification/retry/merge rule against a fake transport;
this file exists to prove the real backend actually behaves the way that
fake was built to model, including one genuine surprise found only by
querying the real server directly before writing any assertion: reading a
soft-deleted or destroyed KV v2 version answers real HTTP 404 with a JSON
body still attached, not HTTP 200 with a null payload -- see
`packages/litellm_client/secret_manager_integration.py`'s own module
docstring for the full account of that correction.

No prebuilt OpenBao release binary or container image was reachable from
this repo's interactive dev sandbox (the same documented class of gap
FTF-0004/FTF-0102/FTF-R017 already established for Postgres/Docker/
SeaweedFS); this evidence was instead produced against a real OpenBao
server built directly from source (`go build` against
`github.com/openbao/openbao/v2` commit
`e98dc184344fe84ee4e69965ac3f4217d5b6a45d`, a real hosted-runner-reachable
Git remote and Go module proxy) and started as a real local `-dev` server
this test spawns and tears down itself, the same "spin up the real thing
locally" pattern `tests/tasks/test_ftf_0101.py`'s own real local OIDC
provider and `tests/integration/test_postgres_backup.py`'s own directly-
installed PostgreSQL already established for exactly this class of
resource_gate. CI's `secret-manager-integration` job builds the identical
pinned commit fresh on the hosted runner's own real network.

Requires a `bao` (or `vault`-API-compatible) binary on `PATH`, or
`SOVRA_BAO_BINARY` pointing at one directly.

Run directly:
    SOVRA_BAO_BINARY=/path/to/bao python3 tests/integration/test_ftf_0202_secret_manager.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client import secret_manager_integration as smi  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True
    except BaseException as exc:  # pragma: no cover -- surfaced via failure message
        print(f"  (unexpected exception type: {exc!r})")
        return False


ADDRESS = "http://127.0.0.1:18202"
ROOT_TOKEN = f"sovra-ci-test-{uuid.uuid4().hex}"


def _resolve_bao_binary() -> str:
    explicit = os.environ.get("SOVRA_BAO_BINARY")
    if explicit:
        return explicit
    found = shutil.which("bao")
    if found:
        return found
    print(
        "SKIP: no 'bao' binary on PATH and SOVRA_BAO_BINARY is not set -- "
        "cannot run the real OpenBao integration proof (resource_gate: "
        "BLOCKED_EXTERNAL, never silently skipped as a pass)."
    )
    sys.exit(1)


def _wait_for_health(timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{ADDRESS}/v1/sys/health", timeout=1.0) as response:  # noqa: S310
                if response.status in (200, 429, 472, 473, 501, 503):
                    return
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"OpenBao dev server never became healthy: {last_error}")


def _raw_get(url_path: str, *, token: str = ROOT_TOKEN) -> tuple[int, dict]:
    request = urllib.request.Request(f"{ADDRESS}{url_path}", method="GET")
    request.add_header("X-Vault-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, json.loads(body) if body else {}


def main() -> None:
    binary = _resolve_bao_binary()
    process = subprocess.Popen(
        [
            binary,
            "server",
            "-dev",
            f"-dev-listen-address={ADDRESS.removeprefix('http://')}",
            f"-dev-root-token-id={ROOT_TOKEN}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_health()
        _run_checks()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover -- defensive cleanup only
            process.kill()
            process.wait(timeout=10)

    if FAILURES:
        print(f"FTF-0202 real OpenBao integration: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0202 real OpenBao integration: all checks passed")
    sys.exit(0)


def _run_checks() -> None:
    config = smi.OpenBaoConfig.from_env(
        {"SOVRA_OPENBAO_ADDR": ADDRESS, "SOVRA_OPENBAO_TOKEN": ROOT_TOKEN}, require_tls=False
    )
    wrong_token_config = smi.OpenBaoConfig(address=ADDRESS, token="definitely-not-the-real-token")

    # -- store + resolve --------------------------------------------------
    ref = smi.store_secret_ref(
        config=config, mount="secret", path="litellm/real-conn", field="api_key", value="sk-real-1"
    )
    check(
        "FTF-0202",
        "store_secret_ref returns the exact openbao:// ref this module itself would parse",
        ref == "openbao://secret/litellm/real-conn/api_key",
    )
    resolved = smi.resolve_secret_ref(ref, config=config)
    check(
        "FTF-0202",
        "resolve_secret_ref returns the real value just stored, against a real live OpenBao server",
        resolved == "sk-real-1",
    )

    # -- merge onto a co-located field -------------------------------------
    ref_b = smi.store_secret_ref(
        config=config, mount="secret", path="litellm/real-conn", field="org_id", value="org-42"
    )
    check(
        "FTF-0202",
        "storing a second field at the same real path does not clobber the first field",
        smi.resolve_secret_ref(ref, config=config) == "sk-real-1"
        and smi.resolve_secret_ref(ref_b, config=config) == "org-42",
    )

    # -- rotate: new value resolves, previous real version becomes unreadable
    status_before, body_before = _raw_get("/v1/secret/data/litellm/real-conn")
    version_before = body_before["data"]["metadata"]["version"]
    smi.rotate_secret_ref(ref, config=config, new_value="sk-real-2")
    check(
        "FTF-0202",
        "resolve_secret_ref returns the real rotated value, not the pre-rotation one",
        smi.resolve_secret_ref(ref, config=config) == "sk-real-2",
    )
    check(
        "FTF-0202",
        "the co-located org_id field survives rotation of the api_key field at the same real path",
        smi.resolve_secret_ref(ref_b, config=config) == "org-42",
    )
    status_old, body_old = _raw_get(f"/v1/secret/data/litellm/real-conn?version={version_before}")
    check(
        "FTF-0202",
        "the real superseded KV v2 version is genuinely unreadable after rotation "
        f"(observed HTTP {status_old}, backend body {body_old!r})",
        status_old == 404,
    )

    # -- rotate refuses when nothing exists --------------------------------
    check(
        "FTF-0202",
        "rotate_secret_ref refuses a ref with no existing secret against the real backend",
        _raises(
            smi.SecretNotFoundError,
            smi.rotate_secret_ref,
            "openbao://secret/litellm/never-created/api_key",
            config=config,
            new_value="x",
        ),
    )

    # -- unknown ref --------------------------------------------------------
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretNotFoundError for a real unknown path -- never returned",
        _raises(
            smi.SecretNotFoundError,
            smi.resolve_secret_ref,
            "openbao://secret/litellm/does-not-exist/api_key",
            config=config,
        ),
    )

    # -- ref-denied -----------------------------------------------------
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretAccessDeniedError for a real token the backend "
        "genuinely refuses (HTTP 403) -- never returned",
        _raises(
            smi.SecretAccessDeniedError, smi.resolve_secret_ref, ref, config=wrong_token_config
        ),
    )

    # -- expired --------------------------------------------------------
    expired_ref = smi.store_secret_ref(
        config=config,
        mount="secret",
        path="litellm/real-expiring",
        field="api_key",
        value="sk-will-expire",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretExpiredError against a real recorded past expires_at "
        "-- never returned, even though the real backend would still happily serve it",
        _raises(smi.SecretExpiredError, smi.resolve_secret_ref, expired_ref, config=config),
    )

    not_yet_ref = smi.store_secret_ref(
        config=config,
        mount="secret",
        path="litellm/real-not-yet-expiring",
        field="api_key",
        value="sk-still-good",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    check(
        "FTF-0202",
        "resolve_secret_ref still returns a real value with a future expires_at",
        smi.resolve_secret_ref(not_yet_ref, config=config) == "sk-still-good",
    )

    # -- a real destroyed version is also unreadable, not just soft-deleted -
    destroy_ref = smi.store_secret_ref(
        config=config, mount="secret", path="litellm/real-destroy", field="api_key", value="v1"
    )
    _raw_post_for_test("/v1/secret/destroy/litellm/real-destroy", {"versions": [1]})
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretNotFoundError for a real hard-destroyed version too",
        _raises(smi.SecretNotFoundError, smi.resolve_secret_ref, destroy_ref, config=config),
    )


def _raw_post_for_test(url_path: str, body: dict) -> None:
    request = urllib.request.Request(
        f"{ADDRESS}{url_path}",
        data=json.dumps(body).encode(),
        method="POST",
    )
    request.add_header("X-Vault-Token", ROOT_TOKEN)
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=5.0):  # noqa: S310
        pass


if __name__ == "__main__":
    main()
