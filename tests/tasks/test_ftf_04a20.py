#!/usr/bin/env python3
"""FTF-04A20 -- ManifestEvidence schema/API (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list, cross-tenant IDOR, cross-snapshot-evidence-rejected and
grant-surface/CHECK-constraint evidence is
`tests/integration/test_ftf_04a20_manifest_evidence.py`, run in CI's
dedicated integration job -- this repo's interactive dev sandbox has no
container-registry egress, same documented finding as every prior
integration-tested task). This suite proves, without any database:

1. `validate_evidence_type`/`validate_field_path`: real, bounded/closed-set
   validation, never silently accepted.
2. `ManifestEvidence.__init__` itself refuses every structurally
   inconsistent per-`evidence_type` field combination this task's own
   negative_acceptance implies before any session or database is ever
   touched: fabricated line refs (wrong range, wrong pairing, attached to
   a non-line-anchored type), a symbol on a non-repo_symbol row, a
   detected type missing its own required commit/confidence, and a
   user_input row carrying any repo-shaped provenance at all.
3. The real, locally-hosted `build_manifest_evidence_router` (a real
   uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_04a19.py` established) denies an unauthenticated
   request and a role outside `AGENT_MANIFEST_WRITE_ROLES` *before*
   `session_factory` is ever called.
4. `manifest_evidence_crud.py`/`manifest_evidence_routes.py` compose the
   same certified primitives every prior task in this chain already
   established -- no new, hand-rolled authorization/tenant-scoping
   primitive is introduced.

Run directly: python3 tests/tasks/test_ftf_04a20.py
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
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.target_connectors.agent_manifest_routes import (  # noqa: E402
    AGENT_MANIFEST_READ_ROLES,
    AGENT_MANIFEST_WRITE_ROLES,
)
from app.target_connectors.manifest_evidence_crud import (  # noqa: E402
    create_manifest_evidence,
    get_manifest_evidence,
    list_manifest_evidence,
)
from app.target_connectors.manifest_evidence_routes import (  # noqa: E402
    _serialize,
    build_manifest_evidence_router,
)
from app.target_connectors.manifest_evidence_schema import (  # noqa: E402
    ALLOWED_EVIDENCE_TYPES,
    DETECTED_EVIDENCE_TYPES,
    LINE_REF_EVIDENCE_TYPES,
    REPO_CITED_EVIDENCE_TYPES,
    ManifestEvidence,
    ManifestEvidenceValidationError,
    validate_evidence_type,
    validate_field_path,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except ManifestEvidenceValidationError:
        return True


_ORG_ID = uuid.uuid4()
_MANIFEST_ID = uuid.uuid4()


def _make_evidence(**overrides: object) -> ManifestEvidence:
    from datetime import UTC, datetime

    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "agent_manifest_id": _MANIFEST_ID,
        "field_path": "agent_entrypoint_json",
        "evidence_type": "repo_file",
        "repository_commit_sha": "a" * 40,
        "file_path": "src/agents/support.ts",
        "line_start": 10,
        "line_end": 20,
        "symbol": None,
        "content_hash": "b" * 64,
        "confidence_score": "0.900",
        "evidence_json": {"matched": "class SupportAgent"},
        # No DB session is involved in this suite, so the column's own
        # server_default("now()") never fires -- set explicitly here so
        # `_serialize` (which calls `.isoformat()`) has a real value.
        "created_at": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return ManifestEvidence(**kwargs)


# -- 1. field validators --------------------------------------------------


def test_validate_evidence_type_is_the_closed_six_value_set() -> None:
    check(
        "FTF-04A20",
        "ALLOWED_EVIDENCE_TYPES is exactly Section 6.8E's own six-value set",
        ALLOWED_EVIDENCE_TYPES
        == ("repo_file", "repo_symbol", "config", "test", "runtime_probe", "user_input"),
    )
    for evidence_type in ALLOWED_EVIDENCE_TYPES:
        check(
            "FTF-04A20",
            f"validate_evidence_type accepts the real evidence_type {evidence_type!r}",
            validate_evidence_type(evidence_type) == evidence_type,
        )
    check(
        "FTF-04A20",
        "an unrecognized evidence_type is refused, not silently accepted",
        _rejects(validate_evidence_type, "fabricated_type"),
    )
    check(
        "FTF-04A20",
        "a non-string evidence_type is refused",
        _rejects(validate_evidence_type, None),
    )


def test_validate_field_path_requires_a_real_manifest_content_field() -> None:
    check(
        "FTF-04A20",
        "a real top-level CONTENT_FIELDS member round-trips unchanged",
        validate_field_path("agent_entrypoint_json") == "agent_entrypoint_json",
    )
    check(
        "FTF-04A20",
        "a dotted sub-path under a real top-level field is accepted -- the "
        "manifest's own internal JSON structure is not frozen by this task",
        validate_field_path("current_model_binding_json.litellm_alias")
        == "current_model_binding_json.litellm_alias",
    )
    check(
        "FTF-04A20",
        "negative_acceptance: an unknown field_path (not a real "
        "AgentManifest content field) is rejected",
        _rejects(validate_field_path, "not_a_real_field"),
    )
    check(
        "FTF-04A20",
        "negative_acceptance: a dotted path whose own leading segment is "
        "unknown is rejected too",
        _rejects(validate_field_path, "not_a_real_field.nested"),
    )
    check(
        "FTF-04A20",
        "an empty field_path is rejected",
        _rejects(validate_field_path, ""),
    )
    check(
        "FTF-04A20",
        "an over-length field_path is rejected",
        _rejects(validate_field_path, "agent_entrypoint_json" + "x" * 500),
    )


# -- 2. ManifestEvidence.__init__ per-evidence_type structural shape ------


def test_construction_accepts_a_real_repo_file_row() -> None:
    evidence = _make_evidence()
    check(
        "FTF-04A20",
        "a real, well-formed repo_file row constructs cleanly",
        evidence.evidence_type == "repo_file" and evidence.line_start == 10,
    )


def test_construction_accepts_a_real_repo_symbol_row() -> None:
    evidence = _make_evidence(
        evidence_type="repo_symbol",
        field_path="agent_entrypoint_json",
        symbol="SupportAgent.handle",
    )
    check(
        "FTF-04A20",
        "a real, well-formed repo_symbol row (with a real symbol) constructs cleanly",
        evidence.evidence_type == "repo_symbol" and evidence.symbol == "SupportAgent.handle",
    )


def test_construction_accepts_a_real_config_row_with_no_line_refs() -> None:
    evidence = _make_evidence(
        evidence_type="config",
        field_path="runtime_dependencies_json",
        line_start=None,
        line_end=None,
        symbol=None,
    )
    check(
        "FTF-04A20",
        "a real config row (file-level, no line refs) constructs cleanly",
        evidence.evidence_type == "config" and evidence.line_start is None,
    )


def test_construction_accepts_a_real_runtime_probe_row() -> None:
    evidence = _make_evidence(
        evidence_type="runtime_probe",
        field_path="current_model_binding_json",
        file_path=None,
        line_start=None,
        line_end=None,
        symbol=None,
        content_hash=None,
    )
    check(
        "FTF-04A20",
        "a real runtime_probe row (no file citation, still a real commit "
        "anchor and confidence) constructs cleanly",
        evidence.evidence_type == "runtime_probe"
        and evidence.repository_commit_sha is not None
        and evidence.file_path is None,
    )


def test_construction_accepts_a_real_user_input_row() -> None:
    evidence = _make_evidence(
        evidence_type="user_input",
        field_path="mandate_json",
        repository_commit_sha=None,
        file_path=None,
        line_start=None,
        line_end=None,
        symbol=None,
        content_hash=None,
        confidence_score=None,
    )
    check(
        "FTF-04A20",
        "acceptance: 'user override provenance' -- a real user_input row "
        "with NO repo provenance and NO confidence constructs cleanly",
        evidence.evidence_type == "user_input"
        and evidence.repository_commit_sha is None
        and evidence.confidence_score is None,
    )


def test_construction_rejects_fabricated_line_refs_wrong_range() -> None:
    check(
        "FTF-04A20",
        "negative_acceptance: line_end < line_start is a fabricated range, rejected",
        _rejects(_make_evidence, line_start=20, line_end=10),
    )
    check(
        "FTF-04A20",
        "negative_acceptance: line_start < 1 is a fabricated range, rejected",
        _rejects(_make_evidence, line_start=0, line_end=5),
    )


def test_construction_rejects_unpaired_line_refs() -> None:
    check(
        "FTF-04A20",
        "line_start without line_end is refused",
        _rejects(_make_evidence, line_end=None),
    )
    check(
        "FTF-04A20",
        "line_end without line_start is refused",
        _rejects(_make_evidence, line_start=None),
    )


def test_construction_rejects_line_refs_on_a_non_line_anchored_type() -> None:
    check(
        "FTF-04A20",
        "negative_acceptance: a config row (not in LINE_REF_EVIDENCE_TYPES) "
        "carrying real line_start/line_end is fabricated, rejected",
        _rejects(
            _make_evidence,
            evidence_type="config",
            field_path="runtime_dependencies_json",
            symbol=None,
        ),
    )


def test_construction_requires_line_refs_for_line_anchored_types() -> None:
    check(
        "FTF-04A20",
        "a repo_file row with NO line refs at all is refused -- "
        "repo_file/repo_symbol are always line-anchored",
        _rejects(_make_evidence, line_start=None, line_end=None),
    )


def test_construction_rejects_symbol_outside_repo_symbol() -> None:
    check(
        "FTF-04A20",
        "a symbol on a repo_file row (not repo_symbol) is refused",
        _rejects(_make_evidence, symbol="SupportAgent.handle"),
    )
    check(
        "FTF-04A20",
        "a repo_symbol row with NO symbol at all is refused",
        _rejects(
            _make_evidence,
            evidence_type="repo_symbol",
            field_path="agent_entrypoint_json",
            symbol=None,
        ),
    )


def test_construction_rejects_detected_row_missing_commit_or_confidence() -> None:
    check(
        "FTF-04A20",
        "negative_acceptance: a detected row (repo_file) with no "
        "repository_commit_sha at all is refused",
        _rejects(_make_evidence, repository_commit_sha=None),
    )
    check(
        "FTF-04A20",
        "negative_acceptance: 'unknown values presented as detected are "
        "rejected' -- a detected row with no confidence_score at all is refused",
        _rejects(_make_evidence, confidence_score=None),
    )


def test_construction_rejects_repo_cited_row_missing_file_path_or_hash() -> None:
    check(
        "FTF-04A20",
        "a repo_file row with no file_path at all is refused",
        _rejects(_make_evidence, file_path=None),
    )
    check(
        "FTF-04A20",
        "a repo_file row with no content_hash at all is refused",
        _rejects(_make_evidence, content_hash=None),
    )


def test_construction_rejects_user_input_row_with_any_repo_provenance() -> None:
    check(
        "FTF-04A20",
        "negative_acceptance: a user_input row carrying a real "
        "repository_commit_sha (detection provenance) is refused -- the "
        "human is the source of truth, never blended with a repo citation",
        _rejects(
            _make_evidence,
            evidence_type="user_input",
            field_path="mandate_json",
            file_path=None,
            line_start=None,
            line_end=None,
            symbol=None,
            content_hash=None,
            confidence_score=None,
        ),
    )
    check(
        "FTF-04A20",
        "a user_input row carrying a real confidence_score is refused",
        _rejects(
            _make_evidence,
            evidence_type="user_input",
            field_path="mandate_json",
            repository_commit_sha=None,
            file_path=None,
            line_start=None,
            line_end=None,
            symbol=None,
            content_hash=None,
        ),
    )


def test_construction_rejects_empty_evidence_json() -> None:
    check(
        "FTF-04A20",
        "negative_acceptance: an empty evidence_json ({}) is vacuous, "
        "rejected -- never a fabricated-looking placeholder",
        _rejects(_make_evidence, evidence_json={}),
    )
    check(
        "FTF-04A20",
        "a non-object evidence_json is refused",
        _rejects(_make_evidence, evidence_json="not-an-object"),
    )


def test_evidence_type_partitions_are_disjoint_and_consistent() -> None:
    check(
        "FTF-04A20",
        "LINE_REF_EVIDENCE_TYPES is a strict subset of REPO_CITED_EVIDENCE_TYPES",
        LINE_REF_EVIDENCE_TYPES <= REPO_CITED_EVIDENCE_TYPES,
    )
    check(
        "FTF-04A20",
        "REPO_CITED_EVIDENCE_TYPES is a strict subset of DETECTED_EVIDENCE_TYPES",
        REPO_CITED_EVIDENCE_TYPES <= DETECTED_EVIDENCE_TYPES,
    )
    check(
        "FTF-04A20",
        "user_input is never a member of DETECTED_EVIDENCE_TYPES",
        "user_input" not in DETECTED_EVIDENCE_TYPES,
    )


# -- 3. Real demo router: role-boundary denial never opens a session -----


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_WORKSPACE_ADMIN_PRINCIPAL = AuthenticatedPrincipal(
    issuer="https://issuer.example", subject="wsadmin-1"
)
_EVALUATOR_PRINCIPAL = AuthenticatedPrincipal(
    issuer="https://issuer.example", subject="evaluator-1"
)


def _principal_resolver(request):
    header = request.headers.get("x-test-principal")
    if header == "workspace_admin":
        return _WORKSPACE_ADMIN_PRINCIPAL
    if header == "evaluator":
        return _EVALUATOR_PRINCIPAL
    return None


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID):
    if organization_id != _ORG_ID:
        return None
    if subject == _WORKSPACE_ADMIN_PRINCIPAL.subject:
        return Role.WORKSPACE_ADMIN
    if subject == _EVALUATOR_PRINCIPAL.subject:
        return Role.EVALUATOR
    return None


@contextlib.contextmanager
def _running_router(session_factory) -> Iterator[str]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_manifest_evidence_router(
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
    project_id = uuid.uuid4()
    return (
        f"/api/v1/organizations/{_ORG_ID}/projects/{project_id}/manifests/{_MANIFEST_ID}/evidence"
    )


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path())
        check(
            "FTF-04A20",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-04A20",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_is_denied_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "POST", _collection_path(), principal="evaluator", body=b"{}")
        check(
            "FTF-04A20",
            "an evaluator is denied create (403) -- write is gated to "
            "{workspace_admin, organization_admin} alone (reused from FTF-04A19)",
            result.status == 403,
        )
        check(
            "FTF-04A20",
            "the evaluator's denied write never opens a session",
            session_factory.call_count == 0,
        )


def test_evaluator_is_allowed_read() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="evaluator")
        check(
            "FTF-04A20",
            "an evaluator is allowed read (never 401/403) -- Section 45's widest read baseline",
            result.status not in (401, 403),
        )


def test_workspace_admin_reaches_the_session_layer() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="workspace_admin")
        check(
            "FTF-04A20",
            "a workspace_admin's read passes the role gate and reaches the "
            "session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-04A20",
            "the workspace_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_sets_are_reused_not_redeclared() -> None:
    import inspect

    from app.target_connectors import manifest_evidence_routes

    source = inspect.getsource(manifest_evidence_routes)
    check(
        "FTF-04A20",
        "manifest_evidence_routes.py imports AGENT_MANIFEST_READ_ROLES/"
        "AGENT_MANIFEST_WRITE_ROLES from agent_manifest_routes.py directly "
        "-- never a second, hand-duplicated role set",
        "from app.target_connectors.agent_manifest_routes import" in source
        and "AGENT_MANIFEST_READ_ROLES" in source
        and "AGENT_MANIFEST_WRITE_ROLES" in source,
    )
    check(
        "FTF-04A20",
        "the real, imported role sets are exactly FTF-04A19's own",
        AGENT_MANIFEST_WRITE_ROLES == frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN})
        and AGENT_MANIFEST_READ_ROLES == frozenset(Role),
    )


def test_crud_module_composes_certified_primitives_not_duplicates() -> None:
    import inspect

    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.target_connectors import manifest_evidence_crud

    crud_source = inspect.getsource(manifest_evidence_crud)
    check(
        "FTF-04A20",
        "manifest_evidence_crud.py authorizes/fetches the parent manifest "
        "through the real, imported get_agent_manifest (FTF-04A19) -- "
        "never a second, hand-duplicated ownership check",
        "get_agent_manifest(" in crud_source
        and "from app.target_connectors.agent_manifest_crud import" in crud_source,
    )
    check(
        "FTF-04A20",
        "manifest_evidence_crud.py reads every row through the real, "
        "imported execute_tenant_scoped (FTF-0105) -- never a bare "
        "session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-04A20",
        "the real, callable CRUD surface this router's own module imports",
        callable(create_manifest_evidence)
        and callable(get_manifest_evidence)
        and callable(list_manifest_evidence),
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    evidence = _make_evidence()
    body = _serialize(evidence)
    check(
        "FTF-04A20",
        "the serialized evidence row carries its real field_path/"
        "evidence_type/line refs and a real float confidence_score",
        body["field_path"] == "agent_entrypoint_json"
        and body["evidence_type"] == "repo_file"
        and body["line_start"] == 10
        and body["line_end"] == 20
        and isinstance(body["confidence_score"], float),
    )
    check(
        "FTF-04A20",
        "id/organization_id/agent_manifest_id serialize as strings, not raw UUID objects",
        isinstance(body["id"], str)
        and isinstance(body["organization_id"], str)
        and isinstance(body["agent_manifest_id"], str),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A20 ManifestEvidence schema tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A20 ManifestEvidence schema tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
