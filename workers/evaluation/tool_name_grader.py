"""Tool-name grader (FTF-0606, Section 23/24/26/29/69).

Section 23's own grader-type list names `tool_name` third, right after
`json_schema`, as one of the deterministic grader types the evaluation
engine must prioritize. This module implements exactly that one grader,
standing alone from the other grader-type tasks
(`exact_match`/`json_schema`/`tool_arguments`/`tool_sequence`/
`forbidden_action`/`required_refusal`/`numeric_tolerance`/`latency`/`cost`/
`llm_judge`), each its own separately dependency-gated FTF-06xx task on
FTF-0601.

This task's own acceptance clause ("Tool-name grader verifies emitted tool
protocol and allowed/forbidden sets") and negative_acceptance clause
("Mention of tool in natural language is not an executed valid tool call")
shape this module's design, grounded in Section 24's own evaluation-case
shape (`must_not_call`, `must_call_one_of`, both sets of exact tool
identifiers, never free text):

  - **emitted tool protocol**: the only input this grader ever accepts for
    "what the candidate actually did" is a sequence of `EmittedToolCall`
    records -- a real, protocol-level dataclass with a `tool_call_id` and
    `name`, the shape Section 25's own evaluation runtime loop records for
    an actual parsed, dispatched tool call. There is no code path that
    accepts a bare string, a `Mapping`, or an assistant message's free-text
    `content` and treats it as a call -- passing anything other than a real
    `EmittedToolCall` instance is refused outright (`ToolNameGraderError`),
    never silently coerced or pattern-matched against. This is the module's
    whole answer to "mention in natural language is not an executed valid
    tool call": a mention can never even reach the comparison logic, because
    it is not shaped like a call in the first place.

  - **allowed/forbidden sets**: `must_call_one_of` (a tool-name set where any
    one call satisfies the requirement -- empty means no required call) and
    `must_not_call` (a tool-name set where any call is a critical, blocking
    violation regardless of whether a required call also happened) are both
    required keyword-only arguments with no default, each a sequence of
    non-empty strings. Membership is decided by Python string `==` only --
    no substring/`in`/`startswith`/case-folding/edit-distance -- so a tool
    named `request_approval_v2` is never treated as satisfying a requirement
    naming `request_approval`, and a forbidden name is never partially
    matched into a false positive either. Tool identifiers are protocol
    constants, not natural-language text, so (unlike FTF-0604's own
    case/whitespace policy) there is deliberately no normalization knob
    here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "EmittedToolCall",
    "ToolNameGraderError",
    "ToolNameResult",
    "grade_tool_name",
]


@dataclass(frozen=True)
class EmittedToolCall:
    """One real, protocol-level tool invocation the candidate actually
    executed -- never a free-text mention of a tool's name. `tool_call_id`
    is the runtime's own call identity (Section 25's own recorded "tool
    call" turn); `name` is the exact tool identifier the runtime dispatched.
    Both are required, non-empty strings -- there is no code path that
    builds this record from a bare string or a dict, so a caller cannot
    construct a convincing-looking fake from assistant message text without
    going through this explicit, named constructor."""

    tool_call_id: str
    name: str


class ToolNameGraderError(ValueError):
    """The grader itself was called with a malformed emitted-call list or
    expectation contract. Never raised for a legitimate non-compliant
    tool-call sequence -- that is a normal graded `passed=False` result,
    not an error."""


@dataclass(frozen=True)
class ToolNameResult:
    passed: bool
    called_names: tuple[str, ...]
    forbidden_calls: tuple[str, ...]
    satisfied_required_one_of: bool
    must_call_one_of: tuple[str, ...]
    must_not_call: tuple[str, ...]


def _validate_name_set(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ToolNameGraderError(
            f"{field_name} must be a sequence of tool-name strings, " f"not {type(value).__name__}"
        )
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ToolNameGraderError(
                f"every value in {field_name} must be a non-empty string, " f"not {item!r}"
            )
        names.append(item)
    return tuple(names)


def grade_tool_name(
    emitted_tool_calls: Sequence[EmittedToolCall],
    *,
    must_call_one_of: Sequence[str],
    must_not_call: Sequence[str],
) -> ToolNameResult:
    """Grades the real, protocol-level tool calls the candidate emitted
    against an explicit allowed set (`must_call_one_of` -- any one call
    satisfies it; empty means no required call) and an explicit forbidden
    set (`must_not_call` -- any one matching call is a blocking violation,
    even if a required call also happened). Raises `ToolNameGraderError`
    for a malformed call; otherwise always returns a result (a genuine
    non-compliant sequence is `passed=False`, not an error)."""
    if not isinstance(emitted_tool_calls, Sequence) or isinstance(emitted_tool_calls, str | bytes):
        raise ToolNameGraderError(
            "emitted_tool_calls must be a sequence of EmittedToolCall, "
            f"not {type(emitted_tool_calls).__name__}"
        )
    for call in emitted_tool_calls:
        if not isinstance(call, EmittedToolCall):
            raise ToolNameGraderError(
                "every emitted tool call must be a real EmittedToolCall "
                f"record (a protocol-level dispatch), not {call!r} -- a "
                "free-text mention of a tool's name is never a valid "
                "emitted call"
            )
        if not isinstance(call.tool_call_id, str) or not call.tool_call_id:
            raise ToolNameGraderError(
                f"emitted tool call has an invalid tool_call_id {call.tool_call_id!r}: "
                "must be a non-empty string"
            )
        if not isinstance(call.name, str) or not call.name:
            raise ToolNameGraderError(
                f"emitted tool call {call.tool_call_id!r} has an invalid name "
                f"{call.name!r}: must be a non-empty string"
            )

    required = _validate_name_set(must_call_one_of, field_name="must_call_one_of")
    forbidden = _validate_name_set(must_not_call, field_name="must_not_call")

    called_names = tuple(call.name for call in emitted_tool_calls)
    forbidden_calls = tuple(name for name in called_names if name in forbidden)
    satisfied_required_one_of = not required or any(name in required for name in called_names)
    passed = not forbidden_calls and satisfied_required_one_of

    return ToolNameResult(
        passed=passed,
        called_names=called_names,
        forbidden_calls=forbidden_calls,
        satisfied_required_one_of=satisfied_required_one_of,
        must_call_one_of=required,
        must_not_call=forbidden,
    )
