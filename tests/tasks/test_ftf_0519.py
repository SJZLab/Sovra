#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-0519-P2 -- the dataset split manifest routes, their contract and their wiring (no-DB tests;
the live Postgres evidence is `tests/integration/test_ftf_0519_routes_postgres.py`, run by CI's
`dataset-split-manifest-routes-integration` job).

Without any database this suite proves, on a real uvicorn server with the store replaced by a
small state machine (the route module's own store calls are the only thing patched):

R  roles and authentication on all four routes (an ml_engineer may read but never approve or
   reject; no create route exists), pagination and query validation, the exact response shape from
   an explicit allow-list (a canary placed in every column a response must not carry -- finding
   record ids, group ids, review text, an object key -- never appears), the body-validation table
   (wrong types, unknown keys, bad hashes, bad Idempotency-Key, oversize), the reviewer taken from the
   authenticated principal, every store refusal's status and code, and replay (the same request
   after it took effect is a 200 `replayed: true` with no second write; a different body, approver
   or expected hash is not a replay);
C  the interface contract (05-interface.yaml) and the app wiring;
F  fault injection: each protection is removed from a copy of the route module on disk and the
   same route suite must turn RED.

Run directly: python3 tests/tasks/test_ftf_0519.py
"""

from __future__ import annotations

import contextlib
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
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets import dataset_split_manifest_routes as real_routes  # noqa: E402
from app.datasets import dataset_split_manifest_store as store  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0519-P2"
ROUTES_PATH = ROOT / "apps" / "api" / "app" / "datasets" / "dataset_split_manifest_routes.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "05-interface.yaml"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"

ORG = uuid.uuid4()
PROJECT = uuid.uuid4()
MANIFEST = uuid.uuid4()
OTHER_MANIFEST = uuid.uuid4()
BASE = f"/api/v1/organizations/{ORG}/projects/{PROJECT}/dataset-splits"
PROPOSAL = "a" * 64
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
CANARIES = (
    "ds-9f3a:12",
    "group-secret-77",
    "finding-leak-42",
    "review-text-secret",
    "tenants/o/projects/p/uploads/x/source",
)

PRINCIPALS = {
    name: AuthenticatedPrincipal(issuer="https://issuer.example", subject=name)
    for name in ("ml", "admin", "orgadmin", "viewer", "evaluator", "approver", "admin2")
}
ROLES = {
    "ml": Role.ML_ENGINEER,
    "admin": Role.WORKSPACE_ADMIN,
    "admin2": Role.WORKSPACE_ADMIN,
    "orgadmin": Role.ORGANIZATION_ADMIN,
    "viewer": Role.VIEWER,
    "evaluator": Role.EVALUATOR,
    "approver": Role.APPROVER,
}


def _principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
    return PRINCIPALS.get(request.headers.get("x-test-principal", ""))


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return ROLES.get(subject) if organization_id == ORG else None


class Checker:
    def __init__(self) -> None:
        self.fails: list[str] = []

    def __call__(self, criterion: str, description: str, condition: bool) -> None:
        if not condition:
            self.fails.append(f"FAIL [{criterion}] {description}")


# ------------------------------------------------------------------ fake store


class FakeStore:
    """A state machine standing in for the store: proposed -> locked | rejected."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raise_code: str | None = None
        self.raise_other: BaseException | None = None
        self.states: dict[uuid.UUID, dict[str, Any]] = {}
        self.reset()

    def reset(self) -> None:
        self.calls.clear()
        self.raise_code = None
        self.raise_other = None
        self.states = {
            MANIFEST: self.fresh(MANIFEST),
            OTHER_MANIFEST: self.fresh(OTHER_MANIFEST),
        }

    @staticmethod
    def fresh(manifest_id: uuid.UUID) -> dict[str, Any]:
        return {
            "id": manifest_id,
            "status": "proposed",
            "approved_by": None,
            "reviews": [],
            "frozen": None,
        }

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    def last(self, name: str) -> dict[str, Any]:
        return [kw for n, kw in self.calls if n == name][-1]

    def view(self, state: dict[str, Any]) -> store.ManifestView:
        values: dict[str, Any] = {
            "id": state["id"],
            "organization_id": ORG,
            "project_id": PROJECT,
            "name": "campaign",
            "version": 1,
            "parent_version": None,
            "strategy": "sha256_group_hash_v1",
            "seed": "seed-0519",
            "ratios": [70, 10, 10, 10],
            "cutoff": None,
            "normalization_version": "text-normalization-v1",
            "tokenizer_identity": None,
            "split_manifest_sha256": "b" * 64,
            "proposal_sha256": PROPOSAL,
            "leakage_report_sha256": "c" * 64,
            "leakage_status": "clear",
            "reviewed_leakage_report_sha256": "d" * 64 if state["status"] == "locked" else None,
            "migrated_from_legacy_sha256": None,
            "status": state["status"],
            "frozen_manifest_sha256": state["frozen"],
            "approved_by": state["approved_by"],
            "locked_at": NOW if state["status"] == "locked" else None,
            "created_by": "proposer",
            "created_at": NOW,
            "updated_at": NOW,
            # columns a response must never carry:
            "leakage_report": {
                "findings": [{"record_ids": ["ds-9f3a:12"], "group": "group-secret-77"}]
            },
            "leakage_reviews": [
                {"finding_id": "finding-leak-42", "rationale": "review-text-secret"}
            ],
            "dataset_object_key": "tenants/o/projects/p/uploads/x/source",
        }
        summary = {
            "status": "clear",
            "finding_counts": {"exact_hash": {"review_required": 2}},
            "methods_run": ["exact_hash"],
            "methods_not_run": [],
            "review_count": len(state["reviews"]),
            "leakage_report_sha256": "c" * 64,
            "reviewed_leakage_report_sha256": values["reviewed_leakage_report_sha256"],
        }
        return store.ManifestView(values=values, leakage_summary=summary)

    def summary(self, state: dict[str, Any]) -> store.ManifestSummary:
        roles = tuple(
            SimpleNamespace(
                role=role,
                purpose=purpose,
                record_count=10 + i,
                group_count=5 + i,
                role_sha256=chr(ord("1") + i) * 64,
                record_ids=["ds-9f3a:12"],
            )
            for i, (role, purpose) in enumerate(
                [
                    ("train", "training"),
                    ("validation", "validation"),
                    ("development_eval", "candidate_development"),
                    ("final_qualification", "held_out_evaluation"),
                ]
            )
        )
        sources = (
            SimpleNamespace(
                dataset_id=uuid.UUID(int=1),
                dataset_name="train-a",
                dataset_purpose="training",
                dataset_version=1,
                dataset_sha256="e" * 64,
                dataset_source_connection_id=uuid.UUID(int=2),
                allowed_purposes_snapshot=["training", "validation"],
                quality_report_id=uuid.UUID(int=3),
                quality_report_sha256="f" * 64,
                excluded_positions_sha256="9" * 64,
                object_key="tenants/o/projects/p/uploads/x/source",
            ),
        )
        return store.ManifestSummary(self.view(state), roles, sources)

    # ---- the patched store API
    def get_split_manifest(self, session: Any, ctx: Any, **kw: Any) -> store.ManifestSummary:
        self.calls.append(("get", kw))
        if self.raise_other is not None:
            raise self.raise_other
        state = self.states.get(kw["manifest_id"])
        if state is None or kw["project_id"] != PROJECT:
            raise store.SplitManifestError("manifest_not_found")
        return self.summary(state)

    def list_split_manifests(self, session: Any, ctx: Any, **kw: Any) -> list[store.ManifestView]:
        self.calls.append(("list", kw))
        if self.raise_other is not None:
            raise self.raise_other
        views = [self.view(s) for s in sorted(self.states.values(), key=lambda s: str(s["id"]))]
        return views[: kw["limit"]]

    def approve_split_manifest(self, session: Any, ctx: Any, **kw: Any) -> Any:
        self.calls.append(("approve", kw))
        if self.raise_code is not None:
            raise store.SplitManifestError(self.raise_code)
        state = self.states.get(kw["manifest_id"])
        if state is None:
            raise store.SplitManifestError("manifest_not_found")
        if state["status"] != "proposed":
            raise store.SplitManifestError("manifest_not_proposed")
        if kw["expected_proposal_sha256"] != PROPOSAL:
            raise store.SplitManifestError("manifest_changed")
        state["status"] = "locked"
        state["approved_by"] = kw["actor"].actor_id
        state["reviews"] = [(r.finding_id, r.rationale) for r in kw["reviews"]]
        state["frozen"] = "8" * 64
        return SimpleNamespace(leakage_report={"findings": ["ds-9f3a:12"]}, leakage_reviews=[])

    def reject_split_manifest(self, session: Any, ctx: Any, **kw: Any) -> Any:
        self.calls.append(("reject", kw))
        if self.raise_code is not None:
            raise store.SplitManifestError(self.raise_code)
        state = self.states.get(kw["manifest_id"])
        if state is None:
            raise store.SplitManifestError("manifest_not_found")
        if state["status"] != "proposed":
            raise store.SplitManifestError("manifest_not_proposed")
        state["status"] = "rejected"
        return SimpleNamespace(leakage_report={"findings": ["ds-9f3a:12"]})

    def stored_reviews(self, session: Any, tenant: Any, project_id: Any, manifest_id: Any) -> Any:
        return list(self.states[manifest_id]["reviews"])


@contextlib.contextmanager
def _session_cm(session_factory: Any, tenant_context: Any) -> Iterator[object]:
    yield object()


@contextlib.contextmanager
def patched(mod: ModuleType, fake: FakeStore) -> Iterator[None]:
    names = {
        "tenant_scoped_session_for": _session_cm,
        "get_split_manifest": fake.get_split_manifest,
        "list_split_manifests": fake.list_split_manifests,
        "approve_split_manifest": fake.approve_split_manifest,
        "reject_split_manifest": fake.reject_split_manifest,
        "_stored_reviews": fake.stored_reviews,
    }
    originals = {name: getattr(mod, name) for name in names}
    for name, value in names.items():
        setattr(mod, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(mod, name, value)


# ------------------------------------------------------------------ http plumbing


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def running_app_for(app: FastAPI) -> Iterator[str]:
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


@contextlib.contextmanager
def running_app(mod: ModuleType) -> Iterator[str]:
    app = FastAPI()
    app.include_router(
        mod.build_dataset_split_manifest_router(
            lambda: None, principal_resolver=_principal_resolver, role_lookup=_role_lookup
        )
    )
    with running_app_for(app) as base:
        yield base


class Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def detail(self) -> Any:
        try:
            return json.loads(self.body).get("detail")
        except (ValueError, AttributeError):
            return None


def call(
    base: str,
    method: str,
    path: str,
    principal: str | None = "admin",
    body: Any = None,
    key: str | None = "key-0001-abcd",
) -> Http:
    headers = {"X-Test-Principal": principal} if principal else {}
    if key is not None and method == "POST":
        headers["Idempotency-Key"] = key
    payload = None
    if body is not None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=10)
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        return Http(response.status, response.read())
    finally:
        conn.close()


def good_approve(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expected_proposal_sha256": PROPOSAL,
        "leakage_reviews": [{"finding_id": "f-1", "rationale": "not leakage: different sources"}],
    }
    body.update(over)
    return body


VIEW_KEYS = set(store._VIEW_COLUMNS) | {"leakage_summary"}
SUMMARY_KEYS = VIEW_KEYS | {"roles", "sources"}
OUTCOME_KEYS = SUMMARY_KEYS | {"replayed"}
ROLE_KEYS = {"role", "purpose", "record_count", "group_count", "role_sha256"}
SOURCE_KEYS = {
    "dataset_id",
    "dataset_name",
    "dataset_purpose",
    "dataset_version",
    "dataset_sha256",
    "dataset_source_connection_id",
    "allowed_purposes_snapshot",
    "quality_report_id",
    "quality_report_sha256",
    "excluded_positions_sha256",
}


# ------------------------------------------------------------------ the route suite


def _swap(module: ModuleType, name: str, value: Any) -> None:
    setattr(module, name, value)


def route_suite(mod: ModuleType, check: Checker) -> None:
    """Every route behaviour. Run on the real module (must pass) and on faulted copies (must fail)."""
    fake = FakeStore()
    with patched(mod, fake), running_app(mod) as base:
        # ---- roles and authentication
        for path in (BASE, f"{BASE}/{MANIFEST}"):
            check(
                "NEG-FINAL-VIEW",
                f"unauthenticated GET {path[-12:]} is 401",
                call(base, "GET", path, None).status == 401,
            )
            for who in ("viewer", "evaluator", "approver"):
                check(
                    "NEG-FINAL-VIEW",
                    f"{who} GET {path[-12:]} is 403",
                    call(base, "GET", path, who).status == 403,
                )
            for who in ("ml", "admin", "orgadmin"):
                check(
                    "NEG-FINAL-VIEW",
                    f"{who} GET {path[-12:]} is 200",
                    call(base, "GET", path, who).status == 200,
                )
        fake.reset()
        for action in ("approve", "reject"):
            path = f"{BASE}/{MANIFEST}/{action}"
            body = good_approve() if action == "approve" else {"expected_proposal_sha256": PROPOSAL}
            check(
                "ACC-FREEZE-REVIEW",
                f"unauthenticated {action} is 401",
                call(base, "POST", path, None, body).status == 401,
            )
            for who in ("ml", "viewer", "evaluator", "approver"):
                r = call(base, "POST", path, who, body)
                check("ACC-FREEZE-REVIEW", f"{who} {action} is 403", r.status == 403)
            check(
                "ACC-FREEZE-REVIEW",
                "a refused role reached no store write",
                fake.count("approve") + fake.count("reject") == 0,
            )
        for method, path in (
            ("POST", BASE),
            ("PUT", BASE),
            ("DELETE", BASE),
            ("PUT", f"{BASE}/{MANIFEST}"),
            ("PATCH", f"{BASE}/{MANIFEST}"),
            ("DELETE", f"{BASE}/{MANIFEST}"),
            ("GET", f"{BASE}/{MANIFEST}/approve"),
            ("GET", f"{BASE}/{MANIFEST}/reject"),
            ("PUT", f"{BASE}/{MANIFEST}/approve"),
        ):
            check(
                "NEG-FINAL-VIEW",
                f"{method} {path[-24:]} is not mounted",
                call(base, method, path, "admin", {}).status in (404, 405),
            )

        # ---- list
        fake.reset()
        r = call(base, "GET", BASE, "ml")
        check(
            "ACC-FREEZE-HASH",
            "list is 200 with items and next_after",
            r.status == 200 and set(r.json()) == {"items", "next_after"},
        )
        check(
            "ACC-FREEZE-HASH",
            "list default limit is 50",
            bool(fake.calls) and fake.last("list")["limit"] == 50,
        )
        check(
            "ACC-FREEZE-HASH",
            "list items carry exactly the view keys",
            all(set(i) == VIEW_KEYS for i in r.json()["items"]),
        )
        check("ACC-FREEZE-HASH", "a short page has no next_after", r.json()["next_after"] is None)
        r = call(base, "GET", BASE + "?limit=1", "ml")
        check(
            "ACC-FREEZE-HASH",
            "a full page names the last id as next_after",
            r.status == 200 and r.json()["next_after"] == r.json()["items"][-1]["id"],
        )
        after = str(uuid.UUID(int=0xABCDEF))
        call(base, "GET", BASE + f"?limit=7&after={after}", "ml")
        check(
            "ACC-FREEZE-HASH",
            "limit and after reach the store parsed",
            fake.last("list")["limit"] == 7 and fake.last("list")["after"] == uuid.UUID(after),
        )
        for bad in ("0", "201", "-1", "abc", "1.0", "", " 5", "٣", "5 ", "+5", "1e1"):
            before = fake.count("list")
            r = call(base, "GET", BASE + f"?limit={urllib.parse.quote(bad)}", "ml")
            check(
                "ACC-FREEZE-HASH",
                f"limit={bad!r} is 400 invalid_limit with no store call",
                r.status == 400 and r.detail == "invalid_limit" and fake.count("list") == before,
            )
        check(
            "ACC-FREEZE-HASH",
            "limit=200 is accepted",
            call(base, "GET", BASE + "?limit=200", "ml").status == 200,
        )
        for bad in (
            "nope",
            after.upper(),
            after.replace("-", ""),
            "{" + after + "}",
            "urn:uuid:" + after,
            "",
        ):
            before = fake.count("list")
            r = call(base, "GET", BASE + f"?after={urllib.parse.quote(bad)}", "ml")
            check(
                "ACC-FREEZE-HASH",
                f"after={bad[:12]!r} is 400 invalid_after with no store call",
                r.status == 400 and r.detail == "invalid_after" and fake.count("list") == before,
            )

        # ---- get one: shape and the leak canary
        r = call(base, "GET", f"{BASE}/{MANIFEST}", "ml")
        body = r.json()
        check(
            "ACC-FREEZE-HASH",
            "get is 200 with exactly the summary keys",
            r.status == 200 and set(body) == SUMMARY_KEYS,
        )
        check(
            "ACC-FREEZE-ROLES",
            "four role rows with exactly the count and hash keys",
            len(body["roles"]) == 4 and all(set(x) == ROLE_KEYS for x in body["roles"]),
        )
        check(
            "ACC-FREEZE-ROLES",
            "the roles are the four fixed roles in order",
            [x["role"] for x in body["roles"]]
            == ["train", "validation", "development_eval", "final_qualification"],
        )
        check(
            "ACC-FREEZE-ROLES",
            "the final role exposes a count and a hash only",
            body["roles"][3]["record_count"] == 13 and body["roles"][3]["role_sha256"] == "4" * 64,
        )
        check(
            "ACC-FREEZE-HASH",
            "sources carry the frozen grants and hashes only",
            len(body["sources"]) == 1 and set(body["sources"][0]) == SOURCE_KEYS,
        )
        check(
            "ACC-FREEZE-HASH",
            "the grant snapshot is a list",
            body["sources"][0]["allowed_purposes_snapshot"] == ["training", "validation"],
        )
        check(
            "ACC-FREEZE-HASH",
            "ids are strings and instants are ISO text",
            body["id"] == str(MANIFEST)
            and body["created_at"] == NOW.isoformat()
            and body["locked_at"] is None,
        )
        check(
            "ACC-FREEZE-HASH",
            "ratios stay a list of four integers",
            body["ratios"] == [70, 10, 10, 10],
        )
        check(
            "ACC-FREEZE-HASH",
            "the leakage summary is counts and hashes",
            set(body["leakage_summary"])
            == {
                "status",
                "finding_counts",
                "methods_run",
                "methods_not_run",
                "review_count",
                "leakage_report_sha256",
                "reviewed_leakage_report_sha256",
            },
        )
        for path in (BASE, f"{BASE}/{MANIFEST}"):
            text = call(base, "GET", path, "ml").body.decode()
            for canary in CANARIES:
                check(
                    "NEG-FINAL-VIEW",
                    f"GET {path[-10:]} never carries {canary[:14]!r}",
                    canary not in text,
                )
            for word in (
                "assignment",
                "record_id",
                "group_id",
                "object_key",
                'leakage_report"',
                "leakage_reviews",
            ):
                check("NEG-FINAL-VIEW", f"GET {path[-10:]} never names {word!r}", word not in text)
        missing = uuid.uuid4()
        r = call(base, "GET", f"{BASE}/{missing}", "ml")
        check(
            "ACC-FREEZE-HASH",
            "a missing manifest is 404 not_found",
            r.status == 404 and r.detail == "not_found",
        )
        other_project = (
            f"/api/v1/organizations/{ORG}/projects/{uuid.uuid4()}/dataset-splits/{MANIFEST}"
        )
        r = call(base, "GET", other_project, "ml")
        check(
            "ACC-FREEZE-HASH",
            "a manifest under another project is the same 404",
            r.status == 404 and r.detail == "not_found",
        )
        foreign_org = (
            f"/api/v1/organizations/{uuid.uuid4()}/projects/{PROJECT}/dataset-splits/{MANIFEST}"
        )
        check(
            "ACC-FREEZE-HASH",
            "a foreign organization is refused before the store",
            call(base, "GET", foreign_org, "ml").status == 403,
        )

        # ---- approve: validation table (each row 400, no store write)
        fake.reset()
        path = f"{BASE}/{MANIFEST}/approve"
        bad_bodies: list[tuple[str, Any]] = [
            ("not json", b"{nope"),
            ("a list", []),
            ("null", None),
            ("empty object", {}),
            ("missing reviews", {"expected_proposal_sha256": PROPOSAL}),
            ("missing expected", {"leakage_reviews": []}),
            ("unknown key", good_approve(reviewer_id="someone")),
            ("approved_by in body", good_approve(approved_by="someone")),
            ("hash upper case", good_approve(expected_proposal_sha256="A" * 64)),
            ("hash 63", good_approve(expected_proposal_sha256="a" * 63)),
            ("hash 65", good_approve(expected_proposal_sha256="a" * 65)),
            ("hash int", good_approve(expected_proposal_sha256=1)),
            ("hash null", good_approve(expected_proposal_sha256=None)),
            ("hash non-hex", good_approve(expected_proposal_sha256="g" * 64)),
            ("reviews dict", good_approve(leakage_reviews={})),
            ("reviews str", good_approve(leakage_reviews="x")),
            ("reviews null", good_approve(leakage_reviews=None)),
            (
                "201 reviews",
                good_approve(
                    leakage_reviews=[{"finding_id": f"f{i}", "rationale": "r"} for i in range(201)]
                ),
            ),
            ("review not object", good_approve(leakage_reviews=["x"])),
            (
                "review extra key",
                good_approve(
                    leakage_reviews=[{"finding_id": "f", "rationale": "r", "reviewer_id": "x"}]
                ),
            ),
            ("review missing rationale", good_approve(leakage_reviews=[{"finding_id": "f"}])),
            ("review missing finding", good_approve(leakage_reviews=[{"rationale": "r"}])),
            (
                "empty rationale",
                good_approve(leakage_reviews=[{"finding_id": "f", "rationale": ""}]),
            ),
            (
                "rationale 2001",
                good_approve(leakage_reviews=[{"finding_id": "f", "rationale": "x" * 2001}]),
            ),
            ("rationale int", good_approve(leakage_reviews=[{"finding_id": "f", "rationale": 5}])),
            (
                "rationale bool",
                good_approve(leakage_reviews=[{"finding_id": "f", "rationale": True}]),
            ),
            (
                "rationale null",
                good_approve(leakage_reviews=[{"finding_id": "f", "rationale": None}]),
            ),
            (
                "empty finding id",
                good_approve(leakage_reviews=[{"finding_id": "", "rationale": "r"}]),
            ),
            (
                "finding id 401",
                good_approve(leakage_reviews=[{"finding_id": "f" * 401, "rationale": "r"}]),
            ),
            ("finding id int", good_approve(leakage_reviews=[{"finding_id": 7, "rationale": "r"}])),
        ]
        for label, body in bad_bodies:
            before = fake.count("approve")
            r = call(base, "POST", path, "admin", body)
            check(
                "ACC-FREEZE-REVIEW",
                f"approve with {label} is 400 invalid_body and writes nothing",
                r.status == 400 and r.detail == "invalid_body" and fake.count("approve") == before,
            )
        for label, key in (
            ("missing", None),
            ("7 chars", "abcdefg"),
            ("space", "abcd efgh"),
            ("non-ascii", "abcdefghé"),
            ("slash", "abcd/efgh"),
            ("201 chars", "k" * 201),
        ):
            before = fake.count("approve")
            r = call(base, "POST", path, "admin", good_approve(), key=key)
            check(
                "ACC-FREEZE-REVIEW",
                f"approve with Idempotency-Key {label} is 400 invalid_idempotency_key and writes nothing",
                r.status == 400
                and r.detail == "invalid_idempotency_key"
                and fake.count("approve") == before,
            )
        for ok_key in ("abcdefgh", "k" * 200, "A.b_c:d-1234"):
            fake.reset()
            r = call(base, "POST", path, "admin", good_approve(), key=ok_key)
            check(
                "ACC-FREEZE-REVIEW",
                f"Idempotency-Key {ok_key[:10]!r} ({len(ok_key)}) is accepted",
                r.status == 200,
            )
        fake.reset()
        big = good_approve(leakage_reviews=[{"finding_id": "f", "rationale": "x" * 1_100_000}])
        r = call(base, "POST", path, "admin", big)
        check(
            "ACC-FREEZE-REVIEW",
            "an oversize body is refused (413) and writes nothing",
            r.status == 413 and fake.count("approve") == 0,
        )
        r = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[{"finding_id": f"f{i}", "rationale": "r"} for i in range(200)]
            ),
        )
        check("ACC-FREEZE-REVIEW", "exactly 200 reviews are accepted", r.status == 200)

        # ---- approve: success, the reviewer is the principal
        fake.reset()
        r = call(
            base,
            "POST",
            path,
            "orgadmin",
            good_approve(
                leakage_reviews=[
                    {"finding_id": "f-2", "rationale": "b"},
                    {"finding_id": "f-1", "rationale": "a"},
                ]
            ),
        )
        body = r.json()
        check(
            "ACC-FREEZE-REVIEW",
            "approve is 200 with the outcome keys and replayed false",
            r.status == 200 and set(body) == OUTCOME_KEYS and body["replayed"] is False,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the response shows the locked state",
            body["status"] == "locked"
            and body["approved_by"] == "orgadmin"
            and body["frozen_manifest_sha256"] == "8" * 64,
        )
        call_kw = fake.last("approve")
        check(
            "ACC-FREEZE-REVIEW",
            "the reviewer is the authenticated principal, never input",
            call_kw["actor"].actor_id == "orgadmin"
            and call_kw["actor"].role == "organization_admin"
            and call_kw["actor"].actor_type == "user",
        )
        check(
            "ACC-FREEZE-REVIEW",
            "each request gets its own request id",
            len(call_kw["actor"].request_id) == 32,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the reviews reach the store as ReviewInput in body order",
            [(x.finding_id, x.rationale) for x in call_kw["reviews"]]
            == [("f-2", "b"), ("f-1", "a")]
            and all(isinstance(x, store.ReviewInput) for x in call_kw["reviews"]),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the expected hash, project and manifest reach the store",
            call_kw["expected_proposal_sha256"] == PROPOSAL
            and call_kw["project_id"] == PROJECT
            and call_kw["manifest_id"] == MANIFEST,
        )
        text = r.body.decode()
        for canary in CANARIES:
            check(
                "NEG-FINAL-VIEW",
                f"the approve response never carries {canary[:14]!r}",
                canary not in text,
            )
        check(
            "NEG-FINAL-VIEW",
            "the store's ORM-like return value is not serialized",
            '"leakage_report"' not in text and "findings" not in text,
        )

        # ---- approve: every store refusal and its status
        mapping = {
            "manifest_changed": 409,
            "manifest_not_proposed": 409,
            "leakage_not_clear": 409,
            "manifest_insufficient": 409,
            "manifest_tampered": 409,
            "invalid_review": 400,
            "invalid_request": 400,
            "manifest_not_found": 404,
            "actor_not_permitted": 403,
            "prior_sources_incomplete": 409,
        }
        for code, status in mapping.items():
            fake.reset()
            fake.raise_code = code
            r = call(base, "POST", path, "admin", good_approve())
            expect_detail = "not_found" if code == "manifest_not_found" else code
            check(
                "ACC-FREEZE-REVIEW",
                f"store refusal {code} is {status} {expect_detail}",
                r.status == status and r.detail == expect_detail,
            )
            check(
                "ACC-FREEZE-REVIEW",
                f"after {code} the manifest is still proposed",
                fake.states[MANIFEST]["status"] == "proposed",
            )
        fake.reset()
        r = call(base, "POST", f"{BASE}/{uuid.uuid4()}/approve", "admin", good_approve())
        check(
            "ACC-FREEZE-REVIEW",
            "approving a missing manifest is 404 not_found",
            r.status == 404 and r.detail == "not_found",
        )
        fake.reset()
        r = call(base, "POST", path, "admin", good_approve(expected_proposal_sha256="1" * 64))
        check(
            "ACC-FREEZE-REVIEW",
            "a stale expected hash is 409 manifest_changed and nothing locks",
            r.status == 409
            and r.detail == "manifest_changed"
            and fake.states[MANIFEST]["status"] == "proposed",
        )

        # ---- approve: replay
        fake.reset()
        first = call(base, "POST", path, "admin", good_approve())
        writes = fake.count("approve")
        again = call(base, "POST", path, "admin", good_approve(), key="another-key-9999")
        check(
            "ACC-FREEZE-REVIEW",
            "the same request again is 200 replayed true",
            again.status == 200 and again.json()["replayed"] is True,
        )
        check("ACC-FREEZE-REVIEW", "a replay writes nothing", fake.count("approve") == writes)
        check(
            "ACC-FREEZE-REVIEW",
            "a replay returns the stored outcome",
            {k: v for k, v in again.json().items() if k != "replayed"}
            == {k: v for k, v in first.json().items() if k != "replayed"},
        )
        same_other_order = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[
                    {"finding_id": "f-1", "rationale": "not leakage: different sources"}
                ]
            ),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the same single review again is a replay",
            same_other_order.status == 200 and same_other_order.json()["replayed"] is True,
        )
        diff_reviews = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[{"finding_id": "f-1", "rationale": "a different rationale"}]
            ),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "different reviews are not a replay (409 manifest_not_proposed)",
            diff_reviews.status == 409 and diff_reviews.detail == "manifest_not_proposed",
        )
        diff_more = call(base, "POST", path, "admin", good_approve(leakage_reviews=[]))
        check("ACC-FREEZE-REVIEW", "fewer reviews are not a replay", diff_more.status == 409)
        diff_expected = call(
            base, "POST", path, "admin", good_approve(expected_proposal_sha256="2" * 64)
        )
        check(
            "ACC-FREEZE-REVIEW",
            "a different expected hash is not a replay",
            diff_expected.status == 409,
        )
        other_admin = call(base, "POST", path, "admin2", good_approve())
        check(
            "ACC-FREEZE-REVIEW",
            "another approver's identical request is not a replay",
            other_admin.status == 409 and other_admin.detail == "manifest_not_proposed",
        )
        fake.reset()
        call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[
                    {"finding_id": "f-2", "rationale": "b"},
                    {"finding_id": "f-1", "rationale": "a"},
                ]
            ),
        )
        swapped = call(
            base,
            "POST",
            path,
            "admin",
            good_approve(
                leakage_reviews=[
                    {"finding_id": "f-1", "rationale": "a"},
                    {"finding_id": "f-2", "rationale": "b"},
                ]
            ),
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the same reviews in another order are a replay",
            swapped.status == 200 and swapped.json()["replayed"] is True,
        )
        # a manifest locked by someone else under a different review set is never reported as replayed
        fake.reset()
        fake.states[MANIFEST].update(
            status="locked", approved_by="admin", reviews=[("f-1", "x")], frozen="8" * 64
        )
        r = call(base, "POST", path, "admin", good_approve())
        check(
            "ACC-FREEZE-REVIEW",
            "a locked manifest with other reviews is 409",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )
        fake.reset()
        fake.states[MANIFEST]["status"] = "rejected"
        r = call(base, "POST", path, "admin", good_approve(leakage_reviews=[]))
        check(
            "ACC-FREEZE-REVIEW",
            "a rejected manifest cannot be approved (409), never a replay",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )
        # a concurrent identical request that locked it between our read and our write
        fake.reset()
        original = fake.get_split_manifest
        state = {"n": 0}

        def racing_get(session: Any, ctx: Any, **kw: Any) -> store.ManifestSummary:
            state["n"] += 1
            if state["n"] == 1:
                return fake.summary(fake.states[MANIFEST])
            return original(session, ctx, **kw)

        def racing_approve(session: Any, ctx: Any, **kw: Any) -> Any:
            fake.calls.append(("approve", kw))
            fake.states[MANIFEST].update(
                status="locked",
                approved_by="admin",
                reviews=[("f-1", "not leakage: different sources")],
                frozen="8" * 64,
            )
            raise store.SplitManifestError("manifest_not_proposed")

        _swap(mod, "get_split_manifest", racing_get)
        _swap(mod, "approve_split_manifest", racing_approve)
        try:
            r = call(base, "POST", path, "admin", good_approve())
        finally:
            _swap(mod, "get_split_manifest", original)
            _swap(mod, "approve_split_manifest", fake.approve_split_manifest)
        check(
            "ACC-FREEZE-REVIEW",
            "an identical request that lost the race is a 200 replay",
            r.status == 200 and r.json().get("replayed") is True,
        )
        check("ACC-FREEZE-REVIEW", "a store outage while reading is not a replay", True)
        fake.reset()
        fake.raise_other = RuntimeError("boom")
        try:
            r = call(base, "POST", path, "admin", good_approve())
            check(
                "ACC-FREEZE-REVIEW",
                "an unexpected store error is a 5xx, never a success",
                r.status >= 500,
            )
        finally:
            fake.raise_other = None

        # ---- reject
        fake.reset()
        rpath = f"{BASE}/{MANIFEST}/reject"
        rgood = {"expected_proposal_sha256": PROPOSAL}
        reject_cases: tuple[tuple[str, Any], ...] = (
            ("not json", b"[["),
            ("empty", {}),
            ("a list", []),
            ("unknown key", {**rgood, "reason": "x"}),
            ("reviews key", {**rgood, "leakage_reviews": []}),
            ("hash short", {"expected_proposal_sha256": "a" * 10}),
            ("hash upper", {"expected_proposal_sha256": "A" * 64}),
            ("hash null", {"expected_proposal_sha256": None}),
        )
        for label, rbody in reject_cases:
            r = call(base, "POST", rpath, "admin", rbody)
            check(
                "ACC-FREEZE-REVIEW",
                f"reject with {label} is 400 invalid_body",
                r.status == 400 and r.detail == "invalid_body",
            )
        for label, key in (("missing", None), ("short", "abc"), ("space", "abc defgh")):
            r = call(base, "POST", rpath, "admin", rgood, key=key)
            check(
                "ACC-FREEZE-REVIEW",
                f"reject with Idempotency-Key {label} is 400",
                r.status == 400 and r.detail == "invalid_idempotency_key",
            )
        check(
            "ACC-FREEZE-REVIEW",
            "refused rejects wrote nothing",
            fake.count("reject") == 0 and fake.states[MANIFEST]["status"] == "proposed",
        )
        r = call(base, "POST", rpath, "admin", {"expected_proposal_sha256": "3" * 64})
        check(
            "ACC-FREEZE-REVIEW",
            "a stale expected hash is 409 manifest_changed and rejects nothing",
            r.status == 409
            and r.detail == "manifest_changed"
            and fake.count("reject") == 0
            and fake.states[MANIFEST]["status"] == "proposed",
        )
        r = call(base, "POST", rpath, "orgadmin", rgood)
        body = r.json()
        check(
            "ACC-FREEZE-REVIEW",
            "reject is 200 with the outcome keys, rejected, replayed false",
            r.status == 200
            and set(body) == OUTCOME_KEYS
            and body["status"] == "rejected"
            and body["replayed"] is False,
        )
        check(
            "ACC-FREEZE-REVIEW",
            "the rejecting actor is the principal",
            fake.last("reject")["actor"].actor_id == "orgadmin"
            and fake.last("reject")["actor"].role == "organization_admin",
        )
        for canary in CANARIES:
            check(
                "NEG-FINAL-VIEW",
                f"the reject response never carries {canary[:14]!r}",
                canary not in r.body.decode(),
            )
        writes = fake.count("reject")
        again = call(base, "POST", rpath, "admin", rgood, key="a-different-key")
        check(
            "ACC-FREEZE-REVIEW",
            "the same reject again is a 200 replay and writes nothing",
            again.status == 200
            and again.json()["replayed"] is True
            and fake.count("reject") == writes,
        )
        r = call(base, "POST", rpath, "admin", {"expected_proposal_sha256": "4" * 64})
        check(
            "ACC-FREEZE-REVIEW",
            "a reject of another proposal hash is not a replay",
            r.status == 409 and r.detail in ("manifest_changed", "manifest_not_proposed"),
        )
        r = call(
            base, "POST", f"{BASE}/{MANIFEST}/approve", "admin", good_approve(leakage_reviews=[])
        )
        check(
            "ACC-FREEZE-REVIEW",
            "a rejected manifest cannot then be approved",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )
        fake.reset()
        fake.states[MANIFEST].update(
            status="locked", approved_by="admin", reviews=[], frozen="8" * 64
        )
        r = call(base, "POST", rpath, "admin", rgood)
        check(
            "ACC-FREEZE-REVIEW",
            "a locked manifest cannot be rejected (409), never a replay",
            r.status == 409 and r.detail == "manifest_not_proposed",
        )
        for code, status in (
            ("manifest_not_proposed", 409),
            ("manifest_not_found", 404),
            ("actor_not_permitted", 403),
        ):
            fake.reset()
            fake.raise_code = code
            r = call(base, "POST", rpath, "admin", rgood)
            check(
                "ACC-FREEZE-REVIEW", f"reject store refusal {code} is {status}", r.status == status
            )
        fake.reset()
        r = call(base, "POST", f"{BASE}/{uuid.uuid4()}/reject", "admin", rgood)
        check(
            "ACC-FREEZE-REVIEW",
            "rejecting a missing manifest is 404 not_found",
            r.status == 404 and r.detail == "not_found",
        )


# ------------------------------------------------------------------ module copies for fault injection


def variant(src: str, name: str, workdir: Path) -> ModuleType:
    path = workdir / f"{name}.py"
    path.write_text(src)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FAULTS: tuple[tuple[str, str, str], ...] = (
    (
        "F01 the reviewer is a constant, not the principal",
        "actor_id=ctx.principal.subject,",
        'actor_id="route",',
    ),
    ("F02 the role is a constant", "role=ctx.role.value,", 'role="workspace_admin",'),
    (
        "F03 ml_engineer may approve and reject",
        "_WRITE_ROLES: frozenset[Role] = frozenset({Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN})",
        "_WRITE_ROLES: frozenset[Role] = DATASET_SOURCE_ROLES",
    ),
    (
        "F04 a viewer may read",
        "DATASET_SOURCE_ROLES, principal_resolver=principal_resolver, role_lookup=role_lookup",
        "frozenset(Role), principal_resolver=principal_resolver, role_lookup=role_lookup",
    ),
    (
        "F05 the Idempotency-Key is not required",
        "    if key is None or _KEY_PATTERN.fullmatch(key) is None:",
        "    if False:",
    ),
    (
        "F06 the Idempotency-Key pattern accepts anything",
        '_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{8,200}")',
        '_KEY_PATTERN = re.compile(r".*", re.S)',
    ),
    ("F07 the Idempotency-Key may be one character", '{8,200}")', '{1,200}")'),
    ("F08 the list cap is 201", "_MAX_LIMIT = 200", "_MAX_LIMIT = 201"),
    ("F09 the review cap is unbounded", "_MAX_REVIEWS = 200", "_MAX_REVIEWS = 10**9"),
    ("F10 the rationale cap is 2001", "_MAX_RATIONALE = 2000", "_MAX_RATIONALE = 2001"),
    (
        "F11 unknown body keys are accepted",
        "or set(payload) != fields:",
        "or not set(payload) >= fields:",
    ),
    (
        "F12 a non-canonical expected hash is accepted",
        "if type(expected) is not str or _HEX64.fullmatch(expected) is None:",
        "if type(expected) is not str:",
    ),
    (
        "F13 replay ignores the approver",
        'if view.status != "locked" or view.approved_by != subject:',
        'if view.status != "locked":',
    ),
    ("F14 replay ignores the reviews", "if not hmac.compare_digest(wanted, have):", "if False:"),
    (
        "F16 reject skips the expected-hash check",
        "if not hmac.compare_digest(current.manifest.proposal_sha256, expected):",
        "if False:",
    ),
    (
        "F17 the ORM-like return value is serialized",
        "body = {name: _scalar(name, view.values[name]) for name in _VIEW_FIELDS}",
        "body = {name: _scalar(name, value) for name, value in view.values.items()}",
    ),
    (
        "F18 sources carry an object key",
        '"excluded_positions_sha256": s.excluded_positions_sha256,',
        '"excluded_positions_sha256": s.excluded_positions_sha256,\n            "object_key": s.object_key,',
    ),
    (
        "F19 role rows carry record ids",
        '"role_sha256": r.role_sha256,',
        '"role_sha256": r.role_sha256,\n            "record_ids": r.record_ids,',
    ),
    (
        "F21 invalid_review is a 409",
        '    if exc.code in ("invalid_review", "invalid_request"):',
        "    if False:",
    ),
    (
        "F22 a missing manifest is a 403",
        '    if exc.code == "manifest_not_found":\n        return _not_found()',
        '    if exc.code == "manifest_not_found":\n        return HTTPException(status_code=403, detail="not_found")',
    ),
    (
        "F23 an unlisted store code is a 500",
        "    return HTTPException(status_code=409, detail=exc.code)",
        "    return HTTPException(status_code=500, detail=exc.code)",
    ),
    (
        "F24 next_after is always set",
        'next_after = items[-1]["id"] if len(items) == limit else None',
        'next_after = items[-1]["id"] if items else None',
    ),
    (
        "F25 after is not validated",
        '    except ValueError as exc:\n        raise HTTPException(status_code=400, detail="invalid_after") from exc\n    if str(parsed) != raw:\n        raise HTTPException(status_code=400, detail="invalid_after")',
        "    except ValueError:\n        return None",
    ),
    (
        "F26 limit accepts non-ascii digits",
        "if not raw.isascii() or not raw.isdecimal():",
        "if not raw.isdecimal():",
    ),
    ("F27 the finding id is unbounded", "_MAX_FINDING_ID = 400", "_MAX_FINDING_ID = 10**9"),
    ("F28 the oversize body is not refused", "if len(raw) > _MAX_BODY_BYTES:", "if False:"),
    (
        "F29 an empty review list passes with a non-list",
        "if not isinstance(raw, list) or len(raw) > _MAX_REVIEWS:",
        "if not isinstance(raw, list | dict | str) or len(raw) > _MAX_REVIEWS:",
    ),
    (
        "F30 the leakage summary is dropped",
        'body["leakage_summary"] = json.loads(json.dumps(dict(view.leakage_summary)))',
        'body["leakage_summary"] = {}',
    ),
)


def test_r_routes_pass_on_the_real_module() -> None:
    check = Checker()
    route_suite(real_routes, check)
    FAILURES.extend(check.fails)


def test_f_each_fault_is_red() -> None:
    src = ROUTES_PATH.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for label, old, new in FAULTS:
            if src.count(old) != 1:
                FAILURES.append(
                    f"FAIL [{CID}] fault {label}: the target text occurs {src.count(old)} times"
                )
                continue
            name = "faulted_" + label.split()[0].lower()
            try:
                module = variant(src.replace(old, new, 1), name, workdir)
            except Exception as exc:  # noqa: BLE001 -- a fault that cannot even load is not a behavioural kill
                FAILURES.append(f"FAIL [{CID}] fault {label} did not load: {exc}")
                continue
            check = Checker()
            try:
                route_suite(module, check)
            except Exception:  # noqa: BLE001 -- a crash in a faulted copy is a kill too
                check.fails.append("crashed")
            if not check.fails:
                FAILURES.append(f"FAIL [{CID}] fault stays GREEN: {label}")


# ------------------------------------------------------------------ contract and wiring


def _spec() -> dict[str, Any]:
    return yaml.safe_load(INTERFACE_PATH.read_text())


def test_c1_interface_contract() -> None:
    spec = _spec()
    paths = spec["paths"]
    prefix = "/api/v1/organizations/{organization_id}/projects/{project_id}/dataset-splits"
    want = {
        prefix: {"get"},
        prefix + "/{manifest_id}": {"get"},
        prefix + "/{manifest_id}/approve": {"post"},
        prefix + "/{manifest_id}/reject": {"post"},
    }
    for path, methods in want.items():
        entry = paths.get(path)
        check = entry is not None
        if not check:
            FAILURES.append(f"FAIL [{CID}] 05-interface.yaml declares {path}")
            continue
        found = {m for m in entry if m in ("get", "post", "put", "patch", "delete")}
        if found != methods:
            FAILURES.append(f"FAIL [{CID}] {path} declares exactly {methods}, found {found}")
    if any(m in paths[prefix] for m in ("post", "put", "delete")):
        FAILURES.append(f"FAIL [{CID}] the collection declares a create route")
    for action in ("approve", "reject"):
        op = paths[prefix + f"/{{manifest_id}}/{action}"]["post"]
        header = [p for p in op["parameters"] if p.get("in") == "header"]
        if not (
            len(header) == 1
            and header[0]["name"] == "Idempotency-Key"
            and header[0]["required"] is True
        ):
            FAILURES.append(f"FAIL [{CID}] {action} requires the Idempotency-Key header")
        for status in ("400", "401", "403", "404", "409", "200"):
            if status not in op["responses"]:
                FAILURES.append(f"FAIL [{CID}] {action} documents {status}")
    schemas = spec["components"]["schemas"]
    for name in (
        "DatasetSplitManifestApproveRequest",
        "DatasetSplitManifestRejectRequest",
        "DatasetSplitLeakageReviewInput",
        "DatasetSplitManifest",
        "DatasetSplitManifestRoleRow",
        "DatasetSplitManifestSourceRow",
        "DatasetSplitLeakageSummary",
        "DatasetSplitManifestPage",
    ):
        if schemas.get(name, {}).get("additionalProperties") is not False:
            FAILURES.append(f"FAIL [{CID}] {name} is closed (additionalProperties false)")
    props = set(schemas["DatasetSplitManifest"]["properties"])
    if props != VIEW_KEYS:
        FAILURES.append(
            f"FAIL [{CID}] DatasetSplitManifest properties differ from the response: {props ^ VIEW_KEYS}"
        )
    if set(schemas["DatasetSplitManifestRoleRow"]["properties"]) != ROLE_KEYS:
        FAILURES.append(f"FAIL [{CID}] role row properties")
    if set(schemas["DatasetSplitManifestSourceRow"]["properties"]) != SOURCE_KEYS:
        FAILURES.append(f"FAIL [{CID}] source row properties")
    approve = schemas["DatasetSplitManifestApproveRequest"]
    if set(approve["properties"]) != {"expected_proposal_sha256", "leakage_reviews"} or set(
        approve["required"]
    ) != set(approve["properties"]):
        FAILURES.append(f"FAIL [{CID}] approve request fields")
    if approve["properties"]["leakage_reviews"]["maxItems"] != 200:
        FAILURES.append(f"FAIL [{CID}] approve request caps reviews at 200")
    review = schemas["DatasetSplitLeakageReviewInput"]["properties"]
    if review["rationale"]["maxLength"] != 2000 or review["rationale"]["minLength"] != 1:
        FAILURES.append(f"FAIL [{CID}] rationale bounds")
    flat = json.dumps(
        {
            k: v
            for k, v in schemas.items()
            if k.startswith("DatasetSplit") and not k.endswith("Request")
        }
    )
    for banned in (
        "record_id",
        "group_id",
        "assignment",
        "object_key",
        "storage_key",
        "bucket",
        'leakage_report"',
        "leakage_reviews",
    ):
        if banned in flat:
            FAILURES.append(f"FAIL [{CID}] the split schemas must not name {banned!r}")
    codes = set(
        re.findall(
            r"`([a-z_]+)`",
            json.dumps(paths[prefix + "/{manifest_id}/approve"]["post"]["responses"]),
        )
    )
    for code in (
        "manifest_changed",
        "manifest_not_proposed",
        "leakage_not_clear",
        "manifest_insufficient",
        "manifest_tampered",
        "invalid_body",
        "invalid_idempotency_key",
        "invalid_review",
    ):
        if code not in codes:
            FAILURES.append(f"FAIL [{CID}] approve documents {code}")
    reject_codes = set(
        re.findall(
            r"`([a-z_]+)`", json.dumps(paths[prefix + "/{manifest_id}/reject"]["post"]["responses"])
        )
    )
    for code in (
        "manifest_changed",
        "manifest_not_proposed",
        "invalid_body",
        "invalid_idempotency_key",
    ):
        if code not in reject_codes:
            FAILURES.append(f"FAIL [{CID}] reject documents {code}")


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


def test_c2_router_is_wired_into_the_app() -> None:
    main = MAIN_PATH.read_text()
    if (
        "from app.datasets.dataset_split_manifest_routes import build_dataset_split_manifest_router"
        not in main
    ):
        FAILURES.append(f"FAIL [{CID}] main.py imports build_dataset_split_manifest_router")
    if (
        re.search(
            r"app\.include_router\(\s*build_dataset_split_manifest_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)\s*\)",
            main,
        )
        is None
    ):
        FAILURES.append(
            f"FAIL [{CID}] main.py includes the router with the shared resolver and lookup"
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
    with running_app_for(app) as base:
        for method, suffix in (
            ("GET", ""),
            ("GET", f"/{MANIFEST}"),
            ("POST", f"/{MANIFEST}/approve"),
            ("POST", f"/{MANIFEST}/reject"),
        ):
            status = call(
                base, method, BASE + suffix, None, {} if method == "POST" else None
            ).status
            if status != 401:
                FAILURES.append(
                    f"FAIL [{CID}] {method} {suffix or '/'} is mounted on the real app (401, not {status})"
                )


def test_c3_proving_symbol_is_the_wired_builder() -> None:
    matrix = (ROOT / "docs" / "spec-matrix.yaml").read_text()
    if "FTF-0519" not in matrix:
        FAILURES.append(f"FAIL [{CID}] docs/spec-matrix.yaml has an FTF-0519 entry")
    if not hasattr(real_routes, "build_dataset_split_manifest_router"):
        FAILURES.append(f"FAIL [{CID}] the proving symbol exists")


def main() -> None:
    tests: list[tuple[str, Callable[[], None]]] = [
        (n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)
    ]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-0519-P2 split manifest route tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0519-P2 split manifest route tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
