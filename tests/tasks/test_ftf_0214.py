#!/usr/bin/env python3
"""FTF-0214 -- no-DB, no-external-egress tests for the pure serialization
helpers in `app.litellm.model_catalog_routes`.

This task's own real, live-wired integration evidence (real Postgres,
real local HTTPS gateway, real fault injection against
`require_model_access`) is `tests/integration/test_ftf_0214_model_catalog.py`
-- same split every sibling FTF-020x/H004 task already established
(tasks/ = pure-logic, no DB; integration/ = real Postgres + route).

This file covers the one genuinely new, DB-free surface this task adds:
the content-free serializers (`_serialize_entry`, `_serialize_anomaly`,
`_serialize_usage`, `_serialize_probe_result`) that turn the nine composed
modules' own real dataclasses into the JSON body the live routes return --
each exercised directly against a real instance of its own dataclass, not
a stub standing in for one.

Run directly: python3 tests/tasks/test_ftf_0214.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.litellm_client.catalog_anomaly_detector import (  # noqa: E402
    AnomalyKind,
    AnomalyRecord,
    AnomalyStatus,
)
from packages.litellm_client.last_known_good_catalog import (  # noqa: E402
    CatalogEntryStatus,
)
from packages.litellm_client.real_chat_probe import (  # noqa: E402
    ChatProbeResult,
    ChatProbeState,
    ChatUsage,
    RouteIdentityState,
)
from packages.litellm_client.real_streaming_probe import (  # noqa: E402
    StreamProbeResult,
    StreamProbeState,
    StreamTerminationReason,
    ToolCallDelta,
)
from packages.litellm_client.real_structured_output_probe import (  # noqa: E402
    StructuredOutputProbeResult,
    StructuredOutputProbeState,
)
from packages.litellm_client.real_tool_call_probe import (  # noqa: E402
    ToolCallProbeResult,
    ToolCallProbeState,
)
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus  # noqa: E402
from packages.model_catalog.access_snapshot import AccessSnapshotEntry  # noqa: E402

from app.litellm.model_catalog_routes import (  # noqa: E402
    _serialize_anomaly,
    _serialize_entry,
    _serialize_probe_result,
    _serialize_usage,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


def test_serialize_entry_round_trips_real_fields() -> None:
    entry = AccessSnapshotEntry(
        id="model-a",
        status=CatalogEntryStatus.CALLABLE,
        metadata_status=ModelMetadataStatus.ENRICHED,
        first_seen_at=_NOW,
        last_seen_callable_at=_NOW,
        fields=MappingProxyType({"max_tokens": 8192}),
    )
    body = _serialize_entry(entry)
    check(
        "FTF-0214", "a real callable entry serializes its real status", body["status"] == "callable"
    )
    check(
        "FTF-0214",
        "a real callable entry serializes its real metadata_status",
        body["metadata_status"] == "enriched",
    )
    check(
        "FTF-0214",
        "a real entry's fields mapping is copied, not dropped",
        body["fields"] == {"max_tokens": 8192},
    )
    check(
        "FTF-0214",
        "a real entry's timestamps are real ISO strings",
        body["first_seen_at"] == _NOW.isoformat()
        and body["last_seen_callable_at"] == _NOW.isoformat(),
    )


def test_serialize_entry_never_fabricates_a_timestamp_for_a_never_callable_entry() -> None:
    entry = AccessSnapshotEntry(
        id="model-b",
        status=CatalogEntryStatus.REMOVED,
        metadata_status=ModelMetadataStatus.LIMITED,
        first_seen_at=_NOW,
        last_seen_callable_at=None,
        fields=MappingProxyType({}),
    )
    body = _serialize_entry(entry)
    check(
        "FTF-0214",
        "negative_acceptance: an entry never seen callable reports last_seen_callable_at=None, "
        "never a fabricated timestamp",
        body["last_seen_callable_at"] is None,
    )
    check(
        "FTF-0214",
        "a removed entry's empty fields serialize as an empty object, not omitted",
        body["fields"] == {},
    )


def test_serialize_anomaly_none_stays_none() -> None:
    check(
        "FTF-0214",
        "no anomaly serializes to None, never a fabricated empty-but-present record",
        _serialize_anomaly(None) is None,
    )


def test_serialize_anomaly_real_record() -> None:
    record = AnomalyRecord(
        id=1,
        kind=AnomalyKind.MAJOR_REDUCTION,
        detected_at=_NOW,
        previous_count=10,
        new_count=2,
        retry_count=0,
        retry_ceiling=3,
        status=AnomalyStatus.OPEN,
        acknowledged_by=None,
        acknowledged_at=None,
    )
    body = _serialize_anomaly(record)
    assert body is not None
    check(
        "FTF-0214",
        "a real anomaly's real previous/new counts are reported",
        body["previous_count"] == 10 and body["new_count"] == 2,
    )
    check("FTF-0214", "a real open anomaly's status is reported", body["status"] == "open")
    check(
        "FTF-0214",
        "an unacknowledged anomaly reports acknowledged_by=None, never a fabricated actor",
        body["acknowledged_by"] is None and body["acknowledged_at"] is None,
    )


def test_serialize_usage_none_and_real() -> None:
    check(
        "FTF-0214",
        "no usage serializes to None, never a zero-filled object",
        _serialize_usage(None) is None,
    )
    usage = ChatUsage(prompt_tokens=9, completion_tokens=1, total_tokens=10)
    body = _serialize_usage(usage)
    assert body is not None
    check(
        "FTF-0214",
        "a real usage object's real token counts are reported",
        body["total_tokens"] == 10,
    )


def test_serialize_probe_result_chat() -> None:
    result = ChatProbeResult(
        state=ChatProbeState.PROBED,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="model-a",
        usage=ChatUsage(prompt_tokens=9, completion_tokens=1, total_tokens=10),
        latency_ms=12.5,
        detail="",
        probed_at=_NOW,
    )
    body = _serialize_probe_result("chat", result)
    check(
        "FTF-0214", "a real chat probe result serializes probe_type", body["probe_type"] == "chat"
    )
    check(
        "FTF-0214", "a real chat probe result serializes its real state", body["state"] == "probed"
    )
    check(
        "FTF-0214",
        "a real chat probe result never carries tool/structured/stream-only keys",
        "advertised_tool_name" not in body and "parsed_content" not in body and "text" not in body,
    )


def test_serialize_probe_result_tool_call() -> None:
    result = ToolCallProbeResult(
        state=ToolCallProbeState.PROBED,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="model-a",
        advertised_tool_name="get_probe_time",
        observed_tool_name="get_probe_time",
        observed_arguments='{"unit":"iso8601"}',
        usage=None,
        latency_ms=5.0,
        detail="",
        probed_at=_NOW,
    )
    body = _serialize_probe_result("tool_call", result)
    check(
        "FTF-0214",
        "a real tool-call probe result reports the real advertised vs. observed tool identity",
        body["advertised_tool_name"] == "get_probe_time"
        and body["observed_tool_name"] == "get_probe_time",
    )
    check(
        "FTF-0214",
        "a tool-call result with no usage reports usage=None, never a zero-filled object",
        body["usage"] is None,
    )


def test_serialize_probe_result_structured_output() -> None:
    result = StructuredOutputProbeResult(
        state=StructuredOutputProbeState.PROBED,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="model-a",
        parsed_content={"word": "pong", "count": 1},
        usage=None,
        latency_ms=5.0,
        detail="",
        probed_at=_NOW,
    )
    body = _serialize_probe_result("structured_output", result)
    check(
        "FTF-0214",
        "a real structured-output probe result's real schema-validated content is serialized",
        body["parsed_content"] == {"word": "pong", "count": 1},
    )


def test_serialize_probe_result_streaming() -> None:
    result = StreamProbeResult(
        state=StreamProbeState.PROBED,
        route_identity_state=RouteIdentityState.VERIFIED,
        resolved_model="model-a",
        text="pong",
        tool_calls=(ToolCallDelta(index=0, id="call-1", name="get_probe_time", arguments="{}"),),
        termination_reason=StreamTerminationReason.STOP,
        usage=ChatUsage(prompt_tokens=9, completion_tokens=1, total_tokens=10),
        chunk_count=3,
        latency_ms=8.0,
        detail="",
        probed_at=_NOW,
    )
    body = _serialize_probe_result("streaming", result)
    check(
        "FTF-0214",
        "a real streaming probe result's reconstructed text is serialized",
        body["text"] == "pong",
    )
    check(
        "FTF-0214",
        "a real streaming probe result's chunk_count is real, not fabricated",
        body["chunk_count"] == 3,
    )
    check(
        "FTF-0214",
        "a real streaming probe result's reconstructed tool-call deltas are serialized",
        body["tool_calls"] == [{"name": "get_probe_time", "arguments": "{}"}],
    )
    check(
        "FTF-0214",
        "a real streaming probe result's termination_reason is serialized by value, not enum repr",
        body["termination_reason"] == "stop",
    )


def main() -> None:
    test_serialize_entry_round_trips_real_fields()
    test_serialize_entry_never_fabricates_a_timestamp_for_a_never_callable_entry()
    test_serialize_anomaly_none_stays_none()
    test_serialize_anomaly_real_record()
    test_serialize_usage_none_and_real()
    test_serialize_probe_result_chat()
    test_serialize_probe_result_tool_call()
    test_serialize_probe_result_structured_output()
    test_serialize_probe_result_streaming()

    if FAILURES:
        print(f"FTF-0214 serializer tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0214 serializer tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
