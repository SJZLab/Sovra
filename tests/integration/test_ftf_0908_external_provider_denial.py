#!/usr/bin/env python3
"""FTF-0908 -- real Postgres proof that an external-provider denial is audited durably.

Round 2 (QUEUE 0m item 1). `authorize_external_provider` raises
`ExternalProviderDeniedError` after writing its denial audit row. Under
FTF-0105's `tenant_scoped_session` that propagated exception rolls the
transaction back, so a denial audit that is only `session.add()`ed vanishes
(0 rows on real Postgres) while the unit tests -- which use a recording fake
session -- stay green. This test runs the real default code on a real
`sovra_app` session through the real `tenant_scoped_session` and asserts:

* a denial persists exactly one content-free `external_provider.denied` row,
  even though the error propagates out of the tenant-scoped session;
* each denial code (profile blocked, not approved, purpose, incomplete,
  mismatch) is durable;
* an allowed decision persists no row;
* a cross-tenant AuditContext (org2's id inside org1's session) is rejected
  by row-level security -- nothing is written for either tenant and the
  route is never allowed;
* org2 cannot read org1's denial row (RLS read isolation);
* the row carries the AuditContext's organization, actor and request.

Round 3->4 (QUEUE 0m, 2026-10-09T10:26Z return): round 2/3's design
committed the denial audit directly on the caller's own tenant-scoped
session, which ends that session's transaction early -- discarding its
`SET LOCAL` tenant context and sweeping in whatever the caller had not yet
committed. This file additionally proves, on real Postgres:

* two denials inside one `tenant_scoped_session` both raise the typed
  `ExternalProviderDeniedError` (not a raw row-level-security error from a
  lost tenant context), both persist their own durable row, and the
  caller's own session can still read its own tenant's rows afterward;
* a denial never commits the caller's own pending (not-yet-committed) row
  in the same session -- it is rolled back exactly as if no denial had
  ever touched that session.

Requires a live Postgres at `SOVRA_DATABASE_URL` (migration owner) plus
`SOVRA_APP_DB_PASSWORD`, like every prior integration test. Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<owner>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0908_external_provider_denial.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.security.external_provider_denial import (  # noqa: E402
    AuditContext,
    ExternalProviderDecision,
    ExternalProviderDeniedError,
    ExternalProviderRequest,
    ProviderApproval,
    authorize_external_provider,
)
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.audit_event_writer import AuditEvent  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.tenant_isolation_query_guards import tenant_scoped_session  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- the durable-denial-audit evidence "
            "requires a live Postgres (migration owner connection). Not silently skipped.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _approval(**overrides: object) -> ProviderApproval:
    fields: dict[str, object] = {
        "provider": "acme",
        "purposes": frozenset({"training", "inference"}),
        "approved_by": "approver",
        "account": "acct",
        "region": "eu",
        "retention_terms": "30d",
        "training_terms": "no-train-on-data",
        "inference_terms": "no-retain",
        "applicable_agreement": "dpa-2026",
        "subprocessors": "listed-in-dpa",
        "deletion_behaviour": "deleted-30d",
    }
    fields.update(overrides)
    return ProviderApproval(**fields)  # type: ignore[arg-type]


def _request(
    purpose: str = "training", profile: str = "standard", region: str = "eu"
) -> ExternalProviderRequest:
    return ExternalProviderRequest("acme", purpose, profile, "standard", None, None, "acct", region)


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    env = dict(os.environ)
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    check(
        "FTF-0908-R2-1",
        f"alembic upgrade head runs cleanly (exit {upgrade.returncode})\n{upgrade.stderr}",
        upgrade.returncode == 0,
    )
    if FAILURES:
        _finish()

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0908", slug=f"acme-0908-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0908", slug=f"globex-0908-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id

    def factory() -> Session:
        return Session(app_engine)

    def denied_rows(request_id: str) -> list[AuditEvent]:
        with Session(owner_engine) as owner:
            return list(
                owner.execute(
                    select(AuditEvent).where(AuditEvent.request_id == request_id)
                ).scalars()
            )

    def run_denial(
        request_id: str,
        request: ExternalProviderRequest,
        approval: ProviderApproval | None,
        *,
        context_org: uuid.UUID | None = None,
        session_org: uuid.UUID | None = None,
    ) -> tuple[str, str | None]:
        """Run under the real tenant_scoped_session; return (outcome, code)."""
        try:
            with tenant_scoped_session(factory, session_org or org1_id) as session:
                decision = authorize_external_provider(
                    request,
                    approval,
                    AuditContext(
                        session, context_org or org1_id, "system", "actor-0908", request_id
                    ),
                )
                assert isinstance(decision, ExternalProviderDecision)
                return ("allowed", None)
        except ExternalProviderDeniedError as exc:
            return ("denied", exc.code)
        except DBAPIError as exc:
            return ("db-error", str(exc.orig))

    # -- 1. A denial persists one content-free row although the error propagates.
    cases = (
        (
            "profile_blocked",
            _request(profile="zero_egress"),
            _approval(),
            "external_provider_profile_blocked",
        ),
        ("not_approved", _request(), None, "external_provider_not_approved"),
        (
            "purpose",
            _request("inference"),
            _approval(purposes=frozenset({"training"})),
            "external_provider_purpose_not_approved",
        ),
        ("incomplete", _request(), _approval(region=None), "external_provider_approval_incomplete"),
        ("mismatch", _request(region="us"), _approval(), "external_provider_approval_mismatch"),
    )
    for label, request, approval, expected in cases:
        request_id = f"req-{label}-{uuid.uuid4().hex[:8]}"
        outcome, code = run_denial(request_id, request, approval)
        check(
            "FTF-0908-R2-1",
            f"{label}: denied with {expected}",
            (outcome, code) == ("denied", expected),
        )
        rows = denied_rows(request_id)
        check(
            "FTF-0908-R2-1",
            f"{label}: exactly one audit row persisted although the error propagated "
            f"(got {len(rows)})",
            len(rows) == 1,
        )
        if len(rows) != 1:
            continue
        row = rows[0]
        check("FTF-0908-R2-3", f"{label}: organization_id", row.organization_id == org1_id)
        check(
            "FTF-0908-R2-3",
            f"{label}: actor and request identity",
            (row.actor_id, row.actor_type) == ("actor-0908", "system"),
        )
        check("FTF-0908-R2-1", f"{label}: action", row.action == "external_provider.denied")
        check(
            "FTF-0908-R2-1",
            f"{label}: details are exactly the content-free four keys",
            set(row.details_json) == {"profile", "surface", "purpose", "code"}
            and row.details_json["code"] == expected
            and "acme" not in str(row.details_json)
            and "acct" not in str(row.details_json),
        )

    # -- 2. An allowed decision persists no row.
    allow_id = f"req-allow-{uuid.uuid4().hex[:8]}"
    outcome, _ = run_denial(allow_id, _request(), _approval())
    check("FTF-0908-R2-1", "an approved request is allowed", outcome == "allowed")
    check("FTF-0908-R2-1", "an allowed decision persists no audit row", denied_rows(allow_id) == [])

    # -- 3. Cross-tenant context: org2's id inside org1's session is rejected by RLS.
    cross_id = f"req-cross-{uuid.uuid4().hex[:8]}"
    outcome, detail = run_denial(
        cross_id, _request(profile="zero_egress"), _approval(), context_org=org2_id
    )
    check(
        "FTF-0908-R2-1",
        f"a cross-tenant AuditContext is rejected by row-level security (got {outcome}: {detail})",
        outcome == "db-error" and detail is not None and "row-level security" in detail,
    )
    check("FTF-0908-R2-1", "the cross-tenant attempt persists nothing", denied_rows(cross_id) == [])

    # -- 4. RLS read isolation: org2 cannot see org1's denial rows; org1 can.
    with tenant_scoped_session(factory, org2_id) as session:
        org2_visible = session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
        org2_sees_org1 = session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "external_provider.denied")
        ).scalar_one()
    with tenant_scoped_session(factory, org1_id) as session:
        org1_denials = session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "external_provider.denied")
        ).scalar_one()
    check(
        "FTF-0908-R2-1",
        "org2 cannot read org1's denial rows",
        org2_sees_org1 == 0 and org2_visible == 0,
    )
    check("FTF-0908-R2-1", "org1 reads its own five denial rows", org1_denials == len(cases))

    # -- 5. Round 3->4: two denials in one tenant_scoped_session are both typed
    # and both durable, and the caller's own session can still read its own
    # tenant's rows afterward (a lost `SET LOCAL` tenant context would instead
    # raise a raw row-level-security error on the second one and blind every
    # later read in that same session to 0 rows).
    two_ids = [f"req-two-{i}-{uuid.uuid4().hex[:8]}" for i in range(2)]
    outcomes: list[tuple[str, str | None]] = []
    with tenant_scoped_session(factory, org1_id) as session:
        for request_id in two_ids:
            try:
                decision = authorize_external_provider(
                    _request(profile="zero_egress"),
                    _approval(),
                    AuditContext(session, org1_id, "system", "actor-two", request_id),
                )
                assert isinstance(decision, ExternalProviderDecision)
                outcomes.append(("allowed", None))
            except ExternalProviderDeniedError as exc:
                outcomes.append(("denied", exc.code))
        visible_after = session.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.request_id.in_(two_ids))
        ).scalar_one()
    check(
        "FTF-0908-R3-1",
        f"two denials in one session are both typed, not a raw RLS error (got {outcomes})",
        outcomes == [("denied", "external_provider_profile_blocked")] * 2,
    )
    check(
        "FTF-0908-R3-1",
        f"the caller's own session still reads both its own rows after both denials "
        f"(got {visible_after})",
        visible_after == 2,
    )
    for request_id in two_ids:
        check(
            "FTF-0908-R3-1",
            f"{request_id}: durable although the caller's session committed neither",
            len(denied_rows(request_id)) == 1,
        )

    # -- 6. Round 3->4: a denial never commits the caller's own pending
    # (not-yet-committed) row in the same session.
    pending_id: uuid.UUID | None = None
    try:
        with tenant_scoped_session(factory, org1_id) as session:
            pending = Workspace(
                organization_id=org1_id,
                name="pending-ws-0908",
                slug=f"pending-0908-{uuid.uuid4().hex[:8]}",
            )
            session.add(pending)
            session.flush()
            pending_id = pending.id
            authorize_external_provider(
                _request(profile="zero_egress"),
                _approval(),
                AuditContext(
                    session,
                    org1_id,
                    "system",
                    "actor-pending",
                    f"req-pending-{uuid.uuid4().hex[:8]}",
                ),
            )
    except ExternalProviderDeniedError:
        pass
    with Session(owner_engine) as owner:
        found = owner.execute(
            select(Workspace).where(Workspace.id == pending_id)
        ).scalar_one_or_none()
    check(
        "FTF-0908-R3-2",
        "the caller's own pending row is not persisted by a denial (it is rolled back "
        "by the enclosing tenant_scoped_session exactly as if no denial had occurred)",
        found is None,
    )

    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0908 durable denial audit real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0908 durable denial audit real proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
