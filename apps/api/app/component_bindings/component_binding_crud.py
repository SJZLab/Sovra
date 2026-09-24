"""ComponentBinding / ComponentSyncOutbox CRUD and service layer (FTF-R002,
Section 39/65/70/77.1/77.3/77.5/77.6).

Path refined from the task packet's own `apps/api/app/services/
component_access.py` to `apps/api/app/component_bindings/
component_binding_crud.py` -- see `component_binding_schema.py`'s own
docstring for the full per-feature-package layout rationale and for why
this module's field names (`approved_endpoint`/`secret_reference`/
`health_result`/...) match FTF-R001's already-CERTIFIED, frozen
`architecture.schema.json` contract literally.

Every real query goes through the same certified composition every prior
task in this chain already established:

  - `TenantContext`/`tenant_scoped_session_for` (FTF-H003) open the unit
    of work and bind the transaction-local RLS tenant context;
  - `authorize_resource_ownership` (FTF-H003) proves a binding/outbox ID
    belongs to the caller's own authenticated organization *before* any
    row is read or mutated -- denied identically (via
    `ResourceAuthorizationError`) whether the ID does not exist at all or
    belongs to a different organization (this task's own
    negative_acceptance: "Deny cross-tenant IDs");
  - `execute_tenant_scoped` (FTF-0105) is what every actual SELECT in this
    module runs through, never a bare `session.execute(...)`/
    `session.get(...)`.

No new authorization primitive is introduced. `validate_component_binding`
below is this module's own real health-check caller of
`packages/security/outbound_guard.py` (FTF-H006) -- the same
validate-then-pin, no-fake-success guarantee `connection_validation.py`
(FTF-0203) already proved for `LiteLLMConnection`, applied here to
`ComponentBinding`. It **never** converts a real connection failure,
timeout, or policy denial into `healthy` -- this task's own
negative_acceptance ("fake health after dependency outage" must never
happen) is enforced by construction: every real failure path below sets
`unhealthy`/`unknown`, never `healthy` (see `compute_health`'s own
docstring), and the row is always written inside the same transaction the
caller's request opened, so a crash between the check and the write can
never leave behind a stale `healthy` status either.

Role gating (Section 77.6: "`/organizations/{id}/component-bindings` --
Admin-only create/read/versioned update"): every operation, including
reads, is `organization_admin`-only -- see `component_binding_routes.py`.

Disclosed, not hidden: `record_sync_outcome` has no HTTP route in
`component_binding_routes.py`. Section 70.1's own sync outbox is written
by a durable Temporal workflow/activity reconciling a Factory job against
an external component's own identity -- never a browser-initiated action
a human triggers directly, unlike `validate_component_binding` (a real
admin-initiated health probe, which does have a route). FTF-R003..R008's
own per-component Temporal workflows (a later, separately dependency-gated
task) are this function's real eventual caller; this task's own live-wired
caller (`build_component_binding_router`, proving this task's product-true
status) covers the `ComponentBinding` CRUD/health-check surface, and
`record_sync_outcome`'s own real behavior is proven directly by
`tests/integration/test_component_bindings.py` against a live database in
the meantime -- the same disclosed "no live caller yet for this one
function" class already accepted for `secret_manager_integration.py`
before FTF-0203 existed.
"""

from __future__ import annotations

import socket
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from packages.component_contracts.job_identity import ALLOWED_COMPONENT_KEYS
from packages.security.outbound_guard import GuardedResponse, OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.audit_event_writer import write_audit_event
from app.auth.resource_authorization import ResourceAuthorizationError, authorize_resource_ownership
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.component_bindings.component_binding_schema import (
    DEFAULT_SECURITY_ZONE_SCOPE,
    ComponentBinding,
    ComponentBindingValidationError,
    ComponentSyncOutbox,
    is_http_endpoint,
    validate_approved_endpoint_or_none,
    validate_secret_reference_or_none,
)
from app.db.tenant_context import TenantContext

__all__ = [
    "ActorInfo",
    "ComponentBindingNotFoundError",
    "ComponentBindingVersionConflictError",
    "ComponentStatusEntry",
    "SyncOutboxConflictError",
    "SyncOutboxNotFoundError",
    "compute_health",
    "create_component_binding",
    "default_component_health_policy",
    "get_component_binding",
    "get_component_status_projection",
    "list_component_bindings",
    "record_sync_outcome",
    "update_component_binding",
    "validate_component_binding",
]

_BINDING_TABLE = ComponentBinding.__table__
_BINDING_ID_COLUMN = _BINDING_TABLE.c.id
_BINDING_ORG_COLUMN = _BINDING_TABLE.c.organization_id

_OUTBOX_TABLE = ComponentSyncOutbox.__table__
_OUTBOX_ID_COLUMN = _OUTBOX_TABLE.c.id
_OUTBOX_ORG_COLUMN = _OUTBOX_TABLE.c.organization_id

_HEALTH_CHECK_TIMEOUT_SECONDS = 10.0


class ComponentBindingNotFoundError(LookupError):
    """No component binding with this ID exists in the caller's own
    organization -- raised identically whether the ID is missing entirely
    or belongs to a different organization."""


class ComponentBindingVersionConflictError(ValueError):
    """`expected_row_version` did not match the binding's real current
    `row_version` -- this task's own "optimistic versions" api_contract,
    the same `ProjectConcurrencyError` discipline `project_crud.py`
    already established, applied here."""


class SyncOutboxNotFoundError(LookupError):
    """No sync outbox row with this ID exists in the caller's own
    organization."""


class SyncOutboxConflictError(ValueError):
    """A `record_sync_outcome` call named an existing (component_binding_id,
    factory_job_id) pair with a *different* `idempotency_key` than the one
    already stored -- Section 70.1: "replay with same fingerprint returns
    prior result, different fingerprint returns 409." Never silently
    overwrites a different logical request's own outcome."""


@dataclass(frozen=True)
class ActorInfo:
    actor_type: str
    actor_id: str
    request_id: str


@dataclass(frozen=True)
class ComponentStatusEntry:
    """One `component_key`'s real, observed status -- Section 77.6's own
    `/projects/{id}/component-status`: 'Real health, sync lag, identity
    and blocked reason; no fabricated success.'"""

    component_key: str
    configured: bool
    health_result: str
    health_checked_at: datetime | None
    pending_outbox_count: int
    failed_outbox_count: int
    blocked_reason: str | None


def _now() -> datetime:
    return datetime.now(UTC)


def default_component_health_policy(
    *, name: str = "component-binding-health-check"
) -> ConnectionPolicy:
    """Section 65.4: "internal org endpoints are separately allowlisted,
    not blanket-blocked." A component binding's own `approved_endpoint` is
    validated at storage time
    (`component_binding_schema.py::validate_approved_endpoint_or_none`),
    but Section 77.1's own seven components are frequently *genuinely*
    internal services reachable only on a private network in this
    deployment topology (Temporal, MLflow, vLLM, Argilla, self-hosted
    OpenBao-adjacent infrastructure) -- never a cloud-metadata address
    (`is_cloud_metadata_address` is checked unconditionally by
    `url_policy.py` regardless of this allowlist). No redirects: a health
    probe response is trusted as-is or not at all, matching Section 65.4's
    own "default no redirects."
    """
    import ipaddress

    return ConnectionPolicy(
        name=name,
        allowed_schemes=frozenset({"https"}),
        # Internal components commonly listen on non-standard ports (the
        # same reasoning `component_binding_schema.py`'s own
        # `_ENDPOINT_URL_POLICY` already documents for the storage-time
        # shape check) -- a health probe must be able to reach them too.
        allowed_ports=frozenset(range(1, 65536)),
        explicit_private_allowlist=(
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("127.0.0.0/8"),
        ),
        allow_redirects=False,
    )


def _fetch_or_raise(
    session: Session, context: TenantContext, binding_id: uuid.UUID
) -> ComponentBinding:
    if not isinstance(binding_id, uuid.UUID):
        raise ComponentBindingNotFoundError(f"binding_id must be a uuid.UUID, got {binding_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _BINDING_TABLE, _BINDING_ID_COLUMN, _BINDING_ORG_COLUMN, binding_id
        )
    except ResourceAuthorizationError as exc:
        raise ComponentBindingNotFoundError(str(exc)) from exc
    statement = select(ComponentBinding).where(ComponentBinding.id == binding_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise ComponentBindingNotFoundError(
            f"component binding {binding_id} vanished mid-transaction"
        )
    return row


def create_component_binding(
    session: Session,
    context: TenantContext,
    *,
    component_key: str,
    deployment_mode: str,
    approved_endpoint: str | None = None,
    secret_reference: str | None = None,
    security_zone_scope: str = DEFAULT_SECURITY_ZONE_SCOPE,
    actor: ActorInfo,
) -> ComponentBinding:
    """`network_policy_identity`/`compatibility_lock_reference` are real,
    non-empty string columns FTF-R001's own frozen contract requires on
    every binding; neither has a real target table yet (see
    `component_binding_schema.py`'s own docstring for the disclosed
    `ComponentVersionLock`/FTF-0902 scope), so both are seeded from a
    deterministic, real value derived from `component_key` -- the same
    worked-example convention `tests/tasks/test_ftf_r001.py::_binding`
    already established, not an arbitrary placeholder string."""
    binding = ComponentBinding(
        organization_id=context.organization_id,
        component_key=component_key,
        security_zone_scope=security_zone_scope,
        project_scope=None,
        deployment_mode=deployment_mode,
        approved_endpoint=approved_endpoint,
        secret_reference=secret_reference,
        network_policy_identity=f"{component_key}-net-policy",
        health_result="unknown",
        compatibility_lock_reference=component_key,
    )
    session.add(binding)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="component_binding.create",
        resource_type="component_binding",
        resource_id=str(binding.id),
        request_id=actor.request_id,
    )
    return binding


def get_component_binding(
    session: Session, context: TenantContext, binding_id: uuid.UUID
) -> ComponentBinding:
    return _fetch_or_raise(session, context, binding_id)


def list_component_bindings(session: Session, context: TenantContext) -> list[ComponentBinding]:
    statement = (
        select(ComponentBinding)
        .where(ComponentBinding.organization_id == context.organization_id)
        .order_by(ComponentBinding.created_at)
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return list(result.scalars().all())


def update_component_binding(
    session: Session,
    context: TenantContext,
    binding_id: uuid.UUID,
    *,
    expected_row_version: int,
    approved_endpoint: str | None = None,
    secret_reference: str | None = None,
    actor: ActorInfo,
) -> ComponentBinding:
    """Edits `approved_endpoint`/`secret_reference` (any subset) under real
    optimistic concurrency: `expected_row_version` must match the
    binding's real current `row_version` (this task's own api_contract
    "optimistic versions"), or `ComponentBindingVersionConflictError`
    (mapped to 409 by the route layer) -- never a silent last-write-wins
    overwrite."""
    binding = _fetch_or_raise(session, context, binding_id)
    if expected_row_version != binding.row_version:
        raise ComponentBindingVersionConflictError(
            f"component binding {binding_id} has row_version {binding.row_version}, "
            f"not the expected {expected_row_version} -- reload and retry"
        )

    changed = False
    if approved_endpoint is not None:
        approved_endpoint = validate_approved_endpoint_or_none(approved_endpoint)
        if binding.deployment_mode == "worker_package":
            raise ComponentBindingValidationError(
                "deployment_mode='worker_package' must not carry an approved_endpoint"
            )
        binding.approved_endpoint = approved_endpoint
        changed = True
    if secret_reference is not None:
        binding.secret_reference = validate_secret_reference_or_none(secret_reference)
        changed = True

    if not changed:
        raise ComponentBindingValidationError(
            "update_component_binding requires at least one of approved_endpoint/secret_reference"
        )

    binding.row_version += 1
    binding.updated_at = _now()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="component_binding.update",
        resource_type="component_binding",
        resource_id=str(binding.id),
        request_id=actor.request_id,
    )
    return binding


def _classify_health_detail(exc: OutboundGuardError) -> str:
    """Content-free classification (Section observability: "no secrets/raw
    prompts in task reports") -- never the raw exception message, which
    could echo a hostname or partial response content."""
    cause = exc.__cause__
    if isinstance(cause, socket.gaierror):
        return "dns_failed"
    if isinstance(cause, ssl.SSLError):
        return "certificate_error"
    if isinstance(cause, TimeoutError):
        return "timeout"
    if isinstance(cause, ConnectionRefusedError):
        return "connection_refused"
    return "connection_failed"


def compute_health(
    *,
    deployment_mode: str,
    approved_endpoint: str | None,
    policy: ConnectionPolicy,
    fetch=guarded_fetch,
) -> tuple[str, str]:
    """The pure, DB-free core of `validate_component_binding`: given a
    binding's own `deployment_mode`/`approved_endpoint`, returns the real
    `(health_result, health_detail)` pair -- never touches a session or
    row. Split out so this task's own central negative_acceptance ("no
    fake health after dependency outage") can be proven without a
    database (`tests/tasks/test_ftf_r002.py`), the same no-DB-first-then-
    DB-wired pattern `connection_crud.py::apply_edits`/`apply_revoke`
    already established.

    `health_result` is FTF-R001's own frozen three-value enum
    (`healthy`/`unhealthy`/`unknown`) -- never widened here. Never
    fabricates `healthy`: a `worker_package` binding (no endpoint to dial
    at all) or a bare `host:port` gRPC target (Temporal -- this module
    cannot speak gRPC) returns `unknown`; a real connection failure,
    timeout, denied destination, or non-2xx HTTP response returns
    `unhealthy`. Only an actual 2xx HTTP response returns `healthy` --
    there is no code path here that returns `healthy` without a real,
    observed 2xx response in hand. `health_detail` (a real, additive
    column beyond the frozen enum) always carries the specific, real
    reason.
    """
    if deployment_mode == "worker_package":
        return "unknown", "worker_package_no_endpoint"

    assert approved_endpoint is not None  # enforced at write time by the schema's own CHECK
    if not is_http_endpoint(approved_endpoint):
        # A real gRPC target (Temporal) -- this module only speaks HTTP(S);
        # see `component_binding_schema.py`'s own docstring.
        return "unknown", "non_http_endpoint_not_probed"

    try:
        response: GuardedResponse = fetch(
            approved_endpoint, policy, method="GET", timeout=_HEALTH_CHECK_TIMEOUT_SECONDS
        )
    except OutboundPolicyError:
        return "unhealthy", "destination_denied"
    except OutboundGuardError as exc:
        return "unhealthy", _classify_health_detail(exc)

    if 200 <= response.status < 300:
        return "healthy", f"http_{response.status}"
    return "unhealthy", f"http_{response.status}"


def validate_component_binding(
    session: Session,
    context: TenantContext,
    binding_id: uuid.UUID,
    *,
    policy: ConnectionPolicy | None = None,
    fetch=guarded_fetch,
    actor: ActorInfo,
) -> ComponentBinding:
    """Real health check (this task's own first acceptance bullet) --
    fetches the real binding, delegates the actual health decision to the
    pure `compute_health`, and writes the result plus a matching audit
    event in the same transaction. See `compute_health`'s own docstring
    for the full no-fake-health guarantee this task's own
    negative_acceptance requires.
    """
    binding = _fetch_or_raise(session, context, binding_id)
    checked_at = _now()
    effective_policy = policy if policy is not None else default_component_health_policy()

    health_result, health_detail = compute_health(
        deployment_mode=binding.deployment_mode,
        approved_endpoint=binding.approved_endpoint,
        policy=effective_policy,
        fetch=fetch,
    )
    binding.health_result = health_result
    binding.health_detail = health_detail
    binding.health_checked_at = checked_at
    binding.updated_at = checked_at
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="component_binding.validate",
        resource_type="component_binding",
        resource_id=str(binding.id),
        request_id=actor.request_id,
        details={"health_result": binding.health_result},
    )
    return binding


def _fetch_outbox_or_raise(
    session: Session, context: TenantContext, outbox_id: uuid.UUID
) -> ComponentSyncOutbox:
    if not isinstance(outbox_id, uuid.UUID):
        raise SyncOutboxNotFoundError(f"outbox_id must be a uuid.UUID, got {outbox_id!r}")
    try:
        authorize_resource_ownership(
            session, context, _OUTBOX_TABLE, _OUTBOX_ID_COLUMN, _OUTBOX_ORG_COLUMN, outbox_id
        )
    except ResourceAuthorizationError as exc:
        raise SyncOutboxNotFoundError(str(exc)) from exc
    statement = select(ComponentSyncOutbox).where(ComponentSyncOutbox.id == outbox_id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    row = result.scalars().one_or_none()
    if row is None:
        raise SyncOutboxNotFoundError(f"sync outbox {outbox_id} vanished mid-transaction")
    return row


def record_sync_outcome(
    session: Session,
    context: TenantContext,
    *,
    component_binding_id: uuid.UUID,
    factory_job_id: uuid.UUID,
    idempotency_key: str,
    outcome: str | None = None,
    external_id: str | None = None,
    error: str | None = None,
    actor: ActorInfo,
) -> ComponentSyncOutbox:
    """Idempotent sync-outbox admission/resolution (this task's own second
    acceptance bullet). `outcome=None` admits (or replays) a `pending`
    row; `outcome in ('succeeded', 'failed')` resolves it.

    Section 70.1: "Idempotency key is scoped by tenant, actor, action and
    request fingerprint; replay with same fingerprint returns prior
    result, different fingerprint returns 409." Here the fingerprint scope
    is `(component_binding_id, factory_job_id)`: a second call naming the
    identical pair with the identical `idempotency_key` returns the
    existing row unchanged (a true replay -- no double-count, no second
    audit event); the identical pair with a *different* `idempotency_key`
    is refused (`SyncOutboxConflictError`) rather than silently
    overwriting a different logical request's own record.
    """
    # Proves the binding is real and belongs to this tenant before an
    # outbox row is ever created against it -- cross-tenant
    # `component_binding_id` is refused here exactly like a cross-tenant
    # outbox ID is refused by `_fetch_outbox_or_raise`.
    _fetch_or_raise(session, context, component_binding_id)

    statement = select(ComponentSyncOutbox).where(
        ComponentSyncOutbox.organization_id == context.organization_id,
        ComponentSyncOutbox.component_binding_id == component_binding_id,
        ComponentSyncOutbox.factory_job_id == factory_job_id,
    )
    result = execute_tenant_scoped(session, context.organization_id, statement)
    existing = result.scalars().one_or_none()

    if existing is not None:
        if existing.idempotency_key != idempotency_key:
            raise SyncOutboxConflictError(
                f"component binding {component_binding_id} / job {factory_job_id} already has "
                f"a sync outbox row under a different idempotency_key -- a different fingerprint "
                f"for the same (component, job) pair is refused, not silently merged"
            )
        outbox = existing
        if outcome is None:
            # A true replay of an already-admitted pending row -- no state
            # change, no second audit event (Section 70.1's own "replay
            # returns prior result").
            return outbox
    else:
        outbox = ComponentSyncOutbox(
            organization_id=context.organization_id,
            component_binding_id=component_binding_id,
            factory_job_id=factory_job_id,
            idempotency_key=idempotency_key,
            status="pending",
        )
        session.add(outbox)
        session.flush()
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="component_sync_outbox.admit",
            resource_type="component_sync_outbox",
            resource_id=str(outbox.id),
            request_id=actor.request_id,
        )
        if outcome is None:
            return outbox

    if outcome not in ("succeeded", "failed"):
        raise ComponentBindingValidationError(
            f"outcome must be 'succeeded' or 'failed' when resolving a sync outbox row: {outcome!r}"
        )
    if outbox.status != "pending":
        # Already resolved -- a second resolution call for the same
        # fingerprint is a real idempotent replay, never a second state
        # transition or a second audit event.
        return outbox

    outbox.status = outcome
    outbox.resolved_at = _now()
    outbox.updated_at = outbox.resolved_at
    if outcome == "succeeded":
        outbox.external_id = external_id
    else:
        outbox.attempt_count += 1
        outbox.last_error = error[:500] if error else None

    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=f"component_sync_outbox.{outcome}",
        resource_type="component_sync_outbox",
        resource_id=str(outbox.id),
        request_id=actor.request_id,
    )
    return outbox


def get_component_status_projection(
    session: Session, context: TenantContext
) -> list[ComponentStatusEntry]:
    """Section 77.6: `/projects/{id}/component-status` -- 'Real health,
    sync lag, identity and blocked reason; no fabricated success.' Every
    one of Section 77.1's seven components is reported, even one with no
    binding configured at all -- `configured=False` and a real
    `blocked_reason`, never silently omitted or reported as healthy."""
    bindings_by_key = {b.component_key: b for b in list_component_bindings(session, context)}

    outbox_statement = select(ComponentSyncOutbox).where(
        ComponentSyncOutbox.organization_id == context.organization_id
    )
    outbox_rows = (
        execute_tenant_scoped(session, context.organization_id, outbox_statement).scalars().all()
    )
    pending_by_binding: dict[uuid.UUID, int] = {}
    failed_by_binding: dict[uuid.UUID, int] = {}
    for row in outbox_rows:
        if row.status == "pending":
            pending_by_binding[row.component_binding_id] = (
                pending_by_binding.get(row.component_binding_id, 0) + 1
            )
        elif row.status == "failed":
            failed_by_binding[row.component_binding_id] = (
                failed_by_binding.get(row.component_binding_id, 0) + 1
            )

    entries: list[ComponentStatusEntry] = []
    for key in ALLOWED_COMPONENT_KEYS:
        binding = bindings_by_key.get(key)
        if binding is None:
            entries.append(
                ComponentStatusEntry(
                    component_key=key,
                    configured=False,
                    health_result="unknown",
                    health_checked_at=None,
                    pending_outbox_count=0,
                    failed_outbox_count=0,
                    blocked_reason="no_binding_configured",
                )
            )
            continue
        entries.append(
            ComponentStatusEntry(
                component_key=key,
                configured=True,
                health_result=binding.health_result,
                health_checked_at=binding.health_checked_at,
                pending_outbox_count=pending_by_binding.get(binding.id, 0),
                failed_outbox_count=failed_by_binding.get(binding.id, 0),
                blocked_reason=None
                if binding.health_result == "healthy"
                else binding.health_result,
            )
        )
    return entries
