#!/usr/bin/env python3
"""FTF-0903 -- Content-free telemetry (no-DB unit/fault tests).

Proves, without any database or network:

1. ACC-1 (workflow/log/span schemas allow bounded metadata only): every
   field in every event kind's real schema accepts its own bounded shape
   and nothing else; `ContentRef` is accepted only as a reference, never
   as inline content.
2. NEG-1 (source snippets, arbitrary exception text or raw prompts cannot
   enter control-plane telemetry): a raw traceback, a multi-line source
   snippet and a long free-text prompt are each refused whichever field
   (id / code / enum) they are attached to, and a value that differs from
   a short code only by a single trailing newline -- the classic `$`
   anchor bypass for `re.match` -- is refused too.
3. Fail-closed input handling: unknown event kind, non-mapping `fields`,
   an undeclared field name, a missing required field, an unhashable
   field value, and a wrong-typed value for every field kind all raise
   the module's own coded errors, never a bare TypeError/AttributeError
   or a silently dropped/coerced field.
4. Fault injection (Law 1): regex mutations of the real module, run in a
   fresh subprocess, each must change behaviour; the file is restored
   byte-identical.

Run directly: python3 tests/tasks/test_ftf_0903.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.security.content_free_telemetry import (  # noqa: E402
    EVENT_KINDS,
    LOG_LEVELS,
    SPAN_STATUSES,
    WORKFLOW_STATES,
    ContentFreeTelemetryError,
    ContentRef,
    ContentRefusedError,
    DisallowedFieldError,
    InvalidEventError,
    TelemetryEvent,
    emit_event,
)

MODULE = ROOT / "packages" / "security" / "content_free_telemetry.py"
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception:
        return False
    return False


def test_event_kinds_and_enums_are_pinned() -> None:
    check(
        "FTF-0903-A1",
        "event kinds are exactly workflow/log/span",
        EVENT_KINDS == ("workflow", "log", "span"),
    )
    check(
        "FTF-0903-A1",
        "workflow states include reconciliation_required (Section 70.1)",
        "reconciliation_required" in WORKFLOW_STATES,
    )
    check(
        "FTF-0903-A1",
        "log levels are the standard four",
        LOG_LEVELS == ("debug", "info", "warning", "error"),
    )
    check("FTF-0903-A1", "span statuses are ok/error", SPAN_STATUSES == ("ok", "error"))


def test_workflow_event_accepts_only_its_bounded_schema() -> None:
    ref = ContentRef("blob-1", "key-1")
    ev = emit_event(
        "workflow",
        {
            "workflow_id": "wf-1",
            "task_id": "task-1",
            "job_id": "job-1",
            "request_id": "req-1",
            "state": "running",
            "timing_ms": 1500,
            "cost_usd": 0.42,
            "error_code": "none",
            "failure_ref": ref,
        },
    )
    check(
        "FTF-0903-A1",
        "a fully-populated workflow event is accepted",
        isinstance(ev, TelemetryEvent),
    )
    check("FTF-0903-A1", "fields are stored as given", ev.fields["state"] == "running")
    check(
        "FTF-0903-A1",
        "the content_ref field holds the ref object, not a string",
        ev.fields["failure_ref"] is ref,
    )

    minimal = emit_event(
        "workflow", {"workflow_id": "wf-1", "task_id": "task-1", "state": "queued"}
    )
    check(
        "FTF-0903-A1",
        "only the three required workflow fields suffice",
        isinstance(minimal, TelemetryEvent),
    )

    check(
        "FTF-0903-A1",
        "missing a required workflow field is refused",
        _raises(
            DisallowedFieldError, emit_event, "workflow", {"task_id": "task-1", "state": "queued"}
        ),
    )
    check(
        "FTF-0903-A1",
        "an undeclared field name is refused",
        _raises(
            DisallowedFieldError,
            emit_event,
            "workflow",
            {"workflow_id": "wf-1", "task_id": "task-1", "state": "queued", "message": "hi"},
        ),
    )


def test_log_and_span_events() -> None:
    log = emit_event("log", {"level": "error", "message_code": "db_timeout"})
    check("FTF-0903-A1", "a minimal log event is accepted", isinstance(log, TelemetryEvent))
    check(
        "FTF-0903-A1",
        "missing message_code is refused",
        _raises(DisallowedFieldError, emit_event, "log", {"level": "error"}),
    )
    check(
        "FTF-0903-A1",
        "an unknown log level is refused (closed enum, not coerced)",
        _raises(ContentRefusedError, emit_event, "log", {"level": "critical", "message_code": "x"}),
    )

    span = emit_event(
        "span",
        {
            "span_id": "sp-1",
            "parent_span_id": "sp-0",
            "name": "train_step",
            "status": "ok",
            "duration_ms": 12.5,
        },
    )
    check(
        "FTF-0903-A1", "a fully-populated span event is accepted", isinstance(span, TelemetryEvent)
    )
    check(
        "FTF-0903-A1",
        "missing required span fields is refused",
        _raises(DisallowedFieldError, emit_event, "span", {"status": "ok"}),
    )


def test_content_ref_is_a_reference_never_inline_content() -> None:
    check(
        "FTF-0903-A1",
        "a raw string is refused where a content_ref is required",
        _raises(
            ContentRefusedError,
            emit_event,
            "workflow",
            {
                "workflow_id": "wf-1",
                "task_id": "t-1",
                "state": "failed",
                "failure_ref": "Traceback (most recent call last): boom",
            },
        ),
    )
    check(
        "FTF-0903-A1",
        "a dict masquerading as a content ref is refused",
        _raises(
            ContentRefusedError,
            emit_event,
            "workflow",
            {
                "workflow_id": "wf-1",
                "task_id": "t-1",
                "state": "failed",
                "failure_ref": {"ref_id": "x", "encryption_key_id": "y"},
            },
        ),
    )
    check(
        "FTF-0903-A1",
        "ContentRef itself refuses a non-bounded ref_id",
        _raises(ContentRefusedError, ContentRef, "bad id with spaces", "key-1"),
    )


def test_timestamp_count_bool_kinds() -> None:
    from packages.security.content_free_telemetry import _FieldSpec, _validate_field

    check(
        "FTF-0903-A1",
        "a timezone-aware datetime is accepted as a timestamp",
        _validate_field("t", _FieldSpec("timestamp"), datetime.now(UTC)) is not None,
    )
    check(
        "FTF-0903-A1",
        "a naive datetime (no tzinfo) is refused",
        _raises(
            ContentRefusedError, _validate_field, "t", _FieldSpec("timestamp"), datetime(2026, 1, 1)
        ),
    )
    check(
        "FTF-0903-A1",
        "a non-negative int is accepted as a count",
        _validate_field("c", _FieldSpec("count"), 3) == 3,
    )
    check(
        "FTF-0903-A1",
        "a bool is refused as a count (bool is not treated as int)",
        _raises(ContentRefusedError, _validate_field, "c", _FieldSpec("count"), True),
    )
    check(
        "FTF-0903-A1",
        "a negative count is refused",
        _raises(ContentRefusedError, _validate_field, "c", _FieldSpec("count"), -1),
    )
    check(
        "FTF-0903-A1",
        "a bool is accepted as bool",
        _validate_field("b", _FieldSpec("bool"), False) is False,
    )
    check(
        "FTF-0903-A1",
        "an int is refused as bool",
        _raises(ContentRefusedError, _validate_field, "b", _FieldSpec("bool"), 0),
    )
    check(
        "FTF-0903-A1",
        "NaN is refused as a number",
        _raises(ContentRefusedError, _validate_field, "n", _FieldSpec("number"), float("nan")),
    )
    check(
        "FTF-0903-A1",
        "+inf is refused as a number",
        _raises(ContentRefusedError, _validate_field, "n", _FieldSpec("number"), float("inf")),
    )
    check(
        "FTF-0903-A1",
        "a number far past the bound is refused",
        _raises(ContentRefusedError, _validate_field, "n", _FieldSpec("number"), 1e18),
    )
    check(
        "FTF-0903-A1",
        "a count of exactly zero is the boundary and is accepted (< 0, not <= 0)",
        _validate_field("c", _FieldSpec("count"), 0) == 0,
    )
    check(
        "FTF-0903-A1",
        "a count of exactly the max bound is accepted (> max, not >= max)",
        _validate_field("c", _FieldSpec("count"), 1_000_000_000) == 1_000_000_000,
    )
    check(
        "FTF-0903-A1",
        "a count one past the max bound is refused",
        _raises(ContentRefusedError, _validate_field, "c", _FieldSpec("count"), 1_000_000_001),
    )


def test_field_kind_dispatch_is_an_exact_closed_match() -> None:
    """`spec.kind` is an internal dispatch key, never derived from
    untrusted input, but the dispatch itself must still be an exact,
    case- and whitespace-sensitive match: a malformed internal spec
    (never produced by this module's own schemas) must raise, never
    silently normalise to a real kind and dispatch anyway."""
    from packages.security.content_free_telemetry import _FieldSpec, _validate_field

    for weird_kind in (
        " ID ",
        " CODE ",
        " ENUM ",
        " COUNT ",
        " NUMBER ",
        " TIMESTAMP ",
        " BOOL ",
        " CONTENT_REF ",
    ):
        check(
            "FTF-0903-A2",
            f"a non-exact field kind {weird_kind!r} raises, never dispatches by a normalised match",
            _raises(
                InvalidEventError, _validate_field, "f", _FieldSpec(weird_kind, enum=("x",)), "x"
            ),
        )


def test_raw_content_is_refused_whatever_field_it_is_attached_to() -> None:
    traceback_text = 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: boom'
    source_snippet = "def f(x):\n    return x.secret_key\n"
    prompt_text = "You are a helpful assistant. The user's password is " + ("a" * 300)

    for payload, label in (
        (traceback_text, "a traceback"),
        (source_snippet, "a source snippet"),
        (prompt_text, "a long prompt"),
    ):
        check(
            "FTF-0903-N1",
            f"{label} is refused in an id field",
            _raises(
                ContentRefusedError,
                emit_event,
                "workflow",
                {"workflow_id": payload, "task_id": "t", "state": "queued"},
            ),
        )
        check(
            "FTF-0903-N1",
            f"{label} is refused in a code field",
            _raises(
                ContentRefusedError, emit_event, "log", {"level": "info", "message_code": payload}
            ),
        )
        check(
            "FTF-0903-N1",
            f"{label} is refused in an enum field",
            _raises(
                ContentRefusedError, emit_event, "log", {"level": payload, "message_code": "ok"}
            ),
        )

    # The classic `$`-anchor bypass: a value identical to a legal code
    # plus exactly one trailing newline must still be refused end-to-end,
    # and directly against the raw regexes (never `match`, always
    # `fullmatch`).
    check(
        "FTF-0903-N1",
        "a code value with one trailing newline is refused (no $ anchor bypass)",
        _raises(ContentRefusedError, emit_event, "log", {"level": "info", "message_code": "ok\n"}),
    )
    check(
        "FTF-0903-N1",
        "an id value with one trailing newline is refused (no $ anchor bypass)",
        _raises(
            ContentRefusedError,
            emit_event,
            "workflow",
            {"workflow_id": "wf-1\n", "task_id": "t-1", "state": "queued"},
        ),
    )
    check(
        "FTF-0903-N1",
        "a code value with a second line after a real newline is refused",
        _raises(
            ContentRefusedError,
            emit_event,
            "log",
            {"level": "info", "message_code": "ok\nSELECT * FROM secrets"},
        ),
    )


def test_fail_closed_input_handling() -> None:
    check(
        "FTF-0903-A2",
        "an unknown event kind raises InvalidEventError",
        _raises(InvalidEventError, emit_event, "trace", {}),
    )
    check(
        "FTF-0903-A2",
        "an event kind with different case/whitespace is refused, never normalised",
        _raises(InvalidEventError, emit_event, " WORKFLOW ", {}),
    )
    check(
        "FTF-0903-A2",
        "a non-string event kind raises InvalidEventError",
        _raises(InvalidEventError, emit_event, 1, {}),
    )
    check(
        "FTF-0903-A2",
        "fields that is not a mapping raises InvalidEventError",
        _raises(InvalidEventError, emit_event, "log", [("level", "info")]),
    )
    check(
        "FTF-0903-A2",
        "an unhashable field value (a list) raises our own error, not TypeError",
        _raises(ContentRefusedError, emit_event, "log", {"level": ["info"], "message_code": "x"}),
    )
    check(
        "FTF-0903-A2",
        "an unhashable field value in an id field raises our own error",
        _raises(
            ContentRefusedError,
            emit_event,
            "workflow",
            {"workflow_id": {}, "task_id": "t", "state": "queued"},
        ),
    )
    check(
        "FTF-0903-A2",
        "a non-string field name is refused, not silently ignored",
        _raises(
            DisallowedFieldError,
            emit_event,
            "log",
            {1: "info", "message_code": "x", "level": "info"},
        ),
    )
    check(
        "FTF-0903-A2",
        "every subclass error is a ContentFreeTelemetryError",
        issubclass(ContentRefusedError, ContentFreeTelemetryError)
        and issubclass(DisallowedFieldError, ContentFreeTelemetryError)
        and issubclass(InvalidEventError, ContentFreeTelemetryError),
    )


def test_decision_is_itself_content_free() -> None:
    try:
        emit_event(
            "workflow",
            {"workflow_id": "SELECT * FROM secrets -- leak", "task_id": "t", "state": "queued"},
        )
        raised = None
    except ContentFreeTelemetryError as exc:
        raised = str(exc)
    check("FTF-0903-A1", "an error was raised", raised is not None)
    assert raised is not None
    check(
        "FTF-0903-A1",
        "the error message names the field, never echoes the rejected value",
        "workflow_id" in raised and "SELECT" not in raised,
    )


# --- Fault injection (Law 1): mutate the real module, prove each guard is load-bearing. ---

_PROBE = """
import sys
sys.path.insert(0, {root!r})
from packages.security.content_free_telemetry import (
    emit_event, ContentFreeTelemetryError,
)

def outcome(fn):
    try:
        fn()
        return "allowed"
    except ContentFreeTelemetryError as exc:
        return exc.code
    except Exception as exc:
        return "ERROR:" + type(exc).__name__

wf = {{"workflow_id": "w", "task_id": "t", "state": "queued"}}
print(outcome(lambda: emit_event("log", {{"level": "critical", "message_code": "x"}})))
print(outcome(lambda: emit_event("log", {{"level": "info", "message_code": "ok\\n"}})))
print(outcome(lambda: emit_event("workflow", {{"task_id": "t", "state": "queued"}})))
print(outcome(lambda: emit_event("workflow", {{**wf, "extra": "x"}})))
print(outcome(lambda: emit_event("workflow", {{**wf, "cost_usd": -1}})))
print(outcome(lambda: emit_event("workflow", {{**wf, "failure_ref": "raw text"}})))
"""


def _probe(source: str | None) -> list[str]:
    original = MODULE.read_bytes()
    try:
        if source is not None:
            MODULE.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", _PROBE.format(root=str(ROOT))],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        MODULE.write_bytes(original)
    if MODULE.read_bytes() != original:
        raise RuntimeError("module not restored")
    return (
        result.stdout.split("\n") if result.returncode == 0 else ["ERROR:" + result.stderr[-300:]]
    )


BASELINE = [
    "content_free_telemetry_content_refused",  # unknown log level
    "content_free_telemetry_content_refused",  # trailing-newline code
    "content_free_telemetry_disallowed_field",  # missing required workflow_id
    "content_free_telemetry_disallowed_field",  # undeclared field "extra"
    "content_free_telemetry_content_refused",  # negative cost
    "content_free_telemetry_content_refused",  # raw string as content_ref
    "",
]

# (description, regex, replacement, output line index that must change)
MUTATIONS = [
    (
        "enum membership check removed (any string level accepted)",
        r"if not isinstance\(value, str\) or value not in allowed:",
        "if False:",
        0,
    ),
    (
        "code regex uses match-style $ anchor instead of fullmatch",
        r"not _CODE_RE\.fullmatch\(value\)",
        "not _CODE_RE.match(value)",
        1,
    ),
    (
        "required-field check removed",
        r"if spec\.required and name not in validated:",
        "if False:",
        2,
    ),
    (
        "undeclared field names silently accepted",
        r"if not isinstance\(name, str\) or name not in schema:",
        "if False:",
        3,
    ),
    (
        "negative/overflowing numbers accepted (bound dropped from _number_value only)",
        r"not _finite\(float\(value\)\)\n        or value < 0\n        or value > _MAX_NUMBER",
        "not _finite(float(value))",
        4,
    ),
    (
        "content_ref type check removed (raw string accepted as a reference)",
        r"if not isinstance\(value, ContentRef\):",
        "if False:",
        5,
    ),
]


def test_fault_injection_each_guard_is_load_bearing() -> None:
    check("FTF-0903-FAULT", "unmutated probe matches baseline", _probe(None) == BASELINE)
    text = MODULE.read_text(encoding="utf-8")
    for description, pattern, replacement, index in MUTATIONS:
        mutated, count = re.subn(pattern, replacement, text, count=1)
        check("FTF-0903-FAULT", f"mutation pattern matches once: {description}", count == 1)
        if count != 1:
            continue
        out = _probe(mutated)
        check(
            "FTF-0903-FAULT",
            f"mutation turns the probe RED: {description}",
            len(out) == len(BASELINE) and out[index] != BASELINE[index],
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0903 Content-free telemetry tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0903 Content-free telemetry tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
