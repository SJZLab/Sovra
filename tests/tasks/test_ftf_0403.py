#!/usr/bin/env python3
"""FTF-0403 -- Budget policy (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
persistence, IDOR, state, and real-concurrency reservation evidence is
`tests/integration/test_ftf_0403_budget_policy.py`, run in CI's dedicated
`budget-policy-integration` job -- this repo's interactive dev sandbox has
no container-registry egress, same documented finding as every prior
integration-tested task). This suite proves, without any database:

1. `validate_amount`: this task's own negative_acceptance ("Negative/
   unknown rates... cannot be accepted") at the pure-function level -- a
   negative value, a non-`Decimal` (bare `float`/`int`/`str`/`None`), a
   non-finite `Decimal` (`NaN`/`Infinity`), an over-precision value, and a
   value beyond `Numeric(18, 6)`'s own real ceiling are all refused; a
   real, in-bounds `Decimal` is accepted unchanged.
2. `validate_currency`/`validate_scopes`: closed-shape validation, never
   silently accepted outside their own real constraints.
3. `BudgetPolicy.__init__`/`BudgetPolicyEvent.__init__` refuse bad input
   before any session or database is ever touched.
4. `create_budget_policy`'s own input validation runs to completion, and
   raises the named error, before `session` is ever touched (every call
   passes `session=None`) -- `reserve_budget`/`enforce_budget_policy_
   reference` both read through `execute_tenant_scoped`, which requires a
   real Postgres session-local tenant context (`current_setting(...)` does
   not exist on SQLite), so their own IDOR/state/currency/scope/
   reservation-ceiling behavior is proven only against a real live
   PostgreSQL server in the live-Postgres suite, never faked here.
5. Source-inspection proof that `apps/api/app/projects/project_crud.py`'s
   `update_project` genuinely calls `enforce_budget_policy_reference` (real
   wiring into the one live surface that exists today -- FTF-0401's own
   project CRUD -- not an unused module).

Run directly: python3 tests/tasks/test_ftf_0403.py
"""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.services.budget_policy import (  # noqa: E402
    AMOUNT_DECIMAL_PLACES,
    BUDGET_SCOPES,
    BudgetPolicy,
    BudgetPolicyEvent,
    BudgetPolicyStateError,
    BudgetPolicyValidationError,
    InsufficientBudgetError,
    InvalidBudgetAmountError,
    InvalidCurrencyCodeError,
    UnknownBudgetScopeError,
    create_budget_policy,
    list_budget_policy_events,
    validate_amount,
    validate_currency,
    validate_scopes,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except BudgetPolicyValidationError:
        return True


def test_validate_amount_rejects_negative_unknown_and_wrong_type() -> None:
    check(
        "FTF-0403",
        "negative_acceptance: a negative amount is refused",
        _rejects(validate_amount, Decimal("-1"), allow_zero=True),
    )
    check(
        "FTF-0403",
        "negative_acceptance: a bare float (not decimal.Decimal) is refused",
        _rejects(validate_amount, 12.5, allow_zero=True),
    )
    check(
        "FTF-0403",
        "negative_acceptance: an int is refused (not decimal.Decimal)",
        _rejects(validate_amount, 12, allow_zero=True),
    )
    check(
        "FTF-0403",
        "negative_acceptance: None ('unknown') is refused",
        _rejects(validate_amount, None, allow_zero=True),
    )
    check(
        "FTF-0403",
        "negative_acceptance: NaN ('unknown rate') is refused",
        _rejects(validate_amount, Decimal("NaN"), allow_zero=True),
    )
    check(
        "FTF-0403",
        "negative_acceptance: Infinity is refused",
        _rejects(validate_amount, Decimal("Infinity"), allow_zero=True),
    )
    check(
        "FTF-0403",
        "zero is refused when allow_zero=False",
        _rejects(validate_amount, Decimal("0"), allow_zero=False),
    )
    check(
        "FTF-0403",
        "zero is accepted when allow_zero=True",
        validate_amount(Decimal("0"), allow_zero=True) == Decimal("0"),
    )
    check(
        "FTF-0403",
        f"an amount with more than {AMOUNT_DECIMAL_PLACES} decimal places is refused",
        _rejects(validate_amount, Decimal("1.1234567"), allow_zero=True),
    )
    check(
        "FTF-0403",
        "an amount beyond the Numeric(18,6) ceiling is refused",
        _rejects(validate_amount, Decimal(10**13), allow_zero=True),
    )
    check(
        "FTF-0403",
        "a real, in-bounds Decimal is accepted unchanged",
        validate_amount(Decimal("199.990000"), allow_zero=False) == Decimal("199.990000"),
    )


def test_validate_currency() -> None:
    check(
        "FTF-0403", "a real uppercase 3-letter code is accepted", validate_currency("USD") == "USD"
    )
    check("FTF-0403", "a lowercase code is refused", _rejects(validate_currency, "usd"))
    check("FTF-0403", "a 2-letter code is refused", _rejects(validate_currency, "US"))
    check("FTF-0403", "a 4-letter code is refused", _rejects(validate_currency, "USDD"))
    check("FTF-0403", "a non-alpha code is refused", _rejects(validate_currency, "US1"))
    check("FTF-0403", "None is refused", _rejects(validate_currency, None))


def test_validate_scopes() -> None:
    check(
        "FTF-0403",
        "a real subset of BUDGET_SCOPES is accepted",
        validate_scopes(["training", "evaluation"]) == ("training", "evaluation"),
    )
    check("FTF-0403", "an empty list is refused", _rejects(validate_scopes, []))
    check(
        "FTF-0403",
        "an unrecognized scope is refused",
        _rejects(validate_scopes, ["training", "yolo"]),
    )
    check(
        "FTF-0403",
        "a duplicate scope is refused",
        _rejects(validate_scopes, ["training", "training"]),
    )
    check("FTF-0403", "a non-list value is refused", _rejects(validate_scopes, "training"))
    check(
        "FTF-0403",
        "BUDGET_SCOPES is grounded in the specification's own named spend classes",
        set(BUDGET_SCOPES) == {"training", "evaluation", "coding_agent", "serving"},
    )


def _org_id() -> uuid.UUID:
    return uuid.uuid4()


def test_budget_policy_construction_refuses_bad_input() -> None:
    org_id, project_id = _org_id(), _org_id()
    check(
        "FTF-0403",
        "constructing a BudgetPolicy with a negative authorized_amount is refused before any "
        "session or database is touched",
        _rejects(
            BudgetPolicy,
            organization_id=org_id,
            project_id=project_id,
            authorized_amount=Decimal("-5"),
            remaining_amount=Decimal("-5"),
            currency="USD",
            scopes_json=["training"],
            status="active",
            created_by="tester",
        ),
    )
    check(
        "FTF-0403",
        "constructing a BudgetPolicy with an unrecognized status is refused",
        _rejects(
            BudgetPolicy,
            organization_id=org_id,
            project_id=project_id,
            authorized_amount=Decimal("5"),
            remaining_amount=Decimal("5"),
            currency="USD",
            scopes_json=["training"],
            status="made_up_status",
            created_by="tester",
        ),
    )
    policy = BudgetPolicy(
        organization_id=org_id,
        project_id=project_id,
        authorized_amount=Decimal("100.00"),
        remaining_amount=Decimal("100.00"),
        currency="USD",
        scopes_json=["training", "evaluation"],
        status="active",
        created_by="tester",
    )
    check(
        "FTF-0403",
        "a validly-constructed BudgetPolicy keeps the real, unmutated field values",
        policy.authorized_amount == Decimal("100.00")
        and policy.currency == "USD"
        and policy.scopes_json == ["training", "evaluation"],
    )


def test_budget_policy_event_construction_refuses_bad_input() -> None:
    check(
        "FTF-0403",
        "constructing a BudgetPolicyEvent with an unrecognized event_type is refused",
        _rejects(
            BudgetPolicyEvent,
            budget_policy_id=_org_id(),
            organization_id=_org_id(),
            project_id=_org_id(),
            event_type="made_up_event",
            amount=Decimal("5"),
            currency="USD",
            scope=None,
            actor="tester",
        ),
    )
    check(
        "FTF-0403",
        "constructing a BudgetPolicyEvent with an unrecognized scope is refused",
        _rejects(
            BudgetPolicyEvent,
            budget_policy_id=_org_id(),
            organization_id=_org_id(),
            project_id=_org_id(),
            event_type="reserved",
            amount=Decimal("5"),
            currency="USD",
            scope="not_a_real_scope",
            actor="tester",
        ),
    )


# -- create_budget_policy/reserve_budget/enforce_budget_policy_reference
# all read through execute_tenant_scoped, which requires a real Postgres
# session-local `app.current_organization_id` (`current_setting(...)` is
# a Postgres-only SQL function -- it does not exist on SQLite, so this
# module deliberately does not fake a DB session for them here). Their
# real IDOR/state/currency/scope/reservation-ceiling behavior is proven
# against a real live PostgreSQL server in
# tests/integration/test_ftf_0403_budget_policy.py. What IS provable here,
# without any database, is that create_budget_policy's own input
# validation runs to completion -- and raises -- before `session` is ever
# touched: every call below passes `session=None`, so a validation
# failure that reached `session.add(...)` would crash with an unrelated
# AttributeError instead of the named exception, not silently pass.


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True


def test_create_budget_policy_validates_before_touching_the_session() -> None:
    org_id, project_id = _org_id(), _org_id()
    check(
        "FTF-0403",
        "negative_acceptance: create_budget_policy refuses a negative authorized_amount before "
        "session is ever touched (session=None would crash on any DB access)",
        _raises(
            InvalidBudgetAmountError,
            create_budget_policy,
            None,
            organization_id=org_id,
            project_id=project_id,
            authorized_amount=Decimal("-1"),
            currency="USD",
            scopes=["training"],
            created_by="tester",
        ),
    )
    check(
        "FTF-0403",
        "negative_acceptance: create_budget_policy refuses an unknown (None) authorized_amount",
        _raises(
            InvalidBudgetAmountError,
            create_budget_policy,
            None,
            organization_id=org_id,
            project_id=project_id,
            authorized_amount=None,
            currency="USD",
            scopes=["training"],
            created_by="tester",
        ),
    )
    check(
        "FTF-0403",
        "create_budget_policy refuses a zero authorized_amount (an authorization of nothing is "
        "not a real authorization)",
        _raises(
            InvalidBudgetAmountError,
            create_budget_policy,
            None,
            organization_id=org_id,
            project_id=project_id,
            authorized_amount=Decimal("0"),
            currency="USD",
            scopes=["training"],
            created_by="tester",
        ),
    )
    check(
        "FTF-0403",
        "create_budget_policy refuses an unrecognized currency",
        _raises(
            InvalidCurrencyCodeError,
            create_budget_policy,
            None,
            organization_id=org_id,
            project_id=project_id,
            authorized_amount=Decimal("100"),
            currency="dollars",
            scopes=["training"],
            created_by="tester",
        ),
    )
    check(
        "FTF-0403",
        "create_budget_policy refuses an empty scopes list",
        _raises(
            UnknownBudgetScopeError,
            create_budget_policy,
            None,
            organization_id=org_id,
            project_id=project_id,
            authorized_amount=Decimal("100"),
            currency="USD",
            scopes=[],
            created_by="tester",
        ),
    )


def test_list_budget_policy_events_symbol_exists() -> None:
    # Proves the "approval history" retrieval symbol is real and importable
    # (its own RLS-scoped behavior is proven against real Postgres in the
    # live-Postgres suite).
    check(
        "FTF-0403",
        "list_budget_policy_events is a real callable",
        callable(list_budget_policy_events),
    )


def test_invalid_budget_amount_error_is_a_budget_policy_validation_error() -> None:
    check(
        "FTF-0403",
        "InvalidBudgetAmountError/InvalidCurrencyCodeError/UnknownBudgetScopeError/"
        "InsufficientBudgetError/BudgetPolicyStateError all subclass BudgetPolicyValidationError "
        "(a caller catching the base class catches every real validation failure this module "
        "raises)",
        issubclass(InvalidBudgetAmountError, BudgetPolicyValidationError)
        and issubclass(InvalidCurrencyCodeError, BudgetPolicyValidationError)
        and issubclass(UnknownBudgetScopeError, BudgetPolicyValidationError)
        and issubclass(InsufficientBudgetError, BudgetPolicyValidationError)
        and issubclass(BudgetPolicyStateError, BudgetPolicyValidationError),
    )


def test_project_crud_genuinely_calls_the_enforcement_primitive() -> None:
    # Source-inspection, the same discipline test_ftf_0402.py already
    # applies: confirms update_project actually calls
    # enforce_budget_policy_reference, not merely imports it unused.
    crud_source = Path(API_ROOT / "app" / "projects" / "project_crud.py").read_text(
        encoding="utf-8"
    )
    check(
        "FTF-0403",
        "project_crud.py imports the real enforcement primitive from this task's own module",
        "from app.services.budget_policy import enforce_budget_policy_reference" in crud_source,
    )
    check(
        "FTF-0403",
        "enforce_budget_policy_reference is actually called (not just imported)",
        "enforce_budget_policy_reference(" in crud_source,
    )
    check(
        "FTF-0403",
        "update_project accepts a real budget_policy_id parameter",
        "budget_policy_id: uuid.UUID | None = None" in crud_source,
    )
    routes_source = Path(API_ROOT / "app" / "projects" / "project_routes.py").read_text(
        encoding="utf-8"
    )
    check(
        "FTF-0403",
        "the live PATCH route genuinely threads budget_policy_id through to update_project",
        'budget_policy_id=_optional_uuid(payload, "budget_policy_id")' in routes_source,
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
