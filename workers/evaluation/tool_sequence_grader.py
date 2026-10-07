"""Tool-sequence grader (FTF-0608, Section 23/24/25/26/69).

Section 23's grader-type list names `tool_sequence` fifth, one of the
deterministic grader types the evaluation engine must prioritize. This
module implements exactly that grader, standing alone from its sibling
grader-type tasks (each its own FTF-06xx task on FTF-0601).

This task's own acceptance clause ("Support declared sequence/partial-order/
state constraints; inspect actual trace and outcome") and negative_acceptance
clause ("Alternative valid orders only accepted when predefined; extra
forbidden tool call always fails") shape the design:

  - **actual trace, never text**: the only input for "what the candidate did"
    is a sequence of `TracedToolCall` records -- a protocol-level dataclass
    (`tool_call_id`, `name`) of calls the runtime really dispatched. A bare
    string, a mapping or free text is refused outright (`ToolSequenceGraderError`),
    and a repeated `tool_call_id` is refused too (one real call cannot be
    counted twice, and two calls cannot share an identity).
  - **declared constraints, all explicit, none defaulted**:
      * `allowed_orders` -- every acceptable exact order is predefined. The
        trace's call names must equal one of them in full (no missing, no
        extra, no reordered call). An order nobody declared is never
        accepted, even when it "looks equivalent". Empty means this
        constraint is not used.
      * `must_precede` -- partial-order pairs `(before, after)`: whenever
        `after` is called, a `before` call must already have happened
        earlier in the trace. A trace that never calls `after` satisfies the
        pair; a call to `after` with no earlier `before` violates it.
      * `must_not_call` -- forbidden tool names. Any call to one fails the
        grade regardless of every other constraint, including a trace that
        matches an allowed order (an allowed order that itself contains a
        forbidden name is a contradictory contract and is refused up front).
      * `required_state` + `final_state` -- the actual outcome the runtime
        recorded must contain each required key with a type-strict equal
        value (`True` is not `1`, `1` is not `1.0`).
  - **no vacuous pass**: a specification with no constraint at all would
    pass every trace, so it is refused rather than graded.
  - Membership and ordering use exact string equality only; there is no
    substring, case-folding or fuzzy matching anywhere.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "ToolSequenceGraderError",
    "ToolSequenceResult",
    "TracedToolCall",
    "grade_tool_sequence",
]


@dataclass(frozen=True)
class TracedToolCall:
    """One tool invocation the runtime really dispatched, in trace order.
    `tool_call_id` is the runtime's own call identity; `name` is the exact
    tool identifier. A mention of a tool in assistant text is never one of
    these."""

    tool_call_id: str
    name: str


class ToolSequenceGraderError(ValueError):
    """The grader was called with a malformed trace or specification.
    Never raised for a legitimately non-compliant trace -- that is a normal
    `passed=False` result."""


@dataclass(frozen=True)
class ToolSequenceResult:
    passed: bool
    called_names: tuple[str, ...]
    matched_order_index: int | None
    order_ok: bool
    forbidden_calls: tuple[str, ...]
    precedence_violations: tuple[tuple[str, str], ...]
    state_mismatches: tuple[str, ...]


def _names(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ToolSequenceGraderError(
            f"{field_name} must be a sequence of tool-name strings, not {type(value).__name__}"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ToolSequenceGraderError(
                f"every value in {field_name} must be a non-empty string, not {item!r}"
            )
        out.append(item)
    return tuple(out)


def _strict_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        assert isinstance(right, Mapping)
        return left.keys() == right.keys() and all(
            _strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list | tuple):
        assert isinstance(right, list | tuple)
        return len(left) == len(right) and all(
            _strict_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def grade_tool_sequence(
    trace: Sequence[TracedToolCall],
    *,
    allowed_orders: Sequence[Sequence[str]],
    must_precede: Sequence[tuple[str, str]],
    must_not_call: Sequence[str],
    required_state: Mapping[str, object],
    final_state: Mapping[str, object],
) -> ToolSequenceResult:
    """Grades the real tool-call trace and recorded outcome against the
    declared constraints. See the module docstring for each constraint's
    exact meaning. Raises `ToolSequenceGraderError` for a malformed trace or
    specification; a non-compliant trace returns `passed=False`."""
    if not isinstance(trace, Sequence) or isinstance(trace, str | bytes):
        raise ToolSequenceGraderError(
            f"trace must be a sequence of TracedToolCall, not {type(trace).__name__}"
        )
    seen_ids: set[str] = set()
    for call in trace:
        if not isinstance(call, TracedToolCall):
            raise ToolSequenceGraderError(
                f"every trace entry must be a real TracedToolCall record, not {call!r} -- "
                "a free-text mention of a tool is never a call"
            )
        if not isinstance(call.tool_call_id, str) or not call.tool_call_id:
            raise ToolSequenceGraderError("a traced tool call has an empty or non-string id")
        if not isinstance(call.name, str) or not call.name:
            raise ToolSequenceGraderError(f"traced tool call {call.tool_call_id!r} has no name")
        if call.tool_call_id in seen_ids:
            raise ToolSequenceGraderError(f"duplicate tool_call_id {call.tool_call_id!r} in trace")
        seen_ids.add(call.tool_call_id)

    if not isinstance(allowed_orders, Sequence) or isinstance(allowed_orders, str | bytes):
        raise ToolSequenceGraderError("allowed_orders must be a sequence of name sequences")
    orders = tuple(_names(o, field_name="allowed_orders[*]") for o in allowed_orders)
    if any(not o for o in orders):
        raise ToolSequenceGraderError("an allowed order must not be empty")
    forbidden = _names(must_not_call, field_name="must_not_call")

    if not isinstance(must_precede, Sequence) or isinstance(must_precede, str | bytes):
        raise ToolSequenceGraderError("must_precede must be a sequence of (before, after) pairs")
    pairs: list[tuple[str, str]] = []
    for pair in must_precede:
        names = _names(pair, field_name="must_precede[*]")
        if len(names) != 2 or names[0] == names[1]:
            raise ToolSequenceGraderError(
                f"must_precede entries must be two distinct tool names, not {pair!r}"
            )
        pairs.append((names[0], names[1]))

    if not isinstance(required_state, Mapping) or not isinstance(final_state, Mapping):
        raise ToolSequenceGraderError("required_state and final_state must be mappings")
    if any(not isinstance(k, str) or not k for k in required_state):
        raise ToolSequenceGraderError("required_state keys must be non-empty strings")

    if not (orders or pairs or forbidden or required_state):
        raise ToolSequenceGraderError(
            "the specification declares no constraint; it would pass every trace"
        )
    for order in orders:
        clash = [name for name in order if name in forbidden]
        if clash:
            raise ToolSequenceGraderError(
                f"allowed order {list(order)!r} contains forbidden tool(s) {clash!r}"
            )

    called = tuple(call.name for call in trace)

    matched: int | None = None
    for index, order in enumerate(orders):
        if called == order:
            matched = index
            break
    order_ok = not orders or matched is not None

    forbidden_calls = tuple(name for name in called if name in forbidden)

    violations: list[tuple[str, str]] = []
    for before, after in pairs:
        if after in called:
            first_after = called.index(after)
            if before not in called[:first_after]:
                violations.append((before, after))

    mismatches = tuple(
        key
        for key, expected in required_state.items()
        if key not in final_state or not _strict_equal(final_state[key], expected)
    )

    passed = order_ok and not forbidden_calls and not violations and not mismatches
    return ToolSequenceResult(
        passed=passed,
        called_names=called,
        matched_order_index=matched,
        order_ok=order_ok,
        forbidden_calls=forbidden_calls,
        precedence_violations=tuple(violations),
        state_mismatches=mismatches,
    )
