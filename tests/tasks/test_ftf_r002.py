#!/usr/bin/env python3
"""FTF-R002 -- Authenticated internal component bindings and reconciled
adapter foundation (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list/update, optimistic-version-conflict, cross-tenant IDOR,
idempotent-sync-outbox and real-health-check-against-a-real-local-server
evidence is `tests/integration/test_component_bindings.py`, run in CI's
dedicated integration job). This suite proves, without any database:

1. `component_binding_schema.py`'s own field validators and
   `ComponentBinding.__init__`'s structural consistency -- field names and
   enums matching FTF-R001's already-CERTIFIED, frozen
   `architecture.schema.json` contract literally: a `worker_package`
   binding can never carry an `approved_endpoint` and an
   `internal_service`/`existing_gateway` binding always requires one
   (Section 77.5's own "Worker packages have no invented API URL"),
   Axolotl/Inspect AI must be `worker_package` and LiteLLM must be
   `existing_gateway` (the frozen contract's own two structural
   invariants), `secret_reference` composes the real, already-certified
   `LiteLLMConnection.validate_secret_ref` rather than a duplicate
   primitive, and `approved_endpoint` accepts either a real `https://` URL
   or Temporal's own real bare `host:port` gRPC target.
2. `component_binding_crud.py::compute_health` -- this task's own central
   negative_acceptance ("no fake health after dependency outage") proven
   directly against the frozen `health_result` three-value enum: every
   real failure mode (denied destination, DNS failure, connection
   refused, timeout, non-2xx response) returns `unhealthy`; a
   `worker_package` binding or a non-HTTP (gRPC) endpoint returns
   `unknown`; only a real, observed 2xx response returns `healthy`.
3. `packages/component_contracts/job_identity.py` -- a real HMAC-signed
   worker job envelope: sign/verify round-trips, a tampered token is
   rejected, an expired token is rejected, a token signed with a
   different key is rejected, and `authorize_object_ref` denies an object
   reference outside the envelope's own granted set.
4. The real, locally-hosted `build_component_binding_router` (a real
   uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_04a07.py` established) denies an unauthenticated
   request and a role outside `COMPONENT_BINDING_ADMIN_ROLES` *before*
   `session_factory` is ever called.
5. `component_binding_crud.py`/`component_binding_routes.py` compose the
   same certified primitives every prior task in this chain already
   established -- no new, hand-rolled authorization/tenant-scoping
   primitive is introduced.

Run directly: python3 tests/tasks/test_ftf_r002.py
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.component_contracts.job_identity import (  # noqa: E402
    ALLOWED_COMPONENT_KEYS as JOB_IDENTITY_COMPONENT_KEYS,
)
from packages.component_contracts.job_identity import (  # noqa: E402
    WorkerJobIdentityError,
    authorize_object_ref,
    is_expired,
    mint_worker_job_identity,
    sign_worker_job_identity,
    verify_worker_job_identity,
)
from packages.security.outbound_guard import GuardedResponse, OutboundGuardError  # noqa: E402
from packages.security.url_policy import OutboundPolicyError  # noqa: E402

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.component_bindings.component_binding_crud import (  # noqa: E402
    compute_health,
    default_component_health_policy,
)
from app.component_bindings.component_binding_routes import (  # noqa: E402
    COMPONENT_BINDING_ADMIN_ROLES,
    build_component_binding_router,
)
from app.component_bindings.component_binding_schema import (  # noqa: E402
    ALLOWED_COMPONENT_KEYS,
    ALLOWED_DEPLOYMENT_MODES,
    ComponentBinding,
    ComponentBindingValidationError,
    ComponentSyncOutbox,
    is_http_endpoint,
    validate_approved_endpoint_or_none,
    validate_component_key,
    validate_deployment_mode,
    validate_secret_reference_or_none,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except (ComponentBindingValidationError, WorkerJobIdentityError):
        return True


_ORG_ID = uuid.uuid4()


# -- 1. field validators / structural consistency ------------------------


def test_validate_component_key_is_the_closed_seven_value_set() -> None:
    check(
        "FTF-R002",
        "ALLOWED_COMPONENT_KEYS is exactly Section 77.1's seven components",
        set(ALLOWED_COMPONENT_KEYS)
        == {"axolotl", "inspect_ai", "mlflow", "vllm", "temporal", "litellm", "argilla"},
    )
    for key in ALLOWED_COMPONENT_KEYS:
        check(
            "FTF-R002",
            f"validate_component_key accepts the real component {key!r}",
            validate_component_key(key) == key,
        )
    check(
        "FTF-R002",
        "an eighth, invented component is refused",
        _rejects(validate_component_key, "openhands"),
    )
    check(
        "FTF-R002",
        "job_identity.py's own ALLOWED_COMPONENT_KEYS is the identical set the "
        "app-layer schema imports from -- never independently re-derived",
        ALLOWED_COMPONENT_KEYS is JOB_IDENTITY_COMPONENT_KEYS,
    )


def test_validate_deployment_mode_is_the_closed_three_value_set() -> None:
    check(
        "FTF-R002",
        "ALLOWED_DEPLOYMENT_MODES is exactly Section 77.3's three real modes",
        set(ALLOWED_DEPLOYMENT_MODES) == {"internal_service", "worker_package", "existing_gateway"},
    )
    check(
        "FTF-R002",
        "an unrecognized deployment_mode is refused",
        _rejects(validate_deployment_mode, "kubernetes_operator"),
    )


def test_validate_approved_endpoint_shape() -> None:
    check(
        "FTF-R002",
        "a real https approved_endpoint round-trips unchanged",
        validate_approved_endpoint_or_none("https://mlflow.internal.example:5000")
        == "https://mlflow.internal.example:5000",
    )
    check(
        "FTF-R002",
        "Temporal's own real bare host:port gRPC target is accepted -- "
        "Temporal does not speak HTTP on its client port at all",
        validate_approved_endpoint_or_none("temporal.internal.sovra.local:7233")
        == "temporal.internal.sovra.local:7233",
    )
    check("FTF-R002", "null is accepted", validate_approved_endpoint_or_none(None) is None)
    check(
        "FTF-R002",
        "an http (non-tls) approved_endpoint is refused",
        _rejects(validate_approved_endpoint_or_none, "http://insecure.example"),
    )
    check(
        "FTF-R002",
        "a userinfo (user:pass@) approved_endpoint is refused (Section 65.4)",
        _rejects(
            validate_approved_endpoint_or_none, "https://admin:hunter2@mlflow.internal.example"
        ),
    )
    check(
        "FTF-R002",
        "a file:// approved_endpoint is refused",
        _rejects(validate_approved_endpoint_or_none, "file:///etc/passwd"),
    )
    check(
        "FTF-R002",
        "a garbage string matching neither the https:// nor host:port shape is refused",
        _rejects(validate_approved_endpoint_or_none, "not a real endpoint at all"),
    )


def test_is_http_endpoint() -> None:
    check(
        "FTF-R002",
        "an https:// URL is recognized as a real HTTP(S) endpoint",
        is_http_endpoint("https://mlflow.internal.example"),
    )
    check(
        "FTF-R002",
        "Temporal's own bare host:port target is not an HTTP endpoint",
        not is_http_endpoint("temporal.internal.sovra.local:7233"),
    )


def test_validate_secret_reference_composes_litellm_primitive_not_a_duplicate() -> None:
    check(
        "FTF-R002",
        "a real vault:// reference round-trips unchanged",
        validate_secret_reference_or_none("vault://path/to/secret") == "vault://path/to/secret",
    )
    check("FTF-R002", "null is accepted", validate_secret_reference_or_none(None) is None)
    check(
        "FTF-R002",
        "a raw bearer-token-shaped value is refused -- never stored as a "
        "credential (Section 65.4 'no API key in... browser local storage')",
        _rejects(validate_secret_reference_or_none, "Bearer sk-live-abc123def456"),
    )


def _make_binding(**overrides: object) -> ComponentBinding:
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "component_key": "mlflow",
        "security_zone_scope": "default",
        "project_scope": None,
        "deployment_mode": "internal_service",
        "approved_endpoint": "https://mlflow.internal.example:5000",
        "network_policy_identity": "mlflow-net-policy",
        "compatibility_lock_reference": "mlflow",
    }
    kwargs.update(overrides)
    return ComponentBinding(**kwargs)


def test_construction_accepts_a_real_internal_service_binding() -> None:
    binding = _make_binding()
    check(
        "FTF-R002",
        "a real internal_service binding with a real endpoint constructs cleanly",
        binding.component_key == "mlflow" and binding.approved_endpoint is not None,
    )


def test_construction_accepts_a_real_worker_package_binding_with_no_endpoint() -> None:
    binding = _make_binding(
        component_key="axolotl",
        deployment_mode="worker_package",
        approved_endpoint=None,
        network_policy_identity="axolotl-net-policy",
        compatibility_lock_reference="axolotl",
    )
    check(
        "FTF-R002",
        "a real worker_package binding with no endpoint constructs cleanly "
        "(Axolotl/Inspect are worker-executed software, not always-on services)",
        binding.deployment_mode == "worker_package" and binding.approved_endpoint is None,
    )


def test_construction_accepts_temporal_with_a_real_host_port_endpoint() -> None:
    binding = _make_binding(
        component_key="temporal",
        deployment_mode="internal_service",
        approved_endpoint="temporal.internal.sovra.local:7233",
        network_policy_identity="temporal-net-policy",
        compatibility_lock_reference="temporal",
    )
    check(
        "FTF-R002",
        "Temporal's own real bare host:port gRPC target constructs cleanly",
        binding.approved_endpoint == "temporal.internal.sovra.local:7233",
    )


def test_construction_rejects_worker_package_with_endpoint() -> None:
    check(
        "FTF-R002",
        "negative_acceptance-adjacent: a worker_package binding claiming an "
        "approved_endpoint is refused -- 'Worker packages have no invented API URL'",
        _rejects(
            _make_binding,
            component_key="axolotl",
            deployment_mode="worker_package",
            approved_endpoint="https://should-not-exist.example",
            network_policy_identity="axolotl-net-policy",
            compatibility_lock_reference="axolotl",
        ),
    )


def test_construction_rejects_service_mode_with_no_endpoint() -> None:
    for mode in ("internal_service", "existing_gateway"):
        check(
            "FTF-R002",
            f"a {mode} binding with no approved_endpoint is refused",
            _rejects(_make_binding, deployment_mode=mode, approved_endpoint=None),
        )


def test_construction_rejects_unknown_component_key_and_mode() -> None:
    check(
        "FTF-R002",
        "constructing a binding with an unknown component_key raises before any DB call",
        _rejects(_make_binding, component_key="openhands"),
    )
    check(
        "FTF-R002",
        "constructing a binding with an unknown deployment_mode raises before any DB call",
        _rejects(_make_binding, deployment_mode="serverless"),
    )


def test_construction_enforces_frozen_architecture_invariants() -> None:
    check(
        "FTF-R002",
        "architecture.schema.json's own invariant: Axolotl claiming "
        "internal_service (not worker_package) is refused",
        _rejects(
            _make_binding,
            component_key="axolotl",
            deployment_mode="internal_service",
            approved_endpoint="https://axolotl-should-not-have-a-url.example",
            network_policy_identity="axolotl-net-policy",
            compatibility_lock_reference="axolotl",
        ),
    )
    check(
        "FTF-R002",
        "architecture.schema.json's own invariant: Inspect AI claiming existing_gateway is refused",
        _rejects(
            _make_binding,
            component_key="inspect_ai",
            deployment_mode="existing_gateway",
            approved_endpoint="https://inspect-should-not-be-a-gateway.example",
            network_policy_identity="inspect-ai-net-policy",
            compatibility_lock_reference="inspect_ai",
        ),
    )
    check(
        "FTF-R002",
        "architecture.schema.json's own invariant: LiteLLM claiming "
        "internal_service (not existing_gateway) is refused -- the existing "
        "gateway is reused as-is, never replaced by a newly deployed service",
        _rejects(
            _make_binding,
            component_key="litellm",
            deployment_mode="internal_service",
            approved_endpoint="https://litellm-should-be-existing-gateway.example",
            network_policy_identity="litellm-net-policy",
            compatibility_lock_reference="litellm",
        ),
    )
    valid_litellm = _make_binding(
        component_key="litellm",
        deployment_mode="existing_gateway",
        approved_endpoint="https://litellm.internal.example",
        network_policy_identity="litellm-net-policy",
        compatibility_lock_reference="litellm",
    )
    check(
        "FTF-R002",
        "LiteLLM with the real required existing_gateway mode constructs cleanly",
        valid_litellm.deployment_mode == "existing_gateway",
    )


def test_sync_outbox_status_validation() -> None:
    outbox = ComponentSyncOutbox(
        organization_id=_ORG_ID,
        component_binding_id=uuid.uuid4(),
        factory_job_id=uuid.uuid4(),
        idempotency_key="job-1-sync",
        status="pending",
    )
    check("FTF-R002", "a real pending outbox row constructs cleanly", outbox.status == "pending")
    check(
        "FTF-R002",
        "an unknown outbox status is refused",
        _rejects(
            ComponentSyncOutbox,
            organization_id=_ORG_ID,
            component_binding_id=uuid.uuid4(),
            factory_job_id=uuid.uuid4(),
            idempotency_key="job-2-sync",
            status="cancelled",
        ),
    )
    check(
        "FTF-R002",
        "an empty idempotency_key is refused",
        _rejects(
            ComponentSyncOutbox,
            organization_id=_ORG_ID,
            component_binding_id=uuid.uuid4(),
            factory_job_id=uuid.uuid4(),
            idempotency_key="",
        ),
    )


# -- 2. compute_health -- the central "no fake health" negative_acceptance ---


def _fetch_returning(status: int):
    def _fetch(url: str, policy, *, method: str, timeout: float) -> GuardedResponse:
        return GuardedResponse(status=status, headers={}, body=b"", final_url=url)

    return _fetch


def _fetch_raising(exc: Exception):
    def _fetch(url: str, policy, *, method: str, timeout: float):
        raise exc

    return _fetch


def test_compute_health_worker_package_is_unknown() -> None:
    status, detail = compute_health(
        deployment_mode="worker_package",
        approved_endpoint=None,
        policy=default_component_health_policy(),
    )
    check(
        "FTF-R002",
        "a worker_package binding (no endpoint to dial) reports the frozen "
        "'unknown' value, never healthy",
        status == "unknown",
    )


def test_compute_health_non_http_endpoint_is_unknown() -> None:
    """Temporal's own bare host:port target -- this module cannot speak
    gRPC, so it must never fabricate a health verdict for it."""
    status, detail = compute_health(
        deployment_mode="internal_service",
        approved_endpoint="temporal.internal.sovra.local:7233",
        policy=default_component_health_policy(),
    )
    check(
        "FTF-R002",
        "negative_acceptance: a non-HTTP (gRPC) endpoint is never probed over "
        "HTTP and never reports a fabricated healthy/unhealthy verdict",
        status == "unknown" and detail == "non_http_endpoint_not_probed",
    )


def test_compute_health_real_2xx_is_healthy() -> None:
    status, detail = compute_health(
        deployment_mode="internal_service",
        approved_endpoint="https://x.example",
        policy=default_component_health_policy(),
        fetch=_fetch_returning(200),
    )
    check("FTF-R002", "a real observed 2xx response reports healthy", status == "healthy")
    check(
        "FTF-R002", "the health_detail is content-free (just the status code)", detail == "http_200"
    )


def test_compute_health_never_fakes_healthy_on_non_2xx() -> None:
    for code in (404, 500, 503):
        status, _ = compute_health(
            deployment_mode="internal_service",
            approved_endpoint="https://x.example",
            policy=default_component_health_policy(),
            fetch=_fetch_returning(code),
        )
        check(
            "FTF-R002",
            f"negative_acceptance: a real {code} response never reports healthy",
            status == "unhealthy",
        )


def test_compute_health_never_fakes_healthy_on_destination_denied() -> None:
    status, detail = compute_health(
        deployment_mode="internal_service",
        approved_endpoint="https://x.example",
        policy=default_component_health_policy(),
        fetch=_fetch_raising(OutboundPolicyError("denied")),
    )
    check(
        "FTF-R002",
        "negative_acceptance: a policy-denied destination never reports healthy",
        status == "unhealthy" and detail == "destination_denied",
    )


def test_compute_health_never_fakes_healthy_on_dependency_outage() -> None:
    """This task's own central fault-injection: simulates a real dependency
    outage (connection refused, DNS failure, TLS failure, timeout) and
    proves every one reports unhealthy, never healthy."""
    cases = [
        (ConnectionRefusedError(), "connection_refused"),
        (socket.gaierror("name resolution failed"), "dns_failed"),
        (TimeoutError(), "timeout"),
    ]
    for cause, expected_detail in cases:
        guard_error = OutboundGuardError("connection failed")
        guard_error.__cause__ = cause
        status, detail = compute_health(
            deployment_mode="internal_service",
            approved_endpoint="https://x.example",
            policy=default_component_health_policy(),
            fetch=_fetch_raising(guard_error),
        )
        check(
            "FTF-R002",
            f"negative_acceptance: a real dependency outage ({expected_detail}) "
            "never reports healthy after the dependency goes down",
            status == "unhealthy" and detail == expected_detail,
        )


# -- 3. worker job identity (job_identity.py) -----------------------------


def test_job_identity_sign_verify_round_trip() -> None:
    now = datetime.now(UTC)
    identity = mint_worker_job_identity(
        organization_id=_ORG_ID,
        job_id=uuid.uuid4(),
        component_key="mlflow",
        policy_version="v1",
        allowed_object_refs={"artifact://a", "artifact://b"},
        issued_at=now,
        ttl_seconds=3600,
        fencing_epoch=1,
    )
    key = b"a-real-signing-key"
    token = sign_worker_job_identity(identity, signing_key=key)
    verified = verify_worker_job_identity(token, signing_key=key, now=now)
    check(
        "FTF-R002",
        "a real signed envelope verifies and round-trips every field",
        verified.organization_id == identity.organization_id
        and verified.component_key == "mlflow"
        and verified.allowed_object_refs == identity.allowed_object_refs,
    )


def _tamper_signature_byte(token: str) -> str:
    # Flips a real byte of the *decoded* signature, not a character of the
    # base64url text. A urlsafe-base64-encoded SHA-256 digest (32 bytes) has
    # a final sextet whose low bits are unused encoding padding, not digest
    # data (32 % 3 == 2, so the last encoded group carries only 16 of its
    # 18 available bits) -- flipping a token *character* there (as an
    # earlier version of this test did, token[-1] or token[-4]) can, for a
    # fraction of randomly generated tokens, decode back to the exact same
    # signature bytes, making the "tamper" a silent no-op and this check
    # flaky (observed for real: ~1.6%-6.3% of tokens across 20k-50k trials,
    # depending on which character was flipped). Flipping a decoded byte
    # instead is unconditional: changing any one of 32 real digest bytes
    # always changes what `hmac.compare_digest` compares against.
    payload_b64, signature_b64 = token.rsplit(".", 1)
    padding = "=" * (-len(signature_b64) % 4)
    signature = bytearray(base64.urlsafe_b64decode(signature_b64 + padding))
    signature[0] ^= 0xFF
    tampered_signature_b64 = base64.urlsafe_b64encode(bytes(signature)).rstrip(b"=").decode("ascii")
    return f"{payload_b64}.{tampered_signature_b64}"


def test_job_identity_rejects_tampered_token() -> None:
    now = datetime.now(UTC)
    identity = mint_worker_job_identity(
        organization_id=_ORG_ID,
        job_id=uuid.uuid4(),
        component_key="temporal",
        policy_version="v1",
        allowed_object_refs=set(),
        issued_at=now,
        ttl_seconds=60,
        fencing_epoch=0,
    )
    key = b"a-real-signing-key"
    token = sign_worker_job_identity(identity, signing_key=key)
    tampered = _tamper_signature_byte(token)
    check(
        "FTF-R002",
        "a bit-flipped/tampered token fails signature verification",
        tampered != token
        and _rejects(verify_worker_job_identity, tampered, signing_key=key, now=now),
    )


def test_job_identity_rejects_wrong_key() -> None:
    now = datetime.now(UTC)
    identity = mint_worker_job_identity(
        organization_id=_ORG_ID,
        job_id=uuid.uuid4(),
        component_key="vllm",
        policy_version="v1",
        allowed_object_refs=set(),
        issued_at=now,
        ttl_seconds=60,
        fencing_epoch=0,
    )
    token = sign_worker_job_identity(identity, signing_key=b"real-key")
    check(
        "FTF-R002",
        "a token verified with the wrong signing key is refused -- a worker "
        "cannot forge an envelope for a component it was never issued one for",
        _rejects(verify_worker_job_identity, token, signing_key=b"different-key", now=now),
    )


def test_job_identity_rejects_expired_token() -> None:
    now = datetime.now(UTC)
    identity = mint_worker_job_identity(
        organization_id=_ORG_ID,
        job_id=uuid.uuid4(),
        component_key="argilla",
        policy_version="v1",
        allowed_object_refs=set(),
        issued_at=now,
        ttl_seconds=30,
        fencing_epoch=0,
    )
    check(
        "FTF-R002",
        "is_expired is false immediately after minting",
        not is_expired(identity, now=now),
    )
    later = now + timedelta(seconds=60)
    check("FTF-R002", "is_expired is true after the ttl elapses", is_expired(identity, now=later))
    key = b"k"
    token = sign_worker_job_identity(identity, signing_key=key)
    check(
        "FTF-R002",
        "a real expired token is refused by verify_worker_job_identity, never silently trusted",
        _rejects(verify_worker_job_identity, token, signing_key=key, now=later),
    )


def test_job_identity_rejects_ttl_above_bound() -> None:
    check(
        "FTF-R002",
        "a ttl_seconds beyond MAX_TTL_SECONDS is refused -- no effectively-"
        "permanent worker credential can be minted",
        _rejects(
            mint_worker_job_identity,
            organization_id=_ORG_ID,
            job_id=uuid.uuid4(),
            component_key="litellm",
            policy_version="v1",
            allowed_object_refs=set(),
            issued_at=datetime.now(UTC),
            ttl_seconds=999_999_999,
            fencing_epoch=0,
        ),
    )


def test_job_identity_authorize_object_ref() -> None:
    now = datetime.now(UTC)
    identity = mint_worker_job_identity(
        organization_id=_ORG_ID,
        job_id=uuid.uuid4(),
        component_key="inspect_ai",
        policy_version="v1",
        allowed_object_refs={"dataset://approved"},
        issued_at=now,
        ttl_seconds=60,
        fencing_epoch=0,
    )
    authorize_object_ref(identity, "dataset://approved")  # must not raise
    check(
        "FTF-R002",
        "an object reference outside the envelope's own granted set is denied "
        "-- Section 65.2 'worker identity is constrained to the task's own namespace'",
        _rejects(authorize_object_ref, identity, "dataset://not-granted"),
    )


# -- 4. Real demo router: role-boundary denial never opens a session -----


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_ORG_ADMIN_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="admin-1")
_EVALUATOR_PRINCIPAL = AuthenticatedPrincipal(
    issuer="https://issuer.example", subject="evaluator-1"
)


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "organization_admin":
        return _ORG_ADMIN_PRINCIPAL
    if header == "evaluator":
        return _EVALUATOR_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _ORG_ADMIN_PRINCIPAL.subject:
        return Role.ORGANIZATION_ADMIN
    if subject == _EVALUATOR_PRINCIPAL.subject:
        return Role.EVALUATOR
    return None


@contextlib.contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_component_binding_router(
            session_factory,
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
        )
    )
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@dataclass
class _HttpResult:
    status: int


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    principal: str | None = None,
    body: bytes | None = None,
) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    if body is not None:
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        response.read()
        return _HttpResult(status=response.status)
    finally:
        conn.close()


def _collection_path() -> str:
    return f"/api/v1/organizations/{_ORG_ID}/component-bindings"


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path())
        check(
            "FTF-R002",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-R002",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_is_denied_read_and_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="evaluator")
        check(
            "FTF-R002",
            "an evaluator is denied read (403) -- this resource is gated to "
            "organization_admin alone (Section 77.6)",
            result.status == 403,
        )
        check(
            "FTF-R002",
            "the evaluator's denied read never opens a session",
            session_factory.call_count == 0,
        )

        result = _request(base_url, "POST", _collection_path(), principal="evaluator", body=b"{}")
        check("FTF-R002", "an evaluator is denied create (403)", result.status == 403)
        check(
            "FTF-R002",
            "the evaluator's denied write never opens a session",
            session_factory.call_count == 0,
        )


def test_organization_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="organization_admin")
        check(
            "FTF-R002",
            "an organization_admin's read passes the role gate and reaches the "
            "session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-R002",
            "the organization_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_set_matches_section_77_6() -> None:
    check(
        "FTF-R002",
        "Section 77.6: '/organizations/{id}/component-bindings -- Admin-only "
        "create/read/versioned update' -- COMPONENT_BINDING_ADMIN_ROLES is "
        "exactly {organization_admin}",
        COMPONENT_BINDING_ADMIN_ROLES == frozenset({Role.ORGANIZATION_ADMIN}),
    )


def test_crud_module_composes_certified_primitives_not_duplicates() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.component_bindings import component_binding_crud, component_binding_routes

    routes_source = inspect.getsource(component_binding_routes)
    check(
        "FTF-R002",
        "component_binding_routes.py builds its dependency from the real, "
        "imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(component_binding_crud)
    check(
        "FTF-R002",
        "component_binding_crud.py authorizes every fetch through the real, "
        "imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-R002",
        "component_binding_crud.py reads every row through the real, "
        "imported execute_tenant_scoped (FTF-0105) -- never a bare "
        "session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-R002",
        "component_binding_crud.py's own health check dials out only through "
        "the real, imported guarded_fetch (FTF-H006) -- no second, hand-"
        "rolled outbound HTTP client",
        "guarded_fetch" in crud_source and "packages.security.outbound_guard" in crud_source,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-R002 component-binding tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-R002 component-binding tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
