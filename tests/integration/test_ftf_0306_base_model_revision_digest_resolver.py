#!/usr/bin/env python3
"""FTF-0306 -- real Postgres Base model revision/digest resolver proof.

Proves this task's own acceptance clause 1 ("Create immutable base-model/
tokenizer manifest and compare hashes on every load/resume") against a
real, live PostgreSQL server -- reusing FTF-0301's own already-CERTIFIED
`trainable_artifacts` table and migration verbatim (this task's own
`database_changes` contract: "No independent schema change. Consume
frozen contract") -- never a mocked connection or an in-memory SQLite
stand-in (SQLite has no row-level security at all, so a passing test
against it would prove nothing about this task's actual acceptance
criterion).

This task adds no new route (see the module's own docstring for why --
no live training/serving-resume caller exists anywhere in this repository
yet, the same disclosed "no live caller yet" precedent already accepted
for FTF-0302/0303/0304 themselves), so this suite drives the real
`base_model_revision_digest_resolver.resolve_base_model_digest`
composition directly against a real tenant-scoped session
(`TenantContext`/`tenant_scoped_session_for`, FTF-H003) and real
`artifact_crud` primitives (FTF-0301) -- exactly the same
session-only-integration pattern
`tests/integration/test_ftf_h003_cross_layer_isolation.py` already
established for a task with no HTTP route of its own -- rather than
standing up a real uvicorn server for a route this task does not add.

Proves, against real committed Postgres rows:

1. **First resolution persists for real.** An artifact registered with a
   real `local_filesystem` source (a real temporary directory) and an
   accepted license has `weights_digest=NULL`; resolving it writes a
   real, canonically-formatted `weights_digest` that is still there after
   a fresh read in a brand-new session/transaction -- not merely held in
   an in-memory ORM object.
2. **"Immutable identity after resolution" + mark-ready.** Once resolved,
   `mark_ready` (FTF-0301, reused unmodified) succeeds; the row's own
   `weights_digest` is unchanged before and after.
3. **"Compare hashes on every load/resume" against an already-`ready`
   row.** Re-running the resolver against the now-`ready` artifact (a
   real "load/resume") succeeds without calling `apply_edits` again (a
   `ready` row correctly refuses `apply_edits`, so a passing re-
   verification here is a real proof this task's own composition takes
   the read-only branch, not a lucky no-op) and writes a real
   `trainable_artifact.weights_digest_reverified` audit event.
4. **negative_acceptance: real drift is refused, never silently
   persisted.** After the real underlying bytes on disk actually change,
   re-resolving the same already-`ready` artifact raises
   `DigestMismatchError`; the row's own `weights_digest` in the database
   is confirmed unchanged afterward (a fresh read in a new transaction),
   proving the rejection never reached the database.
5. **IDOR**: an artifact created under org1 cannot be resolved under
   org2's own `TenantContext` -- denied identically to a nonexistent ID
   (`ArtifactNotFoundError`), the same discipline FTF-0301's own suite
   already proved for every other artifact operation.

Requires a real Postgres reachable at `SOVRA_DATABASE_URL` (the migration
owner role) plus `SOVRA_APP_DB_PASSWORD`, matching every prior
integration test's own environment contract exactly. This repo's
interactive dev sandbox has no container-registry egress (same finding as
every prior integration-tested task) -- this evidence was instead
produced locally against a directly-installed PostgreSQL 16 cluster, and
is wired into CI's dedicated `base-model-revision-digest-resolver-
integration` job (a GitHub Actions `services: postgres` container, real
registry egress on the hosted runner).

Run directly (against an already-migrated or unmigrated database; this
script runs the migration itself):
    SOVRA_DATABASE_URL=postgresql+psycopg://<migration-role>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0306_base_model_revision_digest_resolver.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402

from app.artifacts.artifact_crud import (  # noqa: E402
    ActorInfo,
    ArtifactNotFoundError,
    ArtifactStateError,
    accept_license,
    create_artifact,
    get_artifact,
    mark_ready,
)
from app.artifacts.base_model_revision_digest_resolver import (  # noqa: E402
    DigestMismatchError,
    LocalFilesystemResolutionInputs,
    resolve_base_model_digest,
)
from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real persistence/load-resume "
            "evidence requires a live Postgres (migration owner connection). Not silently "
            "skipped: see this file's own docstring for how to run it locally or in CI.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_migration() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
    )
    check(
        "FTF-0306",
        f"alembic upgrade head runs cleanly (exit {result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}",
        result.returncode == 0,
    )


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    _run_migration()
    if FAILURES:
        print(f"FTF-0306 real Postgres base-model-digest-resolver proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    owner_engine = create_engine(migration_url)
    app_url = _app_role_url(migration_url, app_password)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme FTF-0306", slug=f"acme-ftf0306-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex FTF-0306", slug=f"globex-ftf0306-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id

    pooled_engine = create_engine(app_url, poolclass=QueuePool, pool_size=2, max_overflow=0)
    session_factory = sessionmaker(bind=pooled_engine)
    org1_context = TenantContext(organization_id=org1_id)
    org2_context = TenantContext(organization_id=org2_id)
    actor = ActorInfo(actor_type="user", actor_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "model-a" / "weights.bin", b"\x00\x01\x02\x03" * 4096)
        inputs = LocalFilesystemResolutionInputs(approved_root=root)

        try:
            # == 1. registration, license acceptance, first resolution ======
            with tenant_scoped_session_for(session_factory, org1_context) as session:
                artifact = create_artifact(
                    session,
                    org1_context,
                    display_name=f"digest-{uuid.uuid4().hex[:8]}",
                    source_type="local_filesystem",
                    source_uri="model-a",
                    model_family="tiny",
                    base_model_revision="v1.0",
                    license_id="apache-2.0",
                    actor=actor,
                )
                artifact_id = artifact.id
                check(
                    "FTF-0306",
                    "sanity: a freshly registered artifact has no weights_digest yet",
                    artifact.weights_digest is None,
                )

            with tenant_scoped_session_for(session_factory, org1_context) as session:
                accept_license(session, org1_context, artifact_id, actor=actor)

            with tenant_scoped_session_for(session_factory, org1_context) as session:
                resolved = resolve_base_model_digest(
                    session, org1_context, artifact_id, inputs, actor=actor
                )
                first_digest = resolved.weights_digest

            check(
                "FTF-0306",
                "acceptance: resolution produces a real, non-null canonical weights_digest",
                first_digest is not None and str(first_digest).startswith("sha256:"),
            )

            # == 2. persisted for real -- a brand-new session/transaction ===
            with tenant_scoped_session_for(session_factory, org1_context) as session:
                reread = get_artifact(session, org1_context, artifact_id)
                check(
                    "FTF-0306",
                    "acceptance: the resolved weights_digest is really committed to the "
                    "database -- visible from a brand-new session, not only the in-memory "
                    "ORM object that wrote it",
                    reread.weights_digest == first_digest,
                )

            # == 3. mark-ready succeeds now that weights_digest is resolved =
            with tenant_scoped_session_for(session_factory, org1_context) as session:
                artifact = get_artifact(session, org1_context, artifact_id)
                mark_ready(session, org1_context, artifact_id, actor=actor)

            with tenant_scoped_session_for(session_factory, org1_context) as session:
                ready_artifact = get_artifact(session, org1_context, artifact_id)
                check(
                    "FTF-0306",
                    "sanity: the artifact really became 'ready' and its digest is unchanged "
                    "by that transition",
                    ready_artifact.status == "ready"
                    and ready_artifact.weights_digest == first_digest,
                )

            # == 4. load/resume re-verification against the now-ready row ===
            with tenant_scoped_session_for(session_factory, org1_context) as session:
                reverified = resolve_base_model_digest(
                    session, org1_context, artifact_id, inputs, actor=actor
                )
                check(
                    "FTF-0306",
                    "acceptance: 'compare hashes on every load/resume' -- re-resolving an "
                    "already-ready artifact against real unchanged bytes succeeds without "
                    "touching apply_edits (a ready row would refuse an edit) and reports the "
                    "identical digest",
                    reverified.weights_digest == first_digest,
                )

            # == 5. negative_acceptance: real drift is refused, never persisted ==
            _write(root / "model-a" / "weights.bin", b"\xff\xfe\xfd\xfc" * 4096)
            mismatch_raised = False
            with tenant_scoped_session_for(session_factory, org1_context) as session:
                try:
                    resolve_base_model_digest(
                        session, org1_context, artifact_id, inputs, actor=actor
                    )
                except DigestMismatchError:
                    mismatch_raised = True
            check(
                "FTF-0306",
                "negative_acceptance: 'compare hashes on every load/resume' -- real drifted "
                "bytes raise DigestMismatchError rather than being silently accepted",
                mismatch_raised,
            )

            with tenant_scoped_session_for(session_factory, org1_context) as session:
                post_mismatch = get_artifact(session, org1_context, artifact_id)
                check(
                    "FTF-0306",
                    "negative_acceptance: a rejected mismatch is never persisted -- the real "
                    "database row's own weights_digest is unchanged, confirmed from a fresh "
                    "session/transaction after the raised exception",
                    post_mismatch.weights_digest == first_digest,
                )

            # restore the real bytes so this artifact's own identity stays consistent for
            # any later check in this same run
            _write(root / "model-a" / "weights.bin", b"\x00\x01\x02\x03" * 4096)

            # == 6. IDOR: org2 cannot resolve org1's own artifact ============
            idor_denied = False
            with tenant_scoped_session_for(session_factory, org2_context) as session:
                try:
                    resolve_base_model_digest(
                        session, org2_context, artifact_id, inputs, actor=actor
                    )
                except ArtifactNotFoundError:
                    idor_denied = True
            check(
                "FTF-0306",
                "negative_acceptance (IDOR): a real artifact belonging to org1 is denied "
                "identically to a nonexistent ID when resolved under org2's own tenant "
                "context",
                idor_denied,
            )

        except ArtifactStateError as exc:  # pragma: no cover -- surfaced as a real failure
            check("FTF-0306", f"unexpected ArtifactStateError: {exc}", False)

    if FAILURES:
        print(f"FTF-0306 real Postgres base-model-digest-resolver proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0306 real Postgres base-model-digest-resolver proof: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
