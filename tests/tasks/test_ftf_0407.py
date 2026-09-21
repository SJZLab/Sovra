#!/usr/bin/env python3
"""FTF-0407 -- Critical behavior requirements (no-DB unit/fault tests).

Runnable without Postgres or network egress. This suite proves, without
any database:

1. `enforce_critical_behavior_requirement` is a no-op whenever
   `is_critical=False` -- this module never requires a tool to be marked
   critical, it only refuses an unmeasurable or unenforced claim that it
   is.
2. negative_acceptance, "unmeasurable ... critical mandate blocks
   activation": `is_critical=True` on a `side_effect_class='read_only'`
   tool is refused -- a read-only operation has no enforced side effect
   for a deterministic outcome test or runtime enforcement mechanism to
   bind to.
3. negative_acceptance, "... or conflicting critical mandate blocks
   activation": `is_critical=True` on a tool with a real side effect
   (`reversible_write` or above) but `requires_confirmation=False` is
   refused -- the critical claim conflicts with its own complete lack of
   runtime enforcement.
4. Acceptance: `is_critical=True` is accepted once the tool both has a
   real side effect and carries `requires_confirmation=True`.
5. No regression to FTF-0406's own already-CERTIFIED evidence: a tool
   resolved at or above `irreversible_write` with `is_critical=False`
   still constructs successfully -- this task deliberately does not
   enforce the reverse direction (see
   `app.services.critical_behavior_requirements`'s own module docstring
   for why forcing `is_critical=True` there would regress
   `tests/tasks/test_ftf_0406.py::test_construction_accepts_a_correctly_confirmed_high_risk_tool`).
6. Real live wiring: constructing a real `ToolDefinition` with an
   unmeasurable/conflicting `is_critical` claim fails at construction
   time, before any session or database is ever touched -- the same
   discipline FTF-0404/FTF-0405/FTF-0406's own `ToolDefinition.__init__`
   already established.
7. `derive_critical_behavior_requirement_view` (the real `/requirements`
   projection) reports `measurable=False` only for `read_only`, and
   `runtime_enforced` mirroring `requires_confirmation`, for real rows
   that already passed enforcement.

Run directly: python3 tests/tasks/test_ftf_0407.py
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.services.critical_behavior_requirements import (  # noqa: E402
    CriticalBehaviorRequirementError,
    derive_critical_behavior_requirement_view,
    enforce_critical_behavior_requirement,
)
from app.tools.tool_definition_schema import (  # noqa: E402
    ToolDefinition,
    ToolDefinitionValidationError,
    compute_schema_hash,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except CriticalBehaviorRequirementError:
        return True


# -- 1/2/3/4. enforce_critical_behavior_requirement -------------------------


def test_enforce_is_a_noop_when_not_critical() -> None:
    for side_effect_class, requires_confirmation in (
        ("read_only", False),
        ("reversible_write", False),
        ("reversible_write", True),
        ("irreversible_write", True),
        ("financial", True),
        ("privileged", True),
    ):
        check(
            "FTF-0407",
            f"is_critical=False never raises regardless of ({side_effect_class!r}, "
            f"{requires_confirmation!r})",
            _rejects(
                enforce_critical_behavior_requirement,
                is_critical=False,
                side_effect_class=side_effect_class,
                requires_confirmation=requires_confirmation,
            )
            is False,
        )


def test_enforce_refuses_unmeasurable_critical_read_only_claim() -> None:
    check(
        "FTF-0407",
        "negative_acceptance: is_critical=True on a read_only tool is unmeasurable and refused",
        _rejects(
            enforce_critical_behavior_requirement,
            is_critical=True,
            side_effect_class="read_only",
            requires_confirmation=False,
        ),
    )
    check(
        "FTF-0407",
        "the same refusal holds even if requires_confirmation is (meaninglessly) set True",
        _rejects(
            enforce_critical_behavior_requirement,
            is_critical=True,
            side_effect_class="read_only",
            requires_confirmation=True,
        ),
    )


def test_enforce_refuses_conflicting_unenforced_critical_claim() -> None:
    check(
        "FTF-0407",
        "negative_acceptance: is_critical=True on a reversible_write tool with "
        "requires_confirmation=False conflicts with its own lack of enforcement",
        _rejects(
            enforce_critical_behavior_requirement,
            is_critical=True,
            side_effect_class="reversible_write",
            requires_confirmation=False,
        ),
    )
    check(
        "FTF-0407",
        "the same conflict holds at higher ranks too (defense in depth, even though "
        "FTF-0406 already forces requires_confirmation=True at this rank in practice)",
        _rejects(
            enforce_critical_behavior_requirement,
            is_critical=True,
            side_effect_class="irreversible_write",
            requires_confirmation=False,
        ),
    )


def test_enforce_accepts_a_genuinely_enforced_critical_claim() -> None:
    for side_effect_class in ("reversible_write", "irreversible_write", "financial", "privileged"):
        check(
            "FTF-0407",
            f"acceptance: is_critical=True on a real-effect ({side_effect_class!r}) tool "
            "with requires_confirmation=True is accepted",
            _rejects(
                enforce_critical_behavior_requirement,
                is_critical=True,
                side_effect_class=side_effect_class,
                requires_confirmation=True,
            )
            is False,
        )


def test_enforce_refuses_unknown_side_effect_class_regardless_of_is_critical() -> None:
    check(
        "FTF-0407",
        "an unrecognized side_effect_class is refused explicitly even when is_critical=False",
        _rejects(
            enforce_critical_behavior_requirement,
            is_critical=False,
            side_effect_class="dangerous",
            requires_confirmation=False,
        ),
    )


# -- 5. No regression to FTF-0406's own certified evidence -------------------


def test_no_regression_high_risk_tool_may_stay_non_critical() -> None:
    check(
        "FTF-0407",
        "no regression: a high-risk, confirmed tool with is_critical=False (FTF-0406's own "
        "certified fixture shape) is still accepted -- this task deliberately does not force "
        "is_critical=True on a high-risk resolution",
        _rejects(
            enforce_critical_behavior_requirement,
            is_critical=False,
            side_effect_class="irreversible_write",
            requires_confirmation=True,
        )
        is False,
    )


# -- 6. Real live wiring: ToolDefinition.__init__ ----------------------------


def _make_tool(**overrides: object) -> ToolDefinition:
    now = datetime.now(UTC)
    input_schema = {"type": "object", "properties": {"order_id": {"type": "string"}}}
    output_schema = {"type": "object", "properties": {"status": {"type": "string"}}}
    kwargs: dict[str, object] = {
        "organization_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "name": "lookup_order",
        "description": "Looks up an order by ID.",
        "input_schema_json": input_schema,
        "output_schema_json": output_schema,
        "side_effect_class": "read_only",
        "requires_confirmation": False,
        "is_critical": False,
        "version": 1,
        "schema_hash": compute_schema_hash(input_schema, output_schema),
        "previous_version_id": None,
        "superseded_at": None,
        "created_by": "user-1",
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(overrides)
    return ToolDefinition(**kwargs)


def test_construction_refuses_unmeasurable_critical_read_only_tool_before_any_db() -> None:
    try:
        _make_tool(is_critical=True)
        raised = False
    except ToolDefinitionValidationError:
        raised = True
    check(
        "FTF-0407",
        "constructing a read_only ToolDefinition claimed is_critical=True fails at "
        "construction time, before any session or database is ever touched",
        raised,
    )


def test_construction_refuses_conflicting_unenforced_critical_tool() -> None:
    try:
        _make_tool(
            name="update_order_status",
            description="Updates the status of an order.",
            side_effect_class="reversible_write",
            requires_confirmation=False,
            is_critical=True,
        )
        raised = False
    except ToolDefinitionValidationError:
        raised = True
    check(
        "FTF-0407",
        "constructing a reversible_write ToolDefinition claimed is_critical=True with "
        "requires_confirmation=False fails at construction time",
        raised,
    )


def test_construction_accepts_a_genuinely_enforced_critical_tool() -> None:
    tool = _make_tool(
        name="update_order_status",
        description="Updates the status of an order.",
        side_effect_class="reversible_write",
        requires_confirmation=True,
        is_critical=True,
    )
    check(
        "FTF-0407",
        "a critical, real-effect, confirmed tool constructs successfully",
        tool.is_critical is True and tool.requires_confirmation is True,
    )


def test_construction_no_regression_high_risk_non_critical_tool_still_succeeds() -> None:
    # Byte-for-byte the same shape FTF-0406's own certified
    # test_construction_accepts_a_correctly_confirmed_high_risk_tool uses --
    # this task must not regress it (Law 14).
    tool = _make_tool(
        name="delete_account",
        description="Permanently deletes a user account.",
        side_effect_class="irreversible_write",
        requires_confirmation=True,
        is_critical=False,
    )
    check(
        "FTF-0407",
        "no regression: FTF-0406's own certified high-risk/non-critical/confirmed fixture "
        "still constructs successfully",
        tool.side_effect_class == "irreversible_write" and tool.is_critical is False,
    )


def test_construction_no_regression_default_read_only_fixture_still_succeeds() -> None:
    tool = _make_tool()
    check(
        "FTF-0407",
        "no regression: the plain read_only/non-critical default fixture shape "
        "(FTF-0404/FTF-0405/FTF-0406's own) still constructs successfully",
        tool.side_effect_class == "read_only" and tool.is_critical is False,
    )


# -- 7. derive_critical_behavior_requirement_view ----------------------------


def test_view_marks_read_only_unmeasurable() -> None:
    view = derive_critical_behavior_requirement_view(
        tool_definition_id="11111111-1111-1111-1111-111111111111",
        name="lookup_order",
        side_effect_class="read_only",
        is_critical=False,
        requires_confirmation=False,
    )
    check("FTF-0407", "a read_only row is reported as not measurable", view.measurable is False)
    check(
        "FTF-0407",
        "a read_only row with no confirmation reports runtime_enforced=False",
        view.runtime_enforced is False,
    )


def test_view_marks_a_real_effect_row_measurable_and_enforced() -> None:
    view = derive_critical_behavior_requirement_view(
        tool_definition_id="22222222-2222-2222-2222-222222222222",
        name="delete_account",
        side_effect_class="irreversible_write",
        is_critical=False,
        requires_confirmation=True,
    )
    check(
        "FTF-0407",
        "an irreversible_write row is reported as measurable regardless of is_critical",
        view.measurable is True,
    )
    check(
        "FTF-0407",
        "a confirmed row reports runtime_enforced=True",
        view.runtime_enforced is True,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0407 Critical behavior requirements tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0407 Critical behavior requirements tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
