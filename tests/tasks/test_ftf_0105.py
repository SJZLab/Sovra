#!/usr/bin/env python3
"""FTF-0105 -- Tenant-isolation query guards.

Runnable without Postgres or network egress (the real live-database
connection-pool-reuse/RLS evidence is
`tests/integration/test_tenant_isolation_query_guards.py`, run in CI's
dedicated `tenant-isolation-query-guards-integration` job -- this repo's
interactive dev sandbox has no container-registry egress, same
documented finding as FTF-0004/FTF-0102/FTF-0104/FTF-R017; see that
file's docstring). This suite proves, without any database, that the
guard functions themselves genuinely enforce fail-closed behavior --
never that a mocked session happens to return the right value.

Run directly: python3 tests/tasks/test_ftf_0105.py
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine  # noqa: E402

from app.auth.tenant_isolation_query_guards import (  # noqa: E402
    TenantContextError,
    _reset_dbapi_connection_tenant_context,
    execute_tenant_scoped,
    install_tenant_pool_reset_guard,
    require_tenant_context,
    tenant_scoped_session,
)

MODULE_PATH = ROOT / "apps" / "api" / "app" / "auth" / "tenant_isolation_query_guards.py"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _mock_session_returning(current_setting_value: object) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = current_setting_value
    session.execute.return_value = result
    return session


# -- 1. require_tenant_context: fail-closed on missing/malformed context ----


def test_require_tenant_context_raises_when_missing() -> None:
    session = _mock_session_returning(None)
    try:
        require_tenant_context(session)
        check("FTF-0105", "missing tenant context raises TenantContextError", False)
    except TenantContextError:
        check("FTF-0105", "missing tenant context raises TenantContextError", True)


def test_require_tenant_context_raises_on_malformed_value() -> None:
    session = _mock_session_returning("not-a-real-uuid")
    try:
        require_tenant_context(session)
        check("FTF-0105", "a malformed tenant context value raises TenantContextError", False)
    except TenantContextError:
        check("FTF-0105", "a malformed tenant context value raises TenantContextError", True)


def test_require_tenant_context_returns_the_real_uuid() -> None:
    org_id = uuid.uuid4()
    session = _mock_session_returning(str(org_id))
    result = require_tenant_context(session)
    check(
        "FTF-0105",
        "a well-formed tenant context is parsed back into the exact same uuid.UUID",
        result == org_id,
    )


def test_require_tenant_context_never_trusts_a_string_comparison() -> None:
    """The returned value must be a real uuid.UUID (so callers can compare
    it with `==` against another uuid.UUID safely), not merely a string
    that happens to look right."""
    org_id = uuid.uuid4()
    session = _mock_session_returning(str(org_id))
    result = require_tenant_context(session)
    check(
        "FTF-0105",
        "require_tenant_context returns a uuid.UUID instance",
        isinstance(result, uuid.UUID),
    )


# -- 2. execute_tenant_scoped: refuses to run the caller's own statement ----
#       at all unless the session's live context matches what was asked.


def test_execute_tenant_scoped_rejects_non_uuid_organization_id() -> None:
    session = MagicMock()
    try:
        execute_tenant_scoped(session, "not-a-uuid", "SELECT 1")  # type: ignore[arg-type]
        check(
            "FTF-0105",
            "a non-uuid.UUID organization_id is rejected before touching the session",
            False,
        )
    except TenantContextError:
        check(
            "FTF-0105",
            "a non-uuid.UUID organization_id is rejected before touching the session",
            True,
        )
    check(
        "FTF-0105",
        "rejecting a malformed organization_id never even queries the session",
        session.execute.call_count == 0,
    )


def test_execute_tenant_scoped_refuses_the_statement_on_context_mismatch() -> None:
    active_org = uuid.uuid4()
    expected_org = uuid.uuid4()
    assert active_org != expected_org
    session = _mock_session_returning(str(active_org))
    try:
        execute_tenant_scoped(session, expected_org, "SELECT * FROM workspaces")
        check(
            "FTF-0105",
            "negative_acceptance: a session whose live tenant context "
            "disagrees with the caller's expected organization refuses "
            "the raw-SQL statement outright",
            False,
        )
    except TenantContextError:
        check(
            "FTF-0105",
            "negative_acceptance: a session whose live tenant context "
            "disagrees with the caller's expected organization refuses "
            "the raw-SQL statement outright",
            True,
        )
    check(
        "FTF-0105",
        "on a context mismatch, only the current_setting check ran -- the "
        "caller's own statement was never sent to the database",
        session.execute.call_count == 1,
    )


def test_execute_tenant_scoped_runs_the_statement_when_context_matches() -> None:
    org_id = uuid.uuid4()
    session = _mock_session_returning(str(org_id))
    sentinel = object()
    session.execute.side_effect = [session.execute.return_value, sentinel]
    result = execute_tenant_scoped(session, org_id, "SELECT * FROM workspaces", {"x": 1})
    check(
        "FTF-0105",
        "a matching tenant context lets the guard actually run the caller's statement",
        result is sentinel,
    )
    check(
        "FTF-0105",
        "exactly two statements ran: the context check, then the caller's own query",
        session.execute.call_count == 2,
    )


# -- 3. tenant_scoped_session: fail-closed before a session is even opened --


def test_tenant_scoped_session_rejects_none_before_opening_a_session() -> None:
    session_factory = MagicMock()
    try:
        with tenant_scoped_session(session_factory, None):  # type: ignore[arg-type]
            pass
        check(
            "FTF-0105",
            "opening a tenant-scoped session with organization_id=None is refused",
            False,
        )
    except TenantContextError:
        check(
            "FTF-0105",
            "opening a tenant-scoped session with organization_id=None is refused",
            True,
        )
    check(
        "FTF-0105",
        "refusing a missing tenant never even calls the session factory "
        "(no connection is taken from the pool for a request with no tenant)",
        session_factory.call_count == 0,
    )


def test_tenant_scoped_session_rejects_non_uuid_organization_id() -> None:
    session_factory = MagicMock()
    try:
        with tenant_scoped_session(session_factory, "acme-corp"):  # type: ignore[arg-type]
            pass
        check(
            "FTF-0105",
            "a string organization_id (not a real uuid.UUID) is refused, " "never silently coerced",
            False,
        )
    except TenantContextError:
        check(
            "FTF-0105",
            "a string organization_id (not a real uuid.UUID) is refused, " "never silently coerced",
            True,
        )


def test_tenant_scoped_session_binds_context_and_commits_on_success() -> None:
    org_id = uuid.uuid4()
    session = MagicMock()
    session_factory = MagicMock(return_value=session)
    with tenant_scoped_session(session_factory, org_id) as scoped:
        check("FTF-0105", "the session yielded is the one the factory created", scoped is session)
    check(
        "FTF-0105",
        "set_tenant_context's own set_config(...) call ran before the body "
        "executed (the only session.execute call this test's body made)",
        session.execute.call_count == 1,
    )
    (clause, params), _ = session.execute.call_args
    check(
        "FTF-0105",
        "the bound context is a real parameterized set_config(...) call "
        "(reusing FTF-0102's own set_tenant_context, not a re-implementation)",
        "set_config(" in str(clause) and params.get("org_id") == str(org_id),
    )
    check("FTF-0105", "a successful block commits the transaction", session.commit.called)
    check("FTF-0105", "a successful block never rolls back", not session.rollback.called)
    check(
        "FTF-0105",
        "the session is always closed, returning the connection to the pool",
        session.close.called,
    )


def test_tenant_scoped_session_rolls_back_on_exception_and_still_closes() -> None:
    org_id = uuid.uuid4()
    session = MagicMock()
    session_factory = MagicMock(return_value=session)

    class _Boom(Exception):
        pass

    try:
        with tenant_scoped_session(session_factory, org_id):
            raise _Boom("body failed")
        check("FTF-0105", "an exception inside the block propagates out", False)
    except _Boom:
        check("FTF-0105", "an exception inside the block propagates out", True)

    check(
        "FTF-0105",
        "negative_acceptance: a failed unit of work rolls back rather than "
        "committing (never leaves a half-applied tenant-scoped write)",
        session.rollback.called,
    )
    check("FTF-0105", "a failed block never commits", not session.commit.called)
    check(
        "FTF-0105",
        "the session/connection is still closed even after a failure -- it "
        "is never leaked back to the pool mid-transaction",
        session.close.called,
    )


# -- 4. install_tenant_pool_reset_guard / its per-connection reset ----------


def test_reset_dbapi_connection_tenant_context_runs_parameterized_clear() -> None:
    dbapi_connection = MagicMock()
    cursor = MagicMock()
    dbapi_connection.cursor.return_value = cursor
    _reset_dbapi_connection_tenant_context(dbapi_connection)
    check(
        "FTF-0105",
        "the checkin reset issues the same set_config(...) call "
        "set_tenant_context uses, clearing rather than SET-formatting",
        cursor.execute.call_args
        == call("SELECT set_config('app.current_organization_id', '', false)"),
    )
    check("FTF-0105", "the cursor is closed after the reset", cursor.close.called)
    check(
        "FTF-0105",
        "the reset is committed, not left open on the connection returned to the pool",
        dbapi_connection.commit.called,
    )


def test_reset_dbapi_connection_tenant_context_never_raises_on_a_broken_connection() -> None:
    dbapi_connection = MagicMock()
    dbapi_connection.cursor.side_effect = RuntimeError("connection already closed")
    try:
        _reset_dbapi_connection_tenant_context(dbapi_connection)
        check(
            "FTF-0105",
            "a broken/invalidated connection's reset failure never propagates "
            "and crashes the pool checkin path",
            True,
        )
    except Exception:
        check(
            "FTF-0105",
            "a broken/invalidated connection's reset failure never propagates "
            "and crashes the pool checkin path",
            False,
        )


def test_install_tenant_pool_reset_guard_registers_a_real_checkin_listener() -> None:
    """Proves the guard is genuinely wired to the pool's real `checkin`
    lifecycle event -- not merely defined and never attached -- by
    patching the per-connection reset function the listener calls and
    observing a real checkout/checkin cycle actually invoke it."""
    import app.auth.tenant_isolation_query_guards as guard_module

    engine = create_engine("sqlite://")
    original_reset = guard_module._reset_dbapi_connection_tenant_context
    calls: list[object] = []
    guard_module._reset_dbapi_connection_tenant_context = lambda dbapi_connection: calls.append(
        dbapi_connection
    )
    try:
        install_tenant_pool_reset_guard(engine)
        conn = engine.connect()
        conn.close()  # returns the connection to the pool -- fires "checkin"
        check(
            "FTF-0105",
            "a real pool checkin genuinely invokes the registered reset "
            "(this is wiring, not a defined-but-unattached function)",
            len(calls) == 1,
        )
    finally:
        guard_module._reset_dbapi_connection_tenant_context = original_reset
        engine.dispose()


# -- 5. Real fault injection against the tracked module on disk -------------


_FAULT_INJECTION_PROBE = """
import sys, uuid
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path({root!r})
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.tenant_isolation_query_guards import execute_tenant_scoped

active_org = uuid.uuid4()
other_org = uuid.uuid4()
session = MagicMock()
result_mock = MagicMock()
result_mock.scalar_one_or_none.return_value = str(active_org)
sentinel = object()
session.execute.side_effect = [result_mock, sentinel]
result = execute_tenant_scoped(session, other_org, "SELECT 1")
print("RAN_MISMATCHED_STATEMENT" if result is sentinel else "REFUSED")
"""


def test_real_fault_injection_context_mismatch_check_is_caught() -> None:
    """Not re-reading this module's own claim: remove the mismatch check
    from `execute_tenant_scoped` on disk, run it in a fresh subprocess
    (never `importlib.reload` -- reloading this module in-process would
    silently create a second `TenantContextError` class object, breaking
    every other `except TenantContextError` in this suite via Python's
    ordinary reload-identity gotcha) to confirm the negative case that
    should be refused instead silently runs, then restore byte-identical."""
    original = MODULE_PATH.read_bytes()
    try:
        src = original.decode("utf-8")
        needle = (
            "    active = require_tenant_context(session)\n"
            "    if active != organization_id:\n"
            "        raise TenantContextError(\n"
            '            f"refusing to proceed: session tenant context is {active}, "\n'
            '            f"caller expected {organization_id} -- statement not executed"\n'
            "        )\n"
        )
        assert needle in src, "expected mismatch-check block not found -- module shape changed"
        mutated = src.replace(needle, "    require_tenant_context(session)\n")
        assert mutated != src, "fault injection did not change the file"
        MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = _FAULT_INJECTION_PROBE.format(root=str(ROOT))
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0105",
            "fault injection: removing the context-mismatch check is genuinely "
            f"caught by this suite (subprocess stdout={result.stdout!r} "
            f"stderr={result.stderr!r})",
            result.returncode == 0 and "RAN_MISMATCHED_STATEMENT" in result.stdout,
        )
    finally:
        MODULE_PATH.write_bytes(original)
        restored = MODULE_PATH.read_bytes()
        check(
            "FTF-0105",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0105 tenant-isolation query guards: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0105 tenant-isolation query guards: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
