#!/usr/bin/env python3
"""FTF-1601 -- real Postgres encrypted backup/restore proof.

Proves this task's acceptance and negative_acceptance clauses against a
real, live PostgreSQL server -- never a mocked connection or an in-memory
stand-in:

- **Acceptance ("Create encrypted consistent PostgreSQL backup with
  role/schema/RLS/version metadata and restoration test")**: seeds real
  tenant data through FTF-0102's Organization/Workspace tables, takes a
  real `pg_dump`-based encrypted backup, restores it into a second, clean
  scratch database in the same cluster, and proves the restored copy
  genuinely carries the same tables, `FORCE ROW LEVEL SECURITY` state,
  `tenant_isolation` policies, referenced roles and row data -- not just a
  process exit code.
- **negative_acceptance ("backup command success without verifiable
  usable data/key path is not done")**: restoring with the wrong
  encryption key is rejected before `pg_restore` ever runs, and restoring
  a backup file that was altered after creation is rejected by its own
  checksum, proven against the real artifact this test just created.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role, which must also have `CREATEDB` -- true of the
`postgres:16-alpine` image's bootstrap superuser both locally and in CI's
`services: postgres` container) plus `SOVRA_APP_DB_PASSWORD` and
`SOVRA_BACKUP_ENCRYPTION_KEY`, matching FTF-0102/FTF-0104's own
integration tests' environment contract (see those files' docstrings for
the local-vs-CI registry-egress disclosure this task inherits unchanged).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    SOVRA_BACKUP_ENCRYPTION_KEY=<any-real-key> \\
    python3 tests/integration/test_postgres_backup.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from scripts.postgres_backup import (  # noqa: E402
    BackupError,
    create_backup,
    restore_backup,
)
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.organization_workspace_schema import Organization  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real "
            "encrypted-backup/restore evidence requires a live Postgres "
            "(migration owner connection) plus a real backup encryption "
            "key. Not silently skipped: see this file's own docstring for "
            "how to run it locally or in CI.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _run_migration() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
    )
    check(
        "FTF-1601",
        f"alembic upgrade head runs cleanly (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}",
        result.returncode == 0,
    )


def _database_name(dsn: str) -> str:
    return urlsplit(dsn.replace("postgresql+psycopg://", "postgresql://")).path.lstrip("/")


def _with_database(dsn: str, database_name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(
        (parts.scheme, parts.netloc, f"/{database_name}", parts.query, parts.fragment)
    )


def _create_scratch_database(admin_dsn: str, database_name: str) -> None:
    # A fresh CREATE DATABASE cannot run inside a psycopg transaction block;
    # use autocommit via the driver's own isolation-level setting.
    engine = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        conn.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    engine.dispose()


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    _required_env("SOVRA_APP_DB_PASSWORD")
    encryption_key = _required_env("SOVRA_BACKUP_ENCRYPTION_KEY")

    _run_migration()
    if FAILURES:
        print(f"FTF-1601 postgres backup real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    source_db = _database_name(migration_url)
    owner_engine = create_engine(migration_url)

    # -- Seed real tenant data through FTF-0102's own schema -----------------
    org_name = f"FTF-1601 Backup Test Org {uuid.uuid4().hex[:8]}"
    with Session(owner_engine) as session:
        org = Organization(name=org_name, slug=f"ftf1601-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.commit()
        org_id = org.id

    admin_dsn = _with_database(migration_url, "postgres")

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)

        # -- 1. Acceptance: a real, encrypted, self-verified backup captures -
        #       real role/schema/RLS/version metadata.
        record = create_backup(migration_url, output_dir, encryption_key, database_name=source_db)
        check(
            "FTF-1601",
            "the backup captures at least the organizations table",
            "organizations" in record.tables,
        )
        check(
            "FTF-1601",
            "the backup records FORCE ROW LEVEL SECURITY on organizations",
            "organizations" in record.rls_forced_tables,
        )
        check(
            "FTF-1601",
            "the backup records the tenant_isolation policy on organizations",
            {"table": "organizations", "policy": "tenant_isolation"} in record.rls_policies,
        )
        check(
            "FTF-1601",
            "the backup records the sovra_app role referenced by real GRANTs",
            "sovra_app" in record.roles,
        )
        check(
            "FTF-1601",
            "the backup records a real Postgres server version string",
            bool(record.postgres_version) and record.postgres_version != "unknown",
        )
        check(
            "FTF-1601",
            "the backup records the real Alembic migration head revision",
            record.migration_head_revision is not None,
        )

        ciphertext_path = output_dir / f"{record.backup_id}.sql.enc"
        metadata_path = output_dir / f"{record.backup_id}.metadata.json"
        check("FTF-1601", "the encrypted backup artifact exists on disk", ciphertext_path.exists())

        # -- 2. negative_acceptance: the wrong key is rejected before any ----
        #       pg_restore attempt against a real database.
        restore_db = f"sovra_ftf1601_restore_{uuid.uuid4().hex[:8]}"
        _create_scratch_database(admin_dsn, restore_db)
        restore_dsn = _with_database(migration_url, restore_db)
        try:
            restore_backup(
                restore_dsn,
                ciphertext_path,
                metadata_path,
                "a-completely-wrong-key",
                database_name=restore_db,
            )
            check(
                "FTF-1601",
                "negative_acceptance: restoring a real backup with the wrong key is rejected",
                False,
            )
        except BackupError:
            check(
                "FTF-1601",
                "negative_acceptance: restoring a real backup with the wrong key is rejected",
                True,
            )

        # -- 3. negative_acceptance: a tampered backup file is rejected ------
        #       before any pg_restore attempt against a real database.
        original_bytes = ciphertext_path.read_bytes()
        tampered = bytes([original_bytes[0] ^ 0xFF]) + original_bytes[1:]
        ciphertext_path.write_bytes(tampered)
        try:
            restore_backup(
                restore_dsn,
                ciphertext_path,
                metadata_path,
                encryption_key,
                database_name=restore_db,
            )
            check(
                "FTF-1601",
                "negative_acceptance: restoring a tampered real backup file is rejected",
                False,
            )
        except BackupError:
            check(
                "FTF-1601",
                "negative_acceptance: restoring a tampered real backup file is rejected",
                True,
            )
        finally:
            ciphertext_path.write_bytes(original_bytes)

        # -- 4. Acceptance: the real restoration test against a clean, ------
        #       isolated scratch database (Section 71.2).
        restore_record = restore_backup(
            restore_dsn,
            ciphertext_path,
            metadata_path,
            encryption_key,
            database_name=restore_db,
        )
        check(
            "FTF-1601",
            "acceptance: restoration is validated end-to-end (checksums, "
            "schema, RLS all verified against the real restored database)",
            restore_record.restoration_validated is True,
        )
        check(
            "FTF-1601",
            "the restored database genuinely has the organizations table",
            "organizations" in restore_record.tables_restored,
        )
        check(
            "FTF-1601",
            "the restored database genuinely still FORCEs row-level "
            "security on organizations -- RLS survives restore, not just "
            "schema/data",
            "organizations" in restore_record.rls_forced_tables_verified,
        )
        check(
            "FTF-1601",
            "the restored database's tenant_isolation policy on "
            "organizations genuinely still exists",
            {"table": "organizations", "policy": "tenant_isolation"}
            in restore_record.rls_policies_verified,
        )
        check(
            "FTF-1601",
            "the restored database genuinely carries at least the one "
            "seeded organization row (real usable data survived, not just "
            "an empty schema)",
            restore_record.row_counts.get("organizations", 0) >= 1,
        )

        # -- 5. Verify the actual seeded row content survived the round -----
        #       trip byte-for-byte, not merely a nonzero row count.
        restored_engine = create_engine(restore_dsn)
        with Session(restored_engine) as session:
            restored_org = session.execute(
                select(Organization).where(Organization.id == org_id)
            ).scalar_one_or_none()
        check(
            "FTF-1601",
            "the exact seeded organization row (by primary key) is present "
            "in the restored database with its real name intact",
            restored_org is not None and restored_org.name == org_name,
        )
        restored_engine.dispose()

    # -- Cleanup the scratch restore database --------------------------------
    _cleanup_engine = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    with _cleanup_engine.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{restore_db}"')
    _cleanup_engine.dispose()

    if FAILURES:
        print(f"FTF-1601 postgres backup real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1601 postgres backup real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
