"""FTF-R005 -- durable MLflow sync outbox on the certified FTF-R002 component
outbox (Section 70.1, 77.5, 78.3).

Round 1 shipped the state machine against an in-memory store. This module
replaces that store with the real, tenant-RLS `component_sync_outbox` table
that FTF-R002 owns, through R002's own `record_sync_outcome` /
`ComponentBinding` -- no second table, no migration, no new writer of
admission or resolution. A Factory process restart therefore loses nothing:
the entry is a database row, not Python state.

Division of durable state (Section 78.3, "worker results first commit to
durable restricted storage and Factory evidence"):

* the **result** (metrics, params, hashes) is Factory evidence, loaded
  through the `ResultSource` protocol the wiring task implements over that
  evidence. The outbox never invents or keeps a second copy of a measurement;
* the **sync state** (pending / succeeded / failed, attempt count, last
  error, MLflow run id) is the R002 row. The idempotency key is
  `mlflow:<payload sha256>`, so a different result for the same job is
  refused by R002 (409 class) rather than silently replacing the queued one,
  and a result that changed in Factory evidence after admission is detected
  on load and failed visibly, never synced.

Bounded retries (Section 77.5): a transient outage keeps the row `pending`
and records the attempt and error; the next attempt is due `updated_at +
RetryPolicy.delay_ms(attempt_count)` (derived, so no schema change). At
`RetryPolicy.max_attempts` the row is resolved `failed` with a
`retry limit reached` error, which R002's component-status projection counts
as a failed outbox entry -- visible, never retried forever, never SYNCED.

Law 17: a row whose result cannot be loaded is resolved `failed`
(`result_missing`), never skipped and never reported synced.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.component_bindings.component_binding_crud import (
    ActorInfo,
    SyncOutboxConflictError,
    record_sync_outcome,
)
from app.component_bindings.component_binding_schema import (
    ComponentBinding,
    ComponentSyncOutbox,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from packages.integrations.mlflow.client import MlflowClient, TrackingRejectedError
from packages.integrations.mlflow.reconcile import (
    DEFAULT_RETRY_POLICY,
    OutboxEntry,
    RetryPolicy,
    RunConflictError,
    SyncState,
    TrackedRun,
    logical_run_key,
)
from packages.security.url_policy import ConnectionPolicy

__all__ = [
    "DurableOutbox",
    "ResultSource",
    "client_from_binding",
    "idempotency_key_for",
]

_LAST_ERROR_MAX = 500


class ResultSource(Protocol):
    """Factory evidence: the durable, restricted record of a measured result."""

    def load(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> TrackedRun | None: ...


def idempotency_key_for(run: TrackedRun) -> str:
    return f"mlflow:{run.payload_sha256()}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class DurableOutbox:
    """`OutboxStore` over one organization's MLflow `ComponentBinding`."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        context: TenantContext,
        *,
        binding_id: uuid.UUID,
        results: ResultSource,
        actor: ActorInfo,
        policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if not isinstance(binding_id, uuid.UUID):
            raise ValueError("binding_id must be a uuid.UUID")
        self._session_factory = session_factory
        self._context = context
        self._binding_id = binding_id
        self._results = results
        self._actor = actor
        self.policy = policy
        self._clock = clock

    # -- OutboxStore -------------------------------------------------------

    def add(self, run: TrackedRun) -> OutboxEntry:
        if run.organization_id != self._context.organization_id:
            raise RunConflictError("result belongs to a different organization")
        with tenant_scoped_session_for(self._session_factory, self._context) as session:
            try:
                row = record_sync_outcome(
                    session,
                    self._context,
                    component_binding_id=self._binding_id,
                    factory_job_id=run.job_id,
                    idempotency_key=idempotency_key_for(run),
                    actor=self._actor,
                )
            except SyncOutboxConflictError as exc:
                raise RunConflictError("a different result is already queued for this job") from exc
            return self._entry(row, run, due_tracking=False)

    def pending(self) -> list[OutboxEntry]:
        entries: list[OutboxEntry] = []
        for job_id in self._pending_job_ids():
            run = self._results.load(self._context.organization_id, job_id)
            if run is None:
                self._fail_unloadable(job_id, "result_missing: Factory evidence has no result")
                continue
            if run.organization_id != self._context.organization_id or run.job_id != job_id:
                self._fail_unloadable(job_id, "result_mismatch: evidence identity differs")
                continue
            with tenant_scoped_session_for(self._session_factory, self._context) as session:
                row = self._row(session, job_id)
                changed = row.idempotency_key != idempotency_key_for(run)
                if not changed:
                    entries.append(self._entry(row, run, due_tracking=True))
            if changed:
                self._fail_unloadable(job_id, "result_changed: evidence differs from admission")
        return entries

    def record(self, entry: OutboxEntry) -> None:
        run = entry.run
        key = idempotency_key_for(run)
        with tenant_scoped_session_for(self._session_factory, self._context) as session:
            if entry.state == SyncState.SYNCED:
                row = record_sync_outcome(
                    session,
                    self._context,
                    component_binding_id=self._binding_id,
                    factory_job_id=run.job_id,
                    idempotency_key=key,
                    outcome="succeeded",
                    external_id=entry.mlflow_run_id,
                    actor=self._actor,
                )
            elif entry.state == SyncState.PENDING:
                # Still pending: only the attempt and its error are recorded.
                row = record_sync_outcome(
                    session,
                    self._context,
                    component_binding_id=self._binding_id,
                    factory_job_id=run.job_id,
                    idempotency_key=key,
                    actor=self._actor,
                )
                row.last_error = (entry.last_error or "")[:_LAST_ERROR_MAX] or None
                row.updated_at = self._clock()
            else:
                prefix = "ambiguous: " if entry.state == SyncState.AMBIGUOUS else ""
                row = record_sync_outcome(
                    session,
                    self._context,
                    component_binding_id=self._binding_id,
                    factory_job_id=run.job_id,
                    idempotency_key=key,
                    outcome="failed",
                    error=prefix + (entry.last_error or "sync failed"),
                    actor=self._actor,
                )
            row.attempt_count = entry.attempts

    # -- internals ---------------------------------------------------------

    def _pending_job_ids(self) -> list[uuid.UUID]:
        with tenant_scoped_session_for(self._session_factory, self._context) as session:
            statement = (
                select(ComponentSyncOutbox.factory_job_id)
                .where(
                    ComponentSyncOutbox.organization_id == self._context.organization_id,
                    ComponentSyncOutbox.component_binding_id == self._binding_id,
                    ComponentSyncOutbox.status == "pending",
                )
                .order_by(ComponentSyncOutbox.created_at, ComponentSyncOutbox.id)
            )
            result = execute_tenant_scoped(session, self._context.organization_id, statement)
            return list(result.scalars().all())

    def _row(self, session: Session, job_id: uuid.UUID) -> ComponentSyncOutbox:
        statement = select(ComponentSyncOutbox).where(
            ComponentSyncOutbox.organization_id == self._context.organization_id,
            ComponentSyncOutbox.component_binding_id == self._binding_id,
            ComponentSyncOutbox.factory_job_id == job_id,
        )
        result = execute_tenant_scoped(session, self._context.organization_id, statement)
        return result.scalars().one()

    def _fail_unloadable(self, job_id: uuid.UUID, reason: str) -> None:
        with tenant_scoped_session_for(self._session_factory, self._context) as session:
            row = self._row(session, job_id)
            record_sync_outcome(
                session,
                self._context,
                component_binding_id=self._binding_id,
                factory_job_id=job_id,
                idempotency_key=row.idempotency_key,
                outcome="failed",
                error=reason,
                actor=self._actor,
            )

    def _entry(
        self, row: ComponentSyncOutbox, run: TrackedRun, *, due_tracking: bool
    ) -> OutboxEntry:
        # Only the pending sweep (`pending()`, which reads pending rows alone)
        # tracks when the next attempt is due; an admission replay never does.
        next_retry = None
        if due_tracking and row.attempt_count > 0:
            next_retry = _ms(row.updated_at) + self.policy.delay_ms(row.attempt_count)
        return OutboxEntry(
            entry_id=logical_run_key(run.organization_id, run.job_id),
            run=run,
            attempts=row.attempt_count,
            mlflow_run_id=row.external_id,
            last_error=row.last_error,
            next_retry_at_ms=next_retry,
        )


def client_from_binding(
    binding: ComponentBinding,
    *,
    policy: ConnectionPolicy,
    resolve_secret: Callable[[str], str] | None = None,
    timeout: float = 10.0,
) -> MlflowClient:
    """Builds the adapter from the tenant's own MLflow `ComponentBinding`
    (Section 77.1/77.5): the endpoint and secret reference come from the
    binding, never from a caller-supplied URL. The bearer token is resolved
    only here, handed to the client and never stored or logged."""
    if binding.component_key != "mlflow":
        raise TrackingRejectedError("binding is not the MLflow component")
    if binding.deployment_mode != "internal_service" or not binding.approved_endpoint:
        raise TrackingRejectedError("MLflow binding has no approved internal service endpoint")
    token = None
    if binding.secret_reference:
        if resolve_secret is None:
            raise TrackingRejectedError("binding has a secret reference but no resolver was given")
        token = resolve_secret(binding.secret_reference)
    try:
        return MlflowClient(binding.approved_endpoint, policy, token=token, timeout=timeout)
    except ValueError as exc:
        raise TrackingRejectedError(f"binding endpoint is not usable: {exc}") from None
