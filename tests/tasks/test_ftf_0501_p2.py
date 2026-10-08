#!/usr/bin/env python3
"""FTF-0501-P2 -- upload-session lifecycle hardening (migration 0025), no-DB proof.

Runnable without Postgres or network egress. The real-database proof (raw
`sovra_app` and owner SQL against a live server) is
tests/integration/test_ftf_0501_p2_dataset_upload_hardening.py; this file
pins the migration itself, so a change to it that the integration job would
also catch is caught here first and a mutation of any literal is killed:

* P2-M1  revision id, parent, 32-character limit, single Alembic head;
* P2-M2  the migration changes triggers only (no table, column, grant or
         policy change, no DELETE grant);
* P2-T1  closed-session rule: the replaced freeze function keeps the
         `verified` branch (unchanged message) and refuses every other closed
         status, so the only accepted change is open -> verified | rejected |
         expired;
* P2-I1  insert guard: status must be `open`, and `created_at` must be within
         60 seconds of `now()` in BOTH directions, as a BEFORE INSERT trigger;
* P2-D1  the downgrade restores the exact 0024 function and drops the new
         trigger and function.

Run directly: python3 tests/tasks/test_ftf_0501_p2.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATIONS = ROOT / "apps" / "api" / "alembic" / "versions"
MIGRATION_PATH = MIGRATIONS / "0025_dataset_upload_hardening.py"
MIGRATION_0024 = MIGRATIONS / "0024_dataset_uploads.py"

FAILURES: list[str] = []
REV = "0025_dataset_upload_hardening"
PARENT = "0024_dataset_uploads"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _src() -> str:
    return MIGRATION_PATH.read_text()


def _offline_sql(revision_range: str, action: str) -> str:
    env = {**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check(
        "FTF-0501-P2-M1",
        f"alembic {action} {revision_range} --sql exits 0 ({result.stderr[-200:]})",
        result.returncode == 0,
    )
    return result.stdout


def _shape_findings(src: str) -> list[str]:
    """Every source-shape rule the migration must satisfy; returns the broken ones."""
    broken: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            broken.append(label)

    need("revision id", f'revision = "{REV}"' in src)
    need("parent revision", f'down_revision = "{PARENT}"' in src)
    need("revision id fits alembic_version (<=32)", len(REV) <= 32)
    need("no table is created", "create_table" not in src and "CREATE TABLE" not in src)
    need("no table is altered", "ALTER TABLE" not in src and "add_column" not in src)
    need("no grant is added", "GRANT" not in src)
    need("no policy is touched", "POLICY" not in src and "ROW LEVEL SECURITY" not in src)
    need(
        "the freeze function keeps the verified branch",
        # Two copies: the restored 0024 body (downgrade) and the 0025 body (upgrade).
        src.count("IF OLD.status = 'verified' THEN ") == 2
        and src.count("a verified upload session is immutable") == 2,
    )
    need(
        "the freeze function refuses every other closed status",
        "IF OLD.status <> 'open' THEN " in src,
    )
    need("the insert guard requires an open status", "IF NEW.status <> 'open' THEN " in src)
    need(
        "the insert guard bounds created_at below",
        "NEW.created_at < now() - interval '60 seconds'" in src,
    )
    need(
        "the insert guard bounds created_at above",
        "NEW.created_at > now() + interval '60 seconds'" in src,
    )
    need(
        "the insert guard is a BEFORE INSERT row trigger",
        "BEFORE INSERT ON dataset_upload_sessions" in src,
    )
    need("refusals are check violations", src.count("ERRCODE = 'check_violation'") == 5)
    return broken


def test_m1_revision_chain_and_single_head() -> None:
    src = _src()
    check("FTF-0501-P2-M1", f"revision is {REV} and chains onto {PARENT}", not _shape_findings(src))
    parents: dict[str, str | None] = {}
    for path in MIGRATIONS.glob("0*.py"):
        text_ = path.read_text()
        rev = re.search(r'^revision\s*=\s*"([^"]+)"', text_, re.M)
        down = re.search(r'^down_revision\s*=\s*(?:"([^"]+)"|None)', text_, re.M)
        if rev:
            parents[rev.group(1)] = down.group(1) if down else None
    heads = set(parents) - {p for p in parents.values() if p}
    chain: list[str] = []
    cursor = next(iter(heads)) if len(heads) == 1 else None
    while cursor is not None and cursor not in chain:
        chain.append(cursor)
        cursor = parents.get(cursor)
    check(
        "FTF-0501-P2-M1",
        f"exactly one Alembic head and {REV} sits directly on {PARENT} in its chain "
        f"(heads: {sorted(heads)})",
        len(heads) == 1 and REV in chain and parents.get(REV) == PARENT,
    )


def test_m2_triggers_only() -> None:
    up = _offline_sql(f"{PARENT}:{REV}", "upgrade")
    for needle in (
        "CREATE TABLE",
        "ALTER TABLE",
        "GRANT",
        "REVOKE",
        "POLICY",
        "DROP TABLE",
        "DELETE",
    ):
        check("FTF-0501-P2-M2", f"the upgrade SQL contains no {needle!r}", needle not in up)
    check(
        "FTF-0501-P2-M2",
        "the upgrade SQL ends by recording revision 0025",
        f"UPDATE alembic_version SET version_num='{REV}'" in up,
    )


def test_t1_closed_sessions_are_final() -> None:
    up = _offline_sql(f"{PARENT}:{REV}", "upgrade")
    check(
        "FTF-0501-P2-T1",
        "the freeze function is replaced, not added next to the old one",
        up.count("CREATE OR REPLACE FUNCTION dataset_upload_sessions_freeze_verified()") == 1
        and "CREATE FUNCTION dataset_upload_sessions_freeze_verified" not in up,
    )
    verified_at = up.index("IF OLD.status = 'verified' THEN")
    closed_at = up.index("IF OLD.status <> 'open' THEN")
    check(
        "FTF-0501-P2-T1",
        "the verified branch (with its own message) is checked before the closed-session branch",
        verified_at < closed_at
        and "a verified upload session is immutable" in up[verified_at:closed_at],
    )
    check(
        "FTF-0501-P2-T1",
        "the closed-session branch raises its own message and constraint name",
        "a closed upload session cannot be changed or reopened" in up
        and "tr_dataset_upload_sessions_closed_is_final" in up,
    )
    check(
        "FTF-0501-P2-T1",
        "the existing freeze trigger is reused (not dropped or re-created)",
        "CREATE TRIGGER tr_dataset_upload_sessions_freeze_verified" not in up
        and "DROP TRIGGER" not in up,
    )


def test_i1_insert_guard() -> None:
    up = _offline_sql(f"{PARENT}:{REV}", "upgrade")
    check(
        "FTF-0501-P2-I1",
        "a BEFORE INSERT row trigger runs the guard function",
        "CREATE TRIGGER tr_dataset_upload_sessions_insert_guard "
        "BEFORE INSERT ON dataset_upload_sessions "
        "FOR EACH ROW EXECUTE FUNCTION dataset_upload_sessions_insert_guard()" in up,
    )
    check(
        "FTF-0501-P2-I1",
        "the guard refuses a non-open status with its own message",
        "IF NEW.status <> 'open' THEN" in up and "a new upload session must be open" in up,
    )
    check(
        "FTF-0501-P2-I1",
        "the guard refuses created_at more than 60 seconds on EITHER side of now()",
        "IF NEW.created_at < now() - interval '60 seconds' "
        "OR NEW.created_at > now() + interval '60 seconds' THEN"
        in up
        and up.count("interval '60 seconds'") == 2
        and "created_at must be within 60 seconds of the database clock" in up,
    )
    check(
        "FTF-0501-P2-I1",
        "every refusal is a check_violation naming its trigger constraint",
        up.count("ERRCODE = 'check_violation'") == 4  # 2 in the freeze fn, 2 in the guard
        and "tr_dataset_upload_sessions_insert_guard_status" in up
        and "tr_dataset_upload_sessions_insert_guard_created_at" in up,
    )
    check(
        "FTF-0501-P2-I1",
        "the guard compares against the database clock, never a caller value",
        "now()" in up and "current_setting" not in up and "clock_timestamp" not in up,
    )


def test_d1_downgrade_restores_0024() -> None:
    down = _offline_sql(f"{REV}:{PARENT}", "downgrade")
    check(
        "FTF-0501-P2-D1",
        "the downgrade drops the insert trigger and then its function",
        "DROP TRIGGER IF EXISTS tr_dataset_upload_sessions_insert_guard ON dataset_upload_sessions"
        in down
        and "DROP FUNCTION IF EXISTS dataset_upload_sessions_insert_guard()" in down
        and down.index("DROP TRIGGER IF EXISTS tr_dataset_upload_sessions_insert_guard")
        < down.index("DROP FUNCTION IF EXISTS dataset_upload_sessions_insert_guard()"),
    )
    old_body = MIGRATION_0024.read_text()
    check(
        "FTF-0501-P2-D1",
        "the downgrade restores the 0024 freeze function (verified branch only)",
        "CREATE OR REPLACE FUNCTION dataset_upload_sessions_freeze_verified()" in down
        and "IF OLD.status = 'verified' THEN" in down
        and "<> 'open'" not in down
        and "IF OLD.status = 'verified' THEN " in old_body,
    )
    check(
        "FTF-0501-P2-D1",
        "the downgrade records revision 0024",
        f"UPDATE alembic_version SET version_num='{PARENT}'" in down,
    )


def test_fault_injection_shape_rules_catch_each_change() -> None:
    src = _src()
    check(
        "FTF-0501-P2-M1", "the unmodified migration has no shape findings", not _shape_findings(src)
    )
    faults = {
        "wrong parent revision": (
            f'down_revision = "{PARENT}"',
            'down_revision = "0023_evaluation_cases"',
        ),
        "drop the verified branch": ("IF OLD.status = 'verified' THEN ", "IF false THEN "),
        "drop the closed-session branch": ("IF OLD.status <> 'open' THEN ", "IF false THEN "),
        "drop the insert status check": ("IF NEW.status <> 'open' THEN ", "IF false THEN "),
        "drop the lower created_at bound": (
            "NEW.created_at < now() - interval '60 seconds'",
            "false",
        ),
        "drop the upper created_at bound": (
            "NEW.created_at > now() + interval '60 seconds'",
            "false",
        ),
        "make the insert trigger AFTER": ("BEFORE INSERT ON", "AFTER INSERT ON"),
        "add a grant": ('"CREATE TRIGGER', '"GRANT SELECT ON x TO y" "CREATE TRIGGER'),
        "add a table": (
            "def upgrade() -> None:",
            "def upgrade() -> None:\n    op.create_table('x')",
        ),
        "downgrade reason lost: raise a different code": (
            "ERRCODE = 'check_violation', ",
            "ERRCODE = 'raise_exception', ",
        ),
    }
    for label, (old, new) in faults.items():
        check("FTF-0501-P2-M1", f"fault {label!r}: the pattern exists in the file", old in src)
        check(
            "FTF-0501-P2-M1",
            f"fault {label!r} is caught by the shape rules",
            bool(_shape_findings(src.replace(old, new, 1))),
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0501-P2 upload hardening tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0501-P2 upload hardening tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
