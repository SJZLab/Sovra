"""Model action eligibility and continuation plans (FTF-H010-P4; FINE_TUNING_FACTORY_RELEASE_1.md
Section 0.12 roles, Section 17 paragraphs 3-4, Section 58A "Allowed next operations", Section 68.1
paragraph 4 "Actions are capability driven").

Pure and stdlib-only: no database, network or filesystem access. The route builds a
`ModelActionFacts` from database rows it read itself (the version, its succeeded import job, the
artifact, the license gate, the FTF-1002 lineage manifest and the active roles) and asks
`evaluate_model_actions` what may be done with the version. Nothing here trusts request text.

Rules this module enforces:

- A row that is not validated (no succeeded import job, not factory trained -- including every
  FTF-1001 synchronous create) is denied every action, `import_not_validated`.
- A revoked license denies every use action `license_not_accepted`, and an adapter whose base no
  longer validates denies them `adapter_base_incompatible`. Validation outranks license, and
  license outranks base compatibility.
- An external endpoint can be evaluated and compared. Whatever its reference or roles say, it can
  neither continue tuning (`endpoint_not_trainable`) nor resume (`no_trusted_checkpoint`):
  permission to invoke is not permission or technical ability to continue training (Section 68.1) and a
  teacher role does not determine trainability (Section 0.12). The reference text is carried only
  for display and is never read to decide anything.
- Imported weights and adapters continue as a `new_run` (Section 17: "Imported adapters without
  optimizer state start a new training run, not a resume"); they never `exact_resume`. A factory
  trained version resumes only when a trusted checkpoint fact exists; the route has no such input
  until FTF-0713, so it never assumes one.
- request_qualification, deploy_route and retire need their prerequisite to be exactly
  "available"; a missing, unknown or differently spelled state is denied. Unknown is never allowed.
- retain_incumbent is allowed only for the active `current_production` holder.

`NewRunPlan` and `ExactResumePlan` make "a resumed step differs from a new continuation" a
property: a new run starts at step 0 with no optimizer state under a new run id and its own cost,
a resume starts at the checkpoint's step (>= 1) in the checkpoint's run lineage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = [
    "ACTIONS",
    "ORIGINS",
    "ROLES",
    "ActionDecision",
    "ActionDeniedError",
    "CheckpointFact",
    "ContinuationFactsError",
    "ExactResumePlan",
    "ModelActionFacts",
    "NewRunPlan",
    "evaluate_model_actions",
    "require_action_allowed",
]

ORIGIN_FACTORY_TRAINED = "factory_trained"
ORIGIN_IMPORTED_WEIGHTS = "imported_weights"
ORIGIN_IMPORTED_ADAPTER = "imported_adapter"
ORIGIN_EXTERNAL_ENDPOINT = "external_endpoint"
ORIGINS = (
    ORIGIN_FACTORY_TRAINED,
    ORIGIN_IMPORTED_WEIGHTS,
    ORIGIN_IMPORTED_ADAPTER,
    ORIGIN_EXTERNAL_ENDPOINT,
)
ROLES = ("current_production", "baseline", "candidate", "teacher", "judge")
ACTIONS = (
    "evaluate",
    "compare",
    "continue_tuning",
    "exact_resume",
    "request_qualification",
    "retain_incumbent",
    "deploy_route",
    "retire",
)

CODE_IMPORT_NOT_VALIDATED = "import_not_validated"
CODE_LICENSE_NOT_ACCEPTED = "license_not_accepted"
CODE_ADAPTER_BASE_INCOMPATIBLE = "adapter_base_incompatible"
CODE_ENDPOINT_NOT_TRAINABLE = "endpoint_not_trainable"
CODE_NO_TRUSTED_CHECKPOINT = "no_trusted_checkpoint"
CODE_PREREQUISITE_NOT_AVAILABLE = "prerequisite_not_available"
CODE_NOT_CURRENT_PRODUCTION = "not_current_production"
CODE_UNKNOWN_ACTION = "unknown_action"

PREREQUISITE_AVAILABLE = "available"
_PREREQUISITE_OF = MappingProxyType(
    {
        "request_qualification": "qualification_lifecycle",
        "deploy_route": "deployment_routing",
        "retire": "status_lifecycle",
    }
)
_USE_ACTIONS = frozenset(
    {
        "evaluate",
        "compare",
        "continue_tuning",
        "exact_resume",
        "request_qualification",
        "retain_incumbent",
        "deploy_route",
    }
)

_ID_MAX = 200
_REF_MAX = 2048


class ContinuationFactsError(ValueError):
    """A fact is of the wrong type or outside its closed set. Carries a code, never the value."""


class ActionDeniedError(Exception):
    """An action is not allowed. `code` is the stable reason; the text is the code only."""

    def __init__(self, action: str, code: str) -> None:
        super().__init__(code)
        self.action = action
        self.code = code


def _exact_str(value: object, what: str, *, limit: int) -> str:
    if type(value) is not str or len(value) == 0 or len(value) > limit:
        raise ContinuationFactsError(f"{what}_invalid")
    return value


@dataclass(frozen=True)
class CheckpointFact:
    """A trusted checkpoint of a factory trained run (FTF-0713's manifest, once it exists)."""

    checkpoint_id: str
    step: int
    run_id: str

    def __post_init__(self) -> None:
        _exact_str(self.checkpoint_id, "checkpoint_id", limit=_ID_MAX)
        _exact_str(self.run_id, "checkpoint_run_id", limit=_ID_MAX)
        if type(self.step) is not int or self.step < 1:
            raise ContinuationFactsError("checkpoint_step_invalid")


@dataclass(frozen=True)
class NewRunPlan:
    parent_model_version_id: str
    run_id: str
    kind: str = "new_run"
    start_step: int = 0
    optimizer_state: str = "none"
    separate_cost: bool = True


@dataclass(frozen=True)
class ExactResumePlan:
    checkpoint_id: str
    start_step: int
    run_id: str
    kind: str = "exact_resume"
    same_run_lineage: bool = True


@dataclass(frozen=True)
class ActionDecision:
    action: str
    allowed: bool
    code: str | None
    plan: NewRunPlan | ExactResumePlan | None = None


@dataclass(frozen=True)
class ModelActionFacts:
    """What the route read from the database about one model version.

    `license_accepted` is None only for an external endpoint (no artifact exists) and a bool for
    every weight-backed origin; `base_compatible` is a bool only for an imported adapter.
    """

    model_version_id: str
    origin: str
    validated: bool
    license_accepted: bool | None
    base_compatible: bool | None
    roles: frozenset[str]
    new_run_id: str
    model_ref: str | None = None
    checkpoint: CheckpointFact | None = None
    prerequisites: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _exact_str(self.model_version_id, "model_version_id", limit=_ID_MAX)
        _exact_str(self.new_run_id, "new_run_id", limit=_ID_MAX)
        if type(self.origin) is not str or self.origin not in ORIGINS:
            raise ContinuationFactsError("origin_invalid")
        if type(self.validated) is not bool:
            raise ContinuationFactsError("validated_invalid")
        is_endpoint = self.origin == ORIGIN_EXTERNAL_ENDPOINT
        if is_endpoint:
            if self.license_accepted is not None:
                raise ContinuationFactsError("license_accepted_invalid")
        elif type(self.license_accepted) is not bool:
            raise ContinuationFactsError("license_accepted_invalid")
        if self.origin == ORIGIN_IMPORTED_ADAPTER:
            if type(self.base_compatible) is not bool:
                raise ContinuationFactsError("base_compatible_invalid")
        elif self.base_compatible is not None:
            raise ContinuationFactsError("base_compatible_invalid")
        if type(self.roles) is not frozenset:
            raise ContinuationFactsError("roles_invalid")
        for role in self.roles:
            if type(role) is not str or role not in ROLES:
                raise ContinuationFactsError("roles_invalid")
        if is_endpoint:
            _exact_str(self.model_ref, "model_ref", limit=_REF_MAX)
        if self.model_ref is not None:
            _exact_str(self.model_ref, "model_ref", limit=_REF_MAX)
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in self.model_ref):
                raise ContinuationFactsError("model_ref_invalid")
        if self.checkpoint is not None:
            if type(self.checkpoint) is not CheckpointFact:
                raise ContinuationFactsError("checkpoint_invalid")
            if self.checkpoint.run_id == self.new_run_id:
                raise ContinuationFactsError("new_run_id_invalid")
        if not isinstance(self.prerequisites, Mapping):
            raise ContinuationFactsError("prerequisites_invalid")
        object.__setattr__(self, "prerequisites", MappingProxyType(dict(self.prerequisites)))


def _deny(action: str, code: str) -> ActionDecision:
    return ActionDecision(action=action, allowed=False, code=code)


def _allow(action: str, plan: NewRunPlan | ExactResumePlan | None = None) -> ActionDecision:
    return ActionDecision(action=action, allowed=True, code=None, plan=plan)


def _continue_tuning(facts: ModelActionFacts) -> ActionDecision:
    if facts.origin == ORIGIN_EXTERNAL_ENDPOINT:
        return _deny("continue_tuning", CODE_ENDPOINT_NOT_TRAINABLE)
    plan = NewRunPlan(parent_model_version_id=facts.model_version_id, run_id=facts.new_run_id)
    return _allow("continue_tuning", plan)


def _exact_resume(facts: ModelActionFacts) -> ActionDecision:
    checkpoint = facts.checkpoint
    if facts.origin != ORIGIN_FACTORY_TRAINED or checkpoint is None:
        return _deny("exact_resume", CODE_NO_TRUSTED_CHECKPOINT)
    plan = ExactResumePlan(
        checkpoint_id=checkpoint.checkpoint_id, start_step=checkpoint.step, run_id=checkpoint.run_id
    )
    return _allow("exact_resume", plan)


def _needs_prerequisite(action: str, facts: ModelActionFacts) -> ActionDecision:
    state = facts.prerequisites.get(_PREREQUISITE_OF[action])
    if type(state) is str and state == PREREQUISITE_AVAILABLE:
        return _allow(action)
    return _deny(action, CODE_PREREQUISITE_NOT_AVAILABLE)


def _retain_incumbent(facts: ModelActionFacts) -> ActionDecision:
    if "current_production" in facts.roles:
        return _allow("retain_incumbent")
    return _deny("retain_incumbent", CODE_NOT_CURRENT_PRODUCTION)


def _decide(action: str, facts: ModelActionFacts) -> ActionDecision:
    if not facts.validated:
        return _deny(action, CODE_IMPORT_NOT_VALIDATED)
    if action in _USE_ACTIONS:
        if facts.license_accepted is False:
            return _deny(action, CODE_LICENSE_NOT_ACCEPTED)
        if facts.base_compatible is False:
            return _deny(action, CODE_ADAPTER_BASE_INCOMPATIBLE)
    if action == "continue_tuning":
        return _continue_tuning(facts)
    if action == "exact_resume":
        return _exact_resume(facts)
    if action == "retain_incumbent":
        return _retain_incumbent(facts)
    if action in _PREREQUISITE_OF:
        return _needs_prerequisite(action, facts)
    return _allow(action)


def evaluate_model_actions(facts: ModelActionFacts) -> Mapping[str, ActionDecision]:
    """The decision for every action in `ACTIONS`, in order."""
    if type(facts) is not ModelActionFacts:
        raise ContinuationFactsError("facts_invalid")
    return MappingProxyType({action: _decide(action, facts) for action in ACTIONS})


def require_action_allowed(facts: ModelActionFacts, action: object) -> ActionDecision:
    """For effect routes: the decision when allowed, `ActionDeniedError` otherwise. An action that
    is not exactly one of `ACTIONS` is denied `unknown_action` (unknown is never allowed)."""
    if type(action) is not str or action not in ACTIONS:
        raise ActionDeniedError(str(type(action).__name__), CODE_UNKNOWN_ACTION)
    decision = evaluate_model_actions(facts)[action]
    if not decision.allowed:
        assert decision.code is not None
        raise ActionDeniedError(action, decision.code)
    return decision
