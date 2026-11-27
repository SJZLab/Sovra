#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,method-assign,misc,func-returns-value,union-attr,index"
# ruff: noqa: E501
"""FTF-0701 (P3) real proof: the training experiment routes on the REAL `create_app()` over a live
PostgreSQL 16 as the restricted `sovra_app` role, through the live FTF-0103A auth path (signed
session cookie -> `SessionPrincipalResolver` -> database membership lookup -> `require_role`).

Fixtures come from the FTF-0701-P2 proof (`tests/integration/test_ftf_0701_p2_training_experiments
.py`): a locked split manifest, a ready licensed artifact and a budget policy built through the
certified APIs. Sessions are minted with the application's own signing key; memberships are written
with the certified `grant_membership`.

Needs SOVRA_DATABASE_URL (the migration role) and SOVRA_APP_DB_PASSWORD. Run as CI does:
`uv run python3 tests/integration/test_ftf_0701_routes.py`.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import test_ftf_0519_p1_split_manifests as split_proof  # noqa: E402
import test_ftf_0701_p2_training_experiments as p2  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from workers.training import training_config_schema as schema  # noqa: E402

from app.auth import membership_store, oidc_login  # noqa: E402
from app.auth.oidc_login import SESSION_COOKIE_NAME  # noqa: E402
from app.auth.rbac_middleware import Role  # noqa: E402
from app.datasets import dataset_source_connection_crud as connection_crud  # noqa: E402
from app.training import training_experiment_store as store  # noqa: E402

FAILURES: list[str] = []
CHECKS = [0]
CID = "FTF-0701"
ISSUER = "http://idp.internal.example"
SIGNING_KEY = b"ftf-0701-routes-signing-key-not-a-real-secret"
WRITERS = {Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN}
CANARY = "CANARY0701ROUTES"


def check(description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def _finish() -> None:
    failures = FAILURES + p2.FAILURES + split_proof.FAILURES
    if failures:
        print(f"{CID} training experiment routes real proof: {len(failures)} of {CHECKS[0]} failed")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)
    print(f"{CID} training experiment routes real proof: all {CHECKS[0]} checks passed")
    sys.exit(0)


def _p1() -> Any:
    path = ROOT / "tests" / "tasks" / "test_ftf_0701_p1.py"
    spec = importlib.util.spec_from_file_location("ftf_0701_p1_for_routes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    os.environ["FTF_0701_P1_NO_MAIN"] = "1"
    try:
        spec.loader.exec_module(module)
    finally:
        os.environ.pop("FTF_0701_P1_NO_MAIN", None)
    return module


P1 = _p1()
DEFS = json.loads(
    (ROOT / "packages" / "contracts" / "jsonschema" / "07-domain.schema.json").read_text()
)["$defs"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def serve_real_app(app_url: str) -> Iterator[str]:
    """The REAL `create_app()`, configured through the environment as production configures it."""
    port = _free_port()
    env = {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": app_url,
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": ISSUER,
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": f"http://127.0.0.1:{port}/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": f"{ISSUER}/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": f"{ISSUER}/token",
        "SOVRA_OIDC_JWKS_URI": f"{ISSUER}/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": f"http://127.0.0.1:{port}",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": SIGNING_KEY.decode(),
        "SOVRA_SESSION_TTL_SECONDS": "900",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        import app.main as main_module

        app = main_module.create_app()
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


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


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict[str, str] = {}

    def login(self, label: str, role: Role) -> str:
        """A real signed session for `user-<label>-<role>` (the application's own cookie format)."""
        subject = f"user-{label}-{role.value}"
        token, _csrf, _jti = oidc_login._issue_session_cookie(
            SIGNING_KEY, subject=subject, issuer=ISSUER, email=None
        )
        self.cookies[f"{label}:{role.value}"] = token
        return subject

    def call(self, method: str, path: str, who: str | None, body: Any = None) -> Http:
        headers: dict[str, str] = {}
        if who is not None:
            headers["Cookie"] = f"{SESSION_COOKIE_NAME}={self.cookies.get(who, who)}"
        payload = None
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        host, port = self.base.split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=30)
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            return Http(response.status, response.read())
        finally:
            conn.close()


def base_path(fx: Any) -> str:
    return f"/api/v1/organizations/{fx.ctx.organization_id}/projects/{fx.project_id}/training/experiments"


def post(fx: Any, name: str, key: str, **over: Any) -> dict[str, Any]:
    return {"name": name, "idempotency_key": key, "config": fx.body(**over)}


# code -> a request change that reaches it on the live route with a real fixture
SHAPE_MUTATIONS: dict[str, dict[str, Any]] = {
    "invalid_body": {"trust_remote_code": True},
    "unsupported_schema_version": {"schema_version": "training_config.v2"},
    "invalid_mode": {"mode": "manual"},
    "invalid_method": {"method": "dora"},
    "invalid_source_role": {"data__train__role": "validation"},
    "split_manifest_mismatch": {"data__validation__split_manifest_id": str(uuid.uuid4())},
    "invalid_tokenizer": {"tokenizer__source": "hub"},
    "invalid_template": {"template__loss_policy": "all_tokens"},
    "packing_not_qualified": {"template__packing": True},
    "invalid_lora_rank": {"adapter__lora_rank": 0},
    "invalid_lora_alpha": {"adapter__lora_alpha": 0},
    "invalid_lora_dropout": {"adapter__lora_dropout": "0.9"},
    "invalid_target_modules": {"adapter__target_modules": []},
    "invalid_learning_rate": {"hyperparameters__learning_rate": "1"},
    "invalid_epochs": {"hyperparameters__epochs": 0},
    "invalid_warmup_ratio": {"hyperparameters__warmup_ratio": "0.9"},
    "invalid_max_seq_length": {"hyperparameters__max_seq_length": 3000},
    "invalid_batch_size": {"hyperparameters__micro_batch_size": 0},
    "invalid_gradient_accumulation": {"hyperparameters__gradient_accumulation_steps": 0},
    "invalid_optimizer": {"hyperparameters__optimizer": "sgd"},
    "invalid_scheduler": {"hyperparameters__scheduler": "step"},
    "invalid_weight_decay": {"hyperparameters__weight_decay": "2"},
    "invalid_max_grad_norm": {"hyperparameters__max_grad_norm": "0"},
    "invalid_seed": {"hyperparameters__seed": -1},
    "invalid_checkpoint_policy": {"hyperparameters__checkpoint_interval_steps": 0},
    "unsupported_dtype": {"precision__compute_dtype": "int8"},
    "invalid_quantization": {"precision__quantization": "int4"},
    "method_quantization_mismatch": {"method": "qlora"},
    "invalid_budget": {"budget__max_cost": "0"},
    "invalid_currency": {"budget__currency": "usd"},
    "heldout_training_source_denied": {"data__train__role": "final_qualification"},
    "method_not_enabled": {"method": "full_sft"},
    "auto_rule_version_unknown": {"mode": "auto", "auto_rule_version": "v1"},
    "split_manifest_changed": {"data__frozen_manifest_sha256": "0" * 64},
    "artifact_not_trainable": {"base__trainable_artifact_id": str(uuid.uuid4())},
    "tokenizer_revision_mismatch": {"tokenizer__revision": "rev2"},
    "catalog_mapping_changed": {
        "base__model_catalog_entry_id": str(uuid.uuid4()),
        "base__catalog_version": 1,
    },
    "budget_currency_mismatch": {"budget__currency": "EUR"},
    "budget_limit_exceeds_policy": {"budget__max_cost": "1000.0001"},
}
# Reached on the live route below with their own fixture or request.
OWN_SCENARIO = {
    "split_manifest_not_locked",
    "license_not_accepted",
    "idempotency_key_reused",
    "dataset_grant_changed",
    "budget_policy_missing",
    "budget_scope_not_authorized",
    "not_found",
}
# Not reachable through a request against an honest database: each needs a corrupted row or a
# concurrent writer. They are proven at the store against PostgreSQL in the FTF-0701-P2 proof and,
# for the status mapping of every code on every operation, in tests/tasks/test_ftf_0701.py.
STORE_PROOF_ONLY = {
    "split_manifest_tampered",
    "quality_report_changed",
    "train_split_empty",
    "validation_split_empty",
    "catalog_artifact_mismatch",
    "invalid_facts",
    "version_conflict",
    "config_integrity_failed",
    "actor_not_permitted",
}


def counts(env: Any) -> tuple[int, int]:
    result: tuple[int, int] = p2.counts(env)
    return result


def section_roles(env: Any, client: Client, fx: Any, exp_id: str) -> None:
    base = base_path(fx)
    for role in Role:
        who = f"org1:{role.value}"
        before = counts(env)
        got = client.call(
            "POST", base, who, post(fx, f"role-{role.value}", f"key-role-{role.value}")
        )
        if role in WRITERS:
            check(f"{role.value} POST is 201 ({got.status} {got.detail})", got.status == 201)
            check(
                f"{role.value} POST wrote one row and one audit event",
                counts(env) == (before[0] + 1, before[1] + 1),
            )
        else:
            check(
                f"{role.value} POST is 403 role_not_authorized ({got.status} {got.detail})",
                (got.status, got.detail) == (403, "role_not_authorized"),
            )
            check(f"{role.value} POST wrote nothing", counts(env) == before)
        got = client.call("GET", base, who)
        check(
            f"{role.value} list is 200 ({got.status})",
            got.status == 200 and isinstance(got.json.get("experiments"), list),
        )
        got = client.call("GET", f"{base}/{exp_id}", who)
        check(
            f"{role.value} get is 200 ({got.status})",
            got.status == 200 and got.json["id"] == exp_id,
        )
    for method, path, body in (
        ("POST", base, post(fx, "anon", "key-anon")),
        ("GET", base, None),
        ("GET", f"{base}/{exp_id}", None),
    ):
        before = counts(env)
        got = client.call(method, path, None, body)
        check(
            f"no session: {method} is 401 not_authenticated",
            (got.status, got.detail) == (401, "not_authenticated"),
        )
        got = client.call(method, path, "forged.session.token", body)
        check(f"a forged session: {method} is 401", got.status == 401)
        forged, _, _ = oidc_login._issue_session_cookie(
            b"another-signing-key",
            subject="user-org1-organization_admin",
            issuer=ISSUER,
            email=None,
        )
        got = client.call(method, path, forged, body)
        check(
            f"a session signed with another key: {method} is 401",
            got.status == 401 and counts(env) == before,
        )
        stranger, _, _ = oidc_login._issue_session_cookie(
            SIGNING_KEY, subject="user-with-no-membership", issuer=ISSUER, email=None
        )
        got = client.call(method, path, stranger, body)
        check(
            f"a valid session with no membership: {method} is 403 ({got.status} {got.detail})",
            got.status == 403 and counts(env) == before,
        )
        other_issuer, _, _ = oidc_login._issue_session_cookie(
            SIGNING_KEY,
            subject="user-org1-organization_admin",
            issuer="http://other-idp.example",
            email=None,
        )
        got = client.call(method, path, other_issuer, body)
        check(
            f"the same subject from another issuer has no role: {method} is 403 ({got.status})",
            got.status == 403 and counts(env) == before,
        )


def section_create(env: Any, client: Client, fx: Any) -> str:
    base = base_path(fx)
    before = counts(env)
    body = post(fx, "exp-route", "key-route-1")
    got = client.call("POST", base, "org1:ml_engineer", body)
    check(f"POST is 201 ({got.status} {got.detail})", got.status == 201)
    if got.status != 201:
        _finish()
    exp = got.json
    check(
        "the 201 body validates against TrainingExperiment",
        P1._validate(DEFS["TrainingExperiment"], DEFS, exp),
    )
    check("one row and one audit event", counts(env) == (before[0] + 1, before[1] + 1))
    row = env.rows(
        "SELECT organization_id, project_id, name, version, parent_version, status, method, "
        "config_json, config_sha256, requested_by, max_cost, security_profile, idempotency_key "
        "FROM training_experiments WHERE id=:i",
        i=uuid.UUID(exp["id"]),
    )[0]
    check(
        "the body is the stored row",
        (
            exp["organization_id"],
            exp["project_id"],
            exp["name"],
            exp["version"],
            exp["parent_version"],
            exp["status"],
            exp["method"],
            exp["config"],
            exp["config_sha256"],
            exp["requested_by"],
            Decimal(exp["max_cost"]),
            exp["security_profile"],
        )
        == (
            str(row[0]),
            str(row[1]),
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
            row[11],
        ),
    )
    check(
        "the actor is the session's subject",
        exp["requested_by"] == "user-org1-ml_engineer" and row[12] == "key-route-1",
    )
    check(
        "config_sha256 is an independent hash of the served document",
        exp["config_sha256"]
        == hashlib.sha256(
            json.dumps(
                exp["config"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest(),
    )
    raw = got.raw.decode()
    check(
        "the body carries no idempotency key, request digest, record id or grant snapshot",
        "key-route-1" not in raw
        and "idempotency" not in raw
        and "request_sha256" not in raw
        and "record_id" not in raw
        and "allowed_purposes" not in raw
        and "secret" not in raw,
    )
    audit = env.rows(
        "SELECT actor_id, details_json FROM audit_events WHERE resource_id=:r AND action='training_experiment.create'",
        r=exp["id"],
    )
    check(
        "the audit row names the session's subject and is content-free",
        (
            len(audit) == 1
            and audit[0][0] == "user-org1-ml_engineer"
            and set(audit[0][1]) == {"project_id", "version", "method", "config_sha256"}
        ),
    )
    # replay
    before = counts(env)
    again = client.call("POST", base, "org1:ml_engineer", body)
    check(
        f"a replay is 200 with the same body ({again.status})",
        again.status == 200 and again.json == exp,
    )
    check("a replay writes no row and no audit event", counts(env) == before)
    reused = client.call(
        "POST",
        base,
        "org1:ml_engineer",
        post(fx, "exp-route", "key-route-1", hyperparameters__epochs=4),
    )
    check(
        "the same key with another body is 409 idempotency_key_reused",
        (reused.status, reused.json) == (409, {"detail": "idempotency_key_reused"})
        and counts(env) == before,
    )
    v2 = client.call(
        "POST",
        base,
        "org1:organization_admin",
        post(fx, "exp-route", "key-route-2", hyperparameters__epochs=4),
    )
    check(
        "a changed config by another writer is version 2 with its parent",
        (
            v2.status == 201
            and (v2.json["version"], v2.json["parent_version"], v2.json["requested_by"])
            == (2, 1, "user-org1-organization_admin")
        ),
    )
    # reads
    one = client.call("GET", f"{base}/{exp['id']}", "org1:viewer")
    check("GET returns the same experiment", one.status == 200 and one.json == exp)
    page = client.call("GET", base, "org1:viewer")
    check(
        "the list validates against TrainingExperimentList",
        page.status == 200 and P1._validate(DEFS["TrainingExperimentList"], DEFS, page.json),
    )
    names = [(e["name"], e["version"]) for e in page.json["experiments"]]
    expected = [
        (r[0], r[1])
        for r in env.rows(
            'SELECT name, version FROM training_experiments WHERE project_id=:p ORDER BY name COLLATE "C", version',
            p=fx.project_id,
        )
    ]
    check(
        f"the list is every experiment of the project, by name then version: {names}",
        names == expected and exp in page.json["experiments"],
    )
    first = client.call("GET", f"{base}?limit=1", "org1:viewer").json["experiments"]
    rest = client.call(
        "GET",
        f"{base}?limit=200&after_name={first[0]['name']}&after_version={first[0]['version']}",
        "org1:viewer",
    ).json["experiments"]
    check(
        "paging continues without a gap or a repeat",
        [(e["name"], e["version"]) for e in first + rest] == expected,
    )
    for label, query in (
        ("limit 0", "limit=0"),
        ("limit 201", "limit=201"),
        ("half a cursor", "after_name=a"),
        ("unknown", "offset=1"),
    ):
        got = client.call("GET", f"{base}?{query}", "org1:viewer")
        check(
            f"list {label} is 400 invalid_body",
            (got.status, got.json) == (400, {"detail": "invalid_body"}),
        )
    return str(exp["id"])


def section_bodies(env: Any, client: Client, fx: Any) -> None:
    base = base_path(fx)
    valid = json.dumps(post(fx, "exp-body", "key-body")).encode()
    for label, raw in (
        ("malformed JSON", b'{"name": '),
        ("a non-object body", b'["name"]'),
        ("an empty body", b""),
        ("an oversized body", valid[:-1] + b" " * (64 * 1024) + b"}"),
        ("a missing key", json.dumps({"name": "a", "config": fx.body()}).encode()),
        ("an extra key", json.dumps({**post(fx, "a", "k"), "status": "submitted"}).encode()),
        ("a duplicate key", valid[:-1] + b', "name": "other"}'),
    ):
        before = counts(env)
        got = client.call("POST", base, "org1:ml_engineer", raw)
        check(
            f"{label} is 400 invalid_body ({got.status} {got.detail})",
            (got.status, got.json) == (400, {"detail": "invalid_body"}),
        )
        check(f"{label} wrote nothing", counts(env) == before)
    before = counts(env)
    got = client.call(
        "POST",
        base,
        "org1:ml_engineer",
        {**post(fx, "a", "k-tenant"), "organization_id": str(uuid.uuid4())},
    )
    check(
        "a body naming another organization is 403 tenant_mismatch",
        (got.status, got.detail) == (403, "tenant_mismatch") and counts(env) == before,
    )


def section_codes(env: Any, client: Client, fx: Any) -> None:
    base = base_path(fx)
    covered = set(SHAPE_MUTATIONS) | OWN_SCENARIO | STORE_PROOF_ONLY
    check(
        f"every code of the table is accounted for: missing {sorted(set(store.STORE_ERROR_CODES) - covered)}, extra {sorted(covered - set(store.STORE_ERROR_CODES))}",
        covered == set(store.STORE_ERROR_CODES)
        and not (set(SHAPE_MUTATIONS) & OWN_SCENARIO)
        and not (covered - STORE_PROOF_ONLY) & STORE_PROOF_ONLY,
    )
    for code, over in SHAPE_MUTATIONS.items():
        before = counts(env)
        got = client.call(
            "POST", base, "org1:ml_engineer", post(fx, "exp-code", f"key-code-{code}", **over)
        )
        status = store.STORE_ERROR_CODES[code]
        check(
            f"{code} is reachable with status {status} ({got.status} {got.json})",
            (got.status, got.json) == (status, {"detail": code}),
        )
        check(f"{code} wrote nothing", counts(env) == before)
    # held-out: both slots, every held-out role, with an independent count
    for slot in ("train", "validation"):
        for role in ("final_qualification", "development_eval", "heldout_test"):
            got = client.call(
                "POST",
                base,
                "org1:organization_admin",
                post(fx, "exp-held", f"key-held-{slot}-{role}", **{f"data__{slot}__role": role}),
            )
            check(
                f"held-out {role} in the {slot} slot is 403 heldout_training_source_denied",
                (got.status, got.json) == (403, {"detail": "heldout_training_source_denied"}),
            )
    check(
        "no stored experiment names a held-out role (independent count)",
        int(
            env.scalar(
                "SELECT count(*) FROM training_experiments WHERE config_json#>>'{data,train,role}' <> 'train' "
                "OR config_json#>>'{data,validation,role}' <> 'validation'"
            )
            or 0
        )
        == 0,
    )
    # own scenarios
    unknown = str(uuid.uuid4())
    got = client.call(
        "POST",
        base,
        "org1:ml_engineer",
        post(
            fx,
            "exp-code",
            "key-code-nl",
            data__train__split_manifest_id=unknown,
            data__validation__split_manifest_id=unknown,
        ),
    )
    check(
        "an unknown manifest is 409 split_manifest_not_locked",
        (got.status, got.json) == (409, {"detail": "split_manifest_not_locked"}),
    )
    registered = fx.new_artifact("route-registered", ready=False)
    got = client.call(
        "POST",
        base,
        "org1:ml_engineer",
        post(fx, "exp-code", "key-code-lic", base__trainable_artifact_id=str(registered)),
    )
    check(
        "an unlicensed artifact is 409 license_not_accepted",
        (got.status, got.json) == (409, {"detail": "license_not_accepted"}),
    )


def section_isolation(env: Any, client: Client, fx: Any, foreign: Any, exp_id: str) -> None:
    base, foreign_base = base_path(fx), base_path(foreign)
    made = client.call(
        "POST", foreign_base, "org2:ml_engineer", post(foreign, "exp-foreign", "key-foreign-1")
    )
    check(f"org2's writer creates in org2 ({made.status} {made.detail})", made.status == 201)
    foreign_id = made.json["id"] if made.status == 201 else str(uuid.uuid4())
    sibling = env.world("routes-sibling")
    sibling_base = f"/api/v1/organizations/{fx.ctx.organization_id}/projects/{sibling.project_id}/training/experiments"
    missing = client.call("GET", f"{base}/{uuid.uuid4()}", "org1:organization_admin")
    check(
        "a missing id is 404 not_found",
        (missing.status, missing.json) == (404, {"detail": "not_found"}),
    )
    for label, method, path, who, body in (
        (
            "org1 reading org2's experiment under its own project",
            "GET",
            f"{base}/{foreign_id}",
            "org1:organization_admin",
            None,
        ),
        (
            "org2 reading org1's experiment under its own project",
            "GET",
            f"{foreign_base}/{exp_id}",
            "org2:organization_admin",
            None,
        ),
        (
            "org1's experiment under a sibling project of org1",
            "GET",
            f"{sibling_base}/{exp_id}",
            "org1:organization_admin",
            None,
        ),
        (
            "org2 reading org1's project id under org2's path",
            "GET",
            f"/api/v1/organizations/{foreign.ctx.organization_id}/projects/{fx.project_id}/training/experiments/{exp_id}",
            "org2:organization_admin",
            None,
        ),
        (
            "org2 listing org1's project id under org2's path",
            "GET",
            f"/api/v1/organizations/{foreign.ctx.organization_id}/projects/{fx.project_id}/training/experiments",
            "org2:organization_admin",
            None,
        ),
        (
            "org2 creating in org1's project id under org2's path",
            "POST",
            f"/api/v1/organizations/{foreign.ctx.organization_id}/projects/{fx.project_id}/training/experiments",
            "org2:organization_admin",
            post(fx, "x", "key-x1"),
        ),
        (
            "a project id that does not exist",
            "POST",
            f"/api/v1/organizations/{fx.ctx.organization_id}/projects/{uuid.uuid4()}/training/experiments",
            "org1:organization_admin",
            post(fx, "x", "key-x2"),
        ),
    ):
        before = counts(env)
        got = client.call(method, path, who, body)
        check(
            f"{label}: the identical 404 ({got.status} {got.json})",
            (got.status, got.raw) == (missing.status, missing.raw) and counts(env) == before,
        )
    for label, method, path, who, body in (
        (
            "org2's admin on org1's path (get)",
            "GET",
            f"{base}/{exp_id}",
            "org2:organization_admin",
            None,
        ),
        ("org2's admin on org1's path (list)", "GET", base, "org2:organization_admin", None),
        (
            "org2's writer on org1's path (create)",
            "POST",
            base,
            "org2:ml_engineer",
            post(fx, "x", "key-x3"),
        ),
    ):
        before = counts(env)
        got = client.call(method, path, who, body)
        check(
            f"{label}: 403 role_not_authorized, nothing served or written ({got.status} {got.detail})",
            (got.status, got.detail) == (403, "role_not_authorized")
            and exp_id not in got.raw.decode()
            and counts(env) == before,
        )
    page = client.call("GET", sibling_base, "org1:viewer")
    check(
        "a sibling project lists none of this project's experiments",
        (page.status, page.json) == (200, {"experiments": []}),
    )
    page = client.call("GET", foreign_base, "org2:organization_admin")
    check(
        "org2's list holds only org2's experiment",
        [e["id"] for e in page.json["experiments"]] == [foreign_id],
    )
    # a foreign artifact or manifest named in org2's own project never crosses the tenant line
    got = client.call(
        "POST",
        foreign_base,
        "org2:ml_engineer",
        post(foreign, "x", "key-x4", base__trainable_artifact_id=str(fx.artifact_id)),
    )
    check(
        "org2 naming org1's artifact is 409 artifact_not_trainable",
        (got.status, got.json) == (409, {"detail": "artifact_not_trainable"}),
    )
    got = client.call(
        "POST",
        foreign_base,
        "org2:ml_engineer",
        post(
            foreign,
            "x",
            "key-x5",
            data__train__split_manifest_id=str(fx.manifest_id),
            data__validation__split_manifest_id=str(fx.manifest_id),
            data__frozen_manifest_sha256=fx.frozen,
        ),
    )
    check(
        "org2 naming org1's manifest is 409 split_manifest_not_locked",
        (got.status, got.json) == (409, {"detail": "split_manifest_not_locked"}),
    )


def section_live_facts(env: Any, client: Client) -> None:
    """budget_policy_missing, budget_scope_not_authorized and dataset_grant_changed on the route."""
    bare = p2.Fixture(env, "routes-nobudget")
    got = client.call("POST", base_path(bare), "org1:ml_engineer", post(bare, "x", "key-nb"))
    check(
        "a project with no budget policy is 409 budget_policy_missing",
        (got.status, got.json) == (409, {"detail": "budget_policy_missing"}),
    )
    bare.budget(scopes=("evaluation",))
    got = client.call("POST", base_path(bare), "org1:ml_engineer", post(bare, "x", "key-scope"))
    check(
        "a policy without the training scope is 409 budget_scope_not_authorized",
        (got.status, got.json) == (409, {"detail": "budget_scope_not_authorized"}),
    )
    live = p2.Fixture(env, "routes-grant")
    live.budget()
    ok = client.call("POST", base_path(live), "org1:ml_engineer", post(live, "x", "key-grant-0"))
    check(f"control: the grant fixture creates ({ok.status} {ok.detail})", ok.status == 201)
    _, error = live.run(
        lambda s: connection_crud.revoke_dataset_source_connection(
            s,
            live.ctx,
            live.project_id,
            live.world.conns["all"],
            actor=connection_crud.ActorInfo("user", "user-org-admin", "req-0701-routes"),
        )
    )
    assert error is None, error
    before = counts(env)
    got = client.call("POST", base_path(live), "org1:ml_engineer", post(live, "x", "key-grant-1"))
    check(
        "a grant revoked after lock is 403 dataset_grant_changed",
        (got.status, got.json) == (403, {"detail": "dataset_grant_changed"})
        and counts(env) == before,
    )
    replay = client.call(
        "POST", base_path(live), "org1:ml_engineer", post(live, "x", "key-grant-0")
    )
    check(
        "the earlier experiment still replays (it was stored before the revocation)",
        replay.status == 200 and replay.json == ok.json,
    )


def section_leak(env: Any, client: Client, fx: Any) -> None:
    base = base_path(fx)
    seen: list[str] = []
    for over in (
        {"adapter__target_modules": [f"{CANARY} x"]},
        {"tokenizer__revision": f"{CANARY}"},
        {"budget__max_cost": CANARY},
        {CANARY: 1},
    ):
        got = client.call(
            "POST",
            base,
            "org1:ml_engineer",
            post(fx, f"exp {CANARY}", f"key-leak-{uuid.uuid4().hex}", **over),
        )
        seen.append(got.raw.decode())
        check(f"leak probe refused ({got.status})", got.status in (400, 409))
    check("no canary in any refusal body", not any(CANARY in body for body in seen))
    audit_text = env.scalar(
        "SELECT coalesce(string_agg(a::text, '|'), '') FROM audit_events a WHERE action='training_experiment.create'"
    )
    check("no canary in the audit trail", CANARY not in str(audit_text))


def main() -> None:
    migration_url = split_proof._required_env("SOVRA_DATABASE_URL")
    app_password = split_proof._required_env("SOVRA_APP_DB_PASSWORD")
    migrated = split_proof._alembic(["upgrade", "head"], migration_url)
    check(
        f"alembic upgrade head runs cleanly\n{migrated.stdout}\n{migrated.stderr}",
        migrated.returncode == 0,
    )
    if migrated.returncode != 0:
        _finish()
    heads = split_proof._alembic(["heads"], migration_url)
    check(f"exactly one Alembic head: {heads.stdout!r}", heads.stdout.count("(head)") == 1)

    env = split_proof.Env()
    env.owner = create_engine(migration_url)
    app_url = split_proof._app_role_url(migration_url, app_password)
    app_engine = create_engine(app_url)
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    env.factory = sessionmaker(bind=app_engine, expire_on_commit=False)
    split_proof.seed(env)
    fx = p2.Fixture(env, "routes")
    fx.budget()
    foreign = p2.Fixture(env, "routes-foreign", org=2)
    foreign.budget()

    with serve_real_app(app_url) as base:
        client = Client(base)
        with Session(env.owner) as session:
            for label, org, roles in (
                ("org1", env.org1, tuple(Role)),
                ("org2", env.org2, (Role.ML_ENGINEER, Role.ORGANIZATION_ADMIN)),
            ):
                for role in roles:
                    subject = client.login(label, role)
                    membership_store.grant_membership(
                        session,
                        organization_id=org,
                        issuer=ISSUER,
                        subject=subject,
                        role=role.value,
                    )
            session.commit()
        exp_id = section_create(env, client, fx)
        section_roles(env, client, fx, exp_id)
        section_bodies(env, client, fx)
        section_codes(env, client, fx)
        section_isolation(env, client, fx, foreign, exp_id)
        section_live_facts(env, client)
        section_leak(env, client, fx)
    check(
        "the pure schema module is the one the routes resolved with",
        schema.SCHEMA_VERSION == "training_config.v1",
    )
    _finish()


if __name__ == "__main__":
    main()
