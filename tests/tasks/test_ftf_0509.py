#!/usr/bin/env python3
"""FTF-0509 -- Tool argument validator.

Runnable without any database and without any external network egress:
`validate_tool_calls` is a pure, synchronous, no-I/O function (this task's
own `database_changes` clause -- "No independent schema change. Consume
frozen contract."). Like FTF-0506/FTF-0507, tool-argument (and, here,
tool-result) shape checking is dependency-injected through an
`instance_validator` callable rather than importing across the
`workers/data` <-> `apps/api` deployable-unit boundary -- this suite wires
in FTF-0405's own already-CERTIFIED `validate_instance` directly,
unmodified, proving real composition rather than a hand-rolled stand-in
(`test_tool_arguments_validate_against_a_real_ftf_0405_schema`).

This task's own acceptance clause ("Validate arguments against exact
locked tool schema and optional reviewed output contract") is covered by
`test_accepts_valid_arguments_against_the_locked_schema`,
`test_accepts_when_no_tool_result_is_supplied_output_check_is_skipped`,
`test_validates_supplied_tool_result_against_output_schema`, and
`test_empty_tool_calls_is_not_applicable`.

This task's own negative_acceptance ("Schema coercion, extra critical
arguments or wrong manifest/tool version blocks acceptance") is covered by
`test_rejects_schema_coercion_wrong_type`,
`test_rejects_extra_critical_argument`,
`test_extra_argument_allowed_when_tool_is_not_critical`,
`test_rejects_wrong_tool_version`,
`test_rejects_unmapped_tool`,
`test_rejects_missing_instance_validator`,
`test_rejects_invalid_tool_result`, and
`test_rejects_non_sequence_batch`.

Run directly: python3 tests/tasks/test_ftf_0509.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from workers.data.tool_argument_validator import (  # noqa: E402
    LockedToolContract,
    ToolCallValidationError,
    validate_tool_calls,
)

from app.services.json_schema_tool_validation import validate_instance  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_LOOKUP_ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
    "additionalProperties": True,
}

_ISSUE_REFUND_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "amount_cents": {"type": "integer"},
    },
    "required": ["order_id", "amount_cents"],
    "additionalProperties": True,
}

_ORDER_STATUS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}


def _lookup_order_contract(**overrides: object) -> LockedToolContract:
    fields: dict[str, object] = {
        "name": "lookup_order",
        "version": 1,
        "input_schema_json": _LOOKUP_ORDER_SCHEMA,
        "is_critical": False,
        "output_schema_json": _ORDER_STATUS_OUTPUT_SCHEMA,
    }
    fields.update(overrides)
    return LockedToolContract(**fields)  # type: ignore[arg-type]


def _issue_refund_contract(**overrides: object) -> LockedToolContract:
    fields: dict[str, object] = {
        "name": "issue_refund",
        "version": 3,
        "input_schema_json": _ISSUE_REFUND_SCHEMA,
        "is_critical": True,
        "output_schema_json": None,
    }
    fields.update(overrides)
    return LockedToolContract(**fields)  # type: ignore[arg-type]


def _manifest(*contracts: LockedToolContract) -> dict[str, LockedToolContract]:
    return {contract.name: contract for contract in contracts}


def _current_versions(*contracts: LockedToolContract) -> dict[str, int]:
    return {contract.name: contract.version for contract in contracts}


def test_accepts_valid_arguments_against_the_locked_schema() -> None:
    contract = _lookup_order_contract()
    report = validate_tool_calls(
        [{"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "abc123"}}],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
    )
    check(
        "FTF-0509",
        "a real schema-valid tool call against a real FTF-0405 validate_instance is accepted",
        report.is_clean and report.tool_schema_status == "valid",
    )


def test_tool_arguments_validate_against_a_real_ftf_0405_schema() -> None:
    contract = _issue_refund_contract()
    report = validate_tool_calls(
        [
            {
                "id": "call_1",
                "name": "issue_refund",
                "arguments": {"order_id": "abc123", "amount_cents": 500},
            }
        ],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
    )
    check(
        "FTF-0509",
        "composes for real with FTF-0405's own validate_instance, not a stand-in",
        report.is_clean and report.tool_schema_status == "valid",
    )


def test_empty_tool_calls_is_not_applicable() -> None:
    report = validate_tool_calls(
        [],
        locked_manifest={},
        current_tool_versions={},
        instance_validator=validate_instance,
    )
    check(
        "FTF-0509",
        "a record with no tool calls at all is not_applicable, matching FTF-0508's own enum",
        report.is_clean and report.tool_schema_status == "not_applicable",
    )


def test_accepts_when_no_tool_result_is_supplied_output_check_is_skipped() -> None:
    contract = _lookup_order_contract()
    report = validate_tool_calls(
        [{"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "abc123"}}],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
        tool_results=None,
    )
    check(
        "FTF-0509",
        "the reviewed output contract is optional -- no supplied result means no output check",
        report.is_clean,
    )


def test_validates_supplied_tool_result_against_output_schema() -> None:
    contract = _lookup_order_contract()
    report = validate_tool_calls(
        [{"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "abc123"}}],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
        tool_results={"call_1": {"status": "shipped"}},
    )
    check(
        "FTF-0509",
        "a real, schema-valid supplied tool result is accepted",
        report.is_clean,
    )


def test_rejects_invalid_tool_result() -> None:
    contract = _lookup_order_contract()
    report = validate_tool_calls(
        [{"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "abc123"}}],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
        tool_results={"call_1": {"status": 12345}},
    )
    check(
        "FTF-0509",
        "a supplied tool result violating the reviewed output contract is rejected",
        not report.is_clean
        and report.tool_schema_status == "invalid"
        and any(issue.code == "invalid_result" for issue in report.issues),
    )


def test_rejects_schema_coercion_wrong_type() -> None:
    contract = _issue_refund_contract()
    report = validate_tool_calls(
        [
            {
                "id": "call_1",
                "name": "issue_refund",
                "arguments": {"order_id": "abc123", "amount_cents": "500"},
            }
        ],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
    )
    check(
        "FTF-0509",
        "a numeric-string argument is never coerced into an integer field -- rejected outright",
        not report.is_clean
        and report.tool_schema_status == "invalid"
        and any(issue.code == "invalid_arguments" for issue in report.issues),
    )


def test_rejects_extra_critical_argument() -> None:
    contract = _issue_refund_contract()
    report = validate_tool_calls(
        [
            {
                "id": "call_1",
                "name": "issue_refund",
                "arguments": {
                    "order_id": "abc123",
                    "amount_cents": 500,
                    "override_approval": True,
                },
            }
        ],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
    )
    check(
        "FTF-0509",
        "an undeclared argument on a critical tool is rejected even though the schema's own "
        "additionalProperties is True",
        not report.is_clean
        and report.tool_schema_status == "invalid"
        and any(issue.code == "extra_critical_argument" for issue in report.issues),
    )


def test_extra_argument_allowed_when_tool_is_not_critical() -> None:
    contract = _lookup_order_contract()
    report = validate_tool_calls(
        [
            {
                "id": "call_1",
                "name": "lookup_order",
                "arguments": {"order_id": "abc123", "locale": "en-US"},
            }
        ],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
    )
    check(
        "FTF-0509",
        "the extra-critical-argument guard is scoped to is_critical tools only -- a "
        "non-critical tool's own additionalProperties=True is respected",
        report.is_clean,
    )


def test_rejects_wrong_tool_version() -> None:
    locked = _lookup_order_contract(version=2)
    report = validate_tool_calls(
        [{"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "abc123"}}],
        locked_manifest=_manifest(locked),
        current_tool_versions={"lookup_order": 3},
        instance_validator=validate_instance,
    )
    check(
        "FTF-0509",
        "a tool call is rejected when the locked manifest version no longer matches the "
        "tool's currently active version (Section 7A.9 material drift)",
        not report.is_clean
        and report.tool_schema_status == "invalid"
        and any(issue.code == "wrong_tool_version" for issue in report.issues),
    )


def test_rejects_unmapped_tool() -> None:
    report = validate_tool_calls(
        [{"id": "call_1", "name": "delete_account", "arguments": {}}],
        locked_manifest={},
        current_tool_versions={},
        instance_validator=validate_instance,
    )
    check(
        "FTF-0509",
        "a tool absent from the locked manifest is refused as unmapped, never guessed",
        not report.is_clean and any(issue.code == "unmapped_tool" for issue in report.issues),
    )


def test_rejects_missing_instance_validator() -> None:
    contract = _lookup_order_contract()
    report = validate_tool_calls(
        [{"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "abc123"}}],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=None,
    )
    check(
        "FTF-0509",
        "no instance_validator supplied refuses the call outright rather than accepting "
        "arguments on trust",
        not report.is_clean
        and any(issue.code == "no_instance_validator" for issue in report.issues),
    )


def test_rejects_non_sequence_batch() -> None:
    try:
        validate_tool_calls(
            "not-a-sequence",  # type: ignore[arg-type]
            locked_manifest={},
            current_tool_versions={},
            instance_validator=validate_instance,
        )
    except ToolCallValidationError:
        pass
    else:
        check(
            "FTF-0509",
            "a non-sequence tool_calls batch raises, there is no call to attach evidence to",
            False,
        )


def test_row_evidence_reports_call_position_and_id() -> None:
    contract = _lookup_order_contract()
    report = validate_tool_calls(
        [
            {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "abc123"}},
            {"id": "call_2", "name": "unknown_tool", "arguments": {}},
        ],
        locked_manifest=_manifest(contract),
        current_tool_versions=_current_versions(contract),
        instance_validator=validate_instance,
    )
    bad = [issue for issue in report.issues if issue.code == "unmapped_tool"]
    check(
        "FTF-0509",
        "row evidence names the failing call's own position and id, not just a bare message",
        len(bad) == 1 and bad[0].call_position == 1 and bad[0].call_id == "call_2",
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0509 tool argument validator: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0509 tool argument validator: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
