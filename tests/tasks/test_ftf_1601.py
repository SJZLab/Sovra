#!/usr/bin/env python3
"""FTF-1601 -- Postgres backup.

Runnable without a live Postgres server (the real live-database
backup/restore/RLS-survival evidence is
`tests/integration/test_postgres_backup.py`, run in CI's dedicated
`postgres-backup-integration` job -- same "no container-registry egress in
this repo's interactive dev sandbox" finding as FTF-0004/FTF-0102/FTF-0104/
FTF-R017; see that file's docstring). This suite exercises the *real*
`openssl` binary (already relied on by this repo's environment, not a new
dependency) for genuine encrypt/decrypt round trips, and injects fake
`dump_fn`/`metadata_fn` callables to exercise `create_backup()`'s/
`restore_backup()`'s own self-verification and cleanup logic without a
database. It proves:

1. `sha256_file`/`key_fingerprint` behave correctly and the fingerprint
   never leaks the key itself.
2. `_libpq_dsn` normalizes the SQLAlchemy-style DSN this repo's other
   tools use to the plain libpq URI `pg_dump`/`pg_restore`/`psql` need.
3. `required_env` never silently falls back to a default credential.
4. Real `openssl`-backed encrypt/decrypt genuinely round-trips, and
   decrypting with the wrong key is genuinely rejected (never returns
   garbage as if it were the plaintext).
5. `create_backup()`'s own negative_acceptance clause ("backup command
   success without verifiable usable data/key path is not done"): a dump
   that cannot be decrypted back to itself never produces a BackupRecord
   or leaves a claimed-successful artifact behind, and the plaintext dump
   is never left on disk either on success or on failure.
6. `restore_backup()` rejects a tampered backup file and a wrong key
   before ever attempting `pg_restore`.
7. The frozen contract files
   (`packages/contracts/{jsonschema,openapi}/16-*`) parse, declare zero
   paths (Law 11 -- no live FastAPI app exists yet), and a real
   `BackupRecord`/`RestoreRecord` instance validates against them.

Run directly: python3 tests/tasks/test_ftf_1601.py
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.postgres_backup import (  # noqa: E402
    BackupError,
    RestoreRecord,
    _libpq_dsn,
    create_backup,
    decrypt_file,
    key_fingerprint,
    required_env,
    restore_backup,
    sha256_file,
)

JSONSCHEMA_PATH = ROOT / "packages" / "contracts" / "jsonschema" / "16-domain.schema.json"
OPENAPI_PATH = ROOT / "packages" / "contracts" / "openapi" / "16-interface.yaml"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- 1. sha256_file / key_fingerprint ----------------------------------------


def test_sha256_file_matches_known_hash() -> None:
    import hashlib
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.bin"
        content = b"FTF-1601 sample content for hashing"
        path.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        check("FTF-1601", "sha256_file matches hashlib's own digest", sha256_file(path) == expected)


def test_key_fingerprint_does_not_leak_the_key_and_is_deterministic() -> None:
    key = "super-secret-backup-key-value"
    fingerprint = key_fingerprint(key)
    check("FTF-1601", "the fingerprint is not the raw key", fingerprint != key)
    check(
        "FTF-1601",
        "the fingerprint does not contain the raw key as a substring",
        key not in fingerprint,
    )
    check(
        "FTF-1601",
        "the fingerprint is deterministic for the same key",
        key_fingerprint(key) == fingerprint,
    )
    check(
        "FTF-1601",
        "a different key produces a different fingerprint",
        key_fingerprint("a-completely-different-key") != fingerprint,
    )


# -- 2. _libpq_dsn normalization ---------------------------------------------


def test_libpq_dsn_strips_the_sqlalchemy_psycopg_dialect_prefix() -> None:
    check(
        "FTF-1601",
        "postgresql+psycopg:// is normalized to postgresql://",
        _libpq_dsn("postgresql+psycopg://user:pw@host:5432/db")
        == "postgresql://user:pw@host:5432/db",
    )
    check(
        "FTF-1601",
        "a plain postgresql:// DSN is passed through unchanged",
        _libpq_dsn("postgresql://user:pw@host:5432/db") == "postgresql://user:pw@host:5432/db",
    )


# -- 3. required_env never falls back --------------------------------------


def test_required_env_raises_when_unset() -> None:
    try:
        required_env("FTF_1601_DEFINITELY_UNSET_ENV_VAR")
        check("FTF-1601", "required_env raises when the variable is unset", False)
    except BackupError:
        check("FTF-1601", "required_env raises when the variable is unset", True)


# -- 4. Real openssl-backed encrypt/decrypt round trip -----------------------


def test_encrypt_decrypt_round_trips_with_the_real_key() -> None:
    import tempfile

    from scripts.postgres_backup import _encrypt_file

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        plaintext_path = tmp / "plain.sql"
        plaintext = b"-- fake pg_dump output for round-trip testing\nSELECT 1;\n"
        plaintext_path.write_bytes(plaintext)
        ciphertext_path = tmp / "plain.sql.enc"
        decrypted_path = tmp / "plain.sql.decrypted"

        _encrypt_file(plaintext_path, ciphertext_path, "correct-horse-battery-staple")
        check(
            "FTF-1601",
            "encryption produces a ciphertext that is not the plaintext bytes",
            ciphertext_path.read_bytes() != plaintext,
        )
        decrypt_file(ciphertext_path, decrypted_path, "correct-horse-battery-staple")
        check(
            "FTF-1601",
            "decrypting with the correct key reproduces the original plaintext exactly",
            decrypted_path.read_bytes() == plaintext,
        )


def test_decrypt_with_wrong_key_is_rejected() -> None:
    import tempfile

    from scripts.postgres_backup import _encrypt_file

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        plaintext_path = tmp / "plain.sql"
        plaintext = b"-- another fake dump\nSELECT 2;\n"
        plaintext_path.write_bytes(plaintext)
        ciphertext_path = tmp / "plain.sql.enc"
        decrypted_path = tmp / "plain.sql.decrypted"

        _encrypt_file(plaintext_path, ciphertext_path, "the-real-key")
        try:
            decrypt_file(ciphertext_path, decrypted_path, "a-completely-wrong-key")
            # AES-CBC with a PBKDF2-derived wrong key almost always fails
            # padding/HMAC-less integrity checks and openssl exits nonzero;
            # decrypt_file() must raise in that case. In the vanishingly
            # unlikely event it did not, the recovered bytes must still not
            # equal the real plaintext.
            check(
                "FTF-1601",
                "negative_acceptance: decrypting with the wrong key never "
                "silently reproduces the original plaintext",
                decrypted_path.read_bytes() != plaintext,
            )
        except BackupError:
            check(
                "FTF-1601",
                "negative_acceptance: decrypting with the wrong key is rejected",
                True,
            )


# -- 5. create_backup()'s self-verification and cleanup ----------------------


def _fake_metadata(dsn: str, *, database_name: str) -> dict[str, object]:
    return {
        "postgres_version": "16.13",
        "migration_head_revision": "0002_audit_event_writer",
        "tables": ["organizations", "workspaces"],
        "rls_forced_tables": ["organizations", "workspaces"],
        "rls_policies": [
            {"table": "organizations", "policy": "tenant_isolation"},
            {"table": "workspaces", "policy": "tenant_isolation"},
        ],
        "roles": ["sovra_app"],
    }


def _fake_dump(dsn: str, output_path: Path) -> None:
    output_path.write_bytes(b"-- deterministic fake pg_dump output\nSELECT 1;\n")


def test_create_backup_happy_path_produces_a_verifiable_artifact() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        record = create_backup(
            "postgresql://unused/unused",
            output_dir,
            "a-real-backup-key",
            database_name="ftf1601_fake",
            dump_fn=_fake_dump,
            metadata_fn=_fake_metadata,
        )
        ciphertext_path = output_dir / f"{record.backup_id}.sql.enc"
        metadata_path = output_dir / f"{record.backup_id}.metadata.json"
        plaintext_path = output_dir / f"{record.backup_id}.sql"

        check("FTF-1601", "the encrypted artifact exists on disk", ciphertext_path.exists())
        check("FTF-1601", "the metadata sidecar exists on disk", metadata_path.exists())
        check(
            "FTF-1601",
            "the plaintext dump is never left on disk after a successful backup",
            not plaintext_path.exists(),
        )
        on_disk = json.loads(metadata_path.read_text(encoding="utf-8"))
        check(
            "FTF-1601",
            "the metadata sidecar matches the returned BackupRecord",
            on_disk == record.to_dict(),
        )
        check(
            "FTF-1601",
            "the ciphertext's own recorded checksum matches its real bytes on disk",
            sha256_file(ciphertext_path) == record.ciphertext_sha256,
        )


def test_create_backup_rejects_an_empty_dump() -> None:
    import tempfile

    def empty_dump(dsn: str, output_path: Path) -> None:
        output_path.touch()

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        try:
            create_backup(
                "postgresql://unused/unused",
                output_dir,
                "a-real-backup-key",
                database_name="ftf1601_fake",
                dump_fn=empty_dump,
                metadata_fn=_fake_metadata,
            )
            check("FTF-1601", "a zero-byte dump is rejected, never reported as a success", False)
        except BackupError:
            check("FTF-1601", "a zero-byte dump is rejected, never reported as a success", True)
        leftover = list(output_dir.iterdir())
        check(
            "FTF-1601",
            "no artifact is left behind after a rejected empty-dump backup",
            leftover == [],
        )


def test_create_backup_self_verification_catches_a_broken_encrypt_path() -> None:
    """negative_acceptance: 'backup command success without verifiable
    usable data/key path is not done.' Simulates a broken encryption path
    by monkeypatching `_encrypt_file` to write ciphertext that does not
    actually decrypt back to the real plaintext under the same key
    (as a real bug in the encryption step would), and confirms
    `create_backup()` genuinely refuses to report success rather than
    trusting the encrypt call's exit code alone."""
    import tempfile

    import scripts.postgres_backup as backup_module

    original_encrypt = backup_module._encrypt_file

    def broken_encrypt(input_path: Path, output_path: Path, key: str) -> None:
        # Encrypts under a *different* key than the one the caller will
        # use to self-verify -- reproduces "the encrypt step silently used
        # the wrong key" without touching global state other tests share.
        original_encrypt(input_path, output_path, key + "-mutated")

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        backup_module._encrypt_file = broken_encrypt
        try:
            try:
                create_backup(
                    "postgresql://unused/unused",
                    output_dir,
                    "a-real-backup-key",
                    database_name="ftf1601_fake",
                    dump_fn=_fake_dump,
                    metadata_fn=_fake_metadata,
                )
                check(
                    "FTF-1601",
                    "fault injection: a backup whose self-verification "
                    "decrypt does not reproduce the original bytes is "
                    "genuinely rejected, not reported as a success",
                    False,
                )
            except BackupError:
                check(
                    "FTF-1601",
                    "fault injection: a backup whose self-verification "
                    "decrypt does not reproduce the original bytes is "
                    "genuinely rejected, not reported as a success",
                    True,
                )
        finally:
            backup_module._encrypt_file = original_encrypt

        leftover = list(output_dir.iterdir())
        check(
            "FTF-1601",
            "no ciphertext/metadata artifact survives a failed self-verification",
            leftover == [],
        )


# -- 6. restore_backup() rejects tampered/wrong-key input before pg_restore -


def test_restore_backup_rejects_a_tampered_backup_file() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        record = create_backup(
            "postgresql://unused/unused",
            output_dir,
            "a-real-backup-key",
            database_name="ftf1601_fake",
            dump_fn=_fake_dump,
            metadata_fn=_fake_metadata,
        )
        ciphertext_path = output_dir / f"{record.backup_id}.sql.enc"
        metadata_path = output_dir / f"{record.backup_id}.metadata.json"

        original = ciphertext_path.read_bytes()
        tampered = bytes([original[0] ^ 0xFF]) + original[1:]
        ciphertext_path.write_bytes(tampered)

        def unreachable_restore(dsn: str, dump_path: Path) -> None:
            check(
                "FTF-1601",
                "pg_restore is never invoked against an unverified backup file",
                False,
            )

        try:
            restore_backup(
                "postgresql://unused/unused",
                ciphertext_path,
                metadata_path,
                "a-real-backup-key",
                database_name="ftf1601_fake",
                restore_fn=unreachable_restore,
                metadata_fn=_fake_metadata,
            )
            check("FTF-1601", "a tampered backup file is rejected before restoring", False)
        except BackupError:
            check("FTF-1601", "a tampered backup file is rejected before restoring", True)


def test_restore_backup_rejects_the_wrong_key() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        record = create_backup(
            "postgresql://unused/unused",
            output_dir,
            "the-real-backup-key",
            database_name="ftf1601_fake",
            dump_fn=_fake_dump,
            metadata_fn=_fake_metadata,
        )
        ciphertext_path = output_dir / f"{record.backup_id}.sql.enc"
        metadata_path = output_dir / f"{record.backup_id}.metadata.json"

        def unreachable_restore(dsn: str, dump_path: Path) -> None:
            check(
                "FTF-1601",
                "pg_restore is never invoked when the decrypted content "
                "does not match its recorded checksum",
                False,
            )

        try:
            restore_backup(
                "postgresql://unused/unused",
                ciphertext_path,
                metadata_path,
                "a-completely-wrong-key",
                database_name="ftf1601_fake",
                restore_fn=unreachable_restore,
                metadata_fn=_fake_metadata,
            )
            check(
                "FTF-1601",
                "negative_acceptance: restoring with the wrong key is rejected",
                False,
            )
        except BackupError:
            check(
                "FTF-1601",
                "negative_acceptance: restoring with the wrong key is rejected",
                True,
            )


# -- 7. Frozen shared contract files (this task's own acceptance clause 1) --


def _load_jsonschema() -> dict:
    return json.loads(JSONSCHEMA_PATH.read_text(encoding="utf-8"))


def _load_openapi() -> dict:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


def test_openapi_contract_has_no_placeholder_endpoints() -> None:
    doc = _load_openapi()
    check(
        "FTF-1601",
        "16-interface.yaml parses as valid OpenAPI YAML with an openapi version",
        isinstance(doc.get("openapi"), str) and doc["openapi"].startswith("3."),
    )
    check(
        "FTF-1601",
        "negative_acceptance / Law 11: 16-interface.yaml declares zero paths "
        "-- no route exists here that isn't backed by a real handler, since "
        "no live FastAPI app exists yet",
        doc.get("paths") == {},
    )
    schemas = doc.get("components", {}).get("schemas", {})
    for entity in ("BackupRecord", "RestoreRecord"):
        check(
            "FTF-1601",
            f"16-interface.yaml's components.schemas defines {entity}, "
            "matching the JSON Schema contract rather than diverging from it",
            entity in schemas and schemas[entity].get("type") == "object",
        )


def _validate_against_schema(instance: dict, schema: dict, defs: dict) -> list[str]:
    """A tiny, stdlib-only structural validator -- deliberately not the
    third-party `jsonschema` package (same reasoning as FTF-0102's own
    test suite: adding it would itself be a second, unapproved
    new-runtime-dependency ask-first item). Checks exactly the constraint
    kinds this contract actually uses.
    """
    errors: list[str] = []
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for field in required:
        if field not in instance:
            errors.append(f"{field}: required but missing")
    for field, field_schema in props.items():
        if "$ref" in field_schema:
            ref_name = field_schema["$ref"].rsplit("/", 1)[-1]
            field_schema = defs[ref_name]
        if field not in instance:
            continue
        value = instance[field]
        expected_type = field_schema.get("type")
        if expected_type == "string" and value is not None and not isinstance(value, str):
            errors.append(f"{field}: expected string, got {type(value).__name__}")
            continue
        if expected_type == "integer" and not isinstance(value, int):
            errors.append(f"{field}: expected integer, got {type(value).__name__}")
            continue
        if expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{field}: expected boolean, got {type(value).__name__}")
            continue
        pattern = field_schema.get("pattern")
        if pattern and isinstance(value, str) and not re.fullmatch(pattern, value):
            errors.append(f"{field}: {value!r} does not match pattern {pattern!r}")
        enum = field_schema.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{field}: {value!r} not in enum {enum}")
        if field_schema.get("type") == "array" and field_schema.get("items", {}).get("$ref"):
            item_ref = field_schema["items"]["$ref"].rsplit("/", 1)[-1]
            item_schema = defs[item_ref]
            for item in value:
                errors.extend(_validate_against_schema(item, item_schema, defs))
    return errors


def test_real_backup_record_validates_against_the_frozen_contract() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        record = create_backup(
            "postgresql://unused/unused",
            output_dir,
            "a-real-backup-key",
            database_name="ftf1601_fake",
            dump_fn=_fake_dump,
            metadata_fn=_fake_metadata,
        )
        schema_doc = _load_jsonschema()
        defs = schema_doc["$defs"]
        errors = _validate_against_schema(record.to_dict(), defs["BackupRecord"], defs)
        check(
            "FTF-1601",
            f"a real, validly-constructed BackupRecord satisfies the frozen "
            f"contract schema (errors: {errors})",
            errors == [],
        )

        bad_instance = {**record.to_dict(), "cipher": "rot13"}
        errors = _validate_against_schema(bad_instance, defs["BackupRecord"], defs)
        check(
            "FTF-1601",
            "the same validator genuinely rejects an unsupported cipher "
            "against the frozen contract (not a decorative always-pass check)",
            errors != [],
        )


def test_real_restore_record_shape_validates_against_the_frozen_contract() -> None:
    schema_doc = _load_jsonschema()
    defs = schema_doc["$defs"]
    record = RestoreRecord(
        backup_id=str(uuid.uuid4()),
        database_name="ftf1601_restore_test",
        restored_at="2026-09-18T00:00:00+00:00",
        tables_restored=["organizations", "workspaces"],
        rls_forced_tables_verified=["organizations", "workspaces"],
        rls_policies_verified=[
            {"table": "organizations", "policy": "tenant_isolation"},
        ],
        row_counts={"organizations": 2},
        ciphertext_sha256_verified=True,
        plaintext_sha256_verified=True,
        restoration_validated=True,
    )
    errors = _validate_against_schema(record.to_dict(), defs["RestoreRecord"], defs)
    check(
        "FTF-1601",
        f"a real, validly-constructed RestoreRecord satisfies the frozen "
        f"contract schema (errors: {errors})",
        errors == [],
    )

    bad_instance = {**record.to_dict(), "restoration_validated": "yes"}
    errors = _validate_against_schema(bad_instance, defs["RestoreRecord"], defs)
    check(
        "FTF-1601",
        "the same validator genuinely rejects a non-boolean "
        "restoration_validated (not a decorative always-pass check)",
        errors != [],
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1601 postgres backup: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1601 postgres backup: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
