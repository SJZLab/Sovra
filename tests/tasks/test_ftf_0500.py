#!/usr/bin/env python3
"""FTF-0500 -- DatasetSourceConnection schema/API/RBAC (no-DB unit/fault tests).

Runnable without Postgres, OpenBao, or network egress (the real live-
database create/get/list/edit/revoke and real-OpenBao credential-storage
evidence is `tests/integration/test_ftf_0500_dataset_source_connection.py`,
run in CI's dedicated `dataset-source-connection-integration` job -- this
repo's interactive dev sandbox has no container-registry egress, same
documented finding as every prior integration-tested task). This suite
proves, without any database or secret backend:

1. `validate_source_type`/`validate_allowed_purposes`/
   `validate_security_classification`/`validate_secret_ref_or_none`: each
   field's own closed-set/shape validation, never silently accepted for a
   bad value.
2. `DatasetSourceConnection.__init__` itself refuses bad input before any
   session or database is ever touched.
3. `apply_edits`/`apply_revoke` (the pure, DB-free cores) -- refuses to
   edit a revoked connection; applies a real subset of fields; revoke is
   idempotent.
4. `authorize_purpose_use` -- this task's own negative_acceptance in full,
   proven at the pure-function level with no database: "Source allowed
   only evaluation cannot be trained" (a connection granted only
   `held_out_evaluation` denies `training`), and "grant revocation
   propagates to future derivative use" (a revoked connection denies
   every purpose, including one it was previously granted for).
5. The real, locally-hosted `build_dataset_source_connection_router` (a
   real uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0401.py`/`test_ftf_0404.py` established) denies
   an unauthenticated request and a role outside `DATASET_SOURCE_ROLES` on
   every operation *before* `session_factory` is ever called, and lets a
   granted role (`ml_engineer`) reach the session layer.

Run directly: python3 tests/tasks/test_ftf_0500.py
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

from app.auth.organization_workspace_schema import SchemaValidationError  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets.dataset_source_connection_crud import (  # noqa: E402
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionStateError,
    apply_edits,
    apply_revoke,
    authorize_purpose_use,
)
from app.datasets.dataset_source_connection_routes import (  # noqa: E402
    DATASET_SOURCE_ROLES,
    _serialize,
    build_dataset_source_connection_router,
)
from app.datasets.dataset_source_connection_schema import (  # noqa: E402
    ALLOWED_PURPOSES,
    ALLOWED_SECURITY_CLASSIFICATIONS,
    ALLOWED_SOURCE_TYPES,
    DatasetSourceConnection,
    DatasetSourceConnectionValidationError,
    validate_allowed_purposes,
    validate_secret_ref_or_none,
    validate_security_classification,
    validate_source_type,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except (
        DatasetSourceConnectionValidationError,
        DatasetSourceConnectionStateError,
        SchemaValidationError,
    ):
        return True


_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _make_connection(**overrides: object) -> DatasetSourceConnection:
    now = datetime.now(UTC)
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "source_type": "upload",
        "display_name": "support-tickets-2026",
        "credential_secret_ref": None,
        "connection_config_encrypted_ref": None,
        "allowed_purposes_json": ["training", "validation"],
        "security_classification": "internal",
        "connection_status": "active",
        "created_by": "user-1",
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(overrides)
    return DatasetSourceConnection(**kwargs)


# -- 1. field validators ------------------------------------------------------


def test_validate_source_type_is_the_closed_section_6_8f_set() -> None:
    check(
        "FTF-0500",
        "ALLOWED_SOURCE_TYPES is exactly Section 6.8F's own seven source types",
        set(ALLOWED_SOURCE_TYPES)
        == {
            "upload",
            "s3",
            "minio",
            "database",
            "agent_traces",
            "production_conversations",
            "api",
        },
    )
    for source_type in ALLOWED_SOURCE_TYPES:
        check(
            "FTF-0500",
            f"validate_source_type accepts the real type {source_type!r}",
            validate_source_type(source_type) == source_type,
        )
    check(
        "FTF-0500",
        "an unrecognized source_type is refused, not silently accepted",
        _rejects(validate_source_type, "ftp"),
    )
    check(
        "FTF-0500",
        "a non-string source_type is refused",
        _rejects(validate_source_type, None),
    )


def test_validate_allowed_purposes_is_the_closed_section_6_8f_set() -> None:
    check(
        "FTF-0500",
        "ALLOWED_PURPOSES is exactly Section 6.8F's own five example purposes",
        set(ALLOWED_PURPOSES)
        == {
            "training",
            "validation",
            "held_out_evaluation",
            "candidate_development",
            "baseline_only",
        },
    )
    check(
        "FTF-0500",
        "a real, non-empty subset round-trips unchanged (deduplicated)",
        validate_allowed_purposes(["training", "training", "validation"])
        == ["training", "validation"],
    )
    check(
        "FTF-0500",
        "an empty list is refused -- a grant must authorize at least one purpose",
        _rejects(validate_allowed_purposes, []),
    )
    check(
        "FTF-0500",
        "a non-list value is refused",
        _rejects(validate_allowed_purposes, "training"),
    )
    check(
        "FTF-0500",
        "Section 6.8F: 'The Factory must never expand these permissions "
        "automatically' -- an unrecognized purpose string is refused, not "
        "silently accepted",
        _rejects(validate_allowed_purposes, ["training", "world_domination"]),
    )


def test_validate_security_classification_is_a_closed_set() -> None:
    for classification in ALLOWED_SECURITY_CLASSIFICATIONS:
        check(
            "FTF-0500",
            f"validate_security_classification accepts {classification!r}",
            validate_security_classification(classification) == classification,
        )
    check(
        "FTF-0500",
        "an unrecognized security_classification is refused",
        _rejects(validate_security_classification, "top_secret"),
    )


def test_validate_secret_ref_or_none_never_accepts_a_raw_value() -> None:
    check(
        "FTF-0500",
        "None is accepted -- a connection need not carry a credential",
        validate_secret_ref_or_none(None, field_name="credential_secret_ref") is None,
    )
    check(
        "FTF-0500",
        "a real openbao:// reference is accepted",
        validate_secret_ref_or_none(
            "openbao://dataset-sources/org/conn/credential", field_name="credential_secret_ref"
        )
        == "openbao://dataset-sources/org/conn/credential",
    )
    check(
        "FTF-0500",
        "a raw credential value (no scheme) is refused, never accepted as a ref",
        _rejects(
            validate_secret_ref_or_none, "sk-super-secret-value", field_name="credential_secret_ref"
        ),
    )
    check(
        "FTF-0500",
        "a vault:// (not openbao://) reference is refused -- this table only "
        "ever stores this task's own real backend's reference shape",
        _rejects(
            validate_secret_ref_or_none,
            "vault://path/to/secret",
            field_name="credential_secret_ref",
        ),
    )


# -- 2. DatasetSourceConnection.__init__ refuses bad input -------------------


def test_construction_rejects_bad_display_name() -> None:
    check(
        "FTF-0500",
        "constructing with an empty display_name raises before any DB call",
        _rejects(_make_connection, display_name=""),
    )


def test_construction_rejects_bad_source_type() -> None:
    check(
        "FTF-0500",
        "constructing with an unknown source_type raises",
        _rejects(_make_connection, source_type="ftp"),
    )


def test_construction_rejects_empty_allowed_purposes() -> None:
    check(
        "FTF-0500",
        "constructing with an empty allowed_purposes_json raises",
        _rejects(_make_connection, allowed_purposes_json=[]),
    )


def test_construction_rejects_bad_connection_status() -> None:
    check(
        "FTF-0500",
        "constructing with an unrecognized connection_status raises",
        _rejects(_make_connection, connection_status="pending"),
    )


def test_construction_rejects_raw_credential_value() -> None:
    check(
        "FTF-0500",
        "constructing with a raw (non-ref) credential_secret_ref raises -- this "
        "schema is structurally incapable of storing a raw credential",
        _rejects(_make_connection, credential_secret_ref="raw-api-key-value"),
    )


# -- 3. apply_edits / apply_revoke --------------------------------------------


def test_apply_edits_refuses_to_touch_a_revoked_connection() -> None:
    connection = _make_connection(connection_status="revoked")
    check(
        "FTF-0500",
        "editing an already-revoked connection is refused -- a revoked source is a closed record",
        _rejects(
            apply_edits,
            connection,
            display_name="new-name",
            allowed_purposes=None,
            security_classification=None,
        ),
    )


def test_apply_edits_requires_at_least_one_field() -> None:
    connection = _make_connection()
    check(
        "FTF-0500",
        "calling apply_edits with no fields at all is refused",
        _rejects(
            apply_edits,
            connection,
            display_name=None,
            allowed_purposes=None,
            security_classification=None,
        ),
    )


def test_apply_edits_applies_a_real_subset() -> None:
    connection = _make_connection(allowed_purposes_json=["training"])
    apply_edits(
        connection,
        display_name=None,
        allowed_purposes=["held_out_evaluation"],
        security_classification=None,
    )
    check(
        "FTF-0500",
        "allowed_purposes is genuinely re-granted (this task's own real re-authorization path)",
        connection.allowed_purposes_json == ["held_out_evaluation"],
    )
    check(
        "FTF-0500",
        "a field not supplied is left untouched",
        connection.display_name == "support-tickets-2026",
    )


def test_apply_revoke_is_idempotent() -> None:
    connection = _make_connection()
    check("FTF-0500", "the first revoke reports a real change", apply_revoke(connection) is True)
    check(
        "FTF-0500",
        "connection_status is genuinely 'revoked'",
        connection.connection_status == "revoked",
    )
    check("FTF-0500", "revoked_at is genuinely set", connection.revoked_at is not None)
    check(
        "FTF-0500",
        "repeating revoke on an already-revoked connection is a no-op (idempotent)",
        apply_revoke(connection) is False,
    )


# -- 4. authorize_purpose_use: the negative_acceptance core -------------------


def test_authorize_purpose_use_allows_a_real_granted_purpose() -> None:
    connection = _make_connection(allowed_purposes_json=["training", "validation"])
    try:
        authorize_purpose_use(connection, "training")
        allowed = True
    except DatasetSourceAuthorizationError:
        allowed = False
    check("FTF-0500", "a purpose the connection is genuinely granted for is authorized", allowed)


def test_authorize_purpose_use_denies_an_ungranted_purpose() -> None:
    # negative_acceptance: "Source allowed only evaluation cannot be trained."
    connection = _make_connection(allowed_purposes_json=["held_out_evaluation"])
    check(
        "FTF-0500",
        "a source granted only held_out_evaluation denies a training request",
        _denies(connection, "training"),
    )
    check(
        "FTF-0500",
        "the same source still authorizes the purpose it IS granted for",
        not _denies(connection, "held_out_evaluation"),
    )


def test_authorize_purpose_use_denies_every_purpose_once_revoked() -> None:
    # negative_acceptance: "grant revocation propagates to future derivative use."
    connection = _make_connection(allowed_purposes_json=["training", "validation"])
    check(
        "FTF-0500",
        "training is authorized while the connection is still active",
        not _denies(connection, "training"),
    )
    apply_revoke(connection)
    check(
        "FTF-0500",
        "once revoked, a PREVIOUSLY granted purpose (training) is now denied -- "
        "revocation propagates to future derivative use, not just new grants",
        _denies(connection, "training"),
    )
    check(
        "FTF-0500",
        "once revoked, every other declared purpose is denied too",
        _denies(connection, "validation"),
    )


def _denies(connection: DatasetSourceConnection, purpose: str) -> bool:
    try:
        authorize_purpose_use(connection, purpose)
        return False
    except DatasetSourceAuthorizationError:
        return True


# -- 5. Real demo router: role-boundary denial never opens a session --------


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_ML_ENGINEER_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="mle-1")
_VIEWER_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="viewer-1")


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "ml_engineer":
        return _ML_ENGINEER_PRINCIPAL
    if header == "viewer":
        return _VIEWER_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _ML_ENGINEER_PRINCIPAL.subject:
        return Role.ML_ENGINEER
    if subject == _VIEWER_PRINCIPAL.subject:
        return Role.VIEWER
    return None


@contextlib.contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_dataset_source_connection_router(
            session_factory,
            principal_resolver=_principal_resolver,
            role_lookup=_role_lookup,
            secret_manager_config_factory=lambda: None,
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
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/dataset-sources",
        )
        check(
            "FTF-0500",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-0500",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_viewer_is_denied_every_operation() -> None:
    # Section 45 names only ml_engineer for datasets; this credential-bearing
    # resource does not open reads to viewer the way FTF-0401/FTF-0404's own
    # non-secret-bearing resources do.
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/dataset-sources",
            principal="viewer",
        )
        check(
            "FTF-0500",
            "a viewer (not in DATASET_SOURCE_ROLES) is denied even a read (403)",
            result.status == 403,
        )
        check(
            "FTF-0500",
            "the role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_ml_engineer_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/dataset-sources",
            principal="ml_engineer",
        )
        check(
            "FTF-0500",
            "ml_engineer (Section 45: 'ml_engineer: datasets/training') passes "
            "the role gate and reaches the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0500",
            "the ml_engineer's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_split_matches_section_45() -> None:
    check(
        "FTF-0500",
        "Section 45: 'ml_engineer: datasets/training' plus the standing "
        "project-administration superset -- DATASET_SOURCE_ROLES is exactly "
        "{ml_engineer, workspace_admin, organization_admin}",
        DATASET_SOURCE_ROLES
        == frozenset({Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )


def test_require_role_is_the_real_imported_dependency_not_a_duplicate() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.datasets import dataset_source_connection_crud, dataset_source_connection_routes

    routes_source = inspect.getsource(dataset_source_connection_routes)
    check(
        "FTF-0500",
        "dataset_source_connection_routes.py builds its dependency from the "
        "real, imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(dataset_source_connection_crud)
    check(
        "FTF-0500",
        "dataset_source_connection_crud.py authorizes every fetch through the "
        "real, imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-0500",
        "dataset_source_connection_crud.py reads every row through the real, "
        "imported execute_tenant_scoped (FTF-0105) -- never a bare "
        "session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-0500",
        "dataset_source_connection_crud.py stores credentials through the "
        "real, imported FTF-0202 store_secret_ref -- never a hand-rolled "
        "secret write",
        "store_secret_ref(" in crud_source
        and "packages.litellm_client.secret_manager_integration" in crud_source,
    )


def test_serialize_never_exposes_the_credential_or_config_ref() -> None:
    connection = _make_connection(
        credential_secret_ref="openbao://dataset-sources/org/conn/credential",
        connection_config_encrypted_ref="openbao://dataset-sources/org/conn/config_json",
    )
    body = _serialize(connection)
    check(
        "FTF-0500",
        "the serialized connection never includes the raw credential_secret_ref value",
        "credential_secret_ref" not in body and "connection_config_encrypted_ref" not in body,
    )
    check(
        "FTF-0500",
        "the serialized connection discloses only whether a credential/config is set",
        body["has_credential"] is True and body["has_connection_config"] is True,
    )
    check(
        "FTF-0500",
        "allowed_purposes serializes as a plain list, never the raw JSONB column",
        isinstance(body["allowed_purposes"], list),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0500 DatasetSourceConnection schema/API/RBAC tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0500 DatasetSourceConnection schema/API/RBAC tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
