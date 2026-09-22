#!/usr/bin/env python3
"""FTF-1401 -- API load test (no-DB unit/fault tests).

Runnable without Postgres or network egress. Proves, without any live
server, the pure measurement/aggregation logic
`tests/performance/api_load_test.py` uses to turn real request samples
into the report Section 49 acceptance is judged against:

1. `compute_percentile`/`summarize_latencies` compute the declared
   nearest-rank-with-interpolation percentiles correctly, including the
   single-value and exact-rank edge cases.
2. `build_workload_plan` produces the declared read/write proportion and
   actually shuffles (never a trivially sorted read-block-then-write-block
   sequence, which would hide latency-under-mixed-load effects).
3. `evaluate_acceptance` fails for real whenever a real threshold is
   exceeded (read P95, submit P95, unexpected-5xx rate), each proven as
   its own dedicated negative case, and passes cleanly on real
   within-threshold data.
4. negative_acceptance ("no ... dropped timed-out requests"):
   `execute_request` always returns exactly one `RequestSample` for a
   request against an unreachable host -- proven against a real closed
   local TCP port, never a mock -- and `build_report` always accounts for
   every sample it is given, including failed/timed-out ones
   (`total_requests == len(samples)` always).
5. A real `LoadTestReport`/`WorkloadSpec` built by this module's own
   `build_report` validates against the frozen
   `packages/contracts/jsonschema/14-domain.schema.json` contract, and
   `packages/contracts/openapi/14-interface.yaml` declares matching
   schemas with zero placeholder paths (Law 11).

Own fault injection (Law 1, two distinct vectors, both applied directly to
`tests/performance/api_load_test.py`'s real source and reverted
byte-identical afterward):
  (1) removing the `except OSError` guard in `execute_request` --
      `test_execute_request_never_raises_on_an_unreachable_host` fails for
      real (the call raises instead of returning a sample).
  (2) removing the unexpected-5xx-rate branch in `evaluate_acceptance` --
      `test_evaluate_acceptance_fails_when_5xx_rate_exceeds_threshold`
      fails for real (a report with a 5xx rate above threshold is wrongly
      reported as passed).

Run directly: python3 tests/tasks/test_ftf_1401.py
"""

from __future__ import annotations

import json
import random
import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
PERFORMANCE_ROOT = ROOT / "tests" / "performance"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(PERFORMANCE_ROOT))

import api_load_test as lt  # noqa: E402
import yaml  # noqa: E402

OPENAPI_PATH = ROOT / "packages" / "contracts" / "openapi" / "14-interface.yaml"
JSONSCHEMA_PATH = ROOT / "packages" / "contracts" / "jsonschema" / "14-domain.schema.json"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- 1. compute_percentile / summarize_latencies --------------------------


def test_compute_percentile_single_value() -> None:
    check(
        "FTF-1401",
        "a single-value sequence returns that value at any percentile",
        lt.compute_percentile([42.0], 0) == 42.0
        and lt.compute_percentile([42.0], 50) == 42.0
        and lt.compute_percentile([42.0], 100) == 42.0,
    )


def test_compute_percentile_exact_rank() -> None:
    # 5 values -> p50 rank = 0.5 * 4 = 2.0 (exact index 2, value 30)
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    check(
        "FTF-1401",
        "p50 over [10,20,30,40,50] lands exactly on the middle value (30)",
        lt.compute_percentile(values, 50) == 30.0,
    )
    check(
        "FTF-1401",
        "p0 returns the minimum, p100 returns the maximum",
        lt.compute_percentile(values, 0) == 10.0 and lt.compute_percentile(values, 100) == 50.0,
    )


def test_compute_percentile_interpolates() -> None:
    values = [10.0, 20.0]
    # rank = 0.95 * 1 = 0.95 -> 10 + 0.95 * (20 - 10) = 19.5
    check(
        "FTF-1401",
        "p95 over [10,20] interpolates linearly between the two values (19.5)",
        abs(lt.compute_percentile(values, 95) - 19.5) < 1e-9,
    )


def test_compute_percentile_rejects_invalid_input() -> None:
    def _raises_empty() -> bool:
        try:
            lt.compute_percentile([], 50)
            return False
        except ValueError:
            return True

    def _raises_bad_pct() -> bool:
        try:
            lt.compute_percentile([1.0, 2.0], 101)
            return False
        except ValueError:
            return True

    check("FTF-1401", "compute_percentile([], ...) raises ValueError", _raises_empty())
    check("FTF-1401", "compute_percentile(..., pct=101) raises ValueError", _raises_bad_pct())


def test_summarize_latencies_matches_known_distribution() -> None:
    samples = [
        lt.RequestSample(category="read", method="GET", path="/x", status_code=200, latency_ms=v)
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]
    ]
    stats = lt.summarize_latencies(samples)
    check("FTF-1401", "summarize_latencies count matches sample count", stats.count == 5)
    check(
        "FTF-1401",
        "summarize_latencies p50 matches compute_percentile directly",
        stats.p50_ms == 30.0,
    )
    check("FTF-1401", "summarize_latencies max_ms is the true maximum", stats.max_ms == 50.0)

    def _raises_empty() -> bool:
        try:
            lt.summarize_latencies([])
            return False
        except ValueError:
            return True

    check("FTF-1401", "summarize_latencies([]) raises ValueError", _raises_empty())


# -- 2. build_workload_plan -------------------------------------------------


def test_build_workload_plan_matches_declared_fraction() -> None:
    rng = random.Random(1234)
    plan = lt.build_workload_plan(1000, 0.8, rng)
    reads = plan.count("read")
    writes = plan.count("write")
    check(
        "FTF-1401",
        f"a 1000-request plan at read_fraction=0.8 has exactly 800 reads (got {reads})",
        reads == 800,
    )
    check(
        "FTF-1401",
        f"the remaining requests are writes (got {writes})",
        writes == 200,
    )


def test_build_workload_plan_is_actually_shuffled() -> None:
    rng = random.Random(7)
    plan = lt.build_workload_plan(200, 0.5, rng)
    unshuffled = ["read"] * 100 + ["write"] * 100
    check(
        "FTF-1401",
        "build_workload_plan does not return a trivially sorted "
        "read-block-then-write-block sequence",
        plan != unshuffled,
    )
    # No run of more than ~30 consecutive same-kind entries in a fair
    # shuffle of 200 -- a real, checkable structural property, not a
    # probabilistic guess (the unshuffled sequence has a run of 100).
    longest_run = 1
    current_run = 1
    for a, b in zip(plan, plan[1:], strict=False):
        current_run = current_run + 1 if a == b else 1
        longest_run = max(longest_run, current_run)
    check(
        "FTF-1401",
        f"the longest same-kind run after shuffling ({longest_run}) is far "
        "shorter than the unshuffled run (100)",
        longest_run < 50,
    )


def test_build_workload_plan_rejects_invalid_input() -> None:
    rng = random.Random(1)

    def _raises(total: int, fraction: float) -> bool:
        try:
            lt.build_workload_plan(total, fraction, rng)
            return False
        except ValueError:
            return True

    check("FTF-1401", "total_requests=0 raises ValueError", _raises(0, 0.5))
    check("FTF-1401", "read_fraction=1.5 raises ValueError", _raises(10, 1.5))


# -- 3. evaluate_acceptance --------------------------------------------------

_GOOD_THRESHOLDS = lt.AcceptanceThresholds(
    read_p95_ms=500.0, write_p95_ms=1000.0, max_unexpected_5xx_rate=0.001
)


def _stats(p95: float) -> lt.LatencyStats:
    return lt.LatencyStats(
        count=100, p50_ms=p95 / 2, p95_ms=p95, p99_ms=p95 * 1.1, max_ms=p95 * 1.2
    )


def test_evaluate_acceptance_passes_within_threshold() -> None:
    result = lt.evaluate_acceptance(
        read_stats=_stats(400.0),
        write_stats=_stats(900.0),
        unexpected_5xx_count=0,
        total_requests=1000,
        thresholds=_GOOD_THRESHOLDS,
    )
    check(
        "FTF-1401",
        "within-threshold real data passes with no reasons",
        result.passed and not result.reasons,
    )


def test_evaluate_acceptance_fails_when_read_p95_exceeds_threshold() -> None:
    result = lt.evaluate_acceptance(
        read_stats=_stats(600.0),
        write_stats=_stats(900.0),
        unexpected_5xx_count=0,
        total_requests=1000,
        thresholds=_GOOD_THRESHOLDS,
    )
    check(
        "FTF-1401",
        "read P95 above threshold fails, and the reason names the read P95 clause",
        not result.passed and any("read P95" in r for r in result.reasons),
    )


def test_evaluate_acceptance_fails_when_write_p95_exceeds_threshold() -> None:
    result = lt.evaluate_acceptance(
        read_stats=_stats(400.0),
        write_stats=_stats(1500.0),
        unexpected_5xx_count=0,
        total_requests=1000,
        thresholds=_GOOD_THRESHOLDS,
    )
    check(
        "FTF-1401",
        "submit/write P95 above threshold fails, and the reason names the submit clause",
        not result.passed and any("submit P95" in r for r in result.reasons),
    )


def test_evaluate_acceptance_fails_when_5xx_rate_exceeds_threshold() -> None:
    result = lt.evaluate_acceptance(
        read_stats=_stats(400.0),
        write_stats=_stats(900.0),
        unexpected_5xx_count=5,
        total_requests=1000,
        thresholds=_GOOD_THRESHOLDS,
    )
    check(
        "FTF-1401",
        "negative_acceptance: a 0.5% unexpected-5xx rate (threshold 0.1%) "
        "fails, and the reason names the 5xx clause",
        not result.passed and any("5xx" in r for r in result.reasons),
    )


def test_evaluate_acceptance_handles_no_traffic_of_a_category() -> None:
    result = lt.evaluate_acceptance(
        read_stats=None,
        write_stats=_stats(900.0),
        unexpected_5xx_count=0,
        total_requests=100,
        thresholds=_GOOD_THRESHOLDS,
    )
    check(
        "FTF-1401",
        "a category with no traffic (None stats) is not treated as a threshold breach",
        result.passed,
    )


# -- 4. execute_request / build_report never drop a sample ------------------


def _closed_local_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens here once closed
    return port


def test_execute_request_never_raises_on_an_unreachable_host() -> None:
    port = _closed_local_port()
    try:
        sample = lt.execute_request("127.0.0.1", port, "GET", "/nope", category="read", timeout=1.0)
        raised = False
    except OSError:
        sample = None
        raised = True
    check(
        "FTF-1401",
        "negative_acceptance: a request against an unreachable host never "
        "raises out of execute_request -- it is reported as a real, "
        "status_code=0 sample, not dropped or crashed on",
        not raised and sample is not None and sample.status_code == 0 and sample.error is not None,
    )


def test_build_report_never_drops_a_sample() -> None:
    samples = [
        lt.RequestSample(
            category="read", method="GET", path="/a", status_code=200, latency_ms=10.0
        ),
        lt.RequestSample(
            category="read", method="GET", path="/a", status_code=200, latency_ms=20.0
        ),
        lt.RequestSample(
            category="write", method="POST", path="/b", status_code=201, latency_ms=30.0
        ),
        lt.RequestSample(
            category="write", method="POST", path="/b", status_code=500, latency_ms=40.0
        ),
        lt.RequestSample(
            category="read",
            method="GET",
            path="/a",
            status_code=0,
            latency_ms=1000.0,
            error="timeout",
        ),
    ]
    import datetime

    started = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    finished = started + datetime.timedelta(seconds=10)
    workload = lt.WorkloadSpec(
        profile_name="test",
        planned_duration_seconds=10.0,
        worker_count=1,
        read_fraction=0.6,
        organizations_count=1,
        projects_count=1,
        declared_api_replicas=1,
        is_partial_profile=True,
        disclosed_gaps=["test gap"],
    )
    report = lt.build_report(
        workload=workload,
        started_at=started,
        finished_at=finished,
        samples=samples,
        thresholds=_GOOD_THRESHOLDS,
    )
    check(
        "FTF-1401",
        f"total_requests ({report.total_requests}) equals every sample given "
        f"({len(samples)}), including the failed/timed-out one",
        report.total_requests == len(samples),
    )
    check(
        "FTF-1401",
        "the failed/timed-out sample is counted in failed_or_timed_out_count, "
        "not silently excluded from latency stats or dropped entirely",
        report.failed_or_timed_out_count == 1,
    )
    check(
        "FTF-1401",
        "the real 500 response is counted as an unexpected 5xx",
        report.unexpected_5xx_count == 1,
    )


# -- 5. frozen shared contract files (this task's own acceptance clause 1) --


def _load_jsonschema() -> dict:
    return json.loads(JSONSCHEMA_PATH.read_text(encoding="utf-8"))


def _load_openapi() -> dict:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


def test_openapi_contract_has_no_placeholder_endpoints() -> None:
    doc = _load_openapi()
    check(
        "FTF-1401",
        "14-interface.yaml parses as valid OpenAPI YAML with an openapi version",
        isinstance(doc.get("openapi"), str) and doc["openapi"].startswith("3."),
    )
    check(
        "FTF-1401",
        "negative_acceptance / Law 11: 14-interface.yaml declares zero paths "
        "-- this task's own deliverable is a measurement tool, not a new "
        "served route",
        doc.get("paths") == {},
    )
    schemas = doc.get("components", {}).get("schemas", {})
    for entity in ("WorkloadSpec", "LoadTestReport", "LatencyStats"):
        check(
            "FTF-1401",
            f"14-interface.yaml's components.schemas defines {entity}, "
            "matching the JSON Schema contract rather than diverging from it",
            entity in schemas and schemas[entity].get("type") == "object",
        )


def _validate_against_schema(instance: dict, schema: dict, defs: dict) -> list[str]:
    """Stdlib-only structural validator (same reasoning as FTF-1601's own:
    the third-party `jsonschema` package would itself be a second,
    unapproved new-runtime-dependency ask-first item)."""
    errors: list[str] = []
    for req_field in schema.get("required", []):
        if req_field not in instance:
            errors.append(f"{req_field}: required but missing")
    for field_name, field_schema in schema.get("properties", {}).items():
        if field_name not in instance:
            continue
        value = instance[field_name]
        if "$ref" in field_schema:
            ref_name = field_schema["$ref"].rsplit("/", 1)[-1]
            errors.extend(_validate_against_schema(value, defs[ref_name], defs))
            continue
        if "anyOf" in field_schema:
            if value is None:
                if not any(opt.get("type") == "null" for opt in field_schema["anyOf"]):
                    errors.append(f"{field_name}: null not permitted by anyOf")
                continue
            ref_opt = next((opt for opt in field_schema["anyOf"] if "$ref" in opt), None)
            if ref_opt is not None:
                ref_name = ref_opt["$ref"].rsplit("/", 1)[-1]
                errors.extend(_validate_against_schema(value, defs[ref_name], defs))
            continue
        expected_type = field_schema.get("type")
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"{field_name}: expected string, got {type(value).__name__}")
        elif expected_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            errors.append(f"{field_name}: expected integer, got {type(value).__name__}")
        elif expected_type == "number" and (
            isinstance(value, bool) or not isinstance(value, int | float)
        ):
            errors.append(f"{field_name}: expected number, got {type(value).__name__}")
        elif expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{field_name}: expected boolean, got {type(value).__name__}")
        elif expected_type == "array":
            if not isinstance(value, list):
                errors.append(f"{field_name}: expected array, got {type(value).__name__}")
            else:
                items_schema = field_schema.get("items", {})
                if "$ref" in items_schema:
                    ref_name = items_schema["$ref"].rsplit("/", 1)[-1]
                    for item in value:
                        errors.extend(_validate_against_schema(item, defs[ref_name], defs))
        pattern = field_schema.get("pattern")
        if pattern and isinstance(value, str) and not re.fullmatch(pattern, value):
            errors.append(f"{field_name}: {value!r} does not match pattern {pattern!r}")
    return errors


def test_a_real_load_test_report_validates_against_the_frozen_contract() -> None:
    import datetime

    samples = [
        lt.RequestSample(
            category="read", method="GET", path="/a", status_code=200, latency_ms=10.0 + i
        )
        for i in range(20)
    ] + [
        lt.RequestSample(
            category="write", method="POST", path="/b", status_code=201, latency_ms=50.0 + i
        )
        for i in range(5)
    ]
    started = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    finished = started + datetime.timedelta(seconds=5)
    workload = lt.WorkloadSpec(
        profile_name="control_api_partial",
        planned_duration_seconds=5.0,
        worker_count=4,
        read_fraction=0.8,
        organizations_count=3,
        projects_count=9,
        declared_api_replicas=1,
        is_partial_profile=True,
        disclosed_gaps=["example gap"],
    )
    report = lt.build_report(
        workload=workload,
        started_at=started,
        finished_at=finished,
        samples=samples,
        thresholds=_GOOD_THRESHOLDS,
    )
    schema_doc = _load_jsonschema()
    defs = schema_doc["$defs"]
    errors = _validate_against_schema(report.to_dict(), defs["LoadTestReport"], defs)
    check(
        "FTF-1401",
        f"a real LoadTestReport built by build_report validates against the "
        f"frozen contract schema (errors: {errors})",
        not errors,
    )

    bad_instance = report.to_dict()
    bad_instance["total_requests"] = "not-a-number"
    errors = _validate_against_schema(bad_instance, defs["LoadTestReport"], defs)
    check(
        "FTF-1401",
        "negative_acceptance: an instance with a type-mismatched field is "
        "rejected explicitly by the validator, not silently accepted",
        any("total_requests" in e for e in errors),
    )


def main() -> None:
    tests = [obj for name, obj in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()

    if FAILURES:
        print(f"FTF-1401 no-DB proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print(f"FTF-1401 no-DB proof: all checks passed ({len(tests)} test functions)")
    sys.exit(0)


if __name__ == "__main__":
    main()
