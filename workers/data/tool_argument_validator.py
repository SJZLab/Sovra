"""Tool argument validator (FTF-0509, Section 7/8.1/8.2/22/65-66/77/81).

Section 8.1's import pipeline step 8 ("Validate tool calls") has, until this
task, no real implementation anywhere in this repository:
`record_schema_validator.py`'s (FTF-0508, already-CERTIFIED) own docstring
discloses this exact gap by name ("It does not itself ... validate
tool-call arguments -- those are separate, later pipeline stages"). This
module is that later stage.

This task's own acceptance clause ("Validate arguments against exact
locked tool schema and optional reviewed output contract") and
negative_acceptance clause ("Schema coercion, extra critical arguments or
wrong manifest/tool version blocks acceptance") shape this module's design:

  - **exact locked tool schema**: `LockedToolContract` is this module's own
    caller-supplied snapshot of one project tool at Agent-Manifest-lock
    time (Section 7A.7) -- `name`/`version`/`input_schema_json`/
    `output_schema_json`/`is_critical` are exactly the fields FTF-0404's
    already-CERTIFIED `ToolDefinition` and FTF-0407's already-CERTIFIED
    `is_critical` enforcement already fix, reused by name rather than
    re-invented (Section 77.1 reuse-first). This module never reads
    `ToolDefinition` itself, or any database: `workers/data` has no DB
    session anywhere and does not import `apps/api` (the same cross-package
    worker/app boundary FTF-0506's/FTF-0507's own docstrings already
    disclose and preserve -- "nothing else in this repository imports
    across that boundary in either direction"), so both the locked
    snapshot and each tool's *currently active* version are caller-supplied
    inputs, not looked up here.

  - **schema coercion blocks acceptance**: inherited by construction, not
    reimplemented. Every argument object is validated through a
    caller-supplied `instance_validator` -- the exact
    `(schema, instance, *, field_name=...) -> None` call shape FTF-0506's
    already-CERTIFIED `ToolArgumentValidator` Protocol already fixes
    (`canonical_conversation_normalizer.ToolArgumentValidator`, imported
    and reused directly here, not redefined a second time), matching
    FTF-0405's already-CERTIFIED `validate_instance`, which performs no
    type coercion at all (an `integer`-typed field rejects a numeric
    *string* outright -- see that module's own `_matches_type`). A missing
    `instance_validator` refuses every call outright rather than accepting
    arguments on trust -- the same "no effect authorized ... when nothing
    can prove it" discipline (Section 84.4) FTF-0506 already applies at its
    own call site, reapplied here at this module's own entry point.

  - **extra critical arguments blocks acceptance**: for a contract marked
    `is_critical` (FTF-0407's own already-CERTIFIED field), any argument
    key absent from the schema's own declared `properties` is rejected
    outright by this module's own independent check, in addition to
    whatever `instance_validator` itself decides -- regardless of that
    schema's own `additionalProperties` setting. Section 84.4's "no effect
    authorized solely by a UI control" is reapplied here as: a critical
    tool's blast radius is never silently widened by an argument its own
    schema never declared, even where the schema itself would tolerate it.

  - **wrong manifest/tool version blocks acceptance**: grounded in Section
    7A.9's own material-drift vocabulary ("tool added/removed/schema
    changed" invalidates affected evidence "until the project is re-synced,
    reviewed, and re-qualified"). A tool call is rejected when the locked
    contract's own `version` no longer matches that tool's *currently
    active* version (`current_tool_versions`, caller-supplied) -- the
    dataset record's tool call was captured against (or is being validated
    against) a manifest snapshot the live tool definition has since moved
    past, and accepting it anyway would silently paper over exactly the
    drift Section 7A.9 requires to block until requalification.

  - **optional reviewed output contract**: a tool call's *result* is
    validated against the contract's own `output_schema_json` only when the
    caller supplies one for that call id (`tool_results`) -- optional
    because Section 7's own canonical schema allows the `tool` result
    message to be normalized in a separate pass (FTF-0507) from the
    `assistant` message carrying the call itself; this module never
    requires both to be present in the same invocation.

`tool_schema_status` reuses FTF-0508's own already-CERTIFIED three-value
enum (`not_applicable`/`valid`/`invalid`) verbatim, imported directly from
`record_schema_validator.py` rather than re-declared -- this module is the
real place that decides which of those three values a batch of tool calls
earns.

Scope decision, same disclosed precedent as FTF-0506/FTF-0507/FTF-0508/
FTF-0511/FTF-0512/FTF-0514 ("no live caller yet"): this task's own packet
`files` list names only this module and its test, not an importer/route.
No dataset-import pipeline route exists yet in this repository to call
`validate_tool_calls`. `proving_symbol` stays blank; `product_truth_check.py`'s
product-true count is deliberately unchanged.

Path refinement recorded before READY (Section 84.3): the packet names
`services/training-worker/data/tool_argument_validator.py`, but this repo's
real `SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the real
data/review worker root -- there is no `services/` tree in this repository.
Placed alongside FTF-0506's/FTF-0507's/FTF-0508's own modules instead of
creating a second, non-existent tree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from workers.data.canonical_conversation_normalizer import ToolArgumentValidator
from workers.data.record_schema_validator import TOOL_SCHEMA_STATUS_VALUES

__all__ = [
    "TOOL_SCHEMA_STATUS_VALUES",
    "LockedToolContract",
    "ToolCallValidationError",
    "ToolCallValidationIssue",
    "ToolCallValidationReport",
    "validate_tool_calls",
]


class ToolCallValidationError(ValueError):
    """The candidate tool-call batch itself is not a real sequence of tool
    calls -- not a sequence at all. There is no call to attach that
    failure's evidence to, so it is raised, not reported as a call issue."""


@dataclass(frozen=True)
class LockedToolContract:
    """One project tool's locked snapshot at Agent-Manifest-lock time
    (Section 7A.7) -- the exact fields FTF-0404's already-CERTIFIED
    `ToolDefinition` and FTF-0407's already-CERTIFIED `is_critical`
    enforcement already fix, reused by name."""

    name: str
    version: int
    input_schema_json: Mapping[str, object]
    is_critical: bool
    output_schema_json: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ToolCallValidationIssue:
    call_position: int
    call_id: str | None
    tool_name: str | None
    code: str
    message: str


@dataclass(frozen=True)
class ToolCallValidationReport:
    issues: tuple[ToolCallValidationIssue, ...]
    tool_schema_status: str

    @property
    def is_clean(self) -> bool:
        return not self.issues


def _declared_properties(schema: Mapping[str, object]) -> frozenset[str]:
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        return frozenset(properties)
    return frozenset()


def _validate_one(
    call_position: int,
    raw_call: object,
    *,
    locked_manifest: Mapping[str, LockedToolContract],
    current_tool_versions: Mapping[str, int],
    instance_validator: ToolArgumentValidator | None,
    tool_results: Mapping[str, object],
) -> list[ToolCallValidationIssue]:
    issues: list[ToolCallValidationIssue] = []

    if not isinstance(raw_call, Mapping):
        issues.append(
            ToolCallValidationIssue(
                call_position=call_position,
                call_id=None,
                tool_name=None,
                code="invalid_call_shape",
                message="tool call must be an object",
            )
        )
        return issues

    call_id = raw_call.get("id")
    call_id = call_id if isinstance(call_id, str) and call_id else None
    name = raw_call.get("name")
    name = name if isinstance(name, str) and name else None
    arguments = raw_call.get("arguments")

    if name is None:
        issues.append(
            ToolCallValidationIssue(
                call_position=call_position,
                call_id=call_id,
                tool_name=None,
                code="invalid_call_shape",
                message="tool call name must be a non-empty string",
            )
        )
        return issues

    if not isinstance(arguments, Mapping):
        issues.append(
            ToolCallValidationIssue(
                call_position=call_position,
                call_id=call_id,
                tool_name=name,
                code="invalid_call_shape",
                message="tool call arguments must be an object",
            )
        )
        return issues

    contract = locked_manifest.get(name)
    if contract is None:
        issues.append(
            ToolCallValidationIssue(
                call_position=call_position,
                call_id=call_id,
                tool_name=name,
                code="unmapped_tool",
                message=(
                    f"tool {name!r} is not in the locked manifest -- refusing to guess its "
                    "contract"
                ),
            )
        )
        return issues

    current_version = current_tool_versions.get(name)
    if current_version != contract.version:
        issues.append(
            ToolCallValidationIssue(
                call_position=call_position,
                call_id=call_id,
                tool_name=name,
                code="wrong_tool_version",
                message=(
                    f"tool {name!r} is locked at version {contract.version} but its "
                    f"currently active version is {current_version!r} -- Section 7A.9 "
                    "material drift, blocked until requalification"
                ),
            )
        )
        return issues

    if contract.is_critical:
        extra = set(arguments) - _declared_properties(contract.input_schema_json)
        if extra:
            issues.append(
                ToolCallValidationIssue(
                    call_position=call_position,
                    call_id=call_id,
                    tool_name=name,
                    code="extra_critical_argument",
                    message=(
                        f"critical tool {name!r} received undeclared argument(s) "
                        f"{sorted(extra)} -- never accepted, regardless of the schema's own "
                        "additionalProperties setting"
                    ),
                )
            )
            return issues

    if instance_validator is None:
        issues.append(
            ToolCallValidationIssue(
                call_position=call_position,
                call_id=call_id,
                tool_name=name,
                code="no_instance_validator",
                message=(
                    "no instance_validator was supplied -- refusing to accept a tool "
                    "call's arguments on trust when nothing can prove them"
                ),
            )
        )
        return issues

    try:
        instance_validator(
            dict(contract.input_schema_json),
            dict(arguments),
            field_name=f"tool_calls[{call_position}].arguments",
        )
    except ValueError as exc:
        issues.append(
            ToolCallValidationIssue(
                call_position=call_position,
                call_id=call_id,
                tool_name=name,
                code="invalid_arguments",
                message=f"tool arguments failed schema validation: {exc}",
            )
        )
        return issues

    if call_id is not None and call_id in tool_results and contract.output_schema_json is not None:
        try:
            instance_validator(
                dict(contract.output_schema_json),
                tool_results[call_id],
                field_name=f"tool_calls[{call_position}].result",
            )
        except ValueError as exc:
            issues.append(
                ToolCallValidationIssue(
                    call_position=call_position,
                    call_id=call_id,
                    tool_name=name,
                    code="invalid_result",
                    message=f"tool result failed the reviewed output contract: {exc}",
                )
            )

    return issues


def validate_tool_calls(
    tool_calls: Sequence[Mapping[str, object]],
    *,
    locked_manifest: Mapping[str, LockedToolContract],
    current_tool_versions: Mapping[str, int],
    instance_validator: ToolArgumentValidator | None,
    tool_results: Mapping[str, object] | None = None,
) -> ToolCallValidationReport:
    """Validates a batch of Section 7 canonical `tool_calls` entries
    (`{"id": ..., "name": ..., "arguments": {...}}`) against `locked_manifest`.
    An empty `tool_calls` sequence is `not_applicable` (Section 8.1 step 8
    does not apply to a record with no tool calls at all) -- the same
    `not_applicable` reading FTF-0508's own `TOOL_SCHEMA_STATUS_VALUES`
    already fixes. Never raises on a per-call violation; raises
    `ToolCallValidationError` only when `tool_calls` itself is not a real
    sequence."""
    if not isinstance(tool_calls, Sequence) or isinstance(tool_calls, str | bytes):
        raise ToolCallValidationError("tool_calls must be a sequence of candidate tool calls")

    if not tool_calls:
        return ToolCallValidationReport(issues=(), tool_schema_status="not_applicable")

    resolved_tool_results: Mapping[str, object] = tool_results if tool_results is not None else {}

    all_issues: list[ToolCallValidationIssue] = []
    for call_position, raw_call in enumerate(tool_calls):
        all_issues.extend(
            _validate_one(
                call_position,
                raw_call,
                locked_manifest=locked_manifest,
                current_tool_versions=current_tool_versions,
                instance_validator=instance_validator,
                tool_results=resolved_tool_results,
            )
        )

    status = "invalid" if all_issues else "valid"
    return ToolCallValidationReport(issues=tuple(all_issues), tool_schema_status=status)
