"""Authorized-access catalog display snapshot (FTF-H004, Section 3.2/3.3/77/81).

`packages/litellm_client/last_known_good_catalog.py` (FTF-0208, CERTIFIED)
already separates a connection's historic catalog (every id ever seen,
`callable`/`stale`/`removed`) from a single boolean `metadata_status` --
but it discards `packages/litellm_client/alias_reconciliation.py` (FTF-0207,
CERTIFIED)'s own real, allowlisted `/model/info` field values entirely: a
`ModelDiscoveryResult` only ever carries `metadata_status`
(`enriched`/`limited`), never the actual `fields` mapping a `ReconciledModel`
carries. Nothing in this repository today combines FTF-0208's historic view
with a fresh `AliasReconciliationResult`'s real safe fields into one display
record -- this module is exactly that composition, and this task's own goal
("Separate historic catalog from current callable set; left-join only
matching safe metadata... fail closed on revoked/missing grants") is its
acceptance boundary.

`build_access_snapshot` enforces Section 3.2's "no authorization union" a
second, independent time, as its own defense-in-depth boundary rather than
trusting that a caller only ever passes a `reconciliation` genuinely
produced for this exact catalog: a fresh reconciled model's `fields` are
ever attached to a display entry only when that entry's own catalog
`status` is `CatalogEntryStatus.CALLABLE` -- a `stale` (revoked/expired) or
`removed` id gets empty `fields` unconditionally, even if the fresh
`reconciliation` argument still (mistakenly, or because a slow/duplicate
caller passed a stale reconciliation) reports safe metadata for that exact
id. The set of entries returned is always exactly the catalog's own
`entries` (the historic, audit-visible id set); a `reconciliation` model
whose `id` the catalog has never seen is never turned into a new entry --
this function only ever iterates `catalog_snapshot.entries`, never
`reconciliation.models`, so it cannot itself widen the authorized set no
matter what a caller passes in.

No independent schema change (this task's own `database_changes` clause):
this is a pure, synchronous, no-I/O function -- it never calls the gateway
and never persists its result, same convention `reconcile_aliases` (FTF-0207)
and `LastKnownGoodCatalog` (FTF-0208) themselves already established. A
caller typically supplies the output of a real `discover_models` call fed
into a per-connection `LastKnownGoodCatalog`, plus a fresh
`reconcile_aliases(discovery=..., enrichment=enrich_visible_models(...))`
call for the catalog's own currently-callable ids -- proven together for
real (not merely at unit level) in
`tests/tasks/test_ftf_h004.py::test_real_gateway_snapshot_never_exposes_metadata_only_model_and_survives_revocation`.

Disclosed, not hidden (same precedent FTF-0202/FTF-0205/FTF-0207/FTF-0208/
FTF-0209/FTF-0215 already established for "no live route/caller yet"): no
live FastAPI route or UI anywhere in this repository calls this function
yet -- `docs/TASK_INDEX.md` lists FTF-0214 ("Model catalog UI") as the task
that renders a catalog to an end user. `proving_symbol` stays blank and
`product_truth_check.py`'s product-true count is deliberately unchanged.

Every field on `AccessSnapshotEntry`/`AccessSnapshotView` is content-free
(Section observability: "no secrets/raw prompts in task reports") -- ids,
enum statuses, timestamps and the same already-allowlisted `fields` mapping
`enrich_visible_models` itself already produces, never a credential or raw
response body.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from packages.litellm_client.alias_reconciliation import AliasReconciliationResult, ReconciledModel
from packages.litellm_client.last_known_good_catalog import (
    AccessState,
    CatalogEntryStatus,
    CatalogSnapshot,
)
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus

__all__ = [
    "AccessSnapshotEntry",
    "AccessSnapshotView",
    "build_access_snapshot",
]

_EMPTY_FIELDS: MappingProxyType[str, object] = MappingProxyType({})


@dataclass(frozen=True)
class AccessSnapshotEntry:
    id: str
    status: CatalogEntryStatus
    metadata_status: ModelMetadataStatus
    first_seen_at: datetime
    last_seen_callable_at: datetime | None
    fields: MappingProxyType[str, object]


@dataclass(frozen=True)
class AccessSnapshotView:
    entries: tuple[AccessSnapshotEntry, ...]
    access_state: AccessState
    last_successful_check_at: datetime | None
    last_checked_at: datetime | None
    alert: str | None


def build_access_snapshot(
    catalog_snapshot: CatalogSnapshot,
    reconciliation: AliasReconciliationResult | None = None,
) -> AccessSnapshotView:
    """Combine `catalog_snapshot` (the historic, audit-visible view) with a
    fresh `reconciliation` (the current callable set's own real safe
    metadata) into one display snapshot. `reconciliation` is optional --
    passing `None` (no fresh reconciliation available this cycle) simply
    means every entry's `fields` stays empty, never a stale or invented
    value from a previous cycle."""
    reconciled_by_id: dict[str, ReconciledModel] = {}
    if reconciliation is not None:
        for model in reconciliation.models:
            reconciled_by_id[model.id] = model

    entries = tuple(
        AccessSnapshotEntry(
            id=entry.id,
            status=entry.status,
            metadata_status=entry.metadata_status,
            first_seen_at=entry.first_seen_at,
            last_seen_callable_at=entry.last_seen_callable_at,
            fields=(
                reconciled_by_id[entry.id].fields
                if entry.status == CatalogEntryStatus.CALLABLE and entry.id in reconciled_by_id
                else _EMPTY_FIELDS
            ),
        )
        for entry in catalog_snapshot.entries
    )
    return AccessSnapshotView(
        entries=entries,
        access_state=catalog_snapshot.access_state,
        last_successful_check_at=catalog_snapshot.last_successful_check_at,
        last_checked_at=catalog_snapshot.last_checked_at,
        alert=catalog_snapshot.alert,
    )
