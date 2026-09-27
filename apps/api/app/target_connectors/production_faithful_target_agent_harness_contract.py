"""Production-faithful target-agent harness contract (FTF-04A25, Section
7A.7-7A.8/66.1/66.3-66.5/77/81).

This task's own acceptance clause is narrow and literal: "Define actual
container/HTTP target runtime protocol and model-binding override/parity
requirements." Its own `database_changes` clause ("No independent schema
change. Consume frozen contract.") and empty `shared_lock_required` mean
this is a pure contract/protocol module -- typed request/result shapes and
validation functions a later runtime-adapter implementation task
(Section 7A.8's own harness, not yet a separate claimed task at the time
this module was written) will import and satisfy, not a database table, not
an HTTP route, and not a concrete adapter implementation itself. Declaring
a concrete "default" adapter implementation here -- one that, say, silently
falls back to calling a model directly -- would be exactly this task's own
negative_acceptance violation ("Do not substitute a generic Factory chat
loop for product orchestration"); this module deliberately defines only the
contract (a `typing.Protocol`, not an ABC with a default `invoke`), never a
concrete class satisfying it.

Path refined from the task packet's own generic
`packages/target-connectors/production_faithful_target_agent_harness_
contract.py` suggestion to this repo's own real
`apps/api/app/target_connectors/*.py` layout, the same refinement every
prior 04A-family task (FTF-04A19/FTF-04A20/...) already discloses.

## Runtime entry protocol (Section 66.1)

Section 66.1's own literal sentence: "Supported runtime entry mechanisms
are a signed organization-approved container or an existing HTTP agent
endpoint with a registered adapter that supports model-binding injection
and trace correlation." -- a closed two-value set
(`RuntimeEntryKind.SIGNED_CONTAINER` / `RuntimeEntryKind.HTTP_ENDPOINT`),
never a third "other" member; `validate_runtime_entry_kind` rejects any
other caller-supplied string outright. Section 66.1's own closing sentence
("Unknown frameworks may be manually mapped for source discovery but are
not production-qualified without a real runtime adapter/parity proof") is
this task's own negative_acceptance ("unsupported binding is blocked") read
onto the entry-kind boundary specifically.

## Versioned adapter operations (Section 66.3)

`RuntimeAdapterProtocol` declares exactly Section 66.3's own seven named
operations (`describe`/`preflight`/`invoke`/`cancel`/`collect_outcome`/
`reset`/`attest`) -- "Do not prescribe endpoint names the target does not
implement" -- these names address a Factory-side adapter module, never the
target application's own HTTP surface; a registered adapter (Section
66.1) performs the real mapping from these fixed names onto whatever the
target agent actually exposes. `AdapterInvokeRequest`/`AdapterInvokeResult`
carry exactly Section 66.3's own listed request/result fields.
`AdapterInvokeResult.__post_init__` enforces Section 66.3's own literal
"A plain text answer without a required business-state result is
incomplete evidence": a `status="completed"` result with no
`business_outcome` is rejected at construction, never silently accepted.

## Model-binding override (Section 66.4, first half)

Section 66.4: "Injection modifies only the selected agent's model route; it
cannot replace the whole workflow or disable a difficult tool." `ModelBinding`
is a structural guarantee of this, not merely a runtime check -- it carries
no prompt/tool/permission/workflow field at all, so there is no field to
smuggle a workflow override into. `validate_model_binding_override` is this
task's own negative_acceptance ("unsupported binding is blocked") applied to
the binding boundary: a `route_params` key absent from the adapter's own
`describe()`-declared `supported_model_binding_keys` is rejected outright,
never silently ignored or coerced into a best-effort match.

## Parity requirements (Section 66.4, second half)

Section 66.4: "A separately reconstructed adapter is allowed only when its
supported framework/contract is declared and parity tests compare original
and adapted process using the same model over success, timeout, refusal,
malformed output, approval and multi-step cases. Matching happy-path text
alone is not parity. Otherwise show INTEGRATION_REQUIRED, permit generic
model analysis, and block target-product qualification."
`ParityCaseCategory` is this closed six-value set read literally.
`evaluate_target_runtime_qualification` is this rule as a pure function: a
native (non-reconstructed) adapter -- the target's own actual process --
is qualified by construction, since there is no separately reconstructed
process to compare parity against; a reconstructed adapter is qualified
only once its own `tested_parity_categories` is a superset of the full
closed set (a strict subset -- including `{SUCCESS}` alone, i.e. "matching
happy-path text alone" -- always returns `INTEGRATION_REQUIRED`, never
`QUALIFIED`).

## Attestation (Section 66.3's own `attest` operation / Section 66.5)

Section 66.3 names `attest` as "return observed runtime/config/retrieval/
tool/model fingerprints"; Section 66.5 names the full compatibility tuple.
`RuntimeAttestation` carries one fingerprint field per Section 66.5 tuple
member that this task's own dependency chain has a real source for today
(`retrieval_index_fingerprint` is the one tuple member that is legitimately
absent for a target agent with no retrieval component at all, so it alone
is `str | None`; every other fingerprint is required non-empty). This
module does not implement drift *comparison* between two attestations over
time (Section 7A.9's own "compare files/symbols ... classify drift" --
FTF-04A27's separate, later, dependency-gated job) -- only the single-point
observation shape a drift detector would consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

__all__ = [
    "AdapterDescribeResult",
    "AdapterInvokeRequest",
    "AdapterInvokeResult",
    "AdapterOperation",
    "ErrorClassification",
    "HarnessContractError",
    "ModelBinding",
    "ParityCaseCategory",
    "QualificationStatus",
    "REQUIRED_PARITY_CATEGORIES",
    "ReconstructedAdapterDeclaration",
    "RuntimeAdapterProtocol",
    "RuntimeAttestation",
    "RuntimeEntryKind",
    "evaluate_target_runtime_qualification",
    "validate_model_binding_override",
    "validate_runtime_entry_kind",
]


class HarnessContractError(Exception):
    """A caller-supplied value violates this module's own closed contract
    shape. Never raised for a real target-runtime failure -- that is
    reported through a typed `AdapterInvokeResult`/`error_classification`,
    not an exception (Section 66.3's own result shape carries failure as
    data, not as control flow a caller must catch)."""


class RuntimeEntryKind(str, Enum):
    """Section 66.1's own closed pair of supported runtime entry
    mechanisms."""

    SIGNED_CONTAINER = "signed_container"
    HTTP_ENDPOINT = "http_endpoint"


_VALID_RUNTIME_ENTRY_KINDS = frozenset(member.value for member in RuntimeEntryKind)


def validate_runtime_entry_kind(value: str) -> RuntimeEntryKind:
    """This task's own negative_acceptance ("unsupported binding is
    blocked") applied to the runtime-entry boundary: any value outside
    Section 66.1's own closed pair is rejected outright, never mapped to a
    best-guess member."""
    if value not in _VALID_RUNTIME_ENTRY_KINDS:
        raise HarnessContractError(
            f"unsupported runtime entry kind {value!r}; Section 66.1 supports only "
            f"{sorted(_VALID_RUNTIME_ENTRY_KINDS)!r} -- unsupported binding is blocked"
        )
    return RuntimeEntryKind(value)


class AdapterOperation(str, Enum):
    """Section 66.3's own versioned adapter operations, exactly named --
    a closed set an adapter cannot extend with an eighth operation."""

    DESCRIBE = "describe"
    PREFLIGHT = "preflight"
    INVOKE = "invoke"
    CANCEL = "cancel"
    COLLECT_OUTCOME = "collect_outcome"
    RESET = "reset"
    ATTEST = "attest"


class ParityCaseCategory(str, Enum):
    """Section 66.4's own required parity comparison categories, read
    literally and exhaustively -- "success, timeout, refusal, malformed
    output, approval and multi-step cases"."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    MALFORMED_OUTPUT = "malformed_output"
    APPROVAL = "approval"
    MULTI_STEP = "multi_step"


REQUIRED_PARITY_CATEGORIES: frozenset[ParityCaseCategory] = frozenset(ParityCaseCategory)


class QualificationStatus(str, Enum):
    """Section 66.4's own two possible outcomes -- there is no partial or
    provisional third value; incomplete parity evidence is always
    `INTEGRATION_REQUIRED`, never a weaker form of `QUALIFIED`."""

    QUALIFIED = "qualified"
    INTEGRATION_REQUIRED = "integration_required"


class ErrorClassification(str, Enum):
    """Section 66.3's own "error classification" result field -- a closed
    set an adapter result must pick from, never a free-text string a
    caller would have to pattern-match."""

    NONE = "none"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    MALFORMED_OUTPUT = "malformed_output"
    TRANSIENT = "transient"
    FATAL = "fatal"


_INVOKE_RESULT_STATUSES = ("completed", "cancelled", "error")


def _require_non_empty(field_name: str, value: str) -> None:
    if not value:
        raise HarnessContractError(f"{field_name!r} must be a real, non-empty value")


@dataclass(frozen=True)
class AdapterDescribeResult:
    """Section 66.3's own `describe` result: "protocol version, artifact/
    runtime digest, supported model-binding keys, input/output/tool/trace
    schemas and required dependencies"."""

    protocol_version: str
    artifact_digest: str
    runtime_digest: str
    supported_model_binding_keys: tuple[str, ...]
    input_schema_ref: str
    output_schema_ref: str
    tool_schema_ref: str
    trace_schema_ref: str
    required_dependencies: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("protocol_version", self.protocol_version),
            ("artifact_digest", self.artifact_digest),
            ("runtime_digest", self.runtime_digest),
            ("input_schema_ref", self.input_schema_ref),
            ("output_schema_ref", self.output_schema_ref),
            ("tool_schema_ref", self.tool_schema_ref),
            ("trace_schema_ref", self.trace_schema_ref),
        ):
            _require_non_empty(field_name, value)
        if not self.supported_model_binding_keys:
            raise HarnessContractError(
                "supported_model_binding_keys must declare at least one real key -- "
                "an adapter with no declared binding surface cannot accept model injection"
            )


@dataclass(frozen=True)
class ModelBinding:
    """Section 66.4: "Injection modifies only the selected agent's model
    route; it cannot replace the whole workflow or disable a difficult
    tool." This dataclass carries no prompt/tool/permission/workflow field
    at all -- a structural guarantee, not merely a runtime check."""

    model_id: str
    provider: str
    route_params: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("model_id", self.model_id)
        _require_non_empty("provider", self.provider)


def validate_model_binding_override(
    describe_result: AdapterDescribeResult, binding: ModelBinding
) -> ModelBinding:
    """This task's own negative_acceptance ("unsupported binding is
    blocked") applied to the model-binding boundary: a `route_params` key
    absent from the adapter's own declared `supported_model_binding_keys`
    is rejected outright, never silently ignored."""
    supported = set(describe_result.supported_model_binding_keys)
    for key, _ in binding.route_params:
        if key not in supported:
            raise HarnessContractError(
                f"model binding key {key!r} is not in this adapter's own declared "
                f"supported_model_binding_keys {sorted(supported)!r} -- unsupported "
                f"binding is blocked"
            )
    return binding


@dataclass(frozen=True)
class AdapterInvokeRequest:
    """Section 66.3: "Request carries case_id, tenant namespace, selected
    agent ID, immutable model binding, declared inputs, deadline and
    idempotency key." `agent_manifest_id` is the "selected agent ID" --
    the locked `AgentManifest` (FTF-04A19) that supplies the real prompt/
    tool/binding contract Section 7A.8 lists."""

    case_id: str
    tenant_namespace: str
    agent_manifest_id: str
    model_binding: ModelBinding
    declared_inputs: tuple[tuple[str, object], ...]
    deadline_seconds: float
    idempotency_key: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("case_id", self.case_id),
            ("tenant_namespace", self.tenant_namespace),
            ("agent_manifest_id", self.agent_manifest_id),
            ("idempotency_key", self.idempotency_key),
        ):
            _require_non_empty(field_name, value)
        if self.deadline_seconds <= 0:
            raise HarnessContractError("deadline_seconds must be a real, positive value")


@dataclass(frozen=True)
class AdapterInvokeResult:
    """Section 66.3: "Result carries status, real business outcome,
    side-effect list, trace ref, runtime/route fingerprints and error
    classification. A plain text answer without a required business-state
    result is incomplete evidence." -- enforced below at construction, not
    left to a caller to remember to check."""

    case_id: str
    status: str
    business_outcome: tuple[tuple[str, object], ...] | None
    side_effects: tuple[str, ...]
    trace_ref: str
    runtime_fingerprint: str
    route_fingerprint: str
    error_classification: ErrorClassification

    def __post_init__(self) -> None:
        _require_non_empty("case_id", self.case_id)
        _require_non_empty("trace_ref", self.trace_ref)
        if self.status not in _INVOKE_RESULT_STATUSES:
            raise HarnessContractError(
                f"status {self.status!r} is not one of {_INVOKE_RESULT_STATUSES!r}"
            )
        if self.status == "completed":
            if self.business_outcome is None:
                raise HarnessContractError(
                    "Section 66.3: a plain text answer without a required "
                    "business-state result is incomplete evidence -- a 'completed' "
                    "result must carry a real business_outcome"
                )
            if self.error_classification != ErrorClassification.NONE:
                raise HarnessContractError(
                    "a 'completed' result cannot also carry a non-NONE error_classification"
                )
        if self.status == "error" and self.error_classification == ErrorClassification.NONE:
            raise HarnessContractError(
                "an 'error' result must carry a real, non-NONE error_classification"
            )


@dataclass(frozen=True)
class RuntimeAttestation:
    """Section 66.3's own `attest` result ("observed runtime/config/
    retrieval/tool/model fingerprints") read against Section 66.5's own
    full compatibility tuple. Every fingerprint is a real observed value
    (a hash/digest/version string), never a raw secret or full config
    dump. `retrieval_index_fingerprint` is the one tuple member legitimately
    absent for a target agent with no retrieval component, so it alone is
    optional."""

    source_fingerprint: str
    runtime_image_fingerprint: str
    prompt_fingerprint: str
    tool_schema_fingerprint: str
    permission_policy_fingerprint: str
    external_tool_contract_fingerprint: str
    feature_flag_fingerprint: str
    model_binding_fingerprint: str
    gateway_config_fingerprint: str
    evaluation_policy_fingerprint: str
    observed_at_epoch_seconds: float
    retrieval_index_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_fingerprint", self.source_fingerprint),
            ("runtime_image_fingerprint", self.runtime_image_fingerprint),
            ("prompt_fingerprint", self.prompt_fingerprint),
            ("tool_schema_fingerprint", self.tool_schema_fingerprint),
            ("permission_policy_fingerprint", self.permission_policy_fingerprint),
            ("external_tool_contract_fingerprint", self.external_tool_contract_fingerprint),
            ("feature_flag_fingerprint", self.feature_flag_fingerprint),
            ("model_binding_fingerprint", self.model_binding_fingerprint),
            ("gateway_config_fingerprint", self.gateway_config_fingerprint),
            ("evaluation_policy_fingerprint", self.evaluation_policy_fingerprint),
        ):
            _require_non_empty(field_name, value)
        if self.observed_at_epoch_seconds <= 0:
            raise HarnessContractError("observed_at_epoch_seconds must be a real, positive value")


@runtime_checkable
class RuntimeAdapterProtocol(Protocol):
    """Section 66.3's own versioned adapter operations, exactly named --
    "Do not prescribe endpoint names the target does not implement": these
    method names address a Factory-side adapter module, never the target
    application's own HTTP surface. `typing.Protocol` (not an ABC with a
    default method body) is deliberate -- there is no concrete
    implementation in this module for any operation, so a "default" adapter
    cannot accidentally satisfy this contract by inheritance alone."""

    def describe(self) -> AdapterDescribeResult: ...

    def preflight(self, request: AdapterInvokeRequest) -> bool: ...

    def invoke(self, request: AdapterInvokeRequest) -> AdapterInvokeResult: ...

    def cancel(self, case_id: str) -> AdapterInvokeResult: ...

    def collect_outcome(self, case_id: str) -> AdapterInvokeResult: ...

    def reset(self, tenant_namespace: str) -> None: ...

    def attest(self) -> RuntimeAttestation: ...


@dataclass(frozen=True)
class ReconstructedAdapterDeclaration:
    """Section 66.4: "A separately reconstructed adapter is allowed only
    when its supported framework/contract is declared and parity tests
    compare original and adapted process ... over success, timeout,
    refusal, malformed output, approval and multi-step cases."."""

    declared_framework: str
    tested_parity_categories: frozenset[ParityCaseCategory]

    def __post_init__(self) -> None:
        _require_non_empty("declared_framework", self.declared_framework)


def evaluate_target_runtime_qualification(
    entry_kind: RuntimeEntryKind,
    *,
    is_reconstructed_adapter: bool,
    reconstructed_declaration: ReconstructedAdapterDeclaration | None = None,
) -> QualificationStatus:
    """Section 66.4's own qualification rule, read literally:

    - A native adapter (the target's own actual container/HTTP process,
      `is_reconstructed_adapter=False`) is qualified by construction -- it
      *is* the production process, so there is no separate process to
      compare parity against.
    - A *separately reconstructed* adapter is qualified only once its own
      declared framework is real and its own `tested_parity_categories` is
      a superset of `REQUIRED_PARITY_CATEGORIES` -- "matching happy-path
      text alone is not parity" means `{SUCCESS}` alone, or any other
      strict subset, is never enough.
    - Anything short of that returns `INTEGRATION_REQUIRED`, never
      `QUALIFIED` -- Section 66.4's own "Otherwise show
      INTEGRATION_REQUIRED ... and block target-product qualification."

    `entry_kind` is accepted, not re-validated here -- a caller passes it
    through `validate_runtime_entry_kind` first (this function's own
    negative_acceptance duty is the parity-coverage check, not re-doing
    entry-kind validation)."""
    del entry_kind  # part of the documented contract surface; not branched on today
    if not is_reconstructed_adapter:
        return QualificationStatus.QUALIFIED

    if reconstructed_declaration is None:
        return QualificationStatus.INTEGRATION_REQUIRED

    if reconstructed_declaration.tested_parity_categories >= REQUIRED_PARITY_CATEGORIES:
        return QualificationStatus.QUALIFIED

    return QualificationStatus.INTEGRATION_REQUIRED
