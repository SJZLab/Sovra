#!/usr/bin/env python3
"""FTF-0301 -- TrainableArtifact schema/API (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list/edit/accept-license/mark-ready/archive and IDOR evidence
is `tests/integration/test_ftf_0301_trainable_artifact_schema_api.py`, run
in CI's dedicated `trainable-artifact-schema-api-integration` job -- this
repo's interactive dev sandbox has no container-registry egress, same
documented finding as every prior integration-tested task; see that
file's own docstring). This suite proves, without any database:

1. `validate_source_uri` (Section 65.4-style shape safety, dispatched per
   `source_type`): the exact named rejection cases for each of the four
   allowed `source_type` values, and a real well-formed value per type is
   genuinely accepted.
2. `is_pinned_revision`: the named mutable-alias rejection cases and a
   real pinned revision is accepted.
3. `TrainableArtifact.__init__` itself refuses a bad source_uri/status
   before any session or database is ever touched -- this task's own
   negative_acceptance ("rejected URL... fails") proven at the pure-
   construction level.
4. `apply_edits`/`apply_accept_license`/`apply_mark_ready`/`apply_archive`
   (the pure, DB-free cores): status-transition rules -- editing a
   non-`registered` artifact is refused, license acceptance is idempotent
   and always attributes the real actor, and `apply_mark_ready`'s own
   three-part gate ("Unsafe URI, unknown owner or mutable unresolved
   revision cannot enter training as ready") is proven directly against
   in-memory `TrainableArtifact` objects, no database required.
5. The real, locally-hosted `build_trainable_artifact_router` (a real
   uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0201.py` established) denies an unauthenticated
   request and a role outside `ARTIFACT_WRITE_ROLES` on a write *before*
   `session_factory` is ever called, and lets a read-only role reach the
   session layer for a GET.

Run directly: python3 tests/tasks/test_ftf_0301.py
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

from app.artifacts.artifact_crud import (  # noqa: E402
    ArtifactStateError,
    apply_accept_license,
    apply_archive,
    apply_edits,
    apply_mark_ready,
)
from app.artifacts.artifact_routes import (  # noqa: E402
    ARTIFACT_READ_ROLES,
    ARTIFACT_WRITE_ROLES,
    _serialize,
    build_trainable_artifact_router,
)
from app.artifacts.artifact_schema import (  # noqa: E402
    TrainableArtifact,
    TrainableArtifactValidationError,
    is_pinned_revision,
    validate_source_uri,
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object) -> bool:
    try:
        fn(*args)
        return False
    except (TrainableArtifactValidationError, ArtifactStateError):
        return True


# -- 1. validate_source_uri, dispatched per source_type ----------------------


def test_validate_source_uri_accepts_well_formed_values_per_type() -> None:
    cases = {
        "huggingface": "meta-llama/Llama-3.1-8B",
        "private_s3": "s3://acme-artifacts/models/llama-8b/weights.tar",
        "local_filesystem": "artifacts/llama-8b/weights.tar",
        "private_registry": "https://registry.example.com/models/llama-8b",
    }
    for source_type, value in cases.items():
        check(
            "FTF-0301",
            f"a well-formed {source_type} source_uri is accepted",
            validate_source_uri(source_type, value) == value,
        )


def test_validate_source_uri_rejects_unsafe_huggingface_values() -> None:
    for value in ("https://huggingface.co/org/repo", "/org/repo", "org/../repo", "org repo/x"):
        check(
            "FTF-0301",
            f"huggingface source_uri {value!r} (a URL, absolute path, traversal or "
            "whitespace) is rejected",
            _rejects(validate_source_uri, "huggingface", value),
        )


def test_validate_source_uri_rejects_unsafe_s3_values() -> None:
    for value in (
        "http://acme-artifacts/key",
        "s3://user:pass@acme-artifacts/key",
        "s3://Bad_Bucket/key",
        "s3://acme-artifacts/../etc/passwd",
        "s3://acme-artifacts/",
    ):
        check(
            "FTF-0301",
            f"private_s3 source_uri {value!r} (wrong scheme, userinfo, invalid "
            "bucket, traversal or empty key) is rejected",
            _rejects(validate_source_uri, "private_s3", value),
        )


def test_validate_source_uri_rejects_unsafe_local_filesystem_values() -> None:
    for value in (
        "/etc/passwd",
        "~/secrets",
        "../../etc/passwd",
        "a/../../b",
        "file:///etc/passwd",
        "",
    ):
        check(
            "FTF-0301",
            f"local_filesystem source_uri {value!r} (absolute, home-relative, "
            "traversal, scheme-carrying or empty) is rejected",
            _rejects(validate_source_uri, "local_filesystem", value),
        )


def test_validate_source_uri_rejects_unsafe_private_registry_values() -> None:
    for value in (
        "http://registry.example.com/models/x",
        "https://user:pass@registry.example.com/x",
        "https://registry.example.com/x#frag",
        "https://",
    ):
        check(
            "FTF-0301",
            f"private_registry source_uri {value!r} (non-https, userinfo, "
            "fragment or missing host) is rejected",
            _rejects(validate_source_uri, "private_registry", value),
        )


def test_validate_source_uri_rejects_unknown_source_type_and_oversize() -> None:
    check(
        "FTF-0301",
        "an unknown source_type is rejected",
        _rejects(validate_source_uri, "ftp_share", "anything"),
    )
    check(
        "FTF-0301",
        "an oversize source_uri is rejected",
        _rejects(validate_source_uri, "huggingface", "org/" + "a" * 2100),
    )
    check(
        "FTF-0301",
        "a non-string source_uri is rejected",
        _rejects(validate_source_uri, "huggingface", None),
    )


# -- 2. is_pinned_revision -----------------------------------------------------


def test_is_pinned_revision_rejects_mutable_aliases() -> None:
    for value in ("main", "MASTER", " Latest ", "head", "", "  ", "nightly"):
        check(
            "FTF-0301",
            f"revision {value!r} is a mutable/unresolved alias, not pinned",
            is_pinned_revision(value) is False,
        )


def test_is_pinned_revision_accepts_a_real_pinned_commit() -> None:
    check(
        "FTF-0301",
        "a real commit-sha-shaped revision is treated as pinned",
        is_pinned_revision("a1b2c3d4e5f60718293a4b5c6d7e8f9012345678") is True,
    )


# -- 3. TrainableArtifact.__init__ refuses bad input before any session ------


def test_construction_rejects_unsafe_source_uri_before_any_session() -> None:
    check(
        "FTF-0301",
        "negative_acceptance: constructing an artifact with an unsafe source_uri "
        "fails before any session/database is touched",
        _rejects(
            lambda: TrainableArtifact(
                organization_id=uuid.uuid4(),
                display_name="llama-8b",
                source_type="local_filesystem",
                source_uri="/etc/passwd",
                model_family="llama",
                base_model_revision="a" * 40,
                license_id="apache-2.0",
            )
        ),
    )


def test_construction_rejects_unknown_status() -> None:
    check(
        "FTF-0301",
        "an unknown status value is rejected at construction",
        _rejects(
            lambda: TrainableArtifact(
                organization_id=uuid.uuid4(),
                display_name="llama-8b",
                source_type="huggingface",
                source_uri="meta-llama/Llama-3.1-8B",
                model_family="llama",
                base_model_revision="a" * 40,
                license_id="apache-2.0",
                status="deleted",
            )
        ),
    )


def test_construction_rejects_bad_weights_digest() -> None:
    check(
        "FTF-0301",
        "a malformed weights_digest is rejected at construction",
        _rejects(
            lambda: TrainableArtifact(
                organization_id=uuid.uuid4(),
                display_name="llama-8b",
                source_type="huggingface",
                source_uri="meta-llama/Llama-3.1-8B",
                model_family="llama",
                base_model_revision="a" * 40,
                license_id="apache-2.0",
                weights_digest="deadbeef",
            )
        ),
    )


def _make_artifact(*, status: str = "registered", **overrides: object) -> TrainableArtifact:
    now = datetime.now(UTC)
    fields: dict[str, object] = {
        "organization_id": uuid.uuid4(),
        "display_name": "llama-8b",
        "source_type": "huggingface",
        "source_uri": "meta-llama/Llama-3.1-8B",
        "model_family": "llama",
        "base_model_revision": "a" * 40,
        "license_id": "apache-2.0",
        "status": status,
    }
    fields.update(overrides)
    artifact = TrainableArtifact(**fields)
    artifact.id = uuid.uuid4()
    artifact.created_at = now
    artifact.updated_at = now
    return artifact


# -- 4. apply_edits / apply_accept_license / apply_mark_ready / apply_archive -


def test_apply_edits_refuses_a_non_registered_artifact() -> None:
    for status in ("ready", "archived"):
        artifact = _make_artifact(status=status)
        try:
            apply_edits(
                artifact,
                display_name="renamed",
                source_uri=None,
                base_model_revision=None,
                model_family=None,
                weights_digest=None,
                license_id=None,
                trust_remote_code_allowed=None,
                required_python_packages=None,
                minimum_gpu_profile=None,
            )
            check(
                "FTF-0301",
                f"acceptance: editing a {status} artifact is refused (ArtifactStateError)",
                False,
            )
        except ArtifactStateError:
            check(
                "FTF-0301",
                f"acceptance: editing a {status} artifact is refused (ArtifactStateError)",
                True,
            )


def test_apply_edits_rejects_unsafe_new_source_uri() -> None:
    artifact = _make_artifact()
    check(
        "FTF-0301",
        "negative_acceptance: a rejected new source_uri in an edit fails",
        _rejects(
            lambda: apply_edits(
                artifact,
                display_name=None,
                source_uri="/etc/passwd",
                base_model_revision=None,
                model_family=None,
                weights_digest=None,
                license_id=None,
                trust_remote_code_allowed=None,
                required_python_packages=None,
                minimum_gpu_profile=None,
            )
        ),
    )


def test_apply_accept_license_sets_attribution_and_is_idempotent() -> None:
    artifact = _make_artifact()
    check(
        "FTF-0301",
        "the first license acceptance reports a real change",
        apply_accept_license(artifact, accepted_by="user-1") is True,
    )
    check(
        "FTF-0301",
        "the first acceptance sets license_accepted_by/at",
        artifact.license_accepted_by == "user-1" and artifact.license_accepted_at is not None,
    )
    first_accepted_at = artifact.license_accepted_at

    check(
        "FTF-0301",
        "a second acceptance by a different actor is a no-op (immutable attribution)",
        apply_accept_license(artifact, accepted_by="user-2") is False,
    )
    check(
        "FTF-0301",
        "the original acceptor/timestamp are unchanged after the no-op second call",
        artifact.license_accepted_by == "user-1"
        and artifact.license_accepted_at == first_accepted_at,
    )


def test_apply_accept_license_refuses_on_archived_artifact() -> None:
    artifact = _make_artifact(status="archived")
    check(
        "FTF-0301",
        "accepting a license on an archived artifact is refused",
        _rejects(lambda: apply_accept_license(artifact, accepted_by="user-1")),
    )


def test_apply_mark_ready_blocks_on_unaccepted_license_unresolved_digest_and_mutable_revision() -> (
    None
):
    # Nothing satisfied yet: no license acceptance, no weights_digest, and the
    # fixture's own base_model_revision (40 hex chars) is pinned, so flip it
    # to a mutable alias to also exercise that branch independently below.
    artifact = _make_artifact()
    try:
        apply_mark_ready(artifact)
        check(
            "FTF-0301",
            "negative_acceptance: mark_ready is refused with no license "
            "acceptance and no resolved weights_digest",
            False,
        )
    except ArtifactStateError as exc:
        check(
            "FTF-0301",
            "negative_acceptance: mark_ready is refused with no license "
            "acceptance and no resolved weights_digest",
            "license" in str(exc) and "weights_digest" in str(exc),
        )
    check(
        "FTF-0301",
        "a refused mark_ready does not change status",
        artifact.status == "registered",
    )

    mutable_artifact = _make_artifact(base_model_revision="main")
    apply_accept_license(mutable_artifact, accepted_by="user-1")
    mutable_artifact.weights_digest = "sha256:" + "a" * 64
    check(
        "FTF-0301",
        "negative_acceptance: mark_ready is refused for a mutable/unresolved "
        "revision even once license+digest are otherwise satisfied",
        _rejects(lambda: apply_mark_ready(mutable_artifact)),
    )


def test_apply_mark_ready_succeeds_once_all_three_gates_are_satisfied() -> None:
    artifact = _make_artifact()
    apply_accept_license(artifact, accepted_by="user-1")
    artifact.weights_digest = "sha256:" + "b" * 64
    check(
        "FTF-0301",
        "acceptance: mark_ready succeeds once source_uri is safe, license is "
        "accepted and revision+digest are pinned/resolved",
        apply_mark_ready(artifact) is True,
    )
    check("FTF-0301", "mark_ready actually sets status=ready", artifact.status == "ready")

    check(
        "FTF-0301",
        "acceptance: marking an already-ready artifact ready again is "
        "idempotent -- reports no change",
        apply_mark_ready(artifact) is False,
    )


def test_apply_mark_ready_refuses_every_malformed_weights_digest_shape() -> None:
    """FTF-0307 round-3 same-class sweep (docs/certifications/queue.md
    "2026-10-06T09:45Z" DoD (4)): `apply_mark_ready` re-validates the digest
    with the shared `validate_weights_digest`, not merely `is None`. Each
    value is set post-construction, as an ORM-loaded row would be."""
    for digest in (
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
    ):
        artifact = _make_artifact()
        apply_accept_license(artifact, accepted_by="user-1")
        artifact.weights_digest = digest  # type: ignore[assignment]
        try:
            apply_mark_ready(artifact)
            refused = False
        except ArtifactStateError as exc:
            refused = "weights_digest" in str(exc)
        check(
            "FTF-0301",
            f"negative_acceptance: mark_ready refuses a malformed weights_digest {digest!r}",
            refused,
        )
        check(
            "FTF-0301",
            f"a refused mark_ready for digest {digest!r} leaves status registered",
            artifact.status == "registered",
        )


def test_apply_mark_ready_uses_the_shared_validate_weights_digest() -> None:
    from app.artifacts import artifact_crud, artifact_schema

    check(
        "FTF-0301",
        "artifact_crud binds the shared artifact_schema.validate_weights_digest",
        getattr(artifact_crud, "validate_weights_digest", None)
        is artifact_schema.validate_weights_digest,
    )


def test_apply_mark_ready_refuses_an_unknown_or_non_str_status() -> None:
    """FTF-0307 round-3 same-class sweep (DoD (5)): only a `registered`
    artifact may transition to `ready`. An unrecognised or non-hashable
    status (set post-construction, as an ORM-loaded row could carry) is
    refused, never silently overwritten with `ready`."""
    for status in ("a_future_status", "READY", " registered", ["ready"], {"registered"}):
        artifact = _make_artifact()
        apply_accept_license(artifact, accepted_by="user-1")
        artifact.weights_digest = "sha256:" + "c" * 64
        artifact.status = status  # type: ignore[assignment]
        try:
            apply_mark_ready(artifact)
            outcome = "accepted"
        except ArtifactStateError:
            outcome = "refused"
        except Exception as exc:  # noqa: BLE001 -- reported by the check below
            outcome = repr(exc)
        check(
            "FTF-0301",
            f"mark_ready refuses an artifact with status {status!r} (got {outcome})",
            outcome == "refused",
        )
        check(
            "FTF-0301",
            f"a refused mark_ready never rewrites status {status!r} to ready",
            artifact.status == status,
        )


def test_apply_mark_ready_refuses_on_archived_artifact() -> None:
    artifact = _make_artifact(status="archived")
    check(
        "FTF-0301",
        "marking an archived artifact ready is refused",
        _rejects(lambda: apply_mark_ready(artifact)),
    )


def test_apply_archive_is_idempotent() -> None:
    artifact = _make_artifact()
    check(
        "FTF-0301", "the first archive reports a real transition", apply_archive(artifact) is True
    )
    check("FTF-0301", "the first archive sets status=archived", artifact.status == "archived")
    check("FTF-0301", "the first archive sets archived_at", artifact.archived_at is not None)
    archived_at_after_first = artifact.archived_at

    check(
        "FTF-0301",
        "acceptance: archiving an already-archived artifact is idempotent",
        apply_archive(artifact) is False,
    )
    check(
        "FTF-0301",
        "a second archive does not move archived_at forward",
        artifact.archived_at == archived_at_after_first,
    )


def test_serialize_reports_license_and_status_fields() -> None:
    artifact = _make_artifact()
    body = _serialize(artifact)
    check(
        "FTF-0301",
        "the serialized response carries every real field this task's own domain model owns",
        {
            "id",
            "organization_id",
            "display_name",
            "source_type",
            "source_uri",
            "model_family",
            "base_model_revision",
            "weights_digest",
            "license_id",
            "license_accepted_by",
            "license_accepted_at",
            "trust_remote_code_allowed",
            "required_python_packages",
            "minimum_gpu_profile",
            "status",
            "created_at",
            "updated_at",
            "archived_at",
        }
        <= body.keys(),
    )


def test_role_split_matches_section_45() -> None:
    check(
        "FTF-0301",
        "Section 45: 'ml_engineer: datasets/training, no production approval' "
        "is the only sentence naming a role for this class of work -- "
        "ARTIFACT_WRITE_ROLES is exactly {ml_engineer, organization_admin}",
        ARTIFACT_WRITE_ROLES == frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "FTF-0301",
        "ARTIFACT_READ_ROLES includes every role Section 45 names (no dedicated "
        "restrictive sentence exists for artifact reads)",
        ARTIFACT_READ_ROLES == frozenset(Role),
    )


# -- 5. Real demo router: role-boundary denial never opens a session --------


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
_ORG_ID = uuid.uuid4()


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
        build_trainable_artifact_router(
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


def _request(base_url: str, method: str, path: str, *, principal: str | None = None) -> _HttpResult:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    parsed = urllib.parse.urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        response.read()
        return _HttpResult(status=response.status)
    finally:
        conn.close()


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", f"/api/v1/organizations/{_ORG_ID}/artifacts")
        check(
            "FTF-0301",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-0301",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_can_read_but_not_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url, "GET", f"/api/v1/organizations/{_ORG_ID}/artifacts", principal="evaluator"
        )
        check(
            "FTF-0301",
            "an evaluator (a role Section 45 names, with no dedicated "
            "restrictive sentence for artifacts) can read (not 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0301",
            "the evaluator's read genuinely reaches the session layer",
            session_factory.call_count >= 1,
        )

        session_factory.reset_mock()
        result = _request(
            base_url, "POST", f"/api/v1/organizations/{_ORG_ID}/artifacts", principal="evaluator"
        )
        check(
            "FTF-0301",
            "an evaluator is denied create (403) -- write is ml_engineer/organization_admin only",
            result.status == 403,
        )
        check(
            "FTF-0301",
            "the write role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_ml_engineer_reaches_the_session_layer_for_writes() -> None:
    # Uses the archive route (DELETE, no request body) to prove the *write*
    # role gate specifically passes an ml_engineer through to the session
    # layer -- POST create is not used here because it would also need a
    # real JSON body to get past `_read_json_body` before ever reaching
    # `tenant_scoped_session_for`, which would conflate two different
    # failure points in one assertion.
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "DELETE",
            f"/api/v1/organizations/{_ORG_ID}/artifacts/{uuid.uuid4()}",
            principal="ml_engineer",
        )
        check(
            "FTF-0301",
            "an ml_engineer's archive request passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0301",
            "the ml_engineer's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_require_role_is_the_real_imported_dependency_not_a_duplicate() -> None:
    import ast
    import inspect

    from app.artifacts import artifact_crud, artifact_routes
    from app.auth.rbac_middleware import require_role

    routes_source = inspect.getsource(artifact_routes)
    check(
        "FTF-0301",
        "artifact_routes.py builds its dependencies from the real, imported "
        "require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(artifact_crud)
    check(
        "FTF-0301",
        "artifact_crud.py authorizes every fetch through the real, imported "
        "authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    tree = ast.parse(crud_source)
    calls_session_get = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "session"
        for node in ast.walk(tree)
    )
    check(
        "FTF-0301",
        "artifact_crud.py reads every row through the real, imported "
        "execute_tenant_scoped (FTF-0105) -- never a bare session.get(...) call",
        "execute_tenant_scoped(" in crud_source
        and "app.auth.tenant_isolation_query_guards" in crud_source
        and not calls_session_get,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0301 TrainableArtifact schema/API tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0301 TrainableArtifact schema/API tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
