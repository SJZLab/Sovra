"""Observability foundation (FTF-0006).

A stdlib-only structured-event/metrics seam that any future real API route
or Temporal workflow activity wraps a handler in. No FastAPI app or
Temporal workflow exists yet to instrument (`apps/api` has no `main.py`;
no `services/workflow-worker` exists) — adding either now, solely to host
an observability demo, would itself be a new-runtime-dependency/new-scope
ask-first item under CLAUDE.md (same reasoning FTF-0004/0005 used to stay
stdlib-only). This module is the seam those later tasks attach to; this
task proves it against a real stdlib HTTP server and a real socket
request (see `tests/tasks/test_ftf_0006.py`), the same "genuine local
integration, no Docker/network needed" pattern FTF-0004 used for its own
readiness checks.

The negative_acceptance clause ("training prompts, raw source, tokens and
signed URLs never appear in logs/spans; cardinality remains bounded") is
enforced structurally, not by a best-effort filter:
  - `StructuredEvent` is a frozen dataclass with a fixed, typed field set
    — there is no field capable of holding free-form content, and passing
    an unexpected keyword raises `TypeError` at construction.
  - `identity`/`event` are validated against a short, safe-identifier
    pattern (no spaces/newlines, no query strings, max 128 chars) so a
    caller cannot smuggle a raw prompt, a raw source dump or a signed
    URL's querystring through those fields either. This is a format
    boundary, not a secret scanner: it cannot detect an arbitrary short
    opaque token that happens to look like a legitimate slug-style ID —
    the guarantee against a credential leaking through `identity` is that
    a caller must actively misuse a field meant for job/request IDs, and
    even then only within this tight, non-prose, length-capped shape.
  - `MetricsRegistry` rejects any label outside a caller-predeclared,
    finite set — label cardinality is bounded by construction, not by
    convention.
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import Lock
from typing import TextIO

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class EventStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


_ALLOWED_STATUSES = frozenset(status.value for status in EventStatus)


def _validate_identifier(value: str, field_name: str) -> str:
    if not _SAFE_IDENTIFIER.match(value):
        raise ValueError(
            f"{field_name} must match {_SAFE_IDENTIFIER.pattern!r} (short, safe identifier "
            "— never raw prompt/source/token/URL content), got a value of "
            f"length {len(value)}"
        )
    return value


@dataclass(frozen=True)
class StructuredEvent:
    """A content-free observability event — see module docstring for why
    its field set structurally cannot carry a prompt, source, token or
    signed URL."""

    event: str
    correlation_id: str
    identity: str
    status: str
    duration_ms: float
    timestamp_utc: str
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.event, "event")
        _validate_identifier(self.identity, "identity")
        if self.status not in _ALLOWED_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_ALLOWED_STATUSES)}, got {self.status!r}"
            )
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")

    def to_json_line(self) -> str:
        payload: dict[str, object] = {
            "event": self.event,
            "correlation_id": self.correlation_id,
            "identity": self.identity,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "timestamp_utc": self.timestamp_utc,
        }
        if self.cost_usd is not None:
            payload["cost_usd"] = self.cost_usd
        return json.dumps(payload, sort_keys=True)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


EventSink = Callable[[StructuredEvent], None]


def emit_to_stream(event: StructuredEvent, stream: TextIO = sys.stdout) -> None:
    """Writes exactly one content-free JSON line per event."""
    print(event.to_json_line(), file=stream, flush=True)


class MetricsRegistry:
    """In-memory numeric counters/timers with a bounded, predeclared label set.

    Section 4's frozen observability layer target is
    Prometheus/OpenTelemetry; adding either package now, solely to back a
    stdlib health-style endpoint, would itself be a new-runtime-dependency
    ask-first item. This registry is the seam a real exporter attaches to
    later — the numeric-metric contract (bounded labels, real counts,
    real durations) is proven now regardless of which exporter eventually
    reads it.
    """

    def __init__(self, event_name: str, allowed_labels: frozenset[str]) -> None:
        _validate_identifier(event_name, "event_name")
        if not allowed_labels:
            raise ValueError(
                "allowed_labels must be non-empty — an unbounded registry is not a bound"
            )
        self._event_name = event_name
        self._allowed_labels = allowed_labels
        self._lock = Lock()
        self._counts: dict[str, int] = {}
        self._durations_ms: dict[str, list[float]] = {}

    def record(self, label: str, duration_ms: float) -> None:
        if label not in self._allowed_labels:
            raise ValueError(f"label {label!r} not in bounded set {sorted(self._allowed_labels)}")
        with self._lock:
            self._counts[label] = self._counts.get(label, 0) + 1
            self._durations_ms.setdefault(label, []).append(duration_ms)

    def count(self, label: str) -> int:
        with self._lock:
            return self._counts.get(label, 0)

    def total_duration_ms(self, label: str) -> float:
        with self._lock:
            return sum(self._durations_ms.get(label, ()))

    def cardinality(self) -> int:
        """Number of distinct labels actually observed — bounded above by
        `len(allowed_labels)` regardless of request volume or how many
        distinct correlation IDs were issued."""
        with self._lock:
            return len(self._counts)


class InstrumentedRequest:
    """Wraps one real request/activity end-to-end: issues a correlation
    ID, times the handler, records a numeric metric keyed only by a
    bounded status label, and emits exactly one structured event — the
    'one actual request crosses instrumentation with correlation and
    numeric metrics' proof point."""

    def __init__(
        self,
        event_name: str,
        identity: str,
        metrics: MetricsRegistry,
        sink: EventSink = emit_to_stream,
    ) -> None:
        self._event_name = event_name
        self._identity = identity
        self._metrics = metrics
        self._sink = sink
        self.correlation_id = new_correlation_id()
        self.status: str = EventStatus.OK.value
        self._start = 0.0
        self.duration_ms = 0.0

    def __enter__(self) -> InstrumentedRequest:
        self._start = time.monotonic()
        return self

    def fail(self) -> None:
        self.status = EventStatus.ERROR.value

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.duration_ms = (time.monotonic() - self._start) * 1000.0
        if exc_type is not None:
            self.status = EventStatus.ERROR.value
        self._metrics.record(self.status, self.duration_ms)
        event = StructuredEvent(
            event=self._event_name,
            correlation_id=self.correlation_id,
            identity=self._identity,
            status=self.status,
            duration_ms=self.duration_ms,
            timestamp_utc=datetime.now(UTC).isoformat(),
        )
        self._sink(event)
        # Implicitly returns None — never swallows the real exception.
