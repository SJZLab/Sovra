"""ComponentBinding / ComponentSyncOutbox domain schema (FTF-R002, Section
39/65/70/77.1/77.3/77.5/77.6).

Path refined from the task packet's own generic suggestion
(`apps/api/app/domain/component_bindings.py` +
`apps/api/app/services/component_access.py`) to
`apps/api/app/component_bindings/component_binding_schema.py` +
`component_binding_crud.py`, matching FTF-04A01/FTF-04A07's own real
`apps/api/app/<domain>/*_schema.py` / `*_crud.py` per-feature-package
layout precedent -- `domain/`/`services/` do not otherwise exist in this
repository's layout and this task's own resource does not belong under
any single existing feature package.

**This table's field names and enums are not this task's own invention --
they are FTF-R001's already-CERTIFIED, frozen contract.** FTF-R001 froze
`packages/component-contracts/architecture.schema.json`'s own
`componentBinding` definition (`component_key`, `security_zone_scope`,
`project_scope`, `deployment_mode`, `approved_endpoint`,
`secret_reference`, `network_policy_identity`, `health_result`,
`compatibility_lock_reference`) plus two real structural invariants
(Axolotl/Inspect AI must be `worker_package` with a null endpoint; LiteLLM
must be `existing_gateway`) -- this is Section 77.5's own real
`ComponentBinding` entity, not a separate abstract declaration this task
is free to re-derive its own shape for. Per CLAUDE.md's own default
resolution rule ("check whether the architecture already fixes... the
answer... match it... without asking"), this module's field names,
`health_result`'s exact three-value enum, and the two structural CHECK
constraints all match that frozen contract literally rather than
inventing a parallel, differently-named shape. `tests/tasks/
test_ftf_r001.py::_binding` is the same frozen contract's own worked
example and is used as this module's own real fixture data.

Real, additive columns beyond the frozen abstract shape (disclosed, not a
divergence from it -- `architecture.schema.json`'s own
`additionalProperties: false` governs a validated instance *document*,
not this table's real SQL column list): `id`/`organization_id` (Section
65.1's own "All organization-scoped entities carry organization_id NOT
NULL" -- the real relational RLS/tenant key; `security_zone_scope` is the
frozen contract's own string field for the broader security-zone concept
Section 77.5 names, held at `'default'` for this deployment's single
security zone rather than conflated with the tenant key itself),
`health_checked_at`/`health_detail` (a real timestamp plus a richer,
content-free diagnostic string -- Section 77.6's own "no fabricated
success" needs more than a bare three-value enum to report *why*,
without widening the frozen enum itself), `row_version` (this task's own
real "optimistic versions" api_contract), and `created_at`/`updated_at`.

Scope (disclosed, not hidden): this task implements Section 77.5's own
`ComponentBinding`/`ComponentSyncOutbox` entities -- the "authenticated
internal component bindings and reconciled adapter foundation" itself --
not the seven components' own individual adapters (FTF-R003..R008, each a
separate, later, dependency-gated task). Project-level scope is deferred
(`project_scope` is always `null` for Release 1's first cut): `Project`
carries no `organization_id` column of its own (only reached via
`workspace_id` -> `Workspace.organization_id`), so a real tenant-safe
composite FK from a real `project_scope` value does not exist yet, and
Section 77.5 itself calls project scope "optional." `compatibility_lock_reference`
is a real, non-empty string column, not yet a real FK to a live
`ComponentVersionLock` row: that table is deliberately not created here,
the same disclosed-scope precedent FTF-R001's own certification note
already established for `ExternalRunBinding`/`packages/integrations/`
("no live component run or adapter exists yet to back them"). Until
FTF-R003..R008 build real `ComponentVersionLock` rows, this column is
seeded with the binding's own `component_key` (matching
`test_ftf_r001.py`'s own worked example), a real, disclosed placeholder
value rather than an invented FK target.

`network_policy_identity` is a real, non-empty string identity column --
its actual enforcement (a real Kubernetes NetworkPolicy applying it) is
FTF-0902's own separate, later task ("Network-policy templates", disclosed
in this task's own docs/certifications/queue.md submission as
dependency-ready but deferred: it needs a real CNI-enforcing Kubernetes
cluster this sandbox does not have). This column stores the real,
stable identifier a future FTF-0902 binds a real policy to -- never a
claim that network isolation is already enforced.

`approved_endpoint` accepts two real shapes, matching
`test_ftf_r001.py::valid_bindings`'s own worked example literally: a full
`https://` URL (MLflow/vLLM/Argilla/LiteLLM, all real HTTP(S) services)
or a bare `host:port` gRPC target (Temporal's own real wire protocol --
Temporal does not speak HTTP on its client port at all, so requiring an
`https://` URL for it would reject the one real value Temporal's own
binding can ever have). `component_binding_crud.py::compute_health`
dials out for real only for the `https://`-shaped case; a `host:port`
gRPC target reports `health_result='unknown'` until FTF-R003's own
Temporal integration adds a real gRPC health-check call -- never a
fabricated `healthy` for a protocol this module cannot actually speak.

`secret_reference` reuses `app.litellm.connection_schema.validate_secret_ref`
directly (the same `vault://`/`openbao://`/`secret://`-reference-only
shape FTF-0201 already established) rather than re-deriving a second,
parallel "no raw secret value" primitive.

Real forward Alembic migration
(`apps/api/alembic/versions/0013_component_bindings.py`) FORCEs row-level
security scoped to the same `app.current_organization_id` tenant context
every prior migration in this chain already established, and grants the
restricted `sovra_app` role SELECT/INSERT/UPDATE -- never DELETE, since a
binding is superseded by a new `row_version`, never removed.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from packages.component_contracts.job_identity import ALLOWED_COMPONENT_KEYS
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError, validate_url_shape
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.organization_workspace_schema import Base
from app.litellm.connection_schema import LiteLLMConnectionValidationError, validate_secret_ref

__all__ = [
    "ALLOWED_COMPONENT_KEYS",
    "ALLOWED_DEPLOYMENT_MODES",
    "ALLOWED_HEALTH_RESULTS",
    "ALLOWED_SYNC_OUTBOX_STATUSES",
    "APPROVED_ENDPOINT_MAX_LENGTH",
    "DEFAULT_SECURITY_ZONE_SCOPE",
    "ComponentBinding",
    "ComponentBindingValidationError",
    "ComponentSyncOutbox",
    "component_binding_endpoint_policy",
    "is_http_endpoint",
    "validate_approved_endpoint_or_none",
    "validate_component_key",
    "validate_deployment_mode",
    "validate_health_result",
    "validate_network_policy_identity",
    "validate_secret_reference_or_none",
    "validate_security_zone_scope",
    "validate_sync_outbox_status",
]

APPROVED_ENDPOINT_MAX_LENGTH = 2048
NETWORK_POLICY_IDENTITY_MAX_LENGTH = 200
SECURITY_ZONE_SCOPE_MAX_LENGTH = 100
PROJECT_SCOPE_MAX_LENGTH = 200
COMPATIBILITY_LOCK_REFERENCE_MAX_LENGTH = 200
IDEMPOTENCY_KEY_MAX_LENGTH = 200
HEALTH_DETAIL_MAX_LENGTH = 200
LAST_ERROR_MAX_LENGTH = 500

# Section 77.3's own three real deployment/execution shapes -- matches
# architecture.schema.json's own `$defs.deploymentMode` enum exactly.
ALLOWED_DEPLOYMENT_MODES = ("internal_service", "worker_package", "existing_gateway")

# architecture.schema.json's own `$defs.componentBinding.properties.health_result`
# enum, exactly three values -- never widened here. Finer-grained real
# diagnostic detail lives in the separate, additive `health_detail` string
# column instead of growing this frozen enum.
ALLOWED_HEALTH_RESULTS = ("healthy", "unhealthy", "unknown")

_MODES_REQUIRING_ENDPOINT = frozenset({"internal_service", "existing_gateway"})
_WORKER_PACKAGE_ONLY_COMPONENT_KEYS = frozenset({"axolotl", "inspect_ai"})
_EXISTING_GATEWAY_ONLY_COMPONENT_KEYS = frozenset({"litellm"})

# Section 70.1: admission outcome is real and bounded -- `pending` until a
# worker reports a real terminal outcome.
ALLOWED_SYNC_OUTBOX_STATUSES = ("pending", "succeeded", "failed")

DEFAULT_SECURITY_ZONE_SCOPE = "default"

# Internal components (Section 77.1) commonly listen on non-standard
# ports (MLflow/Temporal/vLLM/Argilla default installs, none of which use
# 443) -- unlike `LiteLLMConnection` (always a public gateway on 443),
# this shape check allows any real TCP port; the real safety boundary is
# `url_policy.py`'s own host/private-range/cloud-metadata checks at dial
# time (`outbound_guard.py`), not a port restriction here.
_ALL_TCP_PORTS = frozenset(range(1, 65536))

_ENDPOINT_URL_POLICY = ConnectionPolicy(
    name="component-binding-endpoint-shape",
    allowed_schemes=frozenset({"https"}),
    allowed_ports=_ALL_TCP_PORTS,
)

# Temporal's own real client-port wire protocol is gRPC, not HTTP --
# `test_ftf_r001.py`'s own worked example uses a bare
# "temporal.internal.sovra.local:7233" value, never an `https://` URL.
# Matches a real hostname (letters/digits/dots/hyphens) followed by
# `:<port>`, structurally distinct from a URL (no `://`).
_HOST_PORT_PATTERN = re.compile(r"^[A-Za-z0-9.-]+:[0-9]{1,5}$")


class ComponentBindingValidationError(ValueError):
    """A field value violates this domain schema's own shape/invariant."""


def component_binding_endpoint_policy() -> ConnectionPolicy:
    """The fixed, https-only, no-userinfo shape policy every
    `https://`-shaped `approved_endpoint` is checked against at write
    time. Runtime dialing (redirects, private/loopback allowance for a
    real internal service, DNS-rebinding defense) is a separate, wider
    policy supplied by the caller of
    `component_binding_crud.py::validate_component_binding`."""
    return _ENDPOINT_URL_POLICY


def is_http_endpoint(value: str) -> bool:
    """True for an HTTP(S) URL shape (MLflow/vLLM/Argilla/LiteLLM, always
    `https://` once stored via `validate_approved_endpoint_or_none` -- a
    bare `http://` is recognized too, purely as a protocol-shape dispatch
    check for a genuinely non-TLS-terminated internal test target, never a
    storage-time security relaxation); false for Temporal's own real bare
    `host:port` gRPC target. Used by
    `component_binding_crud.py::compute_health` to decide whether a real
    HTTP health probe is even possible for this endpoint's own protocol."""
    return value.startswith("https://") or value.startswith("http://")


def validate_component_key(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_COMPONENT_KEYS:
        raise ComponentBindingValidationError(
            f"component_key must be one of {ALLOWED_COMPONENT_KEYS}: {value!r}"
        )
    return value


def validate_deployment_mode(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_DEPLOYMENT_MODES:
        raise ComponentBindingValidationError(
            f"deployment_mode must be one of {ALLOWED_DEPLOYMENT_MODES}: {value!r}"
        )
    return value


def validate_health_result(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_HEALTH_RESULTS:
        raise ComponentBindingValidationError(
            f"health_result must be one of {ALLOWED_HEALTH_RESULTS}: {value!r}"
        )
    return value


def validate_sync_outbox_status(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_SYNC_OUTBOX_STATUSES:
        raise ComponentBindingValidationError(
            f"status must be one of {ALLOWED_SYNC_OUTBOX_STATUSES}: {value!r}"
        )
    return value


def validate_security_zone_scope(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ComponentBindingValidationError("security_zone_scope must be a non-empty string")
    if len(value) > SECURITY_ZONE_SCOPE_MAX_LENGTH:
        raise ComponentBindingValidationError(
            f"security_zone_scope must be at most {SECURITY_ZONE_SCOPE_MAX_LENGTH} characters"
        )
    return value


def validate_project_scope_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ComponentBindingValidationError("project_scope must be a non-empty string or null")
    if len(value) > PROJECT_SCOPE_MAX_LENGTH:
        raise ComponentBindingValidationError(
            f"project_scope must be at most {PROJECT_SCOPE_MAX_LENGTH} characters"
        )
    return value


def validate_network_policy_identity(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ComponentBindingValidationError("network_policy_identity must be a non-empty string")
    if len(value) > NETWORK_POLICY_IDENTITY_MAX_LENGTH:
        raise ComponentBindingValidationError(
            "network_policy_identity must be at most "
            f"{NETWORK_POLICY_IDENTITY_MAX_LENGTH} characters"
        )
    return value


def validate_compatibility_lock_reference(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ComponentBindingValidationError(
            "compatibility_lock_reference must be a non-empty string"
        )
    if len(value) > COMPATIBILITY_LOCK_REFERENCE_MAX_LENGTH:
        raise ComponentBindingValidationError(
            "compatibility_lock_reference must be at most "
            f"{COMPATIBILITY_LOCK_REFERENCE_MAX_LENGTH} characters"
        )
    return value


def validate_approved_endpoint_or_none(value: object) -> str | None:
    """Shape-only (Section 65.4's storage-time half). Accepts either a
    real `https://` URL (validated via the shared `url_policy` shape
    check -- the same primitive `outbound_guard.py` itself validates
    against before ever dialing) or Temporal's own real bare `host:port`
    gRPC target -- see this module's own docstring for why both shapes
    are real, not a laxer catch-all."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ComponentBindingValidationError(
            "approved_endpoint must be a non-empty string or null"
        )
    if len(value) > APPROVED_ENDPOINT_MAX_LENGTH:
        raise ComponentBindingValidationError(
            f"approved_endpoint must be at most {APPROVED_ENDPOINT_MAX_LENGTH} characters"
        )
    if is_http_endpoint(value):
        try:
            validate_url_shape(value, _ENDPOINT_URL_POLICY)
        except OutboundPolicyError as exc:
            raise ComponentBindingValidationError(
                f"approved_endpoint is not permitted: {exc}"
            ) from exc
        return value
    if _HOST_PORT_PATTERN.match(value):
        return value
    raise ComponentBindingValidationError(
        "approved_endpoint must be a real https:// URL or a real 'host:port' "
        f"gRPC target (Temporal's own wire protocol): {value!r}"
    )


def validate_secret_reference_or_none(value: object) -> str | None:
    """Composes `app.litellm.connection_schema.validate_secret_ref`
    directly -- no new, duplicate "reference-only secret" primitive."""
    if value is None:
        return None
    try:
        return validate_secret_ref(value)
    except LiteLLMConnectionValidationError as exc:
        raise ComponentBindingValidationError(str(exc)) from exc


class ComponentBinding(Base):
    """FTF-R001's own frozen `ComponentBinding` contract
    (`packages/component-contracts/architecture.schema.json`), implemented
    as a real, tenant-scoped Postgres table. See this module's own
    docstring for the field-by-field mapping and the real, additive
    columns beyond the frozen abstract shape.
    """

    __tablename__ = "component_bindings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_key: Mapped[str] = mapped_column(String(32), nullable=False)
    security_zone_scope: Mapped[str] = mapped_column(
        String(SECURITY_ZONE_SCOPE_MAX_LENGTH), nullable=False
    )
    project_scope: Mapped[str | None] = mapped_column(
        String(PROJECT_SCOPE_MAX_LENGTH), nullable=True
    )
    deployment_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_endpoint: Mapped[str | None] = mapped_column(
        String(APPROVED_ENDPOINT_MAX_LENGTH), nullable=True
    )
    secret_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    network_policy_identity: Mapped[str] = mapped_column(
        String(NETWORK_POLICY_IDENTITY_MAX_LENGTH), nullable=False
    )
    health_result: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")
    health_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    health_detail: Mapped[str | None] = mapped_column(
        String(HEALTH_DETAIL_MAX_LENGTH), nullable=True
    )
    compatibility_lock_reference: Mapped[str] = mapped_column(
        String(COMPATIBILITY_LOCK_REFERENCE_MAX_LENGTH), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_component_bindings_organization_id_id"),
        UniqueConstraint(
            "organization_id",
            "component_key",
            name="uq_component_bindings_organization_id_component_key",
        ),
        CheckConstraint(
            "component_key in (" + ", ".join(f"'{v}'" for v in ALLOWED_COMPONENT_KEYS) + ")",
            name="ck_component_bindings_component_key",
        ),
        CheckConstraint(
            "deployment_mode in (" + ", ".join(f"'{v}'" for v in ALLOWED_DEPLOYMENT_MODES) + ")",
            name="ck_component_bindings_deployment_mode",
        ),
        CheckConstraint(
            "health_result in (" + ", ".join(f"'{v}'" for v in ALLOWED_HEALTH_RESULTS) + ")",
            name="ck_component_bindings_health_result",
        ),
        CheckConstraint(
            "deployment_mode != 'worker_package' OR approved_endpoint IS NULL",
            name="ck_component_bindings_worker_package_no_endpoint",
        ),
        CheckConstraint(
            "deployment_mode = 'worker_package' OR approved_endpoint IS NOT NULL",
            name="ck_component_bindings_service_requires_endpoint",
        ),
        # architecture.schema.json's own two `allOf`/`if`/`then` structural
        # invariants, real at the database level here.
        CheckConstraint(
            "component_key not in ('axolotl', 'inspect_ai') OR deployment_mode = 'worker_package'",
            name="ck_component_bindings_worker_components_are_worker_package",
        ),
        CheckConstraint(
            "component_key != 'litellm' OR deployment_mode = 'existing_gateway'",
            name="ck_component_bindings_litellm_is_existing_gateway",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "component_key" in kwargs:
            kwargs["component_key"] = validate_component_key(kwargs["component_key"])
        if "deployment_mode" in kwargs:
            kwargs["deployment_mode"] = validate_deployment_mode(kwargs["deployment_mode"])
        if "security_zone_scope" in kwargs:
            kwargs["security_zone_scope"] = validate_security_zone_scope(
                kwargs["security_zone_scope"]
            )
        if "project_scope" in kwargs:
            kwargs["project_scope"] = validate_project_scope_or_none(kwargs["project_scope"])
        if "approved_endpoint" in kwargs:
            kwargs["approved_endpoint"] = validate_approved_endpoint_or_none(
                kwargs["approved_endpoint"]
            )
        if "secret_reference" in kwargs:
            kwargs["secret_reference"] = validate_secret_reference_or_none(
                kwargs["secret_reference"]
            )
        if "network_policy_identity" in kwargs:
            kwargs["network_policy_identity"] = validate_network_policy_identity(
                kwargs["network_policy_identity"]
            )
        if "compatibility_lock_reference" in kwargs:
            kwargs["compatibility_lock_reference"] = validate_compatibility_lock_reference(
                kwargs["compatibility_lock_reference"]
            )
        if "health_result" in kwargs:
            kwargs["health_result"] = validate_health_result(kwargs["health_result"])

        component_key = kwargs.get("component_key")
        mode = kwargs.get("deployment_mode")
        endpoint = kwargs.get("approved_endpoint")
        if mode in _MODES_REQUIRING_ENDPOINT and endpoint is None:
            raise ComponentBindingValidationError(
                f"deployment_mode={mode!r} requires a real approved_endpoint -- only "
                "'worker_package' has no invented API URL (Section 77.5)"
            )
        if mode == "worker_package" and endpoint is not None:
            raise ComponentBindingValidationError(
                "deployment_mode='worker_package' must not carry an approved_endpoint -- "
                "'Worker packages have no invented API URL' (Section 77.5)"
            )
        if component_key in _WORKER_PACKAGE_ONLY_COMPONENT_KEYS and mode != "worker_package":
            raise ComponentBindingValidationError(
                f"component_key={component_key!r} must use deployment_mode='worker_package' "
                "-- Axolotl/Inspect AI are worker-executed software, not always-on "
                "HTTP services (Section 77.1, architecture.schema.json)"
            )
        if component_key in _EXISTING_GATEWAY_ONLY_COMPONENT_KEYS and mode != "existing_gateway":
            raise ComponentBindingValidationError(
                f"component_key={component_key!r} must use deployment_mode='existing_gateway' "
                "-- the existing LiteLLM gateway is reused as-is, never replaced by a "
                "newly deployed internal service (Section 77.3, architecture.schema.json)"
            )
        super().__init__(**kwargs)


class ComponentSyncOutbox(Base):
    """Section 70.1/77.5 -- idempotent mapping from one Factory job to one
    external component-side identity, plus its own real, observable
    pending/failure state. This task's own second acceptance bullet.

    Unique on `(organization_id, component_binding_id, factory_job_id)`:
    the same (component, job) pair always resolves to the same outbox
    row -- a second `record_sync_outcome` call for the identical pair is a
    real idempotent replay (Section 70.1: "replay with same fingerprint
    returns prior result"), enforced by `component_binding_crud.py`'s own
    fetch-or-create fetching this exact row before ever inserting, backed
    by this real unique constraint as the database-level guarantee a
    concurrent double-insert cannot race past.
    """

    __tablename__ = "component_sync_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    factory_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(IDEMPOTENCY_KEY_MAX_LENGTH), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(LAST_ERROR_MAX_LENGTH), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "component_binding_id",
            "factory_job_id",
            name="uq_component_sync_outbox_org_binding_job",
        ),
        CheckConstraint(
            "status in (" + ", ".join(f"'{v}'" for v in ALLOWED_SYNC_OUTBOX_STATUSES) + ")",
            name="ck_component_sync_outbox_status",
        ),
        CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="ck_component_sync_outbox_resolved_pairs_with_status",
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        if "status" in kwargs:
            kwargs["status"] = validate_sync_outbox_status(kwargs["status"])
        if "idempotency_key" in kwargs:
            key = kwargs["idempotency_key"]
            if not isinstance(key, str) or not key:
                raise ComponentBindingValidationError("idempotency_key must be a non-empty string")
            if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
                raise ComponentBindingValidationError(
                    f"idempotency_key must be at most {IDEMPOTENCY_KEY_MAX_LENGTH} characters"
                )
        super().__init__(**kwargs)
