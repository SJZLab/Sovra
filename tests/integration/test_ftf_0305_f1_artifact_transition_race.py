#!/usr/bin/env python3
"""FTF-0305-F1 -- real Postgres: artifact transitions serialize; ready needs a license.

Criterion ids: F1-INTERLEAVE (deterministic mark_ready / revoke_license
interleave never leaves a `ready` row without an accepted license),
F1-CONCURRENT (N concurrent trials, both orders), F1-CHECK (the database
itself refuses ready-without-license as `sovra_app`, on INSERT and UPDATE),
F1-MIGRATION (0029 refuses to run over a violating row without changing
it, then upgrades cleanly; downgrade drops only the CHECK).

Requires SOVRA_DATABASE_URL (migration owner) and SOVRA_APP_DB_PASSWORD,
like every integration test here. Run directly:
    python3 tests/integration/test_ftf_0305_f1_artifact_transition_race.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts import artifact_crud as crud  # noqa: E402
from app.auth.organization_workspace_schema import APP_ROLE_NAME, Organization  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402

FAILURES: list[str] = []
_REV = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
_DIGEST = "sha256:" + "e" * 64


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, SOVRA_DATABASE_URL=url)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _app_url(url: str, password: str) -> str:
    p = urlsplit(url)
    netloc = f"{APP_ROLE_NAME}:{password}@{p.hostname}" + (f":{p.port}" if p.port else "")
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


ACTOR = crud.ActorInfo(actor_type="user", actor_id="user-ml", request_id="req-1")


def main() -> None:
    url = _env("SOVRA_DATABASE_URL")
    password = _env("SOVRA_APP_DB_PASSWORD")

    up = _alembic(url, "upgrade", "head")
    check("F1-MIGRATION", f"upgrade head clean: {up.stderr[-400:]}", up.returncode == 0)
    if FAILURES:
        return _finish()

    owner = create_engine(url)
    app_engine = create_engine(_app_url(url, password))
    factory = sessionmaker(bind=app_engine)

    with Session(owner) as s:
        org = Organization(name="F1", slug=f"f1-{uuid.uuid4().hex[:8]}")
        s.add(org)
        s.commit()
        org_id = org.id
    ctx = TenantContext(organization_id=org_id)

    def new_licensed_registered(name: str) -> uuid.UUID:
        """registered + license accepted + digest resolved: one step from ready."""
        with tenant_scoped_session_for(factory, ctx) as session:
            art = crud.create_artifact(
                session,
                ctx,
                display_name=name,
                source_type="huggingface",
                source_uri="meta-llama/Llama-3.1-8B",
                model_family="llama",
                base_model_revision=_REV,
                license_id="llama3",
                actor=ACTOR,
            )
            aid = art.id
            crud.update_artifact(session, ctx, aid, weights_digest=_DIGEST, actor=ACTOR)
            crud.accept_license(session, ctx, aid, actor=ACTOR)
        return aid

    def row(aid: uuid.UUID) -> tuple[str, object]:
        with Session(owner) as s:
            r = s.execute(
                text("SELECT status, license_accepted_at FROM trainable_artifacts WHERE id=:i"),
                {"i": aid},
            ).one()
            return r[0], r[1]

    def invariant_ok(aid: uuid.UUID) -> bool:
        status, accepted = row(aid)
        return not (status == "ready" and accepted is None)

    # -- F1-INTERLEAVE: A holds mark_ready open; B's revoke must wait for it --
    def run_interleave(first: str, second: str) -> None:
        aid = new_licensed_registered(f"interleave-{first}")
        a_has_row = threading.Event()
        release_a = threading.Event()
        b_done = threading.Event()
        errors: list[BaseException] = []

        def txn_a(op: str = first, target: uuid.UUID = aid) -> None:
            try:
                with tenant_scoped_session_for(factory, ctx) as session:
                    getattr(crud, op)(session, ctx, target, actor=ACTOR)
                    a_has_row.set()
                    release_a.wait(10)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                a_has_row.set()

        def txn_b(op: str = second, target: uuid.UUID = aid) -> None:
            try:
                with tenant_scoped_session_for(factory, ctx) as session:
                    getattr(crud, op)(session, ctx, target, actor=ACTOR)
            except crud.ArtifactStateError:
                pass  # a refusal is a legal outcome of serialization
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                b_done.set()

        ta = threading.Thread(target=txn_a)
        ta.start()
        a_has_row.wait(10)
        tb = threading.Thread(target=txn_b)
        tb.start()
        blocked = not b_done.wait(1.0)
        check("F1-INTERLEAVE", f"{second} waits on {first}'s row lock", blocked)
        release_a.set()
        ta.join(15)
        tb.join(15)
        check("F1-INTERLEAVE", f"{first}->{second}: no unexpected error: {errors!r}", not errors)
        check("F1-INTERLEAVE", f"{first}->{second}: never ready without license", invariant_ok(aid))

    for first, second in (("mark_ready", "revoke_license"), ("revoke_license", "mark_ready")):
        run_interleave(first, second)

    # -- F1-CONCURRENT: many trials, both operations racing --
    bad = 0
    unexpected: list[str] = []

    def run_trial(i: int) -> None:
        nonlocal bad
        aid = new_licensed_registered(f"race-{i}")
        barrier = threading.Barrier(2)

        def racer(op: str, target: uuid.UUID = aid) -> None:
            try:
                barrier.wait(5)
                with tenant_scoped_session_for(factory, ctx) as session:
                    getattr(crud, op)(session, ctx, target, actor=ACTOR)
            except crud.ArtifactStateError:
                pass
            except BaseException as exc:  # noqa: BLE001
                unexpected.append(repr(exc))

        ts = [threading.Thread(target=racer, args=(op,)) for op in ("mark_ready", "revoke_license")]
        for t in ts:
            t.start()
        for t in ts:
            t.join(20)
        if not invariant_ok(aid):
            bad += 1

    for i in range(30):
        run_trial(i)
    check("F1-CONCURRENT", f"30 trials, {bad} ended ready without a license", bad == 0)
    check("F1-CONCURRENT", f"no unexpected errors: {unexpected[:3]}", not unexpected)

    # -- F1-CHECK: sovra_app cannot write ready-without-license directly --
    aid = new_licensed_registered("check-target")
    with Session(app_engine) as s:
        s.execute(
            text("SELECT set_config('app.current_organization_id', :o, true)"), {"o": str(org_id)}
        )
        s.execute(
            text(
                "UPDATE trainable_artifacts SET license_accepted_at=NULL, "
                "license_accepted_by=NULL WHERE id=:i"
            ),
            {"i": aid},
        )
        try:
            s.execute(text("UPDATE trainable_artifacts SET status='ready' WHERE id=:i"), {"i": aid})
            s.commit()
            check("F1-CHECK", "UPDATE to ready without license is refused", False)
        except IntegrityError as exc:
            s.rollback()
            check(
                "F1-CHECK",
                "refusal names the constraint",
                "ck_trainable_artifacts_ready_needs_license" in str(exc),
            )
    with Session(app_engine) as s:
        s.execute(
            text("SELECT set_config('app.current_organization_id', :o, true)"), {"o": str(org_id)}
        )
        try:
            s.execute(
                text(
                    "INSERT INTO trainable_artifacts (id, organization_id, display_name, "
                    "source_type, "
                    "source_uri, model_family, base_model_revision, weights_digest, license_id, "
                    "trust_remote_code_allowed, status) VALUES "
                    "(:i, :o, 'ins-ready', 'huggingface', "
                    "'a/b', 'f', :r, :d, 'x', false, 'ready')"
                ),
                {"i": uuid.uuid4(), "o": org_id, "r": _REV, "d": _DIGEST},
            )
            s.commit()
            check("F1-CHECK", "INSERT as ready without license is refused", False)
        except IntegrityError:
            s.rollback()
    check("F1-CHECK", "the failed UPDATE left the row registered", row(aid)[0] == "registered")

    # -- F1-MIGRATION: refuse over a violating row, unchanged; then succeed --
    down = _alembic(url, "downgrade", "0028_model_import_state_machine")
    check("F1-MIGRATION", f"downgrade drops the CHECK: {down.stderr[-300:]}", down.returncode == 0)
    with Session(owner) as s:
        has = s.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname='ck_trainable_artifacts_ready_needs_license'"
            )
        ).scalar()
        check("F1-MIGRATION", "downgrade removed the CHECK", has == 0)
        s.execute(
            text(
                "UPDATE trainable_artifacts SET status='ready', license_accepted_at=NULL, "
                "license_accepted_by=NULL WHERE id=:i"
            ),
            {"i": aid},
        )
        s.commit()
    refused = _alembic(url, "upgrade", "head")
    check("F1-MIGRATION", "upgrade refuses over a violating row", refused.returncode != 0)
    check(
        "F1-MIGRATION",
        "refusal is typed and names the row",
        "ArtifactReadyWithoutLicenseError" in refused.stderr and str(aid) in refused.stderr,
    )
    check("F1-MIGRATION", "violating row left unchanged", row(aid) == ("ready", None))
    with Session(owner) as s:
        s.execute(
            text("UPDATE trainable_artifacts SET status='registered' WHERE id=:i"), {"i": aid}
        )
        s.commit()
    again = _alembic(url, "upgrade", "head")
    check(
        "F1-MIGRATION",
        f"upgrade succeeds once resolved: {again.stderr[-300:]}",
        again.returncode == 0,
    )

    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0305-F1 real transition-race proof: {len(FAILURES)} failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)
    print("FTF-0305-F1 real transition-race proof: all checks passed")


if __name__ == "__main__":
    main()
