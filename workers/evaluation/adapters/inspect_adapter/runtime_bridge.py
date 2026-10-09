"""FTF-R004-P3 -- Inspect runtime bridge: run a locked suite through the target runtime adapter.

Section 66.3-66.4, 77.3, 77.5, 78.2 and Q16/Q35. Acceptance: "Use qualified Agent Bridge or
actual-runtime adapter; execute original orchestration and real isolated dependencies with
identity pinning." Negative: "A reconstructed generic agent ... cannot pass."

`run_suite_through_runtime` builds a pinned-Inspect `Task` whose only solver,
`native_runtime_adapter`, calls a registered native `RuntimeAdapterProtocol` adapter. Each case
goes through `preflight`, then `invoke` (immutable `ModelBinding`, deadline, idempotency key),
then `collect_outcome`. Nothing else decides a case: no `react()` / `basic_agent()` / generic
agent, no model call by Inspect (model-less mode, `none/none`).

Refusals (`BridgeError.code`, stable, content-free):

* `integration_required` -- `evaluate_target_runtime_qualification` did not return QUALIFIED
  (a reconstructed adapter without every FTF-04A25 parity category);
* `unsupported_entry_kind` / `unsupported_model_binding_key` -- the FTF-04A25 closed contracts;
* `runtime_identity_changed` -- `attest()` and `describe()` are pinned before the run and
  re-checked before every case and after the run; any changed fingerprint (observed time aside)
  is a refusal;
* `generic_agent_solver_forbidden` -- the Inspect log's plan is not exactly the one native
  solver, or a model was called;
* `telemetry_configured` -- `INSPECT_TELEMETRY` / `INSPECT_REQUIRED_HOOKS` is set;
* `outbound_call_blocked` -- the run opened (or tried to open) a network connection; the run
  executes under a socket guard, so it passes only under network denial;
* `provider_boundary_denied` / `generation_config_parity_blocked` -- the `BridgeHook`, which
  applies FTF-R004-P2 screening and attempt capture and the FTF-R004-P1 parity comparison to the
  model traffic of a bridged route.

The log directory is a private (mode 0700) temp workspace created per run; no remote log or eval
destination is ever passed. `BridgeRunResult.log_sha256` is the sha256 of the log file as written
(FTF-R004-P4 re-checks it).

`observe_inspect_generation_forwarding` runs the pinned release's own request->config conversion
(`generate_config_from_openai_completions`, then `clear_generation_params` unless
`forward_generation_config`) so the release's behaviour is captured and compared with P1 rather
than assumed.

`mockllm` is never imported here. Real model routes through the org LiteLLM gateway (FTF-H005
route pinning) are BLOCKED_EXTERNAL; a customer target beyond the reference backend is FTF-H008.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.agent._bridge.completions import generate_config_from_openai_completions
from inspect_ai.agent._bridge.util import clear_generation_params
from inspect_ai.dataset import MemoryDataset
from inspect_ai.log import read_eval_log
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
from inspect_ai.solver import Generate, TaskState, solver

from app.target_connectors.production_faithful_target_agent_harness_contract import (
    AdapterDescribeResult,
    AdapterInvokeRequest,
    AdapterInvokeResult,
    HarnessContractError,
    ModelBinding,
    QualificationStatus,
    ReconstructedAdapterDeclaration,
    RuntimeAdapterProtocol,
    RuntimeAttestation,
    evaluate_target_runtime_qualification,
    validate_model_binding_override,
    validate_runtime_entry_kind,
)
from workers.evaluation.adapters.inspect_adapter.case_adapter import BridgeCase, SuiteCaseSet
from workers.evaluation.adapters.inspect_adapter.generation_config_parity import (
    DeclaredGenerationChange,
    GenerationParityResult,
    capture_effective_generation_settings,
    compare_generation_parity,
)
from workers.evaluation.adapters.inspect_adapter.provider_boundary import (
    AttemptEvidence,
    FilterOutcome,
    ProviderBoundaryError,
    ScreeningPolicy,
    apply_approval_filter,
    capture_response_attempts,
    screen_outbound_model_request,
)

__all__ = [
    "NATIVE_SOLVER_NAME",
    "BridgeError",
    "BridgeHook",
    "BridgeRunResult",
    "CaseRunRecord",
    "observe_inspect_generation_forwarding",
    "run_suite_through_runtime",
]

NATIVE_SOLVER_NAME = "native_runtime_adapter"
_MODEL_LESS = "none/none"
_SCORER_NAME = "adapter_reported_status"

CODE_INTEGRATION_REQUIRED = "integration_required"
CODE_UNSUPPORTED_ENTRY_KIND = "unsupported_entry_kind"
CODE_UNSUPPORTED_BINDING_KEY = "unsupported_model_binding_key"
CODE_IDENTITY_CHANGED = "runtime_identity_changed"
CODE_GENERIC_AGENT = "generic_agent_solver_forbidden"
CODE_TELEMETRY = "telemetry_configured"
CODE_OUTBOUND = "outbound_call_blocked"
CODE_PROVIDER_DENIED = "provider_boundary_denied"
CODE_PARITY_BLOCKED = "generation_config_parity_blocked"
CODE_RUN_FAILED = "inspect_run_failed"
CODE_INVALID_REQUEST = "bridge_invalid_request"
CODE_ADAPTER_MISMATCH = "adapter_case_mismatch"

_TELEMETRY_ENV = ("INSPECT_TELEMETRY", "INSPECT_REQUIRED_HOOKS")

# Inspect's `GenerateConfig` field -> the OpenAI request key the same setting arrives under.
_CONFIG_TO_REQUEST_KEY = {
    "max_tokens": "max_tokens",
    "top_p": "top_p",
    "temperature": "temperature",
    "stop_seqs": "stop",
    "frequency_penalty": "frequency_penalty",
    "presence_penalty": "presence_penalty",
    "seed": "seed",
    "num_choices": "n",
    "logprobs": "logprobs",
    "top_logprobs": "top_logprobs",
    "logit_bias": "logit_bias",
    "parallel_tool_calls": "parallel_tool_calls",
    "reasoning_effort": "reasoning_effort",
}


class BridgeError(Exception):
    """The bridge refused or failed a run. `code` is stable; the message holds no case,
    prompt, URL or tool content."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# --------------------------------------------------------------------------- model-traffic hook


def observe_inspect_generation_forwarding(
    request_body: Mapping[str, object], *, forward_generation_config: bool
) -> Mapping[str, object]:
    """The generation settings the pinned Inspect release keeps from one OpenAI-compatible
    request: its own request->config conversion, then its own parameter clearing unless
    `forward_generation_config` is set. Returned as FTF-R004-P1 captured settings."""
    if not isinstance(request_body, Mapping):
        raise BridgeError(CODE_INVALID_REQUEST, "request body must be a mapping")
    body = dict(request_body)
    config = generate_config_from_openai_completions(body)
    if not forward_generation_config:
        clear_generation_params(config)
    observed: dict[str, object] = {}
    for config_field, request_key in _CONFIG_TO_REQUEST_KEY.items():
        value = getattr(config, config_field, None)
        if value is None:
            continue
        key = request_key
        if config_field == "max_tokens" and "max_completion_tokens" in body:
            key = "max_completion_tokens"
        observed[key] = value
    return capture_effective_generation_settings(observed)


@dataclass
class BridgeHook:
    """Applies FTF-R004-P2 and FTF-R004-P1 to the model traffic of a bridged route. It is a
    required argument of `run_suite_through_runtime`, so a run cannot be started without it,
    and an adapter that talks to a model receives it through `bind_bridge_hook`."""

    policy: ScreeningPolicy
    approved_settings: Mapping[str, object]
    declared_change: DeclaredGenerationChange | None = None
    requests_screened: int = 0
    requests_denied: int = 0
    attempts_captured: int = 0
    responses_filtered: int = 0
    parity_checks: int = 0
    _evidence: list[AttemptEvidence] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def screen_request(self, body: object) -> None:
        """Default-deny screening of one outbound model request (P2). Raises
        `provider_boundary_denied` with the denial codes and paths, never their content."""
        try:
            decision = screen_outbound_model_request(body, self.policy)
        except ProviderBoundaryError as exc:
            with self._lock:
                self.requests_denied += 1
            raise BridgeError(CODE_PROVIDER_DENIED, exc.code) from exc
        with self._lock:
            self.requests_screened += 1
            if not decision.allowed:
                self.requests_denied += 1
        if not decision.allowed:
            detail = ",".join(f"{d.code}@{d.path}" for d in decision.denials)
            raise BridgeError(CODE_PROVIDER_DENIED, detail)

    def capture_and_filter_response(self, response_body: object) -> FilterOutcome:
        """Records every attempted call of a response BEFORE approval filtering (P2), then
        filters against that exact evidence."""
        try:
            evidence = capture_response_attempts(response_body)
            outcome = apply_approval_filter(
                response_body,
                evidence,
                approved_tool_names=self.policy.approved_tool_names,
            )
        except ProviderBoundaryError as exc:
            raise BridgeError(CODE_PROVIDER_DENIED, exc.code) from exc
        with self._lock:
            self._evidence.append(outcome.evidence)
            self.attempts_captured += len(outcome.evidence.attempts)
            self.responses_filtered += 1
        return outcome

    def check_generation_parity(
        self,
        request_body: Mapping[str, object],
        *,
        forward_generation_config: bool,
    ) -> GenerationParityResult:
        """Captures what the pinned Inspect release does to the request's generation settings
        and compares it with the approved baseline (P1). A dropped, changed or inherited
        setting that is not excused by a declared change blocks."""
        before = capture_effective_generation_settings(request_body)
        after = observe_inspect_generation_forwarding(
            request_body, forward_generation_config=forward_generation_config
        )
        result = compare_generation_parity(
            self.approved_settings, before, after, self.declared_change
        )
        with self._lock:
            self.parity_checks += 1
        if result.verdict in ("blocked", "insufficient_evidence"):
            raise BridgeError(CODE_PARITY_BLOCKED, ",".join(result.blocking_keys) or result.verdict)
        return result

    def attempt_evidence(self) -> tuple[AttemptEvidence, ...]:
        with self._lock:
            return tuple(self._evidence)


# --------------------------------------------------------------------------- network denial


class _NetworkGuard:
    """Blocks every non-AF_UNIX connect and every name lookup while a run executes, and
    counts each attempt. A run that needs the network cannot pass."""

    def __init__(self) -> None:
        self.attempts = 0
        self._lock = threading.Lock()
        self._saved: list[tuple[object, str, object]] = []

    def _blocked(self, *_args: object, **_kwargs: object) -> Any:
        with self._lock:
            self.attempts += 1
        raise BridgeError(CODE_OUTBOUND)

    def _guarded_connect(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def connect(sock: socket.socket, address: object, *args: object, **kwargs: object) -> Any:
            if sock.family == getattr(socket, "AF_UNIX", None):
                return original(sock, address, *args, **kwargs)
            return self._blocked()

        return connect

    def __enter__(self) -> _NetworkGuard:
        for name in ("connect", "connect_ex"):
            original = getattr(socket.socket, name)
            self._saved.append((socket.socket, name, original))
            setattr(socket.socket, name, self._guarded_connect(original))
        for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
            self._saved.append((socket, name, getattr(socket, name)))
            setattr(socket, name, self._blocked)
        return self

    def __exit__(self, *_exc: object) -> None:
        for owner, name, original in reversed(self._saved):
            setattr(owner, name, original)
        self._saved.clear()


# --------------------------------------------------------------------------- run state / result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _attestation_view(attestation: RuntimeAttestation) -> dict[str, object]:
    view = {f.name: getattr(attestation, f.name) for f in fields(attestation)}
    del view["observed_at_epoch_seconds"]
    return view


def _describe_view(describe: AdapterDescribeResult) -> dict[str, object]:
    return {f.name: getattr(describe, f.name) for f in fields(describe)}


@dataclass(frozen=True)
class CaseRunRecord:
    """Content-free record of one case: ids, statuses and digests only."""

    case_key: str
    case_id: str
    status: str
    error_classification: str
    trace_ref: str
    runtime_fingerprint: str
    route_fingerprint: str
    business_outcome_sha256: str | None
    side_effect_count: int
    collected_matches_invoke: bool
    deadline_exceeded: bool
    adapter_exception_type: str | None


@dataclass(frozen=True)
class BridgeRunResult:
    run_id: str
    suite_id: str
    agent_manifest_id: str
    pinned_attestation: RuntimeAttestation
    pinned_describe: AdapterDescribeResult
    cases: tuple[CaseRunRecord, ...]
    log_dir: str
    log_path: str
    log_sha256: str
    inspect_version: str
    network_attempts: int
    hook_requests_screened: int
    hook_attempts_captured: int


class _RunState:
    def __init__(
        self,
        *,
        adapter: RuntimeAdapterProtocol,
        cases: dict[str, BridgeCase],
        run_id: str,
        tenant_namespace: str,
        agent_manifest_id: str,
        binding: ModelBinding,
        deadline_seconds: float,
        pinned_attestation: RuntimeAttestation,
        pinned_describe: AdapterDescribeResult,
    ) -> None:
        self.adapter = adapter
        self.cases = cases
        self.run_id = run_id
        self.tenant_namespace = tenant_namespace
        self.agent_manifest_id = agent_manifest_id
        self.binding = binding
        self.deadline_seconds = deadline_seconds
        self.pinned_attestation = pinned_attestation
        self.pinned_describe = pinned_describe
        self.records: dict[str, CaseRunRecord] = {}
        self.first_error: BridgeError | None = None

    def fail(self, error: BridgeError) -> None:
        if self.first_error is None:
            self.first_error = error

    def check_identity(self) -> None:
        if _describe_view(self.adapter.describe()) != _describe_view(self.pinned_describe):
            raise BridgeError(CODE_IDENTITY_CHANGED, "describe")
        changed = [
            name
            for name, value in _attestation_view(self.adapter.attest()).items()
            if value != _attestation_view(self.pinned_attestation)[name]
        ]
        if changed:
            raise BridgeError(CODE_IDENTITY_CHANGED, ",".join(sorted(changed)))


def _outcome_digest(result: AdapterInvokeResult) -> str | None:
    if result.business_outcome is None:
        return None
    return _sha256(json.dumps(result.business_outcome, sort_keys=True, default=repr).encode())


def _run_case(state: _RunState, case: BridgeCase) -> CaseRunRecord:
    request = AdapterInvokeRequest(
        case_id=case.case_key,
        tenant_namespace=state.tenant_namespace,
        agent_manifest_id=state.agent_manifest_id,
        model_binding=state.binding,
        declared_inputs=case.declared_inputs,
        deadline_seconds=state.deadline_seconds,
        idempotency_key=_sha256(f"{state.run_id}\0{case.case_key}".encode()),
    )
    if not state.adapter.preflight(request):
        return CaseRunRecord(
            case_key=case.case_key,
            case_id=case.case_id,
            status="preflight_refused",
            error_classification="none",
            trace_ref="",
            runtime_fingerprint="",
            route_fingerprint="",
            business_outcome_sha256=None,
            side_effect_count=0,
            collected_matches_invoke=False,
            deadline_exceeded=False,
            adapter_exception_type=None,
        )
    started = time.monotonic()
    try:
        invoked = state.adapter.invoke(request)
        collected = state.adapter.collect_outcome(case.case_key)
    except Exception as exc:  # noqa: BLE001 -- recorded as a typed, content-free failure row
        return CaseRunRecord(
            case_key=case.case_key,
            case_id=case.case_id,
            status="adapter_exception",
            error_classification="fatal",
            trace_ref="",
            runtime_fingerprint="",
            route_fingerprint="",
            business_outcome_sha256=None,
            side_effect_count=0,
            collected_matches_invoke=False,
            deadline_exceeded=False,
            adapter_exception_type=type(exc).__name__,
        )
    elapsed = time.monotonic() - started
    if invoked.case_id != case.case_key or collected.case_id != case.case_key:
        raise BridgeError(CODE_ADAPTER_MISMATCH)
    return CaseRunRecord(
        case_key=case.case_key,
        case_id=case.case_id,
        status=invoked.status,
        error_classification=invoked.error_classification.value,
        trace_ref=invoked.trace_ref,
        runtime_fingerprint=invoked.runtime_fingerprint,
        route_fingerprint=invoked.route_fingerprint,
        business_outcome_sha256=_outcome_digest(collected),
        side_effect_count=len(invoked.side_effects),
        collected_matches_invoke=invoked == collected,
        deadline_exceeded=elapsed > state.deadline_seconds,
        adapter_exception_type=None,
    )


def _build_solver(state: _RunState) -> Any:
    @solver(name=NATIVE_SOLVER_NAME)
    def native_runtime_adapter() -> Any:
        async def run(task_state: TaskState, generate: Generate) -> TaskState:
            del generate  # a native adapter owns its orchestration; Inspect never generates
            case = state.cases[str(task_state.sample_id)]
            try:
                state.check_identity()
                record = _run_case(state, case)
            except BridgeError as exc:
                state.fail(exc)
                task_state.metadata["adapter_status"] = "bridge_error"
                return task_state
            state.records[case.case_key] = record
            task_state.metadata["adapter_status"] = record.status
            return task_state

        return run

    return native_runtime_adapter()


@scorer(metrics=[accuracy()], name=_SCORER_NAME)
def _adapter_status_scorer() -> Any:
    async def score(task_state: TaskState, target: Target) -> Score:
        del target
        completed = task_state.metadata.get("adapter_status") == "completed"
        return Score(value=CORRECT if completed else INCORRECT)

    return score


# --------------------------------------------------------------------------- the run


def _refuse_remote_and_telemetry() -> None:
    for name in _TELEMETRY_ENV:
        if os.environ.get(name):
            raise BridgeError(CODE_TELEMETRY, name)


@contextmanager
def _private_workspace(parent: Path | None) -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="sovra-inspect-", dir=parent))
    os.chmod(path, 0o700)
    yield path


def _verify_log(log_path: Path, case_keys: frozenset[str]) -> tuple[str, str]:
    import inspect_ai

    log = read_eval_log(str(log_path))
    if log.status != "success":
        raise BridgeError(CODE_RUN_FAILED, str(log.status))
    steps = [step.solver for step in (log.plan.steps if log.plan else ())]
    if steps != [NATIVE_SOLVER_NAME]:
        raise BridgeError(CODE_GENERIC_AGENT)
    if log.eval.model != _MODEL_LESS or (log.stats and log.stats.model_usage):
        raise BridgeError(CODE_GENERIC_AGENT, "model call")
    sample_ids = {str(sample.id) for sample in (log.samples or ())}
    if sample_ids != case_keys:
        raise BridgeError(CODE_RUN_FAILED, "sample set")
    return _sha256(log_path.read_bytes()), str(inspect_ai.__version__)


def run_suite_through_runtime(
    adapter: RuntimeAdapterProtocol,
    case_set: SuiteCaseSet,
    *,
    hook: BridgeHook,
    entry_kind: str,
    is_reconstructed_adapter: bool,
    model_binding: ModelBinding,
    tenant_namespace: str,
    deadline_seconds: float,
    reconstructed_declaration: ReconstructedAdapterDeclaration | None = None,
    run_id: str,
    workspace_parent: Path | None = None,
) -> BridgeRunResult:
    """Runs every case of one locked development suite through `adapter` under Inspect, or
    raises `BridgeError`. See the module docstring for the refusals."""
    if not isinstance(hook, BridgeHook):
        raise BridgeError(CODE_INVALID_REQUEST, "hook")
    if not isinstance(case_set, SuiteCaseSet) or not case_set.cases:
        raise BridgeError(CODE_INVALID_REQUEST, "case_set")
    if not isinstance(run_id, str) or not run_id or not isinstance(tenant_namespace, str):
        raise BridgeError(CODE_INVALID_REQUEST, "run_id/tenant_namespace")
    if case_set.purpose != "development":
        raise BridgeError(CODE_INVALID_REQUEST, "purpose")

    try:
        kind = validate_runtime_entry_kind(entry_kind)
    except HarnessContractError as exc:
        raise BridgeError(CODE_UNSUPPORTED_ENTRY_KIND) from exc
    status = evaluate_target_runtime_qualification(
        kind,
        is_reconstructed_adapter=is_reconstructed_adapter,
        reconstructed_declaration=reconstructed_declaration,
    )
    if status is not QualificationStatus.QUALIFIED:
        raise BridgeError(CODE_INTEGRATION_REQUIRED)

    describe = adapter.describe()
    try:
        validate_model_binding_override(describe, model_binding)
    except HarnessContractError as exc:
        raise BridgeError(CODE_UNSUPPORTED_BINDING_KEY) from exc
    _refuse_remote_and_telemetry()

    pinned = adapter.attest()
    bind = getattr(adapter, "bind_bridge_hook", None)
    if callable(bind):
        bind(hook)

    cases = {case.case_key: case for case in case_set.cases}
    state = _RunState(
        adapter=adapter,
        cases=cases,
        run_id=run_id,
        tenant_namespace=tenant_namespace,
        agent_manifest_id=case_set.agent_manifest_id,
        binding=model_binding,
        deadline_seconds=deadline_seconds,
        pinned_attestation=pinned,
        pinned_describe=describe,
    )
    task = Task(
        dataset=MemoryDataset(list(case_set.samples), name=f"suite-{case_set.suite_id}"),
        solver=_build_solver(state),
        scorer=_adapter_status_scorer(),
        name=f"sovra-{case_set.suite_id}",
    )
    with _private_workspace(workspace_parent) as log_dir, _NetworkGuard() as guard:
        logs = inspect_eval(
            task,
            model=_MODEL_LESS,
            log_dir=str(log_dir),
            log_samples=True,
            display="none",
            max_samples=1,
            max_tasks=1,
            fail_on_error=False,
            score=True,
        )
    if guard.attempts:
        raise BridgeError(CODE_OUTBOUND)
    if state.first_error is not None:
        raise state.first_error
    if len(logs) != 1 or not logs[0].location:
        raise BridgeError(CODE_RUN_FAILED, "log count")
    log_path = Path(logs[0].location.removeprefix("file://"))
    if log_path.parent.resolve() != log_dir.resolve():
        raise BridgeError(CODE_RUN_FAILED, "log destination")

    log_sha256, version = _verify_log(log_path, frozenset(cases))
    state.check_identity()
    if set(state.records) != set(cases):
        raise BridgeError(CODE_RUN_FAILED, "case records")
    return BridgeRunResult(
        run_id=run_id,
        suite_id=case_set.suite_id,
        agent_manifest_id=case_set.agent_manifest_id,
        pinned_attestation=pinned,
        pinned_describe=describe,
        cases=tuple(state.records[case.case_key] for case in case_set.cases),
        log_dir=str(log_dir),
        log_path=str(log_path),
        log_sha256=log_sha256,
        inspect_version=version,
        network_attempts=guard.attempts,
        hook_requests_screened=hook.requests_screened,
        hook_attempts_captured=hook.attempts_captured,
    )
