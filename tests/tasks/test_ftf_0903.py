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

import math
import re
import subprocess
import sys
from collections.abc import Mapping
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
        ev.fields["failure_ref"] == ref and ev.fields["failure_ref"] is not ref,
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


class _EqStr(str):
    """A str subclass whose __eq__ is always True and whose hash is a
    fixed schema value: sails through `in`/dict lookups unless the exact
    type is required."""

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return hash("log")


class _LeakStr(str):
    """A str subclass that passes a shape check by value but serialises as
    something else (its own __str__/__repr__)."""

    def __str__(self) -> str:
        return "LEAKED-SECRET"

    def __repr__(self) -> str:
        return "LEAKED-SECRET"


class _LeakInt(int):
    def __repr__(self) -> str:
        return "LEAKED-SECRET"


class _LeakFloat(float):
    def __repr__(self) -> str:
        return "LEAKED-SECRET"


class _LeakDatetime(datetime):
    def isoformat(self, *args: object, **kwargs: object) -> str:
        return "LEAKED-SECRET"


class _ReprKey:
    called = False

    def __repr__(self) -> str:
        _ReprKey.called = True
        return "LEAKED-SECRET-KEY"


_WF = {"workflow_id": "wf-1", "task_id": "t-1", "state": "queued"}
_LOG = {"level": "info", "message_code": "ok"}


def _message_of(fn, *args: object) -> str | None:
    try:
        fn(*args)
    except ContentFreeTelemetryError as exc:
        return str(exc) + "|" + repr(exc.args)
    return None


def test_error_messages_never_echo_caller_text() -> None:
    long_kind = "k" * 5000
    msg = _message_of(emit_event, long_kind, {})
    check("FTF-0903-R2-1", "a 5000-char kind is refused", msg is not None)
    check(
        "FTF-0903-R2-1",
        "the refusal message is bounded (kind text is not echoed)",
        msg is not None and len(msg) < 300 and "kkkk" not in msg,
    )
    secret_kind = "sk-live-SECRET123"
    msg = _message_of(emit_event, secret_kind, {})
    check(
        "FTF-0903-R2-1",
        "a secret-looking kind is not echoed in the message",
        msg is not None and "SECRET123" not in msg,
    )
    msg = _message_of(emit_event, "workflow", {**_WF, "sk-live-SECRET123": "x"})
    check(
        "FTF-0903-R2-1",
        "a secret-looking field NAME is not echoed in the message",
        msg is not None and "SECRET123" not in msg,
    )
    msg = _message_of(emit_event, "workflow", {**_WF, "f" * 5000: "x"})
    check(
        "FTF-0903-R2-1",
        "a 5000-char field name is not echoed and the message stays bounded",
        msg is not None and len(msg) < 300 and "ffff" not in msg,
    )
    _ReprKey.called = False
    bad_key_fields: dict[object, object] = {k: v for k, v in _WF.items()}
    bad_key_fields[_ReprKey()] = "x"
    msg = _message_of(emit_event, "workflow", bad_key_fields)
    check(
        "FTF-0903-R2-1",
        "a non-str key's own __repr__ is never run nor echoed",
        msg is not None and not _ReprKey.called and "LEAKED" not in msg,
    )
    msg = _message_of(emit_event, "workflow", {"task_id": "t", "state": "queued", "zz": 1})
    check(
        "FTF-0903-R2-1",
        "every refusal still carries a stable machine code on the class",
        msg is not None and DisallowedFieldError.code.startswith("content_free_telemetry_"),
    )
    msg = _message_of(emit_event, "log", {**_LOG, "message_code": "sk-live-SECRET123 and more"})
    check(
        "FTF-0903-R2-1",
        "a rejected VALUE is not echoed either",
        msg is not None and "SECRET123" not in msg,
    )


def test_exact_types_only() -> None:
    check(
        "FTF-0903-R2-2",
        "a str subclass with __eq__ always True is refused as an enum value",
        _raises(
            ContentRefusedError,
            emit_event,
            "log",
            {"level": _EqStr("bogus"), **{"message_code": "x"}},
        ),
    )
    check(
        "FTF-0903-R2-2",
        "a str subclass (hash/eq of 'log') is refused as the event kind",
        _raises(InvalidEventError, emit_event, _EqStr("bogus"), {}),
    )
    check(
        "FTF-0903-R2-2",
        "a str subclass is refused as a field NAME",
        _raises(DisallowedFieldError, emit_event, "log", {**_LOG, _EqStr("level"): "info"}),
    )
    for field, value in (("workflow_id", _LeakStr("wf-1")), ("task_id", _LeakStr("t-1"))):
        check(
            "FTF-0903-R2-2",
            f"a leaking str subclass is refused for id field {field}",
            _raises(ContentRefusedError, emit_event, "workflow", {**_WF, field: value}),
        )
    check(
        "FTF-0903-R2-2",
        "a leaking str subclass is refused for a code field",
        _raises(ContentRefusedError, emit_event, "log", {**_LOG, "message_code": _LeakStr("ok")}),
    )
    check(
        "FTF-0903-R2-2",
        "a leaking str subclass is refused for an enum field",
        _raises(ContentRefusedError, emit_event, "log", {**_LOG, "level": _LeakStr("info")}),
    )
    check(
        "FTF-0903-R2-2",
        "an int subclass is refused for a number field",
        _raises(ContentRefusedError, emit_event, "workflow", {**_WF, "timing_ms": _LeakInt(5)}),
    )
    check(
        "FTF-0903-R2-2",
        "a float subclass is refused for a number field",
        _raises(ContentRefusedError, emit_event, "workflow", {**_WF, "cost_usd": _LeakFloat(1.5)}),
    )
    from packages.security.content_free_telemetry import _FieldSpec, _validate_field

    check(
        "FTF-0903-R2-2",
        "an int subclass is refused for a count field",
        _raises(ContentRefusedError, _validate_field, "c", _FieldSpec("count"), _LeakInt(3)),
    )
    check(
        "FTF-0903-R2-2",
        "a datetime subclass is refused for a timestamp field",
        _raises(
            ContentRefusedError,
            _validate_field,
            "t",
            _FieldSpec("timestamp"),
            _LeakDatetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    ev = emit_event("workflow", {**_WF, "timing_ms": 5, "cost_usd": 2})
    check(
        "FTF-0903-R2-2",
        "accepted numbers are stored as exact float",
        all(type(ev.fields[k]) is float for k in ("timing_ms", "cost_usd")),
    )
    check(
        "FTF-0903-R2-2",
        "accepted strings and the kind are stored as exact str",
        type(ev.kind) is str and all(type(ev.fields[k]) is str for k in _WF),
    )


def test_content_ref_is_revalidated_at_emit() -> None:
    wf = {**_WF, "state": "failed"}
    forged = object.__new__(ContentRef)
    check(
        "FTF-0903-R2-3",
        "a ContentRef forged with object.__new__ (no attributes) is refused",
        _raises(ContentRefusedError, emit_event, "workflow", {**wf, "failure_ref": forged}),
    )
    forged2 = object.__new__(ContentRef)
    object.__setattr__(forged2, "ref_id", "bad id with spaces and\nnewline")
    object.__setattr__(forged2, "encryption_key_id", "key-1")
    check(
        "FTF-0903-R2-3",
        "a forged ContentRef with an unbounded ref_id is refused",
        _raises(ContentRefusedError, emit_event, "workflow", {**wf, "failure_ref": forged2}),
    )

    class _Skip(ContentRef):
        def __post_init__(self) -> None:
            return None

    check(
        "FTF-0903-R2-3",
        "a ContentRef subclass that skips __post_init__ is refused",
        _raises(
            ContentRefusedError,
            emit_event,
            "workflow",
            {**wf, "failure_ref": _Skip("bad id", "key-1")},
        ),
    )

    class _ValidSub(ContentRef):
        pass

    check(
        "FTF-0903-R2-3",
        "a ContentRef subclass, even with valid ids, is refused (exact type)",
        _raises(
            ContentRefusedError,
            emit_event,
            "workflow",
            {**wf, "failure_ref": _ValidSub("blob-1", "key-1")},
        ),
    )

    class _DuckRef:
        ref_id = "blob-1"
        encryption_key_id = "key-1"

    check(
        "FTF-0903-R2-3",
        "a duck-typed object with valid ref_id/encryption_key_id is not a ContentRef",
        _raises(ContentRefusedError, emit_event, "workflow", {**wf, "failure_ref": _DuckRef()}),
    )
    mutated = ContentRef("blob-1", "key-1")
    object.__setattr__(mutated, "encryption_key_id", "x" * 500)
    check(
        "FTF-0903-R2-3",
        "a ContentRef mutated after construction (object.__setattr__) is refused",
        _raises(ContentRefusedError, emit_event, "workflow", {**wf, "failure_ref": mutated}),
    )
    nonstr = ContentRef("blob-1", "key-1")
    object.__setattr__(nonstr, "ref_id", 123)
    check(
        "FTF-0903-R2-3",
        "a ContentRef whose ref_id was swapped to a non-str is refused",
        _raises(ContentRefusedError, emit_event, "workflow", {**wf, "failure_ref": nonstr}),
    )
    good = ContentRef("blob-1", "key-1")
    ev = emit_event("workflow", {**wf, "failure_ref": good})
    stored = ev.fields["failure_ref"]
    check(
        "FTF-0903-R2-3",
        "a valid ContentRef is accepted and equal",
        isinstance(stored, ContentRef) and stored == good,
    )
    object.__setattr__(good, "ref_id", "tampered after emit")
    check(
        "FTF-0903-R2-3",
        "the stored ref is a copy: mutating the caller's ref after emit changes nothing",
        isinstance(stored, ContentRef) and stored.ref_id == "blob-1",
    )


def test_number_overflow_and_negative_zero() -> None:
    from packages.security.content_free_telemetry import _FieldSpec, _validate_field

    for what, value in (("10**400", 10**400), ("-10**400", -(10**400))):
        for kind in ("number", "count"):
            check(
                "FTF-0903-R2-4",
                f"{what} in a {kind} field raises the coded refusal, not OverflowError",
                _raises(ContentRefusedError, _validate_field, "n", _FieldSpec(kind), value),
            )
    check(
        "FTF-0903-R2-4",
        "10**400 through emit_event is a coded refusal",
        _raises(ContentRefusedError, emit_event, "workflow", {**_WF, "timing_ms": 10**400}),
    )
    out = float(_validate_field("n", _FieldSpec("number"), -0.0))  # type: ignore[arg-type]
    check(
        "FTF-0903-R2-4",
        "-0.0 is normalised to +0.0 (decision: accept and normalise)",
        out == 0.0 and math.copysign(1.0, out) == 1.0,
    )


def test_exact_key_matching_and_enum_case() -> None:
    for name in ("State", "Workflow_ID", "state_extra", "x_anything", "prompt_text", "Prompt"):
        check(
            "FTF-0903-R2-5",
            f"field name {name!r} is refused (exact-key matching, no normalisation)",
            _raises(DisallowedFieldError, emit_event, "workflow", {**_WF, name: "x"}),
        )
    for bad in ("INFO", "Info", " info", "info "):
        check(
            "FTF-0903-R2-5",
            f"enum value {bad!r} is refused (case/whitespace exact)",
            _raises(ContentRefusedError, emit_event, "log", {**_LOG, "level": bad}),
        )
    check(
        "FTF-0903-R2-5",
        "an unhashable kind ([]) raises the typed error, not TypeError",
        _raises(InvalidEventError, emit_event, [], {}),
    )
    check(
        "FTF-0903-R2-5",
        "an unhashable kind ({}) raises the typed error, not TypeError",
        _raises(InvalidEventError, emit_event, {}, {}),
    )


def _assign_state(event: TelemetryEvent) -> None:
    event.fields["state"] = "failed"  # type: ignore[index]


def _delete_state(event: TelemetryEvent) -> None:
    del event.fields["state"]  # type: ignore[attr-defined]


def test_bounds_types_immutability_determinism() -> None:
    ok99 = "a" * 99
    ev = emit_event("workflow", {**_WF, "workflow_id": ok99})
    check(
        "FTF-0903-R2-6",
        "an id of length 99 (the cap) is accepted unchanged, never truncated",
        ev.fields["workflow_id"] == ok99,
    )
    check(
        "FTF-0903-R2-6",
        "an id of length 100 is refused",
        _raises(ContentRefusedError, emit_event, "workflow", {**_WF, "workflow_id": "a" * 100}),
    )
    code64 = "a" * 64
    ev = emit_event("log", {**_LOG, "message_code": code64})
    check(
        "FTF-0903-R2-6",
        "a code of length 64 is accepted unchanged",
        ev.fields["message_code"] == code64,
    )
    check(
        "FTF-0903-R2-6",
        "a code of length 65 is refused",
        _raises(ContentRefusedError, emit_event, "log", {**_LOG, "message_code": "a" * 65}),
    )
    check(
        "FTF-0903-R2-6",
        "an uppercase code is refused",
        _raises(ContentRefusedError, emit_event, "log", {**_LOG, "message_code": "Ok"}),
    )
    from packages.security.content_free_telemetry import _FieldSpec, _validate_field

    check(
        "FTF-0903-R2-6",
        "a float is refused as a count",
        _raises(ContentRefusedError, _validate_field, "c", _FieldSpec("count"), 3.0),
    )
    for junk in (b"wf-1", ValueError("wf-1"), object(), 7, None):
        check(
            "FTF-0903-R2-6",
            f"{type(junk).__name__} is refused for an id field",
            _raises(ContentRefusedError, emit_event, "workflow", {**_WF, "workflow_id": junk}),
        )
        check(
            "FTF-0903-R2-6",
            f"{type(junk).__name__} is refused for a code field",
            _raises(ContentRefusedError, emit_event, "log", {**_LOG, "message_code": junk}),
        )
    ev = emit_event("workflow", _WF)
    check(
        "FTF-0903-R2-6",
        "event.fields cannot be assigned",
        _raises(TypeError, _assign_state, ev),
    )
    check(
        "FTF-0903-R2-6",
        "event.fields cannot be deleted from",
        _raises(TypeError, _delete_state, ev),
    )
    check(
        "FTF-0903-R2-6",
        "the event itself is frozen",
        _raises(AttributeError, lambda: setattr(ev, "kind", "log")),
    )
    forward = emit_event("workflow", {"workflow_id": "w", "task_id": "t", "state": "queued"})
    backward = emit_event("workflow", {"state": "queued", "task_id": "t", "workflow_id": "w"})
    check("FTF-0903-R2-6", "the same input gives an equal event", forward == backward)
    check(
        "FTF-0903-R2-6",
        "field order is the schema order whatever the input order",
        list(forward.fields) == list(backward.fields) == ["workflow_id", "task_id", "state"],
    )
    check(
        "FTF-0903-R2-6",
        "repeated emission is deterministic",
        emit_event("workflow", _WF) == emit_event("workflow", dict(_WF)),
    )


_PROBE = """
import sys
sys.path.insert(0, {root!r})
from packages.security.content_free_telemetry import (
    emit_event, ContentFreeTelemetryError, ContentRef,
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
print(outcome(lambda: emit_event("workflow", {{**wf, "failure_ref": object.__new__(ContentRef)}})))
"""


def _probe(source: str | None, script: str | None = None) -> list[str]:
    original = MODULE.read_bytes()
    code = script if script is not None else _PROBE.format(root=str(ROOT))
    try:
        if source is not None:
            MODULE.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", code],
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
    "content_free_telemetry_content_refused",  # forged ContentRef (no attributes)
    "",
]

# (description, regex, replacement, output line index that must change)
MUTATIONS = [
    (
        "enum membership check removed (any string level accepted)",
        r"if type\(value\) is not str or value not in allowed:",
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
        r"if spec\.required and name not in given:",
        "if False:",
        2,
    ),
    (
        "undeclared field names silently accepted",
        r"if type\(name\) is not str or name not in schema:",
        "if False:",
        3,
    ),
    (
        "negative/overflowing numbers accepted (bound dropped from _number_value only)",
        r"or not \(0 <= value <= _MAX_NUMBER\)",
        "",
        4,
    ),
    (
        "ContentRef re-validation at emit removed (a forged ref is stored as given)",
        r"return ContentRef\(_id_value\(ref_id, \"content ref id\"\), "
        r"_id_value\(key_id, \"encryption key id\"\)\)",
        "return value",
        6,
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


# --- FTF-0903 round 3: behavioural value-echo and field-name tests. ---

_MARK = "ZZMARK-secret traceback!"


def _echo_report(fn) -> str:
    """'clean' if fn is refused without the marker anywhere in the error,
    'LEAK' if the marker reached str(exc)/repr(exc.args), else 'allowed'."""
    try:
        fn()
    except ContentFreeTelemetryError as exc:
        return "LEAK" if "ZZMARK" in str(exc) + repr(exc.args) else "clean"
    return "allowed"


class _SubclassedName(str):
    pass


class _OddMapping(Mapping):
    def __init__(self, pairs: list) -> None:
        self._pairs = pairs

    def __getitem__(self, key: object) -> object:
        raise KeyError(key)

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return len(self._pairs)

    def items(self):
        return self._pairs


def test_rejected_values_are_never_echoed_for_every_field_kind() -> None:
    from packages.security import content_free_telemetry as m

    traceback = 'Traceback (most recent call last):\n  File "x.py"\n' + _MARK
    cases = {
        "id": lambda: emit_event("workflow", {**_WF, "request_id": _MARK}),
        "code": lambda: emit_event("log", {**_LOG, "message_code": _MARK}),
        "enum level": lambda: emit_event("log", {**_LOG, "level": _MARK}),
        "enum status": lambda: emit_event("span", {"span_id": "s", "name": "n", "status": _MARK}),
        "number cost_usd": lambda: emit_event("workflow", {**_WF, "cost_usd": _MARK}),
        "content_ref failure_ref": lambda: emit_event(
            "workflow", {**_WF, "failure_ref": traceback}
        ),
        "count": lambda: m._validate_field("x", m._FieldSpec("count"), _MARK),
        "timestamp": lambda: m._validate_field("x", m._FieldSpec("timestamp"), _MARK),
        "bool": lambda: m._validate_field("x", m._FieldSpec("bool"), _MARK),
    }
    for label, fn in cases.items():
        check(
            "FTF-0903-R3-1",
            f"a rejected {label} value is refused and never echoed",
            _echo_report(fn) == "clean",
        )


def test_field_name_must_be_an_exact_str_and_hashable() -> None:
    check(
        "FTF-0903-R3-2",
        "a str subclass of a real schema name is refused",
        _raises(
            DisallowedFieldError,
            emit_event,
            "workflow",
            {**_WF, _SubclassedName("job_id"): "j-1"},
        ),
    )
    check(
        "FTF-0903-R3-3",
        "an unhashable field name raises DisallowedFieldError, never TypeError",
        _raises(DisallowedFieldError, emit_event, "workflow", _OddMapping([([], "x")])),
    )


_PROBE_R3 = """
import sys
from collections.abc import Mapping
sys.path.insert(0, __ROOT__)
from packages.security import content_free_telemetry as m
from packages.security.content_free_telemetry import emit_event, ContentFreeTelemetryError

M = "ZZMARK-secret traceback!"
WF = {"workflow_id": "w", "task_id": "t", "state": "queued"}
LOG = {"level": "info", "message_code": "ok"}

class S(str):
    pass

class Odd(Mapping):
    def __getitem__(self, k): raise KeyError(k)
    def __iter__(self): return iter(())
    def __len__(self): return 1
    def items(self): return [([], "x")]

def outcome(fn):
    try:
        fn()
        return "allowed"
    except ContentFreeTelemetryError as exc:
        if "ZZMARK" in str(exc) + repr(exc.args):
            return "LEAK"
        return exc.code
    except Exception as exc:
        return "ERROR:" + type(exc).__name__

spec = m._FieldSpec
print(outcome(lambda: emit_event("workflow", {**WF, "request_id": M})))
print(outcome(lambda: emit_event("log", {**LOG, "message_code": M})))
print(outcome(lambda: emit_event("log", {**LOG, "level": M})))
print(outcome(lambda: emit_event("span", {"span_id": "s", "name": "n", "status": M})))
print(outcome(lambda: emit_event("workflow", {**WF, "cost_usd": M})))
print(outcome(lambda: emit_event("workflow", {**WF, "failure_ref": M})))
print(outcome(lambda: m._validate_field("x", spec("count"), M)))
print(outcome(lambda: m._validate_field("x", spec("timestamp"), M)))
print(outcome(lambda: m._validate_field("x", spec("bool"), M)))
print(outcome(lambda: emit_event("workflow", {**WF, S("job_id"): "j"})))
print(outcome(lambda: emit_event("workflow", Odd())))
"""

_REFUSED = "content_free_telemetry_content_refused"
_DISALLOWED = "content_free_telemetry_disallowed_field"
BASELINE_R3 = [_REFUSED] * 9 + [_DISALLOWED] * 2 + [""]


def _echo_mutations() -> list[tuple[str, str, str, int]]:
    funcs = [
        ("_id_value", 0),
        ("_code_value", 1),
        ("_enum_value", 2),
        ("_number_value", 4),
        ("_content_ref_value", 5),
        ("_count_value", 6),
        ("_timestamp_value", 7),
        ("_bool_value", 8),
    ]
    out = []
    for fn, index in funcs:
        for label, expr in (("repr", "{value!r}"), ("truncated str", "{str(value)[:40]}")):
            out.append(
                (
                    f"{fn} echoes the rejected value ({label}) in its message",
                    rf'(def {fn}\(.*?raise ContentRefusedError\(f"[^"]*)"\)',
                    rf'\1 {expr}")',
                    index,
                )
            )
    return out


MUTATIONS_R3 = _echo_mutations() + [
    (
        "field-name check uses isinstance (str subclass accepted)",
        r"if type\(name\) is not str or name not in schema:",
        "if not isinstance(name, str) or name not in schema:",
        9,
    ),
    (
        "field-name membership tested before the type (unhashable name -> TypeError)",
        r"if type\(name\) is not str or name not in schema:",
        "if name not in schema or type(name) is not str:",
        10,
    ),
]


def test_round3_faults_each_turn_the_probe_red() -> None:
    script = _PROBE_R3.replace("__ROOT__", repr(str(ROOT)))
    check(
        "FTF-0903-R3-FAULT",
        "unmutated r3 probe matches baseline",
        _probe(None, script) == BASELINE_R3,
    )
    text = MODULE.read_text(encoding="utf-8")
    for description, pattern, replacement, index in MUTATIONS_R3:
        mutated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
        check("FTF-0903-R3-FAULT", f"mutation pattern matches once: {description}", count == 1)
        if count != 1:
            continue
        out = _probe(mutated, script)
        check(
            "FTF-0903-R3-FAULT",
            f"mutation turns the probe RED: {description}",
            len(out) == len(BASELINE_R3) and out[index] != BASELINE_R3[index],
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
