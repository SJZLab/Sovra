#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,method-assign,misc,func-returns-value,union-attr,index"
# ruff: noqa: E501
"""FTF-0701 (P3) negative-test proof: training experiment routes and the phase-07 contract paths.
No database or network: a real uvicorn server on loopback over the real router, with the store
calls and the session opener replaced by recording stand-ins (the real store over PostgreSQL 16 is
`tests/integration/test_ftf_0701_routes.py`).

C  contract: `07-interface.yaml` declares exactly the router's registered paths and methods (no
   undeclared route, no placeholder path), every operation's response set, the closed request and
   response shapes in `07-domain.schema.json`, and the error-code table equal to the store's.
R  routes: the 6-role truth table for POST / list / get, unauthenticated and foreign-organization
   callers, the strict 64 KiB body, the paging query, 201 vs 200, the serialized experiment
   validated against the schema, every store code with its status on every operation, identical
   404s, and the actor taken from the authenticated principal.
W  wiring: `main.py` imports and mounts the router with the shared resolver and lookup; the real
   `create_app()` serves the three operations behind authentication; the live caller of
   `build_training_experiment_router` is `main.py`.
F  fault-injected copies of the route module and of the contract file, each turning this suite RED.

Run: `uv run python3 tests/tasks/test_ftf_0701.py`
"""

from __future__ import annotations

import contextlib
import copy
import http.client
import importlib.util
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import uvicorn  # noqa: E402
import yaml  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from scripts.product_truth_check import SOURCE_ROOTS, find_callers  # noqa: E402
from workers.training import training_config_schema as schema  # noqa: E402

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.tenant_isolation_query_guards import TenantContextError  # noqa: E402
from app.training import training_experiment_routes as real_routes  # noqa: E402
from app.training import training_experiment_store as store  # noqa: E402

CID = "FTF-0701"
FAILURES: list[str] = []
CHECKS = [0]
ROUTES_PATH = ROOT / "apps" / "api" / "app" / "training" / "training_experiment_routes.py"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"
OPENAPI_PATH = ROOT / "packages" / "contracts" / "openapi" / "07-interface.yaml"
SCHEMA_PATH = ROOT / "packages" / "contracts" / "jsonschema" / "07-domain.schema.json"
UNDER_MUTATION_GATE = "mutation-gate-pyc" in os.environ.get("PYTHONPYCACHEPREFIX", "")
ISSUER = "https://idp.example"
ORG1 = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000a001")
ORG2 = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000b002")
PROJECT = uuid.UUID("cccccccc-0000-4000-8000-00000000c003")
EXPERIMENT = uuid.UUID("dddddddd-0000-4000-8000-00000000d004")
BASE = f"/api/v1/organizations/{ORG1}/projects/{PROJECT}/training/experiments"
COLLECTION = "/api/v1/organizations/{organization_id}/projects/{project_id}/training/experiments"
ITEM = COLLECTION + "/{experiment_id}"
OPERATIONS = {(COLLECTION, "post"), (COLLECTION, "get"), (ITEM, "get")}
WRITERS = {Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN}
CANARY = "CANARY0701P3"


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{CID} {criterion_id}] {description}")


def _load(path: Path, name: str, *, no_main_env: str | None = None) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    saved = os.environ.get(no_main_env) if no_main_env else None
    if no_main_env:
        os.environ[no_main_env] = "1"
    try:
        spec.loader.exec_module(module)
    finally:
        if no_main_env:
            if saved is None:
                os.environ.pop(no_main_env, None)
            else:
                os.environ[no_main_env] = saved
    return module


P1 = _load(
    ROOT / "tests" / "tasks" / "test_ftf_0701_p1.py",
    "ftf_0701_p1_for_p3",
    no_main_env="FTF_0701_P1_NO_MAIN",
)


def resolved_experiment(**over: Any) -> Any:
    """A stored experiment as the store returns it, with a real resolved document."""
    resolved = schema.resolve_training_config(
        schema.parse_training_config_request(P1.valid_body()), P1.make_facts(schema)
    )
    doc = json.loads(schema.canonical_config_json(resolved.doc))
    values: dict[str, Any] = {
        "id": EXPERIMENT,
        "organization_id": ORG1,
        "project_id": PROJECT,
        "name": "exp-a",
        "version": 2,
        "parent_version": 1,
        "status": "draft",
        "method": doc["method"],
        "config_schema_version": doc["schema_version"],
        "config_json": doc,
        "config_sha256": resolved.config_sha256,
        "trainable_artifact_id": uuid.UUID(doc["base"]["trainable_artifact_id"]),
        "model_catalog_entry_id": None,
        "split_manifest_id": uuid.UUID(doc["data"]["train"]["split_manifest_id"]),
        "frozen_manifest_sha256": doc["server"]["frozen_manifest_sha256"],
        "budget_policy_id": uuid.UUID(doc["server"]["budget_policy_id"]),
        "max_cost": Decimal("10.5000"),
        "currency": "USD",
        "security_profile": "standard",
        "requested_by": "user-org1-ml_engineer",
        "request_id": f"req-{CANARY}",
        "idempotency_key": f"key-{CANARY}",
        "request_sha256": "9" * 64,
        "created_at": datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC),
    }
    values.update(over)
    return SimpleNamespace(**values)


EXPECTED_KEYS = {
    "id",
    "organization_id",
    "project_id",
    "name",
    "version",
    "parent_version",
    "status",
    "method",
    "config_schema_version",
    "config",
    "config_sha256",
    "trainable_artifact_id",
    "model_catalog_entry_id",
    "split_manifest_id",
    "frozen_manifest_sha256",
    "budget_policy_id",
    "max_cost",
    "currency",
    "security_profile",
    "requested_by",
    "created_at",
}


def expected_body(exp: Any) -> dict[str, Any]:
    entry = exp.model_catalog_entry_id
    return {
        "id": str(exp.id),
        "organization_id": str(exp.organization_id),
        "project_id": str(exp.project_id),
        "name": exp.name,
        "version": exp.version,
        "parent_version": exp.parent_version,
        "status": exp.status,
        "method": exp.method,
        "config_schema_version": exp.config_schema_version,
        "config": exp.config_json,
        "config_sha256": exp.config_sha256,
        "trainable_artifact_id": str(exp.trainable_artifact_id),
        "model_catalog_entry_id": None if entry is None else str(entry),
        "split_manifest_id": str(exp.split_manifest_id),
        "frozen_manifest_sha256": exp.frozen_manifest_sha256,
        "budget_policy_id": str(exp.budget_policy_id),
        "max_cost": "10.5000",
        "currency": exp.currency,
        "security_profile": exp.security_profile,
        "requested_by": exp.requested_by,
        "created_at": "2026-11-27T12:00:00+00:00",
    }


# ---------------------------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------------------------


class Stub:
    """Recording stand-ins for the store calls and the session opener of one route module."""

    def __init__(self, mod: ModuleType) -> None:
        self.mod = mod
        self.calls: list[tuple[Any, ...]] = []
        self.sessions: list[Any] = []
        self.raise_code: str | None = None
        self.raise_exc: BaseException | None = None
        self.replayed = False
        self.experiment = resolved_experiment()
        self.listing: list[Any] = [self.experiment]
        self.events: list[str] = []
        self.principals: dict[str, AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org in (("org1", ORG1), ("org2", ORG2)):
            for role in Role:
                subject = f"user-{label}-{role.value}"
                self.principals[f"{label}:{role.value}"] = AuthenticatedPrincipal(
                    issuer=ISSUER, subject=subject
                )
                self.memberships[(ISSUER, subject, org)] = role

    def principal_resolver(self, request: Any) -> AuthenticatedPrincipal | None:
        return self.principals.get(request.headers.get("x-test-principal", ""))

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.memberships.get((issuer, subject, organization_id))

    def _maybe_raise(self) -> None:
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.raise_code is not None:
            raise store.TrainingExperimentError(self.raise_code)

    @contextlib.contextmanager
    def session_for(self, session_factory: Any, context: Any) -> Iterator[Any]:
        session = SimpleNamespace(factory=session_factory, context=context)
        self.sessions.append(session)
        self.events.append("open")
        try:
            yield session
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")

    def create(self, session: Any, context: Any, project_id: Any, **kwargs: Any) -> Any:
        self.calls.append(("create", session, context, project_id, kwargs))
        self._maybe_raise()
        return SimpleNamespace(experiment=self.experiment, replayed=self.replayed)

    def get(self, session: Any, context: Any, project_id: Any, experiment_id: Any) -> Any:
        self.calls.append(("get", session, context, project_id, experiment_id))
        self._maybe_raise()
        return self.experiment

    def list(self, session: Any, context: Any, project_id: Any, **kwargs: Any) -> Any:
        self.calls.append(("list", session, context, project_id, kwargs))
        self._maybe_raise()
        return list(self.listing)

    def reset(self) -> None:
        self.calls.clear()
        self.sessions.clear()
        self.events.clear()
        self.raise_code = None
        self.raise_exc = None
        self.replayed = False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def running_app_for(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.01)
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


SESSION_FACTORY = object()


@contextlib.contextmanager
def served(mod: ModuleType) -> Iterator[tuple[str, Stub]]:
    stub = Stub(mod)
    saved = (
        mod.tenant_scoped_session_for,
        mod.create_training_experiment,
        mod.get_training_experiment,
        mod.list_training_experiments,
    )
    mod.tenant_scoped_session_for = stub.session_for
    mod.create_training_experiment = stub.create
    mod.get_training_experiment = stub.get
    mod.list_training_experiments = stub.list
    app = FastAPI()
    app.include_router(
        mod.build_training_experiment_router(
            SESSION_FACTORY,
            principal_resolver=stub.principal_resolver,
            role_lookup=stub.role_lookup,
        )
    )
    try:
        with running_app_for(app) as base:
            yield base, stub
    finally:
        (
            mod.tenant_scoped_session_for,
            mod.create_training_experiment,
            mod.get_training_experiment,
            mod.list_training_experiments,
        ) = saved


class Http:
    def __init__(self, status: int, raw: bytes) -> None:
        self.status = status
        self.raw = raw
        try:
            self.json: Any = json.loads(raw) if raw else None
        except ValueError:
            self.json = None

    @property
    def detail(self) -> Any:
        return self.json.get("detail") if isinstance(self.json, dict) else None


def call(
    base: str, method: str, path: str, principal: str | None = "org1:ml_engineer", body: Any = None
) -> Http:
    headers = {"X-Test-Principal": principal} if principal else {}
    payload = None
    if body is not None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    host, port = base.split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        return Http(response.status, response.read())
    finally:
        conn.close()


def post_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "exp-a", "idempotency_key": "key-1", "config": P1.valid_body()}
    body.update(over)
    return body


# ---------------------------------------------------------------------------------------------
# C: contract
# ---------------------------------------------------------------------------------------------

PLACEHOLDER = re.compile(r"estimate|/runs|metrics|cancel|engine")


def contract_findings(openapi_text: str, schema_text: str, mod: ModuleType) -> list[str]:
    """Every broken contract rule, as text. Empty = the contract equals the router."""
    out: list[str] = []
    root = yaml.safe_load(openapi_text)
    defs = json.loads(schema_text)["$defs"]
    paths = root.get("paths") or {}
    declared = {
        (path, method)
        for path, item in paths.items()
        for method in item
        if method not in ("parameters", "summary", "description")
    }
    router = mod.build_training_experiment_router(lambda: None)
    mounted = {
        (route.path, method.lower())
        for route in router.routes
        for method in route.methods
        if method not in ("HEAD", "OPTIONS")
    }
    if declared != OPERATIONS:
        out.append(f"declared operations are exactly the three: {sorted(declared)}")
    if mounted != OPERATIONS:
        out.append(f"mounted operations are exactly the three: {sorted(mounted)}")
    if declared != mounted:
        out.append("declared operations equal the router's registered routes")
    if set(paths) != {COLLECTION, ITEM}:
        out.append("exactly two path keys")
    if any(PLACEHOLDER.search(path.split("/training/", 1)[-1]) for path in paths):
        out.append("no placeholder phase-07 path")
    if (mod.EXPERIMENTS_PATH, mod.EXPERIMENT_PATH) != (COLLECTION, ITEM):
        out.append("module path constants")
    expected_params = {
        COLLECTION: ["organization_id", "project_id"],
        ITEM: ["organization_id", "project_id", "experiment_id"],
    }
    for path, names in expected_params.items():
        params = (paths.get(path) or {}).get("parameters") or []
        got = [(p.get("name"), p.get("in"), p.get("required"), p.get("schema")) for p in params]
        if got != [(n, "path", True, {"type": "string", "format": "uuid"}) for n in names]:
            out.append(f"path parameters of {path}")
    responses = {
        (COLLECTION, "post"): {"201", "200", "400", "401", "403", "404", "409", "500"},
        (COLLECTION, "get"): {"200", "400", "401", "403", "404", "500"},
        (ITEM, "get"): {"200", "401", "403", "404", "500"},
    }
    success = {
        (COLLECTION, "post"): {"201": "TrainingExperiment", "200": "TrainingExperiment"},
        (COLLECTION, "get"): {"200": "TrainingExperimentList"},
        (ITEM, "get"): {"200": "TrainingExperiment"},
    }
    for (path, method), codes in responses.items():
        op = (paths.get(path) or {}).get(method) or {}
        if set(op.get("responses") or {}) != codes:
            out.append(f"response codes of {method} {path}")
        for code, name in success[(path, method)].items():
            ref = (
                (((op.get("responses") or {}).get(code) or {}).get("content") or {})
                .get("application/json", {})
                .get("schema")
            )
            if ref != {"$ref": f"#/components/schemas/{name}"}:
                out.append(f"{method} {path} {code} body is {name}")
        for code in codes - set(success[(path, method)]):
            want = "NotAuthenticated" if code == "401" else "Refused"
            if (op.get("responses") or {}).get(code) != {"$ref": f"#/components/responses/{want}"}:
                out.append(f"{method} {path} {code} is the {want} response")
    post = (paths.get(COLLECTION) or {}).get("post") or {}
    if post.get("requestBody") != {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/TrainingExperimentCreateRequest"}
            }
        },
    }:
        out.append("POST request body is the closed create request")
    listing = (paths.get(COLLECTION) or {}).get("get") or {}
    query = {
        p.get("name"): (p.get("in"), p.get("required"), p.get("schema"))
        for p in listing.get("parameters") or []
    }
    if query != {
        "limit": (
            "query",
            False,
            {
                "type": "integer",
                "minimum": 1,
                "maximum": mod.MAX_PAGE_SIZE,
                "default": mod.DEFAULT_PAGE_SIZE,
            },
        ),
        "after_name": (
            "query",
            False,
            {"type": "string", "minLength": 1, "maxLength": store.NAME_MAX_LENGTH},
        ),
        "after_version": ("query", False, {"type": "integer", "minimum": 1, "maximum": 2147483647}),
    }:
        out.append("list query parameters")
    schemas = (root.get("components") or {}).get("schemas") or {}
    for name in (
        "TrainingConfigV1",
        "ResolvedTrainingConfig",
        "TrainingConfigErrorCode",
        "TrainingExperimentCreateRequest",
        "TrainingExperiment",
        "TrainingExperimentList",
        "TrainingExperimentErrorCode",
        "TrainingExperimentError",
    ):
        if (
            schemas.get(name) != {"$ref": f"./../jsonschema/07-domain.schema.json#/$defs/{name}"}
            or name not in defs
        ):
            out.append(f"component schema {name} refs the domain schema")
    if set(schemas) != {
        "TrainingConfigV1",
        "ResolvedTrainingConfig",
        "TrainingConfigErrorCode",
        "TrainingExperimentCreateRequest",
        "TrainingExperiment",
        "TrainingExperimentList",
        "TrainingExperimentErrorCode",
        "TrainingExperimentError",
    }:
        out.append("no other component schema")
    refused = ((root.get("components") or {}).get("responses") or {}).get("Refused") or {}
    if (refused.get("content") or {}).get("application/json", {}).get("schema") != {
        "type": "object",
        "required": ["detail"],
        "additionalProperties": False,
        "properties": {"detail": {"type": "string"}},
    }:
        out.append("the Refused response is a closed {detail}")
    # domain shapes
    codes = defs.get("TrainingExperimentErrorCode") or {}
    if (
        codes.get("enum") != list(store.STORE_ERROR_CODES)
        or codes.get("x-http-status") != dict(store.STORE_ERROR_CODES)
        or codes.get("type") != "string"
    ):
        out.append("TrainingExperimentErrorCode equals the store's code table")
    if (defs.get("TrainingConfigErrorCode") or {}).get("enum") != list(schema.ERROR_CODES):
        out.append("TrainingConfigErrorCode is still the pure module's table")
    create = defs.get("TrainingExperimentCreateRequest") or {}
    if create != {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": store.NAME_MAX_LENGTH},
            "idempotency_key": {
                "type": "string",
                "minLength": 1,
                "maxLength": store.IDEMPOTENCY_KEY_MAX_LENGTH,
            },
            "config": {"$ref": "#/$defs/TrainingConfigV1"},
        },
        "required": ["name", "idempotency_key", "config"],
        "additionalProperties": False,
    }:
        out.append("the create request is closed: name, idempotency_key, config")
    exp = defs.get("TrainingExperiment") or {}
    props = exp.get("properties") or {}
    if (
        set(props) != EXPECTED_KEYS
        or set(exp.get("required") or []) != EXPECTED_KEYS
        or exp.get("additionalProperties") is not False
        or exp.get("type") != "object"
    ):
        out.append("TrainingExperiment is closed over exactly the served keys")
    uid = {"type": "string", "pattern": "^" + schema._UUID.pattern + "$"}
    sha = {"type": "string", "pattern": "^" + schema._SHA256.pattern + "$"}
    wanted = {
        "id": uid,
        "organization_id": uid,
        "project_id": uid,
        "trainable_artifact_id": uid,
        "split_manifest_id": uid,
        "budget_policy_id": uid,
        "config_sha256": sha,
        "frozen_manifest_sha256": sha,
        "model_catalog_entry_id": {"type": ["string", "null"], "pattern": uid["pattern"]},
        "config": {"$ref": "#/$defs/ResolvedConfigDocument"},
        "method": {"$ref": "#/$defs/EnabledTrainingMethod"},
        "status": {"type": "string", "enum": ["draft"]},
        "config_schema_version": {"type": "string", "const": schema.SCHEMA_VERSION},
        "version": {"type": "integer", "minimum": 1, "maximum": 2147483647},
        "parent_version": {"type": ["integer", "null"], "minimum": 1, "maximum": 2147483646},
        "max_cost": {"type": "string", "pattern": "^" + schema._DECIMAL.pattern + "$"},
        "currency": {"type": "string", "pattern": "^" + schema._CURRENCY.pattern + "$"},
        "security_profile": {"type": "string", "enum": list(schema.SECURITY_PROFILES)},
        "name": {"type": "string", "minLength": 1, "maxLength": store.NAME_MAX_LENGTH},
        "requested_by": {"type": "string", "minLength": 1, "maxLength": 200},
        "created_at": {"type": "string", "minLength": 1},
    }
    for key, want in wanted.items():
        if props.get(key) != want:
            out.append(f"TrainingExperiment.{key}")
    forbidden = {
        "idempotency_key",
        "request_sha256",
        "request_id",
        "rows",
        "records",
        "grants",
        "secret_ref",
    }
    if forbidden & set(props):
        out.append("TrainingExperiment serves no key, digest, row or secret reference")
    if defs.get("TrainingExperimentList") != {
        "type": "object",
        "properties": {
            "experiments": {
                "type": "array",
                "items": {"$ref": "#/$defs/TrainingExperiment"},
                "maxItems": mod.MAX_PAGE_SIZE,
            }
        },
        "required": ["experiments"],
        "additionalProperties": False,
    }:
        out.append("TrainingExperimentList is a closed page of at most 200")
    if defs.get("TrainingExperimentError") != {
        "type": "object",
        "properties": {"detail": {"$ref": "#/$defs/TrainingExperimentErrorCode"}},
        "required": ["detail"],
        "additionalProperties": False,
    }:
        out.append("TrainingExperimentError is a closed {detail}")
    return out


def part_c() -> None:
    openapi_text = OPENAPI_PATH.read_text(encoding="utf-8")
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    findings = contract_findings(openapi_text, schema_text, real_routes)
    check("CONTRACT", f"the contract equals the router: {findings}", findings == [])
    if UNDER_MUTATION_GATE:
        return
    faults: dict[str, tuple[str, str]] = {
        "placeholder path /training/estimate": (
            "paths:\n",
            "paths:\n  /api/v1/organizations/{organization_id}/projects/{project_id}/training/estimate:\n    post:\n      responses:\n        '200':\n          description: placeholder\n",
        ),
        "an undeclared method (DELETE) on the item": (
            "    get:\n      summary: Get one training experiment\n",
            "    delete:\n      responses:\n        '204':\n          description: gone\n    get:\n      summary: Get one training experiment\n",
        ),
        "the list operation undeclared": (
            "    get:\n      summary: List the project's",
            "    x-get:\n      summary: List the project's",
        ),
        "POST body is the open config": (
            "$ref: '#/components/schemas/TrainingExperimentCreateRequest'",
            "$ref: '#/components/schemas/TrainingConfigV1'",
        ),
        "404 undeclared on get": (
            "          description: The experiment with its resolved configuration.\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/TrainingExperiment'\n        '401':\n          $ref: '#/components/responses/NotAuthenticated'\n        '403':\n          $ref: '#/components/responses/Refused'\n        '404':\n          $ref: '#/components/responses/Refused'\n",
            "          description: The experiment with its resolved configuration.\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/TrainingExperiment'\n        '401':\n          $ref: '#/components/responses/NotAuthenticated'\n        '403':\n          $ref: '#/components/responses/Refused'\n",
        ),
        "limit maximum widened": (
            "            maximum: 200\n            default: 50",
            "            maximum: 1000\n            default: 50",
        ),
        "Refused detail opened": (
            "            additionalProperties: false\n            properties:\n              detail:",
            "            additionalProperties: true\n            properties:\n              detail:",
        ),
    }
    for label, (old, new) in faults.items():
        present = openapi_text.count(old) == 1
        check("FAULT", f"contract fault text occurs once: {label}", present)
        if present:
            red = contract_findings(openapi_text.replace(old, new), schema_text, real_routes)
            check("FAULT", f"contract fault {label} -> RED", red != [])
    domain = json.loads(schema_text)

    def tampered(edit: Any) -> str:
        doc = copy.deepcopy(domain)
        edit(doc["$defs"])
        return json.dumps(doc)

    schema_faults: dict[str, Any] = {
        "create request opened": lambda d: d["TrainingExperimentCreateRequest"].__setitem__(
            "additionalProperties", True
        ),
        "create request: config not required": lambda d: d["TrainingExperimentCreateRequest"][
            "required"
        ].remove("config"),
        "create request: config is the resolved document": lambda d: d[
            "TrainingExperimentCreateRequest"
        ]["properties"].__setitem__("config", {"$ref": "#/$defs/ResolvedConfigDocument"}),
        "experiment opened": lambda d: d["TrainingExperiment"].__setitem__(
            "additionalProperties", True
        ),
        "experiment serves the idempotency key": lambda d: (
            d["TrainingExperiment"]["properties"].__setitem__(
                "idempotency_key", {"type": "string"}
            ),
            d["TrainingExperiment"]["required"].append("idempotency_key"),
        ),
        "experiment: config_sha256 pattern dropped": lambda d: d["TrainingExperiment"][
            "properties"
        ]["config_sha256"].pop("pattern"),
        "experiment: status widened": lambda d: d["TrainingExperiment"]["properties"]["status"][
            "enum"
        ].append("submitted"),
        "experiment: method is any method": lambda d: d["TrainingExperiment"][
            "properties"
        ].__setitem__("method", {"$ref": "#/$defs/TrainingMethod"}),
        "experiment: config is the request document": lambda d: d["TrainingExperiment"][
            "properties"
        ].__setitem__("config", {"$ref": "#/$defs/TrainingConfigV1"}),
        "experiment: organization_id not required": lambda d: d["TrainingExperiment"][
            "required"
        ].remove("organization_id"),
        "error codes: a code dropped": lambda d: d["TrainingExperimentErrorCode"]["enum"].remove(
            "not_found"
        ),
        "error codes: a status changed": lambda d: d["TrainingExperimentErrorCode"][
            "x-http-status"
        ].__setitem__("not_found", 403),
        "error codes: a code added": lambda d: d["TrainingExperimentErrorCode"]["enum"].append(
            "extra"
        ),
        "list page widened": lambda d: d["TrainingExperimentList"]["properties"][
            "experiments"
        ].__setitem__("maxItems", 1000),
        "error body opened": lambda d: d["TrainingExperimentError"].__setitem__(
            "additionalProperties", True
        ),
    }
    for label, edit in schema_faults.items():
        red = contract_findings(openapi_text, tampered(edit), real_routes)
        check("FAULT", f"schema fault {label} -> RED", red != [])


# ---------------------------------------------------------------------------------------------
# R: routes
# ---------------------------------------------------------------------------------------------


def route_suite(mod: ModuleType) -> None:
    defs = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]
    with served(mod) as (base, stub):
        # ---- role truth table: 6 roles x {POST, list, get}
        for role in Role:
            principal = f"org1:{role.value}"
            stub.reset()
            got = call(base, "POST", BASE, principal, post_body())
            if role in WRITERS:
                check(
                    "ROLES", f"{role.value} POST is 201", got.status == 201 and len(stub.calls) == 1
                )
            else:
                check(
                    "ROLES",
                    f"{role.value} POST is 403 role_not_authorized and never reaches the store",
                    (got.status, got.detail) == (403, "role_not_authorized")
                    and stub.calls == []
                    and stub.sessions == [],
                )
            for label, path in (("list", BASE), ("get", f"{BASE}/{EXPERIMENT}")):
                stub.reset()
                got = call(base, "GET", path, principal)
                check(
                    "ROLES",
                    f"{role.value} {label} is 200",
                    got.status == 200 and len(stub.calls) == 1,
                )
        check(
            "ROLES",
            "module role sets",
            (mod.WRITE_ROLES, mod.READ_ROLES) == (frozenset(WRITERS), frozenset(Role)),
        )
        # ---- unauthenticated, and a principal with no membership of the path's organization
        other = f"/api/v1/organizations/{ORG2}/projects/{PROJECT}/training/experiments"
        for method, path, body in (
            ("POST", BASE, post_body()),
            ("GET", BASE, None),
            ("GET", f"{BASE}/{EXPERIMENT}", None),
        ):
            stub.reset()
            got = call(base, method, path, None, body)
            check(
                "AUTH",
                f"unauthenticated {method} is 401 not_authenticated",
                (got.status, got.detail) == (401, "not_authenticated") and stub.calls == [],
            )
            stub.reset()
            got = call(base, method, path, "nobody", body)
            check(
                "AUTH", f"unknown principal {method} is 401", got.status == 401 and stub.calls == []
            )
            stub.reset()
            got = call(
                base, method, path.replace(str(ORG1), str(ORG2)), "org1:organization_admin", body
            )
            check(
                "AUTH",
                f"an org1 admin on org2's path ({method}) is 403 and never reaches the store",
                (got.status, got.detail) == (403, "role_not_authorized") and stub.calls == [],
            )
        del other
        stub.reset()
        got = call(
            base, "POST", BASE, "org1:ml_engineer", {**post_body(), "organization_id": str(ORG2)}
        )
        check(
            "AUTH",
            "a body naming another organization is 403 tenant_mismatch",
            (got.status, got.detail) == (403, "tenant_mismatch") and stub.calls == [],
        )
        # the organization the store sees is the authorized one, from the path
        stub.reset()
        call(base, "POST", other_org_path(), "org2:ml_engineer", post_body())
        check(
            "AUTH",
            "an org2 writer on org2's path reaches the store under org2",
            (
                len(stub.calls) == 1
                and stub.calls[0][2].organization_id == ORG2
                and stub.sessions[0].context.organization_id == ORG2
            ),
        )

        # ---- POST: what reaches the store
        stub.reset()
        body = post_body(name="exp é", idempotency_key=" key ~")
        got = call(base, "POST", BASE, "org1:ml_engineer", body)
        check("POST", "a new experiment is 201", got.status == 201)
        op, session, context, project_id, kwargs = stub.calls[0]
        check(
            "POST",
            "the store is called once, in one session that commits",
            (op == "create" and stub.sessions == [session] and stub.events == ["open", "commit"]),
        )
        check(
            "POST",
            "the session is opened with the router's factory and the authorized organization",
            (
                session.factory is SESSION_FACTORY
                and session.context is context
                and context.organization_id == ORG1
            ),
        )
        check(
            "POST",
            "the project id is the path's",
            project_id == PROJECT and isinstance(project_id, uuid.UUID),
        )
        check(
            "POST",
            "name, config and key reach the store exactly as sent",
            (
                set(kwargs) == {"name", "config_body", "idempotency_key", "actor"}
                and kwargs["name"] == "exp é"
                and kwargs["idempotency_key"] == " key ~"
                and kwargs["config_body"] == body["config"]
            ),
        )
        actor = kwargs["actor"]
        check(
            "POST",
            "the actor is the authenticated principal with its membership role",
            (
                type(actor) is store.ExperimentActor
                and (actor.actor_type, actor.actor_id, actor.role)
                == ("user", "user-org1-ml_engineer", "ml_engineer")
                and re.fullmatch(r"[0-9a-f]{32}", actor.request_id) is not None
            ),
        )
        stub.reset()
        call(base, "POST", BASE, "org1:organization_admin", post_body())
        admin = stub.calls[0][4]["actor"]
        check(
            "POST",
            "the admin's actor carries its own subject and role, and a fresh request id",
            (
                (admin.actor_id, admin.role)
                == ("user-org1-organization_admin", "organization_admin")
                and admin.request_id != actor.request_id
            ),
        )
        check(
            "POST",
            "the 201 body is the serialized experiment",
            got.json == expected_body(stub.experiment),
        )
        check(
            "POST",
            "the body validates against TrainingExperiment",
            P1._validate(defs["TrainingExperiment"], defs, got.json),
        )
        check(
            "POST",
            "the body serves no key, digest or request id",
            (
                set(got.json) == EXPECTED_KEYS
                and CANARY not in got.raw.decode()
                and "9" * 64 not in got.raw.decode()
            ),
        )
        stub.reset()
        stub.replayed = True
        got = call(base, "POST", BASE, "org1:ml_engineer", post_body())
        check(
            "POST",
            "a replay is 200 with the same body",
            got.status == 200 and got.json == expected_body(stub.experiment),
        )
        stub.reset()
        stub.experiment = resolved_experiment(
            model_catalog_entry_id=uuid.UUID("eeeeeeee-0000-4000-8000-00000000e005"),
            parent_version=None,
            version=1,
            max_cost=Decimal("1000"),
        )
        got = call(base, "POST", BASE, "org1:ml_engineer", post_body())
        check(
            "POST",
            "a catalog entry id, a null parent and a whole cost serialize",
            (
                got.json["model_catalog_entry_id"] == "eeeeeeee-0000-4000-8000-00000000e005"
                and got.json["parent_version"] is None
                and got.json["version"] == 1
                and got.json["max_cost"] == "1000.0000"
                and P1._validate(defs["TrainingExperiment"], defs, got.json)
            ),
        )
        stub.reset()
        stub.experiment = resolved_experiment(
            max_cost=Decimal("10.5"),
            created_at=datetime(2026, 11, 27, 15, 0, 0, tzinfo=timezone(timedelta(hours=3))),
        )
        got = call(base, "POST", BASE, "org1:ml_engineer", post_body())
        check(
            "POST",
            "a freshly built row serializes like the row read back (four decimals, UTC instant)",
            (got.json["max_cost"], got.json["created_at"])
            == ("10.5000", "2026-11-27T12:00:00+00:00"),
        )
        stub.experiment = resolved_experiment()
        stub.listing = [stub.experiment]

        # ---- POST: the strict body
        valid = json.dumps(post_body()).encode()
        padding = mod.MAX_BODY_BYTES - len(valid)
        exactly = valid[:-1] + b" " * padding + b"}"
        stub.reset()
        got = call(base, "POST", BASE, "org1:ml_engineer", exactly)
        check(
            "BODY",
            f"a body of exactly 64 KiB is accepted ({len(exactly)})",
            len(exactly) == 65536 and got.status == 201,
        )
        bad_bodies: dict[str, bytes] = {
            "one byte over 64 KiB": valid[:-1] + b" " * (padding + 1) + b"}",
            "33 MiB": b'{"name": "' + b"a" * (33 * 1024 * 1024) + b'"}',
            "empty": b"",
            "malformed JSON": b'{"name": ',
            "not UTF-8": b'{"name": "\xff"}',
            "a list": b"[]",
            "a string": b'"x"',
            "null": b"null",
            "a number": b"7",
            "missing config": json.dumps({"name": "a", "idempotency_key": "k"}).encode(),
            "missing name": json.dumps({"idempotency_key": "k", "config": {}}).encode(),
            "missing key": json.dumps({"name": "a", "config": {}}).encode(),
            "an extra key": json.dumps({**post_body(), "project_id": str(PROJECT)}).encode(),
            "the path's own organization_id as an extra key": json.dumps(
                {**post_body(), "organization_id": str(ORG1)}
            ).encode(),
            "a duplicate top-level key": b'{"name": "a", "name": "b", "idempotency_key": "k", "config": {}}',
            "a duplicate nested key": b'{"name": "a", "idempotency_key": "k", "config": {"mode": "advanced", "mode": "auto"}}',
            "NaN": b'{"name": "a", "idempotency_key": "k", "config": {"x": NaN}}',
            "Infinity": b'{"name": "a", "idempotency_key": "k", "config": {"x": -Infinity}}',
            "deep nesting": b'{"name": "a", "idempotency_key": "k", "config": '
            + b"[" * 20000
            + b"]" * 20000
            + b"}",
        }
        for label, raw in bad_bodies.items():
            stub.reset()
            got = call(base, "POST", BASE, "org1:ml_engineer", raw)
            check(
                "BODY",
                f"{label}: 400 invalid_body, the store is never called ({got.status} {got.detail})",
                (
                    (got.status, got.detail) == (400, "invalid_body")
                    and stub.calls == []
                    and stub.sessions == []
                ),
            )
        # values of the three keys are the store's to judge: they are passed through untouched
        stub.reset()
        got = call(
            base,
            "POST",
            BASE,
            "org1:ml_engineer",
            {"name": 7, "idempotency_key": None, "config": []},
        )
        check(
            "BODY",
            "typed values are passed to the store as they are",
            (
                len(stub.calls) == 1
                and (
                    stub.calls[0][4]["name"],
                    stub.calls[0][4]["idempotency_key"],
                    stub.calls[0][4]["config_body"],
                )
                == (7, None, [])
            ),
        )

        stub.reset()
        cased = b'{"name": "a", "idempotency_key": "k", "config": {"mode": "advanced", "Mode": "x", " mode": 1}}'
        got = call(base, "POST", BASE, "org1:ml_engineer", cased)
        check(
            "BODY",
            "keys differing only in case or padding are different keys, not duplicates",
            got.status == 201
            and len(stub.calls) == 1
            and stub.calls[0][4]["config_body"] == {"mode": "advanced", "Mode": "x", " mode": 1},
        )

        # ---- every store code, with its status, on every operation
        operations = (
            ("POST", BASE, post_body()),
            ("GET", BASE, None),
            ("GET", f"{BASE}/{EXPERIMENT}", None),
        )
        for code, status in store.STORE_ERROR_CODES.items():
            for method, path, body in operations:
                stub.reset()
                stub.raise_code = code
                got = call(base, method, path, "org1:ml_engineer", body)
                check(
                    "CODES",
                    f"{code} on {method} {path[-20:]} is {status} with only the code",
                    (
                        got.status == status
                        and got.json == {"detail": code}
                        and stub.events == ["open", "rollback"]
                    ),
                )
        check(
            "CODES",
            "the table covers every pure code and the store's four",
            (
                set(store.STORE_ERROR_CODES)
                == set(schema.ERROR_CODES)
                | {
                    "not_found",
                    "actor_not_permitted",
                    "version_conflict",
                    "config_integrity_failed",
                }
            ),
        )
        bodies = set()
        for method, path, body in operations:
            for exc in (
                store.TrainingExperimentError("not_found"),
                TenantContextError(f"{CANARY} tenant"),
            ):
                stub.reset()
                stub.raise_exc = exc
                got = call(base, method, path, "org1:ml_engineer", body)
                bodies.add((got.status, got.raw))
        check(
            "CODES",
            f"a missing id and a tenant-context refusal are the identical 404: {bodies}",
            bodies == {(404, b'{"detail":"not_found"}')},
        )
        stub.reset()
        stub.raise_code = "heldout_training_source_denied"
        got = call(
            base,
            "POST",
            BASE,
            "org1:ml_engineer",
            post_body(
                config={
                    **P1.valid_body(),
                    "data": {
                        **P1.valid_body()["data"],
                        "train": {
                            "split_manifest_id": P1.valid_body()["data"]["train"][
                                "split_manifest_id"
                            ],
                            "role": "final_qualification",
                        },
                    },
                }
            ),
        )
        check(
            "CODES",
            "a held-out refusal is 403 heldout_training_source_denied",
            (got.status, got.json) == (403, {"detail": "heldout_training_source_denied"}),
        )
        stub.reset()
        stub.raise_exc = RuntimeError(f"{CANARY} database down")
        got = call(base, "POST", BASE, "org1:ml_engineer", post_body())
        check(
            "CODES",
            "an unexpected error is a 500, never a success, and leaks nothing",
            (
                got.status == 500
                and CANARY not in got.raw.decode()
                and stub.events == ["open", "rollback"]
            ),
        )

        # ---- list
        stub.reset()
        second = resolved_experiment(
            id=uuid.UUID("ffffffff-0000-4000-8000-00000000f006"), version=3, parent_version=2
        )
        stub.listing = [stub.experiment, second]
        got = call(base, "GET", BASE, "org1:viewer")
        op, session, context, project_id, kwargs = stub.calls[0]
        check(
            "LIST",
            "list calls the store with the default page and no cursor",
            (
                op == "list"
                and project_id == PROJECT
                and kwargs == {"limit": mod.DEFAULT_PAGE_SIZE, "after": None}
                and mod.DEFAULT_PAGE_SIZE == 50
                and context.organization_id == ORG1
                and session.factory is SESSION_FACTORY
            ),
        )
        check(
            "LIST",
            "the page is the serialized rows in the store's order",
            (
                got.status == 200
                and got.json
                == {"experiments": [expected_body(stub.experiment), expected_body(second)]}
            ),
        )
        check(
            "LIST",
            "the page validates against TrainingExperimentList",
            P1._validate(defs["TrainingExperimentList"], defs, got.json),
        )
        stub.listing = []
        stub.reset()
        got = call(base, "GET", BASE, "org1:viewer")
        check(
            "LIST",
            "an empty project is an empty page",
            (got.status, got.json) == (200, {"experiments": []}),
        )
        for label, query, expected in (
            ("limit 1", "limit=1", {"limit": 1, "after": None}),
            ("limit 200", "limit=200", {"limit": 200, "after": None}),
            ("a cursor", "after_name=exp-a&after_version=2", {"limit": 50, "after": ("exp-a", 2)}),
            (
                "a one-character cursor name",
                "after_name=a&after_version=1",
                {"limit": 50, "after": ("a", 1)},
            ),
            ("limit 10", "limit=10", {"limit": 10, "after": None}),
            (
                "a cursor and a limit",
                "limit=7&after_name=a%20b&after_version=2147483647",
                {"limit": 7, "after": ("a b", 2147483647)},
            ),
            (
                "a 200-char cursor name",
                "after_name=" + "n" * 200 + "&after_version=1",
                {"limit": 50, "after": ("n" * 200, 1)},
            ),
        ):
            stub.reset()
            got = call(base, "GET", f"{BASE}?{query}", "org1:viewer")
            check(
                "LIST",
                f"{label} reaches the store as {expected}",
                got.status == 200 and len(stub.calls) == 1 and stub.calls[0][4] == expected,
            )
        for label, query in (
            ("limit 0", "limit=0"),
            ("limit 00", "limit=00"),
            ("limit padded", "limit=%205"),
            ("after_version 01", "after_name=a&after_version=01"),
            ("limit 201", "limit=201"),
            ("limit -1", "limit=-1"),
            ("limit 1.0", "limit=1.0"),
            ("limit +5", "limit=%2B5"),
            ("limit 05", "limit=05"),
            ("limit empty", "limit="),
            ("limit abc", "limit=abc"),
            ("limit full-width digit", "limit=%EF%BC%95"),
            ("limit 11 digits", "limit=00000000005"),
            ("limit twice", "limit=5&limit=6"),
            ("an unknown parameter", "offset=5"),
            ("after_name alone", "after_name=a"),
            ("after_version alone", "after_version=1"),
            ("after_name empty", "after_name=&after_version=1"),
            ("after_name 201 chars", "after_name=" + "n" * 201 + "&after_version=1"),
            ("after_version 0", "after_name=a&after_version=0"),
            ("after_version 2^31", "after_name=a&after_version=2147483648"),
            ("after_version text", "after_name=a&after_version=x"),
            ("after_name twice", "after_name=a&after_name=b&after_version=1"),
        ):
            stub.reset()
            got = call(base, "GET", f"{BASE}?{query}", "org1:viewer")
            check(
                "LIST",
                f"{label}: 400 invalid_body, the store is never called ({got.status} {got.detail})",
                (
                    (got.status, got.detail) == (400, "invalid_body")
                    and stub.calls == []
                    and stub.sessions == []
                ),
            )

        # ---- get
        stub.reset()
        stub.listing = [stub.experiment]
        got = call(base, "GET", f"{BASE}/{EXPERIMENT}", "org1:viewer")
        op, session, context, project_id, experiment_id = stub.calls[0]
        check(
            "GET",
            "get calls the store with the path's project and experiment ids",
            (
                op == "get"
                and project_id == PROJECT
                and experiment_id == EXPERIMENT
                and context.organization_id == ORG1
                and stub.events == ["open", "commit"]
            ),
        )
        check(
            "GET",
            "the body is the serialized experiment and validates",
            (
                got.status == 200
                and got.json == expected_body(stub.experiment)
                and P1._validate(defs["TrainingExperiment"], defs, got.json)
            ),
        )
        for label, path in (
            ("a malformed experiment id", f"{BASE}/not-a-uuid"),
            (
                "a malformed project id",
                f"/api/v1/organizations/{ORG1}/projects/not-a-uuid/training/experiments",
            ),
        ):
            stub.reset()
            got = call(base, "GET", path, "org1:viewer")
            check(
                "GET",
                f"{label} never reaches the store ({got.status})",
                got.status in (400, 404, 422) and stub.calls == [],
            )
        for method, path in (
            ("PUT", f"{BASE}/{EXPERIMENT}"),
            ("PATCH", f"{BASE}/{EXPERIMENT}"),
            ("DELETE", f"{BASE}/{EXPERIMENT}"),
            ("DELETE", BASE),
            ("POST", f"{BASE}/{EXPERIMENT}"),
        ):
            stub.reset()
            got = call(base, method, path, "org1:organization_admin", post_body())
            check(
                "GET",
                f"{method} is not a route ({got.status})",
                got.status == 405 and stub.calls == [],
            )
        for suffix in ("/estimate", f"/{EXPERIMENT}/runs", f"/{EXPERIMENT}/cancel"):
            got = call(
                base,
                "GET",
                BASE.replace("/experiments", "") + suffix
                if suffix == "/estimate"
                else BASE + suffix,
                "org1:organization_admin",
            )
            check("GET", f"no placeholder route {suffix} ({got.status})", got.status == 404)


def other_org_path() -> str:
    return f"/api/v1/organizations/{ORG2}/projects/{PROJECT}/training/experiments"


# ---------------------------------------------------------------------------------------------
# W: wiring
# ---------------------------------------------------------------------------------------------


def _full_app_env() -> dict[str, str]:
    return {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": "http://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "http://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "http://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "http://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "http://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }


def part_w() -> None:
    main = MAIN_PATH.read_text(encoding="utf-8")
    check(
        "WIRED",
        "main.py imports the router factory",
        (
            "from app.training.training_experiment_routes import build_training_experiment_router\n"
            in main
        ),
    )
    check(
        "WIRED",
        "main.py mounts it once with the shared resolver and lookup",
        len(
            re.findall(
                r"app\.include_router\(\s*build_training_experiment_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)\s*\)",
                main,
            )
        )
        == 1
        and main.count("build_training_experiment_router") == 2,
    )
    callers = find_callers("build_training_experiment_router", SOURCE_ROOTS)
    check(
        "WIRED",
        f"the live caller of build_training_experiment_router is main.py: {callers}",
        (any(str(c).endswith("apps/api/app/main.py") for c in callers)),
    )
    env = _full_app_env()
    added = set(env) - set(os.environ)
    old = {k: os.environ[k] for k in env if k in os.environ}
    os.environ.update(env)
    try:
        import app.main as main_module

        app = main_module.create_app()
    finally:
        for key in added:
            os.environ.pop(key, None)
        os.environ.update(old)

    def flatten(routes: Any, context: Any = None) -> list[tuple[str, Any]]:
        """(effective path, route), descending through FastAPI's lazily included routers."""
        out: list[tuple[str, Any]] = []
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                child = route.include_context
                if context is not None:
                    child = context.combine(child)
                out.extend(flatten(route.original_router.routes, child))
            elif getattr(route, "methods", None):
                out.append((context.path_for(route) if context is not None else route.path, route))
        return out

    served_ops = {
        (path, method.lower())
        for path, route in flatten(list(app.routes))
        for method in route.methods
        if "/training/" in path and method not in ("HEAD", "OPTIONS")
    }
    check(
        "WIRED",
        f"the real app serves exactly the three training operations: {sorted(served_ops)}",
        served_ops == OPERATIONS,
    )
    with running_app_for(app) as base:
        for method, path, body in (
            ("POST", BASE, post_body()),
            ("GET", BASE, None),
            ("GET", f"{BASE}/{EXPERIMENT}", None),
        ):
            got = call(base, method, path, None, body)
            check(
                "WIRED",
                f"the real app answers {method} without a session with 401 ({got.status} {got.detail})",
                ((got.status, got.detail) == (401, "not_authenticated")),
            )
            got = call(base, method, path, "org1:organization_admin", body)
            check(
                "WIRED",
                f"the real app ignores a forged test-principal header on {method} ({got.status})",
                got.status == 401,
            )


# ---------------------------------------------------------------------------------------------
# F: fault-injected copies of the route module
# ---------------------------------------------------------------------------------------------

FAULTS: list[tuple[str, str, str]] = [
    (
        "router without require_role on reads",
        "    read_dependency = Depends(\n        require_role(READ_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup)\n    )",
        "    read_dependency = Depends(lambda: AuthorizedContext(principal=None, organization_id=uuid.UUID(int=1), role=Role.VIEWER))",
    ),
    (
        "writers widened to every role",
        "            WRITE_ROLES,\n            principal_resolver=principal_resolver,",
        "            ALL_ROLES,\n            principal_resolver=principal_resolver,",
    ),
    (
        "writers widened to the evaluator",
        "WRITE_ROLES: Final = frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN})",
        "WRITE_ROLES: Final = frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN, Role.EVALUATOR})",
    ),
    (
        "writers widened to the viewer",
        "WRITE_ROLES: Final = frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN})",
        "WRITE_ROLES: Final = frozenset({Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN, Role.VIEWER})",
    ),
    (
        "readers narrowed",
        "READ_ROLES: Final = ALL_ROLES",
        "READ_ROLES: Final = frozenset({Role.ORGANIZATION_ADMIN, Role.ML_ENGINEER})",
    ),
    (
        "foreign is 403 instead of 404",
        'HTTPException(status_code=404, detail="not_found")',
        'HTTPException(status_code=403, detail="not_found")',
    ),
    (
        "store status replaced by 400",
        "raise HTTPException(status_code=exc.status, detail=exc.code) from None\n        return JSONResponse(body, status_code=200 if replayed else 201)",
        "raise HTTPException(status_code=400, detail=exc.code) from None\n        return JSONResponse(body, status_code=200 if replayed else 201)",
    ),
    ("a replay answers 201", "status_code=200 if replayed else 201", "status_code=201"),
    ("a create answers 200", "status_code=200 if replayed else 201", "status_code=200"),
    ("body cap removed", "    if len(raw) > MAX_BODY_BYTES:\n        raise _invalid_body()\n", ""),
    (
        "body cap widened",
        "MAX_BODY_BYTES: Final = 64 * 1024",
        "MAX_BODY_BYTES: Final = 64 * 1024 + 1",
    ),
    ("extra body keys accepted", "set(payload) != _BODY_KEYS", "not _BODY_KEYS <= set(payload)"),
    (
        "duplicate keys accepted",
        "raw, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant",
        "raw, parse_constant=_reject_constant",
    ),
    (
        "NaN accepted",
        "raw, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant",
        "raw, object_pairs_hook=_reject_duplicates",
    ),
    (
        "the project id is not the path's",
        '                    project_id,\n                    name=payload["name"],',
        '                    uuid.UUID(int=7),\n                    name=payload["name"],',
    ),
    (
        "another project's experiment served",
        "session, tenant_context, project_id, experiment_id\n",
        "session, tenant_context, experiment_id, experiment_id\n",
    ),
    ("actor id from the body", "actor_id=ctx.principal.subject,", 'actor_id="system",'),
    ("actor role hardcoded", "role=ctx.role.value,", 'role="organization_admin",'),
    (
        "the idempotency key is served",
        '        "id": str(experiment.id),',
        '        "id": str(experiment.id),\n        "idempotency_key": experiment.idempotency_key,',
    ),
    (
        "the request digest is served",
        '        "id": str(experiment.id),',
        '        "id": str(experiment.id),\n        "request_sha256": experiment.request_sha256,',
    ),
    (
        "unknown query parameters ignored",
        "len(keys) != len(set(keys)) or not set(keys) <= _QUERY_KEYS",
        "len(keys) != len(set(keys))",
    ),
    (
        "page limit widened",
        "limit = _decimal(raw_limit, 1, MAX_PAGE_SIZE)",
        "limit = _decimal(raw_limit, 1, MAX_PAGE_SIZE + 1)",
    ),
    (
        "half a cursor accepted",
        "if name is None or raw_version is None or not 1 <= len(name) <= NAME_MAX_LENGTH:",
        "if not 1 <= len(name or 'x') <= NAME_MAX_LENGTH:",
    ),
    (
        "a placeholder route",
        "    return router\n",
        "    @router.post(EXPERIMENTS_PATH.replace('experiments', 'estimate'))\n    async def estimate() -> JSONResponse:\n        return JSONResponse({})\n\n    return router\n",
    ),
]


def run_route_suite(path: Path, name: str) -> list[str]:
    FAILURES.clear()
    mod = _load(path, name)
    openapi_text = OPENAPI_PATH.read_text(encoding="utf-8")
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    findings = contract_findings(openapi_text, schema_text, mod)
    check("CONTRACT", f"the contract equals this module's router: {findings}", findings == [])
    route_suite(mod)
    return list(FAILURES)


def part_f() -> None:
    source = ROUTES_PATH.read_text(encoding="utf-8")
    kept = list(FAILURES)
    results: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, (label, old, new) in enumerate(FAULTS):
            if source.count(old) != 1:
                results.append(
                    f"FAIL [{CID} FAULT] {label}: patch text occurs {source.count(old)} time(s), expected 1"
                )
                continue
            copy_path = Path(tmp) / f"training_experiment_routes_fault_{index}.py"
            copy_path.write_text(source.replace(old, new), encoding="utf-8")
            try:
                red = bool(run_route_suite(copy_path, f"ftf_0701_route_fault_{index}"))
            except Exception:  # noqa: BLE001
                red = True
            CHECKS[0] += 1
            if not red:
                results.append(f"FAIL [{CID} FAULT] {label} stayed GREEN")
    FAILURES.clear()
    FAILURES.extend(kept + results)


def main() -> None:
    part_c()
    route_suite(real_routes)
    part_w()
    if not FAILURES and not UNDER_MUTATION_GATE:
        part_f()
    if FAILURES:
        for failure in FAILURES[:80]:
            print(failure, file=sys.stderr)
        print(f"{len(FAILURES)} failure(s) of {CHECKS[0]} checks", file=sys.stderr)
        sys.exit(1)
    print(f"OK {CID}: {CHECKS[0]} checks")


if __name__ == "__main__":
    main()
