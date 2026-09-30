#!/usr/bin/env python3
"""FTF-04A07 -- TargetSourceSnapshot schema + immutable provenance
(no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
create/get/list, cross-tenant IDOR, cross-connection reassignment-refusal
and grant-surface/CHECK-constraint evidence is
`tests/integration/test_ftf_04a07_target_source_snapshot.py`, run in CI's
dedicated integration job -- this repo's interactive dev sandbox has no
container-registry egress, same documented finding as every prior
integration-tested task). This suite proves, without any database:

1. `validate_commit_sha`/`validate_tree_hash`/`validate_capture_method`/
   `validate_analysis_status`/`validate_branch_or_none`/
   `validate_source_archive_object_key_or_none`/
   `validate_source_archive_sha256_or_none`: real, bounded, closed-set/
   shape validation, never silently accepted.
2. `TargetSourceSnapshot.__init__` itself refuses every structurally
   inconsistent combination this task's own negative_acceptance implies
   before any session or database is ever touched: a mismatched archive
   key/hash pair, a `local_dirty_snapshot` capture with no archive, and a
   snapshot claiming to supersede itself. The "cannot be ... reassigned to
   another repository/project" cross-*row* half (a real different
   connection's snapshot) requires a real database fetch and is proven
   instead in the live-Postgres suite (see `target_source_snapshot_crud.py`'s
   own `create_target_source_snapshot`).
3. The real, locally-hosted `build_target_source_snapshot_router` (a real
   uvicorn server over a real local socket, the same pattern
   `tests/tasks/test_ftf_1001.py`/`test_ftf_04a01.py` established) denies
   an unauthenticated request and a role outside `TARGET_SNAPSHOT_ROLES`
   *before* `session_factory` is ever called.
4. `target_source_snapshot_crud.py`/`target_source_snapshot_routes.py`
   compose the same certified primitives every prior task in this chain
   already established -- no new, hand-rolled authorization/tenant-scoping
   primitive is introduced.

Run directly: python3 tests/tasks/test_ftf_04a07.py
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

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.target_connectors.target_source_snapshot_crud import (  # noqa: E402
    create_target_source_snapshot,
    get_target_source_snapshot,
    list_target_source_snapshots,
)
from app.target_connectors.target_source_snapshot_routes import (  # noqa: E402
    TARGET_SNAPSHOT_ROLES,
    _serialize,
    build_target_source_snapshot_router,
)
from app.target_connectors.target_source_snapshot_schema import (  # noqa: E402
    ALLOWED_ANALYSIS_STATUSES,
    ALLOWED_CAPTURE_METHODS,
    TargetSourceSnapshot,
    TargetSourceSnapshotValidationError,
    validate_analysis_status,
    validate_branch_or_none,
    validate_capture_method,
    validate_commit_sha,
    validate_source_archive_object_key_or_none,
    validate_source_archive_sha256_or_none,
    validate_tree_hash,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except TargetSourceSnapshotValidationError:
        return True


_ORG_ID = uuid.uuid4()
_CONNECTION_ID = uuid.uuid4()
_REAL_SHA1 = "a" * 40
_REAL_SHA256_OBJECT = "b" * 64
_REAL_ARCHIVE_SHA256 = "c" * 64


def _make_snapshot(**overrides: object) -> TargetSourceSnapshot:
    kwargs: dict[str, object] = {
        "organization_id": _ORG_ID,
        "target_application_connection_id": _CONNECTION_ID,
        "commit_sha": _REAL_SHA1,
        "branch": "main",
        "tree_hash": _REAL_SHA1,
        "capture_method": "github_resolved_commit",
        "analysis_status": "not_analyzed",
        "captured_at": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return TargetSourceSnapshot(**kwargs)


# -- 1. field validators ------------------------------------------------


def test_validate_commit_sha_and_tree_hash_accept_real_git_object_ids() -> None:
    check(
        "FTF-04A07",
        "a real 40-hex-char (SHA-1) commit_sha round-trips unchanged",
        validate_commit_sha(_REAL_SHA1) == _REAL_SHA1,
    )
    check(
        "FTF-04A07",
        "a real 64-hex-char (SHA-256 object format) commit_sha round-trips unchanged",
        validate_commit_sha(_REAL_SHA256_OBJECT) == _REAL_SHA256_OBJECT,
    )
    check(
        "FTF-04A07",
        "a real 40-hex-char tree_hash round-trips unchanged",
        validate_tree_hash(_REAL_SHA1) == _REAL_SHA1,
    )


def test_validate_commit_sha_rejects_bad_shapes() -> None:
    check(
        "FTF-04A07",
        "a short/abbreviated commit_sha is rejected -- never a valid provenance reference",
        _rejects(validate_commit_sha, "a" * 7),
    )
    check(
        "FTF-04A07",
        "an uppercase-hex commit_sha is rejected (not the canonical lowercase form)",
        _rejects(validate_commit_sha, "A" * 40),
    )
    check(
        "FTF-04A07",
        "a non-hex-character commit_sha is rejected",
        _rejects(validate_commit_sha, "g" * 40),
    )
    check(
        "FTF-04A07",
        "a non-string commit_sha is rejected",
        _rejects(validate_commit_sha, None),
    )
    check(
        "FTF-04A07",
        "an empty commit_sha is rejected",
        _rejects(validate_commit_sha, ""),
    )


def test_validate_capture_method_is_the_closed_three_value_set() -> None:
    check(
        "FTF-04A07",
        "ALLOWED_CAPTURE_METHODS is exactly the two required connectors' own real capture shapes",
        set(ALLOWED_CAPTURE_METHODS)
        == {"github_resolved_commit", "local_git_commit", "local_dirty_snapshot"},
    )
    for method in ALLOWED_CAPTURE_METHODS:
        check(
            "FTF-04A07",
            f"validate_capture_method accepts the real method {method!r}",
            validate_capture_method(method) == method,
        )
    check(
        "FTF-04A07",
        "an unrecognized capture_method is refused, not silently accepted",
        _rejects(validate_capture_method, "gitlab_resolved_commit"),
    )


def test_validate_analysis_status_is_the_closed_one_value_set() -> None:
    check(
        "FTF-04A07",
        "ALLOWED_ANALYSIS_STATUSES is a closed one-value set for Release 1",
        ALLOWED_ANALYSIS_STATUSES == ("not_analyzed",),
    )
    check(
        "FTF-04A07",
        "validate_analysis_status accepts the one real Release-1 value",
        validate_analysis_status("not_analyzed") == "not_analyzed",
    )
    check(
        "FTF-04A07",
        "a value outside the closed one-value set (e.g. a later task's own "
        "state) is refused, not silently accepted",
        _rejects(validate_analysis_status, "analysis_complete"),
    )


def test_validate_branch_or_none_supports_detached_head() -> None:
    check(
        "FTF-04A07",
        "a real branch name round-trips unchanged",
        validate_branch_or_none("main") == "main",
    )
    check(
        "FTF-04A07",
        "null is accepted -- Section 7A.3's own real detached-HEAD support",
        validate_branch_or_none(None) is None,
    )
    check(
        "FTF-04A07",
        "an empty-string branch is refused (must be null, not empty, when absent)",
        _rejects(validate_branch_or_none, ""),
    )


def test_validate_source_archive_fields_or_none() -> None:
    check(
        "FTF-04A07",
        "a real object key round-trips unchanged",
        validate_source_archive_object_key_or_none("snapshots/org/conn/key.tar.zst")
        == "snapshots/org/conn/key.tar.zst",
    )
    check(
        "FTF-04A07",
        "null is accepted for source_archive_object_key",
        validate_source_archive_object_key_or_none(None) is None,
    )
    check(
        "FTF-04A07",
        "a real 64-hex-char sha256 round-trips unchanged",
        validate_source_archive_sha256_or_none(_REAL_ARCHIVE_SHA256) == _REAL_ARCHIVE_SHA256,
    )
    check(
        "FTF-04A07",
        "null is accepted for source_archive_sha256",
        validate_source_archive_sha256_or_none(None) is None,
    )
    check(
        "FTF-04A07",
        "a malformed (non-64-hex) source_archive_sha256 is refused",
        _rejects(validate_source_archive_sha256_or_none, "not-a-real-digest"),
    )


# -- 2. TargetSourceSnapshot.__init__ structural consistency -------------


def test_construction_accepts_a_real_github_resolved_snapshot() -> None:
    snapshot = _make_snapshot()
    check(
        "FTF-04A07",
        "a real, well-formed github_resolved_commit snapshot constructs cleanly",
        snapshot.capture_method == "github_resolved_commit"
        and snapshot.commit_sha == _REAL_SHA1
        and snapshot.analysis_status == "not_analyzed"
        and snapshot.source_archive_object_key is None
        and snapshot.source_archive_sha256 is None,
    )


def test_construction_accepts_a_real_detached_head_local_snapshot() -> None:
    snapshot = _make_snapshot(capture_method="local_git_commit", branch=None)
    check(
        "FTF-04A07",
        "a real detached-HEAD local_git_commit snapshot (no branch) constructs cleanly",
        snapshot.capture_method == "local_git_commit" and snapshot.branch is None,
    )


def test_construction_accepts_a_real_dirty_snapshot_with_archive() -> None:
    snapshot = _make_snapshot(
        capture_method="local_dirty_snapshot",
        source_archive_object_key="snapshots/org/conn/dirty.tar.zst",
        source_archive_sha256=_REAL_ARCHIVE_SHA256,
    )
    check(
        "FTF-04A07",
        "a real local_dirty_snapshot carrying a real archive constructs cleanly",
        snapshot.capture_method == "local_dirty_snapshot"
        and snapshot.source_archive_object_key == "snapshots/org/conn/dirty.tar.zst"
        and snapshot.source_archive_sha256 == _REAL_ARCHIVE_SHA256,
    )


def test_construction_rejects_dirty_snapshot_with_no_archive() -> None:
    check(
        "FTF-04A07",
        "negative_acceptance-adjacent: a local_dirty_snapshot with no "
        "archive at all is refused -- a dirty tree has no other durable "
        "reference to its own content",
        _rejects(_make_snapshot, capture_method="local_dirty_snapshot"),
    )


def test_construction_rejects_mismatched_archive_pair() -> None:
    check(
        "FTF-04A07",
        "an archive object key with no matching content hash is refused",
        _rejects(
            _make_snapshot,
            source_archive_object_key="snapshots/org/conn/key.tar.zst",
            source_archive_sha256=None,
        ),
    )
    check(
        "FTF-04A07",
        "an archive content hash with no object key is refused",
        _rejects(
            _make_snapshot,
            source_archive_object_key=None,
            source_archive_sha256=_REAL_ARCHIVE_SHA256,
        ),
    )


def test_construction_rejects_self_supersession() -> None:
    shared_id = uuid.uuid4()
    check(
        "FTF-04A07",
        "a snapshot claiming to supersede its own id is refused before any "
        "session or database is ever touched",
        _rejects(_make_snapshot, id=shared_id, supersedes_snapshot_id=shared_id),
    )


def test_construction_rejects_unknown_capture_method() -> None:
    check(
        "FTF-04A07",
        "constructing a snapshot with an unknown capture_method raises before any DB call",
        _rejects(_make_snapshot, capture_method="ftp_mirror"),
    )


def test_construction_rejects_unknown_analysis_status() -> None:
    check(
        "FTF-04A07",
        "constructing a snapshot with an analysis_status outside the "
        "closed one-value Release-1 set raises before any DB call",
        _rejects(_make_snapshot, analysis_status="analysis_complete"),
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
        build_target_source_snapshot_router(
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


def _collection_path() -> str:
    return (
        f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT_ID}"
        f"/target-connections/{_CONNECTION_ID}/snapshots"
    )


def test_unauthenticated_request_never_opens_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path())
        check(
            "FTF-04A07",
            "an unauthenticated request is denied via the real require_role "
            "dependency (401), never a hand-rolled check",
            result.status == 401,
        )
        check(
            "FTF-04A07",
            "an unauthenticated request never even takes a connection from the pool",
            session_factory.call_count == 0,
        )


def test_evaluator_is_denied_read_and_write() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(base_url, "GET", _collection_path(), principal="evaluator")
        check(
            "FTF-04A07",
            "an evaluator is denied read (403) -- this resource is gated "
            "to organization_admin alone, the same real read-only-access-"
            "into-repository-content precedent FTF-04A01's own "
            "TargetApplicationConnection already established",
            result.status == 403,
        )
        check(
            "FTF-04A07",
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
            "FTF-04A07",
            "an evaluator is denied create (403)",
            result.status == 403,
        )
        check(
            "FTF-04A07",
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
            "FTF-04A07",
            "an organization_admin's read passes the role gate and reaches "
            "the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-04A07",
            "the organization_admin's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_role_set_matches_section_45() -> None:
    check(
        "FTF-04A07",
        "Section 45: 'organization_admin: connections, security profiles, "
        "cost profiles' is the only sentence naming a role for this class "
        "of resource -- TARGET_SNAPSHOT_ROLES is exactly {organization_admin}, "
        "the same precedent FTF-04A01's own TARGET_CONNECTION_ROLES applied",
        TARGET_SNAPSHOT_ROLES == frozenset({Role.ORGANIZATION_ADMIN}),
    )


def test_crud_module_composes_certified_primitives_not_duplicates() -> None:
    import inspect

    from app.auth.rbac_middleware import require_role
    from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
    from app.target_connectors import target_source_snapshot_crud, target_source_snapshot_routes

    routes_source = inspect.getsource(target_source_snapshot_routes)
    check(
        "FTF-04A07",
        "target_source_snapshot_routes.py builds its dependency from the "
        "real, imported require_role -- not a second, hand-rolled role check",
        "require_role(" in routes_source and require_role.__module__ in routes_source,
    )
    crud_source = inspect.getsource(target_source_snapshot_crud)
    check(
        "FTF-04A07",
        "target_source_snapshot_crud.py authorizes every fetch through the "
        "real, imported authorize_resource_ownership (FTF-H003)",
        "authorize_resource_ownership(" in crud_source
        and "app.auth.resource_authorization" in crud_source,
    )
    check(
        "FTF-04A07",
        "target_source_snapshot_crud.py reads every row through the real, "
        "imported execute_tenant_scoped (FTF-0105) -- never a bare "
        "session.get(...) call",
        "execute_tenant_scoped(" in crud_source and execute_tenant_scoped.__module__ in crud_source,
    )
    check(
        "FTF-04A07",
        "create_target_source_snapshot/get_target_source_snapshot/"
        "list_target_source_snapshots are the real, callable CRUD surface "
        "this router's own module imports",
        callable(create_target_source_snapshot)
        and callable(get_target_source_snapshot)
        and callable(list_target_source_snapshots),
    )


def test_serialize_never_exposes_a_wrong_shape() -> None:
    snapshot = _make_snapshot()
    body = _serialize(snapshot)
    check(
        "FTF-04A07",
        "the serialized snapshot carries its real string-typed provenance "
        "fields and a null supersedes_snapshot_id when none was set",
        body["commit_sha"] == _REAL_SHA1
        and body["capture_method"] == "github_resolved_commit"
        and body["analysis_status"] == "not_analyzed"
        and body["supersedes_snapshot_id"] is None,
    )

    superseding = _make_snapshot(supersedes_snapshot_id=uuid.uuid4())
    body = _serialize(superseding)
    check(
        "FTF-04A07",
        "a real supersedes_snapshot_id is serialized as a string, not a raw UUID object",
        isinstance(body["supersedes_snapshot_id"], str),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A07 TargetSourceSnapshot schema tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A07 TargetSourceSnapshot schema tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
