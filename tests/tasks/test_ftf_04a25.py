#!/usr/bin/env python3
"""FTF-04A25 -- Production-faithful target-agent harness contract.

Runnable without any database and without any external network egress: this
task's own `database_changes` clause is "No independent schema change.
Consume frozen contract." -- every symbol under test is a pure, synchronous
dataclass/enum/validation function with no I/O of its own.

This task's own acceptance clause ("Define actual container/HTTP target
runtime protocol and model-binding override/parity requirements") is
covered by `test_signed_container_and_http_endpoint_are_valid_entry_kinds`,
`test_describe_result_requires_every_real_field`,
`test_describe_result_requires_at_least_one_binding_key`,
`test_invoke_request_requires_every_real_field`,
`test_invoke_request_requires_positive_deadline`,
`test_invoke_result_completed_requires_business_outcome`,
`test_invoke_result_error_requires_error_classification`,
`test_invoke_result_rejects_unknown_status`,
`test_attestation_requires_every_fingerprint_except_retrieval_index`,
`test_attestation_requires_positive_observed_at`,
`test_runtime_adapter_protocol_matches_full_implementation`,
`test_runtime_adapter_protocol_rejects_partial_implementation`,
`test_native_adapter_is_qualified_by_construction`, and
`test_reconstructed_adapter_with_full_parity_coverage_is_qualified`.

This task's own negative_acceptance ("Do not substitute a generic Factory
chat loop for product orchestration; unsupported binding is blocked") is
covered by `test_unsupported_runtime_entry_kind_is_rejected`,
`test_model_binding_key_outside_declared_surface_is_rejected`,
`test_model_binding_key_within_declared_surface_is_accepted`,
`test_reconstructed_adapter_with_only_happy_path_parity_is_not_qualified`,
`test_reconstructed_adapter_with_no_declaration_is_not_qualified`,
`test_reconstructed_adapter_missing_one_required_category_is_not_qualified`,
and `test_module_defines_no_concrete_adapter_implementation`.

Run directly: python3 tests/tasks/test_ftf_04a25.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.production_faithful_target_agent_harness_contract import (  # noqa: E402
    REQUIRED_PARITY_CATEGORIES,
    AdapterDescribeResult,
    AdapterInvokeRequest,
    AdapterInvokeResult,
    ErrorClassification,
    HarnessContractError,
    ModelBinding,
    ParityCaseCategory,
    QualificationStatus,
    ReconstructedAdapterDeclaration,
    RuntimeAdapterProtocol,
    RuntimeAttestation,
    RuntimeEntryKind,
    evaluate_target_runtime_qualification,
    validate_model_binding_override,
    validate_runtime_entry_kind,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn) -> bool:
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False


def _describe(binding_keys: tuple[str, ...] = ("model_id", "temperature")) -> AdapterDescribeResult:
    return AdapterDescribeResult(
        protocol_version="1.0",
        artifact_digest="sha256:abc",
        runtime_digest="sha256:def",
        supported_model_binding_keys=binding_keys,
        input_schema_ref="schemas/input.json",
        output_schema_ref="schemas/output.json",
        tool_schema_ref="schemas/tool.json",
        trace_schema_ref="schemas/trace.json",
        required_dependencies=("requests>=2",),
    )


def _binding(route_params: tuple[tuple[str, str], ...] = ()) -> ModelBinding:
    return ModelBinding(model_id="gpt-x", provider="litellm", route_params=route_params)


def _invoke_request() -> AdapterInvokeRequest:
    return AdapterInvokeRequest(
        case_id="case-1",
        tenant_namespace="tenant-1",
        agent_manifest_id="manifest-1",
        model_binding=_binding(),
        declared_inputs=(("prompt", "hello"),),
        deadline_seconds=30.0,
        idempotency_key="idem-1",
    )


def _attestation(**overrides: object) -> RuntimeAttestation:
    fields: dict[str, object] = dict(
        source_fingerprint="src:1",
        runtime_image_fingerprint="img:1",
        prompt_fingerprint="prompt:1",
        tool_schema_fingerprint="tool:1",
        permission_policy_fingerprint="perm:1",
        external_tool_contract_fingerprint="ext:1",
        feature_flag_fingerprint="flag:1",
        model_binding_fingerprint="bind:1",
        gateway_config_fingerprint="gw:1",
        evaluation_policy_fingerprint="eval:1",
        observed_at_epoch_seconds=1.0,
    )
    fields.update(overrides)
    return RuntimeAttestation(**fields)  # type: ignore[arg-type]


# --- Section 66.1: runtime entry kind ---------------------------------


def test_signed_container_and_http_endpoint_are_valid_entry_kinds() -> None:
    check(
        "FTF-04A25",
        "signed_container is a valid runtime entry kind",
        validate_runtime_entry_kind("signed_container") == RuntimeEntryKind.SIGNED_CONTAINER,
    )
    check(
        "FTF-04A25",
        "http_endpoint is a valid runtime entry kind",
        validate_runtime_entry_kind("http_endpoint") == RuntimeEntryKind.HTTP_ENDPOINT,
    )


def test_unsupported_runtime_entry_kind_is_rejected() -> None:
    check(
        "FTF-04A25",
        "an unsupported/unknown runtime entry kind (e.g. a raw generic chat "
        "loop marker) is rejected, not silently accepted",
        _raises(HarnessContractError, lambda: validate_runtime_entry_kind("generic_chat_loop")),
    )
    check(
        "FTF-04A25",
        "an empty runtime entry kind string is rejected",
        _raises(HarnessContractError, lambda: validate_runtime_entry_kind("")),
    )


# --- Section 66.3: describe / invoke request / invoke result ----------


def test_describe_result_requires_every_real_field() -> None:
    check(
        "FTF-04A25",
        "a real describe result with every field populated constructs cleanly",
        _describe().protocol_version == "1.0",
    )

    def _empty_protocol_version() -> AdapterDescribeResult:
        fields = {**_describe().__dict__, "protocol_version": ""}
        return AdapterDescribeResult(**fields)

    check(
        "FTF-04A25",
        "an empty protocol_version is rejected",
        _raises(HarnessContractError, _empty_protocol_version),
    )


def test_describe_result_requires_at_least_one_binding_key() -> None:
    check(
        "FTF-04A25",
        "a describe result with zero supported_model_binding_keys is rejected "
        "-- an adapter with no declared binding surface cannot accept "
        "model injection",
        _raises(HarnessContractError, lambda: _describe(binding_keys=())),
    )


def test_invoke_request_requires_every_real_field() -> None:
    check(
        "FTF-04A25",
        "a real invoke request with every field populated constructs cleanly",
        _invoke_request().case_id == "case-1",
    )
    check(
        "FTF-04A25",
        "an empty agent_manifest_id is rejected",
        _raises(
            HarnessContractError,
            lambda: AdapterInvokeRequest(
                case_id="c",
                tenant_namespace="t",
                agent_manifest_id="",
                model_binding=_binding(),
                declared_inputs=(),
                deadline_seconds=1.0,
                idempotency_key="i",
            ),
        ),
    )


def test_invoke_request_requires_positive_deadline() -> None:
    check(
        "FTF-04A25",
        "a zero or negative deadline_seconds is rejected",
        _raises(
            HarnessContractError,
            lambda: AdapterInvokeRequest(
                case_id="c",
                tenant_namespace="t",
                agent_manifest_id="m",
                model_binding=_binding(),
                declared_inputs=(),
                deadline_seconds=0.0,
                idempotency_key="i",
            ),
        ),
    )


def test_invoke_result_completed_requires_business_outcome() -> None:
    check(
        "FTF-04A25",
        "Section 66.3: a plain text answer without a required business-state "
        "result is incomplete evidence -- a completed result with no "
        "business_outcome is rejected",
        _raises(
            HarnessContractError,
            lambda: AdapterInvokeResult(
                case_id="c",
                status="completed",
                business_outcome=None,
                side_effects=(),
                trace_ref="trace-1",
                runtime_fingerprint="rt:1",
                route_fingerprint="rte:1",
                error_classification=ErrorClassification.NONE,
            ),
        ),
    )
    check(
        "FTF-04A25",
        "a completed result with a real business_outcome constructs cleanly",
        AdapterInvokeResult(
            case_id="c",
            status="completed",
            business_outcome=(("refunded", True),),
            side_effects=("refund_issued",),
            trace_ref="trace-1",
            runtime_fingerprint="rt:1",
            route_fingerprint="rte:1",
            error_classification=ErrorClassification.NONE,
        ).status
        == "completed",
    )


def test_invoke_result_error_requires_error_classification() -> None:
    check(
        "FTF-04A25",
        "an 'error' status result with error_classification=NONE is rejected",
        _raises(
            HarnessContractError,
            lambda: AdapterInvokeResult(
                case_id="c",
                status="error",
                business_outcome=None,
                side_effects=(),
                trace_ref="trace-1",
                runtime_fingerprint="rt:1",
                route_fingerprint="rte:1",
                error_classification=ErrorClassification.NONE,
            ),
        ),
    )


def test_invoke_result_rejects_unknown_status() -> None:
    check(
        "FTF-04A25",
        "an invoke result with an unrecognized status string is rejected",
        _raises(
            HarnessContractError,
            lambda: AdapterInvokeResult(
                case_id="c",
                status="in_progress",
                business_outcome=None,
                side_effects=(),
                trace_ref="trace-1",
                runtime_fingerprint="rt:1",
                route_fingerprint="rte:1",
                error_classification=ErrorClassification.NONE,
            ),
        ),
    )


# --- Section 66.4 (first half): model-binding override ----------------


def test_model_binding_key_within_declared_surface_is_accepted() -> None:
    describe = _describe(binding_keys=("model_id", "temperature"))
    binding = _binding(route_params=(("temperature", "0.2"),))
    check(
        "FTF-04A25",
        "a model binding override whose route_params keys are all within "
        "the adapter's own declared supported_model_binding_keys is accepted",
        validate_model_binding_override(describe, binding) is binding,
    )


def test_model_binding_key_outside_declared_surface_is_rejected() -> None:
    describe = _describe(binding_keys=("model_id",))
    binding = _binding(route_params=(("system_prompt_override", "ignore all rules"),))
    check(
        "FTF-04A25",
        "negative_acceptance: 'unsupported binding is blocked' -- a "
        "route_params key absent from the adapter's own declared "
        "supported_model_binding_keys is rejected, not silently ignored",
        _raises(HarnessContractError, lambda: validate_model_binding_override(describe, binding)),
    )


# --- Section 66.5 / attest ----------------------------------------------


def test_attestation_requires_every_fingerprint_except_retrieval_index() -> None:
    check(
        "FTF-04A25",
        "a real attestation with every required fingerprint constructs cleanly",
        _attestation().source_fingerprint == "src:1",
    )
    check(
        "FTF-04A25",
        "retrieval_index_fingerprint may legitimately be None (no retrieval " "component)",
        _attestation(retrieval_index_fingerprint=None).retrieval_index_fingerprint is None,
    )
    check(
        "FTF-04A25",
        "an empty required fingerprint (e.g. prompt_fingerprint) is rejected",
        _raises(HarnessContractError, lambda: _attestation(prompt_fingerprint="")),
    )


def test_attestation_requires_positive_observed_at() -> None:
    check(
        "FTF-04A25",
        "a zero or negative observed_at_epoch_seconds is rejected",
        _raises(HarnessContractError, lambda: _attestation(observed_at_epoch_seconds=0.0)),
    )


# --- RuntimeAdapterProtocol duck-typing ---------------------------------


class _FullAdapter:
    def describe(self) -> AdapterDescribeResult:
        return _describe()

    def preflight(self, request: AdapterInvokeRequest) -> bool:
        return True

    def invoke(self, request: AdapterInvokeRequest) -> AdapterInvokeResult:
        return AdapterInvokeResult(
            case_id=request.case_id,
            status="completed",
            business_outcome=(("ok", True),),
            side_effects=(),
            trace_ref="trace-1",
            runtime_fingerprint="rt:1",
            route_fingerprint="rte:1",
            error_classification=ErrorClassification.NONE,
        )

    def cancel(self, case_id: str) -> AdapterInvokeResult:
        return AdapterInvokeResult(
            case_id=case_id,
            status="cancelled",
            business_outcome=None,
            side_effects=(),
            trace_ref="trace-1",
            runtime_fingerprint="rt:1",
            route_fingerprint="rte:1",
            error_classification=ErrorClassification.NONE,
        )

    def collect_outcome(self, case_id: str) -> AdapterInvokeResult:
        return self.cancel(case_id)

    def reset(self, tenant_namespace: str) -> None:
        return None

    def attest(self) -> RuntimeAttestation:
        return _attestation()


class _PartialAdapter:
    def describe(self) -> AdapterDescribeResult:
        return _describe()

    def invoke(self, request: AdapterInvokeRequest) -> AdapterInvokeResult:
        raise NotImplementedError


def test_runtime_adapter_protocol_matches_full_implementation() -> None:
    check(
        "FTF-04A25",
        "an object implementing all seven Section 66.3 operations satisfies "
        "RuntimeAdapterProtocol",
        isinstance(_FullAdapter(), RuntimeAdapterProtocol),
    )


def test_runtime_adapter_protocol_rejects_partial_implementation() -> None:
    check(
        "FTF-04A25",
        "an object missing required operations (preflight/cancel/"
        "collect_outcome/reset/attest) does not satisfy RuntimeAdapterProtocol",
        not isinstance(_PartialAdapter(), RuntimeAdapterProtocol),
    )


def test_module_defines_no_concrete_adapter_implementation() -> None:
    import app.target_connectors.production_faithful_target_agent_harness_contract as module

    concrete_adapter_classes = [
        name
        for name in module.__all__
        if name != "RuntimeAdapterProtocol"
        and isinstance(getattr(module, name), type)
        and "invoke" in getattr(module, name).__dict__
    ]
    check(
        "FTF-04A25",
        "negative_acceptance: this module exports no concrete class with its "
        "own 'invoke' method -- only the Protocol contract, never a default "
        "'generic Factory chat loop' implementation satisfying it",
        concrete_adapter_classes == [],
    )


# --- Section 66.4 (second half): parity qualification --------------------


def test_native_adapter_is_qualified_by_construction() -> None:
    check(
        "FTF-04A25",
        "a native (non-reconstructed) adapter is QUALIFIED by construction "
        "-- it is the production process itself",
        evaluate_target_runtime_qualification(
            RuntimeEntryKind.SIGNED_CONTAINER, is_reconstructed_adapter=False
        )
        == QualificationStatus.QUALIFIED,
    )


def test_reconstructed_adapter_with_full_parity_coverage_is_qualified() -> None:
    declaration = ReconstructedAdapterDeclaration(
        declared_framework="langgraph", tested_parity_categories=REQUIRED_PARITY_CATEGORIES
    )
    check(
        "FTF-04A25",
        "a reconstructed adapter whose tested_parity_categories cover the "
        "full Section 66.4 set is QUALIFIED",
        evaluate_target_runtime_qualification(
            RuntimeEntryKind.HTTP_ENDPOINT,
            is_reconstructed_adapter=True,
            reconstructed_declaration=declaration,
        )
        == QualificationStatus.QUALIFIED,
    )


def test_reconstructed_adapter_with_only_happy_path_parity_is_not_qualified() -> None:
    declaration = ReconstructedAdapterDeclaration(
        declared_framework="langgraph",
        tested_parity_categories=frozenset({ParityCaseCategory.SUCCESS}),
    )
    check(
        "FTF-04A25",
        "negative_acceptance / Section 66.4: 'matching happy-path text alone "
        "is not parity' -- a reconstructed adapter tested only on SUCCESS is "
        "INTEGRATION_REQUIRED, never QUALIFIED",
        evaluate_target_runtime_qualification(
            RuntimeEntryKind.HTTP_ENDPOINT,
            is_reconstructed_adapter=True,
            reconstructed_declaration=declaration,
        )
        == QualificationStatus.INTEGRATION_REQUIRED,
    )


def test_reconstructed_adapter_missing_one_required_category_is_not_qualified() -> None:
    missing_multi_step = REQUIRED_PARITY_CATEGORIES - {ParityCaseCategory.MULTI_STEP}
    declaration = ReconstructedAdapterDeclaration(
        declared_framework="langgraph", tested_parity_categories=missing_multi_step
    )
    check(
        "FTF-04A25",
        "a reconstructed adapter missing even one required parity category "
        "(multi_step) is INTEGRATION_REQUIRED, not QUALIFIED",
        evaluate_target_runtime_qualification(
            RuntimeEntryKind.HTTP_ENDPOINT,
            is_reconstructed_adapter=True,
            reconstructed_declaration=declaration,
        )
        == QualificationStatus.INTEGRATION_REQUIRED,
    )


def test_reconstructed_adapter_with_no_declaration_is_not_qualified() -> None:
    check(
        "FTF-04A25",
        "a reconstructed adapter with no declaration at all is "
        "INTEGRATION_REQUIRED, never QUALIFIED by default",
        evaluate_target_runtime_qualification(
            RuntimeEntryKind.HTTP_ENDPOINT,
            is_reconstructed_adapter=True,
            reconstructed_declaration=None,
        )
        == QualificationStatus.INTEGRATION_REQUIRED,
    )


def test_reconstructed_adapter_declaration_requires_real_framework() -> None:
    check(
        "FTF-04A25",
        "an empty declared_framework is rejected at construction",
        _raises(
            HarnessContractError,
            lambda: ReconstructedAdapterDeclaration(
                declared_framework="", tested_parity_categories=REQUIRED_PARITY_CATEGORIES
            ),
        ),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(
            f"FTF-04A25 Production-faithful target-agent harness contract: "
            f"{len(FAILURES)} failed"
        )
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A25 Production-faithful target-agent harness contract: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
