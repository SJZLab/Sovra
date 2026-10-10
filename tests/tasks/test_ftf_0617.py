#!/usr/bin/env python3
# mypy: disable-error-code="dict-item,arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,misc,no-untyped-def,no-untyped-call"
# ruff: noqa: E501
"""FTF-0617 -- Zero-egress judge policy.

No database. The DB boundary (`execute_tenant_scoped`, `get_connection`,
`lookup_org_role`) is replaced in this file by an in-memory fake that honours
tenant scoping; everything else is the real default code: the real FTF-0901
`enforce_effect`, the real FTF-H005 `attest_route` and `pin_route_identity`, and the real
FTF-0616 `grade_llm_judge` against a real local TLS gateway (the FTF-0616 harness,
reused verbatim). The real-Postgres half (`tests/integration/test_ftf_0617_zero_egress_judge.py`)
proves the actual SQL, RLS and `lookup_org_role` wiring.

Acceptance ("Server enforces private-only judge or authorized humans on strict
projects") -- `test_mode_table_*`, `test_profile_*`, `test_human_*`.

Negative acceptance ("External fallback or unknown upstream judge fails closed even when
LiteLLM alias looks local") -- `test_admission_*`, `test_run_*`, `test_looks_local_*`: the
request counter shows ZERO requests carrying candidate content when admission fails.

Every protection also has a behavioural fault -> RED row (`test_fault_*`): the module source is
mutated in memory, a fresh copy is loaded, and the scenario that pins the protection must fail.

Run directly: python3 tests/tasks/test_ftf_0617.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import types
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_ftf_0616 as h  # noqa: E402  (the certified FTF-0616 gateway harness, reused verbatim)
import workers.evaluation.zero_egress_judge_policy as m  # noqa: E402
from packages.security.security_profile_enforcement import (  # noqa: E402
    Destination,
    RouteAuthorization,
)
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.rbac_middleware import Role  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
from app.litellm.connection_crud import ConnectionNotFoundError  # noqa: E402

FAILURES: list[str] = []
MODULE_PATH = ROOT / "workers" / "evaluation" / "zero_egress_judge_policy.py"
NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
CANDIDATE = "CANDIDATE-CONTENT-7f3a9c"
PROBE_PROMPT = "Reply with the single word: pong."
IDP = "https://idp.test"
# `scripts/mutation_gate.py` edits the real module in place and re-runs this file once per mutant:
# the in-memory fault table (which only proves the tests can fail) steps aside there, and the
# generated-variant sweeps use a thinner deterministic sample so each run stays short.
UNDER_MUTATION_GATE = "mutation-gate-pyc" in os.environ.get("PYTHONPYCACHEPREFIX", "")
# Leak marker built at run time so no source line (which a traceback prints) contains it.
MK = "qx7" + "mk"


def L(name: str) -> str:
    return MK + "-" + name


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Gateway: probe (admission) and judge calls answered separately ------------------


class _Gateway:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.requests: list[tuple[bool, str]] = []
        self.probe_model: str | None = "judge-model-v1"
        self.probe_fp: str | None = "dep-A"
        self.probe_status = 200
        self.judge_model = "judge-model-v1"
        self.judge_fp: str | None = "dep-A"
        self.judge_status = 200
        self.judge_body: bytes | None = None
        self.verdict = "pass"

    @property
    def candidate_requests(self) -> int:
        return sum(1 for _probe, body in self.requests if CANDIDATE in body)


G = _Gateway()
PORT = 0

if h._TRUSTED_LEAF is not None:

    class _PolicyGatewayHandler(h.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _send(self, status: int, body: bytes, fingerprint: str | None) -> None:
            self.send_response(status)
            if fingerprint is not None:
                self.send_header("x-litellm-model-id", fingerprint)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            is_probe = PROBE_PROMPT in raw
            G.requests.append((is_probe, raw))
            if self.headers.get("Authorization", "") != f"Bearer {h._SECRET_VALUE}":
                self._send(
                    401, b'{"error":{"type":"invalid_request_error","message":"no key"}}', None
                )
                return
            if is_probe:
                if G.probe_status != 200:
                    self._send(G.probe_status, b"{}", None)
                    return
                body = json.dumps(
                    {
                        "id": "chatcmpl-probe",
                        "model": G.probe_model,
                        "choices": [
                            {"index": 0, "message": {"role": "assistant", "content": "pong"}}
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                ).encode()
                self._send(200, body, G.probe_fp)
                return
            if G.judge_status != 200:
                self._send(G.judge_status, b"{}", None)
                return
            body = G.judge_body or h._success_body(h._verdict_json(G.verdict), model=G.judge_model)
            self._send(200, body, G.judge_fp)

    _SERVER = h._start_https_server(_PolicyGatewayHandler)
    PORT = _SERVER.server_address[1]


# -- Fake DB boundary (tenant scoping honoured; real SQL is the integration test's job) ---

ORG_A, ORG_B = uuid.uuid4(), uuid.uuid4()
PROJECT_A, PROJECT_B = uuid.uuid4(), uuid.uuid4()
CONN_A, CONN_B = uuid.uuid4(), uuid.uuid4()
SESSION = Session()
CTX_A = TenantContext(ORG_A)
ADMIN = (IDP, "admin-1")
REVIEWER = (IDP, "reviewer-1")
PRODUCER = (IDP, "producer-1")


class _Rows:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.org_profile = {ORG_A: "zero_egress", ORG_B: "standard"}
        self.projects = {(ORG_A, PROJECT_A): "standard", (ORG_B, PROJECT_B): "standard"}
        self.connections = {
            (ORG_A, CONN_A): SimpleNamespace(
                status="active", base_url=f"https://127.0.0.1:{PORT}", secret_ref=h._SECRET_REF
            ),
            (ORG_B, CONN_B): SimpleNamespace(
                status="active", base_url=f"https://127.0.0.1:{PORT}", secret_ref=h._SECRET_REF
            ),
        }
        self.memberships = {
            (ORG_A, *ADMIN): Role.ORGANIZATION_ADMIN,
            (ORG_A, *REVIEWER): Role.EVALUATOR,
            (ORG_A, *PRODUCER): Role.ML_ENGINEER,
        }


DB = _Db()


def _fake_exec(session, organization_id, statement, params=None):
    sql = str(statement)
    params = params or {}
    if params.get("org") != organization_id:  # RLS: only the context org's rows exist
        return _Rows([])
    if "FROM organizations" in sql:
        value = DB.org_profile.get(organization_id)
        return _Rows([] if value is None else [(value,)])
    if "FROM projects" in sql:
        value = DB.projects.get((organization_id, params["project"]))
        return _Rows([] if value is None else [(value,)])
    raise AssertionError(f"unexpected SQL {sql!r}")


def _fake_get_connection(session, context, connection_id):
    row = DB.connections.get((context.organization_id, connection_id))
    if row is None:
        raise ConnectionNotFoundError(f"connection {connection_id} {MK}-conn not found")
    return row


def _fake_lookup(session_factory, issuer, subject, organization_id):
    return DB.memberships.get((organization_id, issuer, subject))


def install(mod: types.ModuleType) -> types.ModuleType:
    mod.execute_tenant_scoped = _fake_exec
    mod.get_connection = _fake_get_connection
    mod.lookup_org_role = _fake_lookup
    return mod


def reset() -> None:
    G.reset()
    DB.reset()
    h._reset_gateway()


# -- Approval + call helpers -----------------------------------------------------------


def approval_dict(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "judge_model_id": "private-judge",
        "resolved_model": "judge-model-v1",
        "deployment_fingerprint": "dep-A",
        "boundary_id": "boundary-1",
        "destinations": {
            "upstream": {"kind": "internal"},
            "fallbacks": [],
            "logging_callbacks": {"kind": "internal"},
            "cache": {"kind": "disabled"},
            "embedding": {"kind": "disabled"},
        },
        "approved_by_issuer": ADMIN[0],
        "approved_by_subject": ADMIN[1],
        "approved_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    }
    document.update(overrides)
    return document


def approval_json(**overrides: object) -> str:
    return json.dumps(approval_dict(**overrides))


def with_destination(role: str, value: object) -> str:
    document = approval_dict()
    document["destinations"][role] = value  # type: ignore[index]
    return json.dumps(document)


def parse(mod, raw: object, *, org="zero_egress", project="standard", job=None, now=NOW):
    return mod.parse_private_judge_approval(
        raw, organization_profile=org, project_profile=project, job_profile=job, now=now
    )


def code_of(call) -> str:
    """The stable code of the module error `call()` raises ('' if it returns, 'EXC:<type>' else)."""
    try:
        call()
    except m.ZeroEgressJudgePolicyError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        return f"EXC:{type(exc).__name__}"
    return ""


def code_of_mod(mod, call) -> str:
    try:
        call()
    except mod.ZeroEgressJudgePolicyError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        return f"EXC:{type(exc).__name__}"
    return ""


def decide(mod, **overrides):
    kwargs = {
        "session": SESSION,
        "context": CTX_A,
        "session_factory": lambda: SESSION,
        "project_id": PROJECT_A,
        "job_profile": None,
        "connection_id": CONN_A,
        "approval": approval_json(),
        "human_reviewers": (),
        "candidate_producer": PRODUCER,
        "judge_destination": None,
        "authorization": None,
        "secret_config": h._SECRET_CONFIG,
        "policy": h._policy_for(PORT),
        "now": NOW,
    }
    kwargs.update(overrides)
    return mod.decide_judge_plan(**kwargs)


CALIBRATION = h._calibration(
    judge_model_id="private-judge", judge_model_aliases=("judge-model-v1",)
)


def run(mod, plan, **overrides):
    kwargs = {
        "session": SESSION,
        "context": CTX_A,
        "job_profile": None,
        "rubric": h._RUBRIC,
        "calibration": CALIBRATION,
        "candidate_model_id": "candidate-model-v1",
        "candidate_response": CANDIDATE,
        "secret_config": h._SECRET_CONFIG,
        "policy": h._policy_for(PORT),
        "now": NOW,
    }
    kwargs.update(overrides)
    return mod.run_private_judge(plan, **kwargs)


# =========================== Scenarios (each returns a list of problems) ===========================


def sc_parse_valid(mod) -> list[str]:
    problems: list[str] = []
    a = parse(mod, approval_json())
    if (a.judge_model_id, a.resolved_model, a.deployment_fingerprint, a.boundary_id) != (
        "private-judge",
        "judge-model-v1",
        "dep-A",
        "boundary-1",
    ):
        problems.append("identity fields not carried exactly")
    if (a.upstream.kind, a.logging_callbacks.kind, a.cache.kind, a.embedding.kind) != (
        "internal",
        "internal",
        "disabled",
        "disabled",
    ):
        problems.append("destinations not carried exactly")
    if (a.approved_by_issuer, a.approved_by_subject) != ADMIN:
        problems.append("approver not carried exactly")
    if parse(mod, approval_json().encode()).judge_model_id != "private-judge":
        problems.append("bytes input refused")
    return problems


def _bad_values_for(key: str) -> list[tuple[str, object]]:
    base = approval_dict()[key]
    values: list[tuple[str, object]] = [
        ("null", None),
        ("int", 7),
        ("float", 1.5),
        ("bool", True),
        ("list", []),
        ("nested object", {"a": 1}),
        ("empty string", ""),
    ]
    return [(label, value) for label, value in values if value != base]


def sc_parse_strict_table(mod) -> list[str]:
    problems: list[str] = []
    document = approval_dict()
    for key in document:
        missing = {k: v for k, v in document.items() if k != key}
        got = code_of_mod(mod, lambda missing=missing: parse(mod, json.dumps(missing)))
        if got != "judge_approval_invalid":
            problems.append(f"missing {key}: {got!r}")
        for label, value in _bad_values_for(key):
            if key == "destinations" and label == "nested object":
                continue
            raw = json.dumps({**document, key: value})
            got = code_of_mod(mod, lambda raw=raw: parse(mod, raw))
            if got != "judge_approval_invalid":
                problems.append(f"{key}={label}: {got!r}")
    extra = json.dumps({**document, "unexpected": 1})
    if code_of_mod(mod, lambda: parse(mod, extra)) != "judge_approval_invalid":
        problems.append("extra top-level key accepted")
    for label, version in (("2", 2), ("0", 0), ("1.0", 1.0), ("true", True), ("'1'", "1")):
        raw = json.dumps({**document, "schema_version": version})
        if code_of_mod(mod, lambda raw=raw: parse(mod, raw)) != "judge_approval_invalid":
            problems.append(f"schema_version {label} accepted")
    good = approval_json()
    duplicate = good[:-1] + ', "judge_model_id": "other"}'
    nan = good.replace('"schema_version": 1', '"schema_version": NaN')
    infinity = good.replace('"schema_version": 1', '"schema_version": Infinity')
    hugeint = good.replace('"schema_version": 1', '"schema_version": ' + "9" * 5000)
    deep = '{"a":' * 5000 + "1" + "}" * 5000
    for constant in ("NaN", "Infinity", "-Infinity"):
        # Inside `fallbacks` a float would otherwise read as a (refused) fallback, not as invalid JSON.
        cases_nan = good.replace('"fallbacks": []', '"fallbacks": [' + constant + "]")
        if code_of_mod(mod, lambda raw=cases_nan: parse(mod, raw)) != "judge_approval_invalid":
            problems.append(f"{constant} inside fallbacks is not invalid JSON")
    cases: list[tuple[str, object]] = [
        ("duplicate key", duplicate),
        ("NaN", nan),
        ("Infinity", infinity),
        ("huge int", hugeint),
        ("deep nesting", deep),
        ("empty", ""),
        ("whitespace only", "   "),
        ("array", "[]"),
        ("scalar", "7"),
        ("null", "null"),
        ("not json", "{not json"),
        ("trailing garbage", good + " x"),
        ("invalid utf-8", b'{"a":"\xff"}'),
        ("oversize", " " * m.APPROVAL_MAX_BYTES + good),
        ("dict passed instead of JSON", approval_dict()),
        ("None passed", None),
        ("int passed", 7),
        ("list passed", [approval_json()]),
        ("str subclass passed", type("S", (str,), {})(good)),
        ("bytearray passed", bytearray(good.encode())),
    ]
    for label, raw in cases:
        if label == "str subclass passed":
            continue  # a str subclass is still the caller's JSON text; its content is what is parsed
        got = code_of_mod(mod, lambda raw=raw: parse(mod, raw))
        if got != "judge_approval_invalid":
            problems.append(f"{label}: {got!r}")
    at_limit = " " * (m.APPROVAL_MAX_BYTES - len(good.encode())) + good
    if len(at_limit.encode()) != m.APPROVAL_MAX_BYTES or parse(mod, at_limit) is None:
        problems.append("approval of exactly the size limit refused")
    return problems


def sc_parse_destinations(mod) -> list[str]:
    problems: list[str] = []
    document = approval_dict()
    destinations = document["destinations"]
    for role in m.DESTINATION_ROLES:
        missing = {k: v for k, v in destinations.items() if k != role}
        raw = json.dumps({**document, "destinations": missing})
        if code_of_mod(mod, lambda raw=raw: parse(mod, raw)) != "judge_approval_invalid":
            problems.append(f"destination role {role} missing accepted")
    extra = json.dumps(
        {**document, "destinations": {**destinations, "metrics": {"kind": "internal"}}}
    )
    if code_of_mod(mod, lambda: parse(mod, extra)) != "judge_approval_invalid":
        problems.append("extra destination role accepted")
    if (
        code_of_mod(mod, lambda: parse(mod, json.dumps({**document, "destinations": []})))
        != "judge_approval_invalid"
    ):
        problems.append("destinations as list accepted")
    for role in ("upstream", "logging_callbacks", "cache", "embedding"):
        bad: list[tuple[str, object]] = [
            ("unknown kind", {"kind": "unknown"}),
            ("kind missing", {"provider": "x"}),
            ("kind list", {"kind": ["internal"]}),
            ("kind null", {"kind": None}),
            ("internal with provider", {"kind": "internal", "provider": "acme"}),
            ("internal with extra key", {"kind": "internal", "x": 1}),
            ("managed without provider", {"kind": "managed_provider"}),
            ("external without provider", {"kind": "external"}),
            ("external provider int", {"kind": "external", "provider": 7}),
            ("external provider empty", {"kind": "external", "provider": ""}),
            ("external extra key", {"kind": "external", "provider": "acme", "x": 1}),
            ("disabled with provider", {"kind": "disabled", "provider": "acme"}),
            ("string", "internal"),
            ("null", None),
            ("list", []),
        ]
        if role not in ("cache", "embedding"):
            bad.append(("disabled", {"kind": "disabled"}))
        for label, value in bad:
            got = code_of_mod(
                mod, lambda role=role, value=value: parse(mod, with_destination(role, value))
            )
            if got != "judge_approval_invalid":
                problems.append(f"{role} {label}: {got!r}")
    for role in ("upstream", "logging_callbacks", "cache", "embedding"):
        for kind in ("managed_provider", "external"):
            raw = with_destination(role, {"kind": kind, "provider": "acme"})
            got = code_of_mod(mod, lambda raw=raw: parse(mod, raw))
            if got != "judge_destination_blocked":
                problems.append(f"{role} {kind}: {got!r}")
    for role in ("cache", "embedding"):
        if (
            parse(mod, with_destination(role, {"kind": "internal"})).judge_model_id
            != "private-judge"
        ):
            problems.append(f"{role} internal refused")
    return problems


def sc_parse_fallbacks(mod) -> list[str]:
    problems: list[str] = []
    for label, value in (
        ("internal fallback", [{"kind": "internal"}]),
        ("external fallback", [{"kind": "external", "provider": "acme"}]),
        ("garbage entry", [7]),
        ("two entries", [{"kind": "internal"}, {"kind": "internal"}]),
    ):
        got = code_of_mod(mod, lambda value=value: parse(mod, with_destination("fallbacks", value)))
        if got != "judge_fallback_not_permitted":
            problems.append(f"{label}: {got!r}")
    for label, value in (("null", None), ("object", {}), ("string", "x")):
        got = code_of_mod(mod, lambda value=value: parse(mod, with_destination("fallbacks", value)))
        if got != "judge_approval_invalid":
            problems.append(f"fallbacks {label}: {got!r}")
    return problems


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def sc_parse_times(mod) -> list[str]:
    problems: list[str] = []
    micro = timedelta(microseconds=1)

    def with_times(approved: str, expires: str) -> str:
        return approval_json(approved_at=approved, expires_at=expires)

    cases: list[tuple[str, str, str]] = [
        ("expires == now", _stamp(NOW - timedelta(hours=1)), _stamp(NOW)),
        ("expires just before now", _stamp(NOW - timedelta(hours=1)), _stamp(NOW - micro)),
        ("approved just after now", _stamp(NOW + micro), _stamp(NOW + timedelta(hours=1))),
        (
            "approved in the future",
            _stamp(NOW + timedelta(hours=1)),
            _stamp(NOW + timedelta(hours=2)),
        ),
    ]
    for label, approved, expires in cases:
        got = code_of_mod(mod, lambda a=approved, e=expires: parse(mod, with_times(a, e)))
        if got != "judge_approval_expired":
            problems.append(f"{label}: {got!r}")
    ok_cases: list[tuple[str, str, str]] = [
        ("expires just after now", _stamp(NOW - timedelta(hours=1)), _stamp(NOW + micro)),
        ("approved == now", _stamp(NOW), _stamp(NOW + timedelta(hours=1))),
        ("offset form", "2026-10-10T14:00:00+03:00", "2026-10-10T16:00:00+03:00"),
        ("fractional", "2026-10-10T11:00:00.123456Z", "2026-10-10T13:00:00.5Z"),
    ]
    for label, approved, expires in ok_cases:
        try:
            parse(mod, with_times(approved, expires))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}: refused ({type(exc).__name__})")
    inverted = with_times(_stamp(NOW + timedelta(hours=1)), _stamp(NOW - timedelta(hours=1)))
    equal = with_times(_stamp(NOW - timedelta(hours=1)), _stamp(NOW - timedelta(hours=1)))
    for label, raw in (("expires before approved", inverted), ("expires == approved", equal)):
        if code_of_mod(mod, lambda raw=raw: parse(mod, raw)) != "judge_approval_invalid":
            problems.append(f"{label} not invalid")
    good_a, good_e = "2026-10-10T11:00:00Z", "2026-10-10T13:00:00Z"
    for label, value in (
        ("naive", "2026-10-10T11:00:00"),
        ("date only", "2026-10-10"),
        ("lowercase t", "2026-10-10t11:00:00Z"),
        ("space separator", "2026-10-10 11:00:00Z"),
        ("trailing newline", good_a + "\n"),
        ("leading space", " " + good_a),
        ("fullwidth digits", "２０２６-10-10T11:00:00Z"),
        ("arabic-indic digits", "٢٠٢٦-10-10T11:00:00Z"),
        ("no seconds", "2026-10-10T11:00Z"),
        ("month 13", "2026-13-10T11:00:00Z"),
        ("seven fractional digits", "2026-10-10T11:00:00.1234567Z"),
        ("epoch int", 1760000000),
        ("null", None),
        ("empty", ""),
    ):
        for field in ("approved_at", "expires_at"):
            raw = json.dumps({**approval_dict(approved_at=good_a, expires_at=good_e), field: value})
            got = code_of_mod(mod, lambda raw=raw: parse(mod, raw))
            if got != "judge_approval_invalid":
                problems.append(f"{field} {label}: {got!r}")
    for label, bad_now in (
        ("naive now", datetime(2026, 10, 10, 12, 0, 0)),
        ("string now", "2026-10-10T12:00:00Z"),
        ("None now", None),
        ("date now", NOW.date()),
    ):
        got = code_of_mod(mod, lambda bad_now=bad_now: parse(mod, approval_json(), now=bad_now))
        if got != "judge_policy_invalid_input":
            problems.append(f"{label}: {got!r}")
    return problems


def sc_parse_text_bounds(mod) -> list[str]:
    problems: list[str] = []
    text_keys = (
        ("judge_model_id", 200),
        ("resolved_model", 200),
        ("boundary_id", 200),
        ("approved_by_issuer", 200),
        ("approved_by_subject", 200),
        ("deployment_fingerprint", 256),
    )
    for key, limit in text_keys:
        for length, ok in ((0, False), (1, True), (limit, True), (limit + 1, False)):
            raw = approval_json(**{key: "a" * length})
            got = code_of_mod(mod, lambda raw=raw: parse(mod, raw))
            if ok != (got == ""):
                problems.append(f"{key} length {length}: {got!r}")
        for label, value in (
            ("NUL", "a\x00b"),
            ("CR", "a\rb"),
            ("LF", "a\nb"),
            ("trailing LF", "ab\n"),
            ("lone surrogate", "a\ud800b"),
            ("space", "a b"),
            ("tab", "a\tb"),
            ("NBSP", "a b"),
            ("zero-width space", "a​b"),
            ("line separator", "a b"),
            ("RLO", "a‮b"),
            ("DEL", "a\x7fb"),
        ):
            got = code_of_mod(
                mod, lambda key=key, value=value: parse(mod, approval_json(**{key: value}))
            )
            if got != "judge_approval_invalid":
                problems.append(f"{key} {label}: {got!r}")
        for label, value in (("astral", "a\U0001f600b"), ("combining", "aéb")):
            try:
                parsed = parse(mod, approval_json(**{key: value}))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{key} {label} refused ({type(exc).__name__})")
                continue
            carried = {
                "judge_model_id": parsed.judge_model_id,
                "resolved_model": parsed.resolved_model,
                "boundary_id": parsed.boundary_id,
                "approved_by_issuer": parsed.approved_by_issuer,
                "approved_by_subject": parsed.approved_by_subject,
                "deployment_fingerprint": parsed.deployment_fingerprint,
            }[key]
            if carried != value:
                problems.append(f"{key} {label} not carried exactly (normalised)")
    for length, ok in ((0, False), (1, True), (200, True), (201, False)):
        raw = with_destination("upstream", {"kind": "external", "provider": "p" * length})
        got = code_of_mod(mod, lambda raw=raw: parse(mod, raw))
        want = "judge_destination_blocked" if ok else "judge_approval_invalid"
        if got != want:
            problems.append(f"provider length {length}: {got!r}")
    return problems


# -- decide ---------------------------------------------------------------------------


def _is_private(plan) -> bool:
    return plan.mode.value == "private_llm_judge"


def sc_profile_db_only(mod) -> list[str]:
    problems: list[str] = []
    reset()
    plan = decide(mod)
    if (plan.mode.value, plan.reason, plan.profile) != (
        "private_llm_judge",
        "private_judge_attested",
        "zero_egress",
    ):
        problems.append(f"org zero_egress/project standard: {plan.mode.value}/{plan.profile}")
    DB.org_profile[ORG_A], DB.projects[(ORG_A, PROJECT_A)] = "standard", "zero_egress"
    plan = decide(mod)
    if plan.profile != "zero_egress" or not _is_private(plan):
        problems.append(f"project zero_egress under standard org: {plan.profile}")
    DB.projects[(ORG_A, PROJECT_A)] = "standard"
    plan = decide(mod, job_profile="air_gapped")
    if plan.profile != "air_gapped" or not _is_private(plan):
        problems.append(f"job air_gapped over standard org+project: {plan.profile}")
    DB.org_profile[ORG_A] = "air_gapped"
    plan = decide(mod, job_profile="standard")
    if plan.profile != "air_gapped":
        problems.append(f"job standard lowered the profile: {plan.profile}")
    DB.org_profile[ORG_A] = "standard"
    plan = decide(mod, judge_destination=Destination("internal"))
    if (plan.mode.value, plan.profile) != ("standard_llm_judge", "standard"):
        problems.append(f"all standard: {plan.mode.value}/{plan.profile}")
    # No caller-supplied profile exists: a stored unknown profile is refused, never "standard".
    DB.projects[(ORG_A, PROJECT_A)] = "private-ish"
    got = code_of_mod(mod, lambda: decide(mod, judge_destination=Destination("internal")))
    if got != "judge_policy_invalid_input":
        problems.append(f"unknown stored profile: {got!r}")
    return problems


def sc_not_found_identical(mod) -> list[str]:
    problems: list[str] = []
    reset()

    def failure(**kw):
        try:
            decide(mod, **kw)
        except mod.ZeroEgressJudgePolicyError as exc:
            return (type(exc).__name__, exc.code, str(exc), exc.args)
        return None

    missing = failure(project_id=uuid.uuid4())
    foreign = failure(project_id=PROJECT_B)
    if missing is None or missing[1] != "judge_policy_not_found":
        problems.append(f"missing project: {missing}")
    if foreign != missing:
        problems.append(f"foreign project differs from missing: {foreign} vs {missing}")
    other = failure(context=TenantContext(ORG_B), project_id=PROJECT_A, connection_id=CONN_B)
    if other is None or other[1] != "judge_policy_not_found":
        problems.append(f"org B asking for org A project: {other}")
    DB.org_profile.pop(ORG_A)
    if (failure() or ("", ""))[1] != "judge_policy_not_found":
        problems.append("missing organization row not refused")
    return problems


def sc_connection_checks(mod) -> list[str]:
    problems: list[str] = []
    reset()
    DB.connections[(ORG_A, CONN_A)].status = "revoked"
    if code_of_mod(mod, lambda: decide(mod)) != "judge_gateway_revoked":
        problems.append("revoked connection not refused")
    DB.connections[(ORG_A, CONN_A)].status = "suspended"
    if code_of_mod(mod, lambda: decide(mod)) != "judge_gateway_revoked":
        problems.append("non-active connection not refused")
    DB.connections[(ORG_A, CONN_A)].status = "active"
    missing = code_of_mod(mod, lambda: decide(mod, connection_id=uuid.uuid4()))
    foreign = code_of_mod(mod, lambda: decide(mod, connection_id=CONN_B))
    if missing != "judge_policy_not_found" or foreign != missing:
        problems.append(f"missing/foreign connection: {missing!r}/{foreign!r}")
    for label, bad in (("str", str(CONN_A)), ("None", None), ("int", 7)):
        if (
            code_of_mod(mod, lambda bad=bad: decide(mod, connection_id=bad))
            != "judge_policy_invalid_input"
        ):
            problems.append(f"connection_id {label} not refused")
    if G.requests:
        problems.append("a gateway request was made although the connection was refused")
    return problems


def sc_input_types(mod) -> list[str]:
    problems: list[str] = []
    reset()
    bad: list[tuple[str, dict]] = [
        ("project_id str", {"project_id": str(PROJECT_A)}),
        ("project_id None", {"project_id": None}),
        ("naive now", {"now": datetime(2026, 10, 10, 12, 0, 0)}),
        ("now None", {"now": None}),
        ("context dict", {"context": {"organization_id": ORG_A}}),
        ("context subclass-less object", {"context": SimpleNamespace(organization_id=ORG_A)}),
        ("session None", {"session": None}),
        ("session fake", {"session": SimpleNamespace()}),
        ("job_profile int", {"job_profile": 7}),
        ("job_profile empty", {"job_profile": ""}),
        ("reviewers list", {"human_reviewers": [REVIEWER]}),
        ("reviewers too many", {"human_reviewers": tuple((IDP, f"r{i}") for i in range(51))}),
        ("reviewer bad pair", {"human_reviewers": (("a",),)}),
        ("reviewer non-str", {"human_reviewers": ((IDP, 7),)}),
        ("producer list", {"candidate_producer": [IDP, "p"]}),
        ("producer empty subject", {"candidate_producer": (IDP, "")}),
    ]
    for label, kw in bad:
        got = code_of_mod(mod, lambda kw=kw: decide(mod, **kw))
        if got != "judge_policy_invalid_input":
            problems.append(f"{label}: {got!r}")
    if G.requests:
        problems.append("a gateway request was made for a malformed argument")
    return problems


ROLE_SUBJECT = {role: (IDP, f"{role.value}-1") for role in Role}


def add_all_roles() -> None:
    for role, identity in ROLE_SUBJECT.items():
        DB.memberships[(ORG_A, *identity)] = role


def sc_mode_table_strict(mod) -> list[str]:
    problems: list[str] = []

    def expect(label, plan, mode, reason, defect=None):
        if (plan.mode.value, plan.reason, plan.approval_defect) != (mode, reason, defect):
            problems.append(f"{label}: got {(plan.mode.value, plan.reason, plan.approval_defect)}")

    cap = ("blocked", "judge_capability_unavailable")
    human = ("human_review", "authorized_human_review")
    rows: list[tuple[str, dict, tuple, str | None]] = []
    rows.append(("valid", {}, ("private_llm_judge", "private_judge_attested"), None))
    for role in ("upstream", "logging_callbacks", "cache", "embedding"):
        for kind in ("managed_provider", "external"):
            rows.append(
                (
                    f"{role} {kind}",
                    {"approval": with_destination(role, {"kind": kind, "provider": "acme"})},
                    cap,
                    "judge_destination_blocked",
                )
            )
    for role in m.DESTINATION_ROLES:
        document = approval_dict()
        del document["destinations"][role]
        rows.append(
            (
                f"{role} key missing",
                {"approval": json.dumps(document)},
                cap,
                "judge_approval_invalid",
            )
        )
    rows += [
        (
            "fallbacks",
            {"approval": with_destination("fallbacks", [{"kind": "internal"}])},
            cap,
            "judge_fallback_not_permitted",
        ),
        (
            "expired",
            {"approval": approval_json(expires_at=_stamp(NOW))},
            cap,
            "judge_approval_expired",
        ),
        (
            "not yet valid",
            {
                "approval": approval_json(
                    approved_at=_stamp(NOW + timedelta(seconds=1)),
                    expires_at=_stamp(NOW + timedelta(hours=1)),
                )
            },
            cap,
            "judge_approval_expired",
        ),
        ("invalid json", {"approval": "{"}, cap, "judge_approval_invalid"),
        (
            "unknown approver",
            {"approval": approval_json(approved_by_subject="nobody")},
            cap,
            "judge_approval_not_authorized",
        ),
        ("none, no humans", {"approval": None}, cap, None),
    ]
    for label, kw, (mode, reason), defect in rows:
        reset()
        add_all_roles()
        expect(label, decide(mod, **kw), mode, reason, defect)
        if G.candidate_requests:
            problems.append(f"{label}: candidate content reached the gateway")
    reset()
    add_all_roles()
    plan = decide(mod)
    if len([1 for probe, _ in G.requests if probe]) != 1 or len(G.requests) != 1:
        problems.append(
            f"valid approval made {len(G.requests)} requests (want exactly one admission)"
        )
    if b"no-cache" not in G.requests[0][1].encode():
        problems.append("admission request did not bypass the cache")
    if plan.approval is None or plan.approval.judge_model_id != "private-judge":
        problems.append("private plan carries no approval")
    # With an authorized independent human, every defect falls to human_review, never to a judge.
    for label, kw, _expected, defect in rows[1:-1]:
        reset()
        add_all_roles()
        plan = decide(mod, human_reviewers=(REVIEWER,), **kw)
        expect(f"{label} + human", plan, *human, defect)
    reset()
    add_all_roles()
    expect("no approval + human", decide(mod, approval=None, human_reviewers=(REVIEWER,)), *human)
    # Humans alone, none usable (the producer; an unknown subject; a viewer).
    for label, reviewers in (
        ("producer only", (PRODUCER,)),
        ("unknown only", ((IDP, "stranger"),)),
        ("viewer only", (ROLE_SUBJECT[Role.VIEWER],)),
        ("empty", ()),
    ):
        reset()
        add_all_roles()
        expect(
            label,
            decide(mod, approval=None, human_reviewers=reviewers, candidate_producer=PRODUCER),
            *cap,
        )
    # Approver role table: only organization_admin and workspace_admin may approve.
    for role, identity in ROLE_SUBJECT.items():
        reset()
        add_all_roles()
        plan = decide(
            mod,
            approval=approval_json(approved_by_issuer=identity[0], approved_by_subject=identity[1]),
        )
        want_private = role in (Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN)
        if _is_private(plan) != want_private:
            problems.append(f"approver role {role.value}: private={_is_private(plan)}")
        if not want_private and plan.approval_defect != "judge_approval_not_authorized":
            problems.append(f"approver role {role.value}: defect {plan.approval_defect}")
    # A human plan names roles, never labels or identities.
    reset()
    add_all_roles()
    plan = decide(mod, approval=None, human_reviewers=(REVIEWER,))
    if plan.human_reviewer_roles != frozenset(
        {Role.EVALUATOR, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
    ):
        problems.append(f"human plan roles: {plan.human_reviewer_roles}")
    return problems


def sc_mode_table_standard(mod) -> list[str]:
    problems: list[str] = []
    reset()
    DB.org_profile[ORG_A] = "standard"
    allow = RouteAuthorization("acme", frozenset({"judge"}), "approver-1", "acct", "eu", "30d")
    rows: list[tuple[str, dict, tuple[str, str]]] = [
        (
            "internal",
            {"judge_destination": Destination("internal")},
            ("standard_llm_judge", "standard_judge_allowed"),
        ),
        (
            "external, no authorization",
            {"judge_destination": Destination("external", "acme")},
            ("blocked", "judge_destination_blocked"),
        ),
        (
            "external, authorized",
            {"judge_destination": Destination("external", "acme"), "authorization": allow},
            ("standard_llm_judge", "standard_judge_allowed"),
        ),
        (
            "external, other provider",
            {"judge_destination": Destination("external", "other"), "authorization": allow},
            ("blocked", "judge_destination_blocked"),
        ),
        (
            "external, other surface",
            {
                "judge_destination": Destination("external", "acme"),
                "authorization": RouteAuthorization("acme", frozenset({"model"}), "approver-1"),
            },
            ("blocked", "judge_destination_blocked"),
        ),
        (
            "managed, authorization incomplete",
            {
                "judge_destination": Destination("managed_provider", "acme"),
                "authorization": RouteAuthorization("acme", frozenset({"judge"}), "approver-1"),
            },
            ("blocked", "judge_destination_blocked"),
        ),
        (
            "managed, authorization complete",
            {"judge_destination": Destination("managed_provider", "acme"), "authorization": allow},
            ("standard_llm_judge", "standard_judge_allowed"),
        ),
    ]
    for label, kw, (mode, reason) in rows:
        plan = decide(mod, approval=None, **kw)
        if (plan.mode.value, plan.reason) != (mode, reason):
            problems.append(f"{label}: {(plan.mode.value, plan.reason)}")
    for label, kw in (
        ("no destination", {"judge_destination": None}),
        ("destination dict", {"judge_destination": {"kind": "internal"}}),
    ):
        if (
            code_of_mod(mod, lambda kw=kw: decide(mod, approval=None, **kw))
            != "judge_policy_invalid_input"
        ):
            problems.append(f"standard {label} not refused")
    # An authorization does not relax a strict project.
    DB.org_profile[ORG_A] = "zero_egress"
    plan = decide(
        mod, approval=None, judge_destination=Destination("external", "acme"), authorization=allow
    )
    if plan.mode.value != "blocked" or plan.reason != "judge_capability_unavailable":
        problems.append(f"strict project honoured a RouteAuthorization: {plan.mode.value}")
    if G.requests:
        problems.append("standard profile decision called the gateway")
    return problems


def _blocked_before_candidate(mod, label, expect_defect, **gw) -> list[str]:
    reset()
    add_all_roles()
    for key, value in gw.items():
        setattr(G, key, value)
    plan = decide(mod)
    problems = []
    if _is_private(plan) or plan.approval_defect != expect_defect:
        problems.append(f"{label}: {plan.mode.value}/{plan.approval_defect}")
    if G.candidate_requests:
        problems.append(f"{label}: candidate content reached the gateway")
    if plan.approval is not None:
        problems.append(f"{label}: a refused plan still carries the approval")
    return problems


def sc_admission_fail_closed(mod) -> list[str]:
    problems: list[str] = []
    mismatch, unverified = "judge_admission_mismatch", "judge_route_identity_unverified"
    for label, defect, gw in (
        ("fingerprint differs", mismatch, {"probe_fp": "dep-B"}),
        ("resolved model differs", mismatch, {"probe_model": "judge-model-v2"}),
        ("both differ", mismatch, {"probe_model": "judge-model-v2", "probe_fp": "dep-B"}),
        ("header missing", unverified, {"probe_fp": None}),
        ("model echo missing", unverified, {"probe_model": None}),
        ("gateway 500", unverified, {"probe_status": 500}),
        ("gateway 401", unverified, {"probe_status": 401}),
        ("gateway 400", unverified, {"probe_status": 400}),
    ):
        problems += _blocked_before_candidate(mod, label, defect, **gw)
    reset()
    add_all_roles()
    plan = decide(mod, policy=h._policy_for(PORT + 1))  # a port the policy does not allow
    if _is_private(plan) or plan.approval_defect != "judge_route_identity_unverified":
        problems.append("destination denied by outbound policy was not fail-closed")
    return problems


LOOKS_LOCAL = (
    "local-judge",
    "localhost/judge",
    "127.0.0.1/judge",
    "ollama/llama3",
    "hosted_vllm/judge",
    "vllm-judge",
    "private-judge",
    "internal-judge",
    "openai/gpt-4o-local",
    "LOCAL",
)


def sc_looks_local(mod) -> list[str]:
    problems: list[str] = []
    for alias in LOOKS_LOCAL:
        # (a) no approval, no humans: an alias that looks local upgrades nothing.
        reset()
        add_all_roles()
        plan = decide(mod, approval=None)
        if plan.mode.value != "blocked":
            problems.append(f"{alias}: no approval gave {plan.mode.value}")
        # (b) an approval whose pinned identity the live gateway does not report.
        for gw in ({"probe_fp": "dep-B"}, {"probe_model": alias + "-x"}):
            reset()
            add_all_roles()
            for key, value in gw.items():
                setattr(G, key, value)
            plan = decide(mod, approval=approval_json(judge_model_id=alias))
            if _is_private(plan):
                problems.append(f"{alias}: mismatched identity gave a private plan")
            if G.candidate_requests:
                problems.append(f"{alias}: candidate content reached the gateway")
        # (c) the gateway is on 127.0.0.1 -- still not evidence: a missing header stays unverified.
        reset()
        add_all_roles()
        G.probe_fp = None
        plan = decide(mod, approval=approval_json(judge_model_id=alias))
        if _is_private(plan):
            problems.append(f"{alias}: loopback gateway without identity gave a private plan")
    return problems


# -- run_private_judge ---------------------------------------------------------------


def _private_plan(mod):
    reset()
    add_all_roles()
    plan = decide(mod)
    if not _is_private(plan):
        raise AssertionError(
            f"fixture: expected a private plan, got {plan.mode.value}/{plan.approval_defect}"
        )
    G.requests.clear()
    return plan


def _judge_requests() -> int:
    return sum(1 for probe, _ in G.requests if not probe)


def sc_run_verdicts(mod) -> list[str]:
    problems: list[str] = []
    for verdict, want in (("pass", True), ("fail", False)):
        plan = _private_plan(mod)
        G.verdict = verdict
        outcome = run(mod, plan)
        if outcome.blocked or outcome.passed is not want or outcome.code is not None:
            problems.append(f"{verdict}: blocked={outcome.blocked} passed={outcome.passed}")
        if (
            outcome.result is None
            or outcome.result.resolved_model != "judge-model-v1"
            or outcome.result.deployment_fingerprint != "dep-A"
        ):
            problems.append(f"{verdict}: result identity not carried")
        if len(G.requests) != 1 or G.candidate_requests != 1:
            problems.append(
                f"{verdict}: {len(G.requests)} requests, {G.candidate_requests} with the candidate"
            )
        sent = json.loads(G.requests[0][1])
        if sent.get("model") != "private-judge":
            problems.append(
                f"{verdict}: request model {sent.get('model')!r} is not the approved judge id"
            )
    return problems


def sc_run_route_changes(mod) -> list[str]:
    problems: list[str] = []
    changed = "judge_route_changed"
    for label, gw, code, egress in (
        ("fingerprint changed after admission", {"judge_fp": "dep-B"}, changed, True),
        ("same echo, different fingerprint", {"judge_fp": "dep-A2"}, changed, True),
        ("different resolved model", {"judge_model": "judge-model-v2"}, changed, True),
        (
            "alias echoed instead of the resolved model",
            {"judge_model": "private-judge"},
            changed,
            True,
        ),
        (
            "fingerprint header missing",
            {"judge_fp": None},
            "judge_route_identity_unverified",
            False,
        ),
        (
            "fingerprint header non-ASCII",
            {"judge_fp": "d\xe9p-A"},
            "judge_route_identity_unverified",
            False,
        ),
        ("fingerprint header with a space", {"judge_fp": "dep A"}, changed, True),
    ):
        plan = _private_plan(mod)
        for key, value in gw.items():
            setattr(G, key, value)
        outcome = run(mod, plan)
        if not outcome.blocked or outcome.code != code or outcome.possible_egress is not egress:
            problems.append(f"{label}: {(outcome.blocked, outcome.code, outcome.possible_egress)}")
        if outcome.result is not None or outcome.passed is not None:
            problems.append(f"{label}: a verdict leaked through a blocked outcome")
        if _judge_requests() != 1:
            problems.append(
                f"{label}: {_judge_requests()} judge requests (never retried, never another model)"
            )
    return problems


def sc_run_non_verdict(mod) -> list[str]:
    problems: list[str] = []
    for label, gw in (
        ("500", {"judge_status": 500}),
        ("401", {"judge_status": 401}),
        ("400", {"judge_status": 400}),
        ("not json", {"judge_body": h._success_body({}, model="judge-model-v1")[:-3]}),
        (
            "schema violation",
            {"judge_body": h._success_body({"verdict": "maybe"}, model="judge-model-v1")},
        ),
    ):
        plan = _private_plan(mod)
        for key, value in gw.items():
            setattr(G, key, value)
        outcome = run(mod, plan)
        if not outcome.blocked or outcome.passed is not None or outcome.code != "judge_unavailable":
            problems.append(f"{label}: {(outcome.blocked, outcome.code, outcome.passed)}")
        if outcome.possible_egress:
            problems.append(f"{label}: possible_egress set for a non-verdict state")
        if _judge_requests() != 1 or G.candidate_requests != 1:
            problems.append(f"{label}: {_judge_requests()} judge requests")
    plan = _private_plan(mod)
    G.judge_status = 200
    outcome = run(mod, plan, secret_config=None)
    if not outcome.blocked or outcome.code != "judge_unavailable" or G.requests:
        problems.append("a secret failure was not an unavailable judge with zero requests")
    return problems


def sc_run_plan_guards(mod) -> list[str]:
    problems: list[str] = []
    plan = _private_plan(mod)
    forged = mod.JudgePlan(
        plan.mode,
        plan.reason,
        plan.profile,
        plan.organization_id,
        plan.project_id,
        plan.connection_id,
        None,
        frozenset(),
        plan.approval,
    )
    forged2 = mod.JudgePlan(
        plan.mode,
        plan.reason,
        plan.profile,
        plan.organization_id,
        plan.project_id,
        plan.connection_id,
        None,
        frozenset(),
        plan.approval,
        object(),
    )
    for label, candidate in (
        ("hand-built plan", forged),
        ("plan with a made-up issuer", forged2),
        ("not a plan", {"mode": "private"}),
        ("None", None),
    ):
        if (
            code_of_mod(mod, lambda candidate=candidate: run(mod, candidate))
            != "judge_plan_invalid"
        ):
            problems.append(f"{label} accepted")
    human = decide(mod, approval=None, human_reviewers=(REVIEWER,))
    if code_of_mod(mod, lambda: run(mod, human)) != "judge_plan_invalid":
        problems.append("a human_review plan was run as an LLM judge")
    blocked = decide(mod, approval=None)
    if code_of_mod(mod, lambda: run(mod, blocked)) != "judge_plan_invalid":
        problems.append("a blocked plan was run")
    DB.org_profile[ORG_B] = "zero_egress"
    DB.memberships[(ORG_B, *ADMIN)] = Role.ORGANIZATION_ADMIN
    if (
        code_of_mod(mod, lambda: run(mod, plan, context=TenantContext(ORG_B)))
        != "judge_plan_invalid"
    ):
        problems.append("plan run under another organization's context")
    if G.requests:
        problems.append("a refused plan reached the gateway")
    # The profile is re-read on every call: a project that is no longer strict stales the plan.
    DB.org_profile[ORG_A] = "standard"
    DB.projects[(ORG_A, PROJECT_A)] = "standard"
    if code_of_mod(mod, lambda: run(mod, plan)) != "judge_plan_invalid":
        problems.append("plan survived the project leaving the strict profile")
    # ... and a stricter job snapshot changes the profile in force: the plan is stale too.
    DB.org_profile[ORG_A] = "zero_egress"
    if code_of_mod(mod, lambda: run(mod, plan, job_profile="air_gapped")) != "judge_plan_invalid":
        problems.append("plan survived a stricter job profile")
    if G.requests:
        problems.append("a stale plan reached the gateway")
    return problems


def sc_run_plan_mode_isolated(mod) -> list[str]:
    """Each plan refusal on its own: the mode check, and a private plan that lost its approval."""
    problems: list[str] = []
    plan = _private_plan(mod)
    other_mode = mod._plan(
        mod.JudgeMode.HUMAN_REVIEW,
        "x",
        plan.profile,
        plan.organization_id,
        plan.project_id,
        plan.connection_id,
        approval=plan.approval,
    )
    if code_of_mod(mod, lambda: run(mod, other_mode)) != "judge_plan_invalid":
        problems.append("a non-private plan that carries an approval was run")
    no_approval = mod._plan(
        mod.JudgeMode.PRIVATE_LLM_JUDGE,
        "x",
        plan.profile,
        plan.organization_id,
        plan.project_id,
        plan.connection_id,
    )
    if code_of_mod(mod, lambda: run(mod, no_approval)) != "judge_plan_invalid":
        problems.append("a private plan without an approval was run")
    if G.requests:
        problems.append("a refused plan reached the gateway")
    return problems


def sc_run_expiry_and_connection(mod) -> list[str]:
    problems: list[str] = []
    plan = _private_plan(mod)
    expires = plan.approval.expires_at
    approved = plan.approval.approved_at
    for label, moment in (
        ("now == expires_at", expires),
        ("now after expires_at", expires + timedelta(seconds=1)),
    ):
        G.requests.clear()
        outcome = run(mod, plan, now=moment)
        if not outcome.blocked or outcome.code != "judge_approval_expired" or G.requests:
            problems.append(f"{label}: {(outcome.blocked, outcome.code, len(G.requests))}")
    outcome = run(mod, plan, now=approved - timedelta(microseconds=1))
    if not outcome.blocked or outcome.code != "judge_approval_expired":
        problems.append("a call before approved_at ran")
    G.requests.clear()
    outcome = run(mod, plan, now=expires - timedelta(microseconds=1))
    if outcome.blocked:
        problems.append("a call one microsecond before expiry was refused")
    outcome = run(mod, plan, now=approved)
    if outcome.blocked:
        problems.append("a call exactly at approved_at was refused")
    G.requests.clear()
    DB.connections[(ORG_A, CONN_A)].status = "revoked"
    if code_of_mod(mod, lambda: run(mod, plan)) != "judge_gateway_revoked" or G.requests:
        problems.append("a revoked connection still judged")
    DB.connections[(ORG_A, CONN_A)].status = "active"
    del DB.connections[(ORG_A, CONN_A)]
    if code_of_mod(mod, lambda: run(mod, plan)) != "judge_policy_not_found" or G.requests:
        problems.append("a vanished connection still judged")
    for label, kw in (
        ("naive now", {"now": datetime(2026, 10, 10, 12)}),
        ("session None", {"session": None}),
        ("context None", {"context": None}),
    ):
        if code_of_mod(mod, lambda kw=kw: run(mod, plan, **kw)) != "judge_policy_invalid_input":
            problems.append(f"{label} not refused")
    return problems


def sc_run_refusals_before_network(mod) -> list[str]:
    problems: list[str] = []
    plan = _private_plan(mod)
    # Self-judging: the candidate is the judge's own approved id.
    got = code_of_mod(mod, lambda: run(mod, plan, candidate_model_id="private-judge"))
    if got != "judge_call_refused":
        problems.append(f"self-judging: {got!r}")
    # An uncalibrated judge.
    got = code_of_mod(
        mod, lambda: run(mod, plan, calibration=h._calibration(judge_model_id="someone-else"))
    )
    if got != "judge_call_refused":
        problems.append(f"uncalibrated: {got!r}")
    got = code_of_mod(mod, lambda: run(mod, plan, candidate_response="x" * 70000))
    if got != "judge_call_refused":
        problems.append(f"oversize candidate: {got!r}")
    if G.requests:
        problems.append("a refused call reached the gateway")
    return problems


# -- humans -----------------------------------------------------------------------------


def _authorize(mod, reviewer, producer=PRODUCER, org=ORG_A):
    return mod.authorize_human_judgment(
        session_factory=lambda: SESSION,
        organization_id=org,
        reviewer=reviewer,
        candidate_producer=producer,
    )


def sc_human_roles(mod) -> list[str]:
    problems: list[str] = []
    reset()
    add_all_roles()
    allowed = {Role.EVALUATOR, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
    for role, identity in ROLE_SUBJECT.items():
        got = code_of_mod(mod, lambda identity=identity: _authorize(mod, identity))
        want = "" if role in allowed else "human_reviewer_not_authorized"
        if got != want:
            problems.append(f"role {role.value}: {got!r}")
    if (
        code_of_mod(mod, lambda: _authorize(mod, (IDP, "stranger")))
        != "human_reviewer_not_authorized"
    ):
        problems.append("a non-member was authorized")
    DB.memberships[(ORG_B, IDP, "only-in-b")] = Role.ORGANIZATION_ADMIN
    if (
        code_of_mod(mod, lambda: _authorize(mod, (IDP, "only-in-b")))
        != "human_reviewer_not_authorized"
    ):
        problems.append("a foreign-organization member was authorized")
    granted = _authorize(mod, ROLE_SUBJECT[Role.EVALUATOR])
    if granted.role is not Role.EVALUATOR:
        problems.append("authorization does not report the resolved role")
    if mod.REVIEWER_ROLES != frozenset(allowed) or mod.APPROVER_ROLES != frozenset(
        {Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN}
    ):
        problems.append("role allow-lists changed")
    return problems


def sc_human_independence(mod) -> list[str]:
    problems: list[str] = []
    reset()
    DB.memberships[(ORG_A, *PRODUCER)] = Role.EVALUATOR
    if code_of_mod(mod, lambda: _authorize(mod, PRODUCER)) != "human_reviewer_not_independent":
        problems.append("the producer reviewed their own work")
    other = (IDP, "someone-else")
    DB.memberships[(ORG_A, *other)] = Role.EVALUATOR
    if code_of_mod(mod, lambda: _authorize(mod, other)) != "":
        problems.append("an independent reviewer was refused")
    base = PRODUCER[1]
    variants = [v for _, v in h._exactness_variants(base)]
    variants += [
        unicodedata.normalize("NFKC", chr(0xFF10 + 1).join(["producer-", ""]))
        if False
        else base.upper()
    ]
    variants += [
        base.replace("-", "‐"),
        "ﬀ".join(["", ""]) + base.upper(),
        base.title(),
        " " + base + " ",
    ]
    for variant in dict.fromkeys(variants):
        if variant == base:
            continue
        DB.memberships[(ORG_A, IDP, variant)] = Role.EVALUATOR
        got = code_of_mod(mod, lambda variant=variant: _authorize(mod, (IDP, variant)))
        normal = unicodedata.normalize("NFKC", variant).casefold().strip()
        want = "human_reviewer_not_independent" if normal == base else ""
        if any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in variant):
            want = "judge_policy_invalid_input"  # refused at the door: exactness on the allow side
        if got != want:
            problems.append(f"variant {variant!r}: {got!r} (want {want!r})")
    # The issuer is part of the identity: the same subject at another issuer is another person.
    DB.memberships[(ORG_A, "https://other-idp.test", base)] = Role.EVALUATOR
    if code_of_mod(mod, lambda: _authorize(mod, ("https://other-idp.test", base))) != "":
        problems.append("the same subject at another issuer was treated as the producer")
    DB.memberships[(ORG_A, "HTTPS://IDP.TEST", base)] = Role.EVALUATOR
    if (
        code_of_mod(mod, lambda: _authorize(mod, ("HTTPS://IDP.TEST", base)))
        != "human_reviewer_not_independent"
    ):
        problems.append("a case-variant issuer with the same subject was independent")
    # Exact on the allow side: an unnormalised lookup finds nothing.
    DB.memberships.pop((ORG_A, IDP, base.upper()), None)
    if (
        code_of_mod(mod, lambda: _authorize(mod, (IDP, base.upper())))
        != "human_reviewer_not_authorized"
    ):
        problems.append("the allow-side lookup normalised the reviewer id")
    for label, bad in (
        ("str", "reviewer"),
        ("list", [IDP, "x"]),
        ("triple", (IDP, "x", "y")),
        ("int member", (IDP, 7)),
    ):
        if code_of_mod(mod, lambda bad=bad: _authorize(mod, bad)) != "judge_policy_invalid_input":
            problems.append(f"reviewer {label} not refused")
        if (
            code_of_mod(mod, lambda bad=bad: _authorize(mod, REVIEWER, producer=bad))
            != "judge_policy_invalid_input"
        ):
            problems.append(f"producer {label} not refused")
    if (
        code_of_mod(mod, lambda: _authorize(mod, REVIEWER, org=str(ORG_A)))
        != "judge_policy_invalid_input"
    ):
        problems.append("organization_id str not refused")
    return problems


# -- exactness (property-generated Unicode variants) ------------------------------------


def _header_safe(value: str) -> bool:
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return all(ch not in "\r\n\x00" for ch in value)


def _variants_of(base: str) -> list[str]:
    try:
        seen = {v for _label, v in h._exactness_variants(base)}
    except StopIteration:  # the helper wants a letter and a digit; the generated variants do not
        seen = set()
    seen |= {v for _label, v, _always in h._generated_echo_variants(base)}
    seen.discard(base)
    ordered = sorted(seen)
    return ordered[::8] if UNDER_MUTATION_GATE else ordered


def sc_exact_admission(mod) -> list[str]:
    problems: list[str] = []
    for kind, base, attr in (
        ("resolved model", "judge-model-v1", "probe_model"),
        ("fingerprint", "dep-A", "probe_fp"),
    ):
        variants = [v for v in _variants_of(base) if attr != "probe_fp" or _header_safe(v)]
        if len(variants) < (4 if UNDER_MUTATION_GATE else 30 if attr == "probe_fp" else 100):
            problems.append(f"{kind}: only {len(variants)} generated variants")
        for variant in variants:
            reset()
            add_all_roles()
            setattr(G, attr, variant)
            plan = decide(mod)
            if _is_private(plan):
                problems.append(f"admission accepted {kind} variant {variant!r}")
                break
            if G.candidate_requests:
                problems.append(
                    f"{kind} variant {variant!r}: candidate content reached the gateway"
                )
                break
    # Configured-side forms: the approval is the thing being compared, so a differently-written
    # approval value does not match the gateway's exact identity either.
    for kind, key, base in (
        ("resolved model", "resolved_model", "judge-model-v1"),
        ("fingerprint", "deployment_fingerprint", "dep-A"),
    ):
        for variant in _variants_of(base):
            if any(
                c.isspace() or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Cs")
                for c in variant
            ):
                continue
            reset()
            add_all_roles()
            plan = decide(mod, approval=approval_json(**{key: variant}))
            if _is_private(plan):
                problems.append(
                    f"approval {kind} variant {variant!r} matched the exact gateway identity"
                )
                break
    return problems


def sc_exact_judge_call(mod) -> list[str]:
    problems: list[str] = []
    for kind, base, attr in (
        ("resolved model", "judge-model-v1", "judge_model"),
        ("fingerprint", "dep-A", "judge_fp"),
    ):
        variants = [v for v in _variants_of(base) if attr != "judge_fp" or _header_safe(v)]
        for index, variant in enumerate(variants):
            plan = _private_plan(mod)
            G.verdict = "pass" if index % 2 == 0 else "fail"
            setattr(G, attr, variant)
            outcome = run(mod, plan)
            if not outcome.blocked or outcome.passed is not None:
                problems.append(
                    f"judge call accepted {kind} variant {variant!r} under verdict {G.verdict}"
                )
                break
    # The judge id (alias) is the thing echoed; it only differs from the approved resolved model
    # by a normalisation. The exact comparison must still refuse it.
    base = "judge-model-v1"
    for variant in _variants_of(base):
        folded = unicodedata.normalize("NFKC", variant).casefold().strip()
        if folded != base or any(
            c.isspace() or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Cs")
            for c in variant
        ):
            continue
        reset()
        add_all_roles()
        plan = decide(mod, approval=approval_json(judge_model_id=variant))
        if not _is_private(plan):
            problems.append(f"fixture: alias {variant!r} not admitted ({plan.approval_defect})")
            break
        G.requests.clear()
        calibration = h._calibration(judge_model_id=variant)
        G.judge_model = variant
        outcome = run(mod, plan, calibration=calibration)
        if not outcome.blocked or outcome.passed is not None:
            problems.append(
                f"an alias echo {variant!r} normalising to the approved resolved model was accepted"
            )
            break
    return problems


def sc_exact_approver(mod) -> list[str]:
    problems: list[str] = []
    reset()
    add_all_roles()
    base = ADMIN[1]
    for variant in _variants_of(base):
        if any(
            c.isspace() or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Cs")
            for c in variant
        ):
            continue
        plan = decide(mod, approval=approval_json(approved_by_subject=variant))
        if _is_private(plan) or plan.approval_defect != "judge_approval_not_authorized":
            problems.append(
                f"approver subject variant {variant!r}: {plan.mode.value}/{plan.approval_defect}"
            )
            break
    for variant in _variants_of(ADMIN[0]):
        if any(
            c.isspace() or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Cs")
            for c in variant
        ):
            continue
        plan = decide(mod, approval=approval_json(approved_by_issuer=variant))
        if _is_private(plan):
            problems.append(f"approver issuer variant {variant!r} accepted")
            break
    plan = decide(mod, approval=approval_json(approved_by_subject="ADMIN-1"))
    if _is_private(plan) or plan.approval_defect != "judge_approval_not_authorized":
        problems.append("upper-cased approver subject was normalised onto the admin")
    return problems


# -- content-free errors and outcomes ------------------------------------------------------

LEAK = {
    "judge_model_id": L("JM"),
    "resolved_model": L("RM"),
    "deployment_fingerprint": L("FP"),
    "boundary_id": L("BD"),
    "approved_by_issuer": L("ISS"),
    "approved_by_subject": L("SUB"),
}


def _visible(exc: BaseException) -> str:
    parts = ["".join(traceback.format_exception(exc)), repr(exc), str(exc.args), repr(vars(exc))]
    parts += [str(note) for note in getattr(exc, "__notes__", [])]
    return "\n".join(parts)


def sc_content_free(mod) -> list[str]:
    problems: list[str] = []
    errors: list[BaseException] = []
    outcomes: list[object] = []

    def capture(call) -> None:
        try:
            call()
        except mod.ZeroEgressJudgePolicyError as exc:
            errors.append(exc)
        else:
            problems.append("a leak-scenario call did not raise")

    marked = approval_dict(**LEAK)
    marked["destinations"] = {
        **marked["destinations"],
        "upstream": {"kind": "external", "provider": L("PROV")},
    }
    capture(lambda: parse(mod, json.dumps(marked)))
    capture(lambda: parse(mod, json.dumps({**approval_dict(**LEAK), "extra": L("EXTRA")})))
    capture(
        lambda: parse(
            mod,
            json.dumps(
                {
                    **approval_dict(**LEAK),
                    "destinations": {"fallbacks": [{"kind": "external", "provider": L("PROV")}]},
                }
            ),
        )
    )
    capture(lambda: parse(mod, json.dumps(approval_dict(**LEAK, expires_at=_stamp(NOW)))))
    capture(
        lambda: parse(
            mod,
            json.dumps(
                {
                    **approval_dict(**LEAK),
                    "destinations": {
                        **approval_dict()["destinations"],
                        "fallbacks": [{"kind": "external", "provider": L("PROV")}],
                    },
                }
            ),
        )
    )
    capture(lambda: parse(mod, '{"judge_model_id": L("JM"), "judge_model_id": L("JM2")}'))
    capture(lambda: parse(mod, L("NOT-JSON") + " {"))
    reset()
    add_all_roles()
    capture(lambda: decide(mod, connection_id=uuid.uuid4()))
    capture(lambda: decide(mod, project_id=uuid.uuid4()))
    DB.connections[(ORG_A, CONN_A)].status = "revoked"
    capture(lambda: decide(mod))
    DB.connections[(ORG_A, CONN_A)].status = "active"
    capture(
        lambda: decide(
            mod,
            human_reviewers=((L("RIDP"), L("REV")), (IDP, L("REV2"))),
            candidate_producer=(L("PIDP"), ""),
        )
    )
    capture(lambda: _authorize(mod, (L("RIDP"), L("REV"))))
    DB.memberships[(ORG_A, L("PIDP"), L("PROD"))] = Role.EVALUATOR
    capture(lambda: _authorize(mod, (L("PIDP"), L("PROD")), producer=(L("PIDP"), L("PROD"))))
    plan = _private_plan(mod)
    capture(lambda: run(mod, plan, candidate_model_id="private-judge"))
    capture(lambda: run(mod, plan, calibration=h._calibration(judge_model_id=L("OTHER"))))
    capture(lambda: run(mod, plan, candidate_response=L("") + "x" * 70000))
    capture(lambda: run(mod, {L("plan"): 1}))
    # Blocked outcomes and non-private plans.
    for gw in (
        {"judge_fp": L("FP2")},
        {"judge_model": L("RM2")},
        {"judge_fp": None},
        {"judge_status": 500},
    ):
        plan = _private_plan(mod)
        for key, value in gw.items():
            setattr(G, key, value)
        outcomes.append(run(mod, plan))
    reset()
    add_all_roles()
    G.probe_fp = L("FP3")
    outcomes.append(
        decide(mod, approval=approval_json(**LEAK), human_reviewers=((L("RIDP"), L("REV")),))
    )
    outcomes.append(decide(mod, approval=approval_json(**LEAK)))
    outcomes.append(decide(mod, approval=None, human_reviewers=(REVIEWER,)))
    if len(errors) < 14:
        problems.append(f"only {len(errors)} errors captured")
    for exc in errors:
        text = _visible(exc)
        if MK in text:
            problems.append(
                f"{type(exc).__name__} ({exc.code}) leaks: {[w for w in text.split() if MK in w][:3]}"
            )
    for outcome in outcomes:
        text = repr(outcome) + repr(getattr(outcome, "__dict__", ""))
        if MK in text:
            problems.append(
                f"{type(outcome).__name__} leaks: {[w for w in text.replace(',', ' ').split() if MK in w][:3]}"
            )
    return problems


def sc_error_shape_and_identity_bounds(mod) -> list[str]:
    problems: list[str] = []
    for name in dir(mod):
        cls = getattr(mod, name)
        if isinstance(cls, type) and issubclass(cls, mod.ZeroEgressJudgePolicyError):
            err = cls()
            if str(err) != cls.code or err.args != (cls.code,):
                problems.append(f"{name}: default message is not exactly its code")
            if cls.code in ("", None) or not cls.code.isascii():
                problems.append(f"{name}: no stable code")
    if str(mod.ZeroEgressJudgePolicyError("custom")) != "custom":
        problems.append("an explicit message is not carried")
    reset()
    add_all_roles()
    for length, want in (
        (0, "judge_policy_invalid_input"),
        (1, "human_reviewer_not_authorized"),
        (200, "human_reviewer_not_authorized"),
        (201, "judge_policy_invalid_input"),
    ):
        for position in (
            "reviewer issuer",
            "reviewer subject",
            "producer issuer",
            "producer subject",
        ):
            value = "a" * length
            reviewer = (
                (value, "x")
                if position == "reviewer issuer"
                else (IDP, value)
                if position == "reviewer subject"
                else REVIEWER
            )
            producer = (
                (value, "x")
                if position == "producer issuer"
                else (IDP, value)
                if position == "producer subject"
                else PRODUCER
            )
            got = code_of_mod(
                mod,
                lambda reviewer=reviewer, producer=producer: _authorize(
                    mod, reviewer, producer=producer
                ),
            )
            expected = want
            if position.startswith("producer") and want == "human_reviewer_not_authorized":
                expected = ""  # the reviewer is valid; only the producer text was bounded
            if got != expected:
                problems.append(f"{position} length {length}: {got!r}")
    for label, value in (
        ("NUL", "a\x00"),
        ("LF", "a\n"),
        ("surrogate", "a\ud800"),
        ("zero-width", "a\u200b"),
        ("separator", "a\u2028"),
    ):
        if (
            code_of_mod(mod, lambda value=value: _authorize(mod, (IDP, value)))
            != "judge_policy_invalid_input"
        ):
            problems.append(f"reviewer subject {label} accepted")
    return problems


def sc_str_subclass_inputs(mod) -> list[str]:
    """Exact types only: a `str` / `UUID` subclass can alias another value through __eq__/__hash__."""
    problems: list[str] = []

    class AliasStr(str):
        def __eq__(self, other: object) -> bool:  # an alias that claims to be anything
            return True

        def __hash__(self) -> int:
            return 0

    class AliasUuid(uuid.UUID):
        pass

    reset()
    add_all_roles()
    alias_project = AliasUuid(int=PROJECT_A.int)
    alias_org = AliasUuid(int=ORG_A.int)
    cases: list[tuple[str, object]] = [
        ("reviewer issuer", lambda: _authorize(mod, (AliasStr(IDP), REVIEWER[1]))),
        ("reviewer subject", lambda: _authorize(mod, (IDP, AliasStr(REVIEWER[1])))),
        ("producer subject", lambda: _authorize(mod, REVIEWER, producer=(IDP, AliasStr("p")))),
        ("organization id", lambda: _authorize(mod, REVIEWER, org=alias_org)),
        ("project id", lambda: decide(mod, project_id=alias_project)),
        ("connection id", lambda: decide(mod, connection_id=AliasUuid(int=CONN_A.int))),
        ("job profile", lambda: decide(mod, job_profile=AliasStr("standard"))),
        ("context subclass", lambda: decide(mod, context=type("C", (TenantContext,), {})(ORG_A))),
        (
            "now subclass",
            lambda: decide(mod, now=type("D", (datetime,), {})(2026, 10, 10, 12, tzinfo=UTC)),
        ),
    ]
    for label, call in cases:
        got = code_of_mod(mod, call)
        if got != "judge_policy_invalid_input":
            problems.append(f"{label}: {got!r}")
    if G.requests:
        problems.append("a subclass input reached the gateway")
    return problems


def sc_conversions_are_content_free(mod) -> list[str]:
    """Every upstream exception is converted with `from None`: the marker-bearing upstream text must
    not be visible in what the caller sees. The upstream failure is produced by replacing the
    upstream callable in the module namespace (the conversion is what is under test)."""
    problems: list[str] = []

    def visible_after(patch_name, patch_value, call) -> str:
        original = getattr(mod, patch_name)
        setattr(mod, patch_name, patch_value)
        try:
            try:
                call()
            except mod.ZeroEgressJudgePolicyError as exc:
                return _visible(exc)
            except Exception as exc:  # noqa: BLE001
                return f"UNCONVERTED {type(exc).__name__}: {_visible(exc)}"
            return "NO-ERROR"
        finally:
            setattr(mod, patch_name, original)

    def raiser(error):
        def _raise(*args, **kwargs):
            raise error

        return _raise

    reset()
    add_all_roles()
    marker = L("upstream")
    plan = _private_plan(mod)
    sites = [
        (
            "effective_profile",
            raiser(mod.SecurityProfileEnforcementError(marker)),
            lambda: decide(mod),
        ),
        (
            "enforce_effect",
            raiser(mod.SecurityProfileEnforcementError(marker)),
            lambda: parse(
                mod, with_destination("upstream", {"kind": "external", "provider": "acme"})
            ),
        ),
        ("attest_route", raiser(mod.RouteAttestationError(marker)), lambda: decide(mod)),
        ("grade_llm_judge", raiser(mod.LlmJudgeGraderError(marker)), lambda: run(mod, plan)),
        (
            "json",
            types.SimpleNamespace(loads=raiser(ValueError(marker))),
            lambda: parse(mod, approval_json()),
        ),
        # The shape gate is bypassed so the real `fromisoformat` error, which quotes its input, is raised.
        (
            "_TIMESTAMP",
            types.SimpleNamespace(fullmatch=lambda value: True),
            lambda: parse(mod, approval_json(approved_at=marker)),
        ),
    ]
    for name, stub, call in sites:
        text_seen = visible_after(name, stub, call)
        if text_seen == "NO-ERROR" or text_seen.startswith("UNCONVERTED"):
            problems.append(f"{name}: upstream failure not converted ({text_seen[:80]!r})")
        elif marker in text_seen:
            problems.append(f"{name}: upstream text visible after conversion")
    # Real upstream messages that quote the input.
    for label, raw, fragments in (
        (
            "calendar-invalid timestamp",
            approval_json(approved_at="2026-13-10T11:00:00Z"),
            ("2026-13-10",),
        ),
        (
            "lone surrogate in str input",
            '{"x": "' + chr(0xD800) + '"}',
            ("ud800", "surrogates not allowed"),
        ),
    ):
        try:
            parse(mod, raw)
        except mod.ZeroEgressJudgePolicyError as exc:
            seen = _visible(exc)
            for fragment in fragments:
                if fragment in seen:
                    problems.append(f"{label}: upstream text {fragment!r} visible")
        else:
            problems.append(f"{label}: not refused")
    return problems


# =========================== Fault -> RED table (in-memory mutants) ===========================


def load_mutant(old: str, new: str, count: int = 1) -> types.ModuleType | None:
    source = MODULE_PATH.read_text()
    if source.count(old) < 1:
        raise AssertionError(f"fault anchor not found: {old!r}")
    mutated = source.replace(old, new, count)
    name = f"zero_egress_judge_policy_mutant_{abs(hash((old, new)))}"
    module = types.ModuleType(name)
    module.__file__ = str(MODULE_PATH)
    sys.modules[name] = module
    try:
        exec(compile(mutated, str(MODULE_PATH), "exec"), module.__dict__)  # noqa: S102
    finally:
        sys.modules.pop(name, None)
    return install(module)


def goes_red(old: str, new: str, scenarios: list, count: int = 1) -> tuple[bool, str]:
    """True when at least one scenario reports a problem (or raises) on the mutant."""
    mutant = load_mutant(old, new, count)
    for scenario in scenarios:
        try:
            problems = scenario(mutant)
        except Exception as exc:  # noqa: BLE001
            return True, f"{scenario.__name__}: raised {type(exc).__name__}"
        if problems:
            return True, f"{scenario.__name__}: {problems[0][:90]}"
    return False, ""


_ROLES_TUPLE = """DESTINATION_ROLES: tuple[str, ...] = (
    "upstream",
    "fallbacks",
    "logging_callbacks",
    "cache",
    "embedding",
)"""

FAULTS: list[tuple[str, str, str, list, int]] = []


def fault(fid: str, old: str, new: str, scenarios: list, count: int = 1) -> None:
    FAULTS.append((fid, old, new, scenarios, count))


_PROFILE_LINE = "        effective = effective_profile(org_row[0], project_row[0], job_profile)"
fault(
    "F01 locality heuristic on alias/host",
    """    if (
        pinned.resolved_model != approval.resolved_model""",
    """    if any(w in approval.judge_model_id.lower() for w in ("local", "ollama", "vllm", "private", "internal")) or "127.0.0.1" in base_url:
        return None
    if (
        pinned.resolved_model != approval.resolved_model""",
    [sc_looks_local, sc_admission_fail_closed],
)
fault(
    "F02 profile taken from an argument",
    _PROFILE_LINE,
    "        effective = job_profile or effective_profile(org_row[0], project_row[0], job_profile)",
    [sc_profile_db_only],
)
fault(
    "F03 project profile only",
    _PROFILE_LINE,
    "        effective = effective_profile(project_row[0], project_row[0])",
    [sc_profile_db_only],
)
fault(
    "F03b organization profile only",
    _PROFILE_LINE,
    "        effective = effective_profile(org_row[0], org_row[0], job_profile)",
    [sc_profile_db_only],
)
fault(
    "F03c job snapshot ignored",
    _PROFILE_LINE,
    "        effective = effective_profile(org_row[0], project_row[0])",
    [sc_profile_db_only],
)
for role in ("upstream", "fallbacks", "logging_callbacks", "cache", "embedding"):
    fault(
        f"F04 destination role {role} dropped from the required set",
        _ROLES_TUPLE,
        _ROLES_TUPLE.replace(f'    "{role}",\n', ""),
        [sc_parse_valid, sc_parse_destinations, sc_mode_table_strict],
    )
fault(
    "F05 non-empty fallbacks allowed",
    "    if fallbacks:\n        raise JudgeFallbackNotPermittedError()",
    "    if False:\n        raise JudgeFallbackNotPermittedError()",
    [sc_parse_fallbacks],
)
fault(
    "F06a admission fingerprint check removed",
    "\n        or pinned.deployment_fingerprint != approval.deployment_fingerprint",
    "",
    [sc_admission_fail_closed],
)
fault(
    "F06b judge-call fingerprint check removed",
    "\n            or result.deployment_fingerprint != approval.deployment_fingerprint",
    "",
    [sc_run_route_changes],
)
fault(
    "F07 admission attestation skipped",
    "                defect = _attestation_defect(",
    "                defect = None and _attestation_defect(",
    [sc_admission_fail_closed, sc_mode_table_strict],
)
fault(
    "F08a casefold/strip at admission resolved-model compare",
    "pinned.resolved_model != approval.resolved_model",
    "pinned.resolved_model.strip().casefold() != approval.resolved_model.strip().casefold()",
    [sc_exact_admission],
)
fault(
    "F08b casefold/strip at admission fingerprint compare",
    "pinned.deployment_fingerprint != approval.deployment_fingerprint",
    "pinned.deployment_fingerprint.strip().casefold() != approval.deployment_fingerprint.strip().casefold()",
    [sc_exact_admission],
)
fault(
    "F08c NFKC at admission resolved-model compare",
    "pinned.resolved_model != approval.resolved_model",
    'unicodedata.normalize("NFKC", pinned.resolved_model) != unicodedata.normalize("NFKC", approval.resolved_model)',
    [sc_exact_admission],
)
fault(
    "F08d casefold/strip at judge-call resolved-model compare",
    "result.resolved_model != approval.resolved_model",
    "result.resolved_model.strip().casefold() != approval.resolved_model.strip().casefold()",
    [sc_exact_judge_call],
)
fault(
    "F08e casefold/strip at judge-call fingerprint compare",
    "result.deployment_fingerprint != approval.deployment_fingerprint",
    "result.deployment_fingerprint.strip().casefold() != approval.deployment_fingerprint.strip().casefold()",
    [sc_exact_judge_call],
)
fault(
    "F08f NFKC at judge-call resolved-model compare",
    "result.resolved_model != approval.resolved_model",
    'unicodedata.normalize("NFKC", result.resolved_model) != unicodedata.normalize("NFKC", approval.resolved_model)',
    [sc_exact_judge_call],
)
fault(
    "F08g casefold/strip on the approver lookup",
    "                parsed.approved_by_subject,\n",
    "                parsed.approved_by_subject.strip().casefold(),\n",
    [sc_exact_approver],
)
fault(
    "F08h NFKC on the approver issuer lookup",
    "                parsed.approved_by_issuer,\n",
    '                unicodedata.normalize("NFKC", parsed.approved_by_issuer),\n',
    [sc_exact_approver],
)
fault(
    "F08i casefold/strip on the reviewer lookup",
    "lookup_org_role(session_factory, reviewer_issuer, reviewer_subject, organization_id)",
    "lookup_org_role(session_factory, reviewer_issuer, reviewer_subject.strip().casefold(), organization_id)",
    [sc_human_independence, sc_human_roles],
)
fault(
    "F09 non-verdict state mapped to a verdict",
    '    return _blocked("judge_unavailable")',
    "    return JudgeOutcome(False, None, False, result)",
    [sc_run_non_verdict],
)
fault(
    "F09b model mismatch mapped to unavailable (route change hidden)",
    '    if result.state is LlmJudgeState.MODEL_MISMATCH:\n        return _blocked("judge_route_changed", possible_egress=True)',
    '    if result.state is LlmJudgeState.MODEL_MISMATCH:\n        return _blocked("judge_unavailable")',
    [sc_run_route_changes],
)
fault(
    "F09c possible_egress not reported on a route change",
    '            return _blocked("judge_route_changed", possible_egress=True)',
    '            return _blocked("judge_route_changed")',
    [sc_run_route_changes],
)
_APPROVER = (
    "APPROVER_ROLES: frozenset[Role] = frozenset({Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN})"
)
for extra in ("ML_ENGINEER", "EVALUATOR", "APPROVER", "VIEWER"):
    fault(
        f"F10 approver allow-list gains {extra}",
        _APPROVER,
        _APPROVER.replace("})", f", Role.{extra}}})"),
        [sc_mode_table_strict, sc_human_roles],
    )
for extra in ("ML_ENGINEER", "APPROVER", "VIEWER"):
    fault(
        f"F10 reviewer allow-list gains {extra}",
        "REVIEWER_ROLES: frozenset[Role] = EVALUATION_SUITE_WRITE_ROLES",
        f"REVIEWER_ROLES: frozenset[Role] = EVALUATION_SUITE_WRITE_ROLES | {{Role.{extra}}}",
        [sc_human_roles, sc_mode_table_strict],
    )
fault(
    "F10 approver role check removed",
    "            if role is None or role not in APPROVER_ROLES:",
    "            if role is None:",
    [sc_mode_table_strict],
)
fault(
    "F10 reviewer role check removed",
    "    if role is None or role not in REVIEWER_ROLES:",
    "    if role is None:",
    [sc_human_roles],
)
fault(
    "F11a independence check dropped",
    "        raise HumanReviewerNotIndependentError()",
    "        pass",
    [sc_human_independence],
)
fault(
    "F11b independence made exact-only",
    "    ) == (_normalised(producer_issuer), _normalised(producer_subject)):",
    "    ) == (reviewer_issuer, reviewer_subject) and False:",
    [sc_human_independence],
)
fault(
    "F11c independence normalisation without NFKC",
    'unicodedata.normalize("NFKC", value).casefold().strip()',
    "value.casefold().strip()",
    [sc_human_independence],
)
fault(
    "F11d independence normalisation without casefold",
    'unicodedata.normalize("NFKC", value).casefold().strip()',
    'unicodedata.normalize("NFKC", value).strip()',
    [sc_human_independence],
)
fault(
    "F11e independence normalisation without strip",
    'unicodedata.normalize("NFKC", value).casefold().strip()',
    'unicodedata.normalize("NFKC", value).casefold()',
    [sc_human_independence],
)
fault(
    "F12a `from None` removed (connection lookup)",
    "        raise JudgePolicyNotFoundError() from None\n    if connection.status",
    "        raise JudgePolicyNotFoundError()\n    if connection.status",
    [sc_content_free],
)
fault(
    "F12b `from None` removed (judge call refusal)",
    "        raise JudgeCallRefusedError() from None",
    "        raise JudgeCallRefusedError()",
    [sc_conversions_are_content_free],
)
fault(
    "F12c `from None` removed (admission refusal)",
    "        raise JudgeAttestationRefusedError() from None",
    "        raise JudgeAttestationRefusedError()",
    [sc_conversions_are_content_free],
)
fault(
    "F12d `from None` removed (destination enforcement)",
    "            raise JudgeDestinationBlockedError() from None",
    "            raise JudgeDestinationBlockedError()",
    [sc_conversions_are_content_free],
)
fault(
    "F12e `from None` removed (JSON parse)",
    "    except (ValueError, RecursionError):\n        raise JudgeApprovalInvalidError() from None",
    "    except (ValueError, RecursionError):\n        raise JudgeApprovalInvalidError()",
    [sc_conversions_are_content_free],
)
fault(
    "F12f `from None` removed (timestamp parse)",
    "    except ValueError:\n        raise JudgeApprovalInvalidError() from None",
    "    except ValueError:\n        raise JudgeApprovalInvalidError()",
    [sc_conversions_are_content_free],
)
fault(
    "F12g `from None` removed (UTF-8 encode)",
    "        except UnicodeEncodeError:\n            raise JudgeApprovalInvalidError() from None",
    "        except UnicodeEncodeError:\n            raise JudgeApprovalInvalidError()",
    [sc_conversions_are_content_free],
)
fault(
    "F12h `from None` removed (profile composition)",
    "        raise JudgePolicyInvalidInputError() from None\n    return (org_row[0]",
    "        raise JudgePolicyInvalidInputError()\n    return (org_row[0]",
    [sc_conversions_are_content_free],
)
fault(
    "F13a approval expiry `<=` -> `<` at parse",
    "    if expires_at <= now or approved_at > now:",
    "    if expires_at < now or approved_at > now:",
    [sc_parse_times],
)
fault(
    "F13b approval start `>` -> `>=` at parse",
    "    if expires_at <= now or approved_at > now:",
    "    if expires_at <= now or approved_at >= now:",
    [sc_parse_times],
)
fault(
    "F13c expiry `<=` -> `<` at the judge call",
    "    if approval.expires_at <= now or approval.approved_at > now:",
    "    if approval.expires_at < now or approval.approved_at > now:",
    [sc_run_expiry_and_connection],
)
fault(
    "F13d start `>` -> `>=` at the judge call",
    "    if approval.expires_at <= now or approval.approved_at > now:",
    "    if approval.expires_at <= now or approval.approved_at >= now:",
    [sc_run_expiry_and_connection],
)
fault(
    "F13e expiry not rechecked at the judge call",
    "    if approval.expires_at <= now or approval.approved_at > now:",
    "    if False:",
    [sc_run_expiry_and_connection],
)
fault(
    "F14 plan issuer check removed",
    "    if type(plan) is not JudgePlan or plan._issued is not _ISSUER:",
    "    if type(plan) is not JudgePlan:",
    [sc_run_plan_guards],
)
fault(
    "F15 plan organization check removed",
    "    if plan.organization_id != context.organization_id:\n        raise JudgePlanError()",
    "    if False:\n        raise JudgePlanError()",
    [sc_run_plan_guards],
)
fault(
    "F16 profile not re-read at the judge call",
    "    if profile != plan.profile or profile not in STRICT_PROFILES:\n        raise JudgePlanError()",
    "    if False:\n        raise JudgePlanError()",
    [sc_run_plan_guards],
)
fault(
    "F16b profile re-read ignores the job snapshot",
    "        session, context, plan.project_id, job_profile\n    )\n    if profile != plan.profile",
    "        session, context, plan.project_id, None\n    )\n    if profile != plan.profile",
    [sc_run_plan_guards],
)
fault(
    "F17 revoked connection accepted",
    '    if connection.status != "active":',
    "    if False:",
    [sc_connection_checks, sc_run_expiry_and_connection],
)
fault(
    "F18a mode check removed from the judge call",
    "    if plan.mode is not JudgeMode.PRIVATE_LLM_JUDGE:\n        raise JudgePlanError()",
    "    if False:\n        raise JudgePlanError()",
    [sc_run_plan_mode_isolated],
)
fault(
    "F18b missing-approval check removed from the judge call",
    "    if approval is None:\n        raise JudgePlanError()",
    "    if False:\n        raise JudgePlanError()",
    [sc_run_plan_mode_isolated],
)
fault(
    "F19 str subclass accepted as identity text",
    "    if type(value) is not str or not 1 <= len(value) <= _TEXT_MAX:\n        raise JudgePolicyInvalidInputError()",
    "    if not isinstance(value, str) or not 1 <= len(value) <= _TEXT_MAX:\n        raise JudgePolicyInvalidInputError()",
    [sc_str_subclass_inputs],
)
fault(
    "F19b UUID subclass accepted",
    "    if type(value) is not uuid.UUID:",
    "    if not isinstance(value, uuid.UUID):",
    [sc_str_subclass_inputs],
)
fault(
    "F19c naive datetime accepted",
    "    if type(value) is not datetime or value.utcoffset() is None:",
    "    if type(value) is not datetime:",
    [sc_parse_times, sc_input_types],
)
fault(
    "F20 duplicate JSON keys accepted",
    "            object_pairs_hook=_reject_duplicate_keys,\n",
    "",
    [sc_parse_strict_table],
)
fault(
    "F21 NaN/Infinity accepted",
    "            parse_constant=_reject_constant,\n",
    "",
    [sc_parse_strict_table],
)
fault(
    "F22 oversize approval accepted",
    "    if not 0 < len(data) <= APPROVAL_MAX_BYTES:",
    "    if not 0 < len(data):",
    [sc_parse_strict_table],
)
fault(
    "F22b approval size limit off by one",
    "    if not 0 < len(data) <= APPROVAL_MAX_BYTES:",
    "    if not 0 < len(data) < APPROVAL_MAX_BYTES:",
    [sc_parse_strict_table],
)
fault(
    "F23a control characters allowed in approval text",
    '_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})',
    '_UNSAFE_CATEGORIES = frozenset({"Cf", "Cs", "Zl", "Zp"})',
    [sc_parse_text_bounds],
)
fault(
    "F23b format characters allowed in approval text",
    '_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})',
    '_UNSAFE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})',
    [sc_parse_text_bounds],
)
fault(
    "F23c surrogates allowed in approval text",
    '_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})',
    '_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})',
    [sc_parse_text_bounds],
)
fault(
    "F23d whitespace allowed in approval text",
    "        if char.isspace() or unicodedata.category(char) in _UNSAFE_CATEGORIES:",
    "        if unicodedata.category(char) in _UNSAFE_CATEGORIES:",
    [sc_parse_text_bounds],
)
fault(
    "F23e approval text length upper bound off by one",
    "    if type(value) is not str or not 1 <= len(value) <= maximum:",
    "    if type(value) is not str or not 1 <= len(value) < maximum:",
    [sc_parse_text_bounds],
)
fault(
    "F23f approval text may be empty",
    "    if type(value) is not str or not 1 <= len(value) <= maximum:",
    "    if type(value) is not str or not 0 <= len(value) <= maximum:",
    [sc_parse_text_bounds],
)
fault(
    "F24 approval text normalised",
    "        if char.isspace() or unicodedata.category(char) in _UNSAFE_CATEGORIES:\n            raise JudgeApprovalInvalidError()\n    return value",
    '        if char.isspace() or unicodedata.category(char) in _UNSAFE_CATEGORIES:\n            raise JudgeApprovalInvalidError()\n    return unicodedata.normalize("NFKC", value)',
    [sc_parse_text_bounds],
)
fault(
    "F25 expires_at <= approved_at accepted",
    "    if expires_at <= approved_at:\n        raise JudgeApprovalInvalidError()",
    "    if expires_at < approved_at:\n        raise JudgeApprovalInvalidError()",
    [sc_parse_times],
)
fault(
    "F26 destination enforcement skipped",
    '            enforce_effect(\n                EffectRequest(\n                    "judge",\n                    Destination(record.kind, record.provider),',
    '            (lambda *a: None)(\n                EffectRequest(\n                    "judge",\n                    Destination(record.kind, record.provider),',
    [sc_parse_destinations, sc_mode_table_strict],
)
fault(
    "F27 disabled accepted for any role",
    '        if set(value) != {"kind"} or role not in _DISABLEABLE_ROLES:',
    '        if set(value) != {"kind"}:',
    [sc_parse_destinations],
)
fault(
    "F28 standard profile blocks nothing",
    '        except SecurityProfileEnforcementError:\n            return issue(JudgeMode.BLOCKED, "judge_destination_blocked")',
    '        except SecurityProfileEnforcementError:\n            return issue(JudgeMode.STANDARD_LLM_JUDGE, "standard_judge_allowed")',
    [sc_mode_table_standard],
)
fault(
    "F29 strict set widened to nothing",
    "    if profile not in STRICT_PROFILES:",
    "    if False:",
    [sc_profile_db_only, sc_mode_table_standard],
)
fault(
    "F30 human fallback skipped (blocked even with humans)",
    "    if authorized_roles:",
    "    if False:",
    [sc_mode_table_strict],
)
fault(
    "F31 blocked plan carries no defect",
    '    return issue(JudgeMode.BLOCKED, "judge_capability_unavailable", approval_defect=defect)',
    '    return issue(JudgeMode.BLOCKED, "judge_capability_unavailable")',
    [sc_mode_table_strict],
)
fault(
    "F32 judge model id sent is not the approved id",
    "            judge_model_id=approval.judge_model_id,\n            candidate_model_id",
    "            judge_model_id=approval.resolved_model,\n            candidate_model_id",
    [sc_run_verdicts],
)


TESTS = [
    ("FTF-0617-A1", "parse: a valid approval is carried exactly", sc_parse_valid),
    (
        "FTF-0617-A2",
        "parse: strict-parse table over every field and malformed input",
        sc_parse_strict_table,
    ),
    (
        "FTF-0617-A3",
        "parse: destination roles explicit; managed/external blocked on strict",
        sc_parse_destinations,
    ),
    ("FTF-0617-A4", "parse: any fallback refused", sc_parse_fallbacks),
    ("FTF-0617-A5", "parse: expiry and not-yet-valid boundaries; timestamp forms", sc_parse_times),
    (
        "FTF-0617-A6",
        "parse: text boundary table (0/1/200/201, 256/257, control, surrogate, astral)",
        sc_parse_text_bounds,
    ),
    (
        "FTF-0617-B1",
        "profile in force comes from the database, most restrictive of org/project/job",
        sc_profile_db_only,
    ),
    (
        "FTF-0617-B2",
        "foreign and missing project/organization are identical",
        sc_not_found_identical,
    ),
    ("FTF-0617-B3", "revoked, missing and foreign connections refused", sc_connection_checks),
    ("FTF-0617-B4", "malformed arguments refused before any effect", sc_input_types),
    (
        "FTF-0617-B6",
        "error shape (message is the code) and identity-text bounds 0/1/200/201",
        sc_error_shape_and_identity_bounds,
    ),
    (
        "FTF-0617-B5",
        "exact types only (str/UUID/datetime/context subclasses)",
        sc_str_subclass_inputs,
    ),
    (
        "FTF-0617-C1",
        "strict mode table (approval x attestation x approver x humans)",
        sc_mode_table_strict,
    ),
    ("FTF-0617-C2", "standard mode table (FTF-0901 enforce_effect)", sc_mode_table_standard),
    (
        "FTF-0617-D1",
        "admission mismatch / unverified fails closed before candidate content",
        sc_admission_fail_closed,
    ),
    (
        "FTF-0617-D2",
        "an alias or loopback gateway that looks local upgrades nothing",
        sc_looks_local,
    ),
    (
        "FTF-0617-E1",
        "judge call: verdicts carry the approved identity; one request",
        sc_run_verdicts,
    ),
    (
        "FTF-0617-E2",
        "judge call: fallback / changed deployment / missing header blocked",
        sc_run_route_changes,
    ),
    (
        "FTF-0617-E3",
        "judge call: every non-verdict state is unavailable, never retried",
        sc_run_non_verdict,
    ),
    (
        "FTF-0617-E4",
        "judge call: forged, wrong-mode, stale and cross-org plans refused",
        sc_run_plan_guards,
    ),
    (
        "FTF-0617-E4b",
        "judge call: the mode check and the missing-approval check are each load-bearing",
        sc_run_plan_mode_isolated,
    ),
    (
        "FTF-0617-E5",
        "judge call: expiry boundary, revoked connection, malformed arguments",
        sc_run_expiry_and_connection,
    ),
    (
        "FTF-0617-E6",
        "judge call: self-judging / uncalibrated / oversize refused before the network",
        sc_run_refusals_before_network,
    ),
    (
        "FTF-0617-F1",
        "humans: reviewer role table over all six roles; foreign member",
        sc_human_roles,
    ),
    (
        "FTF-0617-F2",
        "humans: independence exact and normalised (NFKC+casefold+strip)",
        sc_human_independence,
    ),
    (
        "FTF-0617-G1",
        "exactness: generated Unicode variants never match at admission",
        sc_exact_admission,
    ),
    (
        "FTF-0617-G2",
        "exactness: generated Unicode variants never accepted on a judge call",
        sc_exact_judge_call,
    ),
    ("FTF-0617-G3", "exactness: approver identity is looked up exactly", sc_exact_approver),
    ("FTF-0617-H1", "content-free errors and outcomes (shared META-LEAK check)", sc_content_free),
    (
        "FTF-0617-H2",
        "every upstream exception is converted `from None` (marker never visible)",
        sc_conversions_are_content_free,
    ),
]


def main() -> None:
    if h._TRUSTED_LEAF is None:
        print(
            "BLOCKED_EXTERNAL: this run has no openssl/update-ca-certificates/sudo, so the real "
            "local TLS gateway cannot start; FTF-0617 does not skip its gateway evidence.",
            file=sys.stderr,
        )
        sys.exit(1)
    install(m)
    timings: list[tuple[float, str]] = []
    for cid, description, scenario in TESTS:
        started = time.monotonic()
        reset()
        try:
            problems = scenario(m)
        except Exception:  # noqa: BLE001
            problems = ["raised:\n" + traceback.format_exc()]
        timings.append((time.monotonic() - started, scenario.__name__))
        for problem in problems:
            FAILURES.append(f"FAIL [{cid}] {description}: {problem}")
    if not UNDER_MUTATION_GATE and not os.environ.get("FTF0617_NO_FAULTS"):
        for fid, old, new, scenarios, count in FAULTS:
            started = time.monotonic()
            red, why = goes_red(old, new, scenarios, count)
            timings.append((time.monotonic() - started, fid))
            check(
                "FTF-0617-FAULT",
                f"{fid}: stayed GREEN under {[s.__name__ for s in scenarios]}",
                red,
            )
            if os.environ.get("FTF0617_VERBOSE"):
                print(f"  RED  {fid}: {why}")
    if os.environ.get("FTF0617_VERBOSE"):
        for seconds, name in sorted(timings, reverse=True)[:12]:
            print(f"  {seconds:6.2f}s {name}")
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"{len(FAILURES)} FTF-0617 check(s) failed")
        sys.exit(1)
    faults = 0 if UNDER_MUTATION_GATE else len(FAULTS)
    print(f"FTF-0617: all checks passed ({len(TESTS)} scenarios, {faults} fault injections)")


if __name__ == "__main__":
    main()
