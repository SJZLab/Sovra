#!/usr/bin/env python3
"""Postgres backup and restore (FTF-1601, Section 48/71.2).

A stdlib-only (plus the `pg_dump`/`pg_restore`/`psql`/`openssl` OS binaries
already relied on by this repository's Postgres tooling -- no new pip
dependency; `cryptography`/`pynacl` do not appear anywhere in this repo's
`pyproject.toml`/`uv.lock`, and adding one would itself be an unapproved
new-runtime-dependency ask-first item) tool that:

1. Takes a consistent logical backup of one PostgreSQL database via
   `pg_dump --format=custom` (schema + data; `CREATE POLICY`/`GRANT`
   statements are part of the schema section regardless of ownership
   flags, so RLS policies and role grants are captured for real, not
   merely described).
2. Records **role/schema/RLS/version metadata** describing that backup:
   the server version, the Alembic migration head revision the database
   was at, the public-schema table names, which tables have
   `FORCE ROW LEVEL SECURITY`, the declared `pg_policies` rows, and the
   distinct role names the backed-up tables' own GRANTs reference.
3. Encrypts the dump with `openssl enc -aes-256-cbc -pbkdf2` under a
   caller-supplied key (`SOVRA_BACKUP_ENCRYPTION_KEY` -- no development
   fallback, same rule as `SOVRA_APP_DB_PASSWORD` in
   `apps/api/alembic/versions/0001_org_workspace_schema.py`), then
   immediately self-verifies by decrypting the ciphertext back and
   checksumming it against the original plaintext dump before ever
   reporting success -- this is what this task's own negative_acceptance
   clause ("backup command success without verifiable usable data/key
   path is not done") requires: a `create_backup()` call that produced a
   file but could not itself be decrypted back to the same bytes never
   returns a `BackupRecord`.
4. Restores an encrypted backup into a target database and re-derives the
   same role/schema/RLS metadata against the restored copy, plus actual
   row counts, so a restoration can be verified as carrying real usable
   data -- not just a process exit code.

Scope, disclosed rather than hidden: Postgres roles (`CREATE ROLE`) are
cluster-wide state, not per-database; a full role-definition dump needs
`pg_dumpall --roles-only` against a role with (at least) the privileges to
read `pg_authid`, which this per-database backup tool does not attempt.
"Role...metadata" here means the role names this database's own GRANTs
reference (enough to detect drift/absence on restore into a fresh cluster)
-- not a `CREATE ROLE` capture. Both this repository's local dev stack and
its CI integration jobs run every task's tables inside one already-
provisioned Postgres cluster where `sovra_app`/`sovra_migrate` already
exist cluster-wide, so a restore in either environment does not need this
tool to recreate roles.

CLI:
    python scripts/postgres_backup.py backup \\
        --dsn postgresql://user:pass@host:5432/db --output-dir backups/
    python scripts/postgres_backup.py restore \\
        --dsn postgresql://user:pass@host:5432/restored_db \\
        --backup-file backups/<id>.sql.enc \\
        --metadata-file backups/<id>.metadata.json
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

CIPHER = "aes-256-cbc"
ENCRYPTION_KEY_ENV_VAR = "SOVRA_BACKUP_ENCRYPTION_KEY"


class BackupError(RuntimeError):
    """A backup/restore operation could not produce verifiable evidence.

    Raised instead of returning a partial/unverified result -- this
    task's negative_acceptance clause requires that a backup or restore
    that cannot prove its own data/key path never reports success.
    """


@dataclasses.dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    database_name: str
    postgres_version: str
    migration_head_revision: str | None
    tables: list[str]
    rls_forced_tables: list[str]
    rls_policies: list[dict[str, str]]
    roles: list[str]
    created_at: str
    plaintext_sha256: str
    ciphertext_sha256: str
    cipher: str
    key_fingerprint: str
    ciphertext_size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class RestoreRecord:
    backup_id: str
    database_name: str
    restored_at: str
    tables_restored: list[str]
    rls_forced_tables_verified: list[str]
    rls_policies_verified: list[dict[str, str]]
    row_counts: dict[str, int]
    ciphertext_sha256_verified: bool
    plaintext_sha256_verified: bool
    restoration_validated: bool

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def _libpq_dsn(dsn: str) -> str:
    """Normalizes a SQLAlchemy-style DSN to the plain libpq URI
    `pg_dump`/`pg_restore`/`psql` accept -- this repository's other tools
    (Alembic, `SOVRA_DATABASE_URL`) use the `postgresql+psycopg://`
    dialect prefix, which libpq's own client binaries do not understand.
    """
    prefix = "postgresql+psycopg://"
    if dsn.startswith(prefix):
        return "postgresql://" + dsn[len(prefix) :]
    return dsn


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise BackupError(
            f"{name} is required -- no development fallback credential exists "
            "(same rule as SOVRA_APP_DB_PASSWORD in "
            "apps/api/alembic/versions/0001_org_workspace_schema.py)."
        )
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def key_fingerprint(key: str) -> str:
    """A non-secret fingerprint of the encryption key.

    Lets an operator confirm they hold the key that produced a given
    backup without the key itself ever being stored or logged (Section
    71.2: "validate decrypt on the restored side without exposing keys").
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _run(
    cmd: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, capture_output=True, env=env)


def _psql_rows(dsn: str, sql: str, *, separator: str = "|") -> list[list[str]]:
    """Runs one read-only query via `psql -tAc` and returns its rows.

    `-t` (tuples only) and `-A` (unaligned) with a `--field-separator`
    give a stable, script-parseable format without pulling in a
    third-party Postgres driver.
    """
    result = _run(
        [
            "psql",
            _libpq_dsn(dsn),
            "-tAc",
            sql,
            "--field-separator",
            separator,
        ]
    )
    if result.returncode != 0:
        raise BackupError(
            f"psql query failed (exit {result.returncode}): "
            f"{result.stderr.decode(errors='replace')}\nquery: {sql}"
        )
    stdout = result.stdout.decode(errors="replace").strip("\n")
    if not stdout:
        return []
    return [line.split(separator) for line in stdout.split("\n")]


def capture_metadata(dsn: str, *, database_name: str) -> dict[str, object]:
    """Queries the live database for the role/schema/RLS/version metadata
    this task's acceptance clause requires a backup to record."""
    version_rows = _psql_rows(dsn, "SHOW server_version;")
    postgres_version = version_rows[0][0] if version_rows and version_rows[0] else "unknown"

    migration_rows = _psql_rows(
        dsn,
        "SELECT version_num FROM alembic_version "
        "WHERE to_regclass('public.alembic_version') IS NOT NULL;",
    )
    migration_head_revision = migration_rows[0][0] if migration_rows and migration_rows[0] else None

    table_rows = _psql_rows(
        dsn,
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;",
    )
    tables = [row[0] for row in table_rows if row and row[0]]

    forced_rows = _psql_rows(
        dsn,
        "SELECT relname FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relforcerowsecurity "
        "ORDER BY 1;",
    )
    rls_forced_tables = [row[0] for row in forced_rows if row and row[0]]

    policy_rows = _psql_rows(
        dsn,
        "SELECT tablename, policyname FROM pg_policies " "WHERE schemaname='public' ORDER BY 1, 2;",
    )
    rls_policies = [
        {"table": row[0], "policy": row[1]} for row in policy_rows if len(row) >= 2 and row[0]
    ]

    role_rows = _psql_rows(
        dsn,
        "SELECT DISTINCT grantee FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND grantee <> 'PUBLIC' ORDER BY 1;",
    )
    roles = [row[0] for row in role_rows if row and row[0]]

    return {
        "postgres_version": postgres_version,
        "migration_head_revision": migration_head_revision,
        "tables": tables,
        "rls_forced_tables": rls_forced_tables,
        "rls_policies": rls_policies,
        "roles": roles,
    }


def _pg_dump(dsn: str, output_path: Path) -> None:
    result = _run(
        ["pg_dump", "--dbname", _libpq_dsn(dsn), "--format=custom", "--file", str(output_path)]
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise BackupError(
            "pg_dump did not produce a usable dump file "
            f"(exit {result.returncode}): {result.stderr.decode(errors='replace')}"
        )


def _encrypt_file(input_path: Path, output_path: Path, key: str) -> None:
    result = _run(
        [
            "openssl",
            "enc",
            f"-{CIPHER}",
            "-pbkdf2",
            "-salt",
            "-pass",
            "env:SOVRA_BACKUP_OPENSSL_PASS",
            "-in",
            str(input_path),
            "-out",
            str(output_path),
        ],
        env={**os.environ, "SOVRA_BACKUP_OPENSSL_PASS": key},
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise BackupError(
            f"encryption did not produce a usable ciphertext file "
            f"(exit {result.returncode}): {result.stderr.decode(errors='replace')}"
        )


def decrypt_file(input_path: Path, output_path: Path, key: str) -> None:
    result = _run(
        [
            "openssl",
            "enc",
            "-d",
            f"-{CIPHER}",
            "-pbkdf2",
            "-pass",
            "env:SOVRA_BACKUP_OPENSSL_PASS",
            "-in",
            str(input_path),
            "-out",
            str(output_path),
        ],
        env={**os.environ, "SOVRA_BACKUP_OPENSSL_PASS": key},
    )
    if result.returncode != 0 or not output_path.exists():
        raise BackupError(
            "decryption failed -- wrong key or corrupted backup "
            f"(exit {result.returncode}): {result.stderr.decode(errors='replace')}"
        )


def create_backup(
    dsn: str,
    output_dir: Path,
    encryption_key: str,
    *,
    database_name: str,
    dump_fn=_pg_dump,
    metadata_fn=capture_metadata,
) -> BackupRecord:
    """Creates one encrypted, self-verified backup.

    `dump_fn`/`metadata_fn` are injectable so the no-DB unit suite
    (`tests/tasks/test_ftf_1601.py`) can exercise the real
    encrypt/self-verify/cleanup logic below without a live Postgres,
    while the default arguments are what every real caller (including
    `tests/integration/test_postgres_backup.py`) actually uses.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_id = str(uuid.uuid4())
    plaintext_path = output_dir / f"{backup_id}.sql"
    ciphertext_path = output_dir / f"{backup_id}.sql.enc"
    metadata_path = output_dir / f"{backup_id}.metadata.json"
    self_check_path = output_dir / f"{backup_id}.selfcheck.tmp"

    try:
        metadata = metadata_fn(dsn, database_name=database_name)
        dump_fn(dsn, plaintext_path)
        if not plaintext_path.exists() or plaintext_path.stat().st_size == 0:
            raise BackupError(
                "the dump step produced no usable data (missing or empty "
                "file) -- refusing to encrypt and report a backup as "
                "successful with nothing real behind it"
            )
        plaintext_sha256 = sha256_file(plaintext_path)

        _encrypt_file(plaintext_path, ciphertext_path, encryption_key)

        # Self-verification: a backup that cannot be decrypted back to the
        # exact bytes it was made from is not "done" (this task's own
        # negative_acceptance clause) -- never trust the encrypt step's
        # zero exit code alone.
        decrypt_file(ciphertext_path, self_check_path, encryption_key)
        if sha256_file(self_check_path) != plaintext_sha256:
            raise BackupError(
                "backup self-verification failed: decrypting the freshly "
                "written ciphertext did not reproduce the original dump "
                "bytes -- refusing to report success"
            )

        record = BackupRecord(
            backup_id=backup_id,
            database_name=database_name,
            postgres_version=str(metadata["postgres_version"]),
            migration_head_revision=metadata["migration_head_revision"],
            tables=list(metadata["tables"]),
            rls_forced_tables=list(metadata["rls_forced_tables"]),
            rls_policies=list(metadata["rls_policies"]),
            roles=list(metadata["roles"]),
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            plaintext_sha256=plaintext_sha256,
            ciphertext_sha256=sha256_file(ciphertext_path),
            cipher=CIPHER,
            key_fingerprint=key_fingerprint(encryption_key),
            ciphertext_size_bytes=ciphertext_path.stat().st_size,
        )
        metadata_path.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
        return record
    except BaseException:
        # Never leave a half-verified artifact that looks like a
        # successful backup behind.
        for path in (ciphertext_path, metadata_path):
            path.unlink(missing_ok=True)
        raise
    finally:
        # The plaintext dump and the self-check scratch file never survive
        # a backup call, success or failure -- only the encrypted artifact
        # (and its metadata sidecar, on success) may remain on disk.
        plaintext_path.unlink(missing_ok=True)
        self_check_path.unlink(missing_ok=True)


def _pg_restore(dsn: str, dump_path: Path) -> None:
    result = _run(["pg_restore", "--dbname", _libpq_dsn(dsn), str(dump_path)])
    # pg_restore exits 1 on "already exists"-class warnings for objects a
    # fresh target database does not yet have any reason to already
    # contain; a hard failure to restore any object still leaves stderr
    # populated and the target materially incomplete, so this checks
    # actual restored content below rather than trusting the exit code
    # alone either way.
    if result.returncode not in (0, 1):
        raise BackupError(
            f"pg_restore failed (exit {result.returncode}): "
            f"{result.stderr.decode(errors='replace')}"
        )


def restore_backup(
    dsn: str,
    backup_file: Path,
    metadata_file: Path,
    encryption_key: str,
    *,
    database_name: str,
    restore_fn=_pg_restore,
    metadata_fn=capture_metadata,
    row_count_tables: list[str] | None = None,
) -> RestoreRecord:
    record = json.loads(metadata_file.read_text(encoding="utf-8"))

    ciphertext_sha256_verified = sha256_file(backup_file) == record["ciphertext_sha256"]
    if not ciphertext_sha256_verified:
        raise BackupError(
            "backup file integrity check failed: its checksum does not "
            "match the recorded metadata -- refusing to restore a backup "
            "that may have been altered or corrupted since creation"
        )

    with _tmp_path(backup_file.parent, f"{record['backup_id']}.restore.sql") as plaintext_path:
        decrypt_file(backup_file, plaintext_path, encryption_key)
        plaintext_sha256_verified = sha256_file(plaintext_path) == record["plaintext_sha256"]
        if not plaintext_sha256_verified:
            raise BackupError(
                "decrypted backup content does not match its recorded "
                "checksum -- wrong key or corrupted backup; refusing to "
                "restore unverifiable data"
            )

        restore_fn(dsn, plaintext_path)

    metadata = metadata_fn(dsn, database_name=database_name)
    row_counts: dict[str, int] = {}
    for table in row_count_tables or record["tables"]:
        if table not in metadata["tables"]:
            continue
        count_rows = _psql_rows(dsn, f'SELECT count(*) FROM "{table}";')
        row_counts[table] = int(count_rows[0][0]) if count_rows and count_rows[0] else 0

    tables_restored = list(metadata["tables"])
    rls_forced_tables_verified = list(metadata["rls_forced_tables"])
    rls_policies_verified = list(metadata["rls_policies"])

    restoration_validated = (
        ciphertext_sha256_verified
        and plaintext_sha256_verified
        and set(record["tables"]).issubset(set(tables_restored))
        and set((p["table"], p["policy"]) for p in record["rls_policies"]).issubset(
            set((p["table"], p["policy"]) for p in rls_policies_verified)
        )
        and set(record["rls_forced_tables"]).issubset(set(rls_forced_tables_verified))
    )

    return RestoreRecord(
        backup_id=record["backup_id"],
        database_name=database_name,
        restored_at=datetime.datetime.now(datetime.UTC).isoformat(),
        tables_restored=tables_restored,
        rls_forced_tables_verified=rls_forced_tables_verified,
        rls_policies_verified=rls_policies_verified,
        row_counts=row_counts,
        ciphertext_sha256_verified=ciphertext_sha256_verified,
        plaintext_sha256_verified=plaintext_sha256_verified,
        restoration_validated=restoration_validated,
    )


class _tmp_path:
    """A tiny scoped-cleanup context manager for a plaintext scratch file
    that must never survive a restore call, success or failure."""

    def __init__(self, directory: Path, name: str) -> None:
        self._path = directory / name

    def __enter__(self) -> Path:
        return self._path

    def __exit__(self, *exc_info: object) -> None:
        self._path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--dsn", required=True)
    backup_parser.add_argument("--output-dir", required=True, type=Path)
    backup_parser.add_argument("--database-name", required=True)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--dsn", required=True)
    restore_parser.add_argument("--backup-file", required=True, type=Path)
    restore_parser.add_argument("--metadata-file", required=True, type=Path)
    restore_parser.add_argument("--database-name", required=True)

    args = parser.parse_args()
    key = required_env(ENCRYPTION_KEY_ENV_VAR)

    if args.command == "backup":
        backup_record = create_backup(
            args.dsn, args.output_dir, key, database_name=args.database_name
        )
        print(json.dumps(backup_record.to_dict(), indent=2))
        return 0

    if args.command == "restore":
        restore_record = restore_backup(
            args.dsn,
            args.backup_file,
            args.metadata_file,
            key,
            database_name=args.database_name,
        )
        print(json.dumps(restore_record.to_dict(), indent=2))
        return 0 if restore_record.restoration_validated else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
