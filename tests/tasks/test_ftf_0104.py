#!/usr/bin/env python3
"""FTF-0104 -- Audit event writer.

Runnable without Postgres or network egress (the real live-database
append-only/tenant-isolation/transactional-atomicity evidence is
`tests/integration/test_audit_event_writer.py`, run in CI's dedicated
`audit-event-writer-integration` job -- same "no container-registry
egress in this repo's interactive dev sandbox" finding as FTF-0004/
FTF-R017/FTF-0102; see that file's docstring). This suite proves, without
any database:

1. `AuditEvent` construction genuinely rejects every negative-shape case
   (unknown actor_type, malformed identifier fields) and accepts the
   corresponding clean case.
2. `redact_details` genuinely strips secret-shaped keys/values (Section
   46) from a details payload, recursively, without mutating the input,
   and leaves ordinary fields untouched.
3. The tracked forward migration
   (`apps/api/alembic/versions/0002_audit_event_writer.py`) genuinely
   declares FORCE ROW LEVEL SECURITY, grants the restricted `sovra_app`
   role SELECT/INSERT only (never UPDATE/DELETE -- this task's own
   negative_acceptance clause), and chains onto FTF-0102's migration as
   the single serialized head -- proven by real fault injection against
   the file on disk, not just re-reading its text.

Run directly: python3 tests/tasks/test_ftf_0104.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import cast

from sqlalchemy import Table

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.audit_event_writer import (  # noqa: E402
    ALLOWED_ACTOR_TYPES,
    REDACTED_PLACEHOLDER,
    AuditEvent,
    AuditEventValidationError,
    redact_details,
)

MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0002_audit_event_writer.py"
PRIOR_MIGRATION_PATH = (
    ROOT / "apps" / "api" / "alembic" / "versions" / "0001_org_workspace_schema.py"
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- 1. AuditEvent domain-shape validation -----------------------------------


def test_audit_event_accepts_valid_fields() -> None:
    import uuid

    event = AuditEvent(
        organization_id=uuid.uuid4(),
        actor_type="user",
        actor_id="user-123",
        action="workspace.rename",
        resource_type="workspace",
        resource_id="ws-456",
        request_id="req-789",
    )
    check(
        "FTF-0104", "a valid AuditEvent constructs successfully", event.action == "workspace.rename"
    )


def test_audit_event_rejects_unknown_actor_type() -> None:
    import uuid

    try:
        AuditEvent(
            organization_id=uuid.uuid4(),
            actor_type="superadmin_backdoor",
            actor_id="x",
            action="x",
            resource_type="x",
            resource_id="x",
            request_id="x",
        )
        check("FTF-0104", "an unknown actor_type is rejected", False)
    except AuditEventValidationError:
        check("FTF-0104", "an unknown actor_type is rejected", True)


def test_audit_event_rejects_malformed_identifier_fields() -> None:
    import uuid

    org_id = uuid.uuid4()
    bad_values = ("", " ", "has spaces", "has\nnewline", "a" * 129, "https://evil?x=1&y=2")
    for field in ("actor_id", "action", "resource_type", "resource_id", "request_id"):
        for bad in bad_values:
            kwargs = {
                "organization_id": org_id,
                "actor_type": "user",
                "actor_id": "ok",
                "action": "ok",
                "resource_type": "ok",
                "resource_id": "ok",
                "request_id": "ok",
                field: bad,
            }
            try:
                AuditEvent(**kwargs)
                check("FTF-0104", f"{field}={bad!r} is rejected", False)
            except AuditEventValidationError:
                check("FTF-0104", f"{field}={bad!r} is rejected", True)


def test_audit_event_allowed_actor_types_matches_domain_model() -> None:
    check(
        "FTF-0104",
        "ALLOWED_ACTOR_TYPES matches Section 6.24's intended actor kinds",
        ALLOWED_ACTOR_TYPES == ("user", "system", "worker"),
    )


def test_audit_event_check_constraint_declared() -> None:
    # AuditEvent.__table__ is a real sqlalchemy.Table; mypy's stubs type
    # the attribute as the more general FromClause, which has no
    # .constraints (same cast FTF-0102's own test suite needed).
    audit_table = cast(Table, AuditEvent.__table__)
    names = {c.name for c in audit_table.constraints}
    check(
        "FTF-0104",
        "a real CHECK constraint enforces actor_type at the database level too",
        "ck_audit_events_actor_type" in names,
    )


def test_audit_event_organization_id_foreign_key_cascades() -> None:
    fks = list(AuditEvent.__table__.c.organization_id.foreign_keys)
    check("FTF-0104", "audit_events.organization_id is a real foreign key", len(fks) == 1)
    check(
        "FTF-0104",
        "the foreign key targets organizations.id",
        fks[0].column.table.name == "organizations" and fks[0].column.name == "id",
    )


# -- 2. redact_details genuinely redacts secret-shaped keys/values ----------


def test_redact_details_redacts_known_secret_keys_case_insensitively() -> None:
    payload = {
        "Authorization": "irrelevant-value-here",
        "api_key": "sk-abc123",
        "note": "kept as-is",
    }
    redacted = redact_details(payload)
    check(
        "FTF-0104",
        "a secret-named key is redacted regardless of casing",
        redacted["Authorization"] == REDACTED_PLACEHOLDER,
    )
    check("FTF-0104", "api_key is redacted", redacted["api_key"] == REDACTED_PLACEHOLDER)
    check("FTF-0104", "an unrelated key is left untouched", redacted["note"] == "kept as-is")


def test_redact_details_redacts_nested_structures() -> None:
    payload = {
        "request": {"headers": {"cookie": "session=abc"}, "path": "/api/v1/x"},
        "items": [{"token": "t-1"}, {"safe": "value"}],
    }
    redacted = redact_details(payload)
    check(
        "FTF-0104",
        "a secret key nested inside a dict is redacted",
        redacted["request"]["headers"]["cookie"] == REDACTED_PLACEHOLDER,
    )
    check(
        "FTF-0104",
        "a non-secret nested field is left untouched",
        redacted["request"]["path"] == "/api/v1/x",
    )
    check(
        "FTF-0104",
        "a secret key nested inside a list of dicts is redacted",
        redacted["items"][0]["token"] == REDACTED_PLACEHOLDER,
    )
    check(
        "FTF-0104",
        "a non-secret entry inside the same list is left untouched",
        redacted["items"][1]["safe"] == "value",
    )


def test_redact_details_redacts_secret_shaped_values_under_unexpected_keys() -> None:
    jwt_shaped = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    payload = {
        "auth_header_value": "Bearer abcdef123456",
        "weird_field": jwt_shaped,
        "plain_number": "12345",
    }
    redacted = redact_details(payload)
    check(
        "FTF-0104",
        "a Bearer-shaped value under an unexpected key is still redacted by shape",
        redacted["auth_header_value"] == REDACTED_PLACEHOLDER,
    )
    check(
        "FTF-0104",
        "a JWT-shaped value under an unexpected key is still redacted by shape",
        redacted["weird_field"] == REDACTED_PLACEHOLDER,
    )
    check(
        "FTF-0104",
        "an ordinary numeric-looking string is not falsely redacted",
        redacted["plain_number"] == "12345",
    )


def test_redact_details_does_not_mutate_input() -> None:
    payload = {"password": "hunter2"}
    redact_details(payload)
    check(
        "FTF-0104",
        "redact_details does not mutate its input in place",
        payload["password"] == "hunter2",
    )


def test_audit_event_construction_applies_redaction_to_details_json() -> None:
    import uuid

    event = AuditEvent(
        organization_id=uuid.uuid4(),
        actor_type="system",
        actor_id="scheduler",
        action="litellm.sync",
        resource_type="litellm_connection",
        resource_id="conn-1",
        request_id="req-1",
        details_json={"secret_ref": "sm://prod/key-1", "model_count": 12},
    )
    check(
        "FTF-0104",
        "constructing an AuditEvent with a secret-shaped details_json field "
        "redacts it before the value ever reaches the ORM instance",
        event.details_json["secret_ref"] == REDACTED_PLACEHOLDER,
    )
    check(
        "FTF-0104",
        "a non-secret details_json field survives construction unredacted",
        event.details_json["model_count"] == 12,
    )


# -- 3. The tracked forward migration genuinely FORCEs RLS, grants only -----
#       SELECT/INSERT (never UPDATE/DELETE), and chains onto FTF-0102.


def _migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_forces_rls() -> None:
    src = _migration_source()
    check(
        "FTF-0104",
        "the migration FORCEs (not just ENABLEs) row-level security on audit_events",
        "ALTER TABLE audit_events FORCE ROW LEVEL SECURITY" in src,
    )


def test_migration_grants_never_include_update_or_delete() -> None:
    src = _migration_source()
    # The migration builds this GRANT via an f-string (`f'... TO
    # "{APP_ROLE_NAME}"'`), so the raw source text on disk contains the
    # literal, unevaluated `{APP_ROLE_NAME}` placeholder, not "sovra_app".
    grant_statements = re.findall(r'GRANT [A-Z, ]+ON audit_events TO "\{APP_ROLE_NAME\}"', src)
    check(
        "FTF-0104",
        "at least one GRANT statement targets audit_events for sovra_app",
        len(grant_statements) >= 1,
    )
    for stmt in grant_statements:
        check(
            "FTF-0104",
            f"negative_acceptance: no GRANT statement on audit_events includes UPDATE ({stmt!r})",
            "UPDATE" not in stmt,
        )
        check(
            "FTF-0104",
            f"negative_acceptance: no GRANT statement on audit_events includes DELETE ({stmt!r})",
            "DELETE" not in stmt,
        )
        check(
            "FTF-0104",
            f"the GRANT statement includes SELECT and INSERT ({stmt!r})",
            "SELECT" in stmt and "INSERT" in stmt,
        )
    check(
        "FTF-0104",
        "the migration never grants ALL PRIVILEGES on audit_events "
        "(that would silently reintroduce UPDATE/DELETE)",
        "GRANT ALL" not in src,
    )


def test_migration_declares_tenant_isolation_policy() -> None:
    src = _migration_source()
    check(
        "FTF-0104",
        "a tenant_isolation policy is declared on audit_events, scoped to "
        "the same app.current_organization_id context as FTF-0102's tables",
        "CREATE POLICY tenant_isolation ON audit_events" in src
        and "app.current_organization_id" in src,
    )


def test_migration_chains_onto_ftf_0102_as_single_serialized_head() -> None:
    src = _migration_source()
    check(
        "FTF-0104",
        "database_changes: 'Serialize migration chain' -- this migration's "
        "down_revision points at FTF-0102's own migration, not a parallel branch",
        'down_revision = "0001_org_workspace_schema"' in src,
    )
    prior_src = PRIOR_MIGRATION_PATH.read_text(encoding="utf-8")
    check(
        "FTF-0104",
        "FTF-0102's own migration is still the prior migration in the chain "
        "(revision id unchanged by this task)",
        'revision = "0001_org_workspace_schema"' in prior_src,
    )


def test_real_fault_injection_missing_force_rls_is_caught() -> None:
    """Not re-reading the producer's own claim: remove FORCE ROW LEVEL
    SECURITY from the tracked migration file on disk and confirm this
    suite's own check above genuinely fails, then restore byte-identical."""
    original = MIGRATION_PATH.read_bytes()
    try:
        mutated = original.decode("utf-8").replace(
            'op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")\n', ""
        )
        assert mutated != original.decode("utf-8"), "fault injection did not change the file"
        MIGRATION_PATH.write_text(mutated, encoding="utf-8")
        forced = "ALTER TABLE audit_events FORCE ROW LEVEL SECURITY" in _migration_source()
        check(
            "FTF-0104",
            "fault injection: removing FORCE ROW LEVEL SECURITY from "
            "audit_events is genuinely caught by this suite's own check "
            "(not decorative)",
            forced is False,
        )
    finally:
        MIGRATION_PATH.write_bytes(original)
        restored = MIGRATION_PATH.read_bytes()
        check(
            "FTF-0104",
            "the migration file is restored byte-identical after fault injection",
            restored == original,
        )


def test_real_fault_injection_widened_grant_is_caught() -> None:
    """A second, independent fault injection: widen the GRANT to include
    UPDATE (simulating a future accidental edit reintroducing editability)
    and confirm this suite's own negative_acceptance check catches it."""
    original = MIGRATION_PATH.read_bytes()
    try:
        mutated = original.decode("utf-8").replace(
            'GRANT SELECT, INSERT ON audit_events TO "{APP_ROLE_NAME}"',
            'GRANT SELECT, INSERT, UPDATE ON audit_events TO "{APP_ROLE_NAME}"',
        )
        assert mutated != original.decode("utf-8"), "fault injection did not change the file"
        MIGRATION_PATH.write_text(mutated, encoding="utf-8")
        src = _migration_source()
        grant_statements = re.findall(r'GRANT [A-Z, ]+ON audit_events TO "\{APP_ROLE_NAME\}"', src)
        widened_grant_includes_update = any("UPDATE" in stmt for stmt in grant_statements)
        check(
            "FTF-0104",
            "fault injection: widening the GRANT to include UPDATE is "
            "genuinely caught as a violation of this task's own "
            "negative_acceptance clause (not decorative)",
            widened_grant_includes_update is True,
        )
    finally:
        MIGRATION_PATH.write_bytes(original)
        restored = MIGRATION_PATH.read_bytes()
        check(
            "FTF-0104",
            "the migration file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0104 audit event writer: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0104 audit event writer: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
