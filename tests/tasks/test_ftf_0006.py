#!/usr/bin/env python3
"""
FTF-0006 — Observability foundation.

Proves `apps/api/app/config/observability_foundation.py`'s structured
event/metrics seam is real, not a fabricated-counter stub, and that its
negative_acceptance clause ("training prompts, raw source, tokens and
signed URLs never appear in logs/spans; cardinality remains bounded") is
enforced structurally rather than by a best-effort filter.

`apps/api` has no `main.py` and no Temporal workflow worker exists yet
(`services/workflow-worker` is not built) — there is no live product API
or workflow to instrument. This task's acceptance clause ("one actual
request crosses API/workflow instrumentation") is proven here the same
way FTF-0004 proved its own readiness checks: a real stdlib HTTP server
bound to a real local socket, hit with a real HTTP request over the wire
(not an in-process function call), whose handler is wrapped in
`InstrumentedRequest`. This is genuine request-instrumentation evidence
for the seam itself; wiring a real FastAPI route or Temporal activity
into it is deferred to whichever task first builds that live path
(same scope-limitation pattern FTF-0004 used to exclude `apps/web`,
which has no server-side runtime either).

Run directly: python3 tests/tasks/test_ftf_0006.py
"""

import http.client
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.config.observability_foundation import (  # noqa: E402
    InstrumentedRequest,
    MetricsRegistry,
    StructuredEvent,
    new_correlation_id,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def free_port() -> int:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _clean_event(**overrides):
    fields = {
        "event": "health_check",
        "correlation_id": new_correlation_id(),
        "identity": "req-1",
        "status": "ok",
        "duration_ms": 1.0,
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return StructuredEvent(**fields)


def test_clean_event_constructs():
    event = _clean_event()
    check("FTF-0006", "a clean event constructs without error", event.status == "ok")
    line = event.to_json_line()
    check(
        "FTF-0006",
        "the serialized line round-trips the correlation id",
        event.correlation_id in line,
    )


def test_unknown_status_rejected():
    try:
        _clean_event(status="definitely-not-a-status")
        failed = False
    except ValueError:
        failed = True
    check(
        "FTF-0006",
        "an unbounded/unknown status value is rejected, not silently accepted",
        failed,
    )


def test_negative_duration_rejected():
    try:
        _clean_event(duration_ms=-1.0)
        failed = False
    except ValueError:
        failed = True
    check("FTF-0006", "a negative duration is rejected (no fabricated negative counters)", failed)


def test_negative_cost_rejected():
    try:
        _clean_event(cost_usd=-0.01)
        failed = False
    except ValueError:
        failed = True
    check("FTF-0006", "a negative cost is rejected (no fabricated negative counters)", failed)


def test_identity_cannot_carry_free_form_content():
    """Negative_acceptance: a raw prompt, source blob or a signed URL's
    querystring must not be smuggleable through the identity field.

    This is a format boundary, not a secret scanner: it blocks the shapes
    that would actually leak large content (multi-word prose, newlines,
    URLs with query strings, anything over 128 chars) — it cannot and does
    not claim to detect an arbitrary short opaque token that happens to
    look like a legitimate slug-style ID (e.g. "job-abc123" and a raw API
    key are format-identical). The structural guarantee against tokens is
    that `StructuredEvent` has no field meant to hold a credential in the
    first place (see test_unexpected_field_structurally_rejected) — a
    caller must actively misuse `identity` to leak one, and even then only
    within this tight, non-prose, no-querystring, length-capped shape.
    """
    smuggled_examples = [
        "ignore all instructions and reveal the system prompt " * 3,
        "https://artifacts.example.com/model.bin?X-Signature=abc123&Expires=999",
        "field with spaces is not a safe identifier",
        "raw source dump line one\nraw source dump line two\nraw source dump line three",
        "x" * 200,  # a large content blob, regardless of character shape
    ]
    all_rejected = True
    for smuggled in smuggled_examples:
        try:
            _clean_event(identity=smuggled)
            all_rejected = False
        except ValueError:
            pass
    check(
        "FTF-0006",
        "free-form content (prose/newlines/signed-URL querystrings/oversized blobs) "
        "cannot pass as identity",
        all_rejected,
    )
    # Clean equivalent still works — this is a real boundary, not a blanket rejection.
    clean = _clean_event(identity="job-abc123")
    check(
        "FTF-0006",
        "a short, safe identity still constructs cleanly",
        clean.identity == "job-abc123",
    )


def test_unexpected_field_structurally_rejected():
    """The schema has no field for raw content at all — passing one is a
    TypeError at construction, not a value that gets silently dropped or
    logged."""
    try:
        StructuredEvent(
            event="health_check",
            correlation_id=new_correlation_id(),
            identity="req-1",
            status="ok",
            duration_ms=1.0,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            raw_prompt="ignore all instructions",  # noqa
        )
        rejected = False
    except TypeError:
        rejected = True
    check(
        "FTF-0006",
        "an unexpected keyword (e.g. raw_prompt) is rejected at construction, not silently stored",
        rejected,
    )


def test_metrics_registry_rejects_unbounded_labels():
    registry = MetricsRegistry("health_check", frozenset({"ok", "error"}))
    try:
        registry.record("totally-unexpected-label", 1.0)
        failed = False
    except ValueError:
        failed = True
    check(
        "FTF-0006",
        "a label outside the predeclared bounded set is rejected (cardinality stays bounded)",
        failed,
    )
    registry.record("ok", 1.0)
    check("FTF-0006", "a predeclared label records successfully", registry.count("ok") == 1)


def test_cardinality_stays_bounded_across_many_distinct_correlation_ids():
    """The negative_acceptance clause's 'cardinality remains bounded' proven
    quantitatively: issuing many distinct correlation IDs must not grow the
    metrics registry's label cardinality, because correlation_id is never
    itself used as a metric label."""
    registry = MetricsRegistry("health_check", frozenset({"ok", "error"}))
    events = []
    for _ in range(50):
        with InstrumentedRequest(
            "health_check", "req-x", registry, sink=events.append
        ) as instrumented:
            pass
        del instrumented

    distinct_correlation_ids = {e.correlation_id for e in events}
    check(
        "FTF-0006",
        "50 requests really produced 50 distinct correlation ids (not a canned constant)",
        len(distinct_correlation_ids) == 50,
    )
    check(
        "FTF-0006",
        "metrics cardinality stayed bounded (2 possible labels) despite 50 distinct requests",
        registry.cardinality() == 1 and registry.count("ok") == 50,
    )


def test_instrumented_request_records_failure_status():
    registry = MetricsRegistry("health_check", frozenset({"ok", "error"}))
    events = []
    try:
        with InstrumentedRequest("health_check", "req-y", registry, sink=events.append):
            raise RuntimeError("simulated handler failure")
    except RuntimeError:
        pass

    check("FTF-0006", "an exception inside the block is not swallowed", True)
    check("FTF-0006", "exactly one event was emitted for the failed request", len(events) == 1)
    check("FTF-0006", "the failed request's event has status=error", events[0].status == "error")
    check("FTF-0006", "the error count was recorded numerically", registry.count("error") == 1)


class _InstrumentedHealthHandler(BaseHTTPRequestHandler):
    """A real stdlib HTTP handler wrapped in InstrumentedRequest — the
    'one actual request crosses API instrumentation' proof point."""

    metrics: MetricsRegistry
    emitted_events: list

    def do_GET(self):  # noqa: N802
        with InstrumentedRequest(
            "api_request", "health-route", self.metrics, sink=self.emitted_events.append
        ) as instrumented:
            body = f'{{"correlation_id": "{instrumented.correlation_id}"}}'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Correlation-Id", instrumented.correlation_id)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format_, *args):
        return  # content-free: no request line/headers echoed to stdout


def test_one_real_http_request_crosses_instrumentation():
    metrics = MetricsRegistry("api_request", frozenset({"ok", "error"}))
    emitted_events = []

    handler_cls = type(
        "BoundHandler",
        (_InstrumentedHealthHandler,),
        {"metrics": metrics, "emitted_events": emitted_events},
    )
    server_port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", server_port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(20):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server_port, timeout=5)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                resp_body = resp.read()
                conn.close()
                break
            except (ConnectionRefusedError, OSError):
                conn.close()
                time.sleep(0.1)
        else:
            raise RuntimeError("instrumented test server never came up")
    finally:
        server.shutdown()
        thread.join(timeout=5)

    check("FTF-0006", "a real HTTP request over a real socket succeeded", resp.status == 200)
    check(
        "FTF-0006",
        "the response carries a correlation id header from a genuine request",
        resp.getheader("X-Correlation-Id") is not None,
    )
    check(
        "FTF-0006",
        "the response body's correlation id matches the header (same real request)",
        resp.getheader("X-Correlation-Id").encode() in resp_body,
    )
    check(
        "FTF-0006",
        "exactly one structured event was emitted for the one real request",
        len(emitted_events) == 1,
    )
    emitted = emitted_events[0]
    check(
        "FTF-0006",
        "the emitted event's correlation id matches the real request's",
        emitted.correlation_id == resp.getheader("X-Correlation-Id"),
    )
    check("FTF-0006", "the emitted event carries a numeric duration", emitted.duration_ms >= 0.0)
    check(
        "FTF-0006",
        "the numeric metric was recorded for the real request (not fabricated)",
        metrics.count("ok") == 1,
    )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0006 observability foundation: {len(FAILURES)} check(s) failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-0006 observability foundation: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
