#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-0519-P1 -- real Postgres proof for immutable split manifests (migration 0030).

Proves against a live PostgreSQL server (never SQLite -- it has no row-level security) every clause
of the QUEUE.md FTF-0519-P1 contract. The sources are real `datasets` rows created through the
FTF-0501-P1 upload store, their quality reports are produced by the real FTF-0508..0514 detectors
through the FTF-0516-P1 store, the split is FTF-0517's `allocate_splits` and the leakage report is
FTF-0518's `detect_split_leakage`, all driven by `dataset_split_manifest_store`:

- the migration upgrades, downgrades and re-upgrades with one head; all four tables ENABLE + FORCE
  row-level security with a non-allow-all `tenant_isolation` policy; the app role has no DELETE,
  no UPDATE on a child table and may update only the lock columns of a manifest;
- golden: two training sources plus a held-out and a development source freeze into version 1 with
  exact counts, independently recomputed role hashes, the grant snapshot and one content-free audit
  row; a changed seed / source version / quality report / grant is version 2 with parent 1 and
  version 1 byte-identical; an unchanged re-proposal writes nothing;
- every refusal code writes nothing (row counts unchanged), each next to a positive control;
- raw `sovra_app` SQL that bypasses the store is refused by the database itself: an UPDATE of a
  locked row, a lock without four roles, a lock whose counts differ from the assignments, a lock of
  a blocked leakage report, a column that is not grantable, DELETE, a child insert after the lock,
  `heldout_test` as a role, a wrong (role, purpose) pair, a held-out record outside the final role,
  a role the source's frozen grant does not contain, a legacy strategy without lineage, a version
  gap and a cross-tenant INSERT;
- training access: only train / validation rows, never a development or final record id; a revoked
  or narrowed grant, a changed quality report or exclusion set, an unresolved quality issue and a
  tampered role hash or leakage report each refuse; the legacy path keeps `heldout_test` in final.

Requires SOVRA_DATABASE_URL (migration-owner role) and SOVRA_APP_DB_PASSWORD.
Run: python3 tests/integration/test_ftf_0519_p1_split_manifests.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from packages.security.security_profile_enforcement import Destination  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from test_ftf_0516_p1_quality_issues import _extra, _full_runs  # noqa: E402
from workers.data import immutable_split_manifest as ism  # noqa: E402
from workers.data.group_aware_split_engine import SplitSpec  # noqa: E402
from workers.data.split_leakage_detector import (  # noqa: E402
    EmbeddingRoute,
    LeakageRecord,
    PriorQualificationSource,
)

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.datasets import dataset_quality_store as qstore  # noqa: E402
from app.datasets import dataset_split_manifest_store as store  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0519-P1"
ROLES = ("train", "validation", "development_eval", "final_qualification")
VERSIONS = {name: "1.0.0" for name in sorted(qstore.REQUIRED_DETECTORS)}
SPEC = SplitSpec("sha256_group_hash_v1", "seed-0519", (70, 10, 10, 10))
ROUTE = EmbeddingRoute(Destination(kind="internal"), "standard", "standard")
BLOCKED_ROUTE = EmbeddingRoute(
    Destination(kind="external", provider="acme-embed"), "zero_egress", "standard"
)
ALL_PURPOSES = ["training", "validation", "candidate_development", "held_out_evaluation"]


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"BLOCKED_EXTERNAL: {name} is not set (see this file's docstring)", file=sys.stderr)
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _alembic(args: list[str], database_url: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _words(seed: str) -> str:
    digest = _sha(seed)
    return " ".join(digest[i : i + 6] for i in range(0, 60, 6))


def _source_record(prefix: str, position: int, *, keys: tuple[str, ...] = ()) -> store.SourceRecord:
    body = _words(f"{prefix}-{position}")
    return store.SourceRecord(
        batch_position=position,
        content={
            "messages": [{"role": "user", "content": [{"type": "text", "text": body}]}],
            "metadata": {},
        },
        record_sha256=_sha(f"raw-{prefix}-{position}"),
        normalized_sha256=_sha(f"norm-{prefix}-{position}"),
        group_keys=keys,
    )


def _long_record(prefix: str, position: int) -> store.SourceRecord:
    """A record long enough for the near-duplicate thresholds to resolve one changed word."""
    body = " ".join(_words(f"{prefix}-{position}-{i}") for i in range(4))
    return replace(
        _source_record(prefix, position),
        content={
            "messages": [{"role": "user", "content": [{"type": "text", "text": body}]}],
            "metadata": {},
        },
    )


def _records(prefix: str, count: int) -> tuple[store.SourceRecord, ...]:
    return tuple(_source_record(prefix, i) for i in range(count))


def _independent_role_hash(rows: list[tuple[str, str, str, str]]) -> str:
    """Re-implemented here on purpose: sha256 over the sorted
    (record_id, record_sha256, normalized_sha256, group_id) rows as canonical JSON."""
    body = json.dumps([list(r) for r in sorted(rows)], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class World:
    """One project with its three source connections. Scenarios use their own world so that a
    locked manifest in one never becomes a required prior campaign of another."""

    label: str
    project_id: uuid.UUID
    conns: dict[str, uuid.UUID]
    ctx: TenantContext


class Env:
    """Everything the checks share. Built once by `main`."""

    def __init__(self) -> None:
        self.owner: Any = None
        self.factory: Any = None
        self.org1 = self.org2 = self.ws1 = self.ws2 = uuid.UUID(int=0)
        self.ctx1 = self.ctx2 = TenantContext(organization_id=uuid.UUID(int=0))
        self.records: dict[uuid.UUID, tuple[store.SourceRecord, ...]] = {}
        self.upload_actor = ustore.UploadActor("user", "seed-user", "req-upload")
        self.ml_quality = qstore.QualityActor("user", "user-ml", "ml_engineer", "req-ml")
        self.admin = store.SplitActor("user", "user-org1-admin", "workspace_admin", "req-admin")
        self.admin2 = store.SplitActor("user", "user-org1-admin-2", "organization_admin", "req-a2")
        self.ml = store.SplitActor("user", "user-org1-ml", "ml_engineer", "req-ml")

    # ---- plumbing --------------------------------------------------------
    def run(
        self, ctx: TenantContext, fn: Callable[[Session], Any]
    ) -> tuple[Any, BaseException | None]:
        try:
            with tenant_scoped_session_for(self.factory, ctx) as session:
                return fn(session), None
        except BaseException as exc:  # noqa: BLE001 -- the test inspects the type
            return None, exc

    def scalar(self, sql: str, **params: Any) -> Any:
        with self.owner.connect() as conn:
            return conn.execute(text(sql), params).scalar()

    def rows(self, sql: str, **params: Any) -> list[Any]:
        with self.owner.connect() as conn:
            return list(conn.execute(text(sql), params).all())

    def execute(self, sql: str, **params: Any) -> None:
        with self.owner.begin() as conn:
            conn.execute(text(sql), params)

    def counts(self) -> tuple[int, ...]:
        return tuple(
            int(self.scalar(f"SELECT count(*) FROM {t}") or 0)
            for t in (
                "dataset_split_manifests",
                "dataset_split_manifest_roles",
                "dataset_split_manifest_sources",
                "dataset_split_assignments",
                "audit_events",
            )
        )

    def fingerprint(self, manifest_id: uuid.UUID) -> str:
        return str(
            self.scalar(
                "SELECT md5(coalesce((SELECT string_agg(m::text, '|' ORDER BY m.id) "
                "FROM dataset_split_manifests m WHERE m.id=:m), '') "
                "|| coalesce((SELECT string_agg(r::text, '|' ORDER BY r.role) "
                "FROM dataset_split_manifest_roles r WHERE r.manifest_id=:m), '') "
                "|| coalesce((SELECT string_agg(s::text, '|' ORDER BY s.dataset_id) "
                "FROM dataset_split_manifest_sources s WHERE s.manifest_id=:m), '') "
                "|| coalesce((SELECT string_agg(a::text, '|' ORDER BY a.record_id) "
                "FROM dataset_split_assignments a WHERE a.manifest_id=:m), ''))",
                m=manifest_id,
            )
        )

    # ---- seeding ---------------------------------------------------------
    def world(self, label: str, *, org: int = 1) -> World:
        org_id, ws_id, ctx = (
            (self.org1, self.ws1, self.ctx1) if org == 1 else (self.org2, self.ws2, self.ctx2)
        )
        with Session(self.owner) as session:
            project = Project(
                organization_id=org_id,
                workspace_id=ws_id,
                name=f"{label}-{uuid.uuid4().hex[:6]}",
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            session.add(project)
            session.commit()
            project_id = project.id
        conns: dict[str, uuid.UUID] = {}
        for kind, purposes in (
            ("all", ALL_PURPOSES),
            ("train", ["training"]),
            ("baseline", ["baseline_only"]),
        ):
            conn_id = uuid.uuid4()
            self.execute(
                "INSERT INTO dataset_source_connections (id, organization_id, project_id, "
                "source_type, display_name, allowed_purposes_json, security_classification, "
                "connection_status, created_by) VALUES (:id, :org, :project, 'upload', :name, "
                "CAST(:purposes AS jsonb), 'internal', 'active', 'seed')",
                id=conn_id,
                org=org_id,
                project=project_id,
                name=f"{label}-{kind}",
                purposes=json.dumps(purposes),
            )
            conns[kind] = conn_id
        return World(label, project_id, conns, ctx)

    def dataset(
        self,
        w: World,
        name: str,
        purpose: str,
        records: int,
        *,
        connection: str = "all",
        report: bool = True,
    ) -> ustore.Dataset:
        sha = _sha(f"{name}-{uuid.uuid4()}")
        conn = w.conns[connection]

        def build(session: Session) -> ustore.Dataset:
            opened = ustore.create_upload_session(
                session,
                w.ctx,
                project_id=w.project_id,
                connection_id=conn,
                purpose=purpose,
                declared_size_bytes=100,
                declared_sha256=sha,
                declared_content_type="application/jsonl",
                max_bytes=100,
                actor=self.upload_actor,
            )
            return ustore.record_upload_verified_and_create_dataset(
                session,
                w.ctx,
                project_id=w.project_id,
                upload_session_id=opened.id,
                dataset_name=name,
                verified_size_bytes=100,
                verified_sha256=sha,
                verified_content_type="application/jsonl",
                actor=self.upload_actor,
            )

        dataset, error = self.run(w.ctx, build)
        if error is not None:
            raise error
        self.records[dataset.id] = _records(str(dataset.id), records)
        if report:
            self.quality(w, dataset)
        return dataset

    def quality(
        self,
        w: World,
        dataset: ustore.Dataset,
        *,
        clean: bool = True,
        extra: tuple[Any, ...] = (),
    ) -> qstore.DatasetQualityReport:
        report, error = self.run(
            w.ctx,
            lambda s: qstore.record_quality_report(
                s,
                w.ctx,
                project_id=w.project_id,
                dataset_id=dataset.id,
                runs=_full_runs(clean=clean),
                extra_issues=extra,
                detector_versions=VERSIONS,
                actor=self.ml_quality,
            ),
        )
        if error is not None:
            raise error
        return report

    def exclude(
        self,
        w: World,
        dataset: ustore.Dataset,
        positions: tuple[int, ...],
        *,
        clean: bool = True,
        reason: str = "seeded_exclusion",
        pick: str | None = None,
        extra: bool = True,
    ) -> None:
        """Records a report and resolves one of its issues by excluding that issue's positions.
        With `extra` the issue is a seeded warning over `positions`; otherwise `pick` names an
        issue the detectors themselves raised."""
        report = self.quality(
            w,
            dataset,
            clean=clean,
            extra=(_extra(reason, positions, severity="warning"),) if extra else (),
        )
        target = pick or reason
        issue, error = self.run(
            w.ctx,
            lambda s: list(
                s.query(qstore.DatasetQualityIssue).filter_by(
                    report_id=report.id, reason_code=target
                )
            )[0],
        )
        if error is not None:
            raise error
        _, error = self.run(
            w.ctx,
            lambda s: qstore.submit_resolutions(
                s,
                w.ctx,
                project_id=w.project_id,
                dataset_id=dataset.id,
                quality_report_id=report.id,
                expected_resolution_version=int(
                    self.scalar(
                        "SELECT resolution_version FROM dataset_quality_reports WHERE id=:i",
                        i=report.id,
                    )
                    or 0
                ),
                idempotency_key=uuid.uuid4().hex,
                resolutions=[
                    qstore.ResolutionRequest(
                        issue_id=issue.issue_id,
                        action="exclude",
                        reason="reviewed by the data owner",
                        expected_before_evidence_sha256=issue.evidence_sha256,
                        excluded_batch_positions=tuple(issue.batch_positions),
                    )
                ],
                include_held_out=True,
                actor=self.ml_quality,
            ),
        )
        if error is not None:
            raise error

    def source(self, dataset: ustore.Dataset) -> store.SplitSourceInput:
        return store.SplitSourceInput(dataset.id, self.records[dataset.id])

    def propose(
        self,
        w: World,
        name: str,
        datasets: list[ustore.Dataset],
        *,
        spec: Any = SPEC,
        prior: tuple[PriorQualificationSource, ...] = (),
        actor: store.SplitActor | None = None,
        ctx: TenantContext | None = None,
        project_id: uuid.UUID | None = None,
        sources: list[store.SplitSourceInput] | None = None,
        route: EmbeddingRoute = ROUTE,
        normalization: str = "text-normalization-v1",
        tokenizer: str | None = None,
    ) -> tuple[Any, BaseException | None]:
        use_ctx = ctx or w.ctx
        pid = project_id or w.project_id
        return self.run(
            use_ctx,
            lambda s: store.propose_split_manifest(
                s,
                use_ctx,
                project_id=pid,
                name=name,
                spec=spec,
                sources=sources if sources is not None else [self.source(d) for d in datasets],
                prior_sources=prior,
                route=route,
                normalization_version=normalization,
                tokenizer_identity=tokenizer,
                actor=actor or self.admin,
            ),
        )

    def approve(
        self,
        w: World,
        manifest: Any,
        *,
        reviews: tuple[store.ReviewInput, ...] = (),
        expected: str | None = None,
        actor: store.SplitActor | None = None,
        ctx: TenantContext | None = None,
        manifest_id: uuid.UUID | None = None,
    ) -> tuple[Any, BaseException | None]:
        use_ctx = ctx or w.ctx
        return self.run(
            use_ctx,
            lambda s: store.approve_split_manifest(
                s,
                use_ctx,
                project_id=w.project_id,
                manifest_id=manifest_id or manifest.id,
                expected_proposal_sha256=expected or manifest.proposal_sha256,
                reviews=reviews,
                actor=actor or self.admin,
            ),
        )

    def training(self, w: World, manifest_id: uuid.UUID) -> tuple[Any, BaseException | None]:
        return self.run(
            w.ctx,
            lambda s: store.load_training_split(
                s, w.ctx, project_id=w.project_id, manifest_id=manifest_id
            ),
        )

    def development(self, w: World, manifest_id: uuid.UUID) -> tuple[Any, BaseException | None]:
        return self.run(
            w.ctx,
            lambda s: store.load_development_split(
                s, w.ctx, project_id=w.project_id, manifest_id=manifest_id
            ),
        )

    def prior_for(self, manifest: Any) -> tuple[PriorQualificationSource, ...]:
        """The development / final suites of a locked manifest, rebuilt from the stored ids and
        the test's own record content."""
        out = []
        for role in ("development_eval", "final_qualification"):
            rows = self.rows(
                "SELECT record_id, source_dataset_id, batch_position, record_sha256, "
                "normalized_sha256 FROM dataset_split_assignments WHERE manifest_id=:m AND role=:r",
                m=manifest.id,
                r=role,
            )
            leak = []
            for record_id, dataset_id, position, raw, normalized in rows:
                src = self.records[dataset_id][position]
                leak.append(
                    LeakageRecord(
                        record_id=record_id,
                        content=src.content,
                        record_sha256=raw,
                        normalized_sha256=normalized,
                        group_keys=src.group_keys,
                    )
                )
            out.append(
                PriorQualificationSource(
                    label=f"{manifest.name}@v{manifest.version}:{role}",
                    role=role,
                    manifest_sha256=manifest.split_manifest_sha256,
                    records=tuple(leak),
                )
            )
        return tuple(out)

    def manifest_row(self, manifest_id: uuid.UUID) -> Any:
        return self.rows(
            "SELECT status, version, parent_version, strategy, leakage_status, approved_by, "
            "frozen_manifest_sha256, locked_at, reviewed_leakage_report_sha256, "
            "leakage_report_sha256, proposal_sha256, leakage_reviews, migrated_from_legacy_sha256 "
            "FROM dataset_split_manifests WHERE id=:m",
            m=manifest_id,
        )[0]

    def record_ids(self, manifest_id: uuid.UUID, *roles: str) -> set[str]:
        return {
            r[0]
            for r in self.rows(
                "SELECT record_id FROM dataset_split_assignments "
                "WHERE manifest_id=:m AND role = ANY(:r)",
                m=manifest_id,
                r=list(roles),
            )
        }


def code_of(error: BaseException | None) -> str | None:
    return error.code if isinstance(error, store.SplitManifestError) else None


def expect_refusal(
    env: Env, description: str, code: str, call: Callable[[], tuple[Any, BaseException | None]]
) -> None:
    """A refusal has the stable code and writes nothing."""
    before = env.counts()
    _, error = call()
    check(
        f"{description}: code {code!r}, got {code_of(error)!r} ({error!r})",
        code_of(error) == code,
    )
    check(f"{description}: nothing was written", env.counts() == before)


def raw_error(env: Env, ctx: TenantContext, sql: str, **params: Any) -> BaseException | None:
    """Runs raw SQL as sovra_app in its own transaction; returns the DB error or None."""
    try:
        with tenant_scoped_session_for(env.factory, ctx) as session:
            session.execute(text(sql), params)
        return None
    except DBAPIError as exc:
        return exc


def refused_by(error: BaseException | None, *needles: str) -> bool:
    return error is not None and all(n in str(error) for n in needles)


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0519-P1 split manifest real proof: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0519-P1 split manifest real proof: all checks passed")
    sys.exit(0)


# ----------------------------------------------------------------------------------------
# catalog
# ----------------------------------------------------------------------------------------

TABLES = (
    "dataset_split_manifests",
    "dataset_split_manifest_roles",
    "dataset_split_manifest_sources",
    "dataset_split_assignments",
)


def check_catalog(env: Env) -> None:
    with env.owner.connect() as conn:
        for table in TABLES:
            enabled, forced = conn.execute(
                text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=:t"),
                {"t": table},
            ).one()
            check(f"{table} has ENABLE and FORCE row-level security", bool(enabled and forced))
            policies = conn.execute(
                text(
                    "SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy "
                    "WHERE polrelid = CAST(:t AS regclass)"
                ),
                {"t": table},
            ).all()
            check(
                f"{table} has one tenant_isolation policy that is not allow-all",
                len(policies) == 1
                and policies[0][0] == "tenant_isolation"
                and "app.current_organization_id" in str(policies[0][1])
                and str(policies[0][1]).strip().lower() != "true",
            )
            for privilege, expected in (("SELECT", True), ("INSERT", True), ("DELETE", False)):
                granted = conn.execute(
                    text("SELECT has_table_privilege('sovra_app', :t, :p)"),
                    {"t": table, "p": privilege},
                ).scalar()
                check(f"sovra_app {privilege} on {table} is {expected}", bool(granted) is expected)
            check(
                f"sovra_app has no table-level UPDATE on {table}",
                not conn.execute(
                    text("SELECT has_table_privilege('sovra_app', :t, 'UPDATE')"), {"t": table}
                ).scalar(),
            )
        for table in TABLES[1:]:
            check(
                f"sovra_app has no UPDATE on any column of {table}",
                conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.column_privileges "
                        "WHERE grantee='sovra_app' AND privilege_type='UPDATE' "
                        "AND table_name=:t"
                    ),
                    {"t": table},
                ).scalar()
                == 0,
            )
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE grantee='sovra_app' AND table_name='dataset_split_manifests' "
                    "AND privilege_type='UPDATE'"
                )
            )
        }
        check(
            f"sovra_app may UPDATE only the lock columns of a manifest: {sorted(columns)}",
            columns
            == {
                "status",
                "leakage_reviews",
                "reviewed_leakage_report_sha256",
                "frozen_manifest_sha256",
                "approved_by",
                "locked_at",
                "updated_at",
            },
        )
        triggers = {
            row[0]
            for row in conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
        }
        for trigger in (
            "tr_dataset_split_manifests_insert_guard",
            "tr_dataset_split_manifests_update_guard",
            "tr_dataset_split_manifest_roles_insert_guard",
            "tr_dataset_split_manifest_sources_insert_guard",
            "tr_dataset_split_assignments_insert_guard",
        ):
            check(f"trigger {trigger} exists", trigger in triggers)


# ----------------------------------------------------------------------------------------
# golden path, refusals, approval, training access (one world)
# ----------------------------------------------------------------------------------------


def verify_frozen_shape(env: Env, manifest: Any, datasets: dict[str, Any]) -> None:
    """The stored rows of a proposal against values recomputed here, independently of the store."""
    mid = manifest.id
    total = 0
    for role in ROLES:
        row = env.rows(
            "SELECT purpose, record_count, group_count, role_sha256 "
            "FROM dataset_split_manifest_roles WHERE manifest_id=:m AND role=:r",
            m=mid,
            r=role,
        )
        check(f"role row for {role} exists exactly once", len(row) == 1)
        if len(row) != 1:
            continue
        assigned = env.rows(
            "SELECT record_id, record_sha256, normalized_sha256, group_id "
            "FROM dataset_split_assignments WHERE manifest_id=:m AND role=:r",
            m=mid,
            r=role,
        )
        total += len(assigned)
        purpose, record_count, group_count, role_sha = row[0]
        check(f"{role}: purpose follows the fixed map", purpose == ism.ROLE_PURPOSE[role])
        check(
            f"{role}: record_count {record_count} == {len(assigned)}", record_count == len(assigned)
        )
        check(
            f"{role}: group_count {group_count} == {len({a[3] for a in assigned})}",
            group_count == len({a[3] for a in assigned}),
        )
        check(
            f"{role}: role_sha256 equals the independently recomputed hash",
            role_sha == _independent_role_hash([tuple(a) for a in assigned]),
        )
        check(f"{role}: has at least one record", record_count >= 1)
    expected_total = sum(len(env.records[d.id]) for d in datasets.values())
    check(f"every record is assigned once: {total} == {expected_total}", total == expected_total)
    crossing = env.scalar(
        "SELECT count(*) FROM (SELECT group_id FROM dataset_split_assignments "
        "WHERE manifest_id=:m GROUP BY group_id HAVING count(DISTINCT role) > 1) t",
        m=mid,
    )
    check("no group crosses a role", crossing == 0)
    for key, role in (("held", "final_qualification"), ("dev", "development_eval")):
        dataset = datasets[key]
        placed = env.rows(
            "SELECT DISTINCT role FROM dataset_split_assignments "
            "WHERE manifest_id=:m AND source_dataset_id=:d",
            m=mid,
            d=dataset.id,
        )
        check(
            f"the {dataset.purpose} source is pinned to {role} by the store: {placed}",
            [r[0] for r in placed] == [role],
        )
    for key in ("train", "val"):
        placed_roles = {
            r[0]
            for r in env.rows(
                "SELECT DISTINCT role FROM dataset_split_assignments "
                "WHERE manifest_id=:m AND source_dataset_id=:d",
                m=mid,
                d=datasets[key].id,
            )
        }
        check(
            f"free source {key} lands only in the four roles: {placed_roles}",
            placed_roles <= set(ROLES),
        )


def section_golden(env: Env) -> None:
    w = env.world("golden")
    a = env.dataset(w, "train-a", "training", 200)
    a2 = env.dataset(w, "val-b", "validation", 40)
    d = env.dataset(w, "dev-set", "candidate_development", 8)
    h = env.dataset(w, "held-out", "held_out_evaluation", 8)
    named = {"train": a, "val": a2, "dev": d, "held": h}

    before = env.counts()
    m1, error = env.propose(w, "campaign", [a, a2, d, h])
    check(f"golden propose succeeds: {error!r}", error is None and m1 is not None)
    if error is not None:
        return
    after = env.counts()
    check(
        "propose writes one manifest, four roles, four sources, 256 assignments, one audit row: "
        f"{before} -> {after}",
        after[0] == before[0] + 1
        and after[1] == before[1] + 4
        and after[2] == before[2] + 4
        and after[3] == before[3] + 256
        and after[4] == before[4] + 1,
    )
    check(
        "version 1, no parent, proposed, the allocation strategy, the stated ratios",
        m1.version == 1
        and m1.parent_version is None
        and m1.status == "proposed"
        and m1.strategy == "sha256_group_hash_v1"
        and list(m1.ratios) == [70, 10, 10, 10]
        and m1.seed == "seed-0519"
        and m1.normalization_version == "text-normalization-v1"
        and m1.tokenizer_identity is None,
    )
    check(
        f"the leakage scan ran on this exact manifest and is clear: {m1.leakage_status}",
        m1.leakage_status == "clear"
        and m1.leakage_report["methods_not_run"] == []
        and m1.leakage_report["findings"] == [],
    )
    verify_frozen_shape(env, m1, named)

    sources = env.rows(
        "SELECT dataset_id, dataset_purpose, allowed_purposes_snapshot, quality_report_id, "
        "quality_report_sha256, excluded_positions_sha256, dataset_source_connection_id "
        "FROM dataset_split_manifest_sources WHERE manifest_id=:m",
        m=m1.id,
    )
    check("four source grants are frozen", len(sources) == 4)
    empty_excluded = _sha(
        json.dumps({"excluded_batch_positions": []}, sort_keys=True, separators=(",", ":"))
    )
    for dataset_id, purpose, snapshot, report_id, report_sha, excluded_sha, conn_id in sources:
        latest = env.rows(
            "SELECT id, report_sha256 FROM dataset_quality_reports WHERE dataset_id=:d "
            "ORDER BY report_seq DESC LIMIT 1",
            d=dataset_id,
        )[0]
        check(
            f"source {purpose}: grant snapshot, quality report and exclusion hash are frozen",
            sorted(snapshot) == sorted(ALL_PURPOSES)
            and (report_id, report_sha) == (latest[0], latest[1])
            and excluded_sha == empty_excluded
            and conn_id == w.conns["all"],
        )
    audit = env.rows(
        "SELECT details_json::text, action FROM audit_events WHERE resource_id=:r",
        r=str(m1.id),
    )
    check(
        "exactly one content-free audit row for the proposal",
        len(audit) == 1
        and audit[0][1] == "dataset_split_manifest.proposed"
        and str(a.id) not in audit[0][0]
        and "record_id" not in audit[0][0],
    )
    leak_text = env.scalar(
        "SELECT leakage_report::text FROM dataset_split_manifests WHERE id=:m", m=m1.id
    )
    sample = env.records[a.id][0].content["messages"][0]["content"][0]["text"]  # type: ignore[index]
    check("the stored leakage report holds no record content", sample not in str(leak_text))

    # An unchanged re-proposal returns the same row and writes nothing (nothing is locked yet).
    again_before = env.counts()
    again, error = env.propose(w, "campaign", [a, a2, d, h])
    check(
        f"re-proposing an unchanged manifest returns version 1 untouched: {error!r}",
        error is None and again.id == m1.id and env.counts() == again_before,
    )

    refusals(env, w, a, a2, d, h, m1)
    approvals_and_lock(env, w, m1, named)
    if env.manifest_row(m1.id)[0] == "locked":
        section_versions(env, w, m1, named)


def refusals(env: Env, w: World, a: Any, a2: Any, d: Any, h: Any, m1: Any) -> None:
    dirty = env.dataset(w, "dirty", "training", 10, report=False)
    env.quality(w, dirty, clean=False)
    expect_refusal(
        env,
        "a source with an unresolved blocking quality issue",
        "quality_not_clear",
        lambda: env.propose(w, "c-dirty", [dirty, d, h]),
    )
    norep = env.dataset(w, "no-report", "training", 10, report=False)
    expect_refusal(
        env,
        "a source with no quality report at all",
        "quality_not_clear",
        lambda: env.propose(w, "c-norep", [norep, d, h]),
    )
    narrow = env.dataset(w, "train-only", "training", 60, connection="train")
    expect_refusal(
        env,
        "records placed in roles whose purpose the source connection does not grant",
        "purpose_not_authorized",
        lambda: env.propose(w, "c-narrow", [narrow, d, h]),
    )
    baseline = env.dataset(w, "baseline", "baseline_only", 5, connection="baseline")
    expect_refusal(
        env,
        "a baseline_only source is not a split purpose",
        "source_not_splittable",
        lambda: env.propose(w, "c-baseline", [a2, baseline]),
    )
    expect_refusal(
        env,
        "an actor that may not see held-out data",
        "actor_not_permitted",
        lambda: env.propose(w, "c-ml", [a2, d, h], actor=env.ml),
    )

    legacy = ism.LegacyThreeSplitManifest(
        seed="s",
        assignments=((f"{a.id}:0", "train", "g"),),
        legacy_sha256=ism.compute_legacy_sha256("s", ((f"{a.id}:0", "train", "g"),)),
    )
    for label, spec in (
        ("a LegacyThreeSplitManifest", legacy),
        ("three ratios", (60, 20, 20)),
        ("the legacy strategy name", "legacy_three_split_v1"),
        ("a heldout_test role name", "heldout_test"),
    ):
        expect_refusal(
            env,
            f"propose refuses {label}: no implicit three-split mapping",
            "legacy_manifest_requires_migration",
            lambda spec=spec: env.propose(w, "c-legacy", [a2, d, h], spec=spec),
        )
    for label, call in (
        ("a spec that is not a SplitSpec", lambda: env.propose(w, "c-x", [a2], spec={"a": 1})),
        ("no sources", lambda: env.propose(w, "c-x", [], sources=[])),
        ("a blank-padded name", lambda: env.propose(w, " padded ", [a2, d, h])),
        ("an empty normalization version", lambda: env.propose(w, "c-x", [a2], normalization="")),
        (
            "the same dataset twice",
            lambda: env.propose(
                w, "c-x", [], sources=[env.source(a2), store.SplitSourceInput(a2.id, ())]
            ),
        ),
        (
            "a duplicated batch position",
            lambda: env.propose(
                w,
                "c-x",
                [],
                sources=[
                    store.SplitSourceInput(a2.id, (env.records[a2.id][0], env.records[a2.id][0]))
                ],
            ),
        ),
    ):
        expect_refusal(env, f"invalid request: {label}", "invalid_request", call)

    expect_refusal(
        env,
        "a dataset of another organization",
        "dataset_not_found",
        lambda: env.propose(w, "c-foreign", [], ctx=env.ctx2, sources=[env.source(a2)]),
    )
    expect_refusal(
        env,
        "a dataset id that does not exist",
        "dataset_not_found",
        lambda: env.propose(
            w, "c-ghost", [], sources=[store.SplitSourceInput(uuid.uuid4(), env.records[a2.id])]
        ),
    )

    # Over the size limit is refused, never truncated; at the limit it is accepted.
    original = store.MAX_MANIFEST_RECORDS
    store.MAX_MANIFEST_RECORDS = 50
    try:
        expect_refusal(
            env,
            "56 records over a limit of 50 are refused, never truncated",
            "manifest_too_large",
            lambda: env.propose(w, "c-big", [a2, d, h]),
        )
        small, error = env.propose(w, "c-small", [a2])
        check(
            f"40 records under the same limit are accepted (positive control): {error!r}",
            error is None and small is not None,
        )
    finally:
        store.MAX_MANIFEST_RECORDS = original

    # Excluded positions are dropped and frozen; a source with nothing left is refused.
    only_dup = env.dataset(w, "all-excluded", "training", 3, report=False)
    env.exclude(w, only_dup, (0, 1, 2))
    expect_refusal(
        env,
        "a source whose every record is excluded",
        "no_records",
        lambda: env.propose(w, "c-none", [only_dup, d, h]),
    )
    x = env.dataset(w, "excluding", "training", 30, report=False)
    env.exclude(w, x, (0, 1, 2))
    mx, error = env.propose(w, "c-excl", [x, d, h])
    check(f"a partly excluded source is accepted: {error!r}", error is None and mx is not None)
    if error is None:
        left = env.scalar(
            "SELECT count(*) FROM dataset_split_assignments "
            "WHERE manifest_id=:m AND source_dataset_id=:d",
            m=mx.id,
            d=x.id,
        )
        dropped = env.scalar(
            "SELECT count(*) FROM dataset_split_assignments "
            "WHERE manifest_id=:m AND source_dataset_id=:d AND batch_position IN (0, 1, 2)",
            m=mx.id,
            d=x.id,
        )
        excluded_sha = env.scalar(
            "SELECT excluded_positions_sha256 FROM dataset_split_manifest_sources "
            "WHERE manifest_id=:m AND dataset_id=:d",
            m=mx.id,
            d=x.id,
        )
        check("excluded positions never reach the manifest", left == 27 and dropped == 0)
        check(
            "the exclusion set is frozen as the hash of the sorted positions",
            excluded_sha
            == _sha(
                json.dumps(
                    {"excluded_batch_positions": [0, 1, 2]}, sort_keys=True, separators=(",", ":")
                )
            ),
        )
        not_locked, error = env.training(w, mx.id)
        check(
            f"a proposed manifest serves no training rows: {code_of(error)!r}",
            not_locked is None and code_of(error) == "manifest_not_locked",
        )
    _ = m1


def approvals_and_lock(env: Env, w: World, m1: Any, named: dict[str, Any]) -> None:
    expect_refusal(
        env,
        "approval by an actor that may not see held-out data",
        "actor_not_permitted",
        lambda: env.approve(w, m1, actor=env.ml),
    )
    expect_refusal(
        env,
        "approval against a stale proposal hash",
        "manifest_changed",
        lambda: env.approve(w, m1, expected="0" * 64),
    )
    expect_refusal(
        env,
        "approval of a manifest of another organization",
        "manifest_not_found",
        lambda: env.approve(w, m1, ctx=env.ctx2),
    )
    expect_refusal(
        env,
        "approval of an id that does not exist",
        "manifest_not_found",
        lambda: env.approve(w, m1, manifest_id=uuid.uuid4()),
    )
    expect_refusal(
        env,
        "a review of a finding that does not exist",
        "invalid_review",
        lambda: env.approve(w, m1, reviews=(store.ReviewInput("f" * 64, "no such finding"),)),
    )
    expect_refusal(
        env,
        "a review with a blank rationale",
        "invalid_review",
        lambda: env.approve(w, m1, reviews=(store.ReviewInput("f" * 64, " "),)),
    )

    throwaway, error = env.propose(w, "c-reject", [named["val"], named["dev"], named["held"]])
    check(f"a throwaway proposal for the rejection path: {error!r}", error is None)
    if error is None:
        rejected, error = env.run(
            w.ctx,
            lambda s: store.reject_split_manifest(
                s, w.ctx, project_id=w.project_id, manifest_id=throwaway.id, actor=env.admin
            ),
        )
        check(
            f"reject moves proposed -> rejected: {error!r}",
            error is None
            and rejected.status == "rejected"
            and rejected.frozen_manifest_sha256 is None,
        )
        expect_refusal(
            env,
            "approving a rejected manifest",
            "manifest_not_proposed",
            lambda: env.approve(w, throwaway),
        )
        expect_refusal(
            env,
            "rejecting a rejected manifest",
            "manifest_not_proposed",
            lambda: env.run(
                w.ctx,
                lambda s: store.reject_split_manifest(
                    s, w.ctx, project_id=w.project_id, manifest_id=throwaway.id, actor=env.admin
                ),
            ),
        )
        expect_refusal(
            env,
            "rejection by an actor that may not see held-out data",
            "actor_not_permitted",
            lambda: env.run(
                w.ctx,
                lambda s: store.reject_split_manifest(
                    s, w.ctx, project_id=w.project_id, manifest_id=m1.id, actor=env.ml
                ),
            ),
        )

    locked, error = env.approve(w, m1)
    check(f"approval of a clear manifest locks it: {error!r}", error is None and locked is not None)
    if error is not None:
        return
    row = env.manifest_row(m1.id)
    (status, version, parent, strategy, leak, approved_by, frozen, locked_at, reviewed, base) = row[
        :10
    ]
    check(
        "locked with the authenticated approver, a frozen hash, a lock time and no reviews",
        status == "locked"
        and approved_by == env.admin.actor_id
        and frozen is not None
        and len(frozen) == 64
        and locked_at is not None
        and row[11] == []
        and reviewed == base,
    )
    audit = env.rows(
        "SELECT details_json::text FROM audit_events WHERE resource_id=:r AND action=:a",
        r=str(m1.id),
        a="dataset_split_manifest.locked",
    )
    check(
        "one content-free audit row for the lock",
        len(audit) == 1 and frozen in audit[0][0] and "record_id" not in audit[0][0],
    )
    expect_refusal(
        env,
        "approving a locked manifest again",
        "manifest_not_proposed",
        lambda: env.approve(w, m1),
    )

    # The frozen hash covers the content: recompute it from the stored rows, then change one field
    # of the content at a time and watch it move.
    summary, error = env.run(
        w.ctx,
        lambda s: store.get_split_manifest(s, w.ctx, project_id=w.project_id, manifest_id=m1.id),
    )
    check(f"get_split_manifest reads the summary: {error!r}", error is None)
    if error is None:
        content = store._content_from_rows(summary.manifest, summary.roles, summary.sources)
        check(
            "the frozen hash is the hash of the stored content",
            ism.compute_frozen_manifest_sha256(content) == frozen,
        )
        check(
            "get_split_manifest returns counts and hashes, never an assignment",
            not hasattr(summary, "assignments")
            and all(not hasattr(r, "record_id") for r in summary.roles),
        )

    for table in TABLES:
        seen, error = env.run(
            env.ctx2, lambda s, t=table: s.execute(text(f"SELECT count(*) FROM {t}")).scalar()
        )
        check(f"org2 sees no {table} row of org1: {seen!r} {error!r}", error is None and seen == 0)
    _, error = env.run(
        env.ctx2,
        lambda s: store.get_split_manifest(s, env.ctx2, project_id=w.project_id, manifest_id=m1.id),
    )
    check(
        "reading org1's manifest as org2 is manifest_not_found",
        code_of(error) == "manifest_not_found",
    )

    training_access(env, w, m1, named)


def training_access(env: Env, w: World, m1: Any, named: dict[str, Any]) -> None:
    view, error = env.training(w, m1.id)
    check(f"the locked manifest serves a training view: {error!r}", error is None)
    if error is not None:
        return
    served = {r.record_id for r in view.rows}
    expected = env.record_ids(m1.id, "train", "validation")
    held_dev = env.record_ids(m1.id, "development_eval", "final_qualification")
    check(
        "the view is exactly the train and validation rows",
        served == expected and len(served) == len(view.rows) and len(served) > 0,
    )
    check("no development or final record id is in the training view", not (served & held_dev))
    check(
        "every served row is a train or validation row of an allowed source",
        all(r.role in ism.TRAINING_ROLES for r in view.rows),
    )
    check(
        "no held-out or development source record is in the training view",
        not any(
            r.source_dataset_id in (str(named["held"].id), str(named["dev"].id)) for r in view.rows
        ),
    )
    check(
        "the view carries the frozen hash",
        view.frozen_manifest_sha256 == env.manifest_row(m1.id)[6],
    )
    dev, error = env.development(w, m1.id)
    check(
        f"the development view is exactly the development_eval rows: {error!r}",
        error is None
        and {r.record_id for r in dev.rows} == env.record_ids(m1.id, "development_eval"),
    )
    check(
        "there is no reader of final-qualification records in the store",
        not [n for n in dir(store) if "final" in n.lower() and callable(getattr(store, n))],
    )

    conn = w.conns["all"]
    # A revoked connection stops training access; restoring it serves again (positive control).
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='revoked' WHERE id=:c", c=conn
    )
    _, error = env.training(w, m1.id)
    check(f"a revoked connection: {code_of(error)!r}", code_of(error) == "grant_changed")
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='active' WHERE id=:c", c=conn
    )
    _, error = env.training(w, m1.id)
    check(f"the restored connection serves again: {error!r}", error is None)
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = "
        "CAST(:p AS jsonb) WHERE id=:c",
        p=json.dumps(["validation", "candidate_development", "held_out_evaluation"]),
        c=conn,
    )
    _, error = env.training(w, m1.id)
    check(
        f"a narrowed grant (training removed): {code_of(error)!r}",
        code_of(error) == "grant_changed",
    )
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = CAST(:p AS jsonb) "
        "WHERE id=:c",
        p=json.dumps(ALL_PURPOSES),
        c=conn,
    )
    _, error = env.training(w, m1.id)
    check(f"the restored grant serves again: {error!r}", error is None)

    # Tampering with stored rows is detected before anything is served.
    victim = env.rows(
        "SELECT record_id FROM dataset_split_assignments "
        "WHERE manifest_id=:m AND role='train' LIMIT 1",
        m=m1.id,
    )[0][0]
    good_hash = env.scalar(
        "SELECT role_sha256 FROM dataset_split_manifest_roles WHERE manifest_id=:m AND role='train'",
        m=m1.id,
    )
    env.execute(
        "UPDATE dataset_split_manifest_roles SET role_sha256=:x WHERE manifest_id=:m AND role='train'",
        x="0" * 64,
        m=m1.id,
    )
    _, error = env.training(w, m1.id)
    check(f"an edited role hash: {code_of(error)!r}", code_of(error) == "manifest_tampered")
    env.execute(
        "UPDATE dataset_split_manifest_roles SET role_sha256=:x WHERE manifest_id=:m AND role='train'",
        x=good_hash,
        m=m1.id,
    )
    env.execute(
        "UPDATE dataset_split_assignments SET role='final_qualification' "
        "WHERE manifest_id=:m AND record_id=:r",
        m=m1.id,
        r=victim,
    )
    _, error = env.training(w, m1.id)
    check(
        f"a train record moved to final: {code_of(error)!r}", code_of(error) == "manifest_tampered"
    )
    env.execute(
        "UPDATE dataset_split_assignments SET role='train' WHERE manifest_id=:m AND record_id=:r",
        m=m1.id,
        r=victim,
    )
    env.execute(
        "UPDATE dataset_split_assignments SET record_sha256=:x WHERE manifest_id=:m AND record_id=:r",
        x="1" * 64,
        m=m1.id,
        r=victim,
    )
    _, error = env.training(w, m1.id)
    check(f"an edited record hash: {code_of(error)!r}", code_of(error) == "manifest_tampered")
    original_hash = _sha(f"raw-{victim.split(':')[0]}-{victim.split(':')[1]}")
    env.execute(
        "UPDATE dataset_split_assignments SET record_sha256=:x WHERE manifest_id=:m AND record_id=:r",
        x=original_hash,
        m=m1.id,
        r=victim,
    )
    ok, error = env.training(w, m1.id)
    check(f"after restoring every edit the manifest serves again: {error!r}", error is None)

    trigger = "tr_dataset_split_manifests_update_guard"
    env.execute(f"ALTER TABLE dataset_split_manifests DISABLE TRIGGER {trigger}")
    try:
        for label, sql in (
            (
                "an edited leakage report",
                "UPDATE dataset_split_manifests SET leakage_report = "
                "jsonb_set(leakage_report, '{methods_not_run}', '[\"embedding\"]') WHERE id=:m",
            ),
            (
                "an edited approver",
                "UPDATE dataset_split_manifests SET approved_by='someone-else' WHERE id=:m",
            ),
            (
                "an edited seed",
                "UPDATE dataset_split_manifests SET seed='another-seed' WHERE id=:m",
            ),
        ):
            snapshot = env.rows(
                "SELECT leakage_report, approved_by, seed FROM dataset_split_manifests WHERE id=:m",
                m=m1.id,
            )[0]
            env.execute(sql, m=m1.id)
            _, error = env.training(w, m1.id)
            check(f"{label}: {code_of(error)!r}", code_of(error) == "manifest_tampered")
            env.execute(
                "UPDATE dataset_split_manifests SET leakage_report=CAST(:l AS jsonb), "
                "approved_by=:a, seed=:s WHERE id=:m",
                l=json.dumps(snapshot[0]),
                a=snapshot[1],
                s=snapshot[2],
                m=m1.id,
            )
    finally:
        env.execute(f"ALTER TABLE dataset_split_manifests ENABLE TRIGGER {trigger}")
    _, error = env.training(w, m1.id)
    check(f"the restored manifest serves again: {error!r}", error is None)
    _ = ok


# ----------------------------------------------------------------------------------------
# version on change and prior campaigns (golden world, manifest 1 locked)
# ----------------------------------------------------------------------------------------


def section_versions(env: Env, w: World, m1: Any, named: dict[str, Any]) -> None:
    a, a2, d, h = named["train"], named["val"], named["dev"], named["held"]
    datasets = [a, a2, d, h]
    before_v1 = env.fingerprint(m1.id)
    prior = env.prior_for(m1)
    seed_b = SplitSpec("sha256_group_hash_v1", "seed-0519-b", (70, 10, 10, 10))

    # The project now has a locked manifest: every later proposal must carry its suites exactly.
    expect_refusal(
        env,
        "no prior campaign supplied although a manifest is locked",
        "prior_sources_incomplete",
        lambda: env.propose(w, "campaign", datasets, spec=seed_b),
    )
    expect_refusal(
        env,
        "only the development suite of the locked manifest",
        "prior_sources_incomplete",
        lambda: env.propose(w, "campaign", datasets, spec=seed_b, prior=prior[:1]),
    )
    first = prior[1].records[0]
    forged = PriorQualificationSource(
        label=prior[1].label,
        role=prior[1].role,
        manifest_sha256=prior[1].manifest_sha256,
        records=(replace(first, normalized_sha256="9" * 64), *prior[1].records[1:]),
    )
    expect_refusal(
        env,
        "a prior suite whose record hash differs from the stored one",
        "prior_sources_incomplete",
        lambda: env.propose(w, "campaign", datasets, spec=seed_b, prior=(prior[0], forged)),
    )
    unknown = PriorQualificationSource(
        label="other@v1:final_qualification",
        role="final_qualification",
        manifest_sha256="8" * 64,
        records=prior[1].records,
    )
    expect_refusal(
        env,
        "an extra prior suite that no locked manifest owns",
        "prior_sources_incomplete",
        lambda: env.propose(w, "campaign", datasets, spec=seed_b, prior=(*prior, unknown)),
    )
    relabelled = PriorQualificationSource(
        label="made-up-label",
        role=prior[1].role,
        manifest_sha256=prior[1].manifest_sha256,
        records=prior[1].records,
    )
    expect_refusal(
        env,
        "a prior suite with a label the store did not derive",
        "prior_sources_incomplete",
        lambda: env.propose(w, "campaign", datasets, spec=seed_b, prior=(prior[0], relabelled)),
    )

    # A changed seed is version 2 of the same manifest; version 1 is byte-identical afterwards.
    v2, error = env.propose(w, "campaign", datasets, spec=seed_b, prior=prior)
    check(f"a changed seed proposes version 2: {error!r}", error is None and v2 is not None)
    if error is not None:
        return
    check(
        "version 2 has parent version 1 and is proposed",
        v2.version == 2 and v2.parent_version == 1 and v2.status == "proposed" and v2.id != m1.id,
    )
    check(
        "version 1 is byte-identical after version 2 was written",
        env.fingerprint(m1.id) == before_v1,
    )
    findings = v2.leakage_report["findings"]
    check(
        "the reused final suite is a blocking consumed_final_reuse finding against version 1",
        v2.leakage_status == "leakage_found"
        and any(
            f["reason"] == "consumed_final_reuse" and f["disposition"] == "blocking"
            for f in findings
        ),
    )
    expect_refusal(
        env,
        "a blocking finding cannot be reviewed away or ignored: version 2 cannot lock",
        "leakage_not_clear",
        lambda: env.approve(w, v2),
    )
    blocking_id = next(f["finding_id"] for f in findings if f["disposition"] == "blocking")
    expect_refusal(
        env,
        "reviewing a blocking finding is refused",
        "invalid_review",
        lambda: env.approve(w, v2, reviews=(store.ReviewInput(blocking_id, "looks fine to me"),)),
    )

    # A new dataset version of a source is a change.
    a_v2 = env.dataset(w, "train-a", "training", 200)
    v3, error = env.propose(w, "campaign", [a_v2, a2, d, h], prior=prior)
    check(f"a new source version proposes version 3: {error!r}", error is None and v3 is not None)
    if error is None:
        versions = {
            r[0]
            for r in env.rows(
                "SELECT dataset_version FROM dataset_split_manifest_sources WHERE manifest_id=:m",
                m=v3.id,
            )
        }
        check(
            "version 3 parent is 2 and the frozen source version is the new one",
            v3.version == 3 and v3.parent_version == 2 and 2 in versions,
        )

    # A changed grant is a change.
    conn = w.conns["all"]
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = CAST(:p AS jsonb) "
        "WHERE id=:c",
        p=json.dumps([*ALL_PURPOSES, "baseline_only"]),
        c=conn,
    )
    v4, error = env.propose(w, "campaign", [a_v2, a2, d, h], prior=prior)
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = CAST(:p AS jsonb) "
        "WHERE id=:c",
        p=json.dumps(ALL_PURPOSES),
        c=conn,
    )
    check(f"a changed grant proposes version 4: {error!r}", error is None and v4 is not None)
    if error is None:
        snapshots = {
            tuple(r[0])
            for r in env.rows(
                "SELECT allowed_purposes_snapshot FROM dataset_split_manifest_sources "
                "WHERE manifest_id=:m",
                m=v4.id,
            )
        }
        check(
            "version 4 froze the widened grant snapshot",
            v4.version == 4
            and v4.parent_version == 3
            and snapshots == {tuple(sorted([*ALL_PURPOSES, "baseline_only"]))},
        )

    # A changed quality report is a change -- and it stops training on version 1.
    report_before = env.scalar(
        "SELECT quality_report_id FROM dataset_split_manifest_sources "
        "WHERE manifest_id=:m AND dataset_id=:d",
        m=m1.id,
        d=a2.id,
    )
    new_report = env.quality(w, a2, extra=(_extra("seeded_note", (1,), severity="warning"),))
    check("a changed finding set is a new quality report", new_report.id != report_before)
    v5, error = env.propose(w, "campaign", [a_v2, a2, d, h], prior=prior)
    check(
        f"a changed quality report proposes version 5: {error!r}", error is None and v5 is not None
    )
    if error is None:
        frozen_report = env.scalar(
            "SELECT quality_report_id FROM dataset_split_manifest_sources "
            "WHERE manifest_id=:m AND dataset_id=:d",
            m=v5.id,
            d=a2.id,
        )
        check(
            "version 5 froze the new quality report; version 1 still holds the old one",
            v5.version == 5 and v5.parent_version == 4 and frozen_report == new_report.id,
        )
    _, error = env.training(w, m1.id)
    check(
        f"a changed quality report on a source stops training on version 1: {code_of(error)!r}",
        code_of(error) == "quality_report_changed",
    )
    chain = env.rows(
        "SELECT version, parent_version FROM dataset_split_manifests "
        "WHERE project_id=:p AND name='campaign' ORDER BY version",
        p=w.project_id,
    )
    check(
        f"the version chain is 1..5 with each parent the previous: {chain}",
        chain == [(1, None), (2, 1), (3, 2), (4, 3), (5, 4)],
    )
    check(
        "version 1 is still byte-identical after five versions", env.fingerprint(m1.id) == before_v1
    )


# ----------------------------------------------------------------------------------------
# a resolved blocking issue is clear for split; deleting its resolution stops training
# ----------------------------------------------------------------------------------------


def section_quality(env: Env) -> None:
    w = env.world("quality")
    x = env.dataset(w, "pii-excluded", "training", 120, report=False)
    env.exclude(w, x, (2,), clean=False, extra=False, pick="pii_secret_email")
    d = env.dataset(w, "q-dev", "candidate_development", 6)
    h = env.dataset(w, "q-held", "held_out_evaluation", 6)
    m, error = env.propose(w, "quality-campaign", [x, d, h])
    check(
        f"a source whose blocking issue is resolved by exclusion is clear for split: {error!r}",
        error is None and m is not None,
    )
    if error is not None:
        return
    gone = env.scalar(
        "SELECT count(*) FROM dataset_split_assignments "
        "WHERE manifest_id=:m AND source_dataset_id=:d AND batch_position=2",
        m=m.id,
        d=x.id,
    )
    check("the excluded position never entered the manifest", gone == 0)
    locked, error = env.approve(w, m)
    check(f"the manifest locks: {error!r}", error is None and locked is not None)
    view, error = env.training(w, m.id)
    check(f"and serves training rows: {error!r}", error is None and len(view.rows) > 0)
    env.execute("DELETE FROM dataset_issue_resolutions WHERE dataset_id=:d", d=x.id)
    _, error = env.training(w, m.id)
    check(
        f"an unresolved blocking issue returning stops training: {code_of(error)!r}",
        code_of(error) == "quality_not_clear",
    )


# ----------------------------------------------------------------------------------------
# leakage review gate
# ----------------------------------------------------------------------------------------


def _variant(record: store.SourceRecord, tag: str) -> store.SourceRecord:
    text_value = record.content["messages"][0]["content"][0]["text"]  # type: ignore[index]
    changed = text_value.rsplit(" ", 1)[0] + f" {tag}"
    return replace(
        record,
        content={
            "messages": [{"role": "user", "content": [{"type": "text", "text": changed}]}],
            "metadata": {},
        },
        record_sha256=_sha(f"raw-variant-{tag}"),
        normalized_sha256=_sha(f"norm-variant-{tag}"),
    )


def section_review(env: Env) -> None:
    w = env.world("review")
    a = env.dataset(w, "r-train", "training", 100)

    # A blocking finding: the same raw hash in development and final.
    d_b = env.dataset(w, "r-dev-block", "candidate_development", 6)
    h_b = env.dataset(w, "r-held-block", "held_out_evaluation", 6)
    env.records[h_b.id] = (
        replace(
            env.records[d_b.id][0],
            batch_position=0,
        ),
        *env.records[h_b.id][1:],
    )
    blocking, error = env.propose(w, "c-block", [a, d_b, h_b])
    check(
        f"an exact cross-role duplicate is proposed: {error!r}",
        error is None and blocking is not None,
    )
    if error is None:
        found = blocking.leakage_report["findings"]
        check(
            "the exact duplicate is a blocking finding",
            blocking.leakage_status == "leakage_found"
            and any(f["disposition"] == "blocking" for f in found),
        )
        expect_refusal(
            env,
            "unresolved blocking leakage cannot be frozen",
            "leakage_not_clear",
            lambda: env.approve(w, blocking),
        )
        blocking_id = next(f["finding_id"] for f in found if f["disposition"] == "blocking")
        expect_refusal(
            env,
            "a blocking finding cannot be reviewed away",
            "invalid_review",
            lambda: env.approve(
                w, blocking, reviews=(store.ReviewInput(blocking_id, "not the same record"),)
            ),
        )

    # A blocked scan (zero-egress project, external embedding route) is never clear.
    d_r = env.dataset(w, "r-dev-route", "candidate_development", 6)
    h_r = env.dataset(w, "r-held-route", "held_out_evaluation", 6)
    routed, error = env.propose(w, "c-route", [a, d_r, h_r], route=BLOCKED_ROUTE)
    check(f"a proposal under a blocked embedding route is stored: {error!r}", error is None)
    if error is None:
        check(
            f"its leakage status is blocked, not clear: {routed.leakage_status}",
            routed.leakage_status == "blocked",
        )
        expect_refusal(
            env,
            "a blocked leakage scan cannot be frozen",
            "leakage_not_clear",
            lambda: env.approve(w, routed),
        )

    # A review_required finding: a near duplicate across development and final.
    d_s = env.dataset(w, "r-dev-sem", "candidate_development", 6)
    h_s = env.dataset(w, "r-held-sem", "held_out_evaluation", 6)
    env.records[d_s.id] = (_long_record(str(d_s.id), 0), *env.records[d_s.id][1:])
    env.records[h_s.id] = (_variant(env.records[d_s.id][0], "zzzzzz"), *env.records[h_s.id][1:])
    semantic, error = env.propose(w, "c-sem", [a, d_s, h_s])
    check(f"a near duplicate across development and final is proposed: {error!r}", error is None)
    if error is not None:
        return
    found = semantic.leakage_report["findings"]
    open_ids = [f["finding_id"] for f in found]
    check(
        "the near duplicate is found and none of it is blocking: "
        f"{[(f['method'], f['disposition']) for f in found]}",
        semantic.leakage_status == "leakage_found"
        and len(found) >= 1
        and all(f["disposition"] == "review_required" for f in found),
    )
    expect_refusal(
        env,
        "unreviewed leakage cannot be frozen",
        "leakage_not_clear",
        lambda: env.approve(w, semantic),
    )
    expect_refusal(
        env,
        "a partial review leaves a finding open",
        "leakage_not_clear" if len(open_ids) > 1 else "invalid_review",
        lambda: env.approve(
            w,
            semantic,
            reviews=(store.ReviewInput(open_ids[0], "different tickets"),)
            if len(open_ids) > 1
            else (store.ReviewInput("f" * 64, "different tickets"),),
        ),
    )
    reviews = tuple(store.ReviewInput(fid, "different tickets, same template") for fid in open_ids)
    locked, error = env.approve(w, semantic, reviews=reviews, actor=env.admin2)
    check(
        f"every finding reviewed not_leakage locks the manifest: {error!r}",
        error is None and locked is not None,
    )
    if error is not None:
        return
    row = env.manifest_row(semantic.id)
    check(
        "the stored reviews name the authenticated approver, never an input",
        row[0] == "locked"
        and row[5] == env.admin2.actor_id
        and len(row[11]) == len(open_ids)
        and all(
            r["reviewer_id"] == env.admin2.actor_id and r["decision"] == "not_leakage"
            for r in row[11]
        ),
    )
    check(
        "the reviewed report hash differs from the base report hash",
        row[8] is not None and row[8] != row[9],
    )
    view, error = env.training(w, semantic.id)
    check(
        f"a reviewed manifest serves training rows: {error!r}", error is None and len(view.rows) > 0
    )
    served = {r.record_id for r in view.rows}
    check(
        "and still never a development or final record",
        not (served & env.record_ids(semantic.id, "development_eval", "final_qualification")),
    )


# ----------------------------------------------------------------------------------------
# legacy three-split migration
# ----------------------------------------------------------------------------------------


def section_legacy(env: Env) -> None:
    w = env.world("legacy")
    a = env.dataset(w, "l-train", "training", 30)
    h = env.dataset(w, "l-held", "held_out_evaluation", 6)
    d = env.dataset(w, "l-dev", "candidate_development", 6)

    def rid(dataset: Any, position: int) -> str:
        return f"{dataset.id}:{position}"

    train_ids = [(rid(a, i), "train", f"g{i}") for i in range(14)]
    val_ids = [(rid(a, i), "validation", f"g{i}") for i in range(14, 20)]
    heldout_in_training_dataset = [(rid(a, i), "heldout_test", f"g{i}") for i in range(20, 23)]
    heldout = [(rid(h, i), "heldout_test", f"h{i}") for i in range(6)]
    assignments = tuple(sorted([*train_ids, *val_ids, *heldout_in_training_dataset, *heldout]))
    legacy = ism.LegacyThreeSplitManifest(
        "legacy-seed", assignments, ism.compute_legacy_sha256("legacy-seed", assignments)
    )

    def migrate(
        name: str,
        legacy_manifest: Any,
        datasets: list[Any],
        development: Any = None,
        ctx: TenantContext | None = None,
    ) -> tuple[Any, BaseException | None]:
        use_ctx = ctx or w.ctx
        return env.run(
            use_ctx,
            lambda s: store.migrate_legacy_split_manifest(
                s,
                use_ctx,
                project_id=w.project_id,
                name=name,
                legacy=legacy_manifest,
                sources=[env.source(x) for x in datasets],
                development_dataset_id=development.id if development is not None else None,
                prior_sources=(),
                route=ROUTE,
                normalization_version="text-normalization-v1",
                tokenizer_identity=None,
                actor=env.admin,
            ),
        )

    expect_refusal(
        env,
        "a legacy 'train' record of a held-out dataset keeps no training role",
        "source_role_pinned",
        lambda: migrate(
            "l-pinned",
            ism.LegacyThreeSplitManifest(
                "s",
                ((rid(h, 0), "train", "x"), (rid(a, 0), "heldout_test", "y")),
                ism.compute_legacy_sha256(
                    "s", ((rid(h, 0), "train", "x"), (rid(a, 0), "heldout_test", "y"))
                ),
            ),
            [a, h],
        ),
    )
    expect_refusal(
        env,
        "a legacy record that no source supplies",
        "invalid_request",
        lambda: migrate("l-missing", legacy, [h]),
    )
    insufficient, error = migrate("l-nodev", legacy, [a, h])
    check(f"a legacy manifest without a development dataset is stored: {error!r}", error is None)
    if error is not None:
        return
    row = env.manifest_row(insufficient.id)
    check(
        "stored under the legacy strategy with its lineage hash",
        row[3] == "legacy_three_split_v1"
        and row[12] == legacy.legacy_sha256
        and row[0] == "proposed",
    )
    in_final = env.record_ids(insufficient.id, "final_qualification")
    check(
        "heldout_test lands only in final_qualification, including the ones in a training dataset",
        in_final == {x[0] for x in [*heldout, *heldout_in_training_dataset]},
    )
    check(
        "train and validation keep their roles",
        env.record_ids(insufficient.id, "train") == {x[0] for x in train_ids}
        and env.record_ids(insufficient.id, "validation") == {x[0] for x in val_ids}
        and env.record_ids(insufficient.id, "development_eval") == set(),
    )
    expect_refusal(
        env,
        "without a development role the manifest cannot lock",
        "manifest_insufficient",
        lambda: env.approve(w, insufficient),
    )
    lock_error = raw_error(
        env,
        w.ctx,
        "UPDATE dataset_split_manifests SET status='locked', approved_by='raw', "
        "frozen_manifest_sha256=:h, reviewed_leakage_report_sha256=:h, locked_at=now() "
        "WHERE id=:m",
        h="a" * 64,
        m=insufficient.id,
    )
    check(
        f"the database also refuses to lock it: {lock_error!r}",
        refused_by(lock_error, "four roles"),
    )

    full, error = migrate("l-dev", legacy, [a, h, d], development=d)
    check(
        f"with an explicit candidate_development dataset the migration is complete: {error!r}",
        error is None,
    )
    if error is not None:
        return
    check(
        "the development role holds exactly the development dataset",
        env.record_ids(full.id, "development_eval") == {f"{d.id}:{i}" for i in range(6)},
    )
    locked, error = env.approve(w, full)
    check(f"and it locks: {error!r}", error is None and locked is not None)
    view, error = env.training(w, full.id)
    check(f"the migrated manifest serves training rows: {error!r}", error is None)
    if error is None:
        served = {r.record_id for r in view.rows}
        check(
            "no heldout_test record is ever in the training view",
            not (served & {x[0] for x in [*heldout, *heldout_in_training_dataset]})
            and served == {x[0] for x in [*train_ids, *val_ids]},
        )


# ----------------------------------------------------------------------------------------
# raw sovra_app SQL that bypasses the store
# ----------------------------------------------------------------------------------------

MANIFEST_COLS = (
    "id",
    "organization_id",
    "project_id",
    "name",
    "version",
    "parent_version",
    "strategy",
    "seed",
    "ratios",
    "cutoff",
    "normalization_version",
    "tokenizer_identity",
    "split_manifest_sha256",
    "proposal_sha256",
    "leakage_report",
    "leakage_report_sha256",
    "leakage_status",
    "leakage_reviews",
    "reviewed_leakage_report_sha256",
    "migrated_from_legacy_sha256",
    "status",
    "frozen_manifest_sha256",
    "approved_by",
    "locked_at",
    "created_by",
    "created_at",
    "updated_at",
)


def manifest_values(env: Env, manifest_id: uuid.UUID) -> dict[str, Any]:
    with env.owner.connect() as conn:
        row = (
            conn.execute(
                text(f"SELECT {', '.join(MANIFEST_COLS)} FROM dataset_split_manifests WHERE id=:m"),
                {"m": manifest_id},
            )
            .mappings()
            .one()
        )
    return dict(row)


def raw_manifest_insert(
    env: Env, ctx: TenantContext, template: uuid.UUID, **over: Any
) -> BaseException | None:
    values = manifest_values(env, template)
    values["id"] = uuid.uuid4()
    values.update(over)
    values["leakage_report"] = json.dumps(values["leakage_report"])
    values["leakage_reviews"] = json.dumps(values["leakage_reviews"])
    casts = {"leakage_report": "jsonb", "leakage_reviews": "jsonb", "ratios": "integer[]"}
    placeholders = ", ".join(
        f"CAST(:{c} AS {casts[c]})" if c in casts else f":{c}" for c in MANIFEST_COLS
    )
    return raw_error(
        env,
        ctx,
        f"INSERT INTO dataset_split_manifests ({', '.join(MANIFEST_COLS)}) VALUES ({placeholders})",
        **values,
    )


def raw_assignment(
    env: Env,
    ctx: TenantContext,
    manifest: uuid.UUID,
    org: uuid.UUID,
    dataset: uuid.UUID,
    position: int,
    role: str,
    group: str | None = None,
) -> BaseException | None:
    return raw_error(
        env,
        ctx,
        "INSERT INTO dataset_split_assignments (organization_id, manifest_id, record_id, "
        "source_dataset_id, batch_position, role, group_id, record_sha256, normalized_sha256) "
        "VALUES (:o, :m, :r, :d, :p, :role, :g, :h1, :h2)",
        o=org,
        m=manifest,
        r=f"{dataset}:{position}",
        d=dataset,
        p=position,
        role=role,
        g=group or f"raw-group-{position}",
        h1="a" * 64,
        h2="b" * 64,
    )


def raw_lock(env: Env, ctx: TenantContext, manifest: uuid.UUID) -> BaseException | None:
    return raw_error(
        env,
        ctx,
        "UPDATE dataset_split_manifests SET status='locked', approved_by='raw-approver', "
        "frozen_manifest_sha256=:h, reviewed_leakage_report_sha256=:h, locked_at=now(), "
        "updated_at=now() WHERE id=:m",
        h="c" * 64,
        m=manifest,
    )


def section_raw(env: Env) -> None:
    w = env.world("raw")
    ctx = w.ctx
    a = env.dataset(w, "raw-train", "training", 60)
    d = env.dataset(w, "raw-dev", "candidate_development", 6)
    h = env.dataset(w, "raw-held", "held_out_evaluation", 6)
    a9 = env.dataset(w, "raw-train-9", "training", 10)

    prop, e1 = env.propose(w, "raw-prop", [a, d, h])
    blocked, e2 = env.propose(w, "raw-blocked", [a, d, h], route=BLOCKED_ROUTE)
    scratch, e3 = env.propose(w, "raw-scratch", [a, d, h])
    lock_me, e4 = env.propose(w, "raw-lock", [a, d, h])
    check(f"raw-scenario proposals exist: {e1!r} {e2!r} {e3!r} {e4!r}", not (e1 or e2 or e3 or e4))
    if e1 or e2 or e3 or e4:
        return
    check("the blocked-route proposal is blocked", blocked.leakage_status == "blocked")
    locked, error = env.approve(w, lock_me)
    check(f"a locked manifest to attack: {error!r}", error is None and locked is not None)
    if error is not None:
        return
    org = env.org1

    # -- immutability of the lock and of every column ---------------------------------------
    error = raw_error(
        env, ctx, "UPDATE dataset_split_manifests SET name='renamed' WHERE id=:m", m=prop.id
    )
    check(
        f"a column that is not grantable cannot be updated: {error!r}",
        refused_by(error, "permission denied"),
    )
    error = raw_error(
        env, ctx, "UPDATE dataset_split_manifests SET seed='x' WHERE id=:m", m=lock_me.id
    )
    check(
        f"the seed of a locked manifest cannot change: {error!r}",
        refused_by(error, "permission denied"),
    )
    for label, sql in (
        ("approved_by", "UPDATE dataset_split_manifests SET approved_by='someone' WHERE id=:m"),
        (
            "status back to proposed",
            "UPDATE dataset_split_manifests SET status='proposed' WHERE id=:m",
        ),
        ("status to rejected", "UPDATE dataset_split_manifests SET status='rejected' WHERE id=:m"),
        (
            "leakage_reviews",
            "UPDATE dataset_split_manifests SET leakage_reviews='[]'::jsonb WHERE id=:m",
        ),
    ):
        error = raw_error(env, ctx, sql, m=lock_me.id)
        check(
            f"a locked manifest never changes ({label}): {error!r}",
            refused_by(error, "never changes"),
        )
    error = raw_error(env, ctx, "DELETE FROM dataset_split_manifests WHERE id=:m", m=lock_me.id)
    check(f"DELETE is not granted: {error!r}", refused_by(error, "permission denied"))
    for table in TABLES[1:]:
        error = raw_error(env, ctx, f"DELETE FROM {table}")
        check(
            f"DELETE on {table} is not granted: {error!r}", refused_by(error, "permission denied")
        )
        error = raw_error(env, ctx, f"UPDATE {table} SET organization_id = organization_id")
        check(
            f"UPDATE on {table} is not granted: {error!r}", refused_by(error, "permission denied")
        )

    # -- the lock transition ----------------------------------------------------------------
    error = raw_error(
        env, ctx, "UPDATE dataset_split_manifests SET status='locked' WHERE id=:m", m=prop.id
    )
    check(
        f"a lock without its lock columns: {error!r}",
        refused_by(error, "ck_dataset_split_manifests_lock_columns"),
    )
    error = raw_error(
        env, ctx, "UPDATE dataset_split_manifests SET status='archived' WHERE id=:m", m=prop.id
    )
    check(
        f"a proposed manifest can only be locked or rejected: {error!r}",
        refused_by(error, "can only be locked or rejected"),
    )
    error = raw_lock(env, ctx, blocked.id)
    check(
        f"a blocked leakage report cannot lock: {error!r}",
        refused_by(error, "ck_dataset_split_manifests_locked_leakage"),
    )
    count_before_extra = env.counts()
    error = raw_assignment(env, ctx, prop.id, org, a.id, 9000, "train")
    check(
        f"control: a valid extra record is accepted into a proposed manifest: {error!r}",
        error is None,
    )
    error = raw_lock(env, ctx, prop.id)
    check(
        f"a lock whose counts differ from the assignments: {error!r}",
        refused_by(error, "do not match the stored assignments"),
    )
    check("neither refused lock wrote anything", env.counts()[0] == count_before_extra[0])

    # A group that spans two roles cannot lock (set up by the owner role, which has no trigger on
    # assignments, so only the lock-time check can catch it).
    pair = env.rows(
        "SELECT t.record_id, f.record_id, t.group_id FROM dataset_split_assignments t "
        "JOIN dataset_split_assignments f ON f.manifest_id = t.manifest_id "
        "WHERE t.manifest_id=:m AND t.role='train' AND f.role='final_qualification' LIMIT 1",
        m=scratch.id,
    )[0]
    env.execute(
        "UPDATE dataset_split_assignments SET group_id=:g WHERE manifest_id=:m AND record_id=:r",
        g=pair[2],
        m=scratch.id,
        r=pair[1],
    )
    error = raw_lock(env, ctx, scratch.id)
    check(
        f"a group spanning two roles cannot lock: {error!r}",
        refused_by(error, "crosses a split boundary"),
    )
    env.execute(
        "UPDATE dataset_split_assignments SET group_id=:g WHERE manifest_id=:m AND record_id=:r",
        g=pair[1].replace(":", "-"),
        m=scratch.id,
        r=pair[1],
    )
    # Control: with every invariant true, a hand-written lock is accepted by the database.
    error = raw_lock(env, ctx, scratch.id)
    check(f"control: a database-valid lock is accepted: {error!r}", error is None)

    # -- child rows -------------------------------------------------------------------------
    for label, call in (
        ("an assignment", lambda: raw_assignment(env, ctx, lock_me.id, org, a.id, 9001, "train")),
        (
            "a role row",
            lambda: raw_error(
                env,
                ctx,
                "INSERT INTO dataset_split_manifest_roles (id, organization_id, manifest_id, role, "
                "purpose, record_count, group_count, role_sha256) VALUES (gen_random_uuid(), :o, "
                ":m, 'train', 'training', 1, 1, :h)",
                o=org,
                m=lock_me.id,
                h="d" * 64,
            ),
        ),
    ):
        error = call()
        check(
            f"{label} cannot be added after the lock: {error!r}",
            refused_by(error, "only to a proposed manifest"),
        )
    for role, purpose, needle in (
        ("heldout_test", "held_out_evaluation", "ck_dataset_split_manifest_roles_pair"),
        ("train", "held_out_evaluation", "ck_dataset_split_manifest_roles_pair"),
        ("train", "training", "uq_dataset_split_manifest_roles_role"),
    ):
        error = raw_error(
            env,
            ctx,
            "INSERT INTO dataset_split_manifest_roles (id, organization_id, manifest_id, role, "
            "purpose, record_count, group_count, role_sha256) VALUES (gen_random_uuid(), :o, :m, "
            ":r, :p, 1, 1, :h)",
            o=org,
            m=prop.id,
            r=role,
            p=purpose,
            h="d" * 64,
        )
        check(f"role row ({role}, {purpose}) is refused: {error!r}", refused_by(error, needle))
    error = raw_assignment(env, ctx, prop.id, org, h.id, 9100, "train")
    check(
        f"a held-out record cannot sit in train: {error!r}",
        refused_by(error, "pinned to the final role"),
    )
    error = raw_assignment(env, ctx, prop.id, org, h.id, 9101, "development_eval")
    check(
        f"a held-out record cannot sit in development: {error!r}",
        refused_by(error, "pinned to the final role"),
    )
    error = raw_assignment(env, ctx, prop.id, org, d.id, 9102, "final_qualification")
    check(
        f"a development record cannot sit in final: {error!r}",
        refused_by(error, "pinned to the development role"),
    )
    error = raw_assignment(env, ctx, prop.id, org, h.id, 9103, "final_qualification")
    check(f"control: a held-out record in final is accepted: {error!r}", error is None)
    error = raw_assignment(env, ctx, prop.id, org, a9.id, 9104, "train")
    check(
        f"a record of a dataset that is not a source of the manifest: {error!r}",
        refused_by(error, "source of its own manifest"),
    )

    dataset_row = env.rows(
        "SELECT name, purpose, version, verified_sha256 FROM datasets WHERE id=:d", d=a9.id
    )[0]
    report_row = env.rows(
        "SELECT id, report_sha256 FROM dataset_quality_reports WHERE dataset_id=:d "
        "ORDER BY report_seq DESC LIMIT 1",
        d=a9.id,
    )[0]
    error = raw_error(
        env,
        ctx,
        "INSERT INTO dataset_split_manifest_sources (id, organization_id, manifest_id, project_id, "
        "dataset_id, dataset_name, dataset_purpose, dataset_version, dataset_sha256, "
        "dataset_source_connection_id, allowed_purposes_snapshot, quality_report_id, "
        "quality_report_sha256, excluded_positions_sha256, created_at) VALUES (gen_random_uuid(), "
        ":o, :m, :p, :d, :n, :pu, :v, :s, :c, ARRAY['training']::text[], :q, :qs, :x, now())",
        o=org,
        m=prop.id,
        p=w.project_id,
        d=a9.id,
        n=dataset_row[0],
        pu=dataset_row[1],
        v=dataset_row[2],
        s=dataset_row[3],
        c=w.conns["all"],
        q=report_row[0],
        qs=report_row[1],
        x="e" * 64,
    )
    check(f"control: a source with a narrow frozen grant is accepted: {error!r}", error is None)
    error = raw_assignment(env, ctx, prop.id, org, a9.id, 9105, "validation")
    check(
        f"a role whose purpose the frozen grant lacks: {error!r}", refused_by(error, "frozen grant")
    )
    error = raw_assignment(env, ctx, prop.id, org, a9.id, 9106, "train")
    check(f"control: the granted purpose is accepted: {error!r}", error is None)

    # -- manifest inserts -------------------------------------------------------------------
    error = raw_manifest_insert(env, ctx, prop.id, name="raw-new")
    check(f"control: a valid raw manifest insert is accepted: {error!r}", error is None)
    error = raw_manifest_insert(
        env,
        ctx,
        prop.id,
        name="raw-prelocked",
        status="locked",
        approved_by="x",
        frozen_manifest_sha256="1" * 64,
        reviewed_leakage_report_sha256="2" * 64,
        locked_at=prop.created_at,
    )
    check(
        f"a manifest cannot be inserted already locked: {error!r}",
        refused_by(error, "inserted as proposed"),
    )
    error = raw_manifest_insert(
        env, ctx, prop.id, name="raw-legacy", strategy="legacy_three_split_v1", ratios=[0, 0, 0, 0]
    )
    check(
        f"the legacy strategy needs its lineage hash: {error!r}",
        refused_by(error, "ck_dataset_split_manifests_legacy_lineage"),
    )
    error = raw_manifest_insert(
        env, ctx, prop.id, name="raw-lineage", migrated_from_legacy_sha256="3" * 64
    )
    check(
        f"a lineage hash needs the legacy strategy: {error!r}",
        refused_by(error, "ck_dataset_split_manifests_legacy_lineage"),
    )
    error = raw_manifest_insert(
        env,
        ctx,
        prop.id,
        name="raw-legacy-ok",
        strategy="legacy_three_split_v1",
        ratios=[0, 0, 0, 0],
        migrated_from_legacy_sha256="3" * 64,
    )
    check(
        f"control: the legacy strategy with its lineage hash is accepted: {error!r}", error is None
    )
    error = raw_manifest_insert(
        env, ctx, prop.id, name="raw-v2-noparent", version=2, parent_version=None
    )
    check(
        f"version 2 needs a parent: {error!r}",
        refused_by(error, "ck_dataset_split_manifests_first_has_no_parent"),
    )
    error = raw_manifest_insert(env, ctx, prop.id, name="raw-gap", version=3, parent_version=2)
    check(
        f"a version gap has no parent row: {error!r}",
        refused_by(error, "fk_dataset_split_manifests_parent"),
    )
    error = raw_manifest_insert(env, ctx, prop.id, name="raw-gap-2", version=3, parent_version=1)
    check(
        f"a parent that is not the previous version: {error!r}",
        refused_by(error, "ck_dataset_split_manifests_parent_is_previous"),
    )
    for ratios in ([50, 50, 0, 0], [70, 10, 10, 5], [0, 40, 40, 20], [101, 0, 0, -1]):
        error = raw_manifest_insert(
            env, ctx, prop.id, name=f"raw-ratios-{ratios[0]}", ratios=ratios
        )
        check(
            f"ratios {ratios} are refused: {error!r}",
            refused_by(error, "ck_dataset_split_manifests_ratios"),
        )
    error = raw_manifest_insert(env, ctx, prop.id, name="raw-prop", version=1)
    check(
        f"the same (project, name, version) twice: {error!r}",
        refused_by(error, "uq_dataset_split_manifests_project_name_version"),
    )
    error = raw_manifest_insert(env, env.ctx2, prop.id, name="raw-cross-tenant")
    check(
        f"a raw cross-tenant INSERT is refused by row-level security: {error!r}",
        refused_by(error, "row-level security"),
    )
    error = raw_manifest_insert(
        env, ctx, prop.id, name="raw-bad-hash", split_manifest_sha256="not-a-hash"
    )
    check(f"a malformed hash: {error!r}", refused_by(error, "ck_dataset_split_manifests_hashes"))
    error = raw_manifest_insert(env, ctx, prop.id, name="raw-bad-leakage", leakage_status="maybe")
    check(
        f"an unknown leakage status: {error!r}",
        refused_by(error, "ck_dataset_split_manifests_leakage_status"),
    )
    error = raw_manifest_insert(env, ctx, prop.id, name="raw-bad-strategy", strategy="random")
    check(
        f"an unknown strategy: {error!r}", refused_by(error, "ck_dataset_split_manifests_strategy")
    )


def seed(env: Env) -> None:
    with Session(env.owner) as session:
        org1 = Organization(name="Acme 0519", slug=f"acme-0519-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0519", slug=f"globex-0519-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Main", slug="main")
        session.add_all([ws1, ws2])
        session.commit()
        env.org1, env.org2, env.ws1, env.ws2 = org1.id, org2.id, ws1.id, ws2.id
    env.ctx1 = TenantContext(organization_id=env.org1)
    env.ctx2 = TenantContext(organization_id=env.org2)


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        migrated = _alembic(args, migration_url)
        check(
            f"{label} runs cleanly (exit {migrated.returncode})\n{migrated.stdout}\n{migrated.stderr}",  # noqa: E501
            migrated.returncode == 0,
        )
        if migrated.returncode != 0:
            _finish()
    heads = _alembic(["heads"], migration_url)
    check(f"exactly one Alembic head: {heads.stdout!r}", heads.stdout.count("(head)") == 1)

    env = Env()
    env.owner = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    env.factory = sessionmaker(bind=app_engine, expire_on_commit=False)
    check_catalog(env)
    seed(env)

    section_golden(env)
    section_quality(env)
    section_review(env)
    section_legacy(env)
    section_raw(env)
    _finish()


if __name__ == "__main__":
    main()
