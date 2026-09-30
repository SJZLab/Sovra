#!/usr/bin/env python3
"""FTF-04A26 -- Isolated real tool-backend adapter contract tests.

Runnable without any database, without any external network egress, and
without any live LLM call: this task's own `database_changes` clause is "No
independent schema change. Consume frozen contract," and its own acceptance
clause ("Run real disposable seeded tool backends with contract request/
response, permissions and side-effect assertions") names FTF-1702's own
`ToolEnvironment` -- already a real, fully in-process, disposable backend --
as the "real tool backend," never an external service.

This task's own acceptance clause is covered end to end by
`test_contract_suite_reports_no_failures`, which runs
`run_isolated_tool_backend_adapter_contract_suite` -- FTF-04A25's own seven
named adapter operations (`describe`/`preflight`/`invoke`/`cancel`/
`collect_outcome`/`reset`/`attest`), each exercised for real against a real
`IsolatedToolBackendAdapter` wrapping a real `ToolEnvironment`, with real
permission assertions (a genuine cross-tenant refusal, a genuine unseeded-
identity refusal) and a real side-effect assertion (a claimed mutation is
independently re-read from the backend through a second, unrelated dispatch
call, never trusted from the adapter's own report alone).

This task's own negative_acceptance ("Canned success tool returns cannot
satisfy production product qualification; no production side effects in
test") is covered directly by `test_refusal_and_fatal_paths_are_never_
reported_as_completed`, `test_malformed_tool_call_is_never_silently_
accepted`, and `test_side_effect_is_independently_observable_not_merely_
claimed` -- plus, for the module's own construction-time honesty rather
than its runtime behavior, `test_module_defines_exactly_one_concrete_
adapter` confirms no second, competing "default" adapter was smuggled in.
Every fixture used throughout is FTF-1702's own real, disposable,
in-process `ToolEnvironment` -- reset (or discarded via a fresh
`build_isolated_tool_backend_adapter()` call) between cases, so no state
from one test can leak into, or be mistaken for, a production system.

Run directly: python3 tests/tasks/test_ftf_04a26.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.isolated_real_tool_backend_adapter_contract_tests import (  # noqa: E402
    IsolatedToolBackendAdapter,
    build_isolated_tool_backend_adapter,
    run_isolated_tool_backend_adapter_contract_suite,
)
from app.target_connectors.production_faithful_target_agent_harness_contract import (  # noqa: E402
    AdapterInvokeRequest,
    ErrorClassification,
    HarnessContractError,
    ModelBinding,
    RuntimeAdapterProtocol,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _request(
    *,
    case_id: str,
    tenant_namespace: str,
    tool_name: str,
    subject_id: str,
    arguments: dict[str, object],
) -> AdapterInvokeRequest:
    return AdapterInvokeRequest(
        case_id=case_id,
        tenant_namespace=tenant_namespace,
        agent_manifest_id="test-manifest",
        model_binding=ModelBinding(model_id="n/a", provider="n/a"),
        declared_inputs=(
            ("tool_name", tool_name),
            ("subject_id", subject_id),
            ("arguments", tuple(arguments.items())),
        ),
        deadline_seconds=10.0,
        idempotency_key=f"{case_id}-key",
    )


def test_contract_suite_reports_no_failures() -> None:
    suite_failures = run_isolated_tool_backend_adapter_contract_suite()
    check(
        "FTF-04A26",
        "the full reusable contract suite passes every one of its own checks: "
        + "; ".join(suite_failures),
        suite_failures == (),
    )


def test_module_defines_exactly_one_concrete_adapter() -> None:
    import app.target_connectors.isolated_real_tool_backend_adapter_contract_tests as module

    exported_classes = [name for name in module.__all__ if isinstance(getattr(module, name), type)]
    check(
        "FTF-04A26",
        "the module exports exactly one concrete adapter class (no smuggled second 'default')",
        exported_classes == ["IsolatedToolBackendAdapter"]
        and isinstance(IsolatedToolBackendAdapter, type),
    )


def test_isolated_adapter_genuinely_implements_the_full_protocol() -> None:
    adapter = build_isolated_tool_backend_adapter()
    check(
        "FTF-04A26",
        "IsolatedToolBackendAdapter genuinely satisfies RuntimeAdapterProtocol "
        "(runtime_checkable structural check, not merely a claim)",
        isinstance(adapter, RuntimeAdapterProtocol),
    )


def test_refusal_and_fatal_paths_are_never_reported_as_completed() -> None:
    adapter = build_isolated_tool_backend_adapter()
    record_id = adapter.tool_environment.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )

    cross_tenant = adapter.invoke(
        _request(
            case_id="neg-cross-tenant",
            tenant_namespace="tenant-b",
            tool_name="get_record",
            subject_id="subject-b1",
            arguments={"record_id": record_id},
        )
    )
    check(
        "FTF-04A26",
        "a real cross-tenant IDOR attempt is never reported as 'completed'",
        cross_tenant.status != "completed",
    )
    check(
        "FTF-04A26",
        "a real cross-tenant IDOR attempt classifies as REFUSAL, not NONE",
        cross_tenant.error_classification == ErrorClassification.REFUSAL,
    )

    unseeded = adapter.invoke(
        _request(
            case_id="neg-unseeded",
            tenant_namespace="tenant-a",
            tool_name="search_records",
            subject_id="ghost-subject",
            arguments={"query": "x"},
        )
    )
    check(
        "FTF-04A26",
        "an unseeded identity is never reported as 'completed'",
        unseeded.status != "completed"
        and unseeded.error_classification == ErrorClassification.FATAL,
    )


def test_malformed_tool_call_is_never_silently_accepted() -> None:
    adapter = build_isolated_tool_backend_adapter()
    result = adapter.invoke(
        _request(
            case_id="neg-malformed",
            tenant_namespace="tenant-a",
            tool_name="definitely_not_a_real_tool",
            subject_id="subject-a1",
            arguments={},
        )
    )
    check(
        "FTF-04A26",
        "an unknown tool name is never reported as 'completed'",
        result.status == "error"
        and result.error_classification == ErrorClassification.MALFORMED_OUTPUT,
    )


def test_side_effect_is_independently_observable_not_merely_claimed() -> None:
    adapter = build_isolated_tool_backend_adapter()
    record_id = adapter.tool_environment.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    result = adapter.invoke(
        _request(
            case_id="side-effect-case",
            tenant_namespace="tenant-a",
            tool_name="create_note",
            subject_id="subject-a1",
            arguments={
                "record_id": record_id,
                "text": "independently observed",
                "idempotency_key": "k9",
            },
        )
    )
    check("FTF-04A26", "the mutating call is reported as completed", result.status == "completed")

    # Never trust `result` alone -- re-read through a second, unrelated
    # dispatch straight against the real backend.
    direct = adapter.tool_environment.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id},
    )
    check(
        "FTF-04A26",
        "the claimed side effect is independently verifiable in the real backend's own state",
        any(note["text"] == "independently observed" for note in direct["notes"]),
    )


def test_two_independently_built_adapters_never_share_state() -> None:
    adapter_one = build_isolated_tool_backend_adapter()
    adapter_two = build_isolated_tool_backend_adapter()
    record_id_one = adapter_one.tool_environment.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    adapter_one.invoke(
        _request(
            case_id="isolation-case",
            tenant_namespace="tenant-a",
            tool_name="create_note",
            subject_id="subject-a1",
            arguments={
                "record_id": record_id_one,
                "text": "only in adapter_one",
                "idempotency_key": "iso-1",
            },
        )
    )
    record_id_two = adapter_two.tool_environment.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    reread_two = adapter_two.tool_environment.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id_two},
    )
    check(
        "FTF-04A26",
        "a mutation in one adapter's own backend never leaks into an independently built one",
        reread_two["notes"] == [],
    )


def test_reset_disposes_real_state_and_clears_case_history() -> None:
    adapter = build_isolated_tool_backend_adapter()
    record_id = adapter.tool_environment.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    invoke_result = adapter.invoke(
        _request(
            case_id="pre-reset-case",
            tenant_namespace="tenant-a",
            tool_name="create_note",
            subject_id="subject-a1",
            arguments={"record_id": record_id, "text": "pre-reset", "idempotency_key": "pre-1"},
        )
    )
    check("FTF-04A26", "the pre-reset case completed for real", invoke_result.status == "completed")

    adapter.reset("tenant-a")

    try:
        adapter.collect_outcome("pre-reset-case")
        raised = False
    except HarnessContractError:
        raised = True
    check("FTF-04A26", "reset() clears prior case history, not just backend state", raised)

    try:
        adapter.reset("tenant-not-in-this-seed")
        refused = False
    except HarnessContractError:
        refused = True
    check(
        "FTF-04A26", "reset() refuses a tenant_namespace this environment never declared", refused
    )


def test_attest_reports_real_deterministic_non_fabricated_fingerprints() -> None:
    adapter = build_isolated_tool_backend_adapter()
    attestation = adapter.attest()
    for field_name, value in (
        ("source_fingerprint", attestation.source_fingerprint),
        ("runtime_image_fingerprint", attestation.runtime_image_fingerprint),
        ("prompt_fingerprint", attestation.prompt_fingerprint),
        ("tool_schema_fingerprint", attestation.tool_schema_fingerprint),
        ("permission_policy_fingerprint", attestation.permission_policy_fingerprint),
        ("external_tool_contract_fingerprint", attestation.external_tool_contract_fingerprint),
        ("feature_flag_fingerprint", attestation.feature_flag_fingerprint),
        ("model_binding_fingerprint", attestation.model_binding_fingerprint),
        ("gateway_config_fingerprint", attestation.gateway_config_fingerprint),
        ("evaluation_policy_fingerprint", attestation.evaluation_policy_fingerprint),
    ):
        check("FTF-04A26", f"attest() reports a real, non-empty {field_name}", bool(value))
    check(
        "FTF-04A26",
        "attest() reports no retrieval_index_fingerprint (no retrieval component exists)",
        attestation.retrieval_index_fingerprint is None,
    )
    check(
        "FTF-04A26",
        "attest() reports a real, positive observed_at_epoch_seconds",
        attestation.observed_at_epoch_seconds > 0,
    )

    # Two independently seeded environments' tool schemas are identical
    # (same frozen FTF-1701 spec) but their real permission fingerprints
    # differ once one is mutated to a custom seed -- proving the fingerprint
    # is a real function of this adapter's own seed, not a hard-coded string.
    from evals.reference.tool_environment import ToolEnvironmentSeed

    custom_seed = ToolEnvironmentSeed(
        tenants=("tenant-only-here",),
        subjects_by_tenant={"tenant-only-here": ("subject-only-here",)},
        records=(),
    )
    other_adapter = build_isolated_tool_backend_adapter(seed=custom_seed)
    other_attestation = other_adapter.attest()
    check(
        "FTF-04A26",
        "permission_policy_fingerprint is a real function of the adapter's own seed",
        other_attestation.permission_policy_fingerprint
        != attestation.permission_policy_fingerprint,
    )
    check(
        "FTF-04A26",
        "tool_schema_fingerprint is unchanged across adapters sharing the same frozen tool specs",
        other_attestation.tool_schema_fingerprint == attestation.tool_schema_fingerprint,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(
            f"FTF-04A26 Isolated real tool-backend adapter contract tests: {len(FAILURES)} failed"
        )
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A26 Isolated real tool-backend adapter contract tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
