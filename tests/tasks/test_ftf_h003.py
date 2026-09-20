#!/usr/bin/env python3
"""FTF-H003 -- Cross-layer tenant isolation and relational identity.

Runnable without Postgres or network egress (the real live-database,
two-real-organizations/pooled-session/real-route evidence is
tests/integration/test_ftf_h003_cross_layer_isolation.py, run in CI's
dedicated `ftf-h003-cross-layer-isolation-integration` job -- this repo's
interactive dev sandbox has no container-registry egress, same
documented finding as FTF-0004/FTF-0102/FTF-0104/FTF-0105). This suite
proves, without any database, that the three new modules
(`app.db.tenant_context`, `app.auth.resource_authorization`,
`packages.storage.tenant_policy`) genuinely enforce fail-closed behavior
and genuinely delegate to FTF-0103/0105's own already-certified guards --
never that a mocked session happens to return the right value.

Run directly: python3 tests/tasks/test_ftf_h003.py
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.storage.tenant_policy import (  # noqa: E402
    ObjectKeyPolicyError,
    build_tenant_scoped_object_key,
    validate_object_key_belongs_to_tenant,
)

from app.auth.organization_workspace_schema import Workspace  # noqa: E402
from app.auth.rbac_middleware import (  # noqa: E402
    AuthenticatedPrincipal,
    AuthorizedContext,
    Role,
)
from app.auth.resource_authorization import (  # noqa: E402
    ResourceAuthorizationError,
    authorize_resource_ownership,
)
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402

RESOURCE_AUTHORIZATION_PATH = ROOT / "apps" / "api" / "app" / "auth" / "resource_authorization.py"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- 1. TenantContext: only ever built from a real AuthorizedContext --------


def test_from_authorized_context_carries_the_real_organization_id() -> None:
    org_id = uuid.uuid4()
    authorized = AuthorizedContext(
        principal=AuthenticatedPrincipal(issuer="https://issuer.example", subject="user-1"),
        organization_id=org_id,
        role=Role.ORGANIZATION_ADMIN,
    )
    context = TenantContext.from_authorized_context(authorized)
    check(
        "FTF-H003",
        "TenantContext.from_authorized_context carries the exact real organization_id",
        context.organization_id == org_id,
    )


def test_tenant_context_rejects_a_non_uuid_organization_id() -> None:
    try:
        TenantContext(organization_id="not-a-uuid")  # type: ignore[arg-type]
        check(
            "FTF-H003",
            "TenantContext refuses a non-uuid.UUID organization_id, never silently coerces",
            False,
        )
    except TypeError:
        check(
            "FTF-H003",
            "TenantContext refuses a non-uuid.UUID organization_id, never silently coerces",
            True,
        )


def test_tenant_scoped_session_for_delegates_to_ftf_0105s_own_guard() -> None:
    org_id = uuid.uuid4()
    session = MagicMock()
    session_factory = MagicMock(return_value=session)
    context = TenantContext(organization_id=org_id)
    with tenant_scoped_session_for(session_factory, context) as scoped:
        check(
            "FTF-H003",
            "tenant_scoped_session_for yields the session FTF-0105's tenant_scoped_session opened",
            scoped is session,
        )
    check(
        "FTF-H003",
        "the same real organization_id flows through to set_tenant_context's set_config call",
        session.execute.call_args[0][1].get("org_id") == str(org_id),
    )
    check(
        "FTF-H003",
        "a successful block commits, via the underlying FTF-0105 guard",
        session.commit.called,
    )
    check(
        "FTF-H003",
        "the session is always closed, via the underlying FTF-0105 guard",
        session.close.called,
    )


# -- 2. authorize_resource_ownership: the IDOR guard ------------------------


def _mock_session_for_lookup(current_setting_value: object, first_row: object) -> MagicMock:
    session = MagicMock()
    setting_result = MagicMock()
    setting_result.scalar_one_or_none.return_value = current_setting_value
    select_result = MagicMock()
    select_result.first.return_value = first_row
    session.execute.side_effect = [setting_result, select_result]
    return session


def test_authorize_resource_ownership_rejects_non_uuid_resource_id() -> None:
    org_id = uuid.uuid4()
    context = TenantContext(organization_id=org_id)
    session = MagicMock()
    try:
        authorize_resource_ownership(
            session,
            context,
            Workspace.__table__,
            Workspace.__table__.c.id,
            Workspace.__table__.c.organization_id,
            "not-a-uuid",  # type: ignore[arg-type]
        )
        check(
            "FTF-H003",
            "a non-uuid.UUID resource_id is rejected before any session.execute call",
            False,
        )
    except ResourceAuthorizationError:
        check(
            "FTF-H003",
            "a non-uuid.UUID resource_id is rejected before any session.execute call",
            True,
        )
    check(
        "FTF-H003",
        "no query was ever issued for a malformed resource_id",
        session.execute.call_count == 0,
    )


def test_authorize_resource_ownership_allows_a_real_same_tenant_row() -> None:
    org_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    context = TenantContext(organization_id=org_id)
    session = _mock_session_for_lookup(str(org_id), (org_id,))
    raised = False
    try:
        authorize_resource_ownership(
            session,
            context,
            Workspace.__table__,
            Workspace.__table__.c.id,
            Workspace.__table__.c.organization_id,
            resource_id,
        )
    except ResourceAuthorizationError:
        raised = True
    check(
        "FTF-H003",
        "acceptance: a resource that really belongs to the authenticated tenant is authorized",
        not raised,
    )


def test_authorize_resource_ownership_denies_missing_and_foreign_tenant_identically() -> None:
    """This task's own acceptance clause 1: 'guessed IDs' must be denied,
    same as a resource that does not exist at all. The foreign-tenant case
    below is the exact IDOR vector FTF-0103's own require_role cannot see
    (it never queries the resource's real owner), and the reason this
    module exists; the two must be indistinguishable from the outside so a
    caller cannot use the response to enumerate real IDs belonging to
    another tenant."""
    org_id = uuid.uuid4()
    foreign_org_id = uuid.uuid4()

    missing_session = _mock_session_for_lookup(str(org_id), None)
    try:
        authorize_resource_ownership(
            missing_session,
            TenantContext(organization_id=org_id),
            Workspace.__table__,
            Workspace.__table__.c.id,
            Workspace.__table__.c.organization_id,
            uuid.uuid4(),
        )
        missing_message = None
    except ResourceAuthorizationError as exc:
        missing_message = str(exc)
    check(
        "FTF-H003",
        "negative_acceptance: a resource ID with no row at all is denied",
        missing_message is not None,
    )

    foreign_session = _mock_session_for_lookup(str(org_id), (foreign_org_id,))
    try:
        authorize_resource_ownership(
            foreign_session,
            TenantContext(organization_id=org_id),
            Workspace.__table__,
            Workspace.__table__.c.id,
            Workspace.__table__.c.organization_id,
            uuid.uuid4(),
        )
        foreign_message = None
    except ResourceAuthorizationError as exc:
        foreign_message = str(exc)
    check(
        "FTF-H003",
        "negative_acceptance: a guessed ID belonging to a different real organization is denied",
        foreign_message is not None,
    )
    check(
        "FTF-H003",
        "the denial is worded identically whether the resource is missing or "
        "foreign-tenant -- a caller cannot use the response to enumerate "
        "real IDs belonging to another tenant",
        foreign_message == missing_message,
    )


def test_authorize_resource_ownership_propagates_a_session_tenant_context_mismatch() -> None:
    """If the session's own live transaction context does not already
    equal context.organization_id, execute_tenant_scoped's own fail-closed
    guard refuses before the resource lookup even runs -- this must
    surface as that guard's own TenantContextError, never be silently
    caught and reinterpreted as an ordinary "not authorized" result."""
    org_id = uuid.uuid4()
    other_session_context = uuid.uuid4()
    context = TenantContext(organization_id=org_id)
    session = _mock_session_for_lookup(str(other_session_context), None)
    try:
        authorize_resource_ownership(
            session,
            context,
            Workspace.__table__,
            Workspace.__table__.c.id,
            Workspace.__table__.c.organization_id,
            uuid.uuid4(),
        )
        check(
            "FTF-H003",
            "a session whose real live tenant context disagrees with the "
            "authorized context raises, rather than silently proceeding",
            False,
        )
    except TenantContextError:
        check(
            "FTF-H003",
            "a session whose real live tenant context disagrees with the "
            "authorized context raises FTF-0105's own TenantContextError, "
            "reused unchanged rather than masked",
            True,
        )
    except ResourceAuthorizationError:
        check(
            "FTF-H003",
            "a session-context mismatch surfaces as TenantContextError, "
            "never silently downgraded to ResourceAuthorizationError",
            False,
        )


# -- 3. tenant_policy: tenant-scoped object storage key policy --------------


def test_build_tenant_scoped_object_key_produces_the_documented_shape() -> None:
    org_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    key = build_tenant_scoped_object_key(org_id, "datasets", resource_id, "train.jsonl")
    check(
        "FTF-H003",
        "build_tenant_scoped_object_key produces organizations/<org>/<type>/<id>/<filename>",
        key == f"organizations/{org_id}/datasets/{resource_id}/train.jsonl",
    )


def test_build_tenant_scoped_object_key_rejects_non_uuid_identities() -> None:
    try:
        build_tenant_scoped_object_key("not-a-uuid", "datasets", uuid.uuid4(), "f.jsonl")  # type: ignore[arg-type]
        check("FTF-H003", "a non-uuid.UUID organization_id is refused", False)
    except ObjectKeyPolicyError:
        check("FTF-H003", "a non-uuid.UUID organization_id is refused", True)


def test_build_tenant_scoped_object_key_rejects_path_traversal_segments() -> None:
    org_id, resource_id = uuid.uuid4(), uuid.uuid4()
    for bad_filename in ("../../etc/passwd", "..", "a/b", "\\windows\\system32", ""):
        try:
            build_tenant_scoped_object_key(org_id, "datasets", resource_id, bad_filename)
            check(
                "FTF-H003",
                f"an unsafe filename segment is refused: {bad_filename!r}",
                False,
            )
        except ObjectKeyPolicyError:
            check(
                "FTF-H003",
                f"an unsafe filename segment is refused: {bad_filename!r}",
                True,
            )


def test_validate_object_key_round_trips_for_the_real_owning_tenant() -> None:
    org_id, resource_id = uuid.uuid4(), uuid.uuid4()
    key = build_tenant_scoped_object_key(org_id, "datasets", resource_id, "train.jsonl")
    check(
        "FTF-H003",
        "acceptance: a key this policy built validates true for its own real owning tenant",
        validate_object_key_belongs_to_tenant(key, org_id) is True,
    )


def test_validate_object_key_denies_a_guessed_foreign_tenant_prefix() -> None:
    org_id, foreign_org_id, resource_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = build_tenant_scoped_object_key(org_id, "datasets", resource_id, "train.jsonl")
    check(
        "FTF-H003",
        "negative_acceptance: a real key is denied for a different (guessed) tenant",
        validate_object_key_belongs_to_tenant(key, foreign_org_id) is False,
    )


def test_validate_object_key_denies_path_traversal_and_malformed_keys() -> None:
    org_id = uuid.uuid4()
    bad_keys = [
        "organizations/../../etc/passwd",
        "/etc/passwd",
        f"organizations/{org_id}/datasets/../../{org_id}/x",
        "not-even-shaped-like-a-key",
        f"organizations/{org_id}/datasets",
        f"organizations/{org_id}/datasets/not-a-uuid/train.jsonl",
        "",
        "organizations\\" + str(org_id) + "\\datasets\\x\\y",
    ]
    for bad_key in bad_keys:
        check(
            "FTF-H003",
            f"a path-traversing or malformed key is denied, not raised: {bad_key!r}",
            validate_object_key_belongs_to_tenant(bad_key, org_id) is False,
        )


def test_validate_object_key_rejects_non_uuid_organization_id_argument() -> None:
    try:
        validate_object_key_belongs_to_tenant("organizations/x/datasets/y/z", "not-a-uuid")  # type: ignore[arg-type]
        check("FTF-H003", "a non-uuid.UUID organization_id argument is refused (caller bug)", False)
    except ObjectKeyPolicyError:
        check("FTF-H003", "a non-uuid.UUID organization_id argument is refused (caller bug)", True)


# -- 4. Fault injection: prove the IDOR guard is genuinely enforced ---------

_FAULT_INJECTION_PROBE = """
import sys
import uuid
sys.path.insert(0, {root!r})
sys.path.insert(0, {root!r} + "/apps/api")
from unittest.mock import MagicMock
from app.auth.resource_authorization import authorize_resource_ownership, ResourceAuthorizationError
from app.auth.organization_workspace_schema import Workspace
from app.db.tenant_context import TenantContext

org_id = uuid.uuid4()
foreign_org_id = uuid.uuid4()
context = TenantContext(organization_id=org_id)
session = MagicMock()
setting_result = MagicMock()
setting_result.scalar_one_or_none.return_value = str(org_id)
select_result = MagicMock()
select_result.first.return_value = (foreign_org_id,)
session.execute.side_effect = [setting_result, select_result]

try:
    authorize_resource_ownership(
        session, context, Workspace.__table__, Workspace.__table__.c.id,
        Workspace.__table__.c.organization_id, uuid.uuid4(),
    )
    print("AUTHORIZED_FOREIGN_TENANT_ROW")
except ResourceAuthorizationError:
    print("DENIED_FOREIGN_TENANT_ROW")
"""


def test_fault_injection_removing_the_foreign_tenant_check_is_caught() -> None:
    """Mutate the real module on disk to drop the 'row[0] !=
    context.organization_id' half of the check -- leaving only the
    'row is None' half -- then, in a fresh subprocess (never
    importlib.reload, which would silently create a second
    ResourceAuthorizationError class object), confirm the negative case
    this suite depends on (a foreign-tenant row must be denied, not just
    a missing one) genuinely flips to "authorized" once removed, then
    restore byte-identical."""
    original = RESOURCE_AUTHORIZATION_PATH.read_bytes()
    try:
        src = original.decode("utf-8")
        needle = "if row is None or row[0] != context.organization_id:"
        assert needle in src, "expected foreign-tenant check not found -- module shape changed"
        mutated = src.replace(needle, "if row is None:")
        assert mutated != src, "fault injection did not change the file"
        RESOURCE_AUTHORIZATION_PATH.write_text(mutated, encoding="utf-8")

        probe = _FAULT_INJECTION_PROBE.format(root=str(ROOT))
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-H003",
            "fault injection: removing the foreign-tenant-row check is "
            f"genuinely caught by this suite (subprocess stdout={result.stdout!r} "
            f"stderr={result.stderr!r})",
            result.returncode == 0 and "AUTHORIZED_FOREIGN_TENANT_ROW" in result.stdout,
        )
    finally:
        RESOURCE_AUTHORIZATION_PATH.write_bytes(original)
        restored = RESOURCE_AUTHORIZATION_PATH.read_bytes()
        check(
            "FTF-H003",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-H003 cross-layer tenant isolation: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H003 cross-layer tenant isolation: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
