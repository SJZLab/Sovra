"""LiteLLM alias reconciliation (FTF-0207, Section 2-3/38/65/77/81).

`packages/litellm_client/v1_models_discovery.py` (FTF-0204, CERTIFIED) and
`packages/litellm_client/v1_model_info_enrichment.py` (FTF-0205, CERTIFIED)
each independently call the gateway and apply their own left-join rule onto
`GET /v1/models`'s authorized alias list. Nothing yet combines their two
results into one canonical per-alias record for a downstream caller --
`docs/TASK_INDEX.md` shows FTF-0208 ("Last-known-good catalog"), FTF-0210
("Real chat probe"), FTF-0214 ("Model catalog UI") and FTF-0215 ("Periodic
sync workflow") all depend on this task rather than on FTF-0204/FTF-0205
directly. That composition, proven never to leak or invent access across the
join, is this task's own scope.

Scope decision (disclosed, same precedent FTF-0202/FTF-0205 already
established for "no live HTTP route yet"): this task's own packet `files`
list names only this module and its test, not a route file -- no route in
`apps/api/app/litellm/connection_routes.py` calls `enrich_visible_models`
either, so there is nothing yet for this module's own result to be wired
into. `proving_symbol` stays blank and `product_truth_check.py`'s
product-true count is deliberately unchanged, exactly as FTF-0202 was before
FTF-0203 wired it and FTF-0205 was before this task's own dependents.

This task's own acceptance clause ("Left-join case-sensitive alias identities
to metadata without replacing user-facing ID") reapplies Section 3.2's "no
authorization union" rule as this module's own independent, defense-in-depth
boundary rather than trusting that `enrichment` was necessarily produced by
calling `enrich_visible_models(visible_model_ids=<these exact discovery
ids>)` -- `reconcile_aliases` recomputes the join itself from
`discovery.models` (the sole source of both existence and display identity)
against `enrichment.entries` keyed by their own `id` string, using plain
Python `==`/`dict` lookups so the match is exact and case-sensitive by
construction (no `.lower()`/`.casefold()`/`.upper()` call anywhere in this
module). A same-name-different-case enrichment entry is a *different*
identity, never attached to the differently-cased authorized alias, and an
enrichment id absent from `discovery.models` entirely (a "name found only in
management metadata", Section 3.2, or an upstream/provider name that happens
to collide with some other alias) is simply never reconciled into a
`ReconciledModel` -- it cannot newly grant visibility of a model absent from
the current accessible set (this task's own negative_acceptance).

The reconciled `id` is always `discovery`'s own alias string, verbatim,
never overwritten by anything the enrichment side reports (this task's own
"without replacing user-facing ID") -- there is no field on either
`DiscoveredModel` or `ModelMetadataEntry` this module ever treats as an
alternate display identity.

"Colliding aliases/upstream names never merge credentials" (this task's own
negative_acceptance) holds by construction, not by an extra runtime check:
neither `DiscoveredModel` nor `ModelMetadataEntry` (nor this module's own
`ReconciledModel`) carries a credential/secret field at all -- both inputs
are already-resolved, already-redacted results (FTF-0204/FTF-0205's own
already-CERTIFIED scope), so there is no credential value anywhere in this
module's data for a collision to merge. A duplicate `id` inside
`enrichment.entries` itself (which `enrich_visible_models` never produces,
since its own internal dedup already guarantees unique ids -- but a future or
adversarial caller could still hand this module a hand-built result that
does) is resolved deterministically: the first occurrence wins and any later
duplicate is ignored, never silently overwriting a field already reconciled
for that id.

No independent schema change (this task's own `database_changes` clause):
`reconcile_aliases` is a pure, synchronous, no-I/O function -- it never calls
the gateway itself and never persists its result. A caller supplies already-
computed `ModelDiscoveryResult`/`ModelInfoEnrichmentResult` values, typically
by calling `discover_models` then `enrich_visible_models(visible_model_ids=
tuple(m.id for m in discovery.models))` in sequence, proven together for real
in `tests/integration/test_ftf_0207_alias_reconciliation.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from packages.litellm_client.v1_model_info_enrichment import (
    ModelInfoEnrichmentResult,
    ModelMetadataEntry,
)
from packages.litellm_client.v1_models_discovery import ModelDiscoveryResult, ModelMetadataStatus

__all__ = [
    "AliasReconciliationResult",
    "ReconciledModel",
    "reconcile_aliases",
]

_EMPTY_FIELDS: MappingProxyType[str, object] = MappingProxyType({})


@dataclass(frozen=True)
class ReconciledModel:
    id: str
    metadata_status: ModelMetadataStatus
    fields: MappingProxyType[str, object]


@dataclass(frozen=True)
class AliasReconciliationResult:
    models: tuple[ReconciledModel, ...]
    model_count: int
    reconciled_at: datetime


def reconcile_aliases(
    *,
    discovery: ModelDiscoveryResult,
    enrichment: ModelInfoEnrichmentResult,
    now: datetime | None = None,
) -> AliasReconciliationResult:
    """Left-joins `enrichment.entries` onto `discovery.models` by exact,
    case-sensitive `id` string equality. `discovery.models` is the sole
    source of both which aliases exist and their display `id` -- this
    function never adds, renames or drops an alias based on anything the
    enrichment side reports. An enrichment entry whose `id` does not exactly
    match a discovered alias (whether by case, by naming a different
    upstream/provider identity, or by naming a model absent from the current
    accessible set entirely) is never consulted."""
    reconciled_at = now if now is not None else datetime.now(UTC)

    enrichment_by_id: dict[str, ModelMetadataEntry] = {}
    for entry in enrichment.entries:
        if entry.id in enrichment_by_id:
            continue
        enrichment_by_id[entry.id] = entry

    models = []
    for discovered in discovery.models:
        matched = enrichment_by_id.get(discovered.id)
        if matched is None:
            models.append(
                ReconciledModel(
                    id=discovered.id,
                    metadata_status=ModelMetadataStatus.LIMITED,
                    fields=_EMPTY_FIELDS,
                )
            )
            continue
        models.append(
            ReconciledModel(
                id=discovered.id,
                metadata_status=matched.metadata_status,
                fields=matched.fields,
            )
        )

    return AliasReconciliationResult(
        models=tuple(models),
        model_count=len(models),
        reconciled_at=reconciled_at,
    )
