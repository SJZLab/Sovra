#!/usr/bin/env python3
"""FTF-04A17 -- DiscoveredAgentCandidate schema/API (no-DB unit/fault
tests).

Runnable without Postgres or network egress (the real live-database
create/get/list, cross-tenant IDOR, cross-snapshot reassignment-refusal,
duplicate-candidate-key and grant-surface/CHECK-constraint evidence is
`tests/integration/test_ftf_04a17_discovered_agent_candidate.py`, run in
CI's dedicated integration job, same pattern as FTF-04A07's own split).
This suite proves, without any database:

1. `validate_candidate_key`/`validate_display_name`/
   `validate_framework_or_runtime_or_none`/`validate_evidence_json_or_none`/
   `validate_confidence_score`/`validate_discovery_status`: real, bounded/
   closed-set/shape validation, never silently accepted.
2. `DiscoveredAgentCandidate.__init__` itself refuses every structurally
   inconsistent value this task's own negative_acceptance implies before
   any session or database is ever touched: an out-of-range
   confidence_score, a discovery_status outside the closed Release-1 set,
   and a non-object evidence payload.
3. The real, locally-hosted `build_discovered_agent_candidate_router` (a
   real uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_04a07.py` established) denies an unauthenticated
   request and a role outside `DISCOVERED_CANDIDATE_ROLES` *before*
   `session_factory` is ever called.
4. `discovered_agent_candidate_crud.py`/`discovered_agent_candidate_routes.py`
   compose the same certified primitives every prior task in this chain
   already established -- including FTF-04A07's own
   `get_target_source_snapshot` directly, reuse-first -- no new,
   hand-rolled authorization/tenant-scoping primitive is introduced.

Run directly: python3 tests/tasks/test_ftf_04a17.py
"""

from __future__ import annotations

import contextlib
import http.client
import os
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

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.target_connectors.discovered_agent_candidate_crud import (  # noqa: E402
    create_discovered_agent_candidate,
    get_discovered_agent_candidate,
    list_discovered_agent_candidates,
)
from app.target_connectors.discovered_agent_candidate_routes import (  # noqa: E402
    DISCOVERED_CANDIDATE_ROLES,
    _serialize,
    build_discovered_agent_candidate_router,
)
from app.target_connectors.discovered_agent_candidate_schema import (  # noqa: E402
    ALLOWED_DISCOVERY_STATUSES,
    DiscoveredAgentCandidate,
    DiscoveredAgentCandidateValidationError,
    validate_candidate_key,
    validate_confidence_score,
    validate_discovery_status,
    validate_display_name,
    validate_evidence_json_or_none,
    validate_framework_or_runtime_or_none,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except DiscoveredAgentCandidateValidationError:
        return True


_ORG_ID = uuid.uuid4()
_SNAPSHOT_ID = uuid.uuid4()


def _make_candidate(**overrides: object) -> DiscoveredAgentCandidate:
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "target_source_snapshot_id": _SNAPSHOT_ID,
        "candidate_key": "entrypoint:apps/api/agent.py:AgentClass",
        "display_name": "Primary support agent",
        "confidence_score": 0.5,
        "discovery_status": "candidate",
        "created_at": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return DiscoveredAgentCandidate(**kwargs)


# -- 1. field validators ------------------------------------------------


def test_validate_candidate_key_accepts_real_and_rejects_bad_shapes() -> None:
    check(
        "FTF-04A17",
        "a real, non-empty candidate_key round-trips unchanged",
        validate_candidate_key("entrypoint:app.py:Agent") == "entrypoint:app.py:Agent",
    )
    check(
        "FTF-04A17",
        "an empty candidate_key is rejected",
        _rejects(validate_candidate_key, ""),
    )
    check(
        "FTF-04A17",
        "a non-string candidate_key is rejected",
        _rejects(validate_candidate_key, None),
    )
    check(
        "FTF-04A17",
        "an oversized candidate_key is rejected",
        _rejects(validate_candidate_key, "x" * 513),
    )


def test_validate_display_name_accepts_real_and_rejects_bad_shapes() -> None:
    check(
        "FTF-04A17",
        "a real, non-empty display_name round-trips unchanged",
        validate_display_name("Support Agent") == "Support Agent",
    )
    check(
        "FTF-04A17",
        "an empty display_name is rejected",
        _rejects(validate_display_name, ""),
    )
    check(
        "FTF-04A17",
        "a non-string display_name is rejected",
        _rejects(validate_display_name, 42),
    )


def test_validate_framework_or_runtime_or_none() -> None:
    check(
        "FTF-04A17",
        "a real framework_or_runtime round-trips unchanged",
        validate_framework_or_runtime_or_none("langchain") == "langchain",
    )
    check(
        "FTF-04A17",
        "null is accepted -- Section 6.8C's own explicit nullable tag",
        validate_framework_or_runtime_or_none(None) is None,
    )
    check(
        "FTF-04A17",
        "an empty-string framework_or_runtime is refused (must be null, not empty)",
        _rejects(validate_framework_or_runtime_or_none, ""),
    )


def test_validate_evidence_json_or_none() -> None:
    check(
        "FTF-04A17",
        "a real JSON object evidence payload round-trips unchanged",
        validate_evidence_json_or_none({"file": "app.py"}, field_name="model_evidence_json")
        == {"file": "app.py"},
    )
    check(
        "FTF-04A17",
        "null is accepted -- Section 7A.4's own 'unresolved fields' rule: "
        "an honestly-unresolved evidence category is null, never fabricated",
        validate_evidence_json_or_none(None, field_name="model_evidence_json") is None,
    )
    check(
        "FTF-04A17",
        "a bare string is refused -- evidence must be a real JSON object, "
        "not a scalar with no place to attach further provenance",
        _rejects(validate_evidence_json_or_none, "not-an-object", field_name="model_evidence_json"),
    )
    check(
        "FTF-04A17",
        "a bare list is refused for the same reason",
        _rejects(validate_evidence_json_or_none, [1, 2, 3], field_name="model_evidence_json"),
    )


def test_validate_confidence_score_bounds() -> None:
    check(
        "FTF-04A17",
        "a real mid-range confidence_score round-trips unchanged",
        validate_confidence_score(0.42) == 0.42,
    )
    check(
        "FTF-04A17",
        "the lower bound 0.0 is accepted",
        validate_confidence_score(0.0) == 0.0,
    )
    check(
        "FTF-04A17",
        "the upper bound 1.0 is accepted",
        validate_confidence_score(1.0) == 1.0,
    )
    check(
        "FTF-04A17",
        "a negative confidence_score is rejected",
        _rejects(validate_confidence_score, -0.01),
    )
    check(
        "FTF-04A17",
        "a confidence_score above 1.0 is rejected",
        _rejects(validate_confidence_score, 1.01),
    )
    check(
        "FTF-04A17",
        "a boolean confidence_score is rejected (bool is a subclass of int in Python)",
        _rejects(validate_confidence_score, True),
    )
    check(
        "FTF-04A17",
        "a non-numeric confidence_score is rejected",
        _rejects(validate_confidence_score, "0.5"),
    )


def test_validate_discovery_status_is_the_closed_one_value_set() -> None:
    check(
        "FTF-04A17",
        "ALLOWED_DISCOVERY_STATUSES is a closed one-value set for Release 1 "
        "-- FTF-04A18's own candidate-selection UI is a separate, "
        "not-yet-built later task",
        ALLOWED_DISCOVERY_STATUSES == ("candidate",),
    )
    check(
        "FTF-04A17",
        "validate_discovery_status accepts the one real Release-1 value",
        validate_discovery_status("candidate") == "candidate",
    )
    check(
        "FTF-04A17",
        "a value outside the closed one-value set (e.g. a later task's own "
        "selected/confirmed state) is refused, not silently accepted -- "
        "negative_acceptance: no automatic in-scope selection",
        _rejects(validate_discovery_status, "selected"),
    )


# -- 2. DiscoveredAgentCandidate.__init__ structural consistency --------


def test_construction_accepts_a_real_candidate_with_partial_evidence() -> None:
    candidate = _make_candidate(
        framework_or_runtime="fastapi",
        model_evidence_json={"litellm_alias": "gpt-4o-mini"},
        tool_evidence_json=None,
    )
    check(
        "FTF-04A17",
        "a real candidate with some evidence categories resolved and "
        "others honestly null constructs cleanly",
        candidate.framework_or_runtime == "fastapi"
        and candidate.model_evidence_json == {"litellm_alias": "gpt-4o-mini"}
        and candidate.tool_evidence_json is None
        and candidate.discovery_status == "candidate",
    )


def test_construction_rejects_out_of_range_confidence_score() -> None:
    check(
        "FTF-04A17",
        "constructing a candidate with confidence_score above 1.0 raises "
        "before any DB call -- advisory-only bounded shape, never accepted "
        "unchecked",
        _rejects(_make_candidate, confidence_score=1.5),
    )


def test_construction_rejects_unknown_discovery_status() -> None:
    check(
        "FTF-04A17",
        "constructing a candidate with a discovery_status outside the "
        "closed one-value Release-1 set raises before any DB call",
        _rejects(_make_candidate, discovery_status="selected"),
    )


def test_construction_rejects_non_object_evidence() -> None:
    check(
        "FTF-04A17",
        "constructing a candidate with a non-object evidence payload raises",
        _rejects(_make_candidate, prompt_evidence_json="a raw string"),
    )


def test_construction_rejects_empty_display_name() -> None:
    check(
        "FTF-04A17",
        "constructing a candidate with an empty display_name raises",
        _rejects(_make_candidate, display_name=""),
    )


# -- 3. Real demo router: role-boundary denial never opens a session -----


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
        build_discovered_agent_candidate_router(
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


_PROJECT_ID = uuid.uuid4()
_CONNECTION_ID = uuid.uuid4()


def _collection_path() -> str:
    return (
        f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}"
        f"/target-connections/{_CONNECTION_ID}/snapshots/{_SNAPSHOT_ID}/candidates"
    )


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path())
        check(
            "FTF-04A17",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-04A17",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_is_denied_read_and_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="evaluator")
        check(
            "FTF-04A17",
            "an evaluator is denied read (403) -- this resource is gated "
            "to organization_admin alone, the same real "
            "credential-bearing-content-access precedent FTF-04A01/FTF-04A07 "
            "already established",
            result.status == 403,
        )
        check(
            "FTF-04A17",
            "the evaluator's denied read never opens a session",
            session_factory.call_count == 0,
        )

        result = _request(
            base_url,
            "POST",
            _collection_path(),
            principal="evaluator",
            body=b"{}",
        )
        check(
            "FTF-04A17",
            "an evaluator is denied create (403)",
            result.status == 403,
        )
        check(
            "FTF-04A17",
            "the evaluator's denied write never opens a session",
            session_factory.call_count == 0,
        )


def test_organization_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "GET",
            _collection_path(),
            principal="organization_admin",
        )
        check(
            "FTF-04A17",
            "an organization_admin's read passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-04A17",
            "the organization_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_set_has_no_dedicated_section_45_sentence_so_follows_precedent() -> None:
    check(
        "FTF-04A17",
        "Section 45 names no dedicated role sentence for this resource "
        "class -- DISCOVERED_CANDIDATE_ROLES is exactly {organization_admin}, "
        "the same precedent FTF-04A01/FTF-04A07 already applied",
        DISCOVERED_CANDIDATE_ROLES == frozenset({Role.ORGANIZATION_ADMIN}),
    )


def test_crud_module_composes_certified_primitives_not_duplicates() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.target_connectors import (
        discovered_agent_candidate_crud,
        discovered_agent_candidate_routes,
    )
    from app.target_connectors.target_source_snapshot_crud import get_target_source_snapshot

    routes_source = inspect.getsource(discovered_agent_candidate_routes)
    check(
        "FTF-04A17",
        "discovered_agent_candidate_routes.py builds its dependency from "
        "the real, imported require_role -- not a second, hand-rolled "
        "role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(discovered_agent_candidate_crud)
    check(
        "FTF-04A17",
        "discovered_agent_candidate_crud.py proves the (connection_id, "
        "snapshot_id) parent chain through the real, imported "
        "get_target_source_snapshot (FTF-04A07) -- reuse-first, never a "
        "re-derived IDOR guard",
        "get_target_source_snapshot(" in crud_source
        and get_target_source_snapshot.__module__ in crud_source,
    )
    check(
        "FTF-04A17",
        "discovered_agent_candidate_crud.py authorizes every candidate-ID "
        "fetch through the real, imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-04A17",
        "discovered_agent_candidate_crud.py reads every row through the "
        "real, imported execute_tenant_scoped (FTF-0105) -- never a bare "
        "session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-04A17",
        "create_discovered_agent_candidate/get_discovered_agent_candidate/"
        "list_discovered_agent_candidates are the real, callable CRUD "
        "surface this router's own module imports",
        callable(create_discovered_agent_candidate)
        and callable(get_discovered_agent_candidate)
        and callable(list_discovered_agent_candidates),
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    candidate = _make_candidate(framework_or_runtime="fastapi")
    body = _serialize(candidate)
    check(
        "FTF-04A17",
        "the serialized candidate carries its real string-typed fields, a "
        "real float confidence_score and the single Release-1 "
        "discovery_status",
        body["candidate_key"] == "entrypoint:apps/api/agent.py:AgentClass"
        and body["framework_or_runtime"] == "fastapi"
        and body["discovery_status"] == "candidate"
        and isinstance(body["confidence_score"], float),
    )
    check(
        "FTF-04A17",
        "an unresolved (null) evidence category serializes as null, never "
        "a fabricated empty object",
        body["tool_evidence_json"] is None,
    )


# -- 6. live-app wiring (Law 7: "designed is not wired" is a real, ------
#      independently-checkable defect, not a documentation footnote) ----


def _full_app_env() -> dict[str, str]:
    """A complete, syntactically-valid env for `app.main.create_app()`.

    `SOVRA_DATABASE_URL` is a well-formed connection string that is never
    actually connected to here: `create_engine()` is lazy and
    `install_tenant_pool_reset_guard()` only registers a SQLAlchemy pool
    event listener, so building the real app object this way needs no
    database, matching the no-DB scope of the rest of this suite.
    """
    return {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": "https://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "https://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "https://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "https://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "https://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "https://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }


def test_router_is_wired_into_the_live_app() -> None:
    """`build_discovered_agent_candidate_router` must be reachable from
    `app.main.create_app()`, the one real FastAPI app this repository
    serves -- the same wiring precedent this task's own direct dependency
    FTF-04A07 established for `build_target_source_snapshot_router`.

    A router that exists, is fully tested against its own standalone
    `FastAPI()` instance, and is never mounted onto the real app is a
    genuine "designed is not wired" defect (Law 7) that a green CI run
    cannot catch, since neither test suite for this task ever imports
    `app.main`.
    """
    added_keys = set(_full_app_env()) - set(os.environ)
    old_values = {k: os.environ[k] for k in _full_app_env() if k in os.environ}
    os.environ.update(_full_app_env())
    try:
        import app.main as main_module

        app = main_module.create_app()
        # The resolved OpenAPI schema, not `app.routes` (whose element
        # shape is a FastAPI-internal implementation detail this suite
        # must not couple to) is the stable, public way to ask "what does
        # this live app actually serve".
        route_paths = set(app.openapi()["paths"])
    finally:
        for k in added_keys:
            os.environ.pop(k, None)
        os.environ.update(old_values)

    check(
        "FTF-04A17",
        "the real live app exposes the candidates collection route "
        "(build_discovered_agent_candidate_router is include_router()'d "
        "into app.main.create_app(), not merely defined)",
        "/api/v1/organizations/{organization_id}/projects/{project_id}"
        "/target-connections/{connection_id}/snapshots/{snapshot_id}/candidates" in route_paths,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A17 DiscoveredAgentCandidate schema tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A17 DiscoveredAgentCandidate schema tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
