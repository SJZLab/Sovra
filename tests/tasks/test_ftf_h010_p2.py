#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,misc,no-untyped-def,var-annotated,union-attr,operator"
# ruff: noqa: E501
"""FTF-H010-P2 -- import-validation activity, job fencing and endpoint-only import.

No database server and no network: the activity runs against an in-memory SQLite store with the
tenant-session, audit and gateway seams replaced (the same split FTF-H010-P1's task test uses);
the real PostgreSQL / TLS-gateway evidence is `tests/integration/test_ftf_h010_p2_import_validation.py`.

Criterion ids:
  A  pure outcome tables over EVERY DiscoveryState / RouteAttestationState value (Law 17)
  B  argument validation (strict types, no coercion)
  C  claim: queued -> epoch 1, live lease refused, expired lease re-claimed at epoch + 1, terminal,
     origin allow-list, cross-project identical to missing, legacy row without a connection
  D  validate: revoked connection, discovery / attestation outcomes, alias exactness (generated
     Unicode variants), success shape (endpoint-only ModelVersion), one audit row
  E  fencing: the old epoch's final commit writes 0 rows -> no ModelVersion, no audit, job untouched
  F  no DB session is open during a gateway call
  G  META-LEAK: markers in ref / fingerprint / base_url / secret never reach a failure code, an audit
     row, an outcome or an exception chain
  H  the ModelVersion constructor: a NULL endpoint_reference only for an explicit external_endpoint
     with an explicit provider ref; the schema CHECK text matches migration 0029
  I  every fault in FAULTS turns the suite RED (fault-injected module copies)

Run directly: python3 tests/tasks/test_ftf_h010_p2.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import re
import sys
import tempfile
import traceback
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.litellm_client.route_attestation import (  # noqa: E402
    RouteAttestationResult,
    RouteAttestationState,
    RouteIdentityState,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)
from sqlalchemy import create_engine, event, select, text  # noqa: E402
from sqlalchemy.orm import Session, attributes  # noqa: E402

import app.model_versions.model_version_crud as version_crud  # noqa: E402
from app.model_versions import model_import_schema as job_schema  # noqa: E402
from app.model_versions import model_version_schema as version_schema  # noqa: E402

CID = "FTF-H010-P2"
MODULE_PATH = ROOT / "apps" / "api" / "app" / "model_versions" / "model_import_validation.py"
MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0030_model_import_validation.py"

_failures: list[str] = []
CHECK_COUNT = [0]


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECK_COUNT[0] += 1
    if not condition:
        _failures.append(f"FAIL [{criterion_id}] {description}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# -- fixtures --------------------------------------------------------------------------------

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
ORG = uuid.UUID("aaaaaaaa-1111-4111-8111-11111111111a")
OTHER_ORG = uuid.UUID("bbbbbbbb-2222-4222-8222-22222222222b")
PROJECT = uuid.UUID("cccccccc-3333-4333-8333-33333333333c")
OTHER_PROJECT = uuid.UUID("dddddddd-4444-4444-8444-44444444444d")
CONN = uuid.UUID("eeeeeeee-5555-4555-8555-55555555555e")
PARENT = uuid.UUID("ffffffff-6666-4666-8666-66666666666f")
REF = "org-agent-small"
FINGERPRINT = "deployment-abc123"
BASE_URL = "https://gateway.example"
SECRET_REF = "openbao://secret/litellm/org1/api_key"
SECRET_CONFIG = OpenBaoConfig(address="http://127.0.0.1:1", token="vault-" + "LEAK" + "TOKEN")
WORKER = "worker-1"


@event.listens_for(job_schema.ModelImportJob, "load")
@event.listens_for(job_schema.ModelImportJob, "refresh")
def _aware(target: Any, *args: Any) -> None:
    """SQLite returns naive datetimes; PostgreSQL's timestamptz returns aware ones."""
    value = target.lease_expires_at
    if value is not None and value.tzinfo is None:
        attributes.set_committed_value(target, "lease_expires_at", value.replace(tzinfo=UTC))


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)


@dataclass
class Gateway:
    discovery_state: DiscoveryState = DiscoveryState.DISCOVERED
    models: tuple[str, ...] = (REF,)
    attestation_state: RouteAttestationState = RouteAttestationState.ATTESTED
    identity: RouteIdentityState = RouteIdentityState.VERIFIED
    fingerprint: str | None = FINGERPRINT
    discover_calls: list[dict[str, Any]] = None
    attest_calls: list[dict[str, Any]] = None
    sessions_open_during_call: list[int] = None
    on_call: Any = None


class Env:
    """An in-memory store plus the replaced seams, bound to one (possibly fault-injected) module."""

    def __init__(self, m: Any, gateway: Gateway | None = None) -> None:
        self.m = m
        self.engine = create_engine("sqlite://")
        job_schema.ModelImportJob.__table__.create(self.engine)
        columns = list(version_schema.ModelVersion.__table__.c)
        defaults = [c.server_default for c in columns]
        for c in columns:
            c.server_default = None
        try:
            version_schema.ModelVersion.__table__.create(self.engine)
        finally:
            for c, d in zip(columns, defaults, strict=True):
                c.server_default = d
        self.audit = Audit()
        self.gateway = gateway or Gateway()
        self.gateway.discover_calls = []
        self.gateway.attest_calls = []
        self.gateway.sessions_open_during_call = []
        self.open_sessions = 0
        self.connection = SimpleNamespace(
            base_url=BASE_URL, secret_ref=SECRET_REF, status="active", revoked_at=None
        )
        self.connection_missing = False
        self.factory = lambda: Session(self.engine)
        self.statements: list[Any] = []

    # -- seams -------------------------------------------------------------------------------

    @contextlib.contextmanager
    def tenant_session(self, session_factory: Any, context: Any):
        session = session_factory()
        self.open_sessions += 1
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
            self.open_sessions -= 1

    def execute_scoped(
        self, session: Any, organization_id: Any, statement: Any, params: Any = None
    ) -> Any:
        self.statements.append(statement)
        return session.execute(statement)

    def get_connection(self, session: Any, context: Any, connection_id: Any) -> Any:
        if self.connection_missing:
            raise self.m.ConnectionNotFoundError("missing")
        return self.connection

    def discover(self, **kwargs: Any) -> ModelDiscoveryResult:
        self.gateway.discover_calls.append(kwargs)
        self.gateway.sessions_open_during_call.append(self.open_sessions)
        if self.gateway.on_call is not None:
            self.gateway.on_call("discover")
        g = self.gateway
        models = tuple(DiscoveredModel(i, ModelMetadataStatus.LIMITED) for i in g.models)
        if g.discovery_state is not DiscoveryState.DISCOVERED:
            models = ()
        return ModelDiscoveryResult(g.discovery_state, models, len(models), None, "d", NOW)

    def attest(self, **kwargs: Any) -> RouteAttestationResult:
        self.gateway.attest_calls.append(kwargs)
        self.gateway.sessions_open_during_call.append(self.open_sessions)
        if self.gateway.on_call is not None:
            self.gateway.on_call("attest")
        g = self.gateway
        return RouteAttestationResult(
            g.attestation_state, g.identity, "resolved", g.fingerprint, 1.0, "d", NOW
        )

    @contextlib.contextmanager
    def patched(self):
        m = self.m
        names = {
            "tenant_scoped_session_for": self.tenant_session,
            "execute_tenant_scoped": self.execute_scoped,
            "write_audit_event": self.audit,
            "get_connection": self.get_connection,
            "discover_models": self.discover,
            "attest_route": self.attest,
        }
        saved = {name: getattr(m, name) for name in names}
        saved_crud = version_crud.execute_tenant_scoped
        for name, value in names.items():
            setattr(m, name, value)
        version_crud.execute_tenant_scoped = names["execute_tenant_scoped"]
        try:
            yield self
        finally:
            for name, value in saved.items():
                setattr(m, name, value)
            version_crud.execute_tenant_scoped = saved_crud
            self.engine.dispose()

    # -- data --------------------------------------------------------------------------------

    def add_job(
        self,
        *,
        origin: str = "external_endpoint",
        state: str = "queued",
        epoch: int = 0,
        lease: datetime | None = None,
        connection: uuid.UUID | None = CONN,
        ref: str | None = REF,
        project: uuid.UUID = PROJECT,
        org: uuid.UUID = ORG,
        parent: uuid.UUID | None = None,
        failure_code: str | None = None,
        bypass_checks: bool = False,
    ) -> uuid.UUID:
        job_id = uuid.uuid4()
        with Session(self.engine) as session:
            if bypass_checks or (origin == "external_endpoint" and connection is None):
                # A legacy FTF-H010-P1 row: 0029 adds the connection CHECK NOT VALID, so such a
                # row exists in production although a new insert could not.
                session.execute(text("PRAGMA ignore_check_constraints = ON"))
            session.add(
                job_schema.ModelImportJob(
                    id=job_id,
                    organization_id=org,
                    project_id=project,
                    requested_origin=origin,
                    parent_model_version_id=parent,
                    trainable_artifact_id=None,
                    external_provider_model_ref=ref,
                    litellm_connection_id=connection,
                    state=state,
                    failure_code=failure_code,
                    claim_epoch=epoch,
                    lease_expires_at=lease,
                    created_by="ml-1",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()
            session.execute(text("PRAGMA ignore_check_constraints = OFF"))
        return job_id

    def job(self, job_id: uuid.UUID) -> Any:
        with Session(self.engine) as session:
            row = session.execute(
                select(job_schema.ModelImportJob).where(job_schema.ModelImportJob.id == job_id)
            ).scalar_one()
            session.expunge(row)
            return row

    def versions(self) -> list[Any]:
        with Session(self.engine) as session:
            rows = list(session.execute(select(version_schema.ModelVersion)).scalars())
            session.expunge_all()
            return rows

    def claim(
        self,
        job_id: uuid.UUID,
        *,
        now: datetime = NOW,
        lease: int = 60,
        project: uuid.UUID = PROJECT,
        org: uuid.UUID = ORG,
        origins: Any = ("external_endpoint",),
        worker: str = WORKER,
    ) -> Any:
        return self.m.claim_model_import_job(
            self.factory,
            org,
            project,
            job_id,
            origins=origins,
            worker_id=worker,
            lease_seconds=lease,
            now=now,
        )

    def validate(self, token: Any, *, now: datetime = NOW) -> Any:
        return self.m.validate_endpoint_import(
            self.factory, token, secret_config=SECRET_CONFIG, now=now
        )


def raises_code(fn: Any, code: str | None = None) -> bool:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ != "ModelImportActivityError":
            return False
        return code is None or exc.code == code
    return False


def expect(m: Any, fn: Any) -> str | None:
    """The activity error code `fn` raises, or None when it does not raise one."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return (
            exc.code
            if type(exc).__name__ == "ModelImportActivityError"
            else f"other:{type(exc).__name__}"
        )
    return None


# -- A: outcome tables -------------------------------------------------------------------------


def group_a(m: Any) -> None:
    failed, blocked = "failed", "blocked_external"
    expected_discovery: dict[str, tuple[str, str] | None] = {
        "DISCOVERED": None,
        "INVALID_TOKEN": (failed, "gateway_access_denied"),
        "CERTIFICATE_ERROR": (blocked, "gateway_unavailable"),
        "TIMEOUT": (blocked, "gateway_unavailable"),
        "DNS_FAILED": (blocked, "gateway_unavailable"),
        "CONNECTION_REFUSED": (blocked, "gateway_unavailable"),
        "UNREACHABLE": (blocked, "gateway_unavailable"),
        "DESTINATION_DENIED": (blocked, "gateway_unavailable"),
        "MALFORMED_RESPONSE": (blocked, "gateway_unavailable"),
        "SECRET_NOT_FOUND": (blocked, "gateway_unavailable"),
        "SECRET_ACCESS_DENIED": (blocked, "gateway_unavailable"),
        "SECRET_EXPIRED": (blocked, "gateway_unavailable"),
        "SECRET_BACKEND_ERROR": (blocked, "gateway_unavailable"),
        "SECRET_BACKEND_UNSUPPORTED": (blocked, "gateway_unavailable"),
    }
    check(
        "A1",
        "the expected discovery table names every DiscoveryState member",
        set(expected_discovery) == {s.name for s in DiscoveryState},
    )
    for state in DiscoveryState:
        check(
            "A1",
            f"discovery_outcome({state.name}) == {expected_discovery[state.name]}",
            m.discovery_outcome(state) == expected_discovery[state.name],
        )

    expected_attest: dict[str, tuple[str, str] | None] = {
        "ATTESTED": None,
        "CONTENT_FILTERED": (blocked, "route_attestation_unavailable"),
        "UNSUPPORTED_PARAMETER": (blocked, "route_attestation_unavailable"),
        "INVALID_TOKEN": (failed, "gateway_access_denied"),
        "CERTIFICATE_ERROR": (blocked, "route_attestation_unavailable"),
        "TIMEOUT": (blocked, "route_attestation_unavailable"),
        "DNS_FAILED": (blocked, "route_attestation_unavailable"),
        "CONNECTION_REFUSED": (blocked, "route_attestation_unavailable"),
        "UNREACHABLE": (blocked, "route_attestation_unavailable"),
        "DESTINATION_DENIED": (blocked, "route_attestation_unavailable"),
        "MALFORMED_RESPONSE": (blocked, "route_attestation_unavailable"),
        "SECRET_NOT_FOUND": (blocked, "route_attestation_unavailable"),
        "SECRET_ACCESS_DENIED": (blocked, "route_attestation_unavailable"),
        "SECRET_EXPIRED": (blocked, "route_attestation_unavailable"),
        "SECRET_BACKEND_ERROR": (blocked, "route_attestation_unavailable"),
        "SECRET_BACKEND_UNSUPPORTED": (blocked, "route_attestation_unavailable"),
    }
    check(
        "A2",
        "the expected attestation table names every RouteAttestationState member",
        set(expected_attest) == {s.name for s in RouteAttestationState},
    )
    for state in RouteAttestationState:
        for identity in RouteIdentityState:
            got = m.attestation_outcome(state, identity, FINGERPRINT)
            if (
                state is RouteAttestationState.ATTESTED
                and identity is RouteIdentityState.UNVERIFIED
            ):
                want: tuple[str, str] | None = (failed, "route_identity_unverified")
            else:
                want = expected_attest[state.name]
            check(
                "A2",
                f"attestation_outcome({state.name}, {identity.name}, fp) == {want}",
                got == want,
            )

    attested, verified = RouteAttestationState.ATTESTED, RouteIdentityState.VERIFIED
    bad_fingerprints: list[tuple[str, object]] = [
        ("None", None),
        ("empty", ""),
        ("spaces", "   "),
        ("NUL", "a\x00b"),
        ("CR", "a\rb"),
        ("LF", "a\nb"),
        ("lone surrogate", "a\ud800b"),
        ("257 chars", "x" * 257),
        ("int", 123),
        ("bytes", b"fp"),
        ("list", ["fp"]),
    ]
    for label, value in bad_fingerprints:
        check(
            "A3",
            f"a VERIFIED attestation with a {label} fingerprint is failed route_identity_unverified",
            m.attestation_outcome(attested, verified, value)
            == (failed, "route_identity_unverified"),
        )
    for label, value in (("1 char", "x"), ("256 chars", "x" * 256), ("typical", FINGERPRINT)):
        check(
            "A3",
            f"a {label} fingerprint is usable",
            m.attestation_outcome(attested, verified, value) is None,
        )
    check(
        "A3",
        "an unverified identity is never usable even with a perfect fingerprint",
        m.attestation_outcome(attested, RouteIdentityState.UNVERIFIED, FINGERPRINT)
        == (failed, "route_identity_unverified"),
    )


# -- B: argument validation --------------------------------------------------------------------


def group_b(m: Any) -> None:
    with Env(m).patched() as env:
        job = env.add_job()
        before = env.job(job)
        base = dict(origins=("external_endpoint",), worker_id=WORKER, lease_seconds=60, now=NOW)

        def call(**override: Any) -> str | None:
            args = {
                "organization_id": ORG,
                "project_id": PROJECT,
                "job_id": job,
                **base,
                **override,
            }
            positional = (args.pop("organization_id"), args.pop("project_id"), args.pop("job_id"))
            return expect(m, lambda: m.claim_model_import_job(env.factory, *positional, **args))

        tables: list[tuple[str, dict[str, Any]]] = [
            ("org as str", {"organization_id": str(ORG)}),
            ("org as None", {"organization_id": None}),
            ("project as str", {"project_id": str(PROJECT)}),
            ("job as int", {"job_id": 7}),
            ("naive now", {"now": datetime(2026, 10, 10, 12, 0)}),
            ("now as str", {"now": "2026-10-10T12:00:00+00:00"}),
            ("lease 0", {"lease_seconds": 0}),
            ("lease -1", {"lease_seconds": -1}),
            ("lease above the cap", {"lease_seconds": m.LEASE_SECONDS_MAX + 1}),
            ("lease as bool", {"lease_seconds": True}),
            ("lease as float", {"lease_seconds": 60.0}),
            ("lease as str", {"lease_seconds": "60"}),
            ("worker blank", {"worker_id": ""}),
            ("worker spaces", {"worker_id": "   "}),
            ("worker NUL", {"worker_id": "w\x00"}),
            ("worker 129 chars", {"worker_id": "w" * 129}),
            ("worker as int", {"worker_id": 7}),
            ("origins empty", {"origins": ()}),
            ("origins as a bare str", {"origins": "external_endpoint"}),
            ("origins as bytes", {"origins": b"external_endpoint"}),
            ("origins with an unknown origin", {"origins": ("external_endpoint", "made_up")}),
            ("origins with factory_trained", {"origins": ("factory_trained",)}),
            ("origins with an int", {"origins": (1,)}),
            ("origins with an unhashable", {"origins": ([],)}),
            ("origins as an int", {"origins": 5}),
        ]
        for label, override in tables:
            check(
                "B1",
                f"claim refuses {label} as invalid_argument",
                call(**override) == "invalid_argument",
            )
        after = env.job(job)
        check(
            "B2",
            "no refused argument wrote anything",
            after.state == before.state and after.claim_epoch == before.claim_epoch,
        )
        check(
            "B3",
            "boundaries: lease 1 and the cap, worker 200 chars are accepted",
            call(lease_seconds=1) is None,
        )
        env.engine.dispose()
    with Env(m).patched() as env:
        job = env.add_job()
        check(
            "B3",
            "the cap lease is accepted",
            expect(m, lambda: env.claim(job, lease=m.LEASE_SECONDS_MAX)) is None,
        )
    with Env(m).patched() as env:
        job = env.add_job()
        check(
            "B3",
            "a 128-character worker id is accepted",
            expect(m, lambda: env.claim(job, worker="w" * 128)) is None,
        )
    with Env(m).patched() as env:
        token = m.ClaimToken(ORG, PROJECT, uuid.uuid4(), 1, WORKER)
        check(
            "B4",
            "validate refuses a non-token",
            expect(
                m,
                lambda: m.validate_endpoint_import(
                    env.factory, SimpleNamespace(), secret_config=None, now=NOW
                ),
            )
            == "invalid_argument",
        )
        check(
            "B4",
            "validate refuses a naive now",
            expect(
                m,
                lambda: m.validate_endpoint_import(
                    env.factory, token, secret_config=None, now=datetime(2026, 1, 1)
                ),
            )
            == "invalid_argument",
        )
        check(
            "B4",
            "validate of a job that does not exist is job_not_found",
            expect(m, lambda: env.validate(token)) == "job_not_found",
        )


# -- C: claim ----------------------------------------------------------------------------------


def group_c(m: Any) -> None:
    with Env(m).patched() as env:
        job = env.add_job()
        token = env.claim(job, lease=90)
        row = env.job(job)
        check(
            "C1",
            "a claim returns a token carrying org, project, job, epoch 1 and the worker",
            (token.organization_id, token.project_id, token.job_id, token.epoch, token.worker_id)
            == (ORG, PROJECT, job, 1, WORKER),
        )
        check(
            "C1",
            "the claimed row is validating at epoch 1 with lease = now + lease_seconds",
            row.state == "validating"
            and row.claim_epoch == 1
            and row.lease_expires_at == NOW + timedelta(seconds=90),
        )
        check("C1", "updated_at is the claim time", row.updated_at.replace(tzinfo=UTC) == NOW)
        check(
            "C2",
            "a second claim inside the lease is job_already_claimed",
            expect(m, lambda: env.claim(job, now=NOW + timedelta(seconds=89)))
            == "job_already_claimed",
        )
        check("C2", "a refused second claim changes nothing", env.job(job).claim_epoch == 1)
        check(
            "C3",
            "a claim exactly at lease expiry is a re-claim (expiry is exclusive)",
            expect(m, lambda: env.claim(job, now=NOW + timedelta(seconds=90))) is None,
        )
        check("C3", "the re-claim after expiry takes epoch 2", env.job(job).claim_epoch == 2)
        later = NOW + timedelta(seconds=500)
        t3 = env.claim(job, now=later)
        check(
            "C3",
            "a further re-claim takes epoch 3 and a fresh lease",
            t3.epoch == 3 and env.job(job).lease_expires_at == later + timedelta(seconds=60),
        )

    with Env(m).patched() as env:
        for state, code in (("failed", "x"), ("blocked_external", "x"), ("succeeded", None)):
            job = env.add_job(state="queued")
            with Session(env.engine) as s:
                row = s.get(job_schema.ModelImportJob, job)
                row.state = state
                row.failure_code = code
                row.claim_epoch = 1
                row.result_model_version_id = uuid.uuid4() if state == "succeeded" else None
                s.commit()
            check(
                "C4",
                f"a {state} job is job_terminal",
                expect(m, lambda j=job: env.claim(j)) == "job_terminal",
            )
            check("C4", f"a refused {state} job keeps its epoch", env.job(job).claim_epoch == 1)

    with Env(m).patched() as env:
        job = env.add_job(origin="imported_weights", connection=None, ref=None)
        check(
            "C5",
            "an origin outside the allow-list is origin_not_claimable",
            expect(m, lambda: env.claim(job)) == "origin_not_claimable",
        )
        check(
            "C5",
            "the allow-list is the only gate: the same job claims when its origin is listed",
            expect(m, lambda: env.claim(job, origins=("imported_weights",))) is None,
        )
        job2 = env.add_job()
        check(
            "C5",
            "an allow-list naming other origins refuses an external_endpoint job",
            expect(m, lambda: env.claim(job2, origins=("imported_weights", "imported_adapter")))
            == "origin_not_claimable",
        )

    with Env(m).patched() as env:
        job = env.add_job(project=OTHER_PROJECT)
        check(
            "C6",
            "a job under another project is job_not_found, identical to a missing one",
            expect(m, lambda: env.claim(job)) == "job_not_found",
        )
        check(
            "C6",
            "a job under another organization is job_not_found",
            expect(m, lambda: env.claim(env.add_job(org=OTHER_ORG), org=ORG)) == "job_not_found",
        )
        check(
            "C6",
            "a missing job is job_not_found",
            expect(m, lambda: env.claim(uuid.uuid4())) == "job_not_found",
        )
        check("C6", "a refused lookup changes nothing", env.job(job).state == "queued")

    with Env(m).patched() as env:
        legacy = env.add_job(connection=None)
        check(
            "C7",
            "a legacy external_endpoint row without a connection is job_invalid",
            expect(m, lambda: env.claim(legacy)) == "job_invalid",
        )
        check(
            "C7",
            "a refused legacy row stays queued at epoch 0",
            env.job(legacy).state == "queued" and env.job(legacy).claim_epoch == 0,
        )

    with Env(m).patched() as env:
        job = env.add_job(state="queued")
        with Session(env.engine) as s:
            s.execute(text("PRAGMA ignore_check_constraints = ON"))
            row = s.get(job_schema.ModelImportJob, job)
            row.state = "validating"
            row.claim_epoch = 4
            row.lease_expires_at = None
            s.commit()
            s.execute(text("PRAGMA ignore_check_constraints = OFF"))
        check(
            "C8",
            "a validating row with no lease is treated as held, never as free",
            expect(m, lambda: env.claim(job)) == "job_already_claimed",
        )


def group_c2(m: Any) -> None:
    from sqlalchemy.dialects import postgresql

    # the claim reads the row under FOR UPDATE (checked on the PostgreSQL rendering of the statement)
    with Env(m).patched() as env:
        job = env.add_job()
        env.claim(job)
        selects = [st for st in env.statements if getattr(st, "is_select", False)]
        rendered = [str(st.compile(dialect=postgresql.dialect())) for st in selects]
        check(
            "C9",
            "the claim's SELECT is rendered FOR UPDATE on PostgreSQL",
            bool(rendered) and all("FOR UPDATE" in r for r in rendered),
        )
    # a competitor commits between this claim's read and its UPDATE: the UPDATE must match 0 rows
    for label, competitor in (
        (
            "another claim",
            lambda s, j: s.execute(
                text(
                    "UPDATE model_import_jobs SET state='validating', claim_epoch=1, lease_expires_at='2099-01-01 00:00:00.000000' WHERE id=:id"
                ),
                {"id": j.hex},
            ),
        ),
        (
            "a terminal write",
            lambda s, j: s.execute(
                text(
                    "UPDATE model_import_jobs SET state='failed', failure_code='x', claim_epoch=1 WHERE id=:id"
                ),
                {"id": j.hex},
            ),
        ),
    ):
        with Env(m).patched() as env:
            job = env.add_job()
            original = m._load_job

            def racing(
                session: Any,
                organization_id: Any,
                project_id: Any,
                job_id: Any,
                original: Any = original,
                competitor: Any = competitor,
                env: Env = env,
            ) -> Any:
                row = original(session, organization_id, project_id, job_id)
                with Session(env.engine) as other:
                    competitor(other, job_id)
                    other.commit()
                return row

            m._load_job = racing
            try:
                code = expect(m, lambda env=env, job=job: env.claim(job))
            finally:
                m._load_job = original
            row = env.job(job)
            check(
                "C10",
                f"a lost race against {label} is job_already_claimed, not a second token",
                code == "job_already_claimed",
            )
            check(
                "C10",
                f"a lost race against {label} leaves the competitor's row untouched",
                row.claim_epoch in (0, 1)
                and row.state in ("validating", "failed")
                and (row.state == "failed" or row.lease_expires_at is not None),
            )


# -- D: validate -------------------------------------------------------------------------------


def claimed(env: Env, **job_kwargs: Any) -> tuple[uuid.UUID, Any]:
    job = env.add_job(**job_kwargs)
    return job, env.claim(job)


def group_d(m: Any) -> None:
    # revoked connection: failed connection_revoked, no gateway call at all
    for label, mutate in (
        ("status revoked", lambda e: setattr(e.connection, "status", "revoked")),
        ("revoked_at set", lambda e: setattr(e.connection, "revoked_at", NOW)),
        ("status not active", lambda e: setattr(e.connection, "status", "disabled")),
        ("connection row missing", lambda e: setattr(e, "connection_missing", True)),
    ):
        with Env(m).patched() as env:
            mutate(env)
            job, token = claimed(env)
            out = env.validate(token)
            row = env.job(job)
            check(
                "D1",
                f"{label}: failed connection_revoked",
                (out.state, out.failure_code, out.model_version_id)
                == ("failed", "connection_revoked", None),
            )
            check(
                "D1",
                f"{label}: the job row is failed/connection_revoked, lease cleared",
                (row.state, row.failure_code, row.lease_expires_at, row.result_model_version_id)
                == ("failed", "connection_revoked", None, None),
            )
            check(
                "D1",
                f"{label}: no gateway call and no ModelVersion",
                not env.gateway.discover_calls
                and not env.gateway.attest_calls
                and not env.versions(),
            )

    # discovery outcomes
    for state in DiscoveryState:
        if state is DiscoveryState.DISCOVERED:
            continue
        with Env(m, Gateway(discovery_state=state)).patched() as env:
            job, token = claimed(env)
            out = env.validate(token)
            want = m.discovery_outcome(state)
            row = env.job(job)
            check(
                "D2",
                f"discovery {state.name}: {want[0]}/{want[1]}",
                (out.state, out.failure_code) == want and (row.state, row.failure_code) == want,
            )
            check(
                "D2",
                f"discovery {state.name}: attestation never attempted, no ModelVersion, never succeeded",
                not env.gateway.attest_calls and not env.versions() and row.state != "succeeded",
            )

    # attestation outcomes
    for state in RouteAttestationState:
        if state is RouteAttestationState.ATTESTED:
            continue
        with Env(m, Gateway(attestation_state=state)).patched() as env:
            job, token = claimed(env)
            out = env.validate(token)
            want = m.attestation_outcome(state, RouteIdentityState.VERIFIED, FINGERPRINT)
            check(
                "D3",
                f"attestation {state.name}: {want}",
                (out.state, out.failure_code) == want and env.job(job).state == want[0],
            )
            check("D3", f"attestation {state.name}: no ModelVersion", not env.versions())
    for label, identity, fingerprint in (
        ("unverified identity", RouteIdentityState.UNVERIFIED, FINGERPRINT),
        ("missing fingerprint header", RouteIdentityState.VERIFIED, None),
        ("blank fingerprint", RouteIdentityState.VERIFIED, " "),
        ("oversize fingerprint", RouteIdentityState.VERIFIED, "x" * 257),
    ):
        with Env(m, Gateway(identity=identity, fingerprint=fingerprint)).patched() as env:
            job, token = claimed(env)
            out = env.validate(token)
            check(
                "D4",
                f"{label}: failed route_identity_unverified with no ModelVersion",
                (out.state, out.failure_code, out.model_version_id)
                == ("failed", "route_identity_unverified", None)
                and not env.versions(),
            )

    # alias exactness: generated Unicode variants of the ref
    variants = alias_variants(REF)
    check(
        "D5",
        f"{len(variants)} generated variants of the ref are all distinct from it",
        all(v != REF for v in variants) and len(variants) >= 10,
    )
    for variant in variants:
        with Env(m, Gateway(models=(variant,))).patched() as env:
            job, token = claimed(env)
            out = env.validate(token)
            check(
                "D5",
                f"discovery listing only {variant!r} -> failed alias_not_authorized",
                (out.state, out.failure_code) == ("failed", "alias_not_authorized"),
            )
            check(
                "D5",
                f"variant {variant!r}: attestation never attempted, no ModelVersion",
                not env.gateway.attest_calls and not env.versions(),
            )
        with Env(m, Gateway(models=(REF,))).patched() as env:
            try:
                job = env.add_job(ref=variant)
            except job_schema.ModelImportValidationError:
                continue  # a control-character ref can never be stored as a job's ref
            token = env.claim(job)
            out = env.validate(token)
            check(
                "D5",
                f"a job whose ref is the variant {variant!r} is not satisfied by the exact listing",
                (out.state, out.failure_code) == ("failed", "alias_not_authorized"),
            )
    with Env(m, Gateway(models=("other", REF, "another"))).patched() as env:
        job, token = claimed(env)
        out = env.validate(token)
        check("D5", "the exact ref among other models is authorized", out.state == "succeeded")
        check(
            "D5",
            "and attestation is for the ref, not for another listed model",
            [c["model_id"] for c in env.gateway.attest_calls] == [REF],
        )
    with Env(m, Gateway(models=())).patched() as env:
        job, token = claimed(env)
        check(
            "D5",
            "an empty listing is alias_not_authorized",
            env.validate(token).failure_code == "alias_not_authorized",
        )

    # success shape
    with Env(m).patched() as env:
        job, token = claimed(env, parent=PARENT)
        out = env.validate(token)
        row = env.job(job)
        versions = env.versions()
        check(
            "D6",
            "success: outcome is succeeded with a result id and no failure code",
            out.state == "succeeded"
            and out.failure_code is None
            and out.model_version_id is not None,
        )
        check(
            "D6",
            "success: exactly one ModelVersion, and the job points at it",
            len(versions) == 1
            and row.result_model_version_id == out.model_version_id == versions[0].id,
        )
        v = versions[0]
        check(
            "D6",
            "success: endpoint-only identity (kind, origin, verbatim ref, NULL endpoint_reference)",
            (v.import_kind, v.origin, v.external_provider_model_ref, v.endpoint_reference)
            == ("endpoint", "external_endpoint", REF, None),
        )
        check(
            "D6",
            "success: the attested fingerprint is recorded",
            v.observed_deployment_fingerprint == FINGERPRINT,
        )
        check(
            "D6",
            "success: lineage is unknown, not adapter-only, with the declared parent kept as declared",
            (v.lineage_completeness, v.adapter_only, v.parent_model_version_id)
            == ("unknown", False, PARENT),
        )
        check(
            "D6",
            "success: no artifact, digest, revision, tokenizer, template or serving config",
            all(
                getattr(v, a) is None
                for a in (
                    "trainable_artifact_id",
                    "weights_digest",
                    "base_model_revision",
                    "tokenizer_revision",
                    "chat_template_sha256",
                    "serving_config_sha256",
                )
            ),
        )
        check(
            "D6",
            "success: tenant, project, version number 1 and the requester recorded",
            (v.organization_id, v.project_id, v.version_number, v.created_by)
            == (ORG, PROJECT, 1, "ml-1"),
        )
        check(
            "D6",
            "success: the job is succeeded, lease cleared, no failure code, epoch unchanged",
            (row.state, row.failure_code, row.lease_expires_at, row.claim_epoch)
            == ("succeeded", None, None, 1),
        )
        check(
            "D6",
            "success: the connection base_url is not stored as an endpoint",
            BASE_URL not in {v.endpoint_reference, v.external_provider_model_ref},
        )
        check("D7", "success: exactly one audit row", len(env.audit.events) == 1)
        ev = env.audit.events[0]
        check(
            "D7",
            "the audit row names the action, resource and the worker as actor",
            (
                ev["action"],
                ev["resource_type"],
                ev["resource_id"],
                ev["actor_type"],
                ev["actor_id"],
                ev["organization_id"],
            )
            == ("model_import.validate", "model_import_job", str(job), "system", WORKER, ORG),
        )
        check(
            "D7",
            "the audit details carry exactly the job id, origin and outcome",
            ev["details"]
            == {"job_id": str(job), "origin": "external_endpoint", "outcome": "succeeded"},
        )
        check(
            "D8",
            "the gateway was called with the connection's own base_url and secret_ref",
            env.gateway.discover_calls[0]["base_url"] == BASE_URL
            and env.gateway.discover_calls[0]["secret_ref"] == SECRET_REF
            and env.gateway.attest_calls[0]["base_url"] == BASE_URL
            and env.gateway.attest_calls[0]["secret_ref"] == SECRET_REF,
        )
        check(
            "D8",
            "attestation is for exactly the ref and approves only the ref",
            env.gateway.attest_calls[0]["model_id"] == REF
            and env.gateway.attest_calls[0]["approved_model_ids"] == frozenset({REF}),
        )
        check(
            "D8",
            "discovery and attestation each get the secret config and the injected clock",
            env.gateway.discover_calls[0]["secret_config"] is SECRET_CONFIG
            and env.gateway.attest_calls[0]["secret_config"] is SECRET_CONFIG
            and env.gateway.discover_calls[0]["now"] == NOW
            and env.gateway.attest_calls[0]["now"] == NOW,
        )
        check(
            "D8",
            "one discovery and one attestation, no more",
            len(env.gateway.discover_calls) == 1 and len(env.gateway.attest_calls) == 1,
        )
        check(
            "D9",
            "a terminal job cannot be claimed or validated again",
            expect(m, lambda: env.claim(job)) == "job_terminal"
            and expect(m, lambda: env.validate(token)) == "fenced_out",
        )
        check(
            "D9",
            "the refused repeat wrote nothing more",
            len(env.versions()) == 1 and len(env.audit.events) == 1,
        )
        check(
            "D9",
            "and did not touch the gateway again",
            len(env.gateway.discover_calls) == 1 and len(env.gateway.attest_calls) == 1,
        )

    # failure paths each write exactly one audit row with the outcome code
    with Env(m, Gateway(discovery_state=DiscoveryState.TIMEOUT)).patched() as env:
        job, token = claimed(env)
        env.validate(token)
        check(
            "D10",
            "a blocked outcome writes one audit row carrying the failure code as the outcome",
            len(env.audit.events) == 1
            and env.audit.events[0]["details"]["outcome"] == "gateway_unavailable",
        )

    # version numbers continue per project
    with Env(m).patched() as env:
        for _ in (1, 2, 3):
            job, token = claimed(env)
            env.validate(token)
        check(
            "D11",
            "version numbers continue 1, 2, 3 within the project",
            sorted(v.version_number for v in env.versions()) == [1, 2, 3],
        )
        other = env.add_job(project=OTHER_PROJECT)
        t = env.claim(other, project=OTHER_PROJECT)
        env.validate(t)
        check(
            "D11",
            "another project starts at 1",
            sorted(v.version_number for v in env.versions() if v.project_id == OTHER_PROJECT)
            == [1],
        )


def group_d2(m: Any) -> None:
    for ref in (
        "Org Agent/Small:v1",
        "  padded-ref  ",
        "MiXeD-Case_ref",
        "gpt-4o\u00a0mini",
        "\uff4f-fullwidth",
    ):
        with Env(m, Gateway(models=(ref,))).patched() as env:
            job = env.add_job(ref=ref)
            token = env.claim(job)
            out = env.validate(token)
            versions = env.versions()
            check(
                "D12",
                f"{ref!r}: stored verbatim, no case, whitespace or NFKC folding",
                out.state == "succeeded"
                and len(versions) == 1
                and versions[0].external_provider_model_ref == ref,
            )
            check(
                "D12",
                f"{ref!r}: attested under exactly that ref",
                [c["model_id"] for c in env.gateway.attest_calls] == [ref],
            )


def alias_variants(ref: str) -> list[str]:
    out = {
        ref.upper(),
        ref.title(),
        ref + " ",
        " " + ref,
        ref + "​",
        "​" + ref,
        ref.replace("-", "‐"),
        ref.replace("a", "а"),
        ref.replace("o", "ο"),
        ref + "́",
        ref.replace("-", "－"),
        unicodedata.normalize("NFKC", "ｏｒｇ") + ref[3:],
        ref.replace("g", "ġ"),
        ref[:-1],
        ref + "-",
        ref + "\n",
        ref + "\x00",
    }
    out.discard(ref)
    return sorted(out)


# -- E: fencing --------------------------------------------------------------------------------


def group_e(m: Any) -> None:
    with Env(m).patched() as env:
        job = env.add_job()
        t1 = env.claim(job, lease=60)
        t2 = env.claim(job, now=NOW + timedelta(seconds=61))
        check(
            "E1",
            "the re-claim holds epoch 2 and the old token is epoch 1",
            (t1.epoch, t2.epoch) == (1, 2),
        )
        before = env.job(job)
        check(
            "E2",
            "the old epoch's final commit is fenced_out",
            expect(m, lambda: env.validate(t1, now=NOW + timedelta(seconds=62))) == "fenced_out",
        )
        after = env.job(job)
        check(
            "E2",
            "the fenced commit wrote no ModelVersion, no audit, and left the job exactly as it was",
            not env.versions()
            and not env.audit.events
            and (
                after.state,
                after.claim_epoch,
                after.lease_expires_at,
                after.failure_code,
                after.result_model_version_id,
            )
            == (
                before.state,
                before.claim_epoch,
                before.lease_expires_at,
                before.failure_code,
                before.result_model_version_id,
            ),
        )
        out = env.validate(t2, now=NOW + timedelta(seconds=62))
        check(
            "E3",
            "the current epoch then validates normally",
            out.state == "succeeded" and len(env.versions()) == 1 and len(env.audit.events) == 1,
        )

    # taken over WHILE the gateway call is in flight: the old holder's terminal write matches 0 rows
    for label, gateway in (
        ("success", Gateway()),
        ("discovery blocked", Gateway(discovery_state=DiscoveryState.TIMEOUT)),
        ("alias not authorized", Gateway(models=("other",))),
    ):
        env = Env(m, gateway)
        with env.patched():
            job = env.add_job()
            t1 = env.claim(job, lease=60)

            fired: list[str] = []

            def take_over(
                stage: str, env: Env = env, job: uuid.UUID = job, fired: list[str] = fired
            ) -> None:
                if not fired:
                    fired.append(stage)
                    with Session(env.engine) as s:
                        row = s.get(job_schema.ModelImportJob, job)
                        row.claim_epoch = row.claim_epoch + 1
                        row.lease_expires_at = NOW + timedelta(hours=1)
                        s.commit()

            gateway.on_call = take_over
            code = expect(m, lambda env=env, t1=t1: env.validate(t1))
            row = env.job(job)
            check("E4", f"{label}: taken over mid-call -> fenced_out", code == "fenced_out")
            check(
                "E4",
                f"{label}: no ModelVersion, no audit, job still validating at the new epoch",
                not env.versions()
                and not env.audit.events
                and row.state == "validating"
                and row.claim_epoch == 2,
            )

    # not claimed at all / wrong state
    with Env(m).patched() as env:
        job = env.add_job()
        token = m.ClaimToken(ORG, PROJECT, job, 1, WORKER)
        check(
            "E5",
            "a token for a job that was never claimed is fenced_out",
            expect(m, lambda: env.validate(token)) == "fenced_out",
        )
        t = env.claim(job)
        wrong_epoch = m.ClaimToken(ORG, PROJECT, job, t.epoch + 1, WORKER)
        check(
            "E5",
            "a token with a future epoch is fenced_out",
            expect(m, lambda: env.validate(wrong_epoch)) == "fenced_out",
        )
        check(
            "E5",
            "a token with epoch 0 is fenced_out",
            expect(m, lambda: env.validate(m.ClaimToken(ORG, PROJECT, job, 0, WORKER)))
            == "fenced_out",
        )
        check(
            "E5",
            "nothing was written by the refused tokens",
            not env.versions() and not env.audit.events and env.job(job).claim_epoch == 1,
        )
        check(
            "E5",
            "a refused token never reaches the gateway",
            not env.gateway.discover_calls and not env.gateway.attest_calls,
        )

    with Env(m).patched() as env:
        job = env.add_job(origin="imported_weights", connection=None, ref=None)
        t = env.claim(job, origins=("imported_weights",))
        check(
            "E6",
            "validate_endpoint_import refuses a job whose origin is not external_endpoint",
            expect(m, lambda: env.validate(t)) == "origin_not_claimable",
        )
    with Env(m).patched() as env:
        # a row the CHECK would never let in (origin is not external_endpoint) still carrying a
        # connection and a ref: the origin test is its own gate, not a side effect of the others
        job = env.add_job(origin="imported_weights", connection=CONN, ref=REF, bypass_checks=True)
        t = env.claim(job, origins=("imported_weights",))
        check(
            "E6",
            "validate refuses a non-endpoint origin even when connection and ref are present",
            expect(m, lambda: env.validate(t)) == "origin_not_claimable",
        )
        check("E6", "and never reaches the gateway", not env.gateway.discover_calls)

    # a concurrent writer finishes the job (same epoch) while the gateway call is in flight
    with Env(m).patched() as env:
        job = env.add_job()
        t = env.claim(job)

        def finish_elsewhere(stage: str, env: Env = env, job: uuid.UUID = job) -> None:
            with Session(env.engine) as s:
                s.execute(
                    text(
                        "UPDATE model_import_jobs SET state='failed', failure_code='elsewhere', lease_expires_at=NULL WHERE id=:id"
                    ),
                    {"id": job.hex},
                )
                s.commit()

        env.gateway.on_call = finish_elsewhere
        code = expect(m, lambda: env.validate(t))
        row = env.job(job)
        check(
            "E8",
            "a job finished elsewhere at the same epoch is fenced_out, not overwritten",
            code == "fenced_out",
        )
        check(
            "E8",
            "the other writer's terminal row is untouched; no ModelVersion, no audit",
            (row.state, row.failure_code) == ("failed", "elsewhere")
            and not env.versions()
            and not env.audit.events,
        )

    with Env(m).patched() as env:
        job = env.add_job()
        wrong_project = m.ClaimToken(ORG, OTHER_PROJECT, job, 1, WORKER)
        check(
            "E7",
            "a token for another project is job_not_found",
            expect(m, lambda: env.validate(wrong_project)) == "job_not_found",
        )
        wrong_org = m.ClaimToken(OTHER_ORG, PROJECT, job, 1, WORKER)
        check(
            "E7",
            "a token for another organization is job_not_found",
            expect(m, lambda: env.validate(wrong_org)) == "job_not_found",
        )


# -- F: no DB session open during a gateway call -------------------------------------------------


def group_f(m: Any) -> None:
    with Env(m).patched() as env:
        job, token = claimed(env)
        env.validate(token)
        seen = env.gateway.sessions_open_during_call
        check("F1", "discovery and attestation were each called", len(seen) == 2)
        check(
            "F1",
            "no database session (so no row lock) was open during either gateway call",
            seen == [0, 0],
        )
        check("F1", "every session was closed afterwards", env.open_sessions == 0)


# -- G: META-LEAK ----------------------------------------------------------------------------------

# Built from parts so the markers never appear as literals in a traceback's source lines.
MARKERS = tuple("LEAK" + part for part in ("REF", "FP", "URL", "SECRET", "TOKEN"))


def exception_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc)) + repr(exc) + repr(exc.args) + repr(vars(exc))


def group_g(m: Any) -> None:
    ref = "model-LEAKREF"
    fingerprint = "fp-LEAKFP"
    base_url = "https://gw-LEAKURL.example"
    secret_ref = "openbao://secret/LEAKSECRET/key"
    scenarios: list[tuple[str, Gateway, dict[str, Any]]] = [
        ("success", Gateway(models=(ref,), fingerprint=fingerprint), {}),
        ("alias", Gateway(models=("other",), fingerprint=fingerprint), {}),
        (
            "blocked discovery",
            Gateway(models=(ref,), discovery_state=DiscoveryState.TIMEOUT, fingerprint=fingerprint),
            {},
        ),
        (
            "denied discovery",
            Gateway(
                models=(ref,), discovery_state=DiscoveryState.INVALID_TOKEN, fingerprint=fingerprint
            ),
            {},
        ),
        (
            "blocked attestation",
            Gateway(
                models=(ref,),
                attestation_state=RouteAttestationState.UNREACHABLE,
                fingerprint=fingerprint,
            ),
            {},
        ),
        (
            "unverified identity",
            Gateway(models=(ref,), identity=RouteIdentityState.UNVERIFIED, fingerprint=fingerprint),
            {},
        ),
        ("oversize fingerprint", Gateway(models=(ref,), fingerprint=fingerprint + "x" * 300), {}),
        ("revoked", Gateway(models=(ref,), fingerprint=fingerprint), {"revoked": True}),
    ]
    for label, gateway, opts in scenarios:
        with Env(m, gateway).patched() as env:
            env.connection.base_url = base_url
            env.connection.secret_ref = secret_ref
            if opts.get("revoked"):
                env.connection.status = "revoked"
            job = env.add_job(ref=ref)
            token = env.claim(job)
            out = env.validate(token)
            row = env.job(job)
            surfaces = {
                "outcome": repr(out),
                "failure_code": repr(row.failure_code),
                "audit": repr(env.audit.events),
                "token": repr(token),
            }
            for surface, text in surfaces.items():
                for marker in MARKERS:
                    check(
                        "G1", f"{label}: {marker} is absent from the {surface}", marker not in text
                    )
    # errors carry the code only
    with Env(m).patched() as env:
        env.connection.base_url = base_url
        job = env.add_job(ref=ref)
        t1 = env.claim(job)
        errors: list[BaseException] = []
        for fn in (
            lambda: env.claim(job),
            lambda: env.claim(uuid.uuid4()),
            lambda: m.claim_model_import_job(
                env.factory,
                ORG,
                PROJECT,
                job,
                origins=(MARKERS[0],),
                worker_id=MARKERS[4] + "\x00",
                lease_seconds=1,
                now=NOW,
            ),
            lambda: env.validate(m.ClaimToken(ORG, PROJECT, job, 9, MARKERS[4])),
        ):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        check("G2", "four refusals were raised", len(errors) == 4)
        for exc in errors:
            check(
                "G2",
                f"{type(exc).__name__}({exc.code!r}): the message is exactly the code",
                str(exc) == exc.code and exc.args == (exc.code,),
            )
            check(
                "G2",
                "the error has no chained cause or context",
                exc.__cause__ is None and exc.__context__ is None,
            )
            check(
                "G2",
                "no marker reaches the rendered traceback",
                not any(marker in exception_text(exc) for marker in MARKERS),
            )
        check("G2", "the token for a refused call was never issued", t1.epoch == 1)


# -- H: ModelVersion constructor + schema text -------------------------------------------------------


def group_h(m: Any, vs: Any = None) -> None:
    vs = vs or version_schema
    MV = vs.ModelVersion
    VE = vs.ModelVersionValidationError

    def build(**kw: Any) -> Any:
        base = dict(
            organization_id=ORG,
            project_id=PROJECT,
            version_number=1,
            created_by="u",
            import_kind="endpoint",
        )
        return MV(**{**base, **kw})

    def refused(**kw: Any) -> bool:
        try:
            build(**kw)
        except VE:
            return True
        except Exception:  # noqa: BLE001
            return False
        return False

    ok = build(origin="external_endpoint", external_provider_model_ref=REF)
    check(
        "H1",
        "an explicit external_endpoint with an explicit ref may have a NULL endpoint_reference",
        ok.endpoint_reference is None
        and ok.external_provider_model_ref == REF
        and ok.origin == "external_endpoint",
    )
    check("H2", "no origin (FTF-1001's own path) with no endpoint_reference is refused", refused())
    check(
        "H2",
        "no origin, only a provider ref, is refused (the ref is not derived into an origin)",
        refused(external_provider_model_ref=REF),
    )
    check(
        "H2",
        "an explicit origin but no provider ref is refused",
        refused(origin="external_endpoint"),
    )
    check(
        "H2",
        "a blank provider ref is refused",
        refused(origin="external_endpoint", external_provider_model_ref="   "),
    )
    check(
        "H2",
        "an empty provider ref is refused",
        refused(origin="external_endpoint", external_provider_model_ref=""),
    )
    check(
        "H2",
        "a NUL in the provider ref is refused",
        refused(origin="external_endpoint", external_provider_model_ref="a\x00b"),
    )
    check(
        "H2",
        "a 2049-character provider ref is refused",
        refused(origin="external_endpoint", external_provider_model_ref="x" * 2049),
    )
    check(
        "H2",
        "a non-string provider ref is refused",
        refused(origin="external_endpoint", external_provider_model_ref=7),
    )
    check(
        "H2",
        "a wrong origin with only a provider ref is refused",
        refused(origin="factory_trained", external_provider_model_ref=REF),
    )
    check(
        "H2",
        "an adapter-kind row cannot take the NULL-reference form",
        refused(import_kind="adapter", origin="external_endpoint", external_provider_model_ref=REF),
    )
    check(
        "H3",
        "an https endpoint_reference still works and is copied into the provider ref",
        build(endpoint_reference="https://models.example.com/v1").external_provider_model_ref
        == "https://models.example.com/v1",
    )
    check(
        "H3",
        "a non-https endpoint_reference is still refused",
        refused(endpoint_reference="http://models.example.com/v1"),
    )
    check(
        "H3",
        "an endpoint row still refuses a digest",
        refused(
            origin="external_endpoint",
            external_provider_model_ref=REF,
            weights_digest="sha256:" + "a" * 64,
        ),
    )
    check(
        "H3",
        "an endpoint row still refuses an artifact",
        refused(
            origin="external_endpoint",
            external_provider_model_ref=REF,
            trainable_artifact_id=uuid.uuid4(),
        ),
    )

    constraint = next(
        c for c in MV.__table__.constraints if c.name == "ck_model_versions_kind_consistency"
    )
    schema_text = re.sub(r"\s+", " ", str(constraint.sqltext))
    migration = MIGRATION_PATH.read_text()
    migration_text = "".join(
        re.findall(
            r'"([^"\n]*)"', migration.split("_KIND_CONSISTENCY_0029 = (")[1].split("\n)\n")[0]
        )
    )
    check(
        "H4",
        "the ORM CHECK text equals migration 0029's endpoint-or-alias definition",
        re.sub(r"\s+", " ", migration_text).strip() == schema_text.strip(),
    )
    check(
        "H4",
        "the CHECK allows a NULL reference only for external_endpoint with a provider ref",
        "endpoint_reference IS NULL AND origin = 'external_endpoint' AND external_provider_model_ref IS NOT NULL"
        in schema_text,
    )


# -- R: routes (the new POST field, the new response fields) over real HTTP, DB seams replaced ----

R_ORG = uuid.UUID("abababab-0000-4000-8000-00000000000a")
R_PROJECT = uuid.UUID("bcbcbcbc-0000-4000-8000-00000000000b")
R_JOB = uuid.UUID("cdcdcdcd-0000-4000-8000-00000000000c")
R_CONN = uuid.UUID("dedededa-0000-4000-8000-00000000000d")
R_RESULT = uuid.UUID("efefefef-0000-4000-8000-00000000000e")


class RouteEnv:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.create_error: Exception | None = None
        self.job: Any = None

    def create(self, session: Any, ctx: Any, **kwargs: Any) -> Any:
        self.create_calls.append(kwargs)
        if self.create_error is not None:
            raise self.create_error
        return self.job

    def get(self, session: Any, ctx: Any, project_id: Any, job_id: Any) -> Any:
        return self.job

    def list_jobs(self, session: Any, ctx: Any, project_id: Any) -> Any:
        return [self.job]


def route_job(**override: Any) -> Any:
    fields: dict[str, Any] = dict(
        id=R_JOB,
        organization_id=R_ORG,
        project_id=R_PROJECT,
        requested_origin="external_endpoint",
        parent_model_version_id=None,
        trainable_artifact_id=None,
        external_provider_model_ref=REF,
        litellm_connection_id=R_CONN,
        result_model_version_id=None,
        state="queued",
        failure_code=None,
        created_by="ml-1",
        created_at=NOW,
        updated_at=NOW,
    )
    fields.update(override)
    return SimpleNamespace(**fields)


def group_r(mir: Any = None) -> None:
    import http.client
    import json
    import socket
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI

    import app.model_versions.model_import_routes as real_mir
    from app.auth.rbac_middleware import AuthenticatedPrincipal, Role

    mir = mir or real_mir
    principals = {
        "ml": AuthenticatedPrincipal(issuer="https://issuer.example", subject="ml-1"),
        "viewer": AuthenticatedPrincipal(issuer="https://issuer.example", subject="viewer-1"),
    }
    roles = {"ml-1": Role.ML_ENGINEER, "viewer-1": Role.VIEWER}

    def resolver(request: Any) -> Any:
        return principals.get(request.headers.get("x-test-principal", ""))

    def role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Any:
        return roles.get(subject) if organization_id == R_ORG else None

    @contextlib.contextmanager
    def session_cm(session_factory: Any, tenant_context: Any):
        yield object()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    app = FastAPI()
    app.include_router(
        mir.build_model_import_router(
            lambda: None, principal_resolver=resolver, role_lookup=role_lookup
        )
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.02)
    env = RouteEnv()
    names = {
        "tenant_scoped_session_for": session_cm,
        "create_model_import_job": env.create,
        "get_model_import_job": env.get,
        "list_model_import_jobs": env.list_jobs,
    }
    saved = {name: getattr(mir, name) for name in names}
    for name, value in names.items():
        setattr(mir, name, value)

    collection = f"/api/v1/organizations/{R_ORG}/projects/{R_PROJECT}/model-imports"

    def call(
        method: str, path: str, payload: Any = None, principal: str | None = "ml"
    ) -> tuple[int, Any]:
        headers = {"Content-Type": "application/json"}
        if principal is not None:
            headers["X-Test-Principal"] = principal
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request(
                method,
                path,
                body=None if payload is None else json.dumps(payload).encode(),
                headers=headers,
            )
            response = conn.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
        finally:
            conn.close()

    try:
        env.job = route_job()
        endpoint = {"requested_origin": "external_endpoint", "external_provider_model_ref": REF}
        wrong_shapes: list[tuple[str, Any]] = [
            ("an int", 7),
            ("a bool", True),
            ("a list", []),
            ("a dict", {}),
            ("a non-uuid string", "nope"),
            ("an empty string", ""),
            ("a float", 1.5),
        ]
        for label, value in wrong_shapes:
            env.create_calls.clear()
            status, body = call("POST", collection, {**endpoint, "litellm_connection_id": value})
            check(
                "R1",
                f"external_endpoint with litellm_connection_id as {label} -> 400 invalid_body",
                status == 400 and body == {"detail": "invalid_body"},
            )
            check("R1", f"{label}: nothing reached the store", not env.create_calls)
        for label, payload in (
            ("missing", endpoint),
            ("null", {**endpoint, "litellm_connection_id": None}),
        ):
            env.create_calls.clear()
            status, body = call("POST", collection, payload)
            check(
                "R2",
                f"external_endpoint with litellm_connection_id {label} -> 400 invalid_body",
                status == 400 and body == {"detail": "invalid_body"} and not env.create_calls,
            )
        for origin in ("imported_weights", "imported_adapter", "factory_trained", "made_up"):
            env.create_calls.clear()
            status, body = call(
                "POST",
                collection,
                {"requested_origin": origin, "litellm_connection_id": str(R_CONN)},
            )
            check(
                "R3",
                f"{origin} with a litellm_connection_id -> 400 invalid_body",
                status == 400 and body == {"detail": "invalid_body"} and not env.create_calls,
            )
        for label, extra in (("missing", {}), ("null", {"litellm_connection_id": None})):
            env.create_calls.clear()
            env.job = route_job(
                requested_origin="imported_weights",
                litellm_connection_id=None,
                external_provider_model_ref=None,
            )
            status, body = call(
                "POST", collection, {"requested_origin": "imported_weights", **extra}
            )
            check(
                "R4",
                f"imported_weights with litellm_connection_id {label} reaches the store with None",
                status == 201
                and len(env.create_calls) == 1
                and env.create_calls[0]["litellm_connection_id"] is None,
            )
        env.job = route_job()
        env.create_calls.clear()
        status, body = call("POST", collection, {**endpoint, "litellm_connection_id": str(R_CONN)})
        check(
            "R5",
            "a valid external_endpoint request is 201 and passes the parsed connection UUID",
            status == 201
            and len(env.create_calls) == 1
            and env.create_calls[0]["litellm_connection_id"] == R_CONN
            and env.create_calls[0]["external_provider_model_ref"] == REF,
        )
        check(
            "R5",
            "the 201 body carries litellm_connection_id and result_model_version_id",
            body is not None
            and body["litellm_connection_id"] == str(R_CONN)
            and body["result_model_version_id"] is None,
        )
        check(
            "R5",
            "the 201 body keeps every earlier field",
            {
                "id",
                "organization_id",
                "project_id",
                "requested_origin",
                "parent_model_version_id",
                "trainable_artifact_id",
                "external_provider_model_ref",
                "state",
                "failure_code",
                "created_by",
                "created_at",
                "updated_at",
            }
            <= set(body),
        )
        check(
            "R5",
            "the response contract has exactly the earlier fields plus the two new ones (no claim_epoch, no lease)",
            set(body)
            == {
                "id",
                "organization_id",
                "project_id",
                "requested_origin",
                "parent_model_version_id",
                "trainable_artifact_id",
                "external_provider_model_ref",
                "litellm_connection_id",
                "result_model_version_id",
                "state",
                "failure_code",
                "created_by",
                "created_at",
                "updated_at",
            },
        )
        env.job = route_job(state="succeeded", result_model_version_id=R_RESULT)
        status, body = call("GET", f"{collection}/{R_JOB}")
        check(
            "R6",
            "GET shows the succeeded job's result id",
            status == 200
            and body["result_model_version_id"] == str(R_RESULT)
            and body["litellm_connection_id"] == str(R_CONN)
            and body["state"] == "succeeded",
        )
        status, body = call("GET", collection)
        check(
            "R6",
            "the list shows the same two fields",
            status == 200
            and body[0]["result_model_version_id"] == str(R_RESULT)
            and body[0]["litellm_connection_id"] == str(R_CONN),
        )
        env.create_calls.clear()
        env.create_error = mir.ConnectionNotFoundError("foreign")
        status, body = call(
            "POST", collection, {**endpoint, "litellm_connection_id": str(uuid.uuid4())}
        )
        check(
            "R7",
            "a foreign or missing connection is 404 not_found",
            status == 404 and body == {"detail": "not_found"},
        )
        env.create_error = None
        status, body = call(
            "POST", collection, {**endpoint, "litellm_connection_id": str(R_CONN), "claim_epoch": 5}
        )
        check(
            "R8",
            "claim_epoch is not an accepted body key",
            status == 400 and "claim_epoch" in body["detail"],
        )
        status, body = call(
            "POST",
            collection,
            {**endpoint, "litellm_connection_id": str(R_CONN)},
            principal="viewer",
        )
        check("R8", "a viewer cannot POST (403)", status == 403)
    finally:
        for name, value in saved.items():
            setattr(mir, name, value)
        server.should_exit = True
        thread.join(timeout=5)


# -- faults ----------------------------------------------------------------------------------------

FAULTS: list[tuple[str, str, str, int]] = [
    (
        "discovery-invalid-token-blocked",
        "    if state is DiscoveryState.INVALID_TOKEN:\n        return STATE_FAILED, CODE_GATEWAY_ACCESS_DENIED",
        "    if False:\n        return STATE_FAILED, CODE_GATEWAY_ACCESS_DENIED",
        1,
    ),
    (
        "discovery-every-state-passes",
        "    if state is DiscoveryState.DISCOVERED:\n        return None",
        "    if True:\n        return None",
        1,
    ),
    (
        "discovery-outage-failed-not-blocked",
        "    return STATE_BLOCKED_EXTERNAL, CODE_GATEWAY_UNAVAILABLE",
        "    return STATE_FAILED, CODE_GATEWAY_UNAVAILABLE",
        1,
    ),
    (
        "attestation-invalid-token-blocked",
        "    if state is RouteAttestationState.INVALID_TOKEN:\n        return STATE_FAILED, CODE_GATEWAY_ACCESS_DENIED",
        "    if False:\n        return STATE_FAILED, CODE_GATEWAY_ACCESS_DENIED",
        1,
    ),
    (
        "attestation-not-attested-passes",
        "    if state is not RouteAttestationState.ATTESTED:",
        "    if False:",
        1,
    ),
    (
        "attestation-unverified-succeeds",
        "    if identity is not RouteIdentityState.VERIFIED or not _usable_fingerprint(fingerprint):",
        "    if not _usable_fingerprint(fingerprint):",
        1,
    ),
    (
        "attestation-null-fingerprint-succeeds",
        "    if identity is not RouteIdentityState.VERIFIED or not _usable_fingerprint(fingerprint):",
        "    if identity is not RouteIdentityState.VERIFIED:",
        1,
    ),
    (
        "fingerprint-length-unbounded",
        "and 0 < len(value) <= _FINGERPRINT_MAX_LENGTH",
        "and 0 < len(value)",
        1,
    ),
    (
        "fingerprint-control-chars-allowed",
        'and not any(unicodedata.category(ch) in ("Cc", "Cs") for ch in value)\n        and not value.isspace()',
        "and not value.isspace()",
        1,
    ),
    ("fingerprint-blank-allowed", "\n        and not value.isspace()\n    )", "\n    )", 1),
    (
        "alias-casefold",
        "model.id == snapshot.ref for model in discovery.models",
        "model.id.casefold() == snapshot.ref.casefold() for model in discovery.models",
        1,
    ),
    (
        "alias-nfkc",
        "model.id == snapshot.ref for model in discovery.models",
        'unicodedata.normalize("NFKC", model.id) == unicodedata.normalize("NFKC", snapshot.ref) for model in discovery.models',
        1,
    ),
    (
        "alias-strip",
        "model.id == snapshot.ref for model in discovery.models",
        "model.id.strip() == snapshot.ref.strip() for model in discovery.models",
        1,
    ),
    (
        "alias-membership-skipped",
        "    if not any(model.id == snapshot.ref for model in discovery.models):",
        "    if False:",
        1,
    ),
    (
        "attest-model-not-the-ref",
        "        model_id=snapshot.ref,\n",
        "        model_id=discovery.models[0].id,\n",
        1,
    ),
    (
        "attest-approval-widened",
        "        approved_model_ids=frozenset({snapshot.ref}),",
        "        approved_model_ids=frozenset({snapshot.ref, 'other'}),",
        1,
    ),
    ("lineage-partial", 'lineage_completeness="unknown",', 'lineage_completeness="partial",', 1),
    ("lineage-complete", 'lineage_completeness="unknown",', 'lineage_completeness="complete",', 1),
    (
        "fingerprint-dropped",
        "observed_deployment_fingerprint=fingerprint,",
        "observed_deployment_fingerprint=None,",
        1,
    ),
    (
        "parent-dropped",
        "parent_model_version_id=snapshot.parent_model_version_id,",
        "parent_model_version_id=None,",
        1,
    ),
    (
        "ref-not-verbatim",
        "external_provider_model_ref=snapshot.ref,",
        "external_provider_model_ref=snapshot.ref.strip().lower(),",
        1,
    ),
    (
        "created-by-dropped-to-worker",
        "created_by=snapshot.created_by,",
        "created_by=token.worker_id,",
        1,
    ),
    (
        "version-number-fixed",
        "version_number=_next_version_number(session, context, token.project_id),",
        "version_number=1,",
        1,
    ),
    (
        "result-link-dropped",
        "result_model_version_id=model_version_id,",
        "result_model_version_id=None,",
        1,
    ),
    (
        "lease-not-cleared",
        "                    lease_expires_at=None,\n",
        "                    lease_expires_at=now,\n",
        1,
    ),
    (
        "final-update-epoch-condition-removed",
        "                    ModelImportJob.claim_epoch == token.epoch,\n",
        "",
        1,
    ),
    (
        "final-update-state-condition-removed",
        '                    ModelImportJob.state == "validating",\n',
        "",
        1,
    ),
    (
        "fenced-write-kept",
        "            if result.rowcount != 1:\n                raise _Fenced",
        "            if False:\n                raise _Fenced",
        1,
    ),
    (
        "fenced-reported-as-other-code",
        'raise ModelImportActivityError("fenced_out") from None',
        'raise ModelImportActivityError("job_already_claimed") from None',
        1,
    ),
    (
        "early-fence-epoch-dropped",
        'if job.state != "validating" or job.claim_epoch != token.epoch:',
        'if job.state != "validating":',
        1,
    ),
    (
        "early-fence-state-dropped",
        'if job.state != "validating" or job.claim_epoch != token.epoch:',
        "if job.claim_epoch != token.epoch:",
        1,
    ),
    (
        "validate-origin-unchecked",
        "            job.requested_origin != ORIGIN_EXTERNAL_ENDPOINT\n            or job.litellm_connection_id is None",
        "            job.litellm_connection_id is None",
        1,
    ),
    (
        "revoked-ignored",
        "    if connection is None or connection.revoked:",
        "    if connection is None:",
        1,
    ),
    (
        "missing-connection-ignored",
        "    if connection is None or connection.revoked:",
        "    if connection is not None and connection.revoked:",
        1,
    ),
    (
        "revoked-status-only",
        'revoked=connection.status != "active" or connection.revoked_at is not None,',
        'revoked=connection.status == "revoked",',
        1,
    ),
    (
        "revoked-at-ignored",
        'revoked=connection.status != "active" or connection.revoked_at is not None,',
        'revoked=connection.status != "active",',
        1,
    ),
    (
        "audit-dropped",
        "            write_audit_event(\n                session,",
        "            (lambda *a, **k: None)(\n                session,",
        1,
    ),
    (
        "audit-leaks-ref",
        '"outcome": state if failure_code is None else failure_code,',
        '"outcome": state if failure_code is None else failure_code,\n                    "ref": snapshot.ref,',
        1,
    ),
    (
        "audit-outcome-always-state",
        '"outcome": state if failure_code is None else failure_code,',
        '"outcome": state,',
        1,
    ),
    ("audit-actor-not-worker", "actor_id=token.worker_id,", "actor_id=snapshot.created_by,", 1),
    (
        "audit-origin-wrong",
        '"origin": ORIGIN_EXTERNAL_ENDPOINT,',
        '"origin": "imported_weights",',
        1,
    ),
    (
        "claim-live-lease-ignored",
        '        if job.state == "validating" and (\n            job.lease_expires_at is None or job.lease_expires_at > now\n        ):',
        "        if False:",
        1,
    ),
    (
        "claim-expiry-inclusive",
        "job.lease_expires_at is None or job.lease_expires_at > now",
        "job.lease_expires_at is None or job.lease_expires_at >= now",
        1,
    ),
    (
        "claim-no-lease-treated-free",
        "job.lease_expires_at is None or job.lease_expires_at > now",
        "job.lease_expires_at is not None and job.lease_expires_at > now",
        1,
    ),
    (
        "claim-epoch-not-incremented",
        "        epoch = job.claim_epoch + 1",
        "        epoch = job.claim_epoch",
        1,
    ),
    (
        "claim-epoch-skips",
        "        epoch = job.claim_epoch + 1",
        "        epoch = job.claim_epoch + 2",
        1,
    ),
    ("claim-terminal-allowed", "        if job.state in _TERMINAL_STATES:", "        if False:", 1),
    (
        "claim-origin-allow-list-ignored",
        "        if job.requested_origin not in allowed:",
        "        if False:",
        1,
    ),
    (
        "claim-legacy-row-claimed",
        "        if job.requested_origin == ORIGIN_EXTERNAL_ENDPOINT and job.litellm_connection_id is None:",
        "        if False:",
        1,
    ),
    (
        "claim-lease-not-set",
        "claim_epoch=epoch, lease_expires_at=lease, updated_at=now",
        "claim_epoch=epoch, lease_expires_at=None, updated_at=now",
        1,
    ),
    (
        "claim-lease-from-wallclock",
        "    return now + timedelta(seconds=lease_seconds)",
        "    return datetime.now(UTC) + timedelta(seconds=lease_seconds)",
        1,
    ),
    (
        "claim-update-unconditional",
        "                ModelImportJob.claim_epoch == job.claim_epoch,\n                ModelImportJob.state == job.state,\n",
        "",
        1,
    ),
    (
        "claim-rowcount-ignored",
        '        if result.rowcount != 1:\n            raise ModelImportActivityError("job_already_claimed")',
        '        if False:\n            raise ModelImportActivityError("job_already_claimed")',
        1,
    ),
    (
        "load-project-unfiltered",
        "            ModelImportJob.project_id == project_id,\n        )\n        .with_for_update()",
        "        )\n        .with_for_update()",
        1,
    ),
    (
        "load-organization-unfiltered",
        "            ModelImportJob.organization_id == organization_id,\n            ModelImportJob.project_id == project_id,\n        )\n        .with_for_update()",
        "            ModelImportJob.project_id == project_id,\n        )\n        .with_for_update()",
        1,
    ),
    ("load-not-locked", "        .with_for_update()\n", "", 1),
    (
        "not-found-skipped",
        '        if job is None:\n            raise ModelImportActivityError("job_not_found")\n        if job.requested_origin not in allowed:',
        "        if job.requested_origin not in allowed:",
        1,
    ),
    (
        "lease-bound-removed",
        "        or not 0 < lease_seconds <= LEASE_SECONDS_MAX",
        "        or not 0 < lease_seconds",
        1,
    ),
    (
        "lease-bool-accepted",
        "        isinstance(lease_seconds, bool)\n        or not isinstance(lease_seconds, int)",
        "        not isinstance(lease_seconds, int)",
        1,
    ),
    (
        "now-naive-accepted",
        "    if not isinstance(value, datetime) or value.tzinfo is None:",
        "    if not isinstance(value, datetime):",
        1,
    ),
    (
        "worker-blank-accepted",
        "        or value.isspace()\n        or len(value) > _WORKER_ID_MAX_LENGTH",
        "        or len(value) > _WORKER_ID_MAX_LENGTH",
        1,
    ),
    ("worker-length-unbounded", "        or len(value) > _WORKER_ID_MAX_LENGTH\n", "\n", 1),
    (
        "origins-unknown-accepted",
        "    if not known or known != origins:",
        "    if not known:",
        1,
    ),
    (
        "origins-unhashable-leaks-typeerror",
        '    except TypeError:\n        raise ModelImportActivityError("invalid_argument") from None\n    known',
        '    except ZeroDivisionError:\n        raise ModelImportActivityError("invalid_argument") from None\n    known',
        1,
    ),
    (
        "token-type-unchecked",
        '    if not isinstance(token, ClaimToken):\n        raise ModelImportActivityError("invalid_argument")\n',
        "",
        1,
    ),
    (
        "uuid-unchecked",
        '    if not isinstance(value, uuid.UUID):\n        raise ModelImportActivityError("invalid_argument")\n    return value',
        "    return value",
        1,
    ),
]

SCHEMA_FAULTS: list[tuple[str, str, str, int]] = [
    ("null-ref-without-origin", 'if kwargs.get("origin") != "external_endpoint":', "if False:", 1),
    (
        "null-ref-without-explicit-ref",
        "    validate_external_provider_model_ref,\n)",
        ")\n\n\ndef validate_external_provider_model_ref(value):\n    return value\n",
        1,
    ),
    (
        "check-widened-to-any-endpoint",
        "OR (endpoint_reference IS NULL AND origin = 'external_endpoint' ",
        "OR (endpoint_reference IS NULL AND true ",
        1,
    ),
    (
        "check-drops-ref-requirement",
        "AND external_provider_model_ref IS NOT NULL))) ",
        "AND true))) ",
        1,
    ),
]


ROUTES_PATH = ROOT / "apps" / "api" / "app" / "model_versions" / "model_import_routes.py"
ROUTE_FAULTS: list[tuple[str, str, str, int]] = [
    (
        "route-body-key-not-allowed",
        '        "external_provider_model_ref",\n        "litellm_connection_id",\n    }',
        '        "external_provider_model_ref",\n    }',
        1,
    ),
    (
        "route-connection-mismatch-allowed",
        'if (payload["requested_origin"] == "external_endpoint") != (connection is not None):',
        "if False:",
        1,
    ),
    (
        "route-connection-type-unchecked",
        "if connection is not None and not isinstance(connection, str):",
        "if False:",
        1,
    ),
    (
        "route-connection-uuid-error-500",
        '    except ValueError as exc:\n        raise HTTPException(status_code=400, detail="invalid_body") from exc',
        '    except KeyError as exc:\n        raise HTTPException(status_code=400, detail="invalid_body") from exc',
        1,
    ),
    (
        "route-connection-null-parsed",
        '    raw = payload.get("litellm_connection_id")\n    if raw is None:\n        return None',
        '    raw = payload.get("litellm_connection_id")\n    if raw is None:\n        raw = str(uuid.uuid4())',
        1,
    ),
    (
        "route-response-drops-connection",
        '        "litellm_connection_id": (\n            None if job.litellm_connection_id is None else str(job.litellm_connection_id)\n        ),\n',
        "",
        1,
    ),
    (
        "route-response-drops-result",
        '        "result_model_version_id": (\n            None if job.result_model_version_id is None else str(job.result_model_version_id)\n        ),\n',
        "",
        1,
    ),
    (
        "route-response-result-wrong",
        "None if job.result_model_version_id is None else str(job.result_model_version_id)",
        "None if job.result_model_version_id is None else str(job.id)",
        1,
    ),
    (
        "route-connection-not-found-500",
        "            ArtifactNotFoundError,\n            ConnectionNotFoundError,\n            TenantContextError,",
        "            ArtifactNotFoundError,\n            TenantContextError,",
        1,
    ),
    (
        "route-connection-not-passed",
        "                    litellm_connection_id=litellm_connection_id,\n                )",
        "                    litellm_connection_id=None,\n                )",
        1,
    ),
    (
        "route-connection-not-parsed",
        "        litellm_connection_id = _parse_connection_id(payload)\n",
        "        litellm_connection_id = None\n",
        1,
    ),
]


def suite(m: Any, with_routes: bool = False) -> list[str]:
    _failures.clear()
    group_a(m)
    group_b(m)
    group_c(m)
    group_c2(m)
    group_d(m)
    group_d2(m)
    group_e(m)
    group_f(m)
    group_g(m)
    group_h(m)
    if with_routes:
        group_r()
    return list(_failures)


def run_fault(source: str, name: str, old: str, new: str, count: int) -> tuple[bool, str]:
    if source.count(old) != count:
        return False, f"patch text occurs {source.count(old)} time(s), expected {count}"
    mutated = source.replace(old, new)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mutant.py"
        path.write_text(mutated)
        try:
            mm = load_module(path, f"model_import_validation_fault_{abs(hash(name))}")
        except BaseException as exc:  # noqa: BLE001
            return False, f"mutant does not import ({type(exc).__name__})"
        try:
            red = suite(mm)
        except BaseException as exc:  # noqa: BLE001 - a crashing suite is also RED
            red = [f"crash {type(exc).__name__}: {exc}"]
    if not red:
        return False, "suite stayed GREEN"
    return True, f"{len(red)} failing check(s), e.g. {red[0][:110]}"


def run_schema_fault(
    source: str, name: str, old: str, new: str, count: int, m: Any
) -> tuple[bool, str]:
    if source.count(old) != count:
        return False, f"patch text occurs {source.count(old)} time(s), expected {count}"
    # The mutant gets its own declarative Base so its Table does not collide with the real one.
    mutated = source.replace(old, new).replace(
        "from app.auth.organization_workspace_schema import Base\n",
        "from sqlalchemy.orm import declarative_base\n\nBase = declarative_base()\n",
        1,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mutant_schema.py"
        path.write_text(mutated)
        try:
            vs = load_module(path, f"model_version_schema_fault_{abs(hash(name))}")
        except BaseException as exc:  # noqa: BLE001
            return False, f"mutant does not import ({type(exc).__name__}: {exc})"
        _failures.clear()
        try:
            group_h(m, vs)
            red = list(_failures)
        except BaseException as exc:  # noqa: BLE001
            red = [f"crash {type(exc).__name__}: {exc}"]
    if not red:
        return False, "suite stayed GREEN"
    return True, f"{len(red)} failing check(s), e.g. {red[0][:110]}"


def run_route_fault(source: str, name: str, old: str, new: str, count: int) -> tuple[bool, str]:
    if source.count(old) != count:
        return False, f"patch text occurs {source.count(old)} time(s), expected {count}"
    mutated = source.replace(old, new)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mutant_routes.py"
        path.write_text(mutated)
        try:
            mm = load_module(path, f"model_import_routes_fault_{abs(hash(name))}")
        except BaseException as exc:  # noqa: BLE001
            return False, f"mutant does not import ({type(exc).__name__}: {exc})"
        _failures.clear()
        try:
            group_r(mm)
            red = list(_failures)
        except BaseException as exc:  # noqa: BLE001
            red = [f"crash {type(exc).__name__}: {exc}"]
    if not red:
        return False, "suite stayed GREEN"
    return True, f"{len(red)} failing check(s), e.g. {red[0][:110]}"


SCHEMA_PATH = ROOT / "apps" / "api" / "app" / "model_versions" / "model_version_schema.py"


def main() -> int:
    if not MODULE_PATH.exists():
        print(f"FAIL [{CID}] module missing: {MODULE_PATH}")
        return 1
    source = MODULE_PATH.read_text()
    m = load_module(MODULE_PATH, "model_import_validation_real")
    failures = suite(m, with_routes=True)
    names = [f[0] for f in FAULTS] + [f[0] for f in SCHEMA_FAULTS] + [f[0] for f in ROUTE_FAULTS]
    if len(set(names)) != len(names):
        failures.append("FAIL [I1] duplicate fault names")
    ran = 0
    for name, old, new, count in FAULTS:
        if count == 0:
            continue
        ok, detail = run_fault(source, name, old, new, count)
        ran += 1
        if ok:
            print(f"  fault {name}: RED ({detail})")
        else:
            failures.append(f"FAIL [I1] fault {name}: {detail}")
    schema_source = SCHEMA_PATH.read_text()
    for name, old, new, count in SCHEMA_FAULTS:
        ok, detail = run_schema_fault(schema_source, name, old, new, count, m)
        ran += 1
        if ok:
            print(f"  fault {name}: RED ({detail})")
        else:
            failures.append(f"FAIL [I1] fault {name}: {detail}")
    route_source = ROUTES_PATH.read_text()
    for name, old, new, count in ROUTE_FAULTS:
        ok, detail = run_route_fault(route_source, name, old, new, count)
        ran += 1
        if ok:
            print(f"  fault {name}: RED ({detail})")
        else:
            failures.append(f"FAIL [I1] fault {name}: {detail}")
    if failures:
        print(f"{len(failures)} failing check(s):")
        for failure in failures[:80]:
            print(f"  {failure}")
        return 1
    print(
        f"OK -- {CHECK_COUNT[0]} checks in {ran + 1} suite runs, {ran} faults RED, 0 failing ({CID})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
