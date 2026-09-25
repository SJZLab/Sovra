#!/usr/bin/env python3
"""FTF-1503 -- CSRF suite (no-DB unit/fault tests).

Runnable without Postgres or network egress. Proves `apps/api/app/auth/
csrf_protection.py`'s own real decision logic directly (fail-closed
defaults, Origin equality, constant-time token comparison) and re-runs
`tests/security/csrf_suite.py`'s own real, live-server scenarios --
CSRF has no database dependency at all, so this file also happens to be
the complete evidence, not only a structural subset (unlike, say,
`tests/tasks/test_ftf_1501.py`'s own relationship to a suite that does
need a real Postgres cluster for its DB-layer checks).

Run directly: python3 tests/tasks/test_ftf_1503.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "security"))

from csrf_suite import run_csrf_scenarios  # noqa: E402
from fastapi import HTTPException, Request  # noqa: E402

from app.auth.csrf_protection import (  # noqa: E402
    InMemoryCsrfSessionStore,
    default_csrf_token_resolver,
    require_csrf_protection,
)

FAILURES: list[str] = []


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [FTF-1503] {description}")


def _scope(*, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "path_params": {}, "method": "POST", "headers": headers or []}


def test_default_csrf_token_resolver_is_fail_closed() -> None:
    request = Request(_scope())
    check(
        "default_csrf_token_resolver denies every request (no session reader wired yet, "
        "same disclosed gap as app.auth.rbac_middleware.default_principal_resolver)",
        default_csrf_token_resolver(request) is None,
    )


def test_require_csrf_protection_refuses_an_empty_allowed_origin() -> None:
    raised = False
    try:
        require_csrf_protection(allowed_origin="")
    except ValueError:
        raised = True
    check(
        "require_csrf_protection refuses to build a dependency with no real allowed_origin",
        raised,
    )


def _dependency_status(
    *, origin: str | None, token: str | None, resolver_value: str | None
) -> int | None:
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if token is not None:
        headers.append((b"x-csrf-token", token.encode()))
    request = Request(_scope(headers=headers))

    def resolver(_request: Request) -> str | None:
        return resolver_value

    dependency = require_csrf_protection(
        allowed_origin="https://app.sovra.example", csrf_token_resolver=resolver
    )

    async def _call() -> None:
        await dependency(request)

    try:
        asyncio.run(_call())
    except HTTPException as exc:
        return exc.status_code
    return None


def test_dependency_allows_matching_origin_and_token() -> None:
    status = _dependency_status(
        origin="https://app.sovra.example", token="real-token", resolver_value="real-token"
    )
    check(
        "require_csrf_protection's own dependency raises nothing when Origin and "
        "X-CSRF-Token both match a real resolved session token",
        status is None,
    )


def test_dependency_denies_wrong_origin() -> None:
    status = _dependency_status(
        origin="https://attacker.example", token="real-token", resolver_value="real-token"
    )
    check(
        "negative_acceptance: a foreign Origin is denied (403) even with the correct token",
        status == 403,
    )


def test_dependency_denies_missing_origin() -> None:
    status = _dependency_status(origin=None, token="real-token", resolver_value="real-token")
    check(
        "negative_acceptance: a request with no Origin header at all is denied (403)",
        status == 403,
    )


def test_dependency_denies_missing_token() -> None:
    status = _dependency_status(
        origin="https://app.sovra.example", token=None, resolver_value="real-token"
    )
    check(
        "negative_acceptance: a missing X-CSRF-Token header is denied (403) even with "
        "the correct Origin -- an ambient session cookie alone is never sufficient",
        status == 403,
    )


def test_dependency_denies_wrong_token() -> None:
    status = _dependency_status(
        origin="https://app.sovra.example", token="guessed", resolver_value="real-token"
    )
    check(
        "negative_acceptance: a wrong/garbage X-CSRF-Token value is denied (403)",
        status == 403,
    )


def test_dependency_denies_when_resolver_has_no_session() -> None:
    status = _dependency_status(
        origin="https://app.sovra.example", token="anything", resolver_value=None
    )
    check(
        "fail-closed: no resolvable session token (the default until a real "
        "resolver is wired in) denies the request (403), never silently passes",
        status == 403,
    )


def test_dependency_uses_constant_time_comparison() -> None:
    import inspect

    from app.auth import csrf_protection

    source = inspect.getsource(csrf_protection)
    check(
        "the token comparison uses secrets.compare_digest, not a plain == "
        "(constant-time, not vulnerable to a timing side channel)",
        "secrets.compare_digest" in source,
    )


def test_in_memory_store_round_trips_a_real_session() -> None:
    store = InMemoryCsrfSessionStore()
    session_id, csrf_token = store.create("user-1")
    check(
        "InMemoryCsrfSessionStore.create returns a real, distinct session id and token",
        session_id != csrf_token and len(session_id) > 10 and len(csrf_token) > 10,
    )
    check(
        "InMemoryCsrfSessionStore.subject_for resolves the real subject for a real session",
        store.subject_for(session_id) == "user-1",
    )
    check(
        "InMemoryCsrfSessionStore.csrf_token_for resolves the real token for a real session",
        store.csrf_token_for(session_id) == csrf_token,
    )
    check(
        "InMemoryCsrfSessionStore resolves nothing for an unknown session id",
        store.subject_for("not-a-real-session") is None
        and store.csrf_token_for("not-a-real-session") is None,
    )


def test_live_demo_suite_reruns_clean() -> None:
    """Re-runs `tests/security/csrf_suite.py`'s own real, live-uvicorn
    scenarios directly -- CSRF needs no database, so there is no separate
    DB-only variant of this evidence the way `tests/tasks/test_ftf_1501.py`
    needs for `tests/security/tenant_isolation_suite.py`'s own DB-layer
    checks; this is the same real evidence, callable from the no-DB gate."""
    results = run_csrf_scenarios()
    check(
        "run_csrf_scenarios returns the exact 7 real scenarios this task defines",
        len(results) == 7,
    )
    failed = [r for r in results if not r.passed]
    check(
        f"every real CSRF scenario passed against the live demonstration app (failed: "
        f"{[r.check for r in failed]})",
        not failed,
    )
    check(
        "every scenario result is content-free by construction (no request/response "
        "body recorded, only resource kind/check/method/path/status)",
        all(
            set(r.to_dict())
            == {
                "resource_kind",
                "check",
                "method",
                "path",
                "status",
                "expected_denied",
                "passed",
            }
            for r in results
        ),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1503 CSRF suite (no-DB) tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1503 CSRF suite (no-DB) tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
