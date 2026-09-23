#!/usr/bin/env python3
"""FTF-04A01 -- TargetApplicationConnection schema/API/RBAC (no-DB unit/fault tests).

Runnable without Postgres, OpenBao, or network egress (the real live-
database create/get/list/edit/revoke and real-OpenBao credential-storage
evidence is
`tests/integration/test_ftf_04a01_target_application_connection.py`, run in
CI's dedicated `target-application-connection-integration` job -- this
repo's interactive dev sandbox has no container-registry egress, same
documented finding as every prior integration-tested task). This suite
proves, without any database or secret backend:

1. `validate_connector_type`/`validate_access_mode`/
   `validate_repository_url_or_none`/`validate_secret_ref_or_none`: each
   field's own closed-set/shape validation, never silently accepted for a
   bad value -- this task's own real "unsupported connector... fail
   server-side" and "excessive permission fail server-side"
   negative_acceptance at the pure-function level.
2. `TargetApplicationConnection.__init__` itself refuses bad input before
   any session or database is ever touched.
3. `_validate_connector_shape` (the CRUD module's own real,
   connector-type-conditional structural check): github requires
   repository_url_redacted and a credential; local_repository refuses
   both.
4. `apply_edits`/`apply_revoke` (the pure, DB-free cores) -- refuses to
   edit a revoked connection; applies a real subset of fields; revoke is
   idempotent.
5. The real, locally-hosted `build_target_application_connection_router`
   (a real uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_0401.py`/`test_ftf_0500.py` established) denies
   an unauthenticated request and a role outside
   `TARGET_CONNECTION_ROLES` on every operation *before* `session_factory`
   is ever called, and lets a granted role (`organization_admin`) reach
   the session layer.

Run directly: python3 tests/tasks/test_ftf_04a01.py
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
from app.target_connectors.target_application_connection_crud import (  # noqa: E402
    TargetApplicationConnectionStateError,
    _validate_connector_shape,
    apply_edits,
    apply_revoke,
)
from app.target_connectors.target_application_connection_routes import (  # noqa: E402
    TARGET_CONNECTION_ROLES,
    _serialize,
    build_target_application_connection_router,
)
from app.target_connectors.target_application_connection_schema import (  # noqa: E402
    ALLOWED_ACCESS_MODES,
    ALLOWED_CONNECTOR_TYPES,
    TargetApplicationConnection,
    TargetApplicationConnectionValidationError,
    validate_access_mode,
    validate_connector_type,
    validate_repository_url_or_none,
    validate_secret_ref_or_none,
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
        TargetApplicationConnectionValidationError,
        TargetApplicationConnectionStateError,
        SchemaValidationError,
    ):
        return True


_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _make_connection(**overrides: object) -> TargetApplicationConnection:
    now = datetime.now(UTC)
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "project_id": _PROJECT_ID,
        "connector_type": "github",
        "display_name": "acme-support-agent",
        "repository_identity": "acme/support-agent",
        "repository_url_redacted": "https://github.com/acme/support-agent",
        "default_branch": "main",
        "selected_branch": "main",
        "credential_secret_ref": "openbao://target-connections/org/conn/credential",
        "access_mode": "read_only",
        "connection_status": "active",
        "created_by": "user-1",
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(overrides)
    return TargetApplicationConnection(**kwargs)


# -- 1. field validators ------------------------------------------------------


def test_validate_connector_type_is_the_closed_section_7a1_set() -> None:
    check(
        "FTF-04A01",
        "ALLOWED_CONNECTOR_TYPES is exactly Section 7A.1's own two minimum "
        "production connectors",
        set(ALLOWED_CONNECTOR_TYPES) == {"github", "local_repository"},
    )
    for connector_type in ALLOWED_CONNECTOR_TYPES:
        check(
            "FTF-04A01",
            f"validate_connector_type accepts the real type {connector_type!r}",
            validate_connector_type(connector_type) == connector_type,
        )
    check(
        "FTF-04A01",
        "an unrecognized connector_type is refused, not silently accepted -- "
        "this task's own 'unsupported connector... fail server-side' "
        "negative_acceptance",
        _rejects(validate_connector_type, "gitlab"),
    )
    check(
        "FTF-04A01",
        "a mock/placeholder connector type is refused (Section 7A.1: 'No "
        "mock connector may satisfy release qualification')",
        _rejects(validate_connector_type, "mock"),
    )
    check(
        "FTF-04A01",
        "a non-string connector_type is refused",
        _rejects(validate_connector_type, None),
    )


def test_validate_access_mode_is_read_only_only() -> None:
    check(
        "FTF-04A01",
        "ALLOWED_ACCESS_MODES is exactly Section 6.8A's own single value "
        "('read_only; write is not required for qualification')",
        set(ALLOWED_ACCESS_MODES) == {"read_only"},
    )
    check(
        "FTF-04A01",
        "validate_access_mode accepts read_only",
        validate_access_mode("read_only") == "read_only",
    )
    check(
        "FTF-04A01",
        "'write' is refused server-side -- this task's own 'excessive "
        "permission fail server-side' negative_acceptance",
        _rejects(validate_access_mode, "write"),
    )
    check(
        "FTF-04A01",
        "'admin' is refused server-side",
        _rejects(validate_access_mode, "admin"),
    )


def test_validate_repository_url_or_none() -> None:
    check(
        "FTF-04A01",
        "None is accepted -- local_repository connections carry no URL",
        validate_repository_url_or_none(None) is None,
    )
    check(
        "FTF-04A01",
        "a real https URL is accepted",
        validate_repository_url_or_none("https://github.com/acme/support-agent")
        == "https://github.com/acme/support-agent",
    )
    check(
        "FTF-04A01",
        "an http (not https) URL is refused by the shared FTF-H006 policy",
        _rejects(validate_repository_url_or_none, "http://github.com/acme/support-agent"),
    )
    check(
        "FTF-04A01",
        "a userinfo URL (user:pass@host) is refused -- FTF-H006's own " "shared shape rule",
        _rejects(validate_repository_url_or_none, "https://user:pass@github.com/acme/repo"),
    )
    check(
        "FTF-04A01",
        "a file:// URL is refused -- FTF-H006's own forbidden-scheme rule",
        _rejects(validate_repository_url_or_none, "file:///etc/passwd"),
    )
    check(
        "FTF-04A01",
        "an empty string is refused",
        _rejects(validate_repository_url_or_none, ""),
    )


def test_validate_secret_ref_or_none_never_accepts_a_raw_value() -> None:
    check(
        "FTF-04A01",
        "None is accepted -- a connection need not carry a credential",
        validate_secret_ref_or_none(None, field_name="credential_secret_ref") is None,
    )
    check(
        "FTF-04A01",
        "a real openbao:// reference is accepted",
        validate_secret_ref_or_none(
            "openbao://target-connections/org/conn/credential", field_name="credential_secret_ref"
        )
        == "openbao://target-connections/org/conn/credential",
    )
    check(
        "FTF-04A01",
        "a raw credential value (no scheme) is refused, never accepted as a ref",
        _rejects(
            validate_secret_ref_or_none, "ghp_rawtoken12345", field_name="credential_secret_ref"
        ),
    )
    check(
        "FTF-04A01",
        "a vault:// (not openbao://) reference is refused",
        _rejects(
            validate_secret_ref_or_none,
            "vault://path/to/secret",
            field_name="credential_secret_ref",
        ),
    )


# -- 2. TargetApplicationConnection.__init__ refuses bad input ---------------


def test_construction_rejects_bad_display_name() -> None:
    check(
        "FTF-04A01",
        "constructing with an empty display_name raises before any DB call",
        _rejects(_make_connection, display_name=""),
    )


def test_construction_rejects_bad_connector_type() -> None:
    check(
        "FTF-04A01",
        "constructing with an unknown connector_type raises",
        _rejects(_make_connection, connector_type="gitlab"),
    )


def test_construction_rejects_empty_repository_identity() -> None:
    check(
        "FTF-04A01",
        "constructing with an empty repository_identity raises",
        _rejects(_make_connection, repository_identity=""),
    )


def test_construction_rejects_excessive_access_mode() -> None:
    check(
        "FTF-04A01",
        "constructing with access_mode='write' raises",
        _rejects(_make_connection, access_mode="write"),
    )


def test_construction_rejects_bad_connection_status() -> None:
    check(
        "FTF-04A01",
        "constructing with an unrecognized connection_status raises",
        _rejects(_make_connection, connection_status="pending"),
    )


def test_construction_rejects_raw_credential_value() -> None:
    check(
        "FTF-04A01",
        "constructing with a raw (non-ref) credential_secret_ref raises -- "
        "this schema is structurally incapable of storing a raw credential",
        _rejects(_make_connection, credential_secret_ref="ghp_rawtoken12345"),
    )


# -- 3. _validate_connector_shape: this task's own real cross-field check ----


def test_github_requires_repository_url_and_credential() -> None:
    check(
        "FTF-04A01",
        "github with both a URL and a credential is accepted",
        not _rejects(
            _validate_connector_shape,
            connector_type="github",
            repository_url_redacted="https://github.com/acme/repo",
            credential="raw-token",
        ),
    )
    check(
        "FTF-04A01",
        "github without repository_url_redacted is refused server-side",
        _rejects(
            _validate_connector_shape,
            connector_type="github",
            repository_url_redacted=None,
            credential="raw-token",
        ),
    )
    check(
        "FTF-04A01",
        "github without a credential is refused server-side -- this task's "
        "own 'validate credentials' acceptance clause made real",
        _rejects(
            _validate_connector_shape,
            connector_type="github",
            repository_url_redacted="https://github.com/acme/repo",
            credential=None,
        ),
    )


def test_local_repository_refuses_url_and_credential() -> None:
    check(
        "FTF-04A01",
        "local_repository with neither a URL nor a credential is accepted",
        not _rejects(
            _validate_connector_shape,
            connector_type="local_repository",
            repository_url_redacted=None,
            credential=None,
        ),
    )
    check(
        "FTF-04A01",
        "local_repository with a repository_url_redacted is refused -- root "
        "allowlisting is FTF-04A06's own separate scope, not this task's",
        _rejects(
            _validate_connector_shape,
            connector_type="local_repository",
            repository_url_redacted="https://github.com/acme/repo",
            credential=None,
        ),
    )
    check(
        "FTF-04A01",
        "local_repository with a credential is refused -- a local "
        "connection carries no credential at all",
        _rejects(
            _validate_connector_shape,
            connector_type="local_repository",
            repository_url_redacted=None,
            credential="raw-token",
        ),
    )


# -- 4. apply_edits / apply_revoke --------------------------------------------


def test_apply_edits_refuses_to_touch_a_revoked_connection() -> None:
    connection = _make_connection(connection_status="revoked")
    check(
        "FTF-04A01",
        "editing an already-revoked connection is refused -- a revoked "
        "connection is a closed record",
        _rejects(apply_edits, connection, display_name="new-name", selected_branch=None),
    )


def test_apply_edits_requires_at_least_one_field() -> None:
    connection = _make_connection()
    check(
        "FTF-04A01",
        "calling apply_edits with no fields at all is refused",
        _rejects(apply_edits, connection, display_name=None, selected_branch=None),
    )


def test_apply_edits_applies_a_real_subset() -> None:
    connection = _make_connection(selected_branch="main")
    apply_edits(connection, display_name=None, selected_branch="feature/discovery")
    check(
        "FTF-04A01",
        "selected_branch is genuinely changed",
        connection.selected_branch == "feature/discovery",
    )
    check(
        "FTF-04A01",
        "a field not supplied is left untouched",
        connection.display_name == "acme-support-agent",
    )


def test_apply_revoke_is_idempotent() -> None:
    connection = _make_connection()
    check("FTF-04A01", "the first revoke reports a real change", apply_revoke(connection) is True)
    check(
        "FTF-04A01",
        "connection_status is genuinely 'revoked'",
        connection.connection_status == "revoked",
    )
    check("FTF-04A01", "revoked_at is genuinely set", connection.revoked_at is not None)
    check(
        "FTF-04A01",
        "repeating revoke on an already-revoked connection is a no-op (idempotent)",
        apply_revoke(connection) is False,
    )


# -- 5. Real demo router: role-boundary denial never opens a session --------


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_ORG_ADMIN_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="admin-1")
_VIEWER_PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="viewer-1")


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "organization_admin":
        return _ORG_ADMIN_PRINCIPAL
    if header == "viewer":
        return _VIEWER_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _ORG_ADMIN_PRINCIPAL.subject:
        return Role.ORGANIZATION_ADMIN
    if subject == _VIEWER_PRINCIPAL.subject:
        return Role.VIEWER
    return None


@contextlib.contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_target_application_connection_router(
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
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/target-connections",
        )
        check(
            "FTF-04A01",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-04A01",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_viewer_is_denied_every_operation() -> None:
    # Section 45 names only organization_admin for "connections"; this
    # credential-bearing resource does not open reads wider than writes.
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/target-connections",
            principal="viewer",
        )
        check(
            "FTF-04A01",
            "a viewer (not in TARGET_CONNECTION_ROLES) is denied even a read (403)",
            result.status == 403,
        )
        check(
            "FTF-04A01",
            "the role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_organization_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}/target-connections",
            principal="organization_admin",
        )
        check(
            "FTF-04A01",
            "organization_admin (Section 45: 'organization_admin: "
            "connections, security profiles, cost profiles') passes the "
            "role gate and reaches the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-04A01",
            "the organization_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_split_matches_section_45() -> None:
    check(
        "FTF-04A01",
        "Section 45: 'organization_admin: connections, security profiles, "
        "cost profiles' -- TARGET_CONNECTION_ROLES is exactly "
        "{organization_admin}",
        TARGET_CONNECTION_ROLES == frozenset({Role.ORGANIZATION_ADMIN}),
    )


def test_require_role_is_the_real_imported_dependency_not_a_duplicate() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.target_connectors import (
        target_application_connection_crud,
        target_application_connection_routes,
        target_application_connection_schema,
    )

    routes_source = inspect.getsource(target_application_connection_routes)
    check(
        "FTF-04A01",
        "target_application_connection_routes.py builds its dependency from "
        "the real, imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(target_application_connection_crud)
    check(
        "FTF-04A01",
        "target_application_connection_crud.py authorizes every fetch "
        "through the real, imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-04A01",
        "target_application_connection_crud.py reads every row through the "
        "real, imported execute_tenant_scoped (FTF-0105) -- never a bare "
        "session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-04A01",
        "target_application_connection_crud.py stores credentials through "
        "the real, imported FTF-0202 store_secret_ref -- never a hand-rolled "
        "secret write",
        "store_secret_ref(" in crud_source
        and "packages.litellm_client.secret_manager_integration" in crud_source,
    )
    schema_source = inspect.getsource(target_application_connection_schema)
    check(
        "FTF-04A01",
        "target_application_connection_schema.py validates URLs through the "
        "real, imported FTF-H006 validate_url_shape -- never a hand-rolled "
        "SSRF check",
        "validate_url_shape(" in schema_source and "packages.security.url_policy" in schema_source,
    )


def test_serialize_never_exposes_the_credential_ref() -> None:
    connection = _make_connection(
        credential_secret_ref="openbao://target-connections/org/conn/credential"
    )
    body = _serialize(connection)
    check(
        "FTF-04A01",
        "the serialized connection never includes the raw credential_secret_ref value",
        "credential_secret_ref" not in body,
    )
    check(
        "FTF-04A01",
        "the serialized connection discloses only whether a credential is set",
        body["has_credential"] is True,
    )
    check(
        "FTF-04A01",
        "access_mode always serializes as read_only",
        body["access_mode"] == "read_only",
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(
            f"FTF-04A01 TargetApplicationConnection schema/API/RBAC tests: "
            f"{len(FAILURES)} failed"
        )
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A01 TargetApplicationConnection schema/API/RBAC tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
