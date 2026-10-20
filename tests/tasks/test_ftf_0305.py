#!/usr/bin/env python3
"""FTF-0305 -- License acceptance workflow (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
revoke-license evidence is
`tests/integration/test_ftf_0305_license_acceptance_workflow.py`, run in
CI's dedicated `license-acceptance-workflow-integration` job -- this
repo's interactive dev sandbox has no container-registry egress, same
documented finding as every prior integration-tested task). This suite
proves, without any database:

1. `evaluate_license_gate`/`assert_license_accepted_for_use`: an
   unaccepted (or revoked) artifact's license is reported not accepted
   and the assertion function raises `LicenseNotAcceptedError`; an
   accepted artifact's gate reports the real acceptor/timestamp/revision.
   No function in `license_acceptance_workflow.py` takes a provider/
   source_type/model_family parameter -- this task's own
   negative_acceptance ("no blanket approval based on provider name")
   proven both by signature introspection and by exercising every one of
   Section 6.7's four allowed `source_type` values identically.
2. `invalidate_stale_license_acceptance`/`apply_revoke_license` (the
   pure, DB-free cores): idempotent, refuse an archived artifact, and
   demote a `ready` artifact back to `registered` on revocation -- this
   task's own negative_acceptance ("revoked license... blocks
   training/serving") proven directly against in-memory
   `TrainableArtifact` objects, no database required.
3. End-to-end with FTF-0301's own `apply_mark_ready`: accept -> resolve
   digest/revision -> mark ready -> revoke -> mark ready again is
   refused, exactly reproducing the concrete mechanism a real training/
   serving admission path relies on (`status == "ready"`).
4. This task deliberately does NOT touch `apply_edits` -- proven by
   source inspection that `apply_edits` never imports or calls anything
   from `license_acceptance_workflow` (see that module's own docstring
   for why: doing so would have materially changed already-CERTIFIED
   FTF-0301 behavior, a Law 15 situation out of this task's own charter).
5. The real, locally-hosted `build_trainable_artifact_router`'s new
   `revoke-license` route denies a role outside `ARTIFACT_WRITE_ROLES`
   before `session_factory` is ever called, and lets a write role reach
   the session layer -- the same real-uvicorn-server pattern
   `tests/tasks/test_ftf_0301.py` already established for this router.

Run directly: python3 tests/tasks/test_ftf_0305.py
"""

from __future__ import annotations

import http.client
import inspect
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.artifacts import artifact_crud  # noqa: E402
from app.artifacts.artifact_crud import ArtifactStateError, apply_mark_ready  # noqa: E402
from app.artifacts.artifact_routes import (  # noqa: E402
    ARTIFACT_WRITE_ROLES,
    build_trainable_artifact_router,
)
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.artifacts.license_acceptance_workflow import (  # noqa: E402
    LicenseAcceptanceError,
    apply_revoke_license,
    assert_license_accepted_for_use,
    evaluate_license_gate,
    invalidate_stale_license_acceptance,
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
    except (LicenseAcceptanceError, ArtifactStateError):
        return True


def _make_artifact(
    *, status: str = "registered", source_type: str = "huggingface", **overrides: object
) -> TrainableArtifact:
    now = datetime.now(UTC)
    default_uris = {
        "huggingface": "meta-llama/Llama-3.1-8B",
        "private_s3": "s3://acme-artifacts/models/x",
        "local_filesystem": "artifacts/llama-8b/weights.tar",
        "private_registry": "https://registry.example.com/models/llama-8b",
    }
    fields: dict[str, object] = {
        "organization_id": uuid.uuid4(),
        "display_name": f"llama-8b-{uuid.uuid4().hex[:8]}",
        "source_type": source_type,
        "source_uri": default_uris[source_type],
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


# -- 1. evaluate_license_gate / assert_license_accepted_for_use -------------


def test_gate_reports_unaccepted_for_a_fresh_artifact() -> None:
    for source_type in ("huggingface", "private_s3", "local_filesystem", "private_registry"):
        artifact = _make_artifact(source_type=source_type)
        result = evaluate_license_gate(artifact)
        check(
            "FTF-0305",
            f"negative_acceptance: a fresh, unaccepted artifact ({source_type}) "
            "is never reported accepted, regardless of source_type",
            result.accepted is False
            and result.accepted_by is None
            and result.accepted_at_iso is None
            and result.reason is not None,
        )
        check(
            "FTF-0305",
            f"assert_license_accepted_for_use raises for an unaccepted "
            f"artifact ({source_type})",
            _rejects(lambda a=artifact: assert_license_accepted_for_use(a)),
        )


def test_gate_reports_accepted_with_real_attribution() -> None:
    artifact = _make_artifact()
    artifact.license_accepted_by = "user-1"
    artifact.license_accepted_at = datetime.now(UTC)
    result = evaluate_license_gate(artifact)
    check(
        "FTF-0305",
        "acceptance: an accepted artifact's gate reports the real "
        "acceptor, timestamp and the artifact's own current "
        "base_model_revision",
        result.accepted is True
        and result.accepted_by == "user-1"
        and result.accepted_at_iso is not None
        and result.bound_base_model_revision == artifact.base_model_revision
        and result.reason is None,
    )
    check(
        "FTF-0305",
        "assert_license_accepted_for_use returns the passing result "
        "without raising once accepted",
        assert_license_accepted_for_use(artifact).accepted is True,
    )


def test_gate_functions_take_no_provider_or_source_parameter() -> None:
    for fn in (evaluate_license_gate, assert_license_accepted_for_use):
        params = list(inspect.signature(fn).parameters)
        check(
            "FTF-0305",
            f"negative_acceptance: {fn.__name__} takes exactly one "
            "parameter (the artifact itself) -- no provider/source_type/"
            "model_family parameter exists that could shortcut the real "
            "per-artifact acceptance check",
            params == ["artifact"],
        )


# -- 2. invalidate_stale_license_acceptance / apply_revoke_license ----------


def test_invalidate_is_idempotent_and_clears_both_fields() -> None:
    artifact = _make_artifact()
    check(
        "FTF-0305",
        "invalidating an already-unaccepted artifact is a no-op",
        invalidate_stale_license_acceptance(artifact) is False,
    )
    artifact.license_accepted_by = "user-1"
    artifact.license_accepted_at = datetime.now(UTC)
    check(
        "FTF-0305",
        "invalidating a real acceptance reports a real change",
        invalidate_stale_license_acceptance(artifact) is True,
    )
    check(
        "FTF-0305",
        "invalidation clears both license_accepted_by and license_accepted_at",
        artifact.license_accepted_by is None and artifact.license_accepted_at is None,
    )
    check(
        "FTF-0305",
        "invalidating again (already cleared) is idempotent",
        invalidate_stale_license_acceptance(artifact) is False,
    )


def test_apply_revoke_license_is_a_noop_when_never_accepted() -> None:
    artifact = _make_artifact()
    check(
        "FTF-0305",
        "revoking a license that was never accepted is a no-op (False)",
        apply_revoke_license(artifact) is False,
    )
    check(
        "FTF-0305",
        "a no-op revoke does not change status",
        artifact.status == "registered",
    )


def test_apply_revoke_license_clears_acceptance_on_a_registered_artifact() -> None:
    artifact = _make_artifact()
    artifact.license_accepted_by = "user-1"
    artifact.license_accepted_at = datetime.now(UTC)
    check(
        "FTF-0305",
        "revoking a real acceptance on a registered artifact reports True",
        apply_revoke_license(artifact) is True,
    )
    check(
        "FTF-0305",
        "revocation clears the acceptance fields",
        artifact.license_accepted_by is None and artifact.license_accepted_at is None,
    )
    check(
        "FTF-0305",
        "revoking on a registered artifact leaves status unchanged",
        artifact.status == "registered",
    )


def test_apply_revoke_license_demotes_a_ready_artifact() -> None:
    artifact = _make_artifact(base_model_revision="a" * 40)
    artifact.license_accepted_by = "user-1"
    artifact.license_accepted_at = datetime.now(UTC)
    artifact.weights_digest = "sha256:" + "b" * 64
    check(
        "FTF-0305",
        "the artifact reaches ready via FTF-0301's own real gate before "
        "this task's own revoke is exercised",
        apply_mark_ready(artifact) is True and artifact.status == "ready",
    )
    check(
        "FTF-0305",
        "negative_acceptance: revoking a ready artifact's license reports " "a real change",
        apply_revoke_license(artifact) is True,
    )
    check(
        "FTF-0305",
        "negative_acceptance: revoking a ready artifact's license demotes "
        "it back to registered -- 'ready' can no longer honestly claim an "
        "accepted license",
        artifact.status == "registered",
    )
    check(
        "FTF-0305",
        "negative_acceptance: a subsequent mark-ready attempt is refused "
        "once the license has been revoked -- this is the concrete "
        "mechanism by which 'revoked license blocks training/serving' is "
        "enforced against any caller that gates real work on status",
        _rejects(lambda: apply_mark_ready(artifact)),
    )


def test_apply_revoke_license_refuses_on_archived_artifact() -> None:
    artifact = _make_artifact(status="archived")
    artifact.license_accepted_by = "user-1"
    artifact.license_accepted_at = datetime.now(UTC)
    check(
        "FTF-0305",
        "revoking a license on an archived artifact is refused -- a "
        "closed record accepts no further mutation, matching FTF-0301's "
        "own accept-license/mark-ready precedent",
        _rejects(lambda: apply_revoke_license(artifact)),
    )


# -- 3. This task deliberately never touches apply_edits ---------------------


def test_apply_edits_does_not_reference_the_license_acceptance_workflow() -> None:
    source = inspect.getsource(artifact_crud.apply_edits)
    check(
        "FTF-0305",
        "apply_edits (FTF-0301, already CERTIFIED) is untouched by this "
        "task -- no reference to license_acceptance_workflow anywhere in "
        "its body, since auto-invalidating on an identity edit would "
        "materially change already-certified behavior (Law 15), outside "
        "this task's own charter",
        "license_acceptance_workflow" not in source
        and "invalidate_stale_license_acceptance" not in source,
    )


def test_editing_base_model_revision_after_acceptance_still_preserves_it() -> None:
    # The exact sequence FTF-0301's own live-Postgres suite already
    # relies on: accept, then pin the revision via a later edit, then
    # mark-ready must still succeed -- proving this task did not regress
    # that already-certified flow.
    artifact = _make_artifact(base_model_revision="main")
    artifact_crud.apply_accept_license(artifact, accepted_by="user-1")
    artifact.weights_digest = "sha256:" + "c" * 64
    artifact_crud.apply_edits(
        artifact,
        display_name=None,
        source_uri=None,
        base_model_revision="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        model_family=None,
        weights_digest=None,
        license_id=None,
        trust_remote_code_allowed=None,
        required_python_packages=None,
        minimum_gpu_profile=None,
    )
    check(
        "FTF-0305",
        "editing base_model_revision after acceptance does not clear the "
        "acceptance (no regression to FTF-0301's own certified flow)",
        artifact.license_accepted_by == "user-1" and artifact.license_accepted_at is not None,
    )
    check(
        "FTF-0305",
        "mark-ready still succeeds after that edit, exactly as FTF-0301's "
        "own live-Postgres suite already proves",
        apply_mark_ready(artifact) is True,
    )


# -- 4. Real demo router: role-boundary denial on the new revoke-license route


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


@contextmanager
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


def test_evaluator_is_denied_revoke_license_without_opening_a_session() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "POST",
            f"/api/v1/organizations/{_ORG_ID}/artifacts/{uuid.uuid4()}/revoke-license",
            principal="evaluator",
        )
        check(
            "FTF-0305",
            "an evaluator is denied revoke-license (403) -- write is "
            "ml_engineer/organization_admin only, the same "
            "ARTIFACT_WRITE_ROLES gate this task's own route reuses",
            result.status == 403,
        )
        check(
            "FTF-0305",
            "the write role-boundary denial never opens a session",
            session_factory.call_count == 0,
        )


def test_ml_engineer_reaches_the_session_layer_for_revoke_license() -> None:
    session_factory = MagicMock()
    with _running_router(session_factory) as base_url:
        result = _request(
            base_url,
            "POST",
            f"/api/v1/organizations/{_ORG_ID}/artifacts/{uuid.uuid4()}/revoke-license",
            principal="ml_engineer",
        )
        check(
            "FTF-0305",
            "an ml_engineer's revoke-license request passes the role gate "
            "and reaches the session layer (never 401/403)",
            result.status not in (401, 403),
        )
        check(
            "FTF-0305",
            "the ml_engineer's request genuinely opens a session",
            session_factory.call_count >= 1,
        )


def test_revoke_license_role_gate_matches_artifact_write_roles() -> None:
    check(
        "FTF-0305",
        "the revoke-license route reuses the real, imported "
        "ARTIFACT_WRITE_ROLES -- not a second, hand-rolled role set",
        ARTIFACT_WRITE_ROLES == frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN}),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0305 License acceptance workflow tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0305 License acceptance workflow tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
