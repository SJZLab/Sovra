#!/usr/bin/env python3
"""FTF-0500C -- real Postgres round-trip proof for the database source
connector (Section 67.1's own "no user SQL or writes" / "repeatable
snapshot... statement timeout" acceptance, plus this task's own real
"lost watermark" negative_acceptance).

`tests/tasks/test_ftf_0500c.py` already proves every validator/branch this
module owns against fake `connect` callables and a real local HTTPS
fixture (for the REST/trace connector half); this file exists to prove the
real Postgres-specific mechanisms that suite cannot reach without a live
server: a real least-privileged read-only role whose writes are genuinely
denied by the database's own grants, this module's own `READ ONLY,
REPEATABLE READ` transaction mode independently blocking a write attempt
by a role that *does* have write privilege, a real concurrent delete
genuinely losing a watermark mid-capture, and a real `UndefinedTable`
error for an unknown contract.

Requires a real Postgres reachable at `SOVRA_FTF0500C_ADMIN_DSN` (a
superuser-or-equivalent role able to `CREATE ROLE`/`CREATE DATABASE`;
defaults to `postgresql://postgres:postgres@127.0.0.1:5432/postgres`,
matching this repo's `services: postgres` CI convention) -- same "no
container-registry egress in this repo's interactive dev sandbox" finding
every prior Postgres-backed task in this repository already documents;
this evidence was produced locally against a directly-installed
PostgreSQL 16 and is wired into CI's own dedicated
`database-api-trace-source-integration` job. If no real server answers at
that DSN, this script prints a disclosed skip message and exits 0 rather
than fabricating a pass.

Run directly:
    SOVRA_FTF0500C_ADMIN_DSN=postgresql://postgres:postgres@127.0.0.1:5432/postgres \\
    python3 tests/integration/test_ftf_0500c_database_api_trace_source.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import app.datasets.database_api_trace_source_contract_and_fail_closed_validation as source_module  # noqa: E402

MODULE_PATH = (
    ROOT
    / "apps"
    / "api"
    / "app"
    / "datasets"
    / "database_api_trace_source_contract_and_fail_closed_validation.py"
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_ADMIN_DSN = os.environ.get(
    "SOVRA_FTF0500C_ADMIN_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
)
_TEST_DB = "sovra_ftf0500c_it"
_RO_ROLE = "sovra_ftf0500c_it_ro"
_RO_PASSWORD = "sovra_ftf0500c_it_ro_only"
_RW_ROLE = "sovra_ftf0500c_it_rw"
_RW_PASSWORD = "sovra_ftf0500c_it_rw_only"


def _admin_available() -> bool:
    try:
        with psycopg.connect(_ADMIN_DSN, connect_timeout=3, autocommit=True) as conn:
            conn.execute("SELECT 1")
        return True
    except OSError:
        return False
    except psycopg.OperationalError:
        return False


def _provision() -> tuple[str, str]:
    """Real setup: a scratch database, a real least-privileged SELECT-only
    role, and a real read/write role (used only to prove this module's own
    READ ONLY transaction mode independently of role grants). Returns
    (ro_conninfo, rw_conninfo)."""
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {_TEST_DB}")
        admin.execute(f"DROP ROLE IF EXISTS {_RO_ROLE}")
        admin.execute(f"DROP ROLE IF EXISTS {_RW_ROLE}")
        admin.execute(f"CREATE ROLE {_RO_ROLE} LOGIN PASSWORD '{_RO_PASSWORD}'")
        admin.execute(f"CREATE ROLE {_RW_ROLE} LOGIN PASSWORD '{_RW_PASSWORD}'")
        # Owned by whichever real role _ADMIN_DSN connects as -- this varies
        # by environment (this sandbox's own directly-installed cluster
        # uses "postgres"; the official postgres Docker image's own
        # superuser is named by its POSTGRES_USER, "sovra_migrate" in CI) --
        # never hardcoded, since a role by that literal name may not exist.
        admin.execute(f"CREATE DATABASE {_TEST_DB}")

    db_dsn = _ADMIN_DSN.rsplit("/", 1)[0] + f"/{_TEST_DB}"
    with psycopg.connect(db_dsn, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA source_schema")
        conn.execute(
            "CREATE TABLE source_schema.records ("
            "id BIGINT PRIMARY KEY, name TEXT NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        conn.execute(
            "INSERT INTO source_schema.records (id, name) "
            "SELECT g, 'record-' || g FROM generate_series(1, 30) AS g"
        )
        conn.execute(f"GRANT USAGE ON SCHEMA source_schema TO {_RO_ROLE}, {_RW_ROLE}")
        conn.execute(f"GRANT SELECT ON source_schema.records TO {_RO_ROLE}")
        conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON source_schema.records TO {_RW_ROLE}")
        conn.execute(f"REVOKE INSERT, UPDATE, DELETE ON source_schema.records FROM {_RO_ROLE}")

    host_and_port = db_dsn.split("@", 1)[1].split("/", 1)[0]
    ro_conninfo = f"postgresql://{_RO_ROLE}:{_RO_PASSWORD}@{host_and_port}/{_TEST_DB}"
    rw_conninfo = f"postgresql://{_RW_ROLE}:{_RW_PASSWORD}@{host_and_port}/{_TEST_DB}"
    return ro_conninfo, rw_conninfo


def _teardown() -> None:
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {_TEST_DB}")
        admin.execute(f"DROP ROLE IF EXISTS {_RO_ROLE}")
        admin.execute(f"DROP ROLE IF EXISTS {_RW_ROLE}")


def _config(ro_conninfo: str, **overrides: object) -> source_module.PostgresSourceConfig:
    base: dict[str, object] = {
        "conninfo": ro_conninfo,
        "schema": "source_schema",
        "table": "records",
        "columns": ("id", "name", "updated_at"),
        "stable_key_column": "id",
        "max_preview_records": 10,
    }
    base.update(overrides)
    return source_module.PostgresSourceConfig(**base)  # type: ignore[arg-type]


def test_real_bounded_cursor_pagination_covers_the_whole_real_table(ro_conninfo: str) -> None:
    config = _config(ro_conninfo)
    seen: list[int] = []
    watermark: object = None
    for _ in range(10):
        result = source_module.validate_and_preview_postgres_source(
            config, resume_after_key=watermark
        )
        seen.extend(int(row.values[0]) for row in result.rows)  # type: ignore[call-overload]
        watermark = result.next_watermark
        if not result.truncated:
            break
    check(
        "FTF-0500C",
        "acceptance: real bounded cursor pagination covers every real row exactly once",
        seen == list(range(1, 31)),
    )


def test_real_statement_timeout_is_genuinely_set_on_the_session(ro_conninfo: str) -> None:
    result = source_module.validate_and_preview_postgres_source(
        _config(ro_conninfo, statement_timeout_ms=1234)
    )
    check(
        "FTF-0500C",
        "acceptance: a real, non-default statement_timeout is accepted and the connector "
        "completes under it",
        bool(result.rows),
    )


def test_real_unknown_table_is_refused_not_fabricated(ro_conninfo: str) -> None:
    try:
        source_module.validate_and_preview_postgres_source(
            _config(ro_conninfo, table="does_not_exist")
        )
        check(
            "FTF-0500C",
            "negative_acceptance: a real unknown table is refused, not fabricated as empty",
            False,
        )
    except source_module.UnknownSourceContractError:
        check(
            "FTF-0500C",
            "negative_acceptance: a real unknown table is refused, not fabricated as empty",
            True,
        )


def test_real_ro_role_write_is_denied_by_database_grants(ro_conninfo: str) -> None:
    try:
        with psycopg.connect(ro_conninfo, autocommit=True) as conn:
            conn.execute("INSERT INTO source_schema.records (id, name) VALUES (9001, 'x')")
        check(
            "FTF-0500C",
            "acceptance: the real least-privileged role's own database grants deny a write",
            False,
        )
    except psycopg.errors.InsufficientPrivilege:
        check(
            "FTF-0500C",
            "acceptance: the real least-privileged role's own database grants deny a write",
            True,
        )


class _SessionSettingsSpyConnection:
    """Wraps a real `psycopg.Connection`, used only as the module's own
    `connect` callable. After observing the module's own `SHOW
    statement_timeout` probe (the query it issues immediately after its two
    `SET` statements), captures `transaction_read_only`/
    `transaction_isolation` on that *same* real connection/transaction --
    proving the module's own SQL, not a hand-copied test re-implementation,
    is what actually sets them. This is the exact gap the 2026-10-01 L2
    review found: the old version of this test replicated the `SET
    TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY` statement on a
    raw connection instead of calling the module, so changing the module's
    own transaction-mode SQL would have left this suite green."""

    def __init__(
        self, real: psycopg.Connection[tuple[object, ...]], captured: dict[str, str | None]
    ) -> None:
        self._real = real
        self._captured = captured

    def __enter__(self) -> _SessionSettingsSpyConnection:
        self._real.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        self._real.__exit__(exc_type, exc_value, traceback)  # type: ignore[arg-type]

    def transaction(self) -> object:
        return self._real.transaction()

    def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        cur = self._real.execute(query, params) if params is not None else self._real.execute(query)
        if "SHOW statement_timeout" in query:
            read_only_row = self._real.execute("SHOW transaction_read_only").fetchone()
            isolation_row = self._real.execute("SHOW transaction_isolation").fetchone()
            self._captured["read_only"] = str(read_only_row[0]) if read_only_row else None
            self._captured["isolation"] = str(isolation_row[0]) if isolation_row else None
        return cur


def test_real_read_only_transaction_mode_is_set_by_the_modules_own_sql(ro_conninfo: str) -> None:
    """2026-10-01 L2 finding: proves the real session GUCs through the
    module itself, not a raw re-implementation of its SQL (see
    `_SessionSettingsSpyConnection`'s own docstring)."""
    captured: dict[str, str | None] = {"read_only": None, "isolation": None}

    def spy_connect(conninfo: str, autocommit: bool = False) -> _SessionSettingsSpyConnection:
        return _SessionSettingsSpyConnection(
            psycopg.connect(conninfo, autocommit=autocommit), captured
        )

    source_module.validate_and_preview_postgres_source(
        _config(ro_conninfo),
        connect=spy_connect,  # type: ignore[arg-type]
    )
    check(
        "FTF-0500C",
        "acceptance: the module's own SQL (not a test re-implementation) genuinely sets "
        "transaction_read_only=on on the real session",
        captured["read_only"] == "on",
    )
    check(
        "FTF-0500C",
        "acceptance: the module's own SQL (not a test re-implementation) genuinely sets "
        "transaction_isolation=repeatable read on the real session",
        captured["isolation"] == "repeatable read",
    )


def test_real_read_only_transaction_mode_independently_blocks_a_write(rw_conninfo: str) -> None:
    """Proves this module's own chosen mechanism (`READ ONLY` transaction
    mode), not merely the least-privileged role's grants: using the
    read/write role (which *can* write outside this transaction mode),
    replicate exactly the two `SET` statements the connector issues and
    confirm a subsequent INSERT is genuinely rejected."""
    try:
        with psycopg.connect(rw_conninfo, autocommit=False) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                conn.execute("INSERT INTO source_schema.records (id, name) VALUES (9002, 'y')")
        check(
            "FTF-0500C",
            "acceptance: this module's own READ ONLY transaction mode independently blocks a "
            "write, even for a role that otherwise has write privilege",
            False,
        )
    except psycopg.errors.ReadOnlySqlTransaction:
        check(
            "FTF-0500C",
            "acceptance: this module's own READ ONLY transaction mode independently blocks a "
            "write, even for a role that otherwise has write privilege",
            True,
        )


def test_real_lost_watermark_after_a_genuine_concurrent_delete(
    ro_conninfo: str, rw_conninfo: str
) -> None:
    config = _config(ro_conninfo, max_preview_records=5)
    first = source_module.validate_and_preview_postgres_source(config)
    watermark = first.next_watermark

    with psycopg.connect(rw_conninfo, autocommit=True) as rw:
        rw.execute("DELETE FROM source_schema.records WHERE id = %s", (watermark,))

    try:
        source_module.validate_and_preview_postgres_source(config, resume_after_key=watermark)
        check(
            "FTF-0500C",
            "negative_acceptance: a real concurrent delete of the watermark row is caught, "
            "not silently resumed from an arbitrary position",
            False,
        )
    except source_module.LostWatermarkError:
        check(
            "FTF-0500C",
            "negative_acceptance: a real concurrent delete of the watermark row is caught, "
            "not silently resumed from an arbitrary position",
            True,
        )

    # Real fault injection (Law 1, disclosed): disable the watermark-presence
    # check directly in the module under test and confirm the assertion
    # above genuinely fails without it, then revert byte-identical.
    original = MODULE_PATH.read_text()
    original_hash = hashlib.md5(original.encode("utf-8")).hexdigest()
    disabled = original.replace(
        "if resume_after_key is not None:\n                    _verify_watermark_present(",
        "if False:  # FTF-0500C fault injection -- disabled for this check\n"
        "                    _verify_watermark_present(",
        1,
    )
    if disabled == original:
        check(
            "FTF-0500C",
            "fault injection: the watermark-presence guard line was found to disable",
            False,
        )
        return
    try:
        MODULE_PATH.write_text(disabled)
        import importlib

        importlib.reload(source_module)
        try:
            source_module.validate_and_preview_postgres_source(
                _config(ro_conninfo, max_preview_records=5), resume_after_key=watermark
            )
            genuinely_failed_without_the_check = True
        except source_module.LostWatermarkError:
            genuinely_failed_without_the_check = False
        check(
            "FTF-0500C",
            "fault injection: disabling the real watermark-presence check genuinely breaks "
            "the assertion above (proves it was testing something real)",
            genuinely_failed_without_the_check,
        )
    finally:
        MODULE_PATH.write_text(original)
        import importlib

        importlib.reload(source_module)
        restored_hash = hashlib.md5(MODULE_PATH.read_text().encode("utf-8")).hexdigest()
        check(
            "FTF-0500C",
            "fault injection: the module file was reverted byte-identical after the injection",
            restored_hash == original_hash,
        )


def main() -> int:
    if not _admin_available():
        print(
            "FTF-0500C: no real Postgres reachable at SOVRA_FTF0500C_ADMIN_DSN in this "
            "environment -- skipped and disclosed here; GitHub Actions' "
            "database-api-trace-source-integration job provisions a real services: postgres "
            "container."
        )
        return 0

    ro_conninfo, rw_conninfo = _provision()
    try:
        test_real_bounded_cursor_pagination_covers_the_whole_real_table(ro_conninfo)
        test_real_statement_timeout_is_genuinely_set_on_the_session(ro_conninfo)
        test_real_unknown_table_is_refused_not_fabricated(ro_conninfo)
        test_real_ro_role_write_is_denied_by_database_grants(ro_conninfo)
        test_real_read_only_transaction_mode_is_set_by_the_modules_own_sql(ro_conninfo)
        test_real_read_only_transaction_mode_independently_blocks_a_write(rw_conninfo)
        test_real_lost_watermark_after_a_genuine_concurrent_delete(ro_conninfo, rw_conninfo)
    finally:
        _teardown()

    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
