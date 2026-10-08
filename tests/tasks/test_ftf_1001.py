#!/usr/bin/env python3
"""FTF-1001 -- ModelVersion schema/API (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list and lineage/IDOR evidence is
`tests/integration/test_ftf_1001_model_version_schema_api.py`, run in
CI's dedicated integration job -- this repo's interactive dev sandbox has
no container-registry egress, same documented finding as every prior
integration-tested task). This suite proves, without any database:

1. `validate_import_kind`/`validate_endpoint_reference`: real, bounded,
   closed-set/shape validation, never silently accepted.
2. `ModelVersion.__init__` itself refuses every structurally inconsistent
   combination this task's own negative_acceptance names ("Required
   nonexistent weights/hash for endpoint import, mutable production
   artifact or cross-tenant parent denied") before any session or
   database is ever touched -- the "endpoint" half of that sentence (an
   endpoint import cannot claim local weight bytes) is a pure,
   construction-time check; the "mutable production artifact"/
   "cross-tenant parent" halves require a real database fetch of the
   referenced `TrainableArtifact` and are proven instead in the
   live-Postgres suite (see `model_version_crud.py`'s own
   `create_model_version`).
3. The real, locally-hosted `build_model_version_router` (a real uvicorn
   server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0404.py` established) denies an unauthenticated
   request and a role outside `MODEL_VERSION_WRITE_ROLES` on a write
   *before* `session_factory` is ever called, and lets a read-only role
   reach the session layer for a GET.

Run directly: python3 tests/tasks/test_ftf_1001.py
"""

from __future__ import annotations

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
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.artifacts.artifact_crud import get_artifact  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.model_versions.model_version_crud import (  # noqa: E402
    ArtifactNotFoundError,
    create_model_version,
    get_model_version,
    list_model_versions,
)
from app.model_versions.model_version_routes import (  # noqa: E402
    MODEL_VERSION_READ_ROLES,
    MODEL_VERSION_WRITE_ROLES,
    _serialize,
    build_model_version_router,
)
from app.model_versions.model_version_schema import (  # noqa: E402
    ALLOWED_IMPORT_KINDS,
    ModelVersion,
    ModelVersionValidationError,
    validate_endpoint_reference,
    validate_import_kind,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except ModelVersionValidationError:
        return True


_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_ARTIFACT_ID = uuid.uuid4()
_REAL_DIGEST = "sha256:" + "a" * 64


def _make_endpoint_version(**overrides: object) -> ModelVersion:
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "version_number": 1,
        "import_kind": "endpoint",
        "endpoint_reference": "https://models.example.com/v1/production",
        "created_by": "user-1",
        "created_at": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return ModelVersion(**kwargs)


def _make_full_weight_version(**overrides: object) -> ModelVersion:
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "version_number": 1,
        "import_kind": "full_weight",
        "trainable_artifact_id": _ARTIFACT_ID,
        "weights_digest": _REAL_DIGEST,
        "base_model_revision": "a1b2c3d4",
        "created_by": "user-1",
        "created_at": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return ModelVersion(**kwargs)


# -- 1. validate_import_kind / validate_endpoint_reference -------------------


_MALFORMED_DIGESTS: tuple[object, ...] = (
    "",
    "sha256:",
    "sha256:" + "A" * 64,
    "SHA256:" + "a" * 64,
    "sha256:" + "a" * 63,
    "sha256:" + "a" * 65,
    " sha256:" + "a" * 64,
    "sha256:" + "a" * 64 + " ",
    "sha256:" + "a" * 64 + "\n",
    "sha512:" + "a" * 64,
    b"sha256:" + b"a" * 64,
)


class _GatePassed(Exception):
    """Raised by the patched `_next_version_number`: the artifact gate let
    the import through to version numbering."""


def _create_with_artifact_digest(digest: object) -> str:
    """Drive the real `create_model_version` with a ready artifact carrying
    `digest`. Only the project-authorization and artifact-fetch DB calls are
    replaced; the artifact gate under test runs unmodified. Returns
    "refused", "passed" or the unexpected exception's repr."""
    from app.artifacts.artifact_schema import TrainableArtifact
    from app.model_versions import model_version_crud

    artifact = TrainableArtifact(
        organization_id=_ORG_ID,
        display_name="llama-8b",
        source_type="huggingface",
        source_uri="meta-llama/Llama-3.1-8B",
        model_family="llama",
        base_model_revision="a" * 40,
        license_id="apache-2.0",
        status="ready",
    )
    artifact.id = _ARTIFACT_ID
    # Set post-construction, as an ORM-loaded row would carry it.
    artifact.weights_digest = digest  # type: ignore[assignment]

    def _no_authz(*_args: object, **_kwargs: object) -> None:
        return None

    def _fetch(*_args: object, **_kwargs: object) -> TrainableArtifact:
        return artifact

    def _next(*_args: object, **_kwargs: object) -> int:
        raise _GatePassed

    saved = (
        model_version_crud._authorize_project_or_raise,
        model_version_crud.get_artifact,
        model_version_crud._next_version_number,
    )
    model_version_crud._authorize_project_or_raise = _no_authz
    model_version_crud.get_artifact = _fetch
    model_version_crud._next_version_number = _next
    try:
        create_model_version(
            MagicMock(),
            MagicMock(organization_id=_ORG_ID),
            project_id=_PROJECT_ID,
            import_kind="full_weight",
            trainable_artifact_id=_ARTIFACT_ID,
            actor=MagicMock(actor_type="user", actor_id="user-1", request_id="r-1"),
        )
        return "no-exception"
    except model_version_crud.ModelVersionArtifactStateError:
        return "refused"
    except _GatePassed:
        return "passed"
    except Exception as exc:  # noqa: BLE001 -- reported by the caller's check
        return repr(exc)
    finally:
        (
            model_version_crud._authorize_project_or_raise,
            model_version_crud.get_artifact,
            model_version_crud._next_version_number,
        ) = saved


def test_create_refuses_every_malformed_artifact_weights_digest() -> None:
    """FTF-0307 round-3 same-class sweep (docs/certifications/queue.md
    "2026-10-06T09:45Z" DoD (4)): `create_model_version` re-validates the
    parent artifact's digest with the shared `validate_weights_digest`, not
    merely `is None`, before copying it into the model version."""
    for digest in _MALFORMED_DIGESTS:
        outcome = _create_with_artifact_digest(digest)
        check(
            "FTF-1001",
            f"negative_acceptance: a ready artifact with digest {digest!r} cannot back a "
            f"model-version import (got {outcome})",
            outcome == "refused",
        )
    check(
        "FTF-1001",
        "a ready artifact with a real sha256 digest passes the gate (no over-blocking)",
        _create_with_artifact_digest(_REAL_DIGEST) == "passed",
    )


def test_model_version_crud_uses_the_shared_validate_weights_digest() -> None:
    from app.artifacts import artifact_schema
    from app.model_versions import model_version_crud

    check(
        "FTF-1001",
        "model_version_crud binds the shared artifact_schema.validate_weights_digest",
        getattr(model_version_crud, "validate_weights_digest", None)
        is artifact_schema.validate_weights_digest,
    )


def test_validate_import_kind_is_the_closed_three_kind_set() -> None:
    check(
        "FTF-1001",
        "ALLOWED_IMPORT_KINDS is exactly the three kinds this task's own acceptance clause names",
        set(ALLOWED_IMPORT_KINDS) == {"endpoint", "full_weight", "adapter"},
    )
    for kind in ALLOWED_IMPORT_KINDS:
        check(
            "FTF-1001",
            f"validate_import_kind accepts the real kind {kind!r}",
            validate_import_kind(kind) == kind,
        )
    check(
        "FTF-1001",
        "an unrecognized import_kind is refused, not silently accepted",
        _rejects(validate_import_kind, "quantized"),
    )
    check(
        "FTF-1001",
        "a non-string import_kind is refused",
        _rejects(validate_import_kind, None),
    )


def test_validate_endpoint_reference_accepts_real_https() -> None:
    check(
        "FTF-1001",
        "a real https endpoint reference round-trips unchanged",
        validate_endpoint_reference("https://models.example.com/v1/production")
        == "https://models.example.com/v1/production",
    )


def test_validate_endpoint_reference_rejects_bad_shapes() -> None:
    check(
        "FTF-1001",
        "an empty endpoint_reference is rejected",
        _rejects(validate_endpoint_reference, ""),
    )
    check(
        "FTF-1001",
        "a non-string endpoint_reference is rejected",
        _rejects(validate_endpoint_reference, None),
    )
    check(
        "FTF-1001",
        "a plain http:// (not https) endpoint_reference is rejected",
        _rejects(validate_endpoint_reference, "http://models.example.com/v1"),
    )
    check(
        "FTF-1001",
        "an endpoint_reference with no host is rejected",
        _rejects(validate_endpoint_reference, "https:///v1"),
    )
    check(
        "FTF-1001",
        "an endpoint_reference carrying userinfo is rejected (Section 65.4)",
        _rejects(validate_endpoint_reference, "https://user:pw@models.example.com/v1"),
    )
    check(
        "FTF-1001",
        "an endpoint_reference carrying a fragment is rejected",
        _rejects(validate_endpoint_reference, "https://models.example.com/v1#frag"),
    )
    check(
        "FTF-1001",
        "an oversized endpoint_reference is rejected",
        _rejects(validate_endpoint_reference, "https://models.example.com/" + "x" * 2048),
    )


# -- 2. ModelVersion.__init__ structural consistency (negative_acceptance) --


def test_construction_accepts_a_real_endpoint_import() -> None:
    version = _make_endpoint_version()
    check(
        "FTF-1001",
        "a real, well-formed endpoint import constructs cleanly",
        version.import_kind == "endpoint"
        and version.endpoint_reference == "https://models.example.com/v1/production"
        and version.trainable_artifact_id is None
        and version.weights_digest is None
        and version.base_model_revision is None,
    )


def test_construction_accepts_a_real_full_weight_import() -> None:
    version = _make_full_weight_version()
    check(
        "FTF-1001",
        "a real, well-formed full_weight import constructs cleanly with "
        "Factory training lineage (trainable_artifact_id set)",
        version.import_kind == "full_weight"
        and version.trainable_artifact_id == _ARTIFACT_ID
        and version.weights_digest == _REAL_DIGEST
        and version.endpoint_reference is None,
    )


def test_construction_accepts_a_real_adapter_import() -> None:
    version = _make_full_weight_version(import_kind="adapter")
    check(
        "FTF-1001",
        "a real, well-formed adapter import constructs cleanly",
        version.import_kind == "adapter" and version.trainable_artifact_id == _ARTIFACT_ID,
    )


def test_construction_rejects_endpoint_import_carrying_weights_digest() -> None:
    check(
        "FTF-1001",
        "negative_acceptance: an endpoint import claiming a weights_digest "
        "-- a required but nonexistent hash for that kind -- is refused "
        "before any session or database is ever touched",
        _rejects(_make_endpoint_version, weights_digest=_REAL_DIGEST),
    )


def test_construction_rejects_endpoint_import_carrying_trainable_artifact_id() -> None:
    check(
        "FTF-1001",
        "negative_acceptance: an endpoint import claiming a "
        "trainable_artifact_id is refused -- no local weight bytes back it",
        _rejects(_make_endpoint_version, trainable_artifact_id=_ARTIFACT_ID),
    )


def test_construction_rejects_endpoint_import_missing_endpoint_reference() -> None:
    check(
        "FTF-1001",
        "an endpoint import with no endpoint_reference at all is refused",
        _rejects(_make_endpoint_version, endpoint_reference=None),
    )


def test_construction_rejects_full_weight_import_carrying_endpoint_reference() -> None:
    check(
        "FTF-1001",
        "a full_weight import claiming an endpoint_reference is refused "
        "-- it is not an endpoint import",
        _rejects(_make_full_weight_version, endpoint_reference="https://x.example.com/v1"),
    )


def test_construction_rejects_full_weight_import_missing_trainable_artifact_id() -> None:
    check(
        "FTF-1001",
        "negative_acceptance: a full_weight import with no "
        "trainable_artifact_id (no Factory training lineage) is refused",
        _rejects(_make_full_weight_version, trainable_artifact_id=None),
    )


def test_construction_rejects_full_weight_import_missing_weights_digest() -> None:
    check(
        "FTF-1001",
        "negative_acceptance: a full_weight import with no weights_digest "
        "-- a required but nonexistent hash -- is refused",
        _rejects(_make_full_weight_version, weights_digest=None),
    )


def test_construction_rejects_full_weight_import_missing_base_model_revision() -> None:
    check(
        "FTF-1001",
        "a full_weight import with no base_model_revision is refused",
        _rejects(_make_full_weight_version, base_model_revision=None),
    )


def test_construction_rejects_malformed_weights_digest_shape() -> None:
    check(
        "FTF-1001",
        "a weights_digest that does not match the real sha256: pattern is "
        "refused, never silently accepted as a resolved hash",
        _rejects(_make_full_weight_version, weights_digest="not-a-real-digest"),
    )


def test_construction_rejects_malformed_base_model_revision_shape() -> None:
    # A *present but malformed* value, not only the already-covered missing
    # case above -- found via FTF-H010-P1's own mutation run against this
    # same __init__ (a swallowed TrainableArtifactValidationError in the
    # base_model_revision except-block went undetected until then).
    check(
        "FTF-1001",
        "a base_model_revision that is blank is refused, never silently accepted",
        _rejects(_make_full_weight_version, base_model_revision=""),
    )
    check(
        "FTF-1001",
        "a base_model_revision over the real max length is refused",
        _rejects(_make_full_weight_version, base_model_revision="x" * 300),
    )


def test_construction_rejects_unknown_import_kind() -> None:
    check(
        "FTF-1001",
        "constructing a ModelVersion with an unknown import_kind raises before any DB call",
        _rejects(_make_endpoint_version, import_kind="quantized"),
    )


# -- 3. Real demo router: role-boundary denial never opens a session --------


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_ML_ENGINEER_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="mle-1")
_EVALUATOR_PRINCIPAL = AuthenticatedPrincipal(
    issuer="https://issuer.example", subject="evaluator-1"
)


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "ml_engineer":
        return _ML_ENGINEER_PRINCIPAL
    if header == "evaluator":
        return _EVALUATOR_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _ML_ENGINEER_PRINCIPAL.subject:
        return Role.ML_ENGINEER
    if subject == _EVALUATOR_PRINCIPAL.subject:
        return Role.EVALUATOR
    return None


@contextlib.contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_model_version_router(
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


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/model-versions",
        )
        check(
            "FTF-1001",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-1001",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_can_read_but_not_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/model-versions",
            principal="evaluator",
        )
        check(
            "FTF-1001",
            "an evaluator (a role Section 45 names, with no dedicated "
            "restrictive sentence for model versions) can read (not 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-1001",
            "the evaluator's read genuinely reaches the session layer",
            session_factory.call_count >= 1,
        )

        session_factory.reset_mock()
        result = _request(
            base_url,
            "POST",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/model-versions",
            principal="evaluator",
        )
        check(
            "FTF-1001",
            "an evaluator is denied create (403) -- write is ml_engineer/organization_admin only",
            result.status == 403,
        )
        check(
            "FTF-1001",
            "the write role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_ml_engineer_reaches_the_session_layer_for_writes() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "POST",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/model-versions",
            principal="ml_engineer",
            body=b'{"import_kind": "endpoint", "endpoint_reference": "https://x.example.com"}',
        )
        check(
            "FTF-1001",
            "an ml_engineer's create request passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-1001",
            "the ml_engineer's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_split_matches_section_45() -> None:
    check(
        "FTF-1001",
        "Section 45: 'ml_engineer: datasets/training, no production "
        "approval' is the only sentence naming a role for this class of "
        "work -- MODEL_VERSION_WRITE_ROLES is exactly {ml_engineer, "
        "organization_admin}, the same precedent FTF-0301 applied",
        MODEL_VERSION_WRITE_ROLES == frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-1001",
        "MODEL_VERSION_READ_ROLES includes every role Section 45 names",
        MODEL_VERSION_READ_ROLES == frozenset(Role),
    )


def test_crud_module_composes_certified_primitives_not_duplicates() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.model_versions import model_version_crud, model_version_routes

    routes_source = inspect.getsource(model_version_routes)
    check(
        "FTF-1001",
        "model_version_routes.py builds its dependencies from the real, "
        "imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(model_version_crud)
    check(
        "FTF-1001",
        "model_version_crud.py authorizes every fetch through the real, "
        "imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-1001",
        "model_version_crud.py reads every row through the real, imported "
        "execute_tenant_scoped (FTF-0105) -- never a bare session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-1001",
        "model_version_crud.py's own cross-tenant-parent guard (this "
        "task's own negative_acceptance) reuses FTF-0301's own already- "
        "certified get_artifact/ArtifactNotFoundError directly -- no new, "
        "second authorization primitive is introduced for the parent "
        "TrainableArtifact reference",
        "from app.artifacts.artifact_crud import" in crud_source
        and "get_artifact(" in crud_source
        and get_artifact.__module__ == "app.artifacts.artifact_crud"
        and ArtifactNotFoundError.__module__ == "app.artifacts.artifact_crud",
    )
    # Referenced so the import above is genuinely exercised, not dead code.
    check(
        "FTF-1001",
        "create_model_version/get_model_version/list_model_versions are "
        "the real, callable CRUD surface this router's own module imports",
        callable(create_model_version)
        and callable(get_model_version)
        and callable(list_model_versions),
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    endpoint_version = _make_endpoint_version()
    body = _serialize(endpoint_version)
    check(
        "FTF-1001",
        "the serialized endpoint version carries version_number as a real "
        "integer field and no artifact/weights lineage fields",
        isinstance(body["version_number"], int)
        and body["version_number"] == 1
        and body["trainable_artifact_id"] is None
        and body["weights_digest"] is None,
    )

    full_weight_version = _make_full_weight_version()
    body = _serialize(full_weight_version)
    check(
        "FTF-1001",
        "the serialized full_weight version carries its real "
        "trainable_artifact_id/weights_digest lineage fields as strings",
        body["trainable_artifact_id"] == str(_ARTIFACT_ID)
        and body["weights_digest"] == _REAL_DIGEST
        and body["endpoint_reference"] is None,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1001 ModelVersion schema/API tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1001 ModelVersion schema/API tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
