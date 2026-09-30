# LiteLLM outage runbook (FTF-1807)

Section 3.3/3.4/3.5/3.6 require tested outage/revocation/fallback/identity
handling and safe routing recovery for the reused LiteLLM gateway, and forbid
any last-known-good snapshot, cache hit, or retried failure from silently
reinstating access or masking a real outage. This document is the
human-readable summary of the real, already-certified library behavior that
governs each of those cases. It never restates a claim those modules'
own code and tests don't already make — read them directly for the binding
implementation.

**Disclosure (Law 5/6/7 — existence is not evidence, designed is not
wired):** every module named below is a pure, in-memory, no-I/O component
with its own already-CERTIFIED test suite; none of them yet has a live
route, scheduler, or job-execution caller in this repository (each
module's own docstring discloses this "no live caller yet" scope
directly — see `packages/litellm_client/periodic_sync_workflow.py`, which
`FTF-0215` names below as the real wiring task, still unwired itself past
this point). This runbook describes the real, tested contract these
components enforce today; it is not a claim that a live incident-response
system is wired end-to-end yet. Operators wiring a real scheduler, job
queue, or admin UI against these modules must preserve every guarantee
below — none of them may be weakened by a future wiring change without a
new, independently reviewed task.

## 1. What "outage" means, precisely

`packages/litellm_client/last_known_good_catalog.py::LastKnownGoodCatalog`
(FTF-0208, CERTIFIED) tracks one connection's own `AccessState`, which is
always exactly one of:

- `HEALTHY` — the most recent `GET /v1/models` call succeeded.
- `REVOKED` — the most recent call returned `DiscoveryState.INVALID_TOKEN`
  (401/403 or a revoked key). A **definite** signal, never retried as
  transient.
- `DEGRADED` — the most recent call failed for any other reason (timeout,
  DNS failure, connection refused, malformed response, a secret-resolution
  failure). An **unconfirmed** signal: it does not confirm a revocation,
  and existing catalog entries are left untouched while degraded.
- `UNKNOWN` — no discovery call has ever succeeded for this connection.

## 2. Revocation handling

On `INVALID_TOKEN`, `LastKnownGoodCatalog._apply_revocation` immediately
sets `access_state=REVOKED` and downgrades every currently-`callable`
catalog entry to `stale`. `is_call_authorized` then refuses every model
id unconditionally the instant `access_state` is `revoked` — this check is
reached before any per-entry status is even consulted, and it does **not**
wait for the freshness TTL to elapse first, because revocation is a
definite signal rather than ordinary staleness.

`packages/litellm_client/access_guard.py::require_model_access` (FTF-H004,
CERTIFIED) composes three checks, in this exact order, before any caller
may treat a model as callable:

1. **Ownership** — `authorize_resource_ownership` against
   `litellm_connections`, so a caller authenticated into the wrong
   organization is refused before this guard looks at the connection's
   status or catalog at all (`ModelAccessDenialReason.CONNECTION_NOT_OWNED`).
2. **Connection-level revocation** — a fresh, tenant-scoped `SELECT` of
   `litellm_connections.status`, re-read on every call rather than trusted
   from a possibly-stale in-memory catalog
   (`ModelAccessDenialReason.CONNECTION_REVOKED`).
3. **Catalog-level authorization** — `LastKnownGoodCatalog.is_call_authorized`,
   unchanged and reused rather than re-implemented.

There is no cached "was authorized last time" flag anywhere in this guard —
every call re-reads real, current state, and a single `ModelAccessDeniedError`
is raised the instant any one check fails. A caller who fails step 1 never
learns anything about that connection's revocation state or catalog
contents from steps 2 or 3.

## 3. Freshness TTL — the outage admission cutoff

`LastKnownGoodCatalog` also refuses admission once
`DEFAULT_FRESHNESS_TTL` (**5 minutes**) has elapsed since the last
successful discovery call, independently of `access_state`
(`AuthorizationReason.FRESHNESS_EXPIRED`). This is the operative rule
during a prolonged outage: once 5 minutes pass with no successful refresh,
no new paid work is admitted against that connection's last-known-good
snapshot, even though the snapshot itself is untouched and still visible
for audit. The TTL is overridable per call (for a differently-configured
caller or a test) but is never hard-coded past this module's own boundary.

## 4. Bounded retries and persisted anomalies

`packages/litellm_client/catalog_anomaly_detector.py::CatalogAnomalyDetector`
(FTF-0209, CERTIFIED) tracks a `retry_ceiling` of **3 attempts**
(`DEFAULT_RETRY_CEILING`) — the same three-attempt ceiling
`packages/litellm_client/periodic_sync_workflow.py::PeriodicSyncWorkflow`
(FTF-0215, CERTIFIED) reuses for its own consecutive-transient-failure
counter, rather than inventing a second number. An unexpected reduction of
more than 50% of previously-active model ids opens or updates a persisted
`AnomalyRecord`, which survives across cycles until an explicit
`acknowledge(...)` call by an operator. `record(...)` always applies the
real discovery result to the wrapped catalog **unconditionally, before**
evaluating whether an anomaly should be opened — the detector's own retry
or acknowledgement bookkeeping never gates, delays, or short-circuits real
authorization. Acknowledging or retrying an anomaly is a pure audit-trail
transition: it never reaches into the catalog's entries and can never turn
an already-removed id back into an authorized one. Only a genuinely fresh,
successful discovery response listing that id again can do that.

## 5. Safe routing recovery — no out-of-order reinstatement

`PeriodicSyncWorkflow.complete_cycle` assigns every dispatched cycle a
monotonically increasing `sequence` from `begin_cycle()`. A completed
cycle is applied to the wrapped catalog/detector only if its sequence is
strictly newer than the last one actually applied, checked **before** any
catalog mutation. A slow discovery response that finally arrives after a
later cycle has already been applied — a genuinely concurrent sync, or an
out-of-order delivery of a stale response — is dropped entirely as
`SyncCycleStatus.SKIPPED_OUT_OF_ORDER`, even if it is a superficially
successful, fully populated response. It cannot resurrect an id a fresher
cycle already removed, and it cannot undo a fresher already-applied
revocation. This is "safe routing recovery" in this repository's own
tested sense: recovery from an outage happens only through a genuinely
newer, real discovery cycle — never through a retried or delayed stale one.

## 6. Fallback and identity handling — no unauthorized cache or silent reroute

`packages/litellm_client/route_attestation.py::attest_route` (FTF-H005,
CERTIFIED) is the second, independent signal beyond an alias name: a
LiteLLM router can silently fall back to a different deployment that still
answers under the identical alias. Every attestation request carries
LiteLLM's own real, documented `"cache": {"no-cache": true}` field, so an
identity attestation can never be silently answered from a cached
response — there is no cache-hit header this module needs to detect after
the fact, because the cache is bypassed by construction on every call.

`route_identity_state` is `VERIFIED` only when **both** a real
`deployment_fingerprint` (from the gateway's own documented
`x-litellm-model-id` response header) and a real `resolved_model` are
present in one response. Either one missing — an older gateway build, a
misconfigured proxy, a non-2xx or malformed outcome — leaves the identity
`UNVERIFIED`, and no fingerprint is fabricated to fill the gap. An
unverified identity cannot claim reproducible or private qualification:
this is this repository's own tested negative_acceptance for **"no
unauthorized cached access or unapproved external fallback during private
outage"** — a silent reroute to a different upstream deployment
during an outage is detected as a changed `deployment_fingerprint`, and a
response this module cannot positively verify is never treated as
equivalent to the pinned deployment.

## 7. Operator summary

| Signal | Detection | Effect |
|---|---|---|
| Key/connection revoked (401/403) | `DiscoveryState.INVALID_TOKEN` | Immediate, unconditional call refusal (`access_revoked`) |
| Connection revoked by admin | `litellm_connections.status != 'active'` | Immediate refusal (`CONNECTION_REVOKED`), independent of catalog state |
| No successful refresh for 5 minutes | `FRESHNESS_EXPIRED` | New work refused; existing snapshot kept for audit only |
| >50% model-count drop | Anomaly opened | Recorded for operator review; authorization from the fresh result still applies unconditionally |
| Transient network failure | `DEGRADED`, bounded 3-attempt retry counter | Existing entries untouched; no revocation inferred |
| Stale/out-of-order sync response | `sequence <= last_applied_sequence` | Dropped before it reaches the catalog; cannot reinstate access |
| Router silently reroutes to a different deployment | Changed/missing `x-litellm-model-id` fingerprint | Identity stays `UNVERIFIED`; never claimed equivalent to the pinned deployment |
| Response served from cache during attestation | Structurally impossible | Every attestation request carries `"cache": {"no-cache": true}` |

See `tests/documentation/test_ftf_1807.py` for the executable proof that
every row above is real, tested behavior of the cited modules, and not
merely asserted here.
