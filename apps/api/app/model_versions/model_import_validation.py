"""Import-validation activity: job claim, lease fencing and endpoint-only import
(FTF-H010-P2, Section 3.3-3.5, 6.17, 17 fencing paragraph, 41/77.6 activity rules, 58A row 1,
68.1 paragraphs 1-2).

Two plain functions a durable dispatcher calls, one job at a time:

- `claim_model_import_job(...)` takes the per-job lease and returns a `ClaimToken`.
- `validate_endpoint_import(...)` validates an `external_endpoint` job through its gateway
  connection and writes exactly one terminal outcome.

Jobs are addressed per job by (organization_id, project_id, job_id), which is what a dispatcher
derives from the enqueue. Nothing here scans across tenants: every database step is its own
tenant-scoped transaction (`tenant_scoped_session_for`), so RLS plus the explicit org/project/id
predicates bound every read and write.

Fencing. A claim sets `claim_epoch = previous + 1` and a lease. A live lease refuses a second
claim (`job_already_claimed`); an expired lease may be re-claimed with epoch + 1. The terminal
write is one conditional UPDATE `... WHERE claim_epoch = <token epoch> AND state = 'validating'`:
a holder whose lease was taken over matches 0 rows, the whole transaction (the ModelVersion
insert and the audit row included) rolls back, and the caller gets `fenced_out` with nothing
written. The migration's trigger pins the same rules in the database (0029).

Outcomes (Law 17: a provider failure, outage or unknown state stays visible as blocked or failed,
never converted to success). `discovery_outcome` and `attestation_outcome` are pure tables over
every `DiscoveryState` / `RouteAttestationState` value; any state not listed fails closed to
`blocked_external`.

Success writes, in one transaction, an endpoint-only ModelVersion: `external_provider_model_ref`
verbatim, `endpoint_reference` NULL (Control-plane ruling 2026-10-10T03:57Z: never a fabricated
URL and never the connection `base_url`), the attested deployment fingerprint, lineage always
`unknown` (Section 68.1: display unknown parent/training data as unknown; a declared parent is
kept as declared only), and no artifact, digest, revision, tokenizer or template. The gateway
calls run outside any database transaction: no row lock is held across a network call.

Every error carries a stable code and never caller text (the ref, fingerprint, base URL and
secret never reach an exception, a failure code or an audit row).

Disclosed: UNWIRED. No Temporal or outbox dispatcher calls claim/validate yet (durable dispatch
is FTF-0801/H016), so deployed jobs stay visibly `queued`. FTF-1001's synchronous
`POST /model-versions` can still create an unvalidated endpoint row; P4's actions route denies
such rows (`import_not_validated`).
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from packages.litellm_client.connection_validation import default_connection_validation_policy
from packages.litellm_client.route_attestation import (
    RouteAttestationState,
    RouteIdentityState,
    attest_route,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig
from packages.litellm_client.v1_models_discovery import DiscoveryState, discover_models
from packages.security.url_policy import ConnectionPolicy
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext, tenant_scoped_session_for
from app.litellm.connection_crud import ConnectionNotFoundError, get_connection
from app.litellm.connection_schema import LiteLLMConnection
from app.model_versions.model_import_schema import (
    ALLOWED_ORIGINS,
    ORIGIN_EXTERNAL_ENDPOINT,
    ModelImportJob,
)
from app.model_versions.model_version_crud import _next_version_number
from app.model_versions.model_version_schema import ModelVersion

__all__ = [
    "ClaimToken",
    "ImportOutcome",
    "ModelImportActivityError",
    "attestation_outcome",
    "claim_model_import_job",
    "discovery_outcome",
    "validate_endpoint_import",
]

STATE_FAILED = "failed"
STATE_SUCCEEDED = "succeeded"
STATE_BLOCKED_EXTERNAL = "blocked_external"
_TERMINAL_STATES = frozenset({STATE_FAILED, STATE_SUCCEEDED, STATE_BLOCKED_EXTERNAL})

CODE_CONNECTION_REVOKED = "connection_revoked"
CODE_GATEWAY_UNAVAILABLE = "gateway_unavailable"
CODE_GATEWAY_ACCESS_DENIED = "gateway_access_denied"
CODE_ALIAS_NOT_AUTHORIZED = "alias_not_authorized"
CODE_ROUTE_ATTESTATION_UNAVAILABLE = "route_attestation_unavailable"
CODE_ROUTE_IDENTITY_UNVERIFIED = "route_identity_unverified"

LEASE_SECONDS_MAX = 86_400
_WORKER_ID_MAX_LENGTH = 128  # the audit_events.actor_id width
_FINGERPRINT_MAX_LENGTH = 256
_DEFAULT_TIMEOUT_SECONDS = 10.0


class ModelImportActivityError(Exception):
    """A claim or validation could not proceed. The message is the stable `code` and nothing
    else: never the ref, fingerprint, base URL or secret.

    Codes: `invalid_argument`, `job_not_found`, `job_invalid`, `origin_not_claimable`,
    `job_terminal`, `job_already_claimed`, `fenced_out`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ClaimToken:
    organization_id: uuid.UUID
    project_id: uuid.UUID
    job_id: uuid.UUID
    epoch: int
    worker_id: str


@dataclass(frozen=True)
class ImportOutcome:
    state: str
    failure_code: str | None
    model_version_id: uuid.UUID | None


# ---------------------------------------------------------------------------
# Pure outcome tables (Law 17)
# ---------------------------------------------------------------------------


def discovery_outcome(state: DiscoveryState) -> tuple[str, str] | None:
    """`None` for DISCOVERED; otherwise the (terminal state, failure code) of every other
    state. A gateway 401/403 is the credential being refused (`failed`); every other state is a
    provider or secret-backend condition the Factory cannot decide on, so it is blocked, not
    failed and never succeeded."""
    if state is DiscoveryState.DISCOVERED:
        return None
    if state is DiscoveryState.INVALID_TOKEN:
        return STATE_FAILED, CODE_GATEWAY_ACCESS_DENIED
    return STATE_BLOCKED_EXTERNAL, CODE_GATEWAY_UNAVAILABLE


def attestation_outcome(
    state: RouteAttestationState,
    identity: RouteIdentityState,
    fingerprint: str | None,
    resolved_model: str | None,
    expected_model: str,
) -> tuple[str, str] | None:
    """`None` only for an ATTESTED route whose identity is VERIFIED, carries a usable fingerprint
    and whose resolved-model echo is exactly `expected_model`. Not attested: 401/403 is `failed`,
    a chat response with no resolved-model echo (MALFORMED_RESPONSE) is `failed`, everything else
    `blocked_external`. Attested but unverified (a missing header) or echoing another model is
    `failed` (Section 3.5: do not certify)."""
    if state is RouteAttestationState.INVALID_TOKEN:
        return STATE_FAILED, CODE_GATEWAY_ACCESS_DENIED
    if state is RouteAttestationState.MALFORMED_RESPONSE:
        return STATE_FAILED, CODE_ROUTE_IDENTITY_UNVERIFIED
    if state is not RouteAttestationState.ATTESTED:
        return STATE_BLOCKED_EXTERNAL, CODE_ROUTE_ATTESTATION_UNAVAILABLE
    if identity is not RouteIdentityState.VERIFIED or not _usable_fingerprint(fingerprint):
        return STATE_FAILED, CODE_ROUTE_IDENTITY_UNVERIFIED
    if not isinstance(resolved_model, str) or resolved_model != expected_model:
        return STATE_FAILED, CODE_ROUTE_IDENTITY_UNVERIFIED
    return None


def _usable_fingerprint(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= _FINGERPRINT_MAX_LENGTH
        and not any(unicodedata.category(ch) in ("Cc", "Cs") for ch in value)
        and not value.isspace()
    )


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


def _require_uuid(value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID):
        raise ModelImportActivityError("invalid_argument")
    return value


def _require_aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ModelImportActivityError("invalid_argument")
    return value


def _require_worker_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.isspace()
        or len(value) > _WORKER_ID_MAX_LENGTH
        or any(unicodedata.category(ch) in ("Cc", "Cs") for ch in value)
    ):
        raise ModelImportActivityError("invalid_argument")
    return value


def _require_origins(value: Iterable[object]) -> frozenset[str]:
    try:
        origins = frozenset(value)
    except TypeError:
        raise ModelImportActivityError("invalid_argument") from None
    known = frozenset(o for o in origins if isinstance(o, str) and o in ALLOWED_ORIGINS)
    if not known or known != origins:
        raise ModelImportActivityError("invalid_argument")
    return known


def _lease_until(now: datetime, lease_seconds: object) -> datetime:
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or not 0 < lease_seconds <= LEASE_SECONDS_MAX
    ):
        raise ModelImportActivityError("invalid_argument")
    return now + timedelta(seconds=lease_seconds)


def _load_job(
    session: Session, organization_id: uuid.UUID, project_id: uuid.UUID, job_id: uuid.UUID
) -> ModelImportJob | None:
    statement = (
        select(ModelImportJob)
        .where(
            ModelImportJob.id == job_id,
            ModelImportJob.organization_id == organization_id,
            ModelImportJob.project_id == project_id,
        )
        .with_for_update()
    )
    job: ModelImportJob | None = (
        execute_tenant_scoped(session, organization_id, statement).scalars().one_or_none()
    )
    return job


def claim_model_import_job(
    session_factory: Callable[[], Session],
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    origins: Iterable[str],
    worker_id: str,
    lease_seconds: int,
    now: datetime,
) -> ClaimToken:
    """Takes the lease on one job and returns its `ClaimToken`.

    Runs in its own tenant-scoped transaction with `SELECT ... FOR UPDATE`. Claims only the
    origins named in `origins` (P2 passes `external_endpoint` only). A live lease gives
    `job_already_claimed`; a terminal job gives `job_terminal`; an expired lease is re-claimed
    with epoch + 1. Raises `ModelImportActivityError`; on any raise nothing was written."""
    organization_id = _require_uuid(organization_id)
    project_id = _require_uuid(project_id)
    job_id = _require_uuid(job_id)
    allowed = _require_origins(origins)
    worker_id = _require_worker_id(worker_id)
    now = _require_aware(now)
    lease = _lease_until(now, lease_seconds)

    context = TenantContext(organization_id=organization_id)
    with tenant_scoped_session_for(session_factory, context) as session:
        job = _load_job(session, organization_id, project_id, job_id)
        if job is None:
            raise ModelImportActivityError("job_not_found")
        if job.requested_origin not in allowed:
            raise ModelImportActivityError("origin_not_claimable")
        if job.state in _TERMINAL_STATES:
            raise ModelImportActivityError("job_terminal")
        if job.requested_origin == ORIGIN_EXTERNAL_ENDPOINT and job.litellm_connection_id is None:
            # A legacy FTF-H010-P1 row that predates the connection column (0029).
            raise ModelImportActivityError("job_invalid")
        if job.state == "validating" and (
            job.lease_expires_at is None or job.lease_expires_at > now
        ):
            raise ModelImportActivityError("job_already_claimed")

        epoch = job.claim_epoch + 1
        statement = (
            update(ModelImportJob)
            .where(
                ModelImportJob.id == job_id,
                ModelImportJob.organization_id == organization_id,
                ModelImportJob.project_id == project_id,
                ModelImportJob.claim_epoch == job.claim_epoch,
                ModelImportJob.state == job.state,
            )
            .values(state="validating", claim_epoch=epoch, lease_expires_at=lease, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        try:
            result = execute_tenant_scoped(session, organization_id, statement)
        except DBAPIError:
            # The trigger refused (for example a re-claim against the database clock).
            raise ModelImportActivityError("job_already_claimed") from None
        if result.rowcount != 1:
            raise ModelImportActivityError("job_already_claimed")
    return ClaimToken(organization_id, project_id, job_id, epoch, worker_id)


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _JobSnapshot:
    created_by: str
    ref: str
    parent_model_version_id: uuid.UUID | None
    connection_id: uuid.UUID


@dataclass(frozen=True)
class _ConnectionSnapshot:
    base_url: str
    secret_ref: str
    revoked: bool


def _read_snapshots(
    session_factory: Callable[[], Session], token: ClaimToken
) -> tuple[_JobSnapshot, _ConnectionSnapshot | None]:
    context = TenantContext(organization_id=token.organization_id)
    with tenant_scoped_session_for(session_factory, context) as session:
        job = _load_job(session, token.organization_id, token.project_id, token.job_id)
        if job is None:
            raise ModelImportActivityError("job_not_found")
        if job.state != "validating" or job.claim_epoch != token.epoch:
            raise ModelImportActivityError("fenced_out")
        if (
            job.requested_origin != ORIGIN_EXTERNAL_ENDPOINT
            or job.litellm_connection_id is None
            or job.external_provider_model_ref is None
        ):
            raise ModelImportActivityError("origin_not_claimable")
        snapshot = _JobSnapshot(
            created_by=job.created_by,
            ref=job.external_provider_model_ref,
            parent_model_version_id=job.parent_model_version_id,
            connection_id=job.litellm_connection_id,
        )
        try:
            connection = get_connection(session, context, snapshot.connection_id)
        except ConnectionNotFoundError:
            return snapshot, None
        return snapshot, _ConnectionSnapshot(
            base_url=connection.base_url,
            secret_ref=connection.secret_ref,
            revoked=connection.status != "active" or connection.revoked_at is not None,
        )


def _connection_still_active(
    session: Session, context: TenantContext, connection_id: uuid.UUID
) -> bool:
    """Re-reads the connection inside the terminal transaction, share-locked so a concurrent
    revoke either commits first (seen here) or waits for this transaction."""
    statement = (
        select(LiteLLMConnection)
        .where(
            LiteLLMConnection.id == connection_id,
            LiteLLMConnection.organization_id == context.organization_id,
        )
        .with_for_update(read=True)
    )
    connection = (
        execute_tenant_scoped(session, context.organization_id, statement).scalars().first()
    )
    return (
        connection is not None and connection.status == "active" and connection.revoked_at is None
    )


class _Fenced(Exception):
    """Raised inside the terminal transaction so the session rolls everything back."""


def _finish(
    session_factory: Callable[[], Session],
    token: ClaimToken,
    snapshot: _JobSnapshot,
    *,
    state: str,
    failure_code: str | None,
    fingerprint: str | None,
    now: datetime,
) -> ImportOutcome:
    """One transaction: (success) the ModelVersion insert, the epoch-conditional terminal
    UPDATE, one audit row. 0 rows updated means this epoch was taken over: rollback."""
    context = TenantContext(organization_id=token.organization_id)
    model_version_id: uuid.UUID | None = None
    try:
        with tenant_scoped_session_for(session_factory, context) as session:
            if state == STATE_SUCCEEDED and not _connection_still_active(
                session, context, snapshot.connection_id
            ):
                # Revoked (or gone) while the gateway calls ran: no version is written.
                state, failure_code = STATE_FAILED, CODE_CONNECTION_REVOKED
            if state == STATE_SUCCEEDED:
                if session.get_bind().dialect.name == "postgresql":
                    # Serializes version-number allocation per project across concurrent
                    # activities (the unique (project_id, version_number) would otherwise race).
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": f"model_versions:{token.project_id}"},
                    )
                version = ModelVersion(
                    id=uuid.uuid4(),
                    created_at=now,
                    organization_id=token.organization_id,
                    project_id=token.project_id,
                    version_number=_next_version_number(session, context, token.project_id),
                    import_kind="endpoint",
                    origin=ORIGIN_EXTERNAL_ENDPOINT,
                    external_provider_model_ref=snapshot.ref,
                    observed_deployment_fingerprint=fingerprint,
                    lineage_completeness="unknown",
                    parent_model_version_id=snapshot.parent_model_version_id,
                    created_by=snapshot.created_by,
                )
                session.add(version)
                session.flush()
                model_version_id = version.id

            statement = (
                update(ModelImportJob)
                .where(
                    ModelImportJob.id == token.job_id,
                    ModelImportJob.organization_id == token.organization_id,
                    ModelImportJob.project_id == token.project_id,
                    ModelImportJob.claim_epoch == token.epoch,
                    ModelImportJob.state == "validating",
                )
                .values(
                    state=state,
                    failure_code=failure_code,
                    result_model_version_id=model_version_id,
                    lease_expires_at=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            result = execute_tenant_scoped(session, token.organization_id, statement)
            if result.rowcount != 1:
                raise _Fenced
            write_audit_event(
                session,
                organization_id=token.organization_id,
                actor_type="system",
                actor_id=token.worker_id,
                action="model_import.validate",
                resource_type="model_import_job",
                resource_id=str(token.job_id),
                request_id=uuid.uuid4().hex,
                details={
                    "job_id": str(token.job_id),
                    "origin": ORIGIN_EXTERNAL_ENDPOINT,
                    "outcome": state if failure_code is None else failure_code,
                },
            )
    except _Fenced:
        raise ModelImportActivityError("fenced_out") from None
    return ImportOutcome(state=state, failure_code=failure_code, model_version_id=model_version_id)


def validate_endpoint_import(
    session_factory: Callable[[], Session],
    token: ClaimToken,
    *,
    secret_config: OpenBaoConfig | None,
    now: datetime,
    policy_factory: Callable[[], ConnectionPolicy] = default_connection_validation_policy,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> ImportOutcome:
    """Validates one claimed `external_endpoint` job and writes its single terminal outcome.

    Loads the job and its connection itself (a revoked connection is failed
    `connection_revoked`), runs one FTF-0204 `discover_models` (the ref must be in the
    discovered ids by exact `==`, otherwise failed `alias_not_authorized`), then one FTF-H005
    `attest_route` for exactly that ref. Raises `ModelImportActivityError` (`fenced_out` when
    this token's epoch was taken over, in which case nothing was written)."""
    if not isinstance(token, ClaimToken):
        raise ModelImportActivityError("invalid_argument")
    now = _require_aware(now)

    snapshot, connection = _read_snapshots(session_factory, token)

    def finish(state: str, code: str | None, fingerprint: str | None = None) -> ImportOutcome:
        return _finish(
            session_factory,
            token,
            snapshot,
            state=state,
            failure_code=code,
            fingerprint=fingerprint,
            now=now,
        )

    if connection is None or connection.revoked:
        return finish(STATE_FAILED, CODE_CONNECTION_REVOKED)

    discovery = discover_models(
        base_url=connection.base_url,
        secret_ref=connection.secret_ref,
        secret_config=secret_config,
        policy=policy_factory(),
        timeout=timeout,
        now=now,
    )
    refused = discovery_outcome(discovery.state)
    if refused is not None:
        return finish(refused[0], refused[1])
    if not any(model.id == snapshot.ref for model in discovery.models):
        return finish(STATE_FAILED, CODE_ALIAS_NOT_AUTHORIZED)

    attestation = attest_route(
        base_url=connection.base_url,
        model_id=snapshot.ref,
        approved_model_ids=frozenset({snapshot.ref}),
        secret_ref=connection.secret_ref,
        secret_config=secret_config,
        policy=policy_factory(),
        timeout=timeout,
        now=now,
    )
    refused = attestation_outcome(
        attestation.state,
        attestation.route_identity_state,
        attestation.deployment_fingerprint,
        attestation.resolved_model,
        snapshot.ref,
    )
    if refused is not None:
        return finish(refused[0], refused[1])
    return finish(STATE_SUCCEEDED, None, attestation.deployment_fingerprint)
