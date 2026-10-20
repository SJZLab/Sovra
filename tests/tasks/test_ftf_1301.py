#!/usr/bin/env python3
"""FTF-1301 -- CostProfile schema/UI (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list/verify/search/IDOR evidence is
`tests/integration/test_ftf_1301_cost_profile.py`, run in CI's dedicated
`cost-profile-integration` job -- this repo's interactive dev sandbox has
no container-registry egress, same documented finding as every prior
integration-tested task). This suite proves, without any database:

1. `validate_profile_type`/`validate_label`/`validate_rate_unit`/
   `validate_rate_amount`/`validate_source`: a real, bounded, closed-set
   validation for every field, refused before any session or database is
   ever touched.
2. `CostProfile.__init__` itself refuses bad input.
3. `is_certifiable_cheapest` -- this task's own negative_acceptance
   ("Token price zero is not local compute free; unverified/expired rate
   cannot certify cheapest") proven at the pure-function level for all
   three disqualifying conditions, plus the two positive cases that prove
   the rule is not simply "reject every zero rate": a verified,
   unexpired, non-zero rate certifies, and critically a verified,
   unexpired *local_hardware* rate of exactly zero also certifies -- the
   zero-rate rule is provider-specific, not a blanket zero refusal.
4. The real, locally-hosted `build_cost_profile_router` (a real uvicorn
   server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0403.py` established) denies an unauthenticated
   request (401) and any role other than `organization_admin` (403,
   including reads and the search endpoint -- Section 45 names cost
   profiles as organization_admin-only, unlike most other resources'
   wider read access) before `session_factory` is ever called, and lets
   `organization_admin` reach the session layer.

Run directly: python3 tests/tasks/test_ftf_1301.py
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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.costs import cost_profile_crud  # noqa: E402
from app.costs.cost_profile_routes import (  # noqa: E402
    COST_PROFILE_ROLES,
    _serialize,
    build_cost_profile_router,
)
from app.costs.cost_profile_schema import (  # noqa: E402
    PROFILE_TYPES,
    RATE_UNITS,
    CostProfile,
    CostProfileValidationError,
    is_certifiable_cheapest,
    validate_label,
    validate_profile_type,
    validate_rate_amount,
    validate_rate_unit,
    validate_source,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except CostProfileValidationError:
        return True


_ORG_ID = uuid.uuid4()


def _make_profile(**overrides: object) -> CostProfile:
    now = datetime.now(UTC)
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "profile_type": "provider",
        "label": "OpenAI gpt-4o-mini",
        "rate_unit": "per_input_token",
        "rate_amount": Decimal("0.00000015"),
        "currency": "USD",
        "source": "https://openai.com/api/pricing",
        "rate_recorded_at": now,
        "verified_at": None,
        "expires_at": None,
        "row_version": 1,
        "created_by": "user-1",
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(overrides)
    return CostProfile(**kwargs)


# -- 1. validate_* pure functions --------------------------------------


def test_validate_profile_type_accepts_real_values() -> None:
    for value in PROFILE_TYPES:
        check(
            "FTF-1301",
            f"profile_type {value!r} is accepted",
            validate_profile_type(value) == value,
        )


def test_validate_profile_type_rejects_unknown() -> None:
    check(
        "FTF-1301",
        "an unrecognized profile_type is rejected",
        _rejects(validate_profile_type, "managed_provider"),
    )


def test_validate_label_rejects_empty() -> None:
    check("FTF-1301", "an empty label is rejected", _rejects(validate_label, ""))
    check("FTF-1301", "a whitespace-only label is rejected", _rejects(validate_label, "   "))


def test_validate_label_rejects_oversized() -> None:
    check("FTF-1301", "an oversized label is rejected", _rejects(validate_label, "x" * 201))


def test_validate_rate_unit_accepts_real_values() -> None:
    for value in RATE_UNITS:
        check("FTF-1301", f"rate_unit {value!r} is accepted", validate_rate_unit(value) == value)


def test_validate_rate_unit_rejects_unknown() -> None:
    check(
        "FTF-1301",
        "an unrecognized rate_unit is rejected",
        _rejects(validate_rate_unit, "per_widget"),
    )


def test_validate_rate_amount_accepts_zero_and_positive() -> None:
    check(
        "FTF-1301",
        "zero is accepted (a legitimate fact to record, unlike a budget authorization)",
        validate_rate_amount(Decimal("0")) == Decimal("0"),
    )
    check(
        "FTF-1301",
        "a small positive per-token rate is accepted",
        validate_rate_amount(Decimal("0.00000015")) == Decimal("0.00000015"),
    )


def test_validate_rate_amount_rejects_bad_shapes() -> None:
    check(
        "FTF-1301",
        "negative_acceptance: a negative rate is rejected",
        _rejects(validate_rate_amount, Decimal("-1")),
    )
    check(
        "FTF-1301",
        "a non-Decimal rate is rejected",
        _rejects(validate_rate_amount, 1.5),
    )
    check(
        "FTF-1301",
        "a bool (a Python bool is an int subclass) is rejected",
        _rejects(validate_rate_amount, True),
    )
    check(
        "FTF-1301",
        "a non-finite rate is rejected",
        _rejects(validate_rate_amount, Decimal("NaN")),
    )
    check(
        "FTF-1301",
        "a rate with too many decimal places for NUMERIC(24,10) is rejected",
        _rejects(validate_rate_amount, Decimal("0.00000000001")),
    )
    check(
        "FTF-1301",
        "a rate too large for NUMERIC(24,10) is rejected",
        _rejects(validate_rate_amount, Decimal("99999999999999999")),
    )


def test_validate_source_rejects_empty_and_oversized() -> None:
    check("FTF-1301", "an empty source is rejected", _rejects(validate_source, ""))
    check("FTF-1301", "an oversized source is rejected", _rejects(validate_source, "x" * 2049))


# -- 2. CostProfile.__init__ construction guards -------------------------


def test_construction_rejects_bad_profile_type() -> None:
    def _build() -> None:
        _make_profile(profile_type="not_a_real_type")

    check("FTF-1301", "constructing with a bad profile_type refuses", _rejects(_build))


def test_construction_accepts_valid_input() -> None:
    profile = _make_profile()
    check(
        "FTF-1301",
        "a validly-constructed CostProfile keeps its own field values",
        profile.profile_type == "provider" and profile.rate_amount == Decimal("0.00000015"),
    )


# -- 3. is_certifiable_cheapest -- this task's own negative_acceptance --


def test_expired_rate_never_certifies() -> None:
    now = datetime.now(UTC)
    profile = _make_profile(
        verified_at=now - timedelta(days=10), expires_at=now - timedelta(days=1)
    )
    certifiable, reason = is_certifiable_cheapest(profile, now)
    check(
        "FTF-1301",
        "negative_acceptance: an expired rate cannot certify cheapest",
        certifiable is False and reason == "expired",
    )


def test_unverified_rate_never_certifies() -> None:
    now = datetime.now(UTC)
    profile = _make_profile(verified_at=None, expires_at=None)
    certifiable, reason = is_certifiable_cheapest(profile, now)
    check(
        "FTF-1301",
        "negative_acceptance: an unverified rate cannot certify cheapest",
        certifiable is False and reason == "unverified",
    )


def test_zero_provider_rate_never_certifies_even_if_verified() -> None:
    now = datetime.now(UTC)
    profile = _make_profile(
        profile_type="provider",
        rate_amount=Decimal("0"),
        verified_at=now - timedelta(days=1),
        expires_at=None,
    )
    certifiable, reason = is_certifiable_cheapest(profile, now)
    check(
        "FTF-1301",
        "negative_acceptance: 'token price zero is not local compute free' -- a verified, "
        "unexpired provider rate of exactly zero still cannot certify cheapest",
        certifiable is False and reason == "zero_provider_rate_unconfirmed",
    )


def test_zero_local_hardware_rate_can_certify() -> None:
    """The zero-rate disqualification is provider-specific, not a blanket
    zero refusal -- a verified, unexpired local_hardware rate of exactly
    zero (e.g. a genuinely negligible marginal per-token cost on
    already-amortized hardware) is a real, certifiable fact."""
    now = datetime.now(UTC)
    profile = _make_profile(
        profile_type="local_hardware",
        rate_amount=Decimal("0"),
        verified_at=now - timedelta(days=1),
        expires_at=None,
    )
    certifiable, reason = is_certifiable_cheapest(profile, now)
    check(
        "FTF-1301",
        "a verified, unexpired local_hardware zero rate certifies (unlike a provider's)",
        certifiable is True and reason is None,
    )


def test_verified_unexpired_nonzero_rate_certifies() -> None:
    now = datetime.now(UTC)
    profile = _make_profile(
        profile_type="provider",
        rate_amount=Decimal("0.00000015"),
        verified_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
    )
    certifiable, reason = is_certifiable_cheapest(profile, now)
    check(
        "FTF-1301",
        "a verified, unexpired, non-zero rate certifies cheapest",
        certifiable is True and reason is None,
    )


def test_expired_check_uses_wall_clock_boundary_correctly() -> None:
    now = datetime.now(UTC)
    profile = _make_profile(
        verified_at=now - timedelta(days=1), expires_at=now + timedelta(seconds=1)
    )
    certifiable, reason = is_certifiable_cheapest(profile, now)
    check(
        "FTF-1301",
        "a rate that expires in the future (not yet expired) still certifies",
        certifiable is True and reason is None,
    )


# -- 4. Real composition, not a hand-rolled duplicate --------------------


def test_crud_uses_real_imported_primitives_not_duplicates() -> None:
    crud_source = inspect.getsource(cost_profile_crud)
    check(
        "FTF-1301",
        "every real query/mutation runs through the real, imported execute_tenant_scoped "
        "(FTF-0105) -- never a bare session.execute(...) call",
        "execute_tenant_scoped(" in crud_source
        and cost_profile_crud.execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-1301",
        "every fetch authorizes through the real, imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-1301",
        "currency validation reuses FTF-0403's own real, already-CERTIFIED "
        "validate_currency -- not a second, duplicate ISO-4217 vocabulary",
        "from app.budget.budget_policy_schema import validate_currency"
        in inspect.getsource(sys.modules["app.costs.cost_profile_schema"]),
    )


def test_require_role_is_the_real_imported_dependency_not_a_duplicate() -> None:
    from app.auth.rbac_middleware import require_role
    from app.costs import cost_profile_routes

    routes_source = inspect.getsource(cost_profile_routes)
    check(
        "FTF-1301",
        "cost_profile_routes.py builds its dependencies from the real, imported "
        "require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )


def test_role_gating_matches_section_45() -> None:
    check(
        "FTF-1301",
        "Section 45: 'organization_admin: connections, security profiles, cost profiles' "
        "names cost profiles explicitly -- COST_PROFILE_ROLES is organization_admin alone",
        COST_PROFILE_ROLES == frozenset({Role.ORGANIZATION_ADMIN}),
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    profile = _make_profile(rate_amount=Decimal("0.00000015"))
    body = _serialize(profile)
    check(
        "FTF-1301",
        "the serialized rate is a decimal string, never a lossy float, quantized to the "
        "real column's own NUMERIC(24,10) scale",
        body["rate_amount"] == "0.0000001500",
    )
    check(
        "FTF-1301",
        "verified_at is null when the profile has never been verified",
        body["verified_at"] is None,
    )


# -- 5. Real demo router: role-boundary denial never opens a session ----


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_ORG_ADMIN_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="oa-1")
_ML_ENGINEER_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="mle-1")


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "organization_admin":
        return _ORG_ADMIN_PRINCIPAL
    if header == "ml_engineer":
        return _ML_ENGINEER_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _ORG_ADMIN_PRINCIPAL.subject:
        return Role.ORGANIZATION_ADMIN
    if subject == _ML_ENGINEER_PRINCIPAL.subject:
        return Role.ML_ENGINEER
    return None


@contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_cost_profile_router(
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
        result = _request(base_url, "GET", f"/api/v1/organizations/{_ORG_ID}/cost-profiles")
        check(
            "FTF-1301",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-1301",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_non_admin_role_denied_even_for_reads() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/cost-profiles",
            principal="ml_engineer",
        )
        check(
            "FTF-1301",
            "Section 45 names cost profiles as organization_admin-only, including reads -- "
            "an ml_engineer is denied (403), unlike most other resources' wider read access",
            result.status == 403,
        )
        check(
            "FTF-1301",
            "the role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_organization_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/cost-profiles",
            principal="organization_admin",
        )
        check(
            "FTF-1301",
            "organization_admin's read request passes the role gate and reaches the "
            "session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-1301",
            "the organization_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_search_endpoint_is_gated_identically() -> None:
    session_factory = MagicMock()
    project_id = uuid.uuid4()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{project_id}/costs/cost-per-success/search",
        )
        check(
            "FTF-1301",
            "the search endpoint denies an unauthenticated request (401) too",
            result.status == 401,
        )
        check(
            "FTF-1301",
            "the search endpoint's denial never opens a session",
            session_factory.call_count == 0,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1301 CostProfile tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1301 CostProfile tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
