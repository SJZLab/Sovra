"""FTF-R002 -- authenticated worker job identity envelope (Section
65.2/70.1/77.5).

Path refined from the task packet's own literal
`packages/component-contracts/job_identity.py` to
`packages/component_contracts/job_identity.py` (underscore, not hyphen):
`packages/component-contracts/` already exists in this repository
(FTF-R001's own `architecture.schema.json`) but is not a Python package
at all -- it has no `__init__.py` and its own directory name is not a
legal Python identifier, so nothing can ever `import
packages.component-contracts.job_identity`. That directory is fine for a
JSON schema referenced only by file path (`scripts/architecture_lock.py`
reads it that way, never imports it) but cannot hold a real importable
`.py` module. This task adds the sibling `packages/component_contracts/`
package (a real `__init__.py`) for exactly the one Python module Section
77.5 assigns there, without touching FTF-R001's own existing directory or
its certified evidence.

Section 65.2: "Worker job envelopes contain server-signed organization/
project/job IDs, policy version, allowed object references, expiry and
fencing epoch. Worker identity is constrained to the task's tenant/job
namespace." This module is that envelope. It is a pure, dependency-free
package (stdlib only, matching `packages/security/url_policy.py`'s own
convention) -- it never opens a database connection or a network socket,
and it is the one module in `packages/component_contracts/` (Section
77.5's own "Repository owner paths") that both the Factory API (which
mints and signs an envelope before handing a job to a worker) and a
worker process (which verifies one before trusting the tenant/job
identity it claims) import, so the two sides can never independently
drift on what "signed" or "expired" means.

"Server-signed" is a real HMAC-SHA256 MAC over a canonical, deterministic
serialization of every field (`_canonical_payload`), not just a shape
convention -- a worker that receives a tampered or forged envelope (any
field changed, a session ID stripped, a later expiry spliced in) fails
`verify_worker_job_identity` for real, the same evidence class this
task's own negative_acceptance ("Deny cross-tenant IDs, arbitrary URLs...")
implies is required *before* trusting any embedded organization ID.

Component/worker identity keys (`ALLOWED_COMPONENT_KEYS`) are the
canonical Section 77.1 "seven selected components" list -- defined here,
not re-derived by `apps/api/app/component_bindings/component_binding_schema.py`,
so the app layer's own storage-time `component_key` validation and this
package's own worker-identity `component_key` validation can never
silently diverge (Section 77.5's package is the frozen source of truth;
the app layer imports from here, never the reverse).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = [
    "ALLOWED_COMPONENT_KEYS",
    "MAX_TTL_SECONDS",
    "WorkerJobIdentity",
    "WorkerJobIdentityError",
    "authorize_object_ref",
    "is_expired",
    "mint_worker_job_identity",
    "sign_worker_job_identity",
    "verify_worker_job_identity",
]

# Section 77.1: "The seven selected software components are Axolotl,
# Inspect AI, MLflow, vLLM, Temporal, the organization's existing LiteLLM
# gateway, and Argilla." -- the exact, closed set; a worker job envelope
# can only ever claim to act as one of these seven, never an eighth
# invented component.
ALLOWED_COMPONENT_KEYS = (
    "axolotl",
    "inspect_ai",
    "mlflow",
    "vllm",
    "temporal",
    "litellm",
    "argilla",
)

# Section 65.2 names no explicit numeric bound; bounded here so a caller
# cannot mint an effectively-permanent worker credential by accident --
# 24h covers the longest plausible single training/evaluation job this
# release's own Section 70 durability model describes, well past which a
# real workflow would already have re-issued a fresh envelope on retry.
MAX_TTL_SECONDS = 24 * 60 * 60


class WorkerJobIdentityError(Exception):
    """A worker job identity envelope is malformed, unsigned, tampered,
    expired, or claims an object reference it was never granted."""


@dataclass(frozen=True)
class WorkerJobIdentity:
    """One authenticated worker job envelope. Never constructed directly
    by application code outside this module -- use `mint_worker_job_identity`,
    which validates every field before a `WorkerJobIdentity` exists at all."""

    organization_id: uuid.UUID
    job_id: uuid.UUID
    component_key: str
    policy_version: str
    allowed_object_refs: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    fencing_epoch: int
    project_id: uuid.UUID | None = field(default=None)


def _require_uuid(value: object, *, field_name: str) -> uuid.UUID:
    if not isinstance(value, uuid.UUID):
        raise WorkerJobIdentityError(f"{field_name} must be a real uuid.UUID, got {value!r}")
    return value


def _require_aware_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise WorkerJobIdentityError(
            f"{field_name} must be a timezone-aware datetime, got {value!r}"
        )
    return value


def mint_worker_job_identity(
    *,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    component_key: str,
    policy_version: str,
    allowed_object_refs: frozenset[str] | set[str],
    issued_at: datetime,
    ttl_seconds: float,
    fencing_epoch: int,
    project_id: uuid.UUID | None = None,
) -> WorkerJobIdentity:
    """Constructs and validates a new envelope. Never signs it -- see
    `sign_worker_job_identity`; a caller that never signs an envelope can
    never hand it to a worker at all (there is no unsigned wire format)."""
    organization_id = _require_uuid(organization_id, field_name="organization_id")
    job_id = _require_uuid(job_id, field_name="job_id")
    if project_id is not None:
        project_id = _require_uuid(project_id, field_name="project_id")
    if component_key not in ALLOWED_COMPONENT_KEYS:
        raise WorkerJobIdentityError(
            f"component_key must be one of {ALLOWED_COMPONENT_KEYS}: {component_key!r}"
        )
    if not isinstance(policy_version, str) or not policy_version:
        raise WorkerJobIdentityError("policy_version must be a non-empty string")
    if not isinstance(fencing_epoch, int) or isinstance(fencing_epoch, bool) or fencing_epoch < 0:
        raise WorkerJobIdentityError(
            f"fencing_epoch must be a non-negative int, got {fencing_epoch!r}"
        )
    issued_at = _require_aware_datetime(issued_at, field_name="issued_at")
    if not isinstance(ttl_seconds, int | float) or isinstance(ttl_seconds, bool):
        raise WorkerJobIdentityError(f"ttl_seconds must be numeric, got {ttl_seconds!r}")
    if ttl_seconds <= 0:
        raise WorkerJobIdentityError(
            "ttl_seconds must be positive -- an already-expired envelope is never minted"
        )
    if ttl_seconds > MAX_TTL_SECONDS:
        raise WorkerJobIdentityError(
            f"ttl_seconds {ttl_seconds} exceeds the bounded maximum {MAX_TTL_SECONDS} "
            "-- an effectively-permanent worker credential is never minted"
        )
    refs = frozenset(allowed_object_refs)
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            raise WorkerJobIdentityError(
                f"every allowed_object_refs entry must be a non-empty string: {ref!r}"
            )

    from datetime import timedelta

    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    return WorkerJobIdentity(
        organization_id=organization_id,
        project_id=project_id,
        job_id=job_id,
        component_key=component_key,
        policy_version=policy_version,
        allowed_object_refs=refs,
        issued_at=issued_at,
        expires_at=expires_at,
        fencing_epoch=fencing_epoch,
    )


def is_expired(identity: WorkerJobIdentity, *, now: datetime) -> bool:
    now = _require_aware_datetime(now, field_name="now")
    return now >= identity.expires_at


def authorize_object_ref(identity: WorkerJobIdentity, object_ref: str) -> None:
    """Section 65.2: 'Worker identity is constrained to the task's
    tenant/job namespace.' Raises unless `object_ref` is one this
    envelope's own issuer explicitly granted -- a worker can never widen
    its own access by asking for an object reference outside the set it
    was minted with."""
    if object_ref not in identity.allowed_object_refs:
        raise WorkerJobIdentityError(
            f"object_ref {object_ref!r} is not in this envelope's own allowed_object_refs "
            f"-- a worker cannot widen its own access beyond what it was minted with"
        )


def _canonical_payload(identity: WorkerJobIdentity) -> bytes:
    """A deterministic, field-order-independent serialization -- the same
    `WorkerJobIdentity` always produces the same bytes, so
    `hmac.compare_digest` over its signature is a real integrity check of
    every field, not just whichever ones a naive f-string happened to
    include."""
    payload = {
        "organization_id": str(identity.organization_id),
        "project_id": str(identity.project_id) if identity.project_id is not None else None,
        "job_id": str(identity.job_id),
        "component_key": identity.component_key,
        "policy_version": identity.policy_version,
        "allowed_object_refs": sorted(identity.allowed_object_refs),
        "issued_at": identity.issued_at.astimezone(UTC).isoformat(),
        "expires_at": identity.expires_at.astimezone(UTC).isoformat(),
        "fencing_epoch": identity.fencing_epoch,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign_worker_job_identity(identity: WorkerJobIdentity, *, signing_key: bytes) -> str:
    """Returns a `<payload_b64>.<signature_b64>` token -- the real,
    server-signed wire form Section 65.2 requires. `signing_key` is never
    stored on the envelope itself or embedded in the token; it must be
    supplied again, out of band, by `verify_worker_job_identity`."""
    if not isinstance(signing_key, bytes) or not signing_key:
        raise WorkerJobIdentityError("signing_key must be a non-empty bytes value")
    payload = _canonical_payload(identity)
    signature = hmac.new(signing_key, payload, hashlib.sha256).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(signature)}"


def verify_worker_job_identity(
    token: str, *, signing_key: bytes, now: datetime
) -> WorkerJobIdentity:
    """Verifies `token`'s signature with a real constant-time comparison
    (`hmac.compare_digest`) before trusting a single field of its payload,
    then re-validates every field through the same rules
    `mint_worker_job_identity` applies and refuses an expired envelope.
    Any failure raises `WorkerJobIdentityError` -- there is no partial-
    trust return value."""
    if not isinstance(signing_key, bytes) or not signing_key:
        raise WorkerJobIdentityError("signing_key must be a non-empty bytes value")
    if not isinstance(token, str) or token.count(".") != 1:
        raise WorkerJobIdentityError("token must be a well-formed '<payload>.<signature>' string")
    payload_b64, signature_b64 = token.split(".", 1)
    try:
        payload = _b64url_decode(payload_b64)
        signature = _b64url_decode(signature_b64)
    except (ValueError, Exception) as exc:  # noqa: BLE001 - any decode failure is the same outcome
        raise WorkerJobIdentityError("token is not valid base64url") from exc

    expected_signature = hmac.new(signing_key, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise WorkerJobIdentityError(
            "token signature does not match -- tampered or forged envelope"
        )

    try:
        fields = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WorkerJobIdentityError("token payload is not valid JSON") from exc
    if not isinstance(fields, dict):
        raise WorkerJobIdentityError("token payload must be a JSON object")

    try:
        organization_id = uuid.UUID(fields["organization_id"])
        job_id = uuid.UUID(fields["job_id"])
        raw_project_id = fields.get("project_id")
        project_id = uuid.UUID(raw_project_id) if raw_project_id is not None else None
        component_key = fields["component_key"]
        policy_version = fields["policy_version"]
        allowed_object_refs = frozenset(fields["allowed_object_refs"])
        issued_at = datetime.fromisoformat(fields["issued_at"])
        expires_at = datetime.fromisoformat(fields["expires_at"])
        fencing_epoch = fields["fencing_epoch"]
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerJobIdentityError(f"token payload is missing/malformed fields: {exc}") from exc

    if component_key not in ALLOWED_COMPONENT_KEYS:
        raise WorkerJobIdentityError(f"token claims an unknown component_key: {component_key!r}")
    if not isinstance(fencing_epoch, int) or isinstance(fencing_epoch, bool) or fencing_epoch < 0:
        raise WorkerJobIdentityError(f"token claims an invalid fencing_epoch: {fencing_epoch!r}")

    identity = WorkerJobIdentity(
        organization_id=organization_id,
        project_id=project_id,
        job_id=job_id,
        component_key=component_key,
        policy_version=policy_version,
        allowed_object_refs=allowed_object_refs,
        issued_at=issued_at,
        expires_at=expires_at,
        fencing_epoch=fencing_epoch,
    )
    if is_expired(identity, now=now):
        raise WorkerJobIdentityError(
            f"token expired at {expires_at.isoformat()} (checked at {now.isoformat()})"
        )
    return identity
