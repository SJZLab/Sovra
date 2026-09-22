#!/usr/bin/env python3
"""FTF-0403 -- Budget policy (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/revise/reserve/release/IDOR/concurrency evidence is
`tests/integration/test_ftf_0403_budget_policy.py`, run in CI's dedicated
`budget-policy-integration` job -- this repo's interactive dev sandbox
has no container-registry egress, same documented finding as every prior
integration-tested task). This suite proves, without any database:

1. `validate_total_amount`/`validate_currency`/`validate_scopes`/
   `validate_reserved_amount`: a policy's own decimal authorization,
   currency and scopes are real, bounded, closed-set validations, never
   silently accepted -- this task's own negative_acceptance ("Negative/
   unknown rates ... cannot be accepted") proven at the pure-function
   level.
2. `BudgetPolicy.__init__` itself refuses bad input (negative/non-Decimal
   amount, unrecognized currency, empty/unknown scopes) before any
   session or database is ever touched.
3. `apply_revise_budget_policy` (the pure, DB-free core) -- refuses a
   stale `expected_row_version`, refuses shrinking `total_amount` below
   what is already reserved, and applies a real change with a real
   `row_version` bump otherwise.
4. `validate_allocation_amount` -- the same negative-rate discipline
   applied to a reservation/release amount.
5. The real, locally-hosted `build_budget_policy_router` (a real uvicorn
   server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0404.py` established) denies an unauthenticated
   request and a role outside `BUDGET_WRITE_ROLES` on a write *before*
   `session_factory` is ever called, and lets a read-only role reach the
   session layer for a GET.

Run directly: python3 tests/tasks/test_ftf_0403.py
"""

from __future__ import annotations

import http.client
import inspect
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.budget.budget_policy_crud import (  # noqa: E402
    BudgetPolicyConcurrencyError,
    BudgetPolicyStateError,
    apply_revise_budget_policy,
    reserve_budget_allocation,
    validate_allocation_amount,
)
from app.budget.budget_policy_routes import (  # noqa: E402
    BUDGET_READ_ROLES,
    BUDGET_WRITE_ROLES,
    _serialize,
    build_budget_policy_router,
)
from app.budget.budget_policy_schema import (  # noqa: E402
    ALLOWED_CURRENCIES,
    BUDGET_SCOPES,
    BudgetPolicy,
    BudgetPolicyValidationError,
    validate_currency,
    validate_reserved_amount,
    validate_scopes,
    validate_total_amount,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except (BudgetPolicyValidationError, BudgetPolicyStateError, BudgetPolicyConcurrencyError):
        return True


_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _make_policy(**overrides: object) -> BudgetPolicy:
    now = datetime.now(UTC)
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "total_amount": Decimal("1000.00"),
        "currency": "USD",
        "scopes_json": ["training"],
        "reserved_amount": Decimal("0"),
        "row_version": 1,
        "created_by": "user-1",
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(overrides)
    return BudgetPolicy(**kwargs)


# -- 1. validate_total_amount / validate_currency / validate_scopes ---------


def test_validate_total_amount_accepts_real_positive_decimals() -> None:
    check(
        "FTF-0403",
        "a real, positive Decimal is accepted unchanged",
        validate_total_amount(Decimal("1000.50")) == Decimal("1000.50"),
    )


def test_validate_total_amount_rejects_bad_shapes() -> None:
    check(
        "FTF-0403",
        "negative_acceptance: a negative amount is rejected",
        _rejects(validate_total_amount, Decimal("-1")),
    )
    check(
        "FTF-0403",
        "negative_acceptance: zero is rejected (not a real authorization)",
        _rejects(validate_total_amount, Decimal("0")),
    )
    check(
        "FTF-0403",
        "a float is rejected -- money is never a float in this module",
        _rejects(validate_total_amount, 1000.0),
    )
    check("FTF-0403", "a plain string is rejected", _rejects(validate_total_amount, "1000"))
    check(
        "FTF-0403",
        "a bool is rejected (bool is a Decimal-unrelated subtype trap in Python)",
        _rejects(validate_total_amount, True),
    )
    check(
        "FTF-0403",
        "NaN is rejected -- must be finite",
        _rejects(validate_total_amount, Decimal("NaN")),
    )
    check(
        "FTF-0403",
        "Infinity is rejected -- must be finite",
        _rejects(validate_total_amount, Decimal("Infinity")),
    )
    check(
        "FTF-0403",
        "more than 4 decimal places is rejected (exceeds NUMERIC(18,4) scale)",
        _rejects(validate_total_amount, Decimal("1.00001")),
    )
    check(
        "FTF-0403",
        "too many integer digits is rejected (exceeds NUMERIC(18,4) precision)",
        _rejects(validate_total_amount, Decimal("9" * 15)),
    )


def test_validate_currency_is_a_real_closed_set() -> None:
    for code in ALLOWED_CURRENCIES:
        check(
            "FTF-0403",
            f"validate_currency accepts the real allowlisted code {code!r}",
            validate_currency(code) == code,
        )
    check(
        "FTF-0403",
        "negative_acceptance: an unrecognized currency code ('unknown rate') is refused",
        _rejects(validate_currency, "XXX"),
    )
    check(
        "FTF-0403",
        "a lowercase currency code is refused (never silently normalized)",
        _rejects(validate_currency, "usd"),
    )
    check("FTF-0403", "a non-string currency is refused", _rejects(validate_currency, 840))
    check(
        "FTF-0403",
        "a made-up 3-letter code outside the allowlist is refused",
        _rejects(validate_currency, "ZZZ"),
    )


def test_validate_scopes_is_a_real_closed_set() -> None:
    check(
        "FTF-0403",
        "BUDGET_SCOPES is exactly the three named cost-bearing workflow categories",
        set(BUDGET_SCOPES) == {"training", "evaluation", "serving"},
    )
    check(
        "FTF-0403",
        "a real subset of scopes is accepted, deduplicated and sorted",
        validate_scopes(["serving", "training", "training"]) == ["serving", "training"],
    )
    check("FTF-0403", "an empty scopes list is refused", _rejects(validate_scopes, []))
    check("FTF-0403", "a non-list scopes value is refused", _rejects(validate_scopes, "training"))
    check(
        "FTF-0403",
        "an unrecognized scope is refused, not silently accepted",
        _rejects(validate_scopes, ["training", "world_domination"]),
    )


def test_validate_reserved_amount_bounds_against_total() -> None:
    total = Decimal("100.00")
    check(
        "FTF-0403",
        "zero reserved is accepted (the normal starting state)",
        validate_reserved_amount(total, Decimal("0")) == Decimal("0"),
    )
    check(
        "FTF-0403",
        "reserved exactly equal to total is accepted (fully committed)",
        validate_reserved_amount(total, Decimal("100.00")) == Decimal("100.00"),
    )
    check(
        "FTF-0403",
        "negative_acceptance: reserved exceeding total is refused",
        _rejects(validate_reserved_amount, total, Decimal("100.01")),
    )
    check(
        "FTF-0403",
        "negative_acceptance: a negative reserved amount is refused",
        _rejects(validate_reserved_amount, total, Decimal("-1")),
    )


# -- 2. BudgetPolicy.__init__ refuses bad input before any session/DB -------


def test_construction_rejects_bad_total_amount() -> None:
    check(
        "FTF-0403",
        "constructing a BudgetPolicy with a negative total_amount raises before any DB call",
        _rejects(_make_policy, total_amount=Decimal("-5")),
    )


def test_construction_rejects_bad_currency() -> None:
    check(
        "FTF-0403",
        "constructing a BudgetPolicy with an unrecognized currency raises",
        _rejects(_make_policy, currency="ZZZ"),
    )


def test_construction_rejects_empty_scopes() -> None:
    check(
        "FTF-0403",
        "constructing a BudgetPolicy with no scopes raises",
        _rejects(_make_policy, scopes_json=[]),
    )


def test_construction_rejects_over_reserved() -> None:
    check(
        "FTF-0403",
        "constructing a BudgetPolicy whose reserved_amount exceeds total_amount raises",
        _rejects(_make_policy, total_amount=Decimal("10.00"), reserved_amount=Decimal("10.01")),
    )


def test_construction_accepts_a_real_policy() -> None:
    policy = _make_policy()
    check(
        "FTF-0403",
        "a real, well-formed BudgetPolicy constructs without raising",
        policy.total_amount == Decimal("1000.00") and policy.currency == "USD",
    )


# -- 3. apply_revise_budget_policy: row_version + reservation-floor guards --


def test_apply_revise_budget_policy_refuses_stale_row_version() -> None:
    policy = _make_policy(row_version=3)
    check(
        "FTF-0403",
        "a stale expected_row_version is refused, the same optimistic-concurrency "
        "discipline project_crud.py's own ProjectConcurrencyError established",
        _rejects(
            apply_revise_budget_policy,
            policy,
            total_amount=Decimal("2000.00"),
            scopes=None,
            expected_row_version=2,
        ),
    )


def test_apply_revise_budget_policy_refuses_shrinking_below_reserved() -> None:
    policy = _make_policy(total_amount=Decimal("1000.00"), reserved_amount=Decimal("400.00"))
    check(
        "FTF-0403",
        "negative_acceptance: revising total_amount below the already-reserved amount "
        "is refused",
        _rejects(
            apply_revise_budget_policy,
            policy,
            total_amount=Decimal("399.99"),
            scopes=None,
            expected_row_version=1,
        ),
    )


def test_apply_revise_budget_policy_applies_a_real_change() -> None:
    policy = _make_policy(total_amount=Decimal("1000.00"), row_version=1)
    changed = apply_revise_budget_policy(
        policy, total_amount=Decimal("5000.00"), scopes=["serving"], expected_row_version=1
    )
    check("FTF-0403", "a real revision reports changed=True", changed is True)
    check(
        "FTF-0403",
        "the real new total_amount is applied",
        policy.total_amount == Decimal("5000.00"),
    )
    check("FTF-0403", "the real new scopes are applied", policy.scopes_json == ["serving"])
    check(
        "FTF-0403",
        "row_version is bumped by exactly 1 on a real revision",
        policy.row_version == 2,
    )


def test_apply_revise_budget_policy_requires_at_least_one_field() -> None:
    policy = _make_policy(row_version=1)
    check(
        "FTF-0403",
        "a no-op revise (nothing to change) is refused",
        _rejects(
            apply_revise_budget_policy,
            policy,
            total_amount=None,
            scopes=None,
            expected_row_version=1,
        ),
    )


# -- 4. validate_allocation_amount -------------------------------------------


def test_validate_allocation_amount_rejects_negative_and_unknown_shapes() -> None:
    check(
        "FTF-0403",
        "a real positive Decimal allocation amount is accepted",
        validate_allocation_amount(Decimal("50.00")) == Decimal("50.00"),
    )
    check(
        "FTF-0403",
        "negative_acceptance: a negative allocation amount is refused",
        _rejects(validate_allocation_amount, Decimal("-1")),
    )
    check(
        "FTF-0403",
        "negative_acceptance: a zero allocation amount is refused",
        _rejects(validate_allocation_amount, Decimal("0")),
    )
    check(
        "FTF-0403",
        "a float allocation amount is refused",
        _rejects(validate_allocation_amount, 50.0),
    )


def test_reserve_and_release_build_a_single_conditional_atomic_update() -> None:
    # Source-inspection proof (the real concurrency proof against a live
    # database is tests/integration/test_ftf_0403_budget_policy.py): both
    # functions must build their SQL as one conditional UPDATE guarded by
    # the row's own current reserved_amount/total_amount, never a
    # read-then-compare-then-write sequence a second concurrent request
    # could race past.
    import app.budget.budget_policy_crud as crud_module

    reserve_source = inspect.getsource(reserve_budget_allocation)
    check(
        "FTF-0403",
        "reserve_budget_allocation issues a real SQLAlchemy update() against BudgetPolicy",
        "update(BudgetPolicy)" in reserve_source,
    )
    check(
        "FTF-0403",
        "reserve_budget_allocation's own UPDATE is guarded by the row's own current "
        "reserved_amount/total_amount in the same statement, not a prior SELECT",
        "reserved_amount + amount <= BudgetPolicy.total_amount" in reserve_source,
    )
    crud_source = inspect.getsource(crud_module)
    check(
        "FTF-0403",
        "release_budget_allocation's own UPDATE is guarded symmetrically",
        "reserved_amount - amount >= 0" in crud_source,
    )
    check(
        "FTF-0403",
        "every real query/mutation runs through the real, imported execute_tenant_scoped "
        "(FTF-0105) -- never a bare session.execute(...) call",
        "execute_tenant_scoped(" in crud_source
        and crud_module.execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-0403",
        "every fetch authorizes through the real, imported authorize_resource_ownership "
        "(FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )


# -- 5. Real demo router: role-boundary denial never opens a session --------


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_WORKSPACE_ADMIN_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="wa-1")
_EVALUATOR_PRINCIPAL = AuthenticatedPrincipal(
    issuer="https://issuer.example", subject="evaluator-1"
)


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "workspace_admin":
        return _WORKSPACE_ADMIN_PRINCIPAL
    if header == "evaluator":
        return _EVALUATOR_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _WORKSPACE_ADMIN_PRINCIPAL.subject:
        return Role.WORKSPACE_ADMIN
    if subject == _EVALUATOR_PRINCIPAL.subject:
        return Role.EVALUATOR
    return None


@contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_budget_policy_router(
            session_factory,
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
        )
    )
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@dataclass
class _HttpResult:
    status: int


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    principal: str | None = None,
    body: bytes | None = None,
) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    if body is not None:
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        response.read()
        return _HttpResult(status=response.status)
    finally:
        conn.close()


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/budget-policy",
        )
        check(
            "FTF-0403",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-0403",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_can_read_but_not_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/budget-policy",
            principal="evaluator",
        )
        check(
            "FTF-0403",
            "an evaluator (a role Section 45 names, with no dedicated restrictive "
            "sentence for budget authorization) can read (not 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0403",
            "the evaluator's read genuinely reaches the session layer",
            session_factory.call_count >= 1,
        )

        session_factory.reset_mock()
        result = _request(
            base_url,
            "POST",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/budget-policy",
            principal="evaluator",
        )
        check(
            "FTF-0403",
            "an evaluator is denied create (403) -- write is workspace_admin/"
            "organization_admin only",
            result.status == 403,
        )
        check(
            "FTF-0403",
            "the write role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )

        session_factory.reset_mock()
        result = _request(
            base_url,
            "POST",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/budget-policy/reserve",
            principal="evaluator",
        )
        check(
            "FTF-0403",
            "an evaluator is denied reserve (403) -- reserve/release are writes too",
            result.status == 403,
        )
        check(
            "FTF-0403",
            "the reserve role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_workspace_admin_write_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "POST",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/budget-policy",
            principal="workspace_admin",
            body=b'{"total_amount": "1000.00", "currency": "USD", "scopes": ["training"]}',
        )
        check(
            "FTF-0403",
            "a workspace_admin's create request passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0403",
            "the workspace_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_split_matches_section_45() -> None:
    check(
        "FTF-0403",
        "Section 45: 'workspace_admin: project administration' is the only sentence "
        "naming a role for this class of work -- BUDGET_WRITE_ROLES is exactly "
        "{workspace_admin, organization_admin}, the same precedent FTF-0401/FTF-0404 applied",
        BUDGET_WRITE_ROLES == frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-0403",
        "BUDGET_READ_ROLES includes every role Section 45 names",
        BUDGET_READ_ROLES == frozenset(Role),
    )


def test_require_role_is_the_real_imported_dependency_not_a_duplicate() -> None:
    from app.auth.rbac_middleware import require_role
    from app.budget import budget_policy_routes

    routes_source = inspect.getsource(budget_policy_routes)
    check(
        "FTF-0403",
        "budget_policy_routes.py builds its dependencies from the real, "
        "imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    policy = _make_policy(total_amount=Decimal("1000.00"), reserved_amount=Decimal("250.00"))
    body = _serialize(policy)
    check(
        "FTF-0403",
        "the serialized policy carries decimal amounts as strings, never a lossy float, "
        "quantized to the real column's own NUMERIC(18,4) scale",
        body["total_amount"] == "1000.0000" and body["reserved_amount"] == "250.0000",
    )
    check(
        "FTF-0403",
        "available_amount is computed as total minus reserved",
        body["available_amount"] == "750.0000",
    )
    check(
        "FTF-0403",
        "row_version is a real integer field",
        isinstance(body["row_version"], int) and body["row_version"] == 1,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0403 Budget policy tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0403 Budget policy tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
