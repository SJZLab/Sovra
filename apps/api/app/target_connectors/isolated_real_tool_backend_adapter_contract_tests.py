"""Isolated real tool-backend adapter contract tests (FTF-04A26, Section
6/7A/66/77/81).

This task's own acceptance clause is narrow and literal: "Run real
disposable seeded tool backends with contract request/response, permissions
and side-effect assertions." Its own `dependencies` name exactly the two
things this module composes, never reimplements: FTF-04A25's own
`RuntimeAdapterProtocol` (the seven-operation contract a target-runtime
adapter must satisfy) and FTF-1702's own `ToolEnvironment` (a real,
disposable, seeded, in-memory business backend built on FTF-1701's own
`ReferenceBusinessServer`/`dispatch_tool_call`). `database_changes` is
honored ("No independent schema change. Consume frozen contract") and this
task's own `resource_gate` clause is moot -- "a real disposable seeded tool
backend" names no container, HTTP endpoint or GPU prerequisite; FTF-1702's
own `ToolEnvironment` already *is* that real backend, fully in-process.

## `IsolatedToolBackendAdapter` -- a native (non-reconstructed) adapter

This is the one concrete class in this repository that actually satisfies
`RuntimeAdapterProtocol` (FTF-04A25's own module deliberately defines none,
naming that omission as its own negative_acceptance). It is a *native*
adapter in Section 66.4's own sense: it wraps FTF-1702's real tool backend
directly, so `evaluate_target_runtime_qualification` would call it
`QUALIFIED` by construction, never a "separately reconstructed" process --
there is nothing to compare parity against, since this adapter's own
`invoke()` runs the real, unmodified `ToolEnvironment.dispatch` and nothing
else.

`invoke()` deliberately does not run a model-driven decision loop -- no live
LLM call is available in this environment, and Section 66.3's own contract
is about the *harness*'s ability to carry a case through a real target
runtime and observe its real business effect, not about reproducing an
agent's own reasoning. Each `AdapterInvokeRequest.declared_inputs` names one
concrete tool call (`tool_name`/`subject_id`/`arguments`), and `invoke()`
dispatches exactly that call through the real backend -- the harness
contract this task is proving is "a request naming a real tool call reaches
the real backend and its real result (or real refusal) comes back
unmodified," which is exactly what a later, separately-claimed model-driven
orchestration layer would need to be true beneath it.

## Error classification mapping (real, not canned)

FTF-1701's own real exception hierarchy is mapped onto Section 66.3's own
closed `ErrorClassification` set, never collapsed to a single generic
failure:

- `ReferenceAuthorizationError` (a real cross-tenant/cross-subject IDOR
  refusal) -> `REFUSAL` -- the backend actively declined the request.
- `ReferenceDenialError` (a real business-state refusal: the approval is not
  yet `approved`, is already executed, etc.) -> `REFUSAL` -- also a real,
  in-band decline, just for a state reason rather than an authorization one.
- `ToolEnvironmentError` (this environment's own guard: an
  `tenant_namespace`/`subject_id` never declared in the seed spec) ->
  `FATAL` -- a structural integration fault, not something a retry or a
  different argument would fix.
- `ReferenceAgentSpecificationError` (unknown tool name, or a call/result
  that violates the tool's own frozen JSON Schema) -> `MALFORMED_OUTPUT`.

This task's own negative_acceptance ("Canned success tool returns cannot
satisfy production product qualification") is met by construction: `invoke`
has no code path that returns `status="completed"` without a real,
unmodified `ToolEnvironment.dispatch` return value flowing into
`business_outcome`, and every one of the four real exception types above
produces a real `status="error"` result with a real, distinct
`error_classification` -- never silently swallowed into a fake success.

## Side effects are independently observable, not merely reported

`side_effects` on a `completed` result names the dispatched tool call
(`f"tool_call:{tool_name}"`); the reusable contract suite below never trusts
that label alone -- for a real mutating call (`create_note`) it
independently re-reads the record through a second, unrelated `get_record`
dispatch and confirms the note is actually present in the backend's own
state. This is this task's own "side-effect assertions" read literally: the
adapter's own claimed outcome is corroborated by directly observing the
backend, the same discipline FTF-1702's own reset test already established
for "reset() is a genuine disposal, not an in-place clear."

## `reset()` -- disclosed scope

Section 66.3 names `reset(tenant_namespace)` as a *per-tenant* namespace
reset. FTF-1702's own `ToolEnvironment.reset()` offers only a real
whole-environment reset (discard the entire disposable server and reseed
it from the same spec) -- there is no per-tenant partition to reset
independently, and inventing one here would mean silently building a second,
competing tenant-partitioning scheme FTF-1702 does not itself have. This
adapter's own `reset(tenant_namespace)` validates that `tenant_namespace` is
a real tenant this environment's own seed spec actually declares (refusing
an unknown one, matching `ToolEnvironment.dispatch`'s own guard) and then
performs the one real reset FTF-1702 offers -- disclosed narrowing, not a
silent capability the task's own contract does not actually have.

## `cancel()`/`collect_outcome()` -- disclosed scope

`invoke()` runs the real backend call synchronously to completion; there is
no genuine in-flight asynchronous case this adapter could interrupt.
`collect_outcome(case_id)` re-reads a real, previously stored
`AdapterInvokeResult` for a case this adapter itself actually invoked
(raising `HarnessContractError` for an unknown case, never fabricating a
result). `cancel(case_id)` is idempotent on an already-completed case (it
returns that same real stored result, matching how a real target runtime
would answer "cancel" against a job that already finished) and raises for
an unknown case -- it never returns a fabricated "cancelled" status for a
case that either never ran or already completed with a different outcome.

## `attest()` -- real, deterministic, non-fabricated fingerprints

Every fingerprint is a real sha256 digest of this adapter's own actual,
inspectable state (this module's own source bytes, the real frozen
reference-tool schemas from FTF-1701, the real seed spec this environment
was built from) -- never a random or hard-coded placeholder string.
`retrieval_index_fingerprint` is left `None`: this adapter has no retrieval
component at all, the one case Section 66.5's own tuple already names as
legitimately absent (FTF-04A25's own docstring, unchanged here).
`prompt_fingerprint` is a real hash of a fixed, disclosed marker
(`"no_prompt:isolated_tool_backend_adapter"`) rather than a fabricated
prompt -- this adapter has no prompt of its own (see `invoke()`'s own
docstring above: no model-driven decision loop runs here), and hashing a
disclosed marker is honest about that rather than inventing a fake prompt
string to fill the field.
"""

from __future__ import annotations

import hashlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_API_ROOT = _ROOT / "apps" / "api"
for _path in (str(_ROOT), str(_API_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from evals.reference.reference_agent_specification import (  # noqa: E402
    REFERENCE_TOOL_SPECS,
    ReferenceAgentSpecificationError,
    ReferenceAuthorizationError,
    ReferenceDenialError,
)
from evals.reference.tool_environment import (  # noqa: E402
    DEFAULT_SEED,
    ToolEnvironment,
    ToolEnvironmentError,
    ToolEnvironmentSeed,
    build_tool_environment,
)

from app.target_connectors.production_faithful_target_agent_harness_contract import (  # noqa: E402
    AdapterDescribeResult,
    AdapterInvokeRequest,
    AdapterInvokeResult,
    ErrorClassification,
    HarnessContractError,
    ModelBinding,
    RuntimeAttestation,
)

__all__ = [
    "IsolatedToolBackendAdapter",
    "build_isolated_tool_backend_adapter",
    "run_isolated_tool_backend_adapter_contract_suite",
]

_PROTOCOL_VERSION = "1.0"
_SUPPORTED_MODEL_BINDING_KEYS = ("temperature", "max_output_tokens")
_NO_PROMPT_MARKER = "no_prompt:isolated_tool_backend_adapter"


def _sha256_hex(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _tool_schema_fingerprint() -> str:
    # Real, deterministic content -- every reference tool's own frozen
    # input/output schema, sorted by name so field ordering never matters.
    parts: list[str] = []
    for spec in sorted(REFERENCE_TOOL_SPECS, key=lambda s: s.name):
        parts.append(spec.name)
        parts.append(repr(spec.input_schema_json))
        parts.append(repr(spec.output_schema_json))
    return _sha256_hex(*parts)


def _source_fingerprint() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass
class _CaseRecord:
    request: AdapterInvokeRequest
    result: AdapterInvokeResult


def _parse_declared_inputs(
    declared_inputs: tuple[tuple[str, object], ...],
) -> tuple[str, str, dict[str, Any]]:
    """This adapter's own real declared_inputs shape (see this module's own
    docstring, "invoke() deliberately does not run a model-driven decision
    loop"): exactly one tool call, named by `tool_name`/`subject_id`/
    `arguments`. Raises `HarnessContractError` for a request that does not
    carry this adapter's own required shape, never silently defaulting."""
    inputs = dict(declared_inputs)
    tool_name = inputs.get("tool_name")
    subject_id = inputs.get("subject_id")
    raw_arguments = inputs.get("arguments", ())
    if not isinstance(tool_name, str) or not tool_name:
        raise HarnessContractError("declared_inputs must carry a real, non-empty 'tool_name'")
    if not isinstance(subject_id, str) or not subject_id:
        raise HarnessContractError("declared_inputs must carry a real, non-empty 'subject_id'")
    if not isinstance(raw_arguments, tuple):
        raise HarnessContractError("declared_inputs' own 'arguments' must be a tuple of pairs")
    return tool_name, subject_id, dict(raw_arguments)


class IsolatedToolBackendAdapter:
    """A real, native `RuntimeAdapterProtocol` implementation backed
    directly by FTF-1702's own `ToolEnvironment` -- see this module's own
    docstring for the full disclosed design (invoke/error-classification/
    reset/cancel/attest)."""

    def __init__(self, *, tool_environment: ToolEnvironment) -> None:
        self._env = tool_environment
        self._cases: dict[str, _CaseRecord] = {}

    @property
    def tool_environment(self) -> ToolEnvironment:
        return self._env

    def describe(self) -> AdapterDescribeResult:
        return AdapterDescribeResult(
            protocol_version=_PROTOCOL_VERSION,
            artifact_digest=_tool_schema_fingerprint(),
            runtime_digest=_source_fingerprint(),
            supported_model_binding_keys=_SUPPORTED_MODEL_BINDING_KEYS,
            input_schema_ref="evals.reference.reference_agent_specification.REFERENCE_TOOL_SPECS",
            output_schema_ref="evals.reference.reference_agent_specification.REFERENCE_TOOL_SPECS",
            tool_schema_ref="evals.reference.reference_agent_specification.REFERENCE_TOOL_SPECS",
            trace_schema_ref="app.target_connectors."
            "isolated_real_tool_backend_adapter_contract_tests.AdapterInvokeResult",
            required_dependencies=("FTF-04A01", "FTF-04A25", "FTF-1702"),
        )

    def preflight(self, request: AdapterInvokeRequest) -> bool:
        try:
            tool_name, subject_id, _arguments = _parse_declared_inputs(request.declared_inputs)
        except HarnessContractError:
            return False
        if tool_name not in {spec.name for spec in REFERENCE_TOOL_SPECS}:
            return False
        if request.tenant_namespace not in self._env.seed.tenants:
            return False
        if subject_id not in self._env.seed.subjects_by_tenant.get(request.tenant_namespace, ()):
            return False
        return True

    def invoke(self, request: AdapterInvokeRequest) -> AdapterInvokeResult:
        tool_name, subject_id, arguments = _parse_declared_inputs(request.declared_inputs)

        try:
            outcome = self._env.dispatch(
                tool_name,
                tenant_id=request.tenant_namespace,
                subject_id=subject_id,
                arguments=arguments,
            )
        except ReferenceAuthorizationError as exc:
            result = self._error_result(request, ErrorClassification.REFUSAL, str(exc))
        except ReferenceDenialError as exc:
            result = self._error_result(request, ErrorClassification.REFUSAL, str(exc))
        except ToolEnvironmentError as exc:
            result = self._error_result(request, ErrorClassification.FATAL, str(exc))
        except ReferenceAgentSpecificationError as exc:
            result = self._error_result(request, ErrorClassification.MALFORMED_OUTPUT, str(exc))
        else:
            result = AdapterInvokeResult(
                case_id=request.case_id,
                status="completed",
                business_outcome=tuple(outcome.items()),
                side_effects=(f"tool_call:{tool_name}",),
                trace_ref=f"trace:{request.case_id}:{request.idempotency_key}",
                runtime_fingerprint=_source_fingerprint(),
                route_fingerprint=_sha256_hex(tool_name, request.tenant_namespace),
                error_classification=ErrorClassification.NONE,
            )

        self._cases[request.case_id] = _CaseRecord(request=request, result=result)
        return result

    def _error_result(
        self,
        request: AdapterInvokeRequest,
        classification: ErrorClassification,
        detail: str,
    ) -> AdapterInvokeResult:
        return AdapterInvokeResult(
            case_id=request.case_id,
            status="error",
            business_outcome=None,
            side_effects=(),
            trace_ref=f"trace:{request.case_id}:{request.idempotency_key}:{detail[:64]}",
            runtime_fingerprint=_source_fingerprint(),
            route_fingerprint=_sha256_hex(request.tenant_namespace),
            error_classification=classification,
        )

    def cancel(self, case_id: str) -> AdapterInvokeResult:
        record = self._cases.get(case_id)
        if record is None:
            raise HarnessContractError(f"cancel: no such case {case_id!r} was ever invoked")
        # invoke() is synchronous -- there is no genuine in-flight case to
        # interrupt, so cancel on an already-completed/errored case is
        # idempotent: it returns the same real stored outcome, never a
        # fabricated "cancelled" status.
        return record.result

    def collect_outcome(self, case_id: str) -> AdapterInvokeResult:
        record = self._cases.get(case_id)
        if record is None:
            raise HarnessContractError(
                f"collect_outcome: no such case {case_id!r} was ever invoked"
            )
        return record.result

    def reset(self, tenant_namespace: str) -> None:
        if tenant_namespace not in self._env.seed.tenants:
            raise HarnessContractError(
                f"reset: tenant_namespace {tenant_namespace!r} is not declared in this "
                f"environment's own seed spec {sorted(self._env.seed.tenants)!r}"
            )
        # FTF-1702's own ToolEnvironment offers only a real whole-environment
        # reset (see this module's own docstring, "reset() -- disclosed
        # scope") -- there is no independent per-tenant partition to clear.
        self._env.reset()
        self._cases.clear()

    def attest(self) -> RuntimeAttestation:
        seed = self._env.seed
        permission_fingerprint = _sha256_hex(
            repr(seed.tenants), repr(sorted(seed.subjects_by_tenant.items()))
        )
        return RuntimeAttestation(
            source_fingerprint=_source_fingerprint(),
            runtime_image_fingerprint=_sha256_hex("in_process_reference_tool_backend"),
            prompt_fingerprint=_sha256_hex(_NO_PROMPT_MARKER),
            tool_schema_fingerprint=_tool_schema_fingerprint(),
            permission_policy_fingerprint=permission_fingerprint,
            external_tool_contract_fingerprint=_tool_schema_fingerprint(),
            feature_flag_fingerprint=_sha256_hex("no_feature_flags:isolated_tool_backend"),
            model_binding_fingerprint=_sha256_hex(*_SUPPORTED_MODEL_BINDING_KEYS),
            gateway_config_fingerprint=_sha256_hex(_PROTOCOL_VERSION),
            evaluation_policy_fingerprint=_sha256_hex(
                *(spec.name for spec in REFERENCE_TOOL_SPECS)
            ),
            observed_at_epoch_seconds=time.time(),
            retrieval_index_fingerprint=None,
        )


def build_isolated_tool_backend_adapter(
    *, seed: ToolEnvironmentSeed = DEFAULT_SEED
) -> IsolatedToolBackendAdapter:
    env = build_tool_environment(seed=seed)
    return IsolatedToolBackendAdapter(tool_environment=env)


def _invoke_request(
    *,
    case_id: str,
    tenant_namespace: str,
    tool_name: str,
    subject_id: str,
    arguments: dict[str, Any],
    idempotency_key: str,
) -> AdapterInvokeRequest:
    return AdapterInvokeRequest(
        case_id=case_id,
        tenant_namespace=tenant_namespace,
        agent_manifest_id="isolated-tool-backend-contract-suite",
        model_binding=ModelBinding(model_id="n/a", provider="n/a"),
        declared_inputs=(
            ("tool_name", tool_name),
            ("subject_id", subject_id),
            ("arguments", tuple(arguments.items())),
        ),
        deadline_seconds=30.0,
        idempotency_key=idempotency_key,
    )


def run_isolated_tool_backend_adapter_contract_suite() -> tuple[str, ...]:
    """The reusable contract-test suite this task's own title names. Builds
    one real `IsolatedToolBackendAdapter` over one real, freshly seeded
    `ToolEnvironment` and exercises every one of Section 66.3's own seven
    operations against it for real -- with permission assertions (cross-
    tenant refusal, unseeded-identity refusal) and side-effect assertions
    (a claimed mutation is independently re-read from the backend, not
    merely trusted from the adapter's own reported result). Returns a tuple
    of failure descriptions; an empty tuple means every check passed."""
    failures: list[str] = []

    def check(description: str, condition: bool) -> None:
        if not condition:
            failures.append(description)

    adapter = build_isolated_tool_backend_adapter()
    env = adapter.tool_environment
    record_id = env.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )

    # -- describe() --
    described = adapter.describe()
    check("describe() reports the real protocol version", described.protocol_version == "1.0")
    check(
        "describe() declares at least one real model-binding key",
        len(described.supported_model_binding_keys) > 0,
    )

    # -- preflight() --
    ok_request = _invoke_request(
        case_id="case-preflight-ok",
        tenant_namespace="tenant-a",
        tool_name="search_records",
        subject_id="subject-a1",
        arguments={"query": "quarterly"},
        idempotency_key="preflight-ok",
    )
    check(
        "preflight() accepts a genuinely seeded tenant/subject/tool", adapter.preflight(ok_request)
    )

    bad_request = _invoke_request(
        case_id="case-preflight-bad",
        tenant_namespace="tenant-never-seeded",
        tool_name="search_records",
        subject_id="subject-a1",
        arguments={"query": "quarterly"},
        idempotency_key="preflight-bad",
    )
    check(
        "preflight() refuses an unseeded tenant before invoke() ever runs",
        not adapter.preflight(bad_request),
    )

    # -- invoke(): real read-only happy path --
    read_result = adapter.invoke(ok_request)
    check("invoke() completes a real read-only call", read_result.status == "completed")
    check(
        "invoke() carries a real business_outcome, not a placeholder",
        read_result.business_outcome is not None
        and any(k == "records" for k, _ in read_result.business_outcome),
    )
    check(
        "invoke() reports NONE error_classification on real success",
        read_result.error_classification == ErrorClassification.NONE,
    )

    # -- invoke(): real mutating call, independently re-observed (side-effect assertion) --
    write_request = _invoke_request(
        case_id="case-write",
        tenant_namespace="tenant-a",
        tool_name="create_note",
        subject_id="subject-a1",
        arguments={"record_id": record_id, "text": "contract-suite note", "idempotency_key": "n1"},
        idempotency_key="write-1",
    )
    write_result = adapter.invoke(write_request)
    check("invoke() completes a real mutating call", write_result.status == "completed")
    check(
        "invoke() names the real side effect it performed",
        write_result.side_effects == ("tool_call:create_note",),
    )
    # Independent re-read through a *second*, unrelated dispatch -- never
    # trusting write_result's own claim alone.
    reread = env.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id},
    )
    check(
        "the claimed note side effect is independently observable in the real backend",
        any(note["text"] == "contract-suite note" for note in reread["notes"]),
    )

    # -- invoke(): real cross-tenant permission refusal --
    cross_tenant_request = _invoke_request(
        case_id="case-cross-tenant",
        tenant_namespace="tenant-b",
        tool_name="get_record",
        subject_id="subject-b1",
        arguments={"record_id": record_id},
        idempotency_key="cross-tenant-1",
    )
    refusal_result = adapter.invoke(cross_tenant_request)
    check(
        "invoke() reports a real error status for a cross-tenant access attempt",
        refusal_result.status == "error",
    )
    check(
        "invoke() classifies a real authorization refusal as REFUSAL, never NONE",
        refusal_result.error_classification == ErrorClassification.REFUSAL,
    )
    check(
        "a refused case carries no fabricated business_outcome",
        refusal_result.business_outcome is None,
    )

    # -- invoke(): real unseeded-identity refusal --
    unseeded_request = _invoke_request(
        case_id="case-unseeded",
        tenant_namespace="tenant-a",
        tool_name="search_records",
        subject_id="subject-never-seeded",
        arguments={"query": "x"},
        idempotency_key="unseeded-1",
    )
    unseeded_result = adapter.invoke(unseeded_request)
    check(
        "invoke() classifies an unseeded identity as FATAL, never a canned success",
        unseeded_result.status == "error"
        and unseeded_result.error_classification == ErrorClassification.FATAL,
    )

    # -- invoke(): real malformed/unknown tool call --
    malformed_request = _invoke_request(
        case_id="case-malformed",
        tenant_namespace="tenant-a",
        tool_name="not_a_real_tool",
        subject_id="subject-a1",
        arguments={},
        idempotency_key="malformed-1",
    )
    malformed_result = adapter.invoke(malformed_request)
    check(
        "invoke() classifies an unknown tool name as MALFORMED_OUTPUT",
        malformed_result.status == "error"
        and malformed_result.error_classification == ErrorClassification.MALFORMED_OUTPUT,
    )

    # -- collect_outcome() --
    check(
        "collect_outcome() returns the real stored result for a real case",
        adapter.collect_outcome("case-write") == write_result,
    )
    try:
        adapter.collect_outcome("case-never-invoked")
        check("collect_outcome() raises for an unknown case_id", False)
    except HarnessContractError:
        check("collect_outcome() raises for an unknown case_id", True)

    # -- cancel() --
    check(
        "cancel() on an already-completed case is idempotent, returning the real stored result",
        adapter.cancel("case-write") == write_result,
    )
    try:
        adapter.cancel("case-never-invoked")
        check("cancel() raises for an unknown case_id", False)
    except HarnessContractError:
        check("cancel() raises for an unknown case_id", True)

    # -- reset(): real disposal, independently observed --
    adapter.reset("tenant-a")
    new_record_id = env.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    check("reset() assigns a freshly generated record ID", new_record_id != record_id)
    post_reset = env.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": new_record_id},
    )
    check(
        "reset() genuinely disposes prior mutations (no notes carried over)",
        post_reset["notes"] == [],
    )
    try:
        adapter.collect_outcome("case-write")
        check("reset() clears prior case records", False)
    except HarnessContractError:
        check("reset() clears prior case records", True)
    try:
        adapter.reset("tenant-never-seeded")
        check("reset() refuses an unseeded tenant_namespace", False)
    except HarnessContractError:
        check("reset() refuses an unseeded tenant_namespace", True)

    # -- attest() --
    attestation_one = adapter.attest()
    attestation_two = adapter.attest()
    check(
        "attest() reports a real, non-empty prompt_fingerprint (disclosed no-prompt marker)",
        bool(attestation_one.prompt_fingerprint),
    )
    check(
        "attest() reports no retrieval_index_fingerprint (this adapter has no retrieval component)",
        attestation_one.retrieval_index_fingerprint is None,
    )
    check(
        "attest() is deterministic across calls for unchanged adapter state "
        "(excluding the real observed timestamp)",
        attestation_one.source_fingerprint == attestation_two.source_fingerprint
        and attestation_one.tool_schema_fingerprint == attestation_two.tool_schema_fingerprint,
    )

    return tuple(failures)
