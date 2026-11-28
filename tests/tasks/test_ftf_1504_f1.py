"""FTF-1504-F1 -- project scoping of the 7 target-connection child handlers (no DB).

The real routers run under FastAPI's TestClient with a `MagicMock` session factory (the same
no-DB pattern as tests/tasks/test_ftf_04a18.py). The store functions each handler calls are
replaced by recording fakes, and `get_target_application_connection` -- the check this slice
adds -- by a fake that knows which project owns the connection. The real-Postgres proof (two
projects in one organization, restricted app role) is
tests/integration/test_ftf_1504_f1_project_scoping.py.

For each of the 7 handlers:
* the own project reaches the store and answers the handler's success status and body;
* another project, or a random project id, answers 404 `not_found` before the store is called;
* the project check is called with the path's project and connection ids;
* the handler's existing 400/409 mappings are unchanged.

Run directly: python3 tests/tasks/test_ftf_1504_f1.py
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.target_connectors.discovered_agent_candidate_routes as cand_routes  # noqa: E402
import app.target_connectors.discovered_agent_candidate_selection_routes as sel_routes  # noqa: E402
import app.target_connectors.target_source_snapshot_routes as snap_routes  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.target_connectors.target_application_connection_crud import (  # noqa: E402
    TargetApplicationConnectionNotFoundError as ConnectionNotInProjectError,
)

FAILURES: list[str] = []
CHECKS = [0]
CID = "FTF-1504-F1"


def check(description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


ORG = uuid.uuid4()
PROJECT_A = uuid.uuid4()  # owns CONNECTION
PROJECT_B = uuid.uuid4()  # same organization, does not own it
CONNECTION = uuid.uuid4()
SNAPSHOT = uuid.uuid4()
CANDIDATE = uuid.uuid4()
PRINCIPAL = AuthenticatedPrincipal(issuer="https://issuer.example", subject="admin")


class _Recorder:
    def __init__(self) -> None:
        self.checks: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.store_calls: list[str] = []
        self.raise_in_store: BaseException | None = None

    def project_check(self, session: Any, context: Any, project_id: Any, connection_id: Any) -> Any:
        del session, context
        self.checks.append((project_id, connection_id))
        if (project_id, connection_id) != (PROJECT_A, CONNECTION):
            raise ConnectionNotInProjectError("not in project")
        return SimpleNamespace(id=connection_id, project_id=project_id)

    def store(self, name: str, result: Any) -> Any:
        def call(*_args: Any, **_kwargs: Any) -> Any:
            self.store_calls.append(name)
            if self.raise_in_store is not None:
                raise self.raise_in_store
            return result

        return call


REC = _Recorder()


def _install() -> TestClient:
    selection = SimpleNamespace(
        candidate_id=CANDIDATE,
        target_source_snapshot_id=SNAPSHOT,
        actor_id="admin",
        selected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    patches: list[tuple[Any, str, Any]] = [
        (snap_routes, "create_target_source_snapshot", REC.store("create_snapshot", "snap")),
        (snap_routes, "list_target_source_snapshots", REC.store("list_snapshots", ["snap"])),
        (snap_routes, "get_target_source_snapshot", REC.store("get_snapshot", "snap")),
        (snap_routes, "_serialize", lambda s: {"snapshot": str(s)}),
        (cand_routes, "create_discovered_agent_candidate", REC.store("create_candidate", "cand")),
        (cand_routes, "list_discovered_agent_candidates", REC.store("list_candidates", "page")),
        (cand_routes, "get_discovered_agent_candidate", REC.store("get_candidate", "cand")),
        (cand_routes, "_serialize", lambda c: {"candidate": str(c)}),
        (cand_routes, "_serialize_page", lambda p: {"page": str(p)}),
        (sel_routes, "select_discovered_agent_candidate", REC.store("select", selection)),
    ]
    patches += [
        (module, "get_target_application_connection", REC.project_check)
        for module in (snap_routes, cand_routes, sel_routes)
    ]
    for module, name, value in patches:
        assert hasattr(module, name), name  # patch only what the route module really calls
        setattr(module, name, value)

    def resolver(_request: Any) -> AuthenticatedPrincipal:
        return PRINCIPAL

    def role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        del issuer, subject
        return Role.ORGANIZATION_ADMIN if organization_id == ORG else None

    app = FastAPI()
    session_factory = MagicMock()
    for build in (
        snap_routes.build_target_source_snapshot_router,
        cand_routes.build_discovered_agent_candidate_router,
        sel_routes.build_discovered_agent_candidate_selection_router,
    ):
        app.include_router(
            build(session_factory, principal_resolver=resolver, role_lookup=role_lookup)
        )
    return TestClient(app)


def _base(project: uuid.UUID) -> str:
    return f"/api/v1/organizations/{ORG}/projects/{project}/target-connections/{CONNECTION}"


SNAPSHOT_BODY = {
    "commit_sha": "c" * 40,
    "tree_hash": "d" * 40,
    "capture_method": "local_git_commit",
}
CANDIDATE_BODY = {
    "candidate_key": "entrypoint:a.py:Agent",
    "display_name": "A",
    "confidence_score": 0.5,
}
HANDLERS = [
    # label, method, suffix, payload, store name, success status, success body
    (
        "POST snapshots",
        "POST",
        "/snapshots",
        SNAPSHOT_BODY,
        "create_snapshot",
        201,
        {"snapshot": "snap"},
    ),
    ("GET snapshots", "GET", "/snapshots", None, "list_snapshots", 200, [{"snapshot": "snap"}]),
    (
        "GET snapshot",
        "GET",
        f"/snapshots/{SNAPSHOT}",
        None,
        "get_snapshot",
        200,
        {"snapshot": "snap"},
    ),
    (
        "POST candidates",
        "POST",
        f"/snapshots/{SNAPSHOT}/candidates",
        CANDIDATE_BODY,
        "create_candidate",
        201,
        {"candidate": "cand"},
    ),
    (
        "GET candidates",
        "GET",
        f"/snapshots/{SNAPSHOT}/candidates",
        None,
        "list_candidates",
        200,
        {"page": "page"},
    ),
    (
        "GET candidate",
        "GET",
        f"/snapshots/{SNAPSHOT}/candidates/{CANDIDATE}",
        None,
        "get_candidate",
        200,
        {"candidate": "cand"},
    ),
    (
        "POST select",
        "POST",
        f"/snapshots/{SNAPSHOT}/candidates/{CANDIDATE}/select",
        None,
        "select",
        201,
        {
            "candidate_id": str(CANDIDATE),
            "target_source_snapshot_id": str(SNAPSHOT),
            "selected_by": "admin",
            "selected_at": "2026-01-01T00:00:00+00:00",
        },
    ),
]


def _call(client: TestClient, method: str, path: str, payload: Any) -> Any:
    return client.request(method, path, json=payload)


def test_every_handler_checks_the_path_project() -> None:
    client = _install()
    for label, method, suffix, payload, store, status, body in HANDLERS:
        for other, project in (("another project", PROJECT_B), ("a random project", uuid.uuid4())):
            REC.checks.clear()
            REC.store_calls.clear()
            response = _call(client, method, _base(project) + suffix, payload)
            check(
                f"{label} under {other} -> 404 not_found (got {response.status_code} "
                f"{response.text[:80]})",
                response.status_code == 404 and response.json() == {"detail": "not_found"},
            )
            check(f"{label} under {other}: the store is never called", REC.store_calls == [])
            check(
                f"{label} under {other}: the check got the path's project and connection",
                REC.checks == [(project, CONNECTION)],
            )
        REC.checks.clear()
        REC.store_calls.clear()
        response = _call(client, method, _base(PROJECT_A) + suffix, payload)
        check(
            f"{label} under the own project -> {status} with the store's body "
            f"(got {response.status_code} {response.text[:80]})",
            response.status_code == status and response.json() == body,
        )
        check(
            f"{label} under the own project: checked, then the store was called once",
            REC.checks == [(PROJECT_A, CONNECTION)] and REC.store_calls == [store],
        )


def test_existing_error_mappings_are_unchanged() -> None:
    client = _install()
    cases = [
        (
            "POST snapshots",
            "/snapshots",
            SNAPSHOT_BODY,
            snap_routes.TargetSourceSnapshotValidationError("x"),
            400,
        ),
        (
            "POST snapshots",
            "/snapshots",
            SNAPSHOT_BODY,
            snap_routes.TargetSourceSnapshotNotFoundError("x"),
            404,
        ),
        (
            "POST candidates",
            f"/snapshots/{SNAPSHOT}/candidates",
            CANDIDATE_BODY,
            cand_routes.DiscoveredAgentCandidateDuplicateKeyError("x"),
            409,
        ),
        (
            "POST candidates",
            f"/snapshots/{SNAPSHOT}/candidates",
            CANDIDATE_BODY,
            cand_routes.DiscoveredAgentCandidateValidationError("x"),
            400,
        ),
        (
            "POST select",
            f"/snapshots/{SNAPSHOT}/candidates/{CANDIDATE}/select",
            None,
            sel_routes.StaleSnapshotCandidateError("x"),
            409,
        ),
        (
            "POST select",
            f"/snapshots/{SNAPSHOT}/candidates/{CANDIDATE}/select",
            None,
            sel_routes.DiscoveredAgentCandidateNotFoundError("x"),
            404,
        ),
    ]
    gets = [
        ("GET snapshots", "/snapshots", snap_routes.TargetApplicationConnectionNotFoundError("x")),
        (
            "GET snapshot",
            f"/snapshots/{SNAPSHOT}",
            snap_routes.TargetSourceSnapshotNotFoundError("x"),
        ),
        (
            "GET candidates",
            f"/snapshots/{SNAPSHOT}/candidates",
            cand_routes.TargetSourceSnapshotNotFoundError("x"),
        ),
        (
            "GET candidate",
            f"/snapshots/{SNAPSHOT}/candidates/{CANDIDATE}",
            cand_routes.DiscoveredAgentCandidateNotFoundError("x"),
        ),
    ]
    for label, suffix, payload, error, status in cases:
        REC.raise_in_store = error
        response = _call(client, "POST", _base(PROJECT_A) + suffix, payload)
        check(
            f"{label}: a store {type(error).__name__} still maps to {status} "
            f"(got {response.status_code})",
            response.status_code == status,
        )
    for label, suffix, error in gets:
        REC.raise_in_store = error
        response = _call(client, "GET", _base(PROJECT_A) + suffix, None)
        check(
            f"{label}: a store {type(error).__name__} still maps to 404 not_found "
            f"(got {response.status_code})",
            response.status_code == 404 and response.json() == {"detail": "not_found"},
        )
    REC.raise_in_store = None


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"{CID} project scoping (no DB): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"{CID} project scoping (no DB): all {CHECKS[0]} checks passed")


if __name__ == "__main__":
    main()
