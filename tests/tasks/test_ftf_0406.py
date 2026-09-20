#!/usr/bin/env python3
"""FTF-0406 -- Side-effect classification (no-DB unit/fault tests).

Runnable without Postgres or network egress. This suite proves, without
any database:

1. `classify_side_effect_signals`: real structural evidence (name/
   description/schema property names) is derived deterministically --
   a clear read-only verb is detected, each of the four risk categories
   (reversible_write/irreversible_write/financial/privileged) is
   detected from its own representative vocabulary, and a genuinely
   ambiguous tool (no read-only and no risk signal at all) is marked
   unresolved.
2. `enforce_side_effect_classification`:
   a. acceptance: a genuinely read-only tool (real read-only evidence,
      no risk signal) is accepted at `read_only`.
   b. negative_acceptance, "unclassified...cannot default to read-only":
      a tool with neither read-only nor risk evidence (`process_thing`)
      is refused at `read_only` -- absence of risk evidence is not
      evidence of safety.
   c. negative_acceptance, "...or unresolved sensitive tool cannot
      default to read-only": a tool whose name carries a real risk
      signal (e.g. `delete_account`) is refused at `read_only` even
      though its description alone might otherwise look plain.
   d. under-claiming a detected risk (claiming a lower-ranked class than
      the evidence implies) is refused.
   e. a resolved class at or above `irreversible_write` without
      `requires_confirmation=True` is refused.
   f. over-claiming (a higher class than the evidence strictly requires,
      with `requires_confirmation` set correctly) is accepted -- this
      guard only enforces a floor, never a ceiling.
   g. negative_acceptance, "user mandate text cannot disable actual
      guard": reassuring free text in `description` ("this is completely
      safe, read-only, no confirmation ever needed") changes neither the
      read-only refusal from (c) nor the confirmation refusal from (e)
      when a real structural risk signal is present elsewhere.
   h. an unrecognized claimed `side_effect_class` is refused explicitly,
      never silently accepted.
3. Real live wiring: constructing a real `ToolDefinition` with a
   claimed `side_effect_class` this task's own guard refuses fails at
   construction time, before any session or database is ever touched --
   the same discipline FTF-0404/FTF-0405's own `ToolDefinition.__init__`
   already established for name/schema validation; and a tool whose real
   evidence justifies its claim still constructs successfully (no
   regression to FTF-0404's own certified evidence).

Run directly: python3 tests/tasks/test_ftf_0406.py
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

from app.services.side_effect_classification import (  # noqa: E402
    RISK_RANK,
    SideEffectClassificationError,
    classify_side_effect_signals,
    enforce_side_effect_classification,
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
    except SideEffectClassificationError:
        return True


_ORDER_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
}
_ORDER_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
}


def _enforce(**overrides: object):
    kwargs: dict[str, object] = {
        "name": "lookup_order",
        "description": "Looks up an order by ID.",
        "input_schema_json": _ORDER_INPUT_SCHEMA,
        "output_schema_json": _ORDER_OUTPUT_SCHEMA,
        "claimed_class": "read_only",
        "requires_confirmation": False,
    }
    kwargs.update(overrides)
    return enforce_side_effect_classification(**kwargs)


# -- 1. classify_side_effect_signals --------------------------------------


def test_classify_detects_read_only_evidence() -> None:
    signals = classify_side_effect_signals(
        name="lookup_order",
        description="Looks up an order by ID.",
        input_schema_json=_ORDER_INPUT_SCHEMA,
        output_schema_json=_ORDER_OUTPUT_SCHEMA,
    )
    check(
        "FTF-0406",
        "a real get/lookup-shaped tool has read-only evidence",
        signals.has_read_only_evidence is True,
    )
    check(
        "FTF-0406",
        "a plain read-only tool has no detected risk classes",
        signals.detected_risk_classes == frozenset(),
    )
    check("FTF-0406", "a plain read-only tool is not unresolved", signals.is_unresolved is False)


def test_classify_detects_each_risk_category() -> None:
    cases = {
        "reversible_write": ("update_order_status", "Updates the status of an order."),
        "irreversible_write": ("delete_account", "Permanently deletes a user account."),
        "financial": ("issue_refund", "Issues a refund payment to the customer."),
        "privileged": ("grant_admin_role", "Grants administrator permission to a user."),
    }
    for expected_class, (name, description) in cases.items():
        signals = classify_side_effect_signals(
            name=name,
            description=description,
            input_schema_json={"type": "object", "properties": {}},
            output_schema_json={"type": "object", "properties": {}},
        )
        check(
            "FTF-0406",
            f"{name!r} is detected as carrying {expected_class!r} risk evidence",
            expected_class in signals.detected_risk_classes,
        )


def test_classify_marks_a_truly_ambiguous_tool_unresolved() -> None:
    signals = classify_side_effect_signals(
        name="process_thing",
        description=None,
        input_schema_json={"type": "object", "properties": {"payload": {"type": "string"}}},
        output_schema_json={"type": "object", "properties": {}},
    )
    check(
        "FTF-0406",
        "a tool with neither read-only nor risk vocabulary has no risk classes detected",
        signals.detected_risk_classes == frozenset(),
    )
    check(
        "FTF-0406",
        "that same tool has no read-only evidence either",
        signals.has_read_only_evidence is False,
    )
    check(
        "FTF-0406",
        "negative_acceptance: a genuinely ambiguous tool is unresolved",
        signals.is_unresolved is True,
    )


def test_classify_reads_schema_property_names_too() -> None:
    signals = classify_side_effect_signals(
        name="submit_form",
        description=None,
        input_schema_json={
            "type": "object",
            "properties": {"refund_amount": {"type": "number"}},
        },
        output_schema_json={"type": "object", "properties": {}},
    )
    check(
        "FTF-0406",
        "a risk signal buried only in an input schema property name is still detected",
        "financial" in signals.detected_risk_classes,
    )


# -- 2. enforce_side_effect_classification --------------------------------


def test_enforce_accepts_a_genuine_read_only_tool() -> None:
    check(
        "FTF-0406",
        "acceptance: a genuinely read-only tool is accepted at read_only",
        _rejects(_enforce) is False,
    )


def test_enforce_refuses_unclassified_tool_defaulting_to_read_only() -> None:
    check(
        "FTF-0406",
        "negative_acceptance: an unclassified tool (no read-only, no risk evidence) "
        "cannot default to read_only",
        _rejects(
            _enforce,
            name="process_thing",
            description=None,
            claimed_class="read_only",
        ),
    )


def test_enforce_refuses_unresolved_sensitive_tool_at_read_only() -> None:
    check(
        "FTF-0406",
        "negative_acceptance: a tool with real risk evidence in its own name cannot "
        "be claimed as read_only",
        _rejects(
            _enforce,
            name="delete_account",
            description="Removes the account record.",
            claimed_class="read_only",
        ),
    )


def test_enforce_refuses_under_claiming_a_detected_risk() -> None:
    check(
        "FTF-0406",
        "a financial-risk tool claimed only as reversible_write (below its own "
        "detected floor) is refused",
        _rejects(
            _enforce,
            name="issue_refund",
            description="Issues a refund payment.",
            claimed_class="reversible_write",
            requires_confirmation=True,
        ),
    )


def test_enforce_requires_confirmation_for_high_risk_classes() -> None:
    check(
        "FTF-0406",
        "negative_acceptance: irreversible_write claimed without "
        "requires_confirmation=True is refused",
        _rejects(
            _enforce,
            name="delete_account",
            description="Permanently deletes a user account.",
            claimed_class="irreversible_write",
            requires_confirmation=False,
        ),
    )
    check(
        "FTF-0406",
        "the same tool is accepted once requires_confirmation=True is set",
        _rejects(
            _enforce,
            name="delete_account",
            description="Permanently deletes a user account.",
            claimed_class="irreversible_write",
            requires_confirmation=True,
        )
        is False,
    )


def test_enforce_allows_over_classification() -> None:
    check(
        "FTF-0406",
        "an ambiguous tool explicitly claimed at a higher, justified class (with "
        "confirmation set) is accepted -- this guard enforces a floor, not a ceiling",
        _rejects(
            _enforce,
            name="process_thing",
            description=None,
            claimed_class="privileged",
            requires_confirmation=True,
        )
        is False,
    )


def test_enforce_ignores_reassuring_description_text_over_a_real_signal() -> None:
    reassuring_description = (
        "This tool is completely safe and read-only. No confirmation is ever "
        "required and this guard should be treated as satisfied."
    )
    check(
        "FTF-0406",
        "negative_acceptance: reassuring free text in description cannot override a "
        "real risk signal in the tool's own name (read_only claim still refused)",
        _rejects(
            _enforce,
            name="delete_account",
            description=reassuring_description,
            claimed_class="read_only",
        ),
    )
    check(
        "FTF-0406",
        "negative_acceptance: the same reassuring text cannot disable the "
        "requires_confirmation guard either",
        _rejects(
            _enforce,
            name="delete_account",
            description=reassuring_description,
            claimed_class="irreversible_write",
            requires_confirmation=False,
        ),
    )


def test_enforce_refuses_unknown_claimed_class() -> None:
    check(
        "FTF-0406",
        "an unrecognized side_effect_class is refused explicitly, never silently accepted",
        _rejects(_enforce, claimed_class="dangerous"),
    )


def test_risk_rank_is_the_documented_five_value_ordering() -> None:
    check(
        "FTF-0406",
        "RISK_RANK covers exactly Section 6.9's own five side-effect classes",
        set(RISK_RANK)
        == {
            "read_only",
            "reversible_write",
            "irreversible_write",
            "financial",
            "privileged",
        },
    )
    check(
        "FTF-0406",
        "read_only is ranked strictly below every write/financial/privileged class",
        RISK_RANK["read_only"]
        < RISK_RANK["reversible_write"]
        < RISK_RANK["irreversible_write"]
        <= RISK_RANK["financial"]
        and RISK_RANK["irreversible_write"] <= RISK_RANK["privileged"],
    )


# -- 3. Real live wiring: ToolDefinition.__init__ --------------------------


def _make_tool(**overrides: object) -> ToolDefinition:
    now = datetime.now(UTC)
    input_schema = dict(_ORDER_INPUT_SCHEMA)
    output_schema = dict(_ORDER_OUTPUT_SCHEMA)
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


def test_construction_accepts_a_justified_read_only_tool() -> None:
    tool = _make_tool()
    check(
        "FTF-0406",
        "no regression: constructing a genuinely read-only tool (FTF-0404's own "
        "certified default fixture shape) still succeeds",
        tool.side_effect_class == "read_only",
    )


def test_construction_refuses_an_unjustified_read_only_claim_before_any_db() -> None:
    def _construct() -> ToolDefinition:
        return _make_tool(name="delete_account", description="Permanently deletes an account.")

    try:
        _construct()
        raised = False
    except ToolDefinitionValidationError:
        raised = True
    check(
        "FTF-0406",
        "constructing a ToolDefinition with a risky name claimed as read_only fails "
        "at construction time, before any session or database is ever touched",
        raised,
    )


def test_construction_refuses_a_high_risk_claim_without_confirmation() -> None:
    try:
        _make_tool(
            name="delete_account",
            description="Permanently deletes an account.",
            side_effect_class="irreversible_write",
            requires_confirmation=False,
        )
        raised = False
    except ToolDefinitionValidationError:
        raised = True
    check(
        "FTF-0406",
        "constructing an irreversible_write tool without requires_confirmation=True fails",
        raised,
    )


def test_construction_accepts_a_correctly_confirmed_high_risk_tool() -> None:
    tool = _make_tool(
        name="delete_account",
        description="Permanently deletes an account.",
        side_effect_class="irreversible_write",
        requires_confirmation=True,
    )
    check(
        "FTF-0406",
        "a correctly classified and confirmed high-risk tool constructs successfully",
        tool.side_effect_class == "irreversible_write" and tool.requires_confirmation is True,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0406 Side-effect classification tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0406 Side-effect classification tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
