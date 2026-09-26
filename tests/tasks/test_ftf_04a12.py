#!/usr/bin/env python3
"""FTF-04A12 -- LiteLLM/current-model binding discovery.

Runnable without any database and without any external network egress:
`discover_current_model_bindings` is a pure, synchronous, no-I/O function
(this task's own `database_changes` clause -- "No independent schema
change. Consume frozen contract.") over already-computed evidence objects
from FTF-04A09/FTF-04A10/FTF-04A11 (all CERTIFIED) and an optional,
already-computed FTF-0207 `AliasReconciliationResult` (also CERTIFIED).

This task's own acceptance clause ("Identify literal/config-derived model
binding with actual gateway role confirmation later") is covered by
`test_python_literal_binding_confirmed_by_gateway`,
`test_typescript_javascript_literal_binding_confirmed_by_gateway`,
`test_config_derived_model_key_resolved_as_literal_config_value`,
`test_config_derived_alias_key_variant_is_recognized`,
`test_gateway_confirmation_not_attempted_without_reconciliation_data`, and
`test_gateway_not_found_when_alias_absent_from_reconciled_catalog`.

This task's own negative_acceptance ("Alias does not reveal weights or
enforceable access; unresolved dynamic model selection remains explicit")
is covered by `test_unresolved_python_dynamic_expression_is_not_reconciled`,
`test_unresolved_typescript_javascript_binding_is_not_reconciled`,
`test_unresolved_binding_forces_model_alias_none_even_if_upstream_disagrees`,
`test_config_key_with_non_string_value_is_not_guessed_into_alias`,
`test_config_key_with_empty_string_value_is_not_guessed_into_alias`,
`test_secret_like_config_key_is_never_treated_as_model_binding`,
`test_unrelated_config_key_is_not_treated_as_model_binding`,
`test_unresolved_config_document_produces_no_candidates`,
`test_gateway_metadata_fields_are_never_exposed_in_output`, and
`test_resolved_true_with_no_alias_raises_error`.

Run directly: python3 tests/tasks/test_ftf_04a12.py
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

from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    AliasReconciliationResult,
    ReconciledModel,
)
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus  # noqa: E402

from app.target_connectors.json_yaml_toml_configuration_discovery import (  # noqa: E402
    ConfigDocumentEvidence,
    ConfigKeyEvidence,
)
from app.target_connectors.litellm_current_model_binding_discovery import (  # noqa: E402
    BindingSource,
    CurrentModelBindingDiscoveryError,
    GatewayConfirmationStatus,
    discover_current_model_bindings,
)
from app.target_connectors.python_agent_config_tool_discovery import (  # noqa: E402
    EntrypointCandidate as PythonEntrypointCandidate,
)
from app.target_connectors.python_agent_config_tool_discovery import (  # noqa: E402
    ModelBindingEvidence as PythonModelBindingEvidence,
)
from app.target_connectors.python_agent_config_tool_discovery import (  # noqa: E402
    ToolSchemaEvidence as PythonToolSchemaEvidence,
)
from app.target_connectors.typescript_javascript_deterministic_agent_config_tool_discovery import (  # noqa: E402
    EntrypointCandidate as TsEntrypointCandidate,
)
from app.target_connectors.typescript_javascript_deterministic_agent_config_tool_discovery import (  # noqa: E402
    ModelBindingEvidence as TsModelBindingEvidence,
)
from app.target_connectors.typescript_javascript_deterministic_agent_config_tool_discovery import (  # noqa: E402
    ToolSchemaEvidence as TsToolSchemaEvidence,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_NO_TOOL_SCHEMA_PY = PythonToolSchemaEvidence(
    resolved=False, tool_names=(), reason="argument_not_present"
)
_NO_TOOL_SCHEMA_TS = TsToolSchemaEvidence(
    resolved=False, tool_names=(), reason="argument_not_present"
)


def _python_entrypoint(
    *, relative_path: str = "src/agent.py", model_alias: str | None, resolved: bool, reason: str
) -> PythonEntrypointCandidate:
    return PythonEntrypointCandidate(
        relative_path=relative_path,
        line=1,
        function_name="handle_request",
        call_attribute="litellm.completion",
        model_binding=PythonModelBindingEvidence(
            resolved=resolved, model_alias=model_alias, reason=reason
        ),
        tool_schema=_NO_TOOL_SCHEMA_PY,
    )


def _ts_entrypoint(
    *, relative_path: str = "src/agent.ts", model_alias: str | None, resolved: bool, reason: str
) -> TsEntrypointCandidate:
    return TsEntrypointCandidate(
        relative_path=relative_path,
        line=1,
        function_name="handleRequest",
        call_attribute="client.chat.completions.create",
        model_binding=TsModelBindingEvidence(
            resolved=resolved, model_alias=model_alias, reason=reason
        ),
        tool_schema=_NO_TOOL_SCHEMA_TS,
    )


def _config_document(
    *, keys: tuple[ConfigKeyEvidence, ...], resolved: bool = True
) -> ConfigDocumentEvidence:
    return ConfigDocumentEvidence(
        relative_path="config/agent.yaml",
        format="yaml",
        resolved=resolved,
        reason="parsed" if resolved else "malformed_document",
        keys=keys,
        keys_truncated=False,
    )


def _reconciliation(*models: ReconciledModel) -> AliasReconciliationResult:
    return AliasReconciliationResult(
        models=models, model_count=len(models), reconciled_at=datetime.now(UTC)
    )


def test_python_literal_binding_confirmed_by_gateway() -> None:
    reconciliation = _reconciliation(
        ReconciledModel(
            id="support-agent-prod",
            metadata_status=ModelMetadataStatus.ENRICHED,
            fields=MappingProxyType({}),
        )
    )
    evidence = discover_current_model_bindings(
        python_entrypoints=[
            _python_entrypoint(
                model_alias="support-agent-prod", resolved=True, reason="literal_string"
            )
        ],
        alias_reconciliation=reconciliation,
    )
    candidate = evidence.candidates[0]
    check(
        "FTF-04A12",
        "a real literal Python model= binding is identified as python_literal and "
        "confirmed against the real reconciled gateway catalog",
        candidate.source is BindingSource.PYTHON_LITERAL
        and candidate.model_alias == "support-agent-prod"
        and candidate.gateway_confirmation_status is GatewayConfirmationStatus.CONFIRMED
        and candidate.gateway_metadata_status == "enriched",
    )


def test_typescript_javascript_literal_binding_confirmed_by_gateway() -> None:
    reconciliation = _reconciliation(
        ReconciledModel(
            id="triage-agent",
            metadata_status=ModelMetadataStatus.LIMITED,
            fields=MappingProxyType({}),
        )
    )
    evidence = discover_current_model_bindings(
        typescript_javascript_entrypoints=[
            _ts_entrypoint(model_alias="triage-agent", resolved=True, reason="literal_string")
        ],
        alias_reconciliation=reconciliation,
    )
    candidate = evidence.candidates[0]
    check(
        "FTF-04A12",
        "a real literal TS/JS model binding is identified as "
        "typescript_javascript_literal and confirmed against the real reconciled "
        "gateway catalog",
        candidate.source is BindingSource.TYPESCRIPT_JAVASCRIPT_LITERAL
        and candidate.model_alias == "triage-agent"
        and candidate.gateway_confirmation_status is GatewayConfirmationStatus.CONFIRMED
        and candidate.gateway_metadata_status == "limited",
    )


def test_config_derived_model_key_resolved_as_literal_config_value() -> None:
    document = _config_document(
        keys=(
            ConfigKeyEvidence(
                key_path="agent.model", value_type="string", secret_like=False, value="gpt-4o"
            ),
        )
    )
    evidence = discover_current_model_bindings(config_documents=[document])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A12",
        "a real config key path ending in a closed 'model' name is identified as "
        "config_derived with the real literal string value",
        candidate.source is BindingSource.CONFIG_DERIVED
        and candidate.resolved is True
        and candidate.model_alias == "gpt-4o"
        and candidate.reason == "literal_config_value",
    )


def test_config_derived_alias_key_variant_is_recognized() -> None:
    document = _config_document(
        keys=(
            ConfigKeyEvidence(
                key_path="gateway.litellm_alias",
                value_type="string",
                secret_like=False,
                value="internal-router",
            ),
        )
    )
    evidence = discover_current_model_bindings(config_documents=[document])
    check(
        "FTF-04A12",
        "the closed 'litellm_alias' key-name variant (normalized) is recognized the "
        "same way 'model' is",
        len(evidence.candidates) == 1 and evidence.candidates[0].model_alias == "internal-router",
    )


def test_gateway_confirmation_not_attempted_without_reconciliation_data() -> None:
    evidence = discover_current_model_bindings(
        python_entrypoints=[
            _python_entrypoint(model_alias="some-alias", resolved=True, reason="literal_string")
        ],
        alias_reconciliation=None,
    )
    check(
        "FTF-04A12",
        "with no caller-supplied AliasReconciliationResult, a resolved alias is "
        "honestly reported not_attempted, never guessed into confirmed",
        evidence.candidates[0].gateway_confirmation_status
        is GatewayConfirmationStatus.NOT_ATTEMPTED
        and evidence.candidates[0].gateway_metadata_status is None,
    )


def test_gateway_not_found_when_alias_absent_from_reconciled_catalog() -> None:
    reconciliation = _reconciliation(
        ReconciledModel(
            id="a-different-alias",
            metadata_status=ModelMetadataStatus.ENRICHED,
            fields=MappingProxyType({}),
        )
    )
    evidence = discover_current_model_bindings(
        python_entrypoints=[
            _python_entrypoint(
                model_alias="hallucinated-alias", resolved=True, reason="literal_string"
            )
        ],
        alias_reconciliation=reconciliation,
    )
    check(
        "FTF-04A12",
        "a real literal alias absent from the real reconciled catalog is reported "
        "not_found, never silently confirmed",
        evidence.candidates[0].gateway_confirmation_status is GatewayConfirmationStatus.NOT_FOUND
        and evidence.candidates[0].gateway_metadata_status is None
        and evidence.gateway_not_found_count == 1,
    )


def test_unresolved_python_dynamic_expression_is_not_reconciled() -> None:
    reconciliation = _reconciliation(
        ReconciledModel(
            id="x", metadata_status=ModelMetadataStatus.ENRICHED, fields=MappingProxyType({})
        )
    )
    evidence = discover_current_model_bindings(
        python_entrypoints=[
            _python_entrypoint(
                model_alias=None, resolved=False, reason="unresolved_dynamic_expression"
            )
        ],
        alias_reconciliation=reconciliation,
    )
    candidate = evidence.candidates[0]
    check(
        "FTF-04A12",
        "an unresolved dynamic Python model binding stays unresolved with no alias "
        "and is never reconciled against the gateway",
        candidate.resolved is False
        and candidate.model_alias is None
        and candidate.gateway_confirmation_status is GatewayConfirmationStatus.NOT_APPLICABLE,
    )


def test_unresolved_typescript_javascript_binding_is_not_reconciled() -> None:
    evidence = discover_current_model_bindings(
        typescript_javascript_entrypoints=[
            _ts_entrypoint(model_alias=None, resolved=False, reason="unresolved_dynamic_expression")
        ]
    )
    candidate = evidence.candidates[0]
    check(
        "FTF-04A12",
        "an unresolved dynamic TS/JS model binding stays unresolved with no alias",
        candidate.resolved is False and candidate.model_alias is None,
    )


def test_unresolved_binding_forces_model_alias_none_even_if_upstream_disagrees() -> None:
    # A structurally malformed upstream evidence object (resolved=False but a
    # non-None model_alias) -- this module's own negative_acceptance is
    # enforced unconditionally, never trusting the upstream field alone.
    malformed = _python_entrypoint(
        model_alias="should-never-surface", resolved=False, reason="weird"
    )
    evidence = discover_current_model_bindings(python_entrypoints=[malformed])
    check(
        "FTF-04A12",
        "even when an upstream evidence object inconsistently carries a "
        "model_alias alongside resolved=False, this module forces model_alias to "
        "None rather than trusting/surfacing it",
        evidence.candidates[0].model_alias is None,
    )


def test_config_key_with_non_string_value_is_not_guessed_into_alias() -> None:
    document = _config_document(
        keys=(
            ConfigKeyEvidence(
                key_path="agent.model", value_type="number", secret_like=False, value=4
            ),
        )
    )
    evidence = discover_current_model_bindings(config_documents=[document])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A12",
        "a 'model'-named config key whose real value is not a string is never "
        "guessed into an alias",
        candidate.resolved is False
        and candidate.model_alias is None
        and candidate.reason == "unresolved_non_string_value",
    )


def test_config_key_with_empty_string_value_is_not_guessed_into_alias() -> None:
    document = _config_document(
        keys=(
            ConfigKeyEvidence(
                key_path="agent.model", value_type="string", secret_like=False, value=""
            ),
        )
    )
    evidence = discover_current_model_bindings(config_documents=[document])
    candidate = evidence.candidates[0]
    check(
        "FTF-04A12",
        "an empty-string 'model' config value is never guessed into a real alias",
        candidate.resolved is False and candidate.reason == "empty_string_value",
    )


def test_secret_like_config_key_is_never_treated_as_model_binding() -> None:
    document = _config_document(
        keys=(
            ConfigKeyEvidence(
                key_path="agent.model",
                value_type="string",
                secret_like=True,
                value="<redacted:6 chars>",
            ),
        )
    )
    evidence = discover_current_model_bindings(config_documents=[document])
    check(
        "FTF-04A12",
        "a secret-shaped config key is never treated as a model-binding candidate, "
        "even when its own key name matches the closed model-key vocabulary",
        len(evidence.candidates) == 0,
    )


def test_unrelated_config_key_is_not_treated_as_model_binding() -> None:
    document = _config_document(
        keys=(
            ConfigKeyEvidence(
                key_path="agent.model_version", value_type="string", secret_like=False, value="v3"
            ),
        )
    )
    evidence = discover_current_model_bindings(config_documents=[document])
    check(
        "FTF-04A12",
        "an unrelated key name ('model_version') that is not an exact match against "
        "the closed vocabulary is never guessed into a model-binding candidate",
        len(evidence.candidates) == 0,
    )


def test_unresolved_config_document_produces_no_candidates() -> None:
    document = _config_document(
        keys=(
            ConfigKeyEvidence(
                key_path="agent.model", value_type="string", secret_like=False, value="x"
            ),
        ),
        resolved=False,
    )
    evidence = discover_current_model_bindings(config_documents=[document])
    check(
        "FTF-04A12",
        "a config document FTF-04A11 itself could not resolve (malformed content) "
        "yields no model-binding candidates, never a guessed one",
        len(evidence.candidates) == 0,
    )


def test_gateway_metadata_fields_are_never_exposed_in_output() -> None:
    reconciliation = _reconciliation(
        ReconciledModel(
            id="prod-alias",
            metadata_status=ModelMetadataStatus.ENRICHED,
            fields=MappingProxyType(
                {
                    "provider": "openai",
                    "input_cost_per_token": 0.00001,
                    "weights_path": "s3://secret-bucket/w",
                }
            ),
        )
    )
    evidence = discover_current_model_bindings(
        python_entrypoints=[
            _python_entrypoint(model_alias="prod-alias", resolved=True, reason="literal_string")
        ],
        alias_reconciliation=reconciliation,
    )
    payload = evidence.candidates[0].to_json()
    serialized = repr(payload)
    check(
        "FTF-04A12",
        "confirming an alias against the gateway catalog never exposes "
        "ReconciledModel.fields (provider/cost/weights-path or any other raw "
        "metadata) anywhere in this module's own output -- only a bare "
        "enriched/limited status string",
        "weights_path" not in serialized
        and "secret-bucket" not in serialized
        and "provider" not in serialized
        and "input_cost_per_token" not in serialized
        and payload["gateway_metadata_status"] == "enriched",
    )


def test_resolved_true_with_no_alias_raises_error() -> None:
    malformed = _python_entrypoint(model_alias=None, resolved=True, reason="literal_string")
    raised = False
    try:
        discover_current_model_bindings(python_entrypoints=[malformed])
    except CurrentModelBindingDiscoveryError:
        raised = True
    check(
        "FTF-04A12",
        "an upstream evidence object claiming resolved=True with no real "
        "model_alias violates its own contract and is rejected outright, not "
        "silently accepted",
        raised,
    )


def test_summary_counts_are_accurate() -> None:
    reconciliation = _reconciliation(
        ReconciledModel(
            id="ok-alias", metadata_status=ModelMetadataStatus.ENRICHED, fields=MappingProxyType({})
        )
    )
    evidence = discover_current_model_bindings(
        python_entrypoints=[
            _python_entrypoint(model_alias="ok-alias", resolved=True, reason="literal_string"),
            _python_entrypoint(model_alias="missing-alias", resolved=True, reason="literal_string"),
            _python_entrypoint(
                model_alias=None, resolved=False, reason="unresolved_dynamic_expression"
            ),
        ],
        alias_reconciliation=reconciliation,
    )
    check(
        "FTF-04A12",
        "the real aggregate resolved_count/gateway_confirmed_count/"
        "gateway_not_found_count summary matches the real per-candidate outcomes",
        evidence.resolved_count == 2
        and evidence.gateway_confirmed_count == 1
        and evidence.gateway_not_found_count == 1,
    )


def test_no_inputs_produces_empty_evidence() -> None:
    evidence = discover_current_model_bindings()
    check(
        "FTF-04A12",
        "no upstream evidence at all produces empty, honest evidence, never a "
        "fabricated candidate",
        evidence.candidates == ()
        and evidence.resolved_count == 0
        and evidence.gateway_confirmed_count == 0,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A12 LiteLLM/current-model binding discovery: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A12 LiteLLM/current-model binding discovery: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
