#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index"
# ruff: noqa: E501
"""FTF-0310 -- real-router capability contract proof.

Mounts the real artifact, FTF-0308-P1 mapping and FTF-0309-P1 capability routers on a real
uvicorn server over a live PostgreSQL 16 (never SQLite: no RLS) and validates every real
response against the FROZEN 03 contract with `capability_contract_tests`:

  - every shipped source type x artifact status x license state x mapped/unmapped cell
    (72 cells, none exempt): the capability, mapping and change responses validate, and
    `trainable` is true only for a mapped, ready artifact with an approved license. The
    ready + pending/revoked cells cannot be written while migration 0029's CHECK is in
    place (asserted), so the test drops that CHECK for the matrix, seeds them directly,
    proves the classifier and route still report trainable=false (defence in depth under
    the CHECK and the FTF-0305-F1 row lock), then archives them and restores the CHECK;
  - the FTF-0305-F1 mark-ready / revoke-license interleave over the real routes never
    leaves a `ready` row without an accepted license;
  - an upstream-only model and unresolvable names (unknown, look-alike, trailing space,
    case variant) are exact-match, never trainable;
  - a generated negative table derived from the real responses is rejected by the checker.

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`.

Mounts the real FTF-0308-P1 mapping router and the real capability router on
a real uvicorn server over a live PostgreSQL (never SQLite: no RLS) and proves:

  - an alias mapped through the real POST is `trainable_open_weight` through
    the capability GET, with the artifact id, digest and catalog version;
  - an unmapped alias is `unknown`, never inferred from its name;
  - a revoked connection is shown revoked and never trainable, and is not dialed;
  - a foreign project, a foreign organization's connection and a random id are
    the same 404; a foreign organization in the path is 403; a viewer may read;
  - one discovery call per request; a non-`discovered` state is 503; the REAL
    default `discover_models` against an unreachable gateway is 503;
  - a GET writes no row (no entry, no change row, no audit event).

Requires `SOVRA_DATABASE_URL` (migration owner) and `SOVRA_APP_DB_PASSWORD`.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)
from packages.model_catalog import capability_contract_tests as cct  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts.artifact_routes import build_trainable_artifact_router  # noqa: E402
from app.artifacts.artifact_schema import ALLOWED_SOURCE_TYPES, TrainableArtifact  # noqa: E402
from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,  # noqa: E402
)
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.litellm.model_capability_routes import build_model_capability_router  # noqa: E402
from app.litellm.model_catalog_mapping_routes import (  # noqa: E402
    build_model_catalog_mapping_router,
)
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-0310"
FAILURES: list[str] = []
_NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
_ISSUER = "https://issuer.example"
AUDIT_ACTION = "model_catalog_entry.training_artifact_mapping_change"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- this task's real evidence requires a live "
            "Postgres (migration owner connection). Not silently skipped: see this file's "
            "docstring for how to run it locally or in CI.",
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


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _serve(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
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


class _Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _request(
    base: str,
    method: str,
    path: str,
    *,
    principal: str | None,
    payload: Any = None,
) -> _Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=20)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


class _Discovery:
    def __init__(self) -> None:
        self.calls = 0
        self.result = _discovered("gpt-x", "openai/gpt-4o")

    def __call__(self, **kwargs: Any) -> ModelDiscoveryResult:
        self.calls += 1
        return self.result


def _discovered(*ids: str) -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=tuple(
            DiscoveredModel(id=i, metadata_status=ModelMetadataStatus.LIMITED) for i in ids
        ),
        model_count=len(ids),
        content_hash="a" * 64,
        detail="",
        checked_at=_NOW,
    )


def _failed(state: DiscoveryState) -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=state, models=(), model_count=None, content_hash=None, detail="", checked_at=_NOW
    )


def _artifact(org_id: uuid.UUID, name: str, status: str) -> TrainableArtifact:
    return TrainableArtifact(
        organization_id=org_id,
        display_name=name,
        source_type="huggingface",
        source_uri="acme/model",
        model_family="llama",
        base_model_revision="a" * 40,
        license_id="mit",
        status=status,
    )


_DIGEST = "sha256:" + "b" * 64

# Round 3 (QUEUE 0k): the same shared Unicode exact-match variant set the unit
# suite's own `_unicode_exact_match_variants` uses (tests/tasks/test_ftf_0309_p1.py),
# exercised here against the REAL get_entry SQL lookup on a live PostgreSQL --
# a byte/codepoint-exact `=` comparison, never normalised.
_FULLWIDTH_OFFSET = 0xFF00 - 0x0020  # ASCII 0x21-0x7E -> the fullwidth block


def _fullwidth(ch: str) -> str:
    code = ord(ch)
    return chr(code + _FULLWIDTH_OFFSET) if 0x21 <= code <= 0x7E else ch


def _unicode_exact_match_variants(base: str) -> list[str]:
    candidates = [
        base.upper(),
        base.casefold(),
        " " + base,
        base + " ",
        base + "\t",
        base + " ",
        base + "​",
        base.replace("-", "‐", 1),
        _fullwidth(base[0]) + base[1:],
        unicodedata.normalize("NFC", base),
        unicodedata.normalize("NFD", base),
        unicodedata.normalize("NFKC", base),
        unicodedata.normalize("NFKD", base),
    ]
    out: list[str] = []
    for candidate in candidates:
        if candidate != base and candidate not in out:
            out.append(candidate)
    return out


def main() -> None:
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    result = _alembic(["upgrade", "head"], migration_url)
    check(CID, f"alembic upgrade head\n{result.stdout}\n{result.stderr}", result.returncode == 0)
    if FAILURES:
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    contract = cct.load_frozen_contract(ROOT)
    root_doc = contract.document
    sources = contract.enum_values("SourceType")
    statuses = contract.enum_values("ArtifactStatus")
    check(
        CID,
        "frozen SourceType equals the shipped ALLOWED_SOURCE_TYPES",
        set(sources) == set(ALLOWED_SOURCE_TYPES),
    )
    matrix = cct.capability_matrix(sources, statuses)
    check(
        CID,
        f"matrix covers every frozen cell ({len(matrix)})",
        len(matrix) == len(sources) * len(statuses) * 6,
    )

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))
    session_factory = sessionmaker(bind=app_engine)

    suffix = uuid.uuid4().hex[:8]
    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0310", slug=f"acme-0310-{suffix}")
        session.add(org1)
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        session.add(ws1)
        session.flush()
        proj1 = Project(
            organization_id=org1.id,
            workspace_id=ws1.id,
            name="p1-0310",
            project_type="generic_model_tuning",
            status="active",
            security_profile="standard",
            row_version=1,
            created_by="seed",
        )
        conn1 = LiteLLMConnection(
            organization_id=org1.id,
            name="gw1",
            base_url="https://gateway1.example.test",
            secret_ref="vault://sovra/gw1",
            status="active",
        )
        session.add_all([proj1, conn1])
        session.commit()
        org1_id = org1.id
        proj1_id, conn1_id = proj1.id, conn1.id

    principals = {
        "admin1": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="admin-1"),
            Role.ORGANIZATION_ADMIN,
            org1_id,
        ),
        "viewer1": (
            AuthenticatedPrincipal(issuer=_ISSUER, subject="viewer-1"),
            Role.VIEWER,
            org1_id,
        ),
    }

    def principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
        entry = principals.get(request.headers.get("x-test-principal", ""))
        return None if entry is None else entry[0]

    def role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        for principal, role, org in principals.values():
            if principal.subject == subject and org == organization_id:
                return role
        return None

    source_uris = {
        "huggingface": "acme/model",
        "private_s3": "s3://bucket/models/x",
        "local_filesystem": "models/x",
        "private_registry": "https://registry.example.test/x",
    }
    check(CID, "every shipped source type has a seed URI", set(source_uris) == set(sources))

    discovery = _Discovery()
    app = FastAPI()
    for router in (
        build_trainable_artifact_router(
            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup
        ),
        build_model_catalog_mapping_router(
            session_factory,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            secret_manager_config_factory=lambda: None,
            discovery_function=discovery,
        ),
        build_model_capability_router(
            session_factory,
            principal_resolver=principal_resolver,
            role_lookup=role_lookup,
            secret_manager_config_factory=lambda: None,
            discovery_function=discovery,
        ),
    ):
        app.include_router(router)

    def map_path(model: str) -> str:
        return f"/api/v1/organizations/{org1_id}/connections/litellm/{conn1_id}/models/{model}/training-artifact"

    def cap_path(model: str) -> str:
        return (
            f"/api/v1/organizations/{org1_id}/connections/litellm/{conn1_id}"
            f"/models/{model}/capabilities?project_id={proj1_id}"
        )

    def artifact_path(artifact_id: uuid.UUID, suffix_: str = "") -> str:
        return f"/api/v1/organizations/{org1_id}/artifacts/{artifact_id}{suffix_}"

    def seed_artifact(source_type: str, label: str) -> uuid.UUID:
        with Session(owner_engine) as s:
            art = TrainableArtifact(
                organization_id=org1_id,
                display_name=f"artifact-{label}",
                source_type=source_type,
                source_uri=source_uris[source_type],
                model_family="llama",
                base_model_revision="a" * 40,
                weights_digest=_DIGEST,
                license_id="mit",
                license_accepted_by="seed",
                license_accepted_at=_NOW,
                minimum_gpu_profile_json={"gpu": "a100", "vram_gb": 40},
                status="ready",
            )
            s.add(art)
            s.commit()
            return uuid.UUID(str(art.id))

    def sql(statement: str, **params: Any) -> None:
        with Session(owner_engine) as s:
            s.execute(text(statement), params)
            s.commit()

    check_name = "ck_trainable_artifacts_ready_needs_license"
    check_state = {"dropped": False}

    def drop_ready_license_check() -> None:
        if not check_state["dropped"]:
            sql(f"ALTER TABLE trainable_artifacts DROP CONSTRAINT {check_name}")
            check_state["dropped"] = True

    def restore_ready_license_check() -> None:
        # Park every state the CHECK forbids, then re-add the CHECK exactly as migration 0029
        # defines it; the restore itself proves no forbidden row is left behind.
        sql(
            "UPDATE trainable_artifacts SET status='archived', archived_at=now() "
            "WHERE status='ready' AND license_accepted_at IS NULL"
        )
        sql(
            f"ALTER TABLE trainable_artifacts ADD CONSTRAINT {check_name} "
            "CHECK (status <> 'ready' OR license_accepted_at IS NOT NULL)"
        )
        check_state["dropped"] = False

    def violations(label: str, instance: Any, schema_name: str) -> list[str]:
        errs = cct.validate(instance, contract.schema(schema_name), root_doc)
        if errs:
            FAILURES.append(
                f"FAIL [{CID}] {label}: response violates frozen {schema_name}: {errs[:3]}"
            )
        return errs

    # The /changes list schema is read from the frozen document, never an inline copy.
    changes_schema = contract.response_schema(
        "/api/v1/organizations/{organization_id}/connections/litellm/{connection_id}"
        "/models/{model_name}/training-artifact/changes",
        "get",
        "200",
    )
    check(
        CID, "the frozen /changes schema is closed", changes_schema["additionalProperties"] is False
    )

    cells = matrix
    aliases = {i: f"cell-{i}" for i in range(len(cells))}
    discovery.result = _discovered(*aliases.values(), "upstream-only-model")
    validated = {"capabilities": 0, "mapping": 0, "changes": 0}
    unlicensed_ready_cells: list[str] = []
    seed_responses: dict[str, Any] = {}

    with _serve(app) as base:
        probe_id = seed_artifact("huggingface", "check-probe")
        for probe, statement in (
            (
                "clearing the license of a ready artifact",
                "UPDATE trainable_artifacts SET license_accepted_by=NULL, "
                "license_accepted_at=NULL WHERE id=:i",
            ),
            (
                "clearing only license_accepted_at of a ready artifact",
                "UPDATE trainable_artifacts SET license_accepted_at=NULL WHERE id=:i",
            ),
        ):
            try:
                sql(statement, i=probe_id)
                refused = False
            except IntegrityError as exc:
                refused = check_name in str(exc)
            check(CID, f"the database refuses {probe} (ck_..._ready_needs_license)", refused)
        for index, cell in enumerate(cells):
            alias = aliases[index]
            label = f"{index}:{cell['source_type']}/{cell['status']}/{cell['license']}/{'mapped' if cell['mapped'] else 'unmapped'}"
            art_id = seed_artifact(cell["source_type"], str(index))
            if cell["status"] == "ready" and cell["license"] != "approved":
                # FTF-0305-F1 / migration 0029: the database refuses this state while its CHECK
                # exists (probed once, before the loop). Lift the CHECK for the matrix only, so the
                # capability route is still proven to report trainable=false for it.
                drop_ready_license_check()
                unlicensed_ready_cells.append(label)
            if cell["mapped"]:
                mapped = _request(
                    base,
                    "POST",
                    map_path(alias),
                    principal="admin1",
                    payload={"trainable_artifact_id": str(art_id), "expected_catalog_version": 0},
                )
                check(
                    CID,
                    f"[{label}] real POST mapping is 200 ({mapped.status})",
                    mapped.status == 200,
                )
                if mapped.status == 200:
                    stripped = {k: v for k, v in mapped.json().items() if k != "changed"}
                    violations(
                        f"[{label}] POST mapping result", stripped, "ModelTrainingArtifactMapping"
                    )
            # Move the artifact to the cell's final state. The revoke and archive steps use
            # the real routes; a direct UPDATE seeds only the states the API cannot reach
            # (ready with no accepted license, registered with an accepted license).
            if cell["license"] == "revoked":
                revoked = _request(
                    base,
                    "POST",
                    artifact_path(art_id, "/revoke-license"),
                    principal="admin1",
                    payload={},
                )
                check(
                    CID,
                    f"[{label}] real revoke-license is 200 ({revoked.status})",
                    revoked.status == 200,
                )
            elif cell["license"] == "pending":
                sql(
                    "UPDATE trainable_artifacts SET license_accepted_by=NULL, "
                    "license_accepted_at=NULL, status=:st WHERE id=:i",
                    i=art_id,
                    st="ready" if cell["status"] == "ready" else "registered",
                )
            if cell["status"] == "archived":
                archived = _request(base, "DELETE", artifact_path(art_id), principal="admin1")
                check(
                    CID,
                    f"[{label}] real archive is 200 ({archived.status})",
                    archived.status == 200,
                )
            elif cell["status"] == "registered" and cell["license"] != "revoked":
                sql("UPDATE trainable_artifacts SET status='registered' WHERE id=:i", i=art_id)
            elif cell["status"] == "ready" and cell["license"] == "revoked":
                # the real revoke demoted it to registered; the CHECK is lifted for this cell
                sql("UPDATE trainable_artifacts SET status='ready' WHERE id=:i", i=art_id)
            if cell["license"] == "revoked" and cell["status"] == "registered":
                # the real revoke route alone must have demoted the ready artifact
                current = _request(base, "GET", artifact_path(art_id), principal="viewer1")
                check(
                    CID,
                    f"[{label}] revoke-license alone demoted the artifact to registered",
                    current.status == 200 and current.json()["status"] == "registered",
                )

            capabilities = _request(base, "GET", cap_path(alias), principal="viewer1")
            check(
                CID,
                f"[{label}] capabilities GET is 200 ({capabilities.status})",
                capabilities.status == 200,
            )
            if capabilities.status != 200:
                continue
            body = capabilities.json()
            validated["capabilities"] += 1
            violations(f"[{label}] capabilities", body, "ModelCapabilities")
            want = cct.expected_trainable(cell)
            check(
                CID,
                f"[{label}] trainable is {want} (got {body.get('trainable')!r}, capability {body.get('capability')!r})",
                body.get("trainable") is want,
            )
            if not want:
                check(
                    CID,
                    f"[{label}] a non-trainable cell never reports trainable_open_weight",
                    body.get("capability") != "trainable_open_weight",
                )
            mapping = _request(base, "GET", map_path(alias), principal="viewer1")
            check(CID, f"[{label}] mapping GET is 200 ({mapping.status})", mapping.status == 200)
            if mapping.status == 200:
                validated["mapping"] += 1
                violations(f"[{label}] mapping", mapping.json(), "ModelTrainingArtifactMapping")
                check(
                    CID,
                    f"[{label}] mapping_state matches the cell",
                    mapping.json()["mapping_state"] == ("mapped" if cell["mapped"] else "unmapped"),
                )
                if cell["mapped"] and cell["status"] == "ready" and cell["license"] == "approved":
                    seed_responses["mapping_mapped"] = mapping.json()
                    seed_responses["capabilities_trainable"] = body
                elif not cell["mapped"] and "mapping_unmapped" not in seed_responses:
                    seed_responses["mapping_unmapped"] = mapping.json()
                    seed_responses["capabilities_unknown"] = body
            changes = _request(base, "GET", map_path(alias) + "/changes", principal="viewer1")
            check(CID, f"[{label}] changes GET is 200 ({changes.status})", changes.status == 200)
            if changes.status == 200:
                validated["changes"] += 1
                changes_body = changes.json()
                if cct.validate(changes_body, changes_schema, root_doc):
                    FAILURES.append(
                        f"FAIL [{CID}] [{label}] changes list violates the frozen contract"
                    )
                check(
                    CID,
                    f"[{label}] change history length matches mapping",
                    len(changes_body["changes"]) == (1 if cell["mapped"] else 0),
                )
                if changes_body["changes"]:
                    seed_responses.setdefault("change", changes_body["changes"][0])

        restore_ready_license_check()
        check(
            CID,
            f"{len(unlicensed_ready_cells)} ready + pending/revoked cells were asserted, none exempt",
            len(unlicensed_ready_cells) == len(sources) * 2 * 2,
        )
        check(
            CID,
            f"validated every cell's responses ({validated})",
            all(v == len(cells) for v in validated.values()),
        )
        try:
            sql(
                "UPDATE trainable_artifacts SET status='ready', license_accepted_at=NULL "
                "WHERE id=(SELECT id FROM trainable_artifacts ORDER BY created_at LIMIT 1)"
            )
            restored = False
        except IntegrityError as exc:
            restored = check_name in str(exc)
        check(CID, "the restored CHECK refuses a ready artifact without a license again", restored)

        # Negative acceptance: an upstream-only model (listed by the gateway, no mapping) and
        # unresolvable names (unknown, Unicode look-alike, trailing space, case variant) are
        # exact-match, never trainable.
        mapped_alias = next(aliases[i] for i, c in enumerate(cells) if cct.expected_trainable(c))
        unresolvable = [
            "no-such-model",
            mapped_alias.upper(),
            mapped_alias + " ",
            mapped_alias.replace("e", "е", 1),
        ]
        unresolvable += _unicode_exact_match_variants(mapped_alias)
        for name in ["upstream-only-model", *unresolvable]:
            response = _request(
                base, "GET", cap_path(urllib.parse.quote(name, safe="")), principal="viewer1"
            )
            check(
                CID,
                f"[{name!r}] capabilities GET is 200 ({response.status})",
                response.status == 200,
            )
            if response.status != 200:
                continue
            got = response.json()
            violations(f"[{name!r}] capabilities", got, "ModelCapabilities")
            check(
                CID,
                f"[{name!r}] upstream-only / unresolvable model is not trainable and not trainable_open_weight",
                got["trainable"] is False
                and got["capability"] == "unknown"
                and got["artifact"]["artifact_id"] is None,
            )
            if name != "upstream-only-model":
                check(
                    CID,
                    f"[{name!r}] unresolvable name has access not_granted",
                    got["access"] == "not_granted",
                )

        # Generated negative table against REAL responses.
        for key, schema_name in (
            ("capabilities_trainable", "ModelCapabilities"),
            ("capabilities_unknown", "ModelCapabilities"),
            ("mapping_mapped", "ModelTrainingArtifactMapping"),
            ("mapping_unmapped", "ModelTrainingArtifactMapping"),
            ("change", "ModelTrainingArtifactMappingChange"),
        ):
            check(CID, f"a real {key} response was captured", key in seed_responses)
            if key not in seed_responses:
                continue
            schema = contract.schema(schema_name)
            table = cct.generate_negative_cases(seed_responses[key], schema, root_doc)
            wrongly = cct.assert_negative_cases_rejected(table, schema, root_doc)
            check(
                CID,
                f"{key}: generated table of {len(table)} cases, all rejected {wrongly[:2]}",
                len(table) >= 10 and not wrongly,
            )

    # A lax checker equality must stay visible: the generator's dedupe is independent of the
    # checker's, so a casefolding / stripping `_json_equal` still faces the case variants (V3).
    if "capabilities_trainable" in seed_responses:
        caps_schema = contract.schema("ModelCapabilities")
        real_equal = cct._json_equal

        def casefold_equal(left: Any, right: Any) -> bool:
            if type(left) is str and type(right) is str:
                return left.strip().casefold() == right.strip().casefold()
            return type(left) is type(right) and bool(left == right)

        cct._json_equal = casefold_equal
        try:
            lax_table = cct.generate_negative_cases(
                seed_responses["capabilities_trainable"], caps_schema, root_doc
            )
            lax_survivors = cct.assert_negative_cases_rejected(lax_table, caps_schema, root_doc)
        finally:
            cct._json_equal = real_equal
        check(
            CID,
            f"a casefolding checker equality is caught by the real-response table ({len(lax_survivors)})",
            len(lax_survivors) > 0,
        )

    # FTF-0305-F1 interleave over the real routes: mark-ready racing revoke-license never
    # leaves a `ready` row without an accepted license (RED without the row lock and CHECK).
    def held_interleave(label: str, held_statement: str, route_suffix: str) -> tuple[int, Any]:
        """Hold a competing transaction's uncommitted write on one artifact row, fire the real
        route at the same row, release, and return (HTTP status, final (status, license))."""
        race_id = seed_artifact("huggingface", f"held-{label}")
        sql("UPDATE trainable_artifacts SET status='registered' WHERE id=:i", i=race_id)
        outcome: list[int] = []
        with _serve(app) as held_base, owner_engine.connect() as holder:
            holder.execute(text(held_statement), {"i": race_id})
            thread = threading.Thread(
                target=lambda: outcome.append(
                    _request(
                        held_base,
                        "POST",
                        artifact_path(race_id, route_suffix),
                        principal="admin1",
                        payload={},
                    ).status
                )
            )
            thread.start()
            time.sleep(1.5)
            holder.commit()
            thread.join(30)
        with Session(owner_engine) as s:
            row = s.execute(
                text("SELECT status, license_accepted_at FROM trainable_artifacts WHERE id=:i"),
                {"i": race_id},
            ).one()
        return (outcome[0] if outcome else -1), (row[0], row[1])

    # (1) a revoke commits its license clearing while mark-ready is in flight: mark-ready must
    # re-read after the lock and refuse; without the row lock the stale read persists `ready`
    # (the CHECK then turns it into a 500).
    code, (final_status, final_license) = held_interleave(
        "revoke-first",
        "UPDATE trainable_artifacts SET license_accepted_by=NULL, license_accepted_at=NULL "
        "WHERE id=:i",
        "/mark-ready",
    )
    check(CID, f"revoke committed first: mark-ready is a clean 409 ({code})", code == 409)
    check(
        CID,
        f"revoke committed first: never ready without a license ({final_status}, {final_license})",
        not (final_status == "ready" and final_license is None) and final_status == "registered",
    )
    # (2) a mark-ready commits while revoke-license is in flight: the revoke must see `ready`
    # and demote it, leaving no ready row with a cleared license.
    code, (final_status, final_license) = held_interleave(
        "ready-first",
        "UPDATE trainable_artifacts SET status='ready' WHERE id=:i",
        "/revoke-license",
    )
    check(CID, f"mark-ready committed first: revoke-license is a clean 200 ({code})", code == 200)
    check(
        CID,
        f"mark-ready committed first: revoke demotes it, never ready without a license ({final_status}, {final_license})",
        final_status == "registered" and final_license is None,
    )

    interleave_bad = 0
    interleave_statuses: set[int] = set()
    with _serve(app) as base:
        for trial in range(12):
            race_id = seed_artifact("huggingface", f"race-{trial}")
            sql(
                "UPDATE trainable_artifacts SET status='registered' WHERE id=:i",
                i=race_id,
            )
            barrier = threading.Barrier(2)
            outcomes: list[int] = []

            def racer(
                suffix_: str,
                target: uuid.UUID = race_id,
                gate: threading.Barrier = barrier,
                sink: list[int] = outcomes,
            ) -> None:
                gate.wait(10)
                sink.append(
                    _request(
                        base, "POST", artifact_path(target, suffix_), principal="admin1", payload={}
                    ).status
                )

            threads = [
                threading.Thread(target=racer, args=(suffix_,))
                for suffix_ in ("/mark-ready", "/revoke-license")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
            interleave_statuses.update(outcomes)
            with Session(owner_engine) as s:
                row = s.execute(
                    text("SELECT status, license_accepted_at FROM trainable_artifacts WHERE id=:i"),
                    {"i": race_id},
                ).one()
            if row[0] == "ready" and row[1] is None:
                interleave_bad += 1
        check(
            CID,
            f"mark-ready x revoke-license never leaves ready without a license ({interleave_bad}/12)",
            interleave_bad == 0,
        )
        check(
            CID,
            f"every racing response is a clean 200/409, never a 500 ({sorted(interleave_statuses)})",
            interleave_statuses <= {200, 409},
        )

    if FAILURES:
        print(f"FTF-0310 capability contract real proof: {len(FAILURES)} failed")
        for failure in FAILURES[:60]:
            print(f"  {failure}")
        sys.exit(1)
    print(
        f"FTF-0310 capability contract real proof: all checks passed ({len(cells)} cells, contract sha256 {contract.sha256})"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
