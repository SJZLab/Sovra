#!/usr/bin/env python3
# ruff: noqa: E501
# mypy: disable-error-code="misc,arg-type,attr-defined,assignment,call-arg,index,union-attr,var-annotated"
"""FTF-H011-P2 -- real Postgres proof for dataset mapping persistence (migration 0034).

Proves against a live PostgreSQL server (never SQLite -- it has no row-level security) every clause
of the QUEUE.md FTF-H011-P2 contract. The datasets are real `datasets` rows created through the
FTF-0501-P1 upload store, the previews are the real FTF-H011-P1 service over the real FTF-0502-P1
dataset reader and the real importers, all driven by `dataset_mapping_store`. Only the byte store is
an in-memory `stream_object_windows` client (the S3 byte path is proved by the P1 / FTF-0502-P1
jobs); the sha256 and size the database holds are those of the very bytes it serves:

- the migration upgrades, downgrades and re-upgrades with one head; `dataset_mappings` is ENABLE +
  FORCE row-level security with a non-allow-all `tenant_isolation` policy; the app role has no
  DELETE and may update only the approval columns;
- golden: a JSONL dataset proposes into version 1 with an independently recomputed `mapping_sha256`,
  the importer from the verified content type, the grant snapshot, content-free counts and one
  content-free audit row; a changed mapping is version 2 with parent 1 and version 1 byte-identical;
  an unchanged re-proposal writes nothing;
- every refusal code writes nothing (row and audit counts unchanged), each next to a positive
  control: bad document, unsupported shape, missing critical roles, missing tool ids, foreign /
  missing / hidden held-out dataset, a revoked or narrowed grant, a role that may not approve, a
  purpose that is not the dataset's, a stale expected preview, a changed object, a storage outage,
  unmapped critical rows, an unacknowledged ignored key, a superseded version;
- approval takes the approver from the actor and stores only the sha256 of the ignored keys;
- `resolve_approved_mapping`: the dataset's own approval, a tabular prior version reused only on an
  unchanged header and unchanged tool definitions, a JSONL prior version never, a revoked grant
  never, a tampered document never;
- raw `sovra_app` SQL that bypasses the store is refused by the database itself: an UPDATE of an
  approved row, of a frozen column, a DELETE, an approval under a purpose that is not the dataset's
  or not in the frozen grant, a row inserted as approved, a version gap, a superseded approval,
  malformed hashes, a counts object that carries content, and a cross-tenant INSERT.

Requires SOVRA_DATABASE_URL (migration-owner role) and SOVRA_APP_DB_PASSWORD.
Run: python3 tests/integration/test_ftf_h011_p2_dataset_mappings.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.datasets import dataset_mapping_store as store  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402
from app.datasets.dataset_object_source import DatasetObjectStorage  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.tools import tool_definition_crud as tools  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-H011-P2"
ALL_PURPOSES = ["training", "validation", "candidate_development", "held_out_evaluation"]
CANARY = "CANARY-4c1d9e07-secret-value"
ALLSNAP = "{candidate_development,held_out_evaluation,training,validation}"


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


def _sha(data: bytes | str) -> str:
    raw = data if isinstance(data, bytes) else data.encode()
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> str:
    """Re-implemented here on purpose: the digest convention of FTF-0502-P1's `mapping_sha256`."""
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


class MemoryClient:
    """The `stream_object_windows` side of an S3 client, over bytes held in memory. `down` makes
    the first read fail like an unreachable store."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.down = False

    def stream_object_windows(
        self, bucket: str, key: str, *, window_bytes: int = 0, chunk_size: int = 0
    ) -> Iterator[bytes]:
        if self.down:
            raise ConnectionError("storage is down")
        data = self.objects[key]
        for start in range(0, len(data), 7):
            yield data[start : start + 7]


@dataclass
class World:
    label: str
    project_id: uuid.UUID
    conns: dict[str, uuid.UUID]
    ctx: TenantContext


class Env:
    def __init__(self) -> None:
        self.owner: Any = None
        self.factory: Any = None
        self.org1 = self.org2 = self.ws1 = self.ws2 = uuid.UUID(int=0)
        self.ctx1 = self.ctx2 = TenantContext(organization_id=uuid.UUID(int=0))
        self.client = MemoryClient()
        self.storage = DatasetObjectStorage(client=self.client, bucket="datasets")
        self.upload_actor = ustore.UploadActor("user", "seed-user", "req-upload")
        self.admin = store.MappingActor("user", "user-org1-admin", "workspace_admin", "req-admin")
        self.admin2 = store.MappingActor(
            "user", "user-org1-admin-2", "organization_admin", "req-a2"
        )
        self.ml = store.MappingActor("user", "user-org1-ml", "ml_engineer", "req-ml")

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

    def counts(self) -> tuple[int, int]:
        return (
            int(self.scalar("SELECT count(*) FROM dataset_mappings") or 0),
            int(self.scalar("SELECT count(*) FROM audit_events") or 0),
        )

    def fingerprint(self, mapping_id: uuid.UUID) -> str:
        return str(
            self.scalar("SELECT md5(m::text) FROM dataset_mappings m WHERE m.id=:i", i=mapping_id)
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
        for kind, purposes in (("all", ALL_PURPOSES), ("train", ["training"])):
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
        data: bytes,
        *,
        content_type: str = "application/jsonl",
        connection: str = "all",
    ) -> ustore.Dataset:
        sha = _sha(data)

        def build(session: Session) -> tuple[ustore.Dataset, str]:
            opened = ustore.create_upload_session(
                session,
                w.ctx,
                project_id=w.project_id,
                connection_id=w.conns[connection],
                purpose=purpose,
                declared_size_bytes=len(data),
                declared_sha256=sha,
                declared_content_type=content_type,
                max_bytes=len(data),
                actor=self.upload_actor,
            )
            dataset = ustore.record_upload_verified_and_create_dataset(
                session,
                w.ctx,
                project_id=w.project_id,
                upload_session_id=opened.id,
                dataset_name=name,
                verified_size_bytes=len(data),
                verified_sha256=sha,
                verified_content_type=content_type,
                actor=self.upload_actor,
            )
            return dataset, opened.object_key

        built, error = self.run(w.ctx, build)
        if error is not None:
            raise error
        dataset, key = built
        self.client.objects[key] = data
        return dataset

    def propose(
        self,
        w: World,
        dataset: ustore.Dataset,
        document: object,
        actor: store.MappingActor | None = None,
    ) -> tuple[Any, BaseException | None]:
        return self.run(
            w.ctx,
            lambda s: store.propose_dataset_mapping(
                s,
                w.ctx,
                project_id=w.project_id,
                dataset_id=dataset.id,
                mapping_document=document,
                storage=self.storage,
                actor=actor or self.admin,
            ),
        )

    def approve(
        self,
        w: World,
        mapping: Any,
        *,
        purpose: str | None = None,
        expected: str | None = None,
        keys: tuple[str, ...] = (),
        actor: store.MappingActor | None = None,
    ) -> tuple[Any, BaseException | None]:
        return self.run(
            w.ctx,
            lambda s: store.approve_dataset_mapping(
                s,
                w.ctx,
                project_id=w.project_id,
                mapping_id=mapping.id,
                expected_preview_sha256=expected or mapping.preview_sha256,
                purpose=purpose or mapping.dataset_purpose,
                acknowledged_ignored_keys=list(keys),
                storage=self.storage,
                actor=actor or self.admin,
            ),
        )

    def resolve(
        self,
        w: World,
        dataset: ustore.Dataset,
        *,
        purpose: str | None = None,
        include_held_out: bool = True,
    ) -> tuple[Any, BaseException | None]:
        return self.run(
            w.ctx,
            lambda s: store.resolve_approved_mapping(
                s,
                w.ctx,
                project_id=w.project_id,
                dataset_id=dataset.id,
                purpose=purpose or dataset.purpose,
                storage=self.storage,
                include_held_out=include_held_out,
            ),
        )


def code_of(error: BaseException | None) -> str | None:
    if error is None:
        return None
    if isinstance(error, store.MappingStoreError):
        return error.code
    return f"{type(error).__name__}: {error}"


def refuses(
    env: Env, label: str, code: str, call: Callable[[], tuple[Any, BaseException | None]]
) -> None:
    """`call` refuses with exactly `code` and writes no row and no audit event."""
    before = env.counts()
    _, error = call()
    check(f"{label}: refused with {code} (got {code_of(error)})", code_of(error) == code)
    check(f"{label}: wrote nothing {before} -> {env.counts()}", env.counts() == before)


def msg_mapping(
    role_map: dict[str, str] | None = None,
    policy: str = "reject",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "shape": "messages",
        "messages_pointer": "/conversation",
        "role_key": "role",
        "content_key": "content",
        "role_map": role_map if role_map is not None else {"human": "user", "bot": "assistant"},
        "on_unmapped_message_keys": policy,
    }
    document.update(extra or {})
    return document


def csv_mapping(prompt: str = "q", completion: str = "a") -> dict[str, Any]:
    return {
        "shape": "prompt_completion",
        "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
        "prompt": {"header": prompt},
        "completion": {"header": completion},
    }


def jsonl(*conversations: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps({"conversation": messages}).encode() + b"\n" for messages in conversations
    )


def convo(text_value: str = "hello", **extra: Any) -> list[dict[str, Any]]:
    return [
        {"role": "human", "content": text_value, **extra},
        {"role": "bot", "content": f"re: {text_value}", **extra},
    ]


TABLE = "dataset_mappings"


def check_catalog(env: Env) -> None:
    with env.owner.connect() as conn:
        enabled, forced = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=:t"),
            {"t": TABLE},
        ).one()
        check(f"{TABLE} has ENABLE and FORCE row-level security", bool(enabled and forced))
        policies = conn.execute(
            text(
                "SELECT polname, pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) FROM pg_policy "
                "WHERE polrelid = CAST(:t AS regclass)"
            ),
            {"t": TABLE},
        ).all()
        check(
            f"{TABLE} has one tenant_isolation policy that is not allow-all",
            len(policies) == 1
            and policies[0][0] == "tenant_isolation"
            and "app.current_organization_id" in str(policies[0][1])
            and str(policies[0][1]).strip().lower() != "true"
            and "app.current_organization_id" in str(policies[0][2])
            and str(policies[0][2]).strip().lower() != "true",
        )
        for privilege, expected in (("SELECT", True), ("INSERT", True), ("DELETE", False)):
            granted = conn.execute(
                text("SELECT has_table_privilege('sovra_app', :t, :p)"),
                {"t": TABLE, "p": privilege},
            ).scalar()
            check(f"sovra_app {privilege} on {TABLE} is {expected}", bool(granted) is expected)
        check(
            f"sovra_app has no table-level UPDATE on {TABLE}",
            not conn.execute(
                text("SELECT has_table_privilege('sovra_app', :t, 'UPDATE')"), {"t": TABLE}
            ).scalar(),
        )
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE grantee='sovra_app' AND table_name=:t AND privilege_type='UPDATE'"
                ),
                {"t": TABLE},
            )
        }
        check(
            f"sovra_app may UPDATE only the approval columns: {sorted(columns)}",
            columns
            == {
                "status",
                "approved_purpose",
                "approved_by",
                "approver_role",
                "approved_at",
                "ignored_keys_sha256",
                "updated_at",
            },
        )
        constraints = {
            row[0]
            for row in conn.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass)"),
                {"t": TABLE},
            )
        }
        for name in (
            "fk_dataset_mappings_connection",
            "fk_dataset_mappings_dataset_lineage",
            "fk_dataset_mappings_parent",
        ):
            check(f"constraint {name} exists", name in constraints)
        triggers = {
            row[0]
            for row in conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
        }
        for trigger in ("tr_dataset_mappings_insert_guard", "tr_dataset_mappings_update_guard"):
            check(f"trigger {trigger} exists", trigger in triggers)


# ----------------------------------------------------------------------------------------
# golden path and versioning
# ----------------------------------------------------------------------------------------


def section_golden(env: Env) -> dict[str, Any]:
    w = env.world("golden")
    data = jsonl(convo(f"hello {CANARY}"), convo("second"))
    dataset = env.dataset(w, "golden", "training", data)
    before = env.counts()
    v1, error = env.propose(w, dataset, msg_mapping())
    check(f"golden propose succeeds ({code_of(error)})", error is None and v1 is not None)
    if error is not None:
        return {}
    check(
        "v1 is mapping_version 1 with no parent",
        v1.mapping_version == 1 and v1.parent_mapping_version is None,
    )
    check(
        "v1 is proposed with every approval column empty",
        v1.status == "proposed"
        and v1.approved_by is None
        and v1.approved_purpose is None
        and v1.approver_role is None
        and v1.approved_at is None,
    )
    check(
        "mapping_sha256 equals the independently recomputed canonical digest",
        v1.mapping_sha256 == _canonical(msg_mapping()),
    )
    check(
        "the importer comes from the verified content type",
        v1.importer == "jsonl" and v1.importer_version == "1" and v1.transform_version == "h011.1",
    )
    check(
        "the dataset identity is copied from the dataset row",
        (v1.dataset_name, v1.dataset_purpose, v1.dataset_version, v1.dataset_sha256)
        == ("golden", "training", 1, _sha(data)),
    )
    check(
        "the grant snapshot is the connection's grant at proposal",
        sorted(v1.allowed_purposes_snapshot) == sorted(ALL_PURPOSES),
    )
    check(
        "counts are content-free and exact",
        v1.preview_counts == {"seen": 2, "accepted": 2, "rejected": 0},
    )
    check("the document is stored verbatim", v1.mapping_document == msg_mapping())
    check("source_schema_sha256 is empty for a JSONL source", v1.source_schema_sha256 is None)
    check(
        "exactly one mapping row and one audit row were written",
        env.counts() == (before[0] + 1, before[1] + 1),
    )
    audit = env.rows(
        "SELECT action, resource_type, resource_id, actor_id, details_json::text FROM audit_events WHERE resource_id=:i",
        i=str(v1.id),
    )
    check(
        "one dataset_mapping.proposed audit row for the actor",
        len(audit) == 1
        and audit[0][0] == "dataset_mapping.proposed"
        and audit[0][3] == env.admin.actor_id,
    )
    check(
        "the audit row is content-free (no document, key or canary)",
        len(audit) == 1
        and CANARY not in audit[0][4]
        and "conversation" not in audit[0][4]
        and "role_map" not in audit[0][4],
    )

    again, error = env.propose(w, dataset, msg_mapping())
    check("an unchanged re-proposal returns the latest row", error is None and again.id == v1.id)
    check("an unchanged re-proposal writes nothing", env.counts() == (before[0] + 1, before[1] + 1))

    fingerprint = env.fingerprint(v1.id)
    v2, error = env.propose(
        w, dataset, msg_mapping({"human": "user", "bot": "assistant", "x": "system"})
    )
    check(f"a changed mapping proposes ({code_of(error)})", error is None and v2 is not None)
    check(
        "the change is version 2 with parent 1",
        v2 is not None and v2.mapping_version == 2 and v2.parent_mapping_version == 1,
    )
    check("version 1 is byte-identical after version 2", env.fingerprint(v1.id) == fingerprint)

    refused = env.propose(w, dataset, msg_mapping(), actor=env.ml)
    check("an ml_engineer may propose a training dataset", refused[1] is None)

    # no preview content anywhere in the table
    leaked = env.scalar(
        "SELECT count(*) FROM dataset_mappings m WHERE m::text LIKE :c", c=f"%{CANARY}%"
    )
    check("no preview content reached the table", leaked == 0)
    leaked_audit = env.scalar(
        "SELECT count(*) FROM audit_events WHERE details_json::text LIKE :c", c=f"%{CANARY}%"
    )
    check("no preview content reached the audit log", leaked_audit == 0)
    return {"w": w, "dataset": dataset, "v1": v1, "v2": v2}


# ----------------------------------------------------------------------------------------
# propose refusals
# ----------------------------------------------------------------------------------------


def section_propose_refusals(env: Env) -> None:
    w = env.world("propose")
    dataset = env.dataset(w, "p", "training", jsonl(convo()))
    held_out = env.dataset(w, "held", "held_out_evaluation", jsonl(convo()))

    def p(document: object, actor: Any = None, ds: Any = None) -> Callable[[], Any]:
        return lambda: env.propose(w, ds or dataset, document, actor)

    refuses(env, "a non-object document", "mapping_invalid", p(["not", "an", "object"]))
    refuses(env, "an unknown shape", "mapping_invalid", p({"shape": "nope"}))
    refuses(env, "an unknown key", "mapping_invalid", p(msg_mapping(extra={"eval": "1+1"})))
    refuses(
        env, "a recognised-unsupported shape", "mapping_unsupported", p({"shape": "grouped_rows"})
    )
    refuses(
        env,
        "no user target in role_map",
        "critical_role_unmapped",
        p(msg_mapping({"bot": "assistant"})),
    )
    refuses(
        env,
        "no assistant target in role_map",
        "critical_role_unmapped",
        p(msg_mapping({"human": "user"})),
    )
    refuses(
        env,
        "a tool target without tool_calls_key",
        "tool_id_unmapped",
        p(msg_mapping({"human": "user", "bot": "assistant", "t": "tool"})),
    )
    refuses(
        env,
        "tool_calls_key without tool_call_id_key",
        "tool_id_unmapped",
        p(msg_mapping(extra={"tool_calls_key": "calls"})),
    )
    refuses(
        env,
        "a tool target with only tool_call_id_key",
        "tool_id_unmapped",
        p(
            msg_mapping(
                {"human": "user", "bot": "assistant", "t": "tool"}, extra={"tool_call_id_key": "id"}
            )
        ),
    )
    refuses(
        env,
        "a csv mapping over a JSONL dataset (importer-level refusal)",
        "mapping_invalid",
        p(csv_mapping()),
    )
    refuses(
        env,
        "an unknown dataset",
        "dataset_not_found",
        p(msg_mapping(), ds=ustore.Dataset(id=uuid.uuid4())),
    )
    refuses(
        env,
        "ml_engineer on a held-out dataset",
        "dataset_not_found",
        p(msg_mapping(), env.ml, held_out),
    )
    refuses(
        env,
        "a viewer role",
        "actor_not_permitted",
        p(msg_mapping(), store.MappingActor("user", "v", "viewer", "r")),
    )
    refuses(
        env,
        "a non-actor",
        "invalid_request",
        lambda: env.run(
            w.ctx,
            lambda s: store.propose_dataset_mapping(
                s,
                w.ctx,
                project_id=w.project_id,
                dataset_id=dataset.id,
                mapping_document=msg_mapping(),
                storage=env.storage,
                actor="admin",
            ),
        ),
    )
    check("an admin may propose a held-out dataset", p(msg_mapping(), None, held_out)()[1] is None)
    ok, error = p(
        msg_mapping(
            {"human": "user", "bot": "assistant", "t": "tool"},
            extra={"tool_calls_key": "calls", "tool_call_id_key": "id"},
        )
    )()
    check(
        f"positive control: tool mapping with both keys proposes ({code_of(error)})", error is None
    )

    # foreign tenant
    other = env.world("foreign", org=2)
    foreign = env.dataset(other, "f", "training", jsonl(convo()))
    refuses(
        env,
        "an org1 caller on an org2 dataset",
        "dataset_not_found",
        lambda: env.run(
            w.ctx,
            lambda s: store.propose_dataset_mapping(
                s,
                w.ctx,
                project_id=w.project_id,
                dataset_id=foreign.id,
                mapping_document=msg_mapping(),
                storage=env.storage,
                actor=env.admin,
            ),
        ),
    )
    refuses(
        env,
        "an org1 caller on an org2 project",
        "dataset_not_found",
        lambda: env.run(
            w.ctx,
            lambda s: store.propose_dataset_mapping(
                s,
                w.ctx,
                project_id=other.project_id,
                dataset_id=foreign.id,
                mapping_document=msg_mapping(),
                storage=env.storage,
                actor=env.admin,
            ),
        ),
    )

    # grants
    narrowed = env.dataset(w, "narrowed", "training", jsonl(convo()))
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = CAST('[\"validation\"]' AS jsonb) WHERE id=:i",
        i=w.conns["all"],
    )
    refuses(env, "a narrowed grant", "purpose_not_authorized", p(msg_mapping(), None, narrowed))
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = CAST(:p AS jsonb), connection_status='revoked' WHERE id=:i",
        i=w.conns["all"],
        p=json.dumps(ALL_PURPOSES),
    )
    refuses(env, "a revoked connection", "purpose_not_authorized", p(msg_mapping(), None, narrowed))
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='active' WHERE id=:i",
        i=w.conns["all"],
    )
    check(
        "positive control: the restored grant proposes",
        p(msg_mapping(), None, narrowed)()[1] is None,
    )

    # Law 17
    env.client.down = True
    refuses(
        env,
        "storage down",
        "source_unavailable",
        p(msg_mapping({"human": "user", "bot": "assistant", "z": "system"})),
    )
    env.client.down = False
    changed = env.dataset(w, "changed", "training", jsonl(convo("original")))
    key = env.scalar(
        "SELECT object_key FROM dataset_upload_sessions WHERE id=(SELECT upload_session_id FROM datasets WHERE id=:i)",
        i=changed.id,
    )
    env.client.objects[key] = jsonl(convo("tampered!"))
    refuses(env, "a changed object", "source_changed", p(msg_mapping(), None, changed))
    env.client.objects[key] = jsonl(convo("original"))
    check(
        "positive control: the restored object proposes",
        p(msg_mapping(), None, changed)()[1] is None,
    )

    # unavailable importer: a content type with no preview importer
    xlsx = env.dataset(
        w,
        "x",
        "training",
        b"PK-not-really-xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    refuses(env, "an XLSX dataset", "importer_unavailable", p(msg_mapping(), None, xlsx))


# ----------------------------------------------------------------------------------------
# approve / reject
# ----------------------------------------------------------------------------------------


def section_approve(env: Env) -> None:
    w = env.world("approve")
    dataset = env.dataset(w, "a", "training", jsonl(convo(), convo("two")))
    v1, _ = env.propose(w, dataset, msg_mapping())

    refuses(
        env,
        "ml_engineer approving",
        "approval_not_permitted",
        lambda: env.approve(w, v1, actor=env.ml),
    )
    refuses(
        env,
        "ml_engineer rejecting",
        "approval_not_permitted",
        lambda: env.run(
            w.ctx,
            lambda s: store.reject_dataset_mapping(
                s, w.ctx, project_id=w.project_id, mapping_id=v1.id, actor=env.ml
            ),
        ),
    )
    refuses(
        env,
        "a purpose that is not the dataset's",
        "purpose_mismatch",
        lambda: env.approve(w, v1, purpose="validation"),
    )
    refuses(
        env,
        "a stale expected preview",
        "preview_changed",
        lambda: env.approve(w, v1, expected=_sha("stale")),
    )
    refuses(
        env,
        "a malformed expected preview",
        "invalid_request",
        lambda: env.approve(w, v1, expected="not-a-hash"),
    )
    refuses(
        env,
        "an unknown mapping",
        "mapping_not_found",
        lambda: env.run(
            w.ctx,
            lambda s: store.approve_dataset_mapping(
                s,
                w.ctx,
                project_id=w.project_id,
                mapping_id=uuid.uuid4(),
                expected_preview_sha256=v1.preview_sha256,
                purpose="training",
                acknowledged_ignored_keys=[],
                storage=env.storage,
                actor=env.admin,
            ),
        ),
    )
    other = env.world("approve-foreign", org=2)
    refuses(
        env,
        "a foreign project's mapping",
        "mapping_not_found",
        lambda: env.run(
            other.ctx,
            lambda s: store.approve_dataset_mapping(
                s,
                other.ctx,
                project_id=w.project_id,
                mapping_id=v1.id,
                expected_preview_sha256=v1.preview_sha256,
                purpose="training",
                acknowledged_ignored_keys=[],
                storage=env.storage,
                actor=env.admin,
            ),
        ),
    )

    # superseded
    v2, _ = env.propose(
        w, dataset, msg_mapping({"human": "user", "bot": "assistant", "s": "system"})
    )
    refuses(env, "a superseded version", "mapping_superseded", lambda: env.approve(w, v1))

    # positive control + approver from the actor
    approved, error = env.approve(w, v2, actor=env.admin2)
    check(f"v2 approves ({code_of(error)})", error is None)
    if error is None:
        check(
            "the approver is the actor, never input",
            approved.approved_by == env.admin2.actor_id
            and approved.approver_role == "organization_admin",
        )
        check(
            "approved_purpose is the dataset's purpose and status approved",
            approved.approved_purpose == "training"
            and approved.status == "approved"
            and approved.approved_at is not None,
        )
        check("no ignored keys for a reject-policy mapping", approved.ignored_keys_sha256 is None)
    row = env.rows(
        "SELECT status, approved_by, approver_role FROM dataset_mappings WHERE id=:i", i=v2.id
    )[0]
    check(
        "the database row holds the approval",
        tuple(row) == ("approved", env.admin2.actor_id, "organization_admin"),
    )
    audit = env.scalar(
        "SELECT count(*) FROM audit_events WHERE action='dataset_mapping.approved' AND resource_id=:i",
        i=str(v2.id),
    )
    check("one content-free approval audit row", audit == 1)
    refuses(env, "approving twice", "mapping_not_proposed", lambda: env.approve(w, v2))
    refuses(
        env,
        "rejecting an approved mapping",
        "mapping_not_proposed",
        lambda: env.run(
            w.ctx,
            lambda s: store.reject_dataset_mapping(
                s, w.ctx, project_id=w.project_id, mapping_id=v2.id, actor=env.admin
            ),
        ),
    )

    # reject, then re-propose: a rejected latest is never reused
    v3, _ = env.propose(
        w, dataset, msg_mapping({"human": "user", "bot": "assistant", "r": "system"})
    )
    rejected, error = env.run(
        w.ctx,
        lambda s: store.reject_dataset_mapping(
            s, w.ctx, project_id=w.project_id, mapping_id=v3.id, actor=env.admin
        ),
    )
    check(
        "a proposed mapping rejects",
        error is None and rejected.status == "rejected" and rejected.approved_by is None,
    )
    v4, error = env.propose(
        w, dataset, msg_mapping({"human": "user", "bot": "assistant", "r": "system"})
    )
    check(
        "an identical re-proposal after a rejection is a new version",
        error is None
        and v4.id != v3.id
        and v4.mapping_version == 4
        and v4.parent_mapping_version == 3,
    )

    # grants at approval time
    g = env.world("approve-grant")
    narrow = env.dataset(g, "n", "training", jsonl(convo()), connection="train")
    pending, _ = env.propose(g, narrow, msg_mapping())
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = CAST('[\"validation\"]' AS jsonb) WHERE id=:i",
        i=g.conns["train"],
    )
    refuses(
        env,
        "a grant narrowed after the proposal",
        "purpose_not_authorized",
        lambda: env.approve(g, pending),
    )
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = CAST('[\"training\"]' AS jsonb), connection_status='revoked' WHERE id=:i",
        i=g.conns["train"],
    )
    refuses(env, "a revoked connection", "purpose_not_authorized", lambda: env.approve(g, pending))
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='active' WHERE id=:i",
        i=g.conns["train"],
    )
    check("positive control: the restored grant approves", env.approve(g, pending)[1] is None)

    # Law 17 at approval
    s = env.world("approve-law17")
    live = env.dataset(s, "l", "training", jsonl(convo()))
    pend, _ = env.propose(s, live, msg_mapping())
    env.client.down = True
    refuses(env, "storage down at approval", "source_unavailable", lambda: env.approve(s, pend))
    env.client.down = False
    key = env.scalar(
        "SELECT object_key FROM dataset_upload_sessions WHERE id=(SELECT upload_session_id FROM datasets WHERE id=:i)",
        i=live.id,
    )
    original = env.client.objects[key]
    env.client.objects[key] = original.replace(b"hello", b"HELLO")
    refuses(
        env, "an object changed after the proposal", "source_changed", lambda: env.approve(s, pend)
    )
    env.client.objects[key] = original
    check("positive control: the restored object approves", env.approve(s, pend)[1] is None)

    # a preview that moved: a tool definition added after the proposal changes tool_definitions_sha256
    t = env.world("approve-tools")
    tooled = env.dataset(t, "t", "training", jsonl(convo()))
    pend_t, _ = env.propose(t, tooled, msg_mapping())
    _, error = env.run(
        t.ctx,
        lambda sess: tools.create_tool_definition(
            sess,
            t.ctx,
            project_id=t.project_id,
            name="lookup",
            description=None,
            input_schema_json={"type": "object"},
            output_schema_json={"type": "object"},
            side_effect_class="read_only",
            requires_confirmation=False,
            is_critical=False,
            actor=tools.ActorInfo(actor_type="user", actor_id="seed", request_id="req-tool"),
        ),
    )
    check(f"a tool definition is added ({code_of(error)})", error is None)
    refuses(
        env,
        "tool definitions changed since the proposal",
        "preview_changed",
        lambda: env.approve(t, pend_t),
    )

    # critical rows
    c = env.world("approve-critical")
    bad = jsonl(convo(), [{"role": "narrator", "content": "x"}, {"role": "bot", "content": "y"}])
    crit = env.dataset(c, "c", "training", bad)
    pend_c, error = env.propose(c, crit, msg_mapping())
    check(f"a mapping with an unmapped raw role still proposes ({code_of(error)})", error is None)
    refuses(env, "unmapped role rows", "critical_rows_unmapped", lambda: env.approve(c, pend_c))
    fixed, error = env.propose(
        c, crit, msg_mapping({"human": "user", "bot": "assistant", "narrator": "system"})
    )
    check("positive control: mapping the role proposes", error is None)
    check("positive control: the mapped role approves", env.approve(c, fixed)[1] is None)

    ignore_data = jsonl(convo(weight=1, zeta="z"), convo("b", weight=2))
    ig = env.dataset(c, "ig", "training", ignore_data)
    reject_policy, _ = env.propose(c, ig, msg_mapping())
    refuses(
        env,
        "unmapped message keys under the reject policy",
        "critical_rows_unmapped",
        lambda: env.approve(c, reject_policy),
    )
    ignoring, error = env.propose(c, ig, msg_mapping(policy="ignore"))
    check(f"an ignore-policy mapping proposes ({code_of(error)})", error is None)
    refuses(
        env,
        "no acknowledgement of ignored keys",
        "ignored_keys_not_acknowledged",
        lambda: env.approve(c, ignoring),
    )
    refuses(
        env,
        "a partial acknowledgement",
        "ignored_keys_not_acknowledged",
        lambda: env.approve(c, ignoring, keys=("weight",)),
    )
    refuses(
        env,
        "an acknowledgement with an extra key",
        "ignored_keys_not_acknowledged",
        lambda: env.approve(c, ignoring, keys=("weight", "zeta", "extra")),
    )
    done, error = env.approve(c, ignoring, keys=("zeta", "weight"))
    check(f"the exact acknowledgement approves ({code_of(error)})", error is None)
    check(
        "only the sha256 of the ignored keys is stored",
        error is None and done.ignored_keys_sha256 == _canonical(["weight", "zeta"]),
    )
    stored = env.scalar(
        "SELECT ignored_keys_sha256 FROM dataset_mappings WHERE id=:i", i=ignoring.id
    )
    leaked = env.scalar("SELECT count(*) FROM dataset_mappings m WHERE m::text LIKE '%zeta%'")
    check(
        "the key names themselves are stored nowhere",
        stored == _canonical(["weight", "zeta"]) and leaked == 0,
    )

    # reject policy with an acknowledgement
    plain = env.dataset(c, "plain", "training", jsonl(convo()))
    pp, _ = env.propose(c, plain, msg_mapping())
    refuses(
        env,
        "an acknowledgement for a mapping that ignores nothing",
        "ignored_keys_not_acknowledged",
        lambda: env.approve(c, pp, keys=("weight",)),
    )


# ----------------------------------------------------------------------------------------
# resolve
# ----------------------------------------------------------------------------------------


def section_resolve(env: Env) -> None:
    w = env.world("resolve")
    ds = env.dataset(w, "own", "training", jsonl(convo()))
    refuses(env, "no approved mapping at all", "mapping_not_found", lambda: env.resolve(w, ds))
    m, _ = env.propose(w, ds, msg_mapping())
    refuses(env, "a proposed (unapproved) mapping", "mapping_not_found", lambda: env.resolve(w, ds))
    env.approve(w, m)
    got, error = env.resolve(w, ds)
    check(f"the dataset's own approved mapping resolves ({code_of(error)})", error is None)
    if error is None:
        check(
            "own resolution is not a reuse and carries the stored hash",
            not got.reused
            and got.mapping.mapping_sha256 == m.mapping_sha256
            and got.mapping_id == m.id
            and got.approved_purpose == "training",
        )
    refuses(
        env,
        "a purpose that is not the dataset's",
        "purpose_mismatch",
        lambda: env.resolve(w, ds, purpose="validation"),
    )
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='revoked' WHERE id=:i",
        i=w.conns["all"],
    )
    refuses(
        env,
        "a revoked grant resolves nothing",
        "purpose_not_authorized",
        lambda: env.resolve(w, ds),
    )
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='active' WHERE id=:i",
        i=w.conns["all"],
    )
    check("positive control: the restored grant resolves", env.resolve(w, ds)[1] is None)

    # tampered document
    env.execute("ALTER TABLE dataset_mappings DISABLE TRIGGER tr_dataset_mappings_update_guard")
    env.execute(
        "UPDATE dataset_mappings SET mapping_document = jsonb_set(mapping_document, '{role_map,x}', '\"system\"') WHERE id=:i",
        i=m.id,
    )
    env.execute("ALTER TABLE dataset_mappings ENABLE TRIGGER tr_dataset_mappings_update_guard")
    refuses(
        env,
        "a stored document that no longer hashes",
        "mapping_tampered",
        lambda: env.resolve(w, ds),
    )

    # tabular reuse
    t = env.world("resolve-csv")
    csv1 = env.dataset(t, "tab", "training", b"q,a\nwhat,this\nwho,that\n", content_type="text/csv")
    c1, error = env.propose(t, csv1, csv_mapping())
    check(f"a CSV mapping proposes ({code_of(error)})", error is None)
    check(
        "a CSV source records its header hash",
        error is None and c1.source_schema_sha256 is not None and c1.importer == "csv",
    )
    env.approve(t, c1)
    csv2 = env.dataset(t, "tab", "training", b"q,a\nfoo,bar\n", content_type="text/csv")
    check("the second upload of the name is version 2", csv2.version == 2)
    reused, error = env.resolve(t, csv2)
    check(
        f"an unchanged header reuses the prior approval ({code_of(error)})",
        error is None
        and reused.reused
        and reused.mapping_id == c1.id
        and reused.source_dataset_id == csv1.id,
    )
    drift = env.dataset(t, "tab", "training", b"q,a,extra\nfoo,bar,baz\n", content_type="text/csv")
    refuses(env, "a changed header", "mapping_reapproval_required", lambda: env.resolve(t, drift))
    renamed = env.dataset(t, "tab", "training", b"question,a\nfoo,bar\n", content_type="text/csv")
    refuses(env, "a renamed column", "mapping_reapproval_required", lambda: env.resolve(t, renamed))
    # tool definitions drift
    csv5 = env.dataset(t, "tab", "training", b"q,a\nfoo,bar\n", content_type="text/csv")
    check("positive control before the tool change", env.resolve(t, csv5)[1] is None)
    _, error = env.run(
        t.ctx,
        lambda sess: tools.create_tool_definition(
            sess,
            t.ctx,
            project_id=t.project_id,
            name="lookup",
            description=None,
            input_schema_json={"type": "object"},
            output_schema_json={"type": "object"},
            side_effect_class="read_only",
            requires_confirmation=False,
            is_critical=False,
            actor=tools.ActorInfo(actor_type="user", actor_id="seed", request_id="req-tool"),
        ),
    )
    check(f"a tool definition is added ({code_of(error)})", error is None)
    refuses(
        env, "changed tool definitions", "mapping_reapproval_required", lambda: env.resolve(t, csv5)
    )
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='revoked' WHERE id=:i",
        i=t.conns["all"],
    )
    refuses(
        env, "a revoked grant on a reuse", "purpose_not_authorized", lambda: env.resolve(t, csv2)
    )
    env.execute(
        "UPDATE dataset_source_connections SET connection_status='active' WHERE id=:i",
        i=t.conns["all"],
    )

    # JSONL never reuses
    j = env.world("resolve-jsonl")
    j1 = env.dataset(j, "log", "training", jsonl(convo()))
    jm, _ = env.propose(j, j1, msg_mapping())
    env.approve(j, jm)
    j2 = env.dataset(j, "log", "training", jsonl(convo("again")))
    refuses(env, "a JSONL prior version", "mapping_reapproval_required", lambda: env.resolve(j, j2))
    jm2, _ = env.propose(j, j2, msg_mapping())
    env.approve(j, jm2)
    check("positive control: a fresh approval resolves", env.resolve(j, j2)[1] is None)

    # a reuse is bound to the purpose of the prior approval
    p = env.world("resolve-purpose")
    pv = env.dataset(p, "pv", "validation", b"q,a\nx,y\n", content_type="text/csv")
    pm, _ = env.propose(p, pv, csv_mapping())
    env.approve(p, pm)
    pv2 = env.dataset(p, "pv", "validation", b"q,a\nx,y\n", content_type="text/csv")
    check("positive control: validation reuses", env.resolve(p, pv2)[1] is None)

    # cross tenant
    other = env.world("resolve-foreign", org=2)
    refuses(
        env,
        "a foreign caller",
        "dataset_not_found",
        lambda: env.run(
            other.ctx,
            lambda s: store.resolve_approved_mapping(
                s,
                other.ctx,
                project_id=w.project_id,
                dataset_id=ds.id,
                purpose="training",
                storage=env.storage,
                include_held_out=True,
            ),
        ),
    )


# ----------------------------------------------------------------------------------------
# raw SQL as the restricted role
# ----------------------------------------------------------------------------------------


def raw(env: Env, ctx: TenantContext, sql: str, **params: Any) -> BaseException | None:
    """Runs `sql` as sovra_app under `ctx`; returns the database error, or None if it was accepted."""
    try:
        with tenant_scoped_session_for(env.factory, ctx) as session:
            session.execute(text(sql), params)
        return None
    except DBAPIError as exc:
        return exc


def section_raw(env: Env) -> None:
    w = env.world("raw")
    ds = env.dataset(w, "raw", "training", jsonl(convo()))
    other_ds = env.dataset(w, "raw2", "training", jsonl(convo()), connection="train")
    proposed, _ = env.propose(w, ds, msg_mapping())
    approved_row, _ = env.propose(w, other_ds, msg_mapping())
    env.approve(w, approved_row)

    err = raw(env, w.ctx, "DELETE FROM dataset_mappings WHERE id=:i", i=proposed.id)
    check("DELETE is refused to the app role", err is not None)
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET mapping_sha256=:h WHERE id=:i",
        h=_sha("x"),
        i=proposed.id,
    )
    check("UPDATE of a frozen column is not granted", err is not None)
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET status='approved', approved_purpose='training', approved_by='x', approver_role='workspace_admin', approved_at=now(), updated_at=now() WHERE id=:i",
        i=approved_row.id,
    )
    check(
        "UPDATE of an approved row is refused by the trigger",
        err is not None and "never changes" in str(err),
    )
    err = raw(
        env, w.ctx, "UPDATE dataset_mappings SET updated_at=now() WHERE id=:i", i=approved_row.id
    )
    check("even a touch of an approved row is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET status='proposed', updated_at=now() WHERE id=:i",
        i=proposed.id,
    )
    check("proposed -> proposed is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET status='approved', updated_at=now() WHERE id=:i",
        i=proposed.id,
    )
    check("approved without the approval columns is refused by the CHECK", err is not None)
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET status='approved', approved_purpose='validation', approved_by='x', approver_role='workspace_admin', approved_at=now(), updated_at=now() WHERE id=:i",
        i=proposed.id,
    )
    check(
        "approved_purpose different from the dataset's purpose is refused",
        err is not None and "approved_purpose_is_dataset_purpose" in str(err),
    )
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET status='approved', approved_purpose='training', approved_by='x', approver_role='ml_engineer', approved_at=now(), updated_at=now() WHERE id=:i",
        i=proposed.id,
    )
    check("a non-admin approver role is refused", err is not None and "approver_role" in str(err))
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET status='rejected', approved_by='x', updated_at=now() WHERE id=:i",
        i=proposed.id,
    )
    check("a rejection that carries approval columns is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET status='approved', approved_purpose='training', approved_by='x', approver_role='workspace_admin', approved_at=now(), ignored_keys_sha256=:h, updated_at=now() WHERE id=:i",
        h=_sha("k"),
        i=proposed.id,
    )
    check(
        "an ignored-keys hash on a mapping that ignores nothing is refused",
        err is not None and "ignored_keys_acknowledged" in str(err),
    )

    # the frozen grant: a snapshot without 'training' can never carry a training approval
    narrow_id = uuid.uuid4()
    insert_sql = (
        "INSERT INTO dataset_mappings (id, organization_id, project_id, dataset_id, dataset_name, "
        "dataset_purpose, dataset_version, dataset_sha256, dataset_source_connection_id, "
        "mapping_version, parent_mapping_version, mapping_document, mapping_sha256, importer, "
        "importer_version, transform_version, tool_definitions_sha256, preview_sha256, "
        "preview_counts, status, allowed_purposes_snapshot, created_by, approved_purpose, "
        "approved_by, approver_role, approved_at) "
        "SELECT :id, d.organization_id, d.project_id, d.id, d.name, d.purpose, d.version, "
        "d.verified_sha256, COALESCE(CAST(:conn AS uuid), u.dataset_source_connection_id), :mv, :pv, CAST(:doc AS jsonb), :h, "
        "'jsonl', '1', 'h011.1', :h, :h, CAST(:counts AS jsonb), :status, CAST(:snap AS text[]), 'raw', "
        "CAST(:ap AS varchar), CAST(:ab AS varchar), CAST(:ar AS varchar), "
        "CASE WHEN CAST(:ap AS varchar) IS NULL THEN NULL ELSE now() END "
        "FROM datasets d JOIN dataset_upload_sessions u ON u.id = d.upload_session_id WHERE d.id=:ds"
    )

    def newds() -> ustore.Dataset:
        return env.dataset(w, f"raw-{uuid.uuid4().hex[:8]}", "training", jsonl(convo()))

    base = dict(
        id=narrow_id,
        mv=1,
        pv=None,
        doc=json.dumps(msg_mapping()),
        h=_sha("m"),
        counts=json.dumps({"seen": 1, "accepted": 1, "rejected": 0}),
        status="proposed",
        snap=ALLSNAP,
        ds=ds.id,
        conn=None,
        ap=None,
        ab=None,
        ar=None,
    )
    fresh = env.dataset(w, "raw3", "training", jsonl(convo()))
    base["ds"] = fresh.id
    # the snapshot is the connection's grant at that moment: a grant narrowed after the upload
    # is the only way a snapshot can lack the dataset's own purpose
    w2 = env.world("raw-narrow")
    nd = env.dataset(w2, "n", "training", jsonl(convo()), connection="train")
    nd2 = env.dataset(w2, "n2", "training", jsonl(convo()), connection="train")
    env.execute(
        "UPDATE dataset_source_connections SET allowed_purposes_json = CAST('[\"validation\"]' AS jsonb) WHERE id=:i",
        i=w2.conns["train"],
    )
    err = raw(
        env, w2.ctx, insert_sql, **{**base, "id": narrow_id, "ds": nd.id, "snap": "{validation}"}
    )
    check(
        f"raw INSERT of a proposed row whose snapshot is the narrowed grant is accepted ({err})",
        err is None,
    )
    err = raw(
        env,
        w2.ctx,
        "UPDATE dataset_mappings SET status='approved', approved_purpose='training', approved_by='x', approver_role='workspace_admin', approved_at=now(), updated_at=now() WHERE id=:i",
        i=narrow_id,
    )
    check(
        "a training approval outside the frozen grant is refused by the CHECK",
        err is not None and "approved_purpose_in_grant" in str(err),
    )

    err = raw(
        env,
        w2.ctx,
        insert_sql,
        **{**base, "id": uuid.uuid4(), "ds": nd2.id, "snap": "{training,validation}"},
    )
    check(
        f"a snapshot that is a superset of the narrowed grant is refused ({str(err)[:120]})",
        err is not None and "grant snapshot" in str(err),
    )
    # the frozen-column diff holds for every role, the owner included
    owner_edit = (
        "UPDATE dataset_mappings SET {col}, status='approved', approved_purpose='validation', "
        "approved_by='x', approver_role='workspace_admin', approved_at=now() WHERE id=:i"
    )
    for label, col, params in (
        ("mapping_sha256", "mapping_sha256=:v", {"v": _sha("other")}),
        ("preview_sha256", "preview_sha256=:v", {"v": _sha("other-preview")}),
        ("mapping_document", "mapping_document=CAST('{}' AS jsonb)", {}),
        ("dataset_name", "dataset_name='renamed'", {}),
        (
            "dataset_source_connection_id",
            "dataset_source_connection_id=CAST(:v AS uuid)",
            {"v": str(w2.conns["all"])},
        ),
        (
            "allowed_purposes_snapshot",
            "allowed_purposes_snapshot=CAST('{validation,training}' AS text[])",
            {},
        ),
    ):
        try:
            env.execute(owner_edit.format(col=col), i=narrow_id, **params)
            outcome = "accepted"
        except DBAPIError as exc:
            outcome = str(exc)
        check(
            f"the owner role cannot change frozen column {label} on a proposed row ({outcome[:100]})",
            "immutable except" in outcome,
        )
    check(
        "the row is untouched after the refused owner edits",
        env.rows("SELECT status, mapping_sha256 FROM dataset_mappings WHERE id=:i", i=narrow_id)[0][
            0
        ]
        == "proposed",
    )

    # the snapshot and the connection cannot be forged at insert (insert guard)
    forge = newds()
    err = raw(
        env, w.ctx, insert_sql, **{**base, "id": uuid.uuid4(), "ds": forge.id, "snap": "{training}"}
    )
    check(
        "a forged grant snapshot is refused by the insert guard",
        err is not None and "grant snapshot" in str(err),
    )
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{
            **base,
            "id": uuid.uuid4(),
            "ds": forge.id,
            "conn": str(w.conns["train"]),
            "snap": ALLSNAP,
        },
    )
    check(
        "a forged connection (another connection of the project) is refused by the insert guard",
        err is not None and "upload connection" in str(err),
    )
    foreign_conn = env.world("raw-other-project").conns["all"]
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{**base, "id": uuid.uuid4(), "ds": forge.id, "conn": str(foreign_conn)},
    )
    check("a connection of another project is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{**base, "id": uuid.uuid4(), "ds": forge.id, "snap": "{training,validation}"},
    )
    check(
        "a partial snapshot of the real grant is refused",
        err is not None and "grant snapshot" in str(err),
    )
    check(
        "positive control: the exact grant on the real connection inserts",
        raw(env, w.ctx, insert_sql, **{**base, "id": uuid.uuid4(), "ds": forge.id}) is None,
    )
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{
            **base,
            "id": uuid.uuid4(),
            "ds": newds().id,
            "status": "approved",
            "ap": "training",
            "ab": "x",
            "ar": "workspace_admin",
        },
    )
    check(
        "a row inserted as approved with every approval column valid is refused by the insert guard",
        err is not None and "inserted as proposed" in str(err),
    )

    err = raw(env, w.ctx, insert_sql, **{**base, "id": uuid.uuid4(), "mv": 1, "pv": None})
    check(f"a first version on the fresh dataset inserts ({err})", err is None)
    err = raw(env, w.ctx, insert_sql, **{**base, "id": uuid.uuid4(), "mv": 3, "pv": 2})
    check(
        "a version gap (parent 2 of a missing version) is refused by the foreign key",
        err is not None and "fk_dataset_mappings_parent" in str(err),
    )
    err = raw(env, w.ctx, insert_sql, **{**base, "id": uuid.uuid4(), "mv": 2, "pv": 1})
    check(f"the next version with its parent is accepted ({err})", err is None)
    err = raw(env, w.ctx, insert_sql, **{**base, "id": uuid.uuid4(), "mv": 2, "pv": None})
    check("a second version without a parent is refused", err is not None)
    err = raw(env, w.ctx, insert_sql, **{**base, "id": uuid.uuid4(), "mv": 5, "pv": 1})
    check("a parent that is not the previous version is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{**base, "id": uuid.uuid4(), "ds": newds().id, "mv": 1, "pv": None, "h": "XYZ"},
    )
    check("a malformed hash is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{
            **base,
            "id": uuid.uuid4(),
            "ds": newds().id,
            "mv": 1,
            "pv": None,
            "counts": json.dumps({"seen": 1, "accepted": 1, "rejected": 0, "secret value": 3}),
        },
    )
    check("a preview_counts object with a content-like key is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{
            **base,
            "id": uuid.uuid4(),
            "ds": newds().id,
            "mv": 1,
            "pv": None,
            "counts": json.dumps({"seen": "many", "accepted": 1, "rejected": 0}),
        },
    )
    check("a preview_counts object with a non-number is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{**base, "id": uuid.uuid4(), "ds": newds().id, "mv": 1, "pv": None, "snap": "{}"},
    )
    check("an empty grant snapshot is refused", err is not None)
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{
            **base,
            "id": uuid.uuid4(),
            "ds": newds().id,
            "mv": 1,
            "pv": None,
            "snap": "{marketing}",
        },
    )
    check("a snapshot with an unknown purpose is refused", err is not None)

    check(
        "positive control: a clean insert on a fresh dataset is accepted",
        raw(
            env,
            w.ctx,
            insert_sql,
            **{
                **base,
                "id": uuid.uuid4(),
                "ds": newds().id,
                "mv": 1,
                "pv": None,
                "snap": ALLSNAP,
            },
        )
        is None,
    )

    # a superseded version is never approved, even by raw SQL
    sup = env.dataset(w, "raw4", "training", jsonl(convo()))
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{
            **base,
            "id": (first := uuid.uuid4()),
            "ds": sup.id,
            "mv": 1,
            "pv": None,
            "snap": ALLSNAP,
        },
    )
    check("a first version inserts", err is None)
    err = raw(
        env,
        w.ctx,
        insert_sql,
        **{**base, "id": uuid.uuid4(), "ds": sup.id, "mv": 2, "pv": 1, "snap": ALLSNAP},
    )
    check("a second version inserts", err is None)
    err = raw(
        env,
        w.ctx,
        "UPDATE dataset_mappings SET status='approved', approved_purpose='training', approved_by='x', approver_role='workspace_admin', approved_at=now(), updated_at=now() WHERE id=:i",
        i=first,
    )
    check(
        "approving a superseded version is refused by the trigger",
        err is not None and "superseded" in str(err),
    )

    # cross-tenant
    other = env.world("raw-foreign", org=2)
    iso = env.dataset(w, "raw5", "training", jsonl(convo()))
    before_rows = env.counts()[0]
    err = raw(
        env, other.ctx, insert_sql, **{**base, "id": uuid.uuid4(), "ds": ds.id, "mv": 20, "pv": 19}
    )
    check(
        "an org2 session's INSERT ... SELECT over an org1 dataset inserts nothing",
        err is None and env.counts()[0] == before_rows,
    )
    d = env.rows(
        "SELECT d.organization_id, d.project_id, d.id, d.name, d.purpose, d.version, d.verified_sha256, u.dataset_source_connection_id FROM datasets d JOIN dataset_upload_sessions u ON u.id=d.upload_session_id WHERE d.id=:i",
        i=iso.id,
    )[0]
    err = raw(
        env,
        other.ctx,
        "INSERT INTO dataset_mappings (id, organization_id, project_id, dataset_id, dataset_name, dataset_purpose, dataset_version, dataset_sha256, dataset_source_connection_id, mapping_version, mapping_document, mapping_sha256, importer, importer_version, transform_version, tool_definitions_sha256, preview_sha256, preview_counts, status, allowed_purposes_snapshot, created_by) "
        "VALUES (:id, :o, :p, :d, :n, :pu, :v, :s, :c, 1, CAST(:doc AS jsonb), :h, 'jsonl', '1', 'h011.1', :h, :h, CAST(:counts AS jsonb), 'proposed', CAST('{candidate_development,held_out_evaluation,training,validation}' AS text[]), 'raw')",
        id=uuid.uuid4(),
        o=d[0],
        p=d[1],
        d=d[2],
        n=d[3],
        pu=d[4],
        v=d[5],
        s=d[6],
        c=d[7],
        doc=json.dumps(msg_mapping()),
        h=_sha("m"),
        counts=json.dumps({"seen": 1, "accepted": 1, "rejected": 0}),
    )
    check(
        "an org2 session cannot INSERT a row carrying org1's organization_id (RLS WITH CHECK)",
        err is not None and ("row-level security" in str(err) or "upload connection" in str(err)),
    )
    check(
        "positive control: the same INSERT as org1 is accepted",
        raw(
            env,
            w.ctx,
            "INSERT INTO dataset_mappings (id, organization_id, project_id, dataset_id, dataset_name, dataset_purpose, dataset_version, dataset_sha256, dataset_source_connection_id, mapping_version, mapping_document, mapping_sha256, importer, importer_version, transform_version, tool_definitions_sha256, preview_sha256, preview_counts, status, allowed_purposes_snapshot, created_by) VALUES (:id, :o, :p, :d, :n, :pu, :v, :s, :c, 1, CAST(:doc AS jsonb), :h, 'jsonl', '1', 'h011.1', :h, :h, CAST(:counts AS jsonb), 'proposed', CAST('{candidate_development,held_out_evaluation,training,validation}' AS text[]), 'raw')",
            id=uuid.uuid4(),
            o=d[0],
            p=d[1],
            d=d[2],
            n=d[3],
            pu=d[4],
            v=d[5],
            s=d[6],
            c=d[7],
            doc=json.dumps(msg_mapping()),
            h=_sha("m"),
            counts=json.dumps({"seen": 1, "accepted": 1, "rejected": 0}),
        )
        is None,
    )
    check(
        "an org2 session cannot see an org1 mapping",
        env.run(
            other.ctx, lambda s: s.execute(text("SELECT count(*) FROM dataset_mappings")).scalar()
        )[0]
        == 0,
    )
    refuses(
        env,
        "an org2 get of an org1 mapping",
        "mapping_not_found",
        lambda: env.run(
            other.ctx,
            lambda s: store.get_dataset_mapping(
                s, other.ctx, project_id=w.project_id, mapping_id=proposed.id
            ),
        ),
    )
    listed, error = env.run(
        w.ctx,
        lambda s: store.list_dataset_mappings(
            s, w.ctx, project_id=w.project_id, dataset_id=sup.id, limit=1
        ),
    )
    check(
        "list pages by mapping_version",
        error is None and [m.mapping_version for m in listed] == [1],
    )
    listed, error = env.run(
        w.ctx,
        lambda s: store.list_dataset_mappings(
            s, w.ctx, project_id=w.project_id, dataset_id=sup.id, limit=5, after_version=1
        ),
    )
    check(
        "list resumes after a version", error is None and [m.mapping_version for m in listed] == [2]
    )
    refuses(
        env,
        "a list limit of 0",
        "invalid_request",
        lambda: env.run(
            w.ctx,
            lambda s: store.list_dataset_mappings(
                s, w.ctx, project_id=w.project_id, dataset_id=sup.id, limit=0
            ),
        ),
    )


def seed(env: Env) -> None:
    with Session(env.owner) as session:
        org1 = Organization(name="Acme H011P2", slug=f"acme-h011p2-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex H011P2", slug=f"globex-h011p2-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Main", slug="main")
        session.add_all([ws1, ws2])
        session.commit()
        env.org1, env.org2, env.ws1, env.ws2 = org1.id, org2.id, ws1.id, ws2.id
    env.ctx1 = TenantContext(organization_id=env.org1)
    env.ctx2 = TenantContext(organization_id=env.org2)


def _finish() -> None:
    if FAILURES:
        for failure in FAILURES:
            print(failure, file=sys.stderr)
        print(f"{len(FAILURES)} check(s) failed", file=sys.stderr)
        sys.exit(1)
    print("all checks passed")
    sys.exit(0)


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
            f"{label} runs cleanly (exit {migrated.returncode})\n{migrated.stdout}\n{migrated.stderr}",
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
    section_propose_refusals(env)
    section_approve(env)
    section_resolve(env)
    section_raw(env)
    _finish()


if __name__ == "__main__":
    main()
