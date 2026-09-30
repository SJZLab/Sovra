"""LiteLLM/current-model binding discovery (FTF-04A12, Section 7A.4/66/77/81).

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/litellm_current_model_binding_discovery.py`)
to `apps/api/app/target_connectors/...`, matching FTF-04A08's/FTF-04A09's/
FTF-04A10's/FTF-04A11's own real layout precedent exactly (that same
packet-vs-real-layout refinement is already disclosed in each of their own
module docstrings).

Section 7A.4's own numbered "Analysis order" names step 4, "Detect model
configuration and LiteLLM aliases", as its own distinct step -- separate
from step 3 (agent entrypoints, FTF-04A09/FTF-04A10's own scope) and from
the generic configuration-normalization FTF-04A11 already built. Neither
FTF-04A09 nor FTF-04A10 assembles anything beyond one call site's own
`model=`/`model` keyword; FTF-04A11's own module docstring says explicitly
it "deliberately does not itself resolve 'model configuration and LiteLLM
aliases' ... from the values it reads". This task is the first to combine
all three already-CERTIFIED evidence sources into one real, cross-source
"current model binding" view, and the first to reconcile a literal alias
against the real, already-CERTIFIED LiteLLM authorized catalog
(FTF-0207's own `reconcile_aliases`) rather than merely reporting a string
found in source.

This task's own acceptance clause ("Identify literal/config-derived model
binding with actual gateway role confirmation later") is read literally:
"role" (Non-Negotiable Product Contract item 12 -- `current_production`/
`baseline`/`candidate`/`teacher`/`judge`, a field this task's own domain
model never even names on `ModelCatalogEntry`) is out of this task's own
scope, deferred to a later, separately-claimed task. What this task does
do now is identify the literal (Python/JS-TS, FTF-04A09/FTF-04A10) or
config-derived (FTF-04A11) alias string a target repository's own source
actually references, and -- when a caller supplies an already-computed
`AliasReconciliationResult` (FTF-0207) -- confirm whether that exact alias
is a real, currently authorized model on the connected LiteLLM gateway.
Confirmation is optional per call (`alias_reconciliation=None` is a fully
valid input, reported as `not_attempted`, never guessed into `confirmed`)
because a caller may run discovery before ever connecting a gateway; this
is not the same as "role confirmation" and never claims to assign one.

This task's own negative_acceptance ("Alias does not reveal weights or
enforceable access; unresolved dynamic model selection remains explicit")
is met by two independent, structural (not merely runtime-checked)
design decisions:

1. `_confirm_against_gateway` only ever reads a matched
   `ReconciledModel.metadata_status` (a two-value enum, `enriched`/
   `limited`) onto `CurrentModelBindingCandidate.gateway_metadata_status`.
   `ReconciledModel.fields` -- the raw provider/cost/capability metadata
   dict FTF-0205's own `enrich_visible_models` attaches, which could carry
   values informative about deployment/capacity but never a credential or
   weight (FTF-0207's own already-CERTIFIED scope carries no credential
   field anywhere) -- is never read by this module at all; there is no
   code path in this file that can reach it. Confirmation here can never
   go further than "this exact alias string is present in the gateway's
   own authorized list", never "and here is what it can do or cost".
2. `_candidate_from_binding` forces `model_alias=None` and
   `gateway_confirmation_status=NOT_APPLICABLE` for every candidate whose
   upstream `resolved` is `False`, unconditionally overriding whatever the
   upstream evidence object's own `model_alias` field happened to carry --
   an unresolved (dynamic/absent) binding is never reconciled against the
   gateway and never silently promoted into a confirmed one. This is the
   same discipline FTF-04A09/FTF-04A10 already applied one layer down
   ("Dynamic/unresolved constructs remain unknown"), reapplied here as
   this task's own independent, defense-in-depth boundary rather than
   trusting the upstream evidence object was necessarily well-formed.

Config-derived detection (Section 7A.4 step 4's own "config" half) uses a
closed, disclosed, exact-match (never substring/`in`) heuristic on a
config leaf's own normalized (non-alphanumeric stripped, upper-cased) key
name -- the same "known, disclosed non-exhaustive heuristic" precedent
FTF-04A11's own `_is_secret_like_key` already established one task over,
applied here to a different closed vocabulary
(`MODEL`/`MODELNAME`/`MODELID`/`MODELALIAS`/`DEFAULTMODEL`/
`LITELLMMODEL`/`LITELLMALIAS`). A matching key whose own `value_type` is
not `"string"`, or whose string value is empty, is never guessed into an
alias -- reported `resolved=False` with an honest reason instead. A
secret-shaped key (`ConfigKeyEvidence.secret_like`, already redacted by
FTF-04A11 itself) is never treated as a model-binding candidate at all,
since its own real value was never carried past FTF-04A11's own redaction
boundary for this module to read.

Database changes: none (this task's own `database_changes` clause -- "No
independent schema change. Consume frozen contract."). Like FTF-0207
itself, `discover_current_model_bindings` is a pure, synchronous, no-I/O
function over already-computed evidence objects; it never calls a gateway,
a filesystem, or a database itself. It also introduces no `ResourceLimits`
of its own -- every input sequence it iterates is already bounded by its
own already-CERTIFIED producer (FTF-04A09/FTF-04A10/FTF-04A11's own
`ResourceLimits`), so a second bound here would be redundant, not a new
protection.

This module does not itself assemble a `DiscoveredAgentCandidate` row or
write `AgentManifest.current_model_binding_json` -- FTF-04A17's own
already-CERTIFIED storage schema and a later composition task own that,
the same "detection is not the same act as storage/selection" boundary
every 04A-family task before this one has drawn. `proving_symbol` stays
blank and `product_truth_check.py`'s product-true count is deliberately
unchanged, the same disclosed "no live route yet" scope FTF-04A09/
FTF-04A10/FTF-04A11 already established.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from packages.litellm_client.alias_reconciliation import AliasReconciliationResult

from app.target_connectors.json_yaml_toml_configuration_discovery import ConfigDocumentEvidence
from app.target_connectors.python_agent_config_tool_discovery import (
    EntrypointCandidate as PythonEntrypointCandidate,
)
from app.target_connectors.typescript_javascript_deterministic_agent_config_tool_discovery import (
    EntrypointCandidate as TypeScriptJavaScriptEntrypointCandidate,
)

__all__ = [
    "BindingSource",
    "CurrentModelBindingCandidate",
    "CurrentModelBindingDiscoveryError",
    "CurrentModelBindingDiscoveryEvidence",
    "GatewayConfirmationStatus",
    "discover_current_model_bindings",
]

_NON_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]")

# Closed, disclosed set of exact, normalized leaf key names this module
# treats as a config-derived model-binding candidate -- never a
# substring/`in` match. See this module's own docstring for why this is a
# known, non-exhaustive heuristic, not a claim of exhaustive detection.
_MODEL_KEY_NAMES = frozenset(
    {
        "MODEL",
        "MODELNAME",
        "MODELID",
        "MODELALIAS",
        "DEFAULTMODEL",
        "LITELLMMODEL",
        "LITELLMALIAS",
    }
)


class BindingSource(str, Enum):
    PYTHON_LITERAL = "python_literal"
    TYPESCRIPT_JAVASCRIPT_LITERAL = "typescript_javascript_literal"
    CONFIG_DERIVED = "config_derived"


class GatewayConfirmationStatus(str, Enum):
    """Section 7A.4/this task's own negative_acceptance -- an unresolved
    binding is never reconciled (`NOT_APPLICABLE`); a resolved one is
    either genuinely checked against a real, caller-supplied gateway
    catalog (`CONFIRMED`/`NOT_FOUND`) or, when no catalog was supplied yet,
    honestly deferred (`NOT_ATTEMPTED`) -- never guessed into `CONFIRMED`."""

    NOT_APPLICABLE = "not_applicable"
    NOT_ATTEMPTED = "not_attempted"
    CONFIRMED = "confirmed"
    NOT_FOUND = "not_found"


class CurrentModelBindingDiscoveryError(Exception):
    """An upstream evidence object violates its own already-established
    contract (`resolved=True` with no real `model_alias` string) --
    rejected at this module's own boundary rather than silently guessed
    past."""


@dataclass(frozen=True)
class CurrentModelBindingCandidate:
    """One real candidate current-model binding, tied to its own source
    file/location, never carrying gateway metadata beyond a bare
    `enriched`/`limited` status string -- see this module's own docstring
    for why `ReconciledModel.fields` is never read here at all."""

    relative_path: str
    source: BindingSource
    location: str
    resolved: bool
    model_alias: str | None
    reason: str
    gateway_confirmation_status: GatewayConfirmationStatus
    gateway_metadata_status: str | None

    def to_json(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "source": self.source.value,
            "location": self.location,
            "resolved": self.resolved,
            "model_alias": self.model_alias,
            "reason": self.reason,
            "gateway_confirmation_status": self.gateway_confirmation_status.value,
            "gateway_metadata_status": self.gateway_metadata_status,
        }


@dataclass(frozen=True)
class CurrentModelBindingDiscoveryEvidence:
    candidates: tuple[CurrentModelBindingCandidate, ...]
    resolved_count: int
    gateway_confirmed_count: int
    gateway_not_found_count: int


def _normalized_leaf_key(key_path: str) -> str:
    last_segment = key_path.rsplit(".", 1)[-1]
    last_segment = last_segment.split("[", 1)[0]
    return _NON_ALPHANUMERIC.sub("", last_segment).upper()


def _confirm_against_gateway(
    model_alias: str, alias_reconciliation: AliasReconciliationResult | None
) -> tuple[GatewayConfirmationStatus, str | None]:
    """Exact, case-sensitive `id` match against an already-computed
    `AliasReconciliationResult` -- the same case-sensitive-exact-match
    discipline FTF-0207's own `reconcile_aliases` already established one
    task over, reapplied here rather than trusted from the caller alone.
    Only `metadata_status.value` (a plain two-value string) is ever read;
    `ReconciledModel.fields` is never touched."""
    if alias_reconciliation is None:
        return GatewayConfirmationStatus.NOT_ATTEMPTED, None
    for reconciled in alias_reconciliation.models:
        if reconciled.id == model_alias:
            return GatewayConfirmationStatus.CONFIRMED, reconciled.metadata_status.value
    return GatewayConfirmationStatus.NOT_FOUND, None


def _candidate_from_binding(
    *,
    relative_path: str,
    source: BindingSource,
    location: str,
    resolved: bool,
    model_alias: str | None,
    reason: str,
    alias_reconciliation: AliasReconciliationResult | None,
) -> CurrentModelBindingCandidate:
    if not resolved:
        # This task's own negative_acceptance, enforced unconditionally:
        # an unresolved binding is never reconciled and never carries a
        # model_alias, whatever the upstream evidence object's own field
        # happened to hold.
        return CurrentModelBindingCandidate(
            relative_path=relative_path,
            source=source,
            location=location,
            resolved=False,
            model_alias=None,
            reason=reason,
            gateway_confirmation_status=GatewayConfirmationStatus.NOT_APPLICABLE,
            gateway_metadata_status=None,
        )
    if not model_alias:
        raise CurrentModelBindingDiscoveryError(
            f"a resolved model binding at {relative_path!r} ({location}) must carry a "
            f"real, non-empty model_alias string, never empty/None"
        )
    status, metadata_status = _confirm_against_gateway(model_alias, alias_reconciliation)
    return CurrentModelBindingCandidate(
        relative_path=relative_path,
        source=source,
        location=location,
        resolved=True,
        model_alias=model_alias,
        reason=reason,
        gateway_confirmation_status=status,
        gateway_metadata_status=metadata_status,
    )


def _config_derived_candidates(
    document: ConfigDocumentEvidence, alias_reconciliation: AliasReconciliationResult | None
) -> list[CurrentModelBindingCandidate]:
    candidates: list[CurrentModelBindingCandidate] = []
    if not document.resolved:
        return candidates
    for key in document.keys:
        if key.secret_like:
            continue
        if _normalized_leaf_key(key.key_path) not in _MODEL_KEY_NAMES:
            continue
        location = f"key:{key.key_path}"
        if key.value_type == "string" and isinstance(key.value, str) and key.value != "":
            candidates.append(
                _candidate_from_binding(
                    relative_path=document.relative_path,
                    source=BindingSource.CONFIG_DERIVED,
                    location=location,
                    resolved=True,
                    model_alias=key.value,
                    reason="literal_config_value",
                    alias_reconciliation=alias_reconciliation,
                )
            )
            continue
        reason = (
            "empty_string_value" if key.value_type == "string" else "unresolved_non_string_value"
        )
        candidates.append(
            _candidate_from_binding(
                relative_path=document.relative_path,
                source=BindingSource.CONFIG_DERIVED,
                location=location,
                resolved=False,
                model_alias=None,
                reason=reason,
                alias_reconciliation=alias_reconciliation,
            )
        )
    return candidates


def discover_current_model_bindings(
    *,
    python_entrypoints: Sequence[PythonEntrypointCandidate] = (),
    typescript_javascript_entrypoints: Sequence[TypeScriptJavaScriptEntrypointCandidate] = (),
    config_documents: Sequence[ConfigDocumentEvidence] = (),
    alias_reconciliation: AliasReconciliationResult | None = None,
) -> CurrentModelBindingDiscoveryEvidence:
    """Combines FTF-04A09's/FTF-04A10's own per-entrypoint
    `ModelBindingEvidence` and FTF-04A11's own per-document
    `ConfigKeyEvidence` into one real, cross-source current-model-binding
    view, optionally reconciled against a real, caller-supplied FTF-0207
    `AliasReconciliationResult`. Never raises for adversarial upstream
    *content* (an unresolved/dynamic binding is simply reported as such);
    only raises `CurrentModelBindingDiscoveryError` for a structurally
    unsafe upstream evidence object (`resolved=True` with no real alias) --
    a producer bug, not untrusted snapshot content, the same "caller
    contract enforced defensively" precedent FTF-04A09's own
    `PythonDiscoveryError`/FTF-04A11's own `ConfigDiscoveryError` already
    established for their own callers."""
    candidates: list[CurrentModelBindingCandidate] = []

    for python_entrypoint in python_entrypoints:
        candidates.append(
            _candidate_from_binding(
                relative_path=python_entrypoint.relative_path,
                source=BindingSource.PYTHON_LITERAL,
                location=f"function:{python_entrypoint.function_name}",
                resolved=python_entrypoint.model_binding.resolved,
                model_alias=python_entrypoint.model_binding.model_alias,
                reason=python_entrypoint.model_binding.reason,
                alias_reconciliation=alias_reconciliation,
            )
        )

    for ts_entrypoint in typescript_javascript_entrypoints:
        candidates.append(
            _candidate_from_binding(
                relative_path=ts_entrypoint.relative_path,
                source=BindingSource.TYPESCRIPT_JAVASCRIPT_LITERAL,
                location=f"function:{ts_entrypoint.function_name}",
                resolved=ts_entrypoint.model_binding.resolved,
                model_alias=ts_entrypoint.model_binding.model_alias,
                reason=ts_entrypoint.model_binding.reason,
                alias_reconciliation=alias_reconciliation,
            )
        )

    for document in config_documents:
        candidates.extend(_config_derived_candidates(document, alias_reconciliation))

    resolved_count = sum(1 for candidate in candidates if candidate.resolved)
    gateway_confirmed_count = sum(
        1
        for candidate in candidates
        if candidate.gateway_confirmation_status is GatewayConfirmationStatus.CONFIRMED
    )
    gateway_not_found_count = sum(
        1
        for candidate in candidates
        if candidate.gateway_confirmation_status is GatewayConfirmationStatus.NOT_FOUND
    )

    return CurrentModelBindingDiscoveryEvidence(
        candidates=tuple(candidates),
        resolved_count=resolved_count,
        gateway_confirmed_count=gateway_confirmed_count,
        gateway_not_found_count=gateway_not_found_count,
    )
