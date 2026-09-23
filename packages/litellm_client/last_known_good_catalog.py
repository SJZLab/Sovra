"""LiteLLM last-known-good catalog (FTF-0208, Section 3.3/65/77/81).

`packages/litellm_client/v1_models_discovery.py` (FTF-0204, CERTIFIED)
already computes one fresh `ModelDiscoveryResult` per `GET /v1/models`
round trip and never retains or merges a previous result -- by design, a
name absent from the current response is simply absent from that single
result, with nothing there to keep it visible for audit or to know
whether "gone" means "genuinely removed" or "the gateway is having an
outage right now." Section 3.3 ("Historical catalog is not current
authority") is the separate, later, stateful layer this task builds:
something a caller keeps across repeated discovery calls, that (a) lets a
name that just disappeared still be inspected/displayed as history, while
(b) never treating that history as authorization to call, queue, or admit
new work against it.

This task's own acceptance clause ("Persist historical catalog and mark
stale/inaccessible separately from callable entries") is `CatalogEntry`'s
own `status` field: every id this catalog has ever seen keeps a row
(`CatalogSnapshot.entries`, for audit/display), and that row is always
exactly one of `callable` / `stale` / `removed` -- never conflated, never
inferred by a caller diffing two raw discovery results itself.

Section 3.3's "On a successful authoritative list response, removed names
immediately become unselectable even if deletion is suspicious" is
`_apply_successful`: any id this catalog previously considered
authorized (`callable` or `stale` -- i.e. not already `removed`) that is
absent from a *successful* fresh `GET /v1/models` response is marked
`removed` in the very same update, unconditionally. The same clause's
"even if deletion is suspicious" is why an unexpected >50% drop only ever
sets `CatalogSnapshot.alert` (a disclosed, content-free string for a human
to review) -- it never restores or retains the dropped ids' `callable`
status, and it never blocks the removal from taking effect.

Section 3.3's "On 401/403 or key revocation, disable new calls and queued
job admission immediately" plus "Never retry access denial as a transient
network issue" is `_apply_revocation`: a `DiscoveryState.INVALID_TOKEN`
result immediately sets `access_state=revoked` and downgrades every
currently-`callable` entry to `stale` -- `is_call_authorized` then refuses
every id unconditionally the instant `access_state` is `revoked`, without
needing the freshness TTL to elapse first (revocation is a definite
signal, not an ordinary staleness case). This is deliberately distinct
from an ordinary connectivity/malformed-response/secret failure
(`_apply_unknown_failure`, `access_state=degraded`): Section 3.3's
"Refresh/probe failure cannot turn unknown access into success" cuts both
ways here -- an unconfirmed failure does not confirm a revocation either,
so existing entries and `last_successful_check_at` are left untouched,
and admission is governed purely by the freshness TTL below, exactly as
"After TTL expires during an outage, do not admit new paid work" requires.

This task's own negative_acceptance ("A last-known-good snapshot cannot
authorize queued/new inference after revocation or freshness expiration")
is `is_call_authorized`'s own two unconditional early-return checks
(`access_revoked`, `freshness_expired`), both reached before this
catalog's per-entry callable status is even consulted -- neither a
revoked connection's nor a stale (TTL-expired) connection's previously
`callable` entries can ever produce `authorized=True`, no matter what a
caller passes as `model_id`.

Default fresh-access TTL (Section 3.3) is `DEFAULT_FRESHNESS_TTL` (5
minutes), overridable per call for a test or a differently-configured
caller -- never hard-coded past this module's own boundary.

No independent schema change (this task's own `database_changes` clause):
`LastKnownGoodCatalog` is an in-memory, no-I/O object a caller constructs
once and feeds successive `ModelDiscoveryResult`s into (typically the
output of repeated `discover_models` calls); it never itself calls the
gateway or persists to a database. Scope decision (disclosed, same
precedent FTF-0202/FTF-0205/FTF-0207 already established for "no live
route/caller yet"): wiring this catalog into a durable per-connection
service and an actual periodic polling loop is `docs/TASK_INDEX.md`'s own
later "Periodic sync workflow" task (FTF-0215) and the API routes that
read its snapshot -- not this task's own `files` list. `proving_symbol`
stays blank and `product_truth_check.py`'s product-true count is
deliberately unchanged.

Every reported `alert` string is content-free (Section observability: "no
secrets/raw prompts in task reports") -- only ids and counts, never a
credential, raw response body, or raw exception message.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum

from packages.litellm_client.v1_models_discovery import (
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)

__all__ = [
    "AccessState",
    "AuthorizationDecision",
    "AuthorizationReason",
    "CatalogEntry",
    "CatalogEntryStatus",
    "CatalogSnapshot",
    "DEFAULT_FRESHNESS_TTL",
    "LastKnownGoodCatalog",
]

DEFAULT_FRESHNESS_TTL = timedelta(minutes=5)

# Section 3.3: "Unexpected >50% reduction raises an alert" -- a drop to
# strictly less than half the previously active set is "unexpected".
_MAJOR_REDUCTION_THRESHOLD = 0.5


class AccessState(str, Enum):
    """Section 3.3: the connection's own health as of the last discovery
    attempt -- independent of any single entry's own status."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    REVOKED = "revoked"
    DEGRADED = "degraded"


class CatalogEntryStatus(str, Enum):
    """This task's own acceptance clause: "mark stale/inaccessible
    separately from callable entries" -- a row is always exactly one of
    these, never inferred by a caller diffing raw results itself."""

    CALLABLE = "callable"
    STALE = "stale"
    REMOVED = "removed"


class AuthorizationReason(str, Enum):
    CALLABLE = "callable"
    ACCESS_REVOKED = "access_revoked"
    NO_SUCCESSFUL_DISCOVERY_YET = "no_successful_discovery_yet"
    FRESHNESS_EXPIRED = "freshness_expired"
    NOT_IN_CATALOG = "not_in_catalog"
    ENTRY_NOT_CALLABLE = "entry_not_callable"


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    metadata_status: ModelMetadataStatus
    status: CatalogEntryStatus
    first_seen_at: datetime
    last_seen_callable_at: datetime | None


@dataclass(frozen=True)
class CatalogSnapshot:
    entries: tuple[CatalogEntry, ...]
    access_state: AccessState
    last_successful_check_at: datetime | None
    last_checked_at: datetime | None
    alert: str | None


@dataclass(frozen=True)
class AuthorizationDecision:
    authorized: bool
    reason: AuthorizationReason


class LastKnownGoodCatalog:
    """Stateful last-known-good view over successive `discover_models`
    results for one LiteLLM connection. Not thread-safe by itself -- a
    caller serializes updates per connection, same convention every
    other stateful Factory component already assumes at this layer."""

    def __init__(self) -> None:
        self._entries: dict[str, CatalogEntry] = {}
        self._access_state: AccessState = AccessState.UNKNOWN
        self._last_successful_check_at: datetime | None = None
        self._last_checked_at: datetime | None = None
        self._alert: str | None = None

    def apply_discovery_result(
        self, result: ModelDiscoveryResult, *, now: datetime | None = None
    ) -> CatalogSnapshot:
        checked_at = now if now is not None else datetime.now(UTC)
        self._last_checked_at = checked_at

        if result.state == DiscoveryState.DISCOVERED:
            self._apply_successful(result, checked_at)
        elif result.state == DiscoveryState.INVALID_TOKEN:
            self._apply_revocation()
        else:
            self._apply_unknown_failure()

        return self.snapshot()

    def _apply_successful(self, result: ModelDiscoveryResult, checked_at: datetime) -> None:
        previous_active_ids = {
            entry_id
            for entry_id, entry in self._entries.items()
            if entry.status in (CatalogEntryStatus.CALLABLE, CatalogEntryStatus.STALE)
        }
        new_ids = {model.id for model in result.models}

        removed_ids = previous_active_ids - new_ids
        for removed_id in removed_ids:
            self._entries[removed_id] = replace(
                self._entries[removed_id], status=CatalogEntryStatus.REMOVED
            )

        for model in result.models:
            existing = self._entries.get(model.id)
            first_seen_at = existing.first_seen_at if existing is not None else checked_at
            self._entries[model.id] = CatalogEntry(
                id=model.id,
                metadata_status=model.metadata_status,
                status=CatalogEntryStatus.CALLABLE,
                first_seen_at=first_seen_at,
                last_seen_callable_at=checked_at,
            )

        self._access_state = AccessState.HEALTHY
        self._last_successful_check_at = checked_at

        previous_count = len(previous_active_ids)
        new_count = len(new_ids)
        if previous_count > 0 and new_count < previous_count * _MAJOR_REDUCTION_THRESHOLD:
            self._alert = (
                f"authorized model count dropped from {previous_count} to {new_count} in "
                "one discovery cycle -- inventory requires review; permissions were not "
                "restored"
            )
        else:
            self._alert = None

    def _apply_revocation(self) -> None:
        self._access_state = AccessState.REVOKED
        for entry_id, entry in self._entries.items():
            if entry.status == CatalogEntryStatus.CALLABLE:
                self._entries[entry_id] = replace(entry, status=CatalogEntryStatus.STALE)

    def _apply_unknown_failure(self) -> None:
        if self._access_state != AccessState.REVOKED:
            self._access_state = AccessState.DEGRADED

    def snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot(
            entries=tuple(sorted(self._entries.values(), key=lambda entry: entry.id)),
            access_state=self._access_state,
            last_successful_check_at=self._last_successful_check_at,
            last_checked_at=self._last_checked_at,
            alert=self._alert,
        )

    def is_call_authorized(
        self,
        model_id: str,
        *,
        now: datetime | None = None,
        freshness_ttl: timedelta = DEFAULT_FRESHNESS_TTL,
    ) -> AuthorizationDecision:
        checked_at = now if now is not None else datetime.now(UTC)

        if self._access_state == AccessState.REVOKED:
            return AuthorizationDecision(False, AuthorizationReason.ACCESS_REVOKED)
        if self._last_successful_check_at is None:
            return AuthorizationDecision(False, AuthorizationReason.NO_SUCCESSFUL_DISCOVERY_YET)
        if checked_at - self._last_successful_check_at >= freshness_ttl:
            return AuthorizationDecision(False, AuthorizationReason.FRESHNESS_EXPIRED)

        entry = self._entries.get(model_id)
        if entry is None:
            return AuthorizationDecision(False, AuthorizationReason.NOT_IN_CATALOG)
        if entry.status != CatalogEntryStatus.CALLABLE:
            return AuthorizationDecision(False, AuthorizationReason.ENTRY_NOT_CALLABLE)
        return AuthorizationDecision(True, AuthorizationReason.CALLABLE)
