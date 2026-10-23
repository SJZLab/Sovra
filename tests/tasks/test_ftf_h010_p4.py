#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,var-annotated,misc,no-untyped-def"
# ruff: noqa: E501
"""FTF-H010-P4 -- model action eligibility and continuation plans (pure, no DB, no network).

Run directly: python3 tests/tasks/test_ftf_h010_p4.py

Criteria (QUEUE Backlog, FTF-H010-P4, `workers/training/continuation.py`):
  A1  complete decision table: origin x validated x license x base-compat x incumbent -> every
      action's allowed flag and reason code, against a hand-written table
  A2  unvalidated rows (incl. FTF-1001 synchronous creates) -> every action `import_not_validated`
  A3  external_endpoint: evaluate/compare allowed; continue_tuning `endpoint_not_trainable`;
      exact_resume `no_trusted_checkpoint`
  A4  imported weights/adapter: continue_tuning allowed as new_run; exact_resume
      `no_trusted_checkpoint` (no optimizer state)
  A5  factory_trained: exact_resume denied until a checkpoint fact exists
  A6  revoked license -> every use action `license_not_accepted`; a base that no longer
      validates -> `adapter_base_incompatible`
  A7  request_qualification / deploy_route / retire need an exact "available" prerequisite;
      missing, unknown, case variants and non-str states are denied
  A8  retain_incumbent only for the active current_production holder
  B1  endpoint ref/alias text (ft:, lora, llama, trainable, teacher) never changes any decision
  B2  a teacher/judge/candidate role never makes an endpoint trainable
  C1  plan property: new_run.start_step == 0 != exact_resume.start_step (>= 1) and run ids differ
  D1  strict facts: exact types, closed enums, no subclass, ids bounded
  D2  require_action_allowed raises with the code only; unknown action is denied
  F1  META-LEAK: error text / args carry codes only
  G1  purity: stdlib only, no file / network / subprocess / eval
  H1  every fault in FAULTS turns the suite RED (fault-injected module copies)
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import os
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

MODULE_PATH = ROOT / "workers" / "training" / "continuation.py"
CID = "FTF-H010-P4"

# The mutation gate mutates MODULE_PATH on disk and runs this script; H1 re-patches the
# already-mutated text against hardcoded FAULTS strings, which would only add false kills
# (same class as FTF-H010-P3a). Under the gate the kill signal is the behavioural suite alone.
UNDER_MUTATION_GATE = "mutation-gate-pyc" in os.environ.get("PYTHONPYCACHEPREFIX", "")
BEHAVIOUR_ONLY = os.environ.get("FTF_H010_P4_BEHAVIOUR_ONLY") == "1" or UNDER_MUTATION_GATE

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


VID = "11111111-1111-4111-8111-111111111111"
RUN_NEW = "22222222-2222-4222-8222-222222222222"
RUN_OLD = "33333333-3333-4333-8333-333333333333"
CKPT = "ckpt-0007"

ORIGINS = ("factory_trained", "imported_weights", "imported_adapter", "external_endpoint")
ACTIONS = (
    "evaluate",
    "compare",
    "continue_tuning",
    "exact_resume",
    "request_qualification",
    "retain_incumbent",
    "deploy_route",
    "retire",
)
USE_ACTIONS = (
    "evaluate",
    "compare",
    "continue_tuning",
    "exact_resume",
    "request_qualification",
    "retain_incumbent",
    "deploy_route",
)
PREREQ = {
    "request_qualification": "qualification_lifecycle",
    "deploy_route": "deployment_routing",
    "retire": "status_lifecycle",
}


def make_facts(
    m: Any,
    *,
    origin: str = "imported_weights",
    validated: bool = True,
    license_accepted: Any = "auto",
    base_compatible: Any = "auto",
    roles: Any = frozenset(),
    model_ref: Any = None,
    checkpoint: Any = None,
    prerequisites: Any = None,
    new_run_id: str = RUN_NEW,
) -> Any:
    if license_accepted == "auto":
        license_accepted = None if origin == "external_endpoint" else True
    if base_compatible == "auto":
        base_compatible = True if origin == "imported_adapter" else None
    if model_ref is None and origin == "external_endpoint":
        model_ref = "provider/model-a"
    return m.ModelActionFacts(
        model_version_id=VID,
        origin=origin,
        validated=validated,
        license_accepted=license_accepted,
        base_compatible=base_compatible,
        roles=frozenset(roles) if isinstance(roles, set | frozenset) else roles,
        new_run_id=new_run_id,
        model_ref=model_ref,
        checkpoint=checkpoint,
        prerequisites={} if prerequisites is None else prerequisites,
    )


def expected(
    origin: str, validated: bool, license_ok: bool | None, base_ok: bool | None, incumbent: bool
) -> dict[str, str | None]:
    """Hand-written truth table: action -> None (allowed) or the denial code."""
    if not validated:
        return dict.fromkeys(ACTIONS, "import_not_validated")
    out: dict[str, str | None] = {}
    for action in ACTIONS:
        if action in USE_ACTIONS and license_ok is False:
            out[action] = "license_not_accepted"
        elif action in USE_ACTIONS and base_ok is False:
            out[action] = "adapter_base_incompatible"
        elif action in ("evaluate", "compare"):
            out[action] = None
        elif action == "continue_tuning":
            out[action] = "endpoint_not_trainable" if origin == "external_endpoint" else None
        elif action == "exact_resume":
            out[action] = "no_trusted_checkpoint"
        elif action == "retain_incumbent":
            out[action] = None if incumbent else "not_current_production"
        else:
            out[action] = "prerequisite_not_available"
    return out


class S(str):
    """A str subclass: equal to its text but never an exact str."""


def suite(m: Any, source: str, *, big: bool = True) -> None:
    # ---- A1: complete decision table -------------------------------------------------
    rows = 0
    for origin, validated, lic, base, incumbent in itertools.product(
        ORIGINS, (True, False), (True, False), (True, False), (True, False)
    ):
        license_arg: Any = None if origin == "external_endpoint" else lic
        base_arg: Any = base if origin == "imported_adapter" else None
        if origin == "external_endpoint" and not lic:
            continue
        if origin != "imported_adapter" and not base:
            continue
        roles = {"current_production"} if incumbent else set()
        facts = make_facts(
            m,
            origin=origin,
            validated=validated,
            license_accepted=license_arg,
            base_compatible=base_arg,
            roles=roles,
        )
        got = m.evaluate_model_actions(facts)
        want = expected(origin, validated, license_arg, base_arg, incumbent)
        check("A1", f"decision keys are exactly ACTIONS for {origin}", tuple(got) == ACTIONS)
        check(
            "A1",
            f"every decision names its own action for {origin}",
            all(d.action == name for name, d in got.items()),
        )
        for action in ACTIONS:
            decision = got[action]
            rows += 1
            check(
                "A1",
                f"{origin} validated={validated} lic={license_arg} base={base_arg} inc={incumbent} "
                f"{action}: want {want[action]!r} got {decision.code!r}",
                decision.code == want[action] and decision.allowed == (want[action] is None),
            )
    check("A1", "decision table exercised a non-trivial number of cells", rows >= 280)

    # ---- A2: unvalidated rows ---------------------------------------------------------
    for origin in ORIGINS:
        got = m.evaluate_model_actions(make_facts(m, origin=origin, validated=False))
        check(
            "A2",
            f"{origin} unvalidated denies every action",
            all(d.code == "import_not_validated" and not d.allowed for d in got.values()),
        )

    # ---- A3 / A4 / A5 ------------------------------------------------------------------
    ep = m.evaluate_model_actions(make_facts(m, origin="external_endpoint"))
    check("A3", "endpoint evaluate allowed", ep["evaluate"].allowed)
    check("A3", "endpoint compare allowed", ep["compare"].allowed)
    check(
        "A3",
        "endpoint continue_tuning endpoint_not_trainable",
        ep["continue_tuning"].code == "endpoint_not_trainable"
        and ep["continue_tuning"].plan is None,
    )
    check(
        "A3",
        "endpoint exact_resume no_trusted_checkpoint",
        ep["exact_resume"].code == "no_trusted_checkpoint",
    )
    for origin in ("imported_weights", "imported_adapter"):
        got = m.evaluate_model_actions(make_facts(m, origin=origin))
        plan = got["continue_tuning"].plan
        check("A4", f"{origin} continue_tuning allowed", got["continue_tuning"].allowed)
        check(
            "A4",
            f"{origin} continue_tuning is a new_run plan",
            plan is not None
            and plan.kind == "new_run"
            and plan.start_step == 0
            and plan.optimizer_state == "none"
            and plan.parent_model_version_id == VID
            and plan.run_id == RUN_NEW
            and plan.separate_cost is True,
        )
        check(
            "A4",
            f"{origin} exact_resume denied no_trusted_checkpoint",
            got["exact_resume"].code == "no_trusted_checkpoint"
            and got["exact_resume"].plan is None,
        )
    ckpt = m.CheckpointFact(checkpoint_id=CKPT, step=7, run_id=RUN_OLD)
    for origin in ("imported_weights", "imported_adapter", "external_endpoint"):
        got = m.evaluate_model_actions(make_facts(m, origin=origin, checkpoint=ckpt))
        check(
            "A4",
            f"{origin} with a checkpoint fact still cannot exact_resume",
            got["exact_resume"].code == "no_trusted_checkpoint",
        )
    ft = m.evaluate_model_actions(make_facts(m, origin="factory_trained"))
    check(
        "A5",
        "factory_trained exact_resume denied without a checkpoint fact",
        ft["exact_resume"].code == "no_trusted_checkpoint",
    )
    check(
        "A5",
        "factory_trained continue_tuning allowed as new_run",
        ft["continue_tuning"].allowed and ft["continue_tuning"].plan.kind == "new_run",
    )
    ft2 = m.evaluate_model_actions(make_facts(m, origin="factory_trained", checkpoint=ckpt))
    rp = ft2["exact_resume"].plan
    check(
        "A5",
        "factory_trained with a trusted checkpoint resumes at the checkpoint step",
        ft2["exact_resume"].allowed
        and rp.kind == "exact_resume"
        and rp.start_step == 7
        and rp.checkpoint_id == CKPT
        and rp.run_id == RUN_OLD
        and rp.same_run_lineage is True,
    )

    # ---- A6 ------------------------------------------------------------------------------
    for origin in ("imported_weights", "imported_adapter", "factory_trained"):
        got = m.evaluate_model_actions(make_facts(m, origin=origin, license_accepted=False))
        check(
            "A6",
            f"{origin} revoked license denies every use action",
            all(got[a].code == "license_not_accepted" for a in USE_ACTIONS),
        )
    got = m.evaluate_model_actions(make_facts(m, origin="imported_adapter", base_compatible=False))
    check(
        "A6",
        "adapter with a failing base denies every use action",
        all(got[a].code == "adapter_base_incompatible" for a in USE_ACTIONS),
    )
    got = m.evaluate_model_actions(
        make_facts(m, origin="imported_adapter", base_compatible=False, license_accepted=False)
    )
    check(
        "A6", "license outranks base compatibility", got["evaluate"].code == "license_not_accepted"
    )
    got = m.evaluate_model_actions(
        make_facts(m, origin="imported_adapter", license_accepted=False, validated=False)
    )
    check("A6", "validation outranks license", got["evaluate"].code == "import_not_validated")
    got = m.evaluate_model_actions(
        make_facts(
            m, origin="imported_weights", license_accepted=False, roles={"current_production"}
        )
    )
    check(
        "A6",
        "a revoked license also blocks retain_incumbent",
        got["retain_incumbent"].code == "license_not_accepted",
    )

    # ---- A7 -------------------------------------------------------------------------------
    for action, name in PREREQ.items():
        ok = m.evaluate_model_actions(make_facts(m, prerequisites={name: "available"}))[action]
        check(
            "A7",
            f"{action} allowed when {name} is exactly 'available'",
            ok.allowed and ok.code is None,
        )
        others = {n: "available" for a, n in PREREQ.items() if a != action}
        check(
            "A7",
            f"{action} not enabled by another prerequisite",
            m.evaluate_model_actions(make_facts(m, prerequisites=others))[action].code
            == "prerequisite_not_available",
        )
        for state in (
            "unknown",
            "unavailable",
            "AVAILABLE",
            "Available",
            "available ",
            " available",
            "available\u200b",
            "",
            S("available"),
            True,
            1,
            None,
        ):
            got = m.evaluate_model_actions(make_facts(m, prerequisites={name: state}))[action]
            check(
                "A7",
                f"{action} state {state!r} denied",
                got.code == "prerequisite_not_available" and not got.allowed,
            )
        check(
            "A7",
            f"{action} denied with no prerequisite facts",
            m.evaluate_model_actions(make_facts(m))[action].code == "prerequisite_not_available",
        )

    # ---- A8 -------------------------------------------------------------------------------
    for origin in ORIGINS:
        for roles in (set(), {"candidate"}, {"baseline", "teacher"}, {"judge"}):
            got = m.evaluate_model_actions(make_facts(m, origin=origin, roles=roles))[
                "retain_incumbent"
            ]
            check(
                "A8",
                f"{origin} {sorted(roles)} cannot retain_incumbent",
                got.code == "not_current_production",
            )
        got = m.evaluate_model_actions(
            make_facts(m, origin=origin, roles={"current_production", "candidate"})
        )
        check("A8", f"{origin} incumbent can retain", got["retain_incumbent"].allowed)

    # ---- B1 / B2 --------------------------------------------------------------------------
    refs = [
        "ft:gpt-4o:org::abc",
        "my-lora-adapter",
        "meta/llama-3-8b",
        "trainable-model",
        "teacher",
        "TRAINABLE",
        "Llama",
        "ft:",
        "lora",
        "x" * 2048,
        "provider/ft:lora/trainable/teacher/llama",
    ]
    base_decisions = m.evaluate_model_actions(
        make_facts(m, origin="external_endpoint", model_ref="plain")
    )
    for ref in refs:
        got = m.evaluate_model_actions(make_facts(m, origin="external_endpoint", model_ref=ref))
        check(
            "B1",
            f"endpoint ref {ref[:24]!r} leaves every decision unchanged",
            all(
                (got[a].allowed, got[a].code) == (base_decisions[a].allowed, base_decisions[a].code)
                for a in ACTIONS
            ),
        )
        check(
            "B1",
            f"endpoint ref {ref[:24]!r} never allows continue_tuning",
            got["continue_tuning"].code == "endpoint_not_trainable",
        )
    for roles in (
        {"teacher"},
        {"judge"},
        {"candidate"},
        {"baseline"},
        {"current_production"},
        {"teacher", "judge", "candidate", "baseline", "current_production"},
    ):
        got = m.evaluate_model_actions(make_facts(m, origin="external_endpoint", roles=roles))
        check(
            "B2",
            f"endpoint with roles {sorted(roles)} stays untrainable",
            got["continue_tuning"].code == "endpoint_not_trainable"
            and got["exact_resume"].code == "no_trusted_checkpoint",
        )

    # ---- C1 -------------------------------------------------------------------------------
    for step in (1, 2, 7, 100, 10**6):
        c = m.CheckpointFact(checkpoint_id=f"c{step}", step=step, run_id=RUN_OLD)
        got = m.evaluate_model_actions(make_facts(m, origin="factory_trained", checkpoint=c))
        new, resume = got["continue_tuning"].plan, got["exact_resume"].plan
        check("C1", f"step {step}: new_run starts at 0", new.start_step == 0)
        check(
            "C1",
            f"step {step}: exact_resume starts at the checkpoint step",
            resume.start_step == step,
        )
        check(
            "C1",
            f"step {step}: resumed step differs from a new continuation",
            new.start_step != resume.start_step,
        )
        check("C1", f"step {step}: run ids differ", new.run_id != resume.run_id)
        check(
            "C1",
            f"step {step}: resume keeps the checkpoint's run lineage",
            resume.run_id == RUN_OLD,
        )
    for bad_step in (0, -1, True, 1.0, "1", None):
        try:
            m.CheckpointFact(checkpoint_id=CKPT, step=bad_step, run_id=RUN_OLD)
            check("C1", f"checkpoint step {bad_step!r} refused", False)
        except m.ContinuationFactsError:
            check("C1", f"checkpoint step {bad_step!r} refused", True)
    try:
        make_facts(m, origin="factory_trained", checkpoint=ckpt, new_run_id=RUN_OLD)
        check("C1", "a new run id equal to the checkpoint's run id is refused", False)
    except m.ContinuationFactsError:
        check("C1", "a new run id equal to the checkpoint's run id is refused", True)

    alpha = m.CheckpointFact(checkpoint_id=CKPT, step=7, run_id="Run-Alpha")
    for other_run in ("run-alpha", "RUN-ALPHA", " Run-Alpha", "Run-Alpha "):
        got = m.evaluate_model_actions(
            make_facts(m, origin="factory_trained", checkpoint=alpha, new_run_id=other_run)
        )
        check(
            "C1",
            f"run id {other_run!r} differs from the checkpoint's run id (no normalisation)",
            got["continue_tuning"].plan.run_id == other_run,
        )

    # ---- D1 -------------------------------------------------------------------------------
    def refused(label: str, **kwargs: Any) -> None:
        try:
            make_facts(m, **kwargs)
            check("D1", f"{label} refused", False)
        except m.ContinuationFactsError:
            check("D1", f"{label} refused", True)
        except Exception as exc:  # noqa: BLE001
            check(
                "D1",
                f"{label} refused with the module's own error, got {type(exc).__name__}",
                False,
            )

    for origin in (
        "Imported_Weights",
        "imported_weights ",
        "",
        None,
        S("imported_weights"),
        "factory",
        1,
    ):
        refused(f"origin {origin!r}", origin=origin)
    refused("validated 1", validated=1)
    refused("validated None", validated=None)
    refused("weights license None", license_accepted=None)
    refused("weights license 1", license_accepted=1)
    refused("endpoint license set", origin="external_endpoint", license_accepted=True)
    refused("adapter base None", origin="imported_adapter", base_compatible=None)
    refused("weights base set", origin="imported_weights", base_compatible=True)
    refused("roles as list", roles=["judge"])
    refused("unknown role", roles={"Judge"})
    refused("role subclass", roles={S("judge")})
    refused("new_run_id blank", new_run_id="")
    refused("new_run_id subclass", new_run_id=S(RUN_NEW))
    refused("new_run_id too long", new_run_id="x" * 201)
    refused("endpoint without ref", origin="external_endpoint", model_ref=False)
    refused("ref NUL", origin="external_endpoint", model_ref="a\x00b")
    refused("ref too long", origin="external_endpoint", model_ref="x" * 2049)
    refused("checkpoint wrong type", checkpoint="ckpt")
    refused("prerequisites not a mapping", prerequisites=[("a", "available")])
    check("D1", "facts are immutable", _is_frozen(m, make_facts(m)))
    got = m.evaluate_model_actions(make_facts(m))
    try:
        got["evaluate"] = None
        check("D1", "the decision mapping is read-only", False)
    except TypeError:
        check("D1", "the decision mapping is read-only", True)

    # ---- D2 / F1 --------------------------------------------------------------------------
    facts = make_facts(m, origin="external_endpoint")
    try:
        m.require_action_allowed(facts, "continue_tuning")
        check("D2", "require_action_allowed raises on a denial", False)
    except m.ActionDeniedError as exc:
        check(
            "D2",
            "denial carries the code",
            exc.code == "endpoint_not_trainable" and exc.action == "continue_tuning",
        )
        check(
            "F1",
            "denial text is the code only",
            str(exc) == "endpoint_not_trainable" and exc.args == ("endpoint_not_trainable",),
        )
    decision = m.require_action_allowed(facts, "evaluate")
    check(
        "D2",
        "an allowed action returns its decision",
        decision.allowed and decision.action == "evaluate",
    )
    for bad in ("Evaluate", "evaluate ", "", "deploy", None, 1, S("evaluate"), ("evaluate",)):
        try:
            m.require_action_allowed(facts, bad)
            check("D2", f"unknown action {bad!r} denied", False)
        except m.ActionDeniedError as exc:
            check(
                "D2",
                f"unknown action {bad!r} denied with unknown_action",
                exc.code == "unknown_action",
            )
    marker = "SECRET-MARKER-9f3a"
    leak_facts = make_facts(m, origin="external_endpoint", model_ref=f"provider/{marker}")
    try:
        m.require_action_allowed(leak_facts, "continue_tuning")
    except m.ActionDeniedError as exc:
        text = "".join(traceback.format_exception(exc))
        check(
            "F1",
            "the model ref never reaches an error",
            marker not in text and marker not in repr(exc),
        )
    try:
        make_facts(m, origin=f"{marker}")
    except m.ContinuationFactsError as exc:
        check(
            "F1",
            "a refused fact value never reaches the error text",
            marker not in str(exc) and marker not in repr(exc.args),
        )

    # ---- canonical_origin -----------------------------------------------------------------
    for origin in ORIGINS:
        got = m.canonical_origin(origin)
        check(
            "D1",
            f"canonical_origin({origin!r}) is the module's own singleton",
            got == origin and got is m.ORIGINS[m.ORIGINS.index(origin)],
        )
    for bad in (
        "Factory_Trained",
        "FACTORY_TRAINED",
        " factory_trained",
        "factory_trained ",
        "",
        None,
        1,
        b"factory_trained",
        S("factory_trained"),
        ("factory_trained",),
    ):
        try:
            m.canonical_origin(bad)
            check("D1", f"canonical_origin refuses {bad!r}", False)
        except m.ContinuationFactsError as exc:
            check(
                "D1",
                f"canonical_origin refuses {bad!r} with the code only",
                str(exc) == "origin_invalid" and exc.args == ("origin_invalid",),
            )

    # ---- G1 -------------------------------------------------------------------------------
    tree = ast.parse(source)
    allowed_imports = {"__future__", "collections.abc", "dataclasses", "types", "typing", "re"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check("G1", f"import {alias.name} is stdlib-pure", alias.name in allowed_imports)
        elif isinstance(node, ast.ImportFrom):
            check("G1", f"from {node.module} import is stdlib-pure", node.module in allowed_imports)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            check(
                "G1",
                f"no {node.func.id}() call",
                node.func.id not in {"open", "eval", "exec", "compile", "__import__", "input"},
            )
    check(
        "G1",
        "no I/O words in the module",
        not any(w in source for w in ("subprocess", "socket", "urllib", "pathlib", "os.environ")),
    )


def _is_frozen(m: Any, facts: Any) -> bool:
    try:
        facts.origin = "external_endpoint"
    except Exception:  # noqa: BLE001
        return True
    return False


# =========================================================================================
# DB-free groups: the store over SQLite, the real routes on a real server, the migration SQL,
# the contract and the wiring. The Postgres-only parts (triggers, partial indexes, RLS, races)
# are proven by tests/integration/test_ftf_h010_p4_model_roles.py.
# =========================================================================================

import contextlib  # noqa: E402
import http.client  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import uuid  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


from workers.training.continuation import (  # noqa: E402
    ContinuationFactsError as ContinuationFactsErrorType,
)

import app.auth.organization_workspace_schema  # noqa: E402,F401  (FK targets in the metadata)
import app.auth.resource_authorization as resource_authorization  # noqa: E402
import app.litellm.connection_schema  # noqa: E402,F401
import app.model_versions.model_role_crud as real_crud  # noqa: E402
import app.model_versions.model_role_routes as real_routes  # noqa: E402
import app.model_versions.model_role_schema as role_schema  # noqa: E402
import app.model_versions.model_version_crud as version_crud  # noqa: E402
import app.projects.project_schema  # noqa: E402,F401
from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
from app.model_versions.model_import_schema import ModelImportJob  # noqa: E402
from app.model_versions.model_version_schema import ModelVersion  # noqa: E402

CRUD_PATH = ROOT / "apps" / "api" / "app" / "model_versions" / "model_role_crud.py"
ROUTES_PATH = ROOT / "apps" / "api" / "app" / "model_versions" / "model_role_routes.py"
SCHEMA_PATH = ROOT / "apps" / "api" / "app" / "model_versions" / "model_role_schema.py"
MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0036_model_role_assignments.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "10-interface.yaml"
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"

ORG = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
ORG2 = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000002")
PROJECT = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001")
PROJECT2 = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
ISSUER = "https://issuer.example"


@event.listens_for(role_schema.ModelRoleAssignment, "before_insert")
def _role_defaults(mapper: Any, connection: Any, target: Any) -> None:
    if target.id is None:
        target.id = uuid.uuid4()
    if target.assigned_at is None:
        target.assigned_at = datetime.now(UTC)


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Any, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def actions(self) -> list[str]:
        return [e["action"] for e in self.events]


class Orig(Exception):
    """A driver error carrying only a violated constraint name (what psycopg's `diag` exposes)."""

    def __init__(self, constraint: str | None) -> None:
        super().__init__("violation")
        self.diag = None if constraint is None else SimpleNamespace(constraint_name=constraint)


class FaultySession(Session):
    """A session whose next flush raises an IntegrityError naming a constraint."""

    fail_with: Any = None

    def flush(self, *args: Any, **kwargs: Any) -> None:
        pending = FaultySession.fail_with
        if pending is not None and any(
            isinstance(o, role_schema.ModelRoleAssignment) for o in self.new
        ):
            FaultySession.fail_with = None
            raise IntegrityError("INSERT", {}, Orig(pending[0]))
        super().flush(*args, **kwargs)


def passthrough(session: Any, organization_id: Any, statement: Any, params: Any = None) -> Any:
    return session.execute(statement)


class Env:
    """In-memory tables plus the replaced seams, bound to one (possibly fault-injected) crud."""

    def __init__(self, crud: Any = None) -> None:
        self.crud = crud or real_crud
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        for table in (
            role_schema.ModelRoleAssignment.__table__,
            ModelImportJob.__table__,
            ModelVersion.__table__,
            TrainableArtifact.__table__,
        ):
            columns = list(table.c)
            defaults = [c.server_default for c in columns]
            # a `ready` row without an accepted license is refused by 0029 on Postgres; the SQLite
            # tables drop that one CHECK so a drifted row (license cleared, status left `ready`)
            # can still be built and the facts read must fail closed on it
            removed = [
                c
                for c in table.constraints
                if getattr(c, "name", None)
                in (
                    "ck_trainable_artifacts_ready_needs_license",
                    "ck_model_versions_origin_values",
                    "ck_model_import_jobs_succeeded_has_result",
                )
            ]
            for c in removed:
                table.constraints.discard(c)
            for c in columns:
                c.server_default = None
            try:
                table.create(self.engine)
            finally:
                for c, d in zip(columns, defaults, strict=True):
                    c.server_default = d
                table.constraints.update(removed)
        self.audit = Audit()
        self.counter = 0
        self.statements: list[Any] = []
        self.session = Session(self.engine)
        self.ctx = TenantContext(organization_id=ORG)
        self.actor = real_crud.ActorInfo(actor_type="user", actor_id="admin-1", request_id="req-1")

    def execute_scoped(
        self, session: Any, organization_id: Any, statement: Any, params: Any = None
    ) -> Any:
        self.statements.append(statement)
        return session.execute(statement)

    @contextlib.contextmanager
    def tenant_session(self, session_factory: Any, context: Any) -> Any:
        session = FaultySession(self.engine)
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @contextlib.contextmanager
    def patched(self, routes: Any = None) -> Any:
        targets = [
            (self.crud, "execute_tenant_scoped", self.execute_scoped),
            (self.crud, "write_audit_event", self.audit),
            (version_crud, "execute_tenant_scoped", self.execute_scoped),
            (resource_authorization, "execute_tenant_scoped", self.execute_scoped),
        ]
        if routes is not None:
            targets.append((routes, "tenant_scoped_session_for", self.tenant_session))
        saved = [(m, n, getattr(m, n)) for m, n, _ in targets]
        for m, n, v in targets:
            setattr(m, n, v)
        try:
            yield self
        finally:
            for m, n, v in saved:
                setattr(m, n, v)
            self.session.close()
            self.engine.dispose()

    # -- data ------------------------------------------------------------------------------
    def artifact(
        self,
        *,
        accepted: bool = True,
        family: str = "llama",
        char: str = "c",
        status: str = "ready",
        revision: str = "a1b2c3d4e5f6",
        org: uuid.UUID = ORG,
        accepted_by: str | None = "seed",
    ) -> TrainableArtifact:
        self.counter += 1
        row = TrainableArtifact(
            id=uuid.uuid4(),
            organization_id=org,
            display_name=f"a-{self.counter}",
            source_type="huggingface",
            source_uri="acme-org/real-base-model",
            model_family=family,
            base_model_revision=revision,
            weights_digest="sha256:" + char * 64,
            license_id="apache-2.0",
            license_accepted_by=accepted_by if accepted else None,
            license_accepted_at=NOW if accepted else None,
            trust_remote_code_allowed=False,
            required_python_packages_json=[],
            minimum_gpu_profile_json={},
            status=status,
            created_at=NOW,
            updated_at=NOW,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def version(
        self,
        kind: str,
        *,
        validated: bool = True,
        project: uuid.UUID = PROJECT,
        org: uuid.UUID = ORG,
        artifact: Any = None,
        parent: Any = None,
        ref: str = "provider/model-a",
        job_state: str = "succeeded",
    ) -> ModelVersion:
        self.counter += 1
        vid = uuid.uuid4()
        common: dict[str, Any] = {
            "id": vid,
            "organization_id": org,
            "project_id": project,
            "version_number": self.counter,
            "created_by": "seed",
            "created_at": NOW,
        }
        if kind == "endpoint":
            row = ModelVersion(
                import_kind="endpoint",
                origin="external_endpoint",
                external_provider_model_ref=ref,
                **common,
            )
        elif kind in ("weights", "factory", "adapter"):
            origin = {
                "weights": "imported_weights",
                "factory": "factory_trained",
                "adapter": "imported_adapter",
            }[kind]
            row = ModelVersion(
                import_kind="adapter" if kind == "adapter" else "full_weight",
                origin=origin,
                trainable_artifact_id=artifact.id,
                weights_digest=artifact.weights_digest,
                base_model_revision=artifact.base_model_revision,
                parent_model_version_id=None if parent is None else parent.id,
                **common,
            )
        else:
            raise AssertionError(kind)
        self.session.add(row)
        self.session.flush()
        if validated and kind != "factory":
            self.job(row, org=org, project=project, state=job_state)
        return row

    def job(
        self,
        version: ModelVersion,
        *,
        org: uuid.UUID = ORG,
        project: uuid.UUID = PROJECT,
        state: str = "succeeded",
        result: Any = "auto",
        result_in_any_state: bool = False,
    ) -> ModelImportJob:
        origin = version.origin
        succeeded = state == "succeeded"
        row = ModelImportJob(
            id=uuid.uuid4(),
            organization_id=org,
            project_id=project,
            requested_origin=origin,
            litellm_connection_id=uuid.uuid4() if origin == "external_endpoint" else None,
            external_provider_model_ref=version.external_provider_model_ref
            if origin == "external_endpoint"
            else None,
            result_model_version_id=(version.id if result == "auto" else result)
            if (succeeded or result_in_any_state)
            else None,
            claim_epoch=0 if state == "queued" else 1,
            lease_expires_at=NOW + timedelta(hours=1) if state == "validating" else None,
            state=state,
            failure_code="orig" if state in ("failed", "blocked_external") else None,
            created_by="seed",
            created_at=NOW,
            updated_at=NOW,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def assign(self, version: Any, role: str = "baseline", key: str = "k-1", **kw: Any) -> Any:
        return self.crud.assign_role(
            self.session,
            self.ctx,
            project_id=kw.pop("project_id", version.project_id),
            model_version_id=version.id,
            role=role,
            idempotency_key=key,
            actor=kw.pop("actor", self.actor),
        )

    def rows(self) -> list[Any]:
        return list(self.session.query(role_schema.ModelRoleAssignment).all())


def store_suite(crud: Any) -> None:
    schema = role_schema
    # ---- schema ------------------------------------------------------------------------------
    for role in schema.ROLES:
        check("SC", f"validate_role accepts {role}", schema.validate_role(role) == role)
    for bad in (
        "Judge",
        "judge ",
        " judge",
        "ｊudge",
        "judge​",
        "",
        None,
        1,
        True,
        ["judge"],
        b"judge",
    ):
        try:
            schema.validate_role(bad)
            check("SC", f"validate_role refuses {bad!r}", False)
        except schema.ModelRoleValidationError as exc:
            check(
                "SC",
                f"validate_role refuses {bad!r} with invalid_role",
                exc.code == "invalid_role" and str(exc) == "invalid_role",
            )
    for key, ok in (
        ("a", True),
        ("a" * 128, True),
        (" ~", True),
        ("", False),
        ("a" * 129, False),
        ("é", False),
        ("a\n", False),
        ("a\x7f", False),
        ("\x1f", False),
        (None, False),
        (1, False),
        (b"a", False),
    ):
        try:
            got = schema.validate_idempotency_key(key)
            check("SC", f"idempotency key {str(key)[:10]!r} accepted", ok and got == key)
        except schema.ModelRoleValidationError as exc:
            check(
                "SC",
                f"idempotency key {str(key)[:10]!r} refused invalid_body",
                (not ok) and exc.code == "invalid_body",
            )
    for role in schema.ROLES:
        check(
            "SC",
            f"is_admin_only({role!r})",
            schema.is_admin_only(role) is (role in ("current_production", "judge")),
        )
    for bad in (
        "Judge",
        "JUDGE",
        "judge ",
        " judge",
        "ｊudge",
        "current_production\u200b",
        "",
        None,
        1,
        S("judge"),
        S("current_production"),
        b"judge",
        ("judge",),
    ):
        check(
            "SC",
            f"is_admin_only({bad!r}) is False for anything but the exact spelling",
            schema.is_admin_only(bad) is False,
        )
    check(
        "SC",
        "ADMIN_ONLY_ROLES is exactly the incumbent and the judge",
        schema.ADMIN_ONLY_ROLES == frozenset({"current_production", "judge"}),
    )
    check("SC", "the idempotency key cap is 128", schema.IDEMPOTENCY_KEY_MAX_LENGTH == 128)
    d1 = schema.request_sha256(PROJECT, uuid.UUID(int=1), "judge")
    check("SC", "request digest is 64 lowercase hex", re.fullmatch(r"[0-9a-f]{64}", d1) is not None)
    check(
        "SC",
        "request digest is stable",
        d1 == schema.request_sha256(PROJECT, uuid.UUID(int=1), "judge"),
    )
    for label, other in (
        ("project", schema.request_sha256(PROJECT2, uuid.UUID(int=1), "judge")),
        ("version", schema.request_sha256(PROJECT, uuid.UUID(int=2), "judge")),
        ("role", schema.request_sha256(PROJECT, uuid.UUID(int=1), "teacher")),
    ):
        check("SC", f"request digest binds the {label}", other != d1)

    # ---- assign / replay / reuse -------------------------------------------------------------
    with Env(crud).patched() as e:
        v = e.version("weights", artifact=e.artifact())
        res = e.assign(v, "baseline", "k-1")
        row = res.assignment
        check("S1", "assign returns a non-replayed assignment", res.replayed is False)
        check(
            "S1",
            "the row carries the actor, request id, key and digest",
            row.assigned_by == "admin-1"
            and row.request_id == "req-1"
            and row.idempotency_key == "k-1"
            and row.request_sha256 == schema.request_sha256(PROJECT, v.id, "baseline")
            and row.role == "baseline"
            and row.organization_id == ORG
            and row.project_id == PROJECT
            and row.model_version_id == v.id
            and row.revoked_at is None,
        )
        check(
            "S1",
            "one audit event names the assignment",
            e.audit.actions() == ["model_role.assign"]
            and e.audit.events[0]["resource_type"] == "model_role_assignment"
            and e.audit.events[0]["resource_id"] == str(row.id)
            and e.audit.events[0]["organization_id"] == ORG
            and e.audit.events[0]["actor_type"] == "user"
            and e.audit.events[0]["actor_id"] == "admin-1"
            and e.audit.events[0]["request_id"] == "req-1",
        )
        again = e.assign(v, "baseline", "k-1")
        check(
            "S1",
            "the same key and request replays the stored row",
            again.replayed is True and again.assignment.id == row.id,
        )
        check(
            "S1",
            "a replay writes no row and no audit event",
            len(e.rows()) == 1 and e.audit.actions() == ["model_role.assign"],
        )
        for label, args in (("another role", ("teacher", "k-1")),):
            try:
                e.assign(v, *args)
                check("S1", f"{label} with a used key is refused", False)
            except crud.RoleConflictError as exc:
                check(
                    "S1",
                    f"{label} with a used key -> idempotency_key_reused",
                    exc.code == "idempotency_key_reused" and str(exc) == "idempotency_key_reused",
                )
        v2 = e.version("weights", artifact=e.artifact(char="d"))
        try:
            e.assign(v2, "baseline", "k-1")
            check("S1", "another version with a used key is refused", False)
        except crud.RoleConflictError as exc:
            check(
                "S1",
                "another version with a used key -> idempotency_key_reused",
                exc.code == "idempotency_key_reused",
            )
        check(
            "S1", "a refused reuse writes nothing", len(e.rows()) == 1 and len(e.audit.events) == 1
        )
        # a key is scoped to the organization and project
        other_project = e.version("weights", project=PROJECT2, artifact=e.artifact(char="e"))
        res2 = e.assign(other_project, "baseline", "k-1")
        check(
            "S1",
            "the same key in another project is a new assignment",
            res2.replayed is False and len(e.rows()) == 2,
        )
        # unvalidated
        unval = e.version("weights", validated=False, artifact=e.artifact(char="f"))
        try:
            e.assign(unval, "baseline", "k-2")
            check("S1", "an unvalidated version is refused", False)
        except crud.RoleConflictError as exc:
            check(
                "S1",
                "an unvalidated version -> import_not_validated",
                exc.code == "import_not_validated",
            )
        check("S1", "the refusal wrote nothing", len(e.rows()) == 2 and len(e.audit.events) == 2)
        # validation is not satisfied by a non-succeeded job
        for state in ("queued", "validating", "failed", "blocked_external"):
            pending = e.version("weights", artifact=e.artifact(char="1"), validated=False)
            # (a row Postgres' own CHECK would refuse: a result named in a non-succeeded state)
            e.job(pending, state=state, result_in_any_state=True)
            try:
                e.assign(pending, "baseline", f"p-{state}")
                check("S1", f"a {state} job does not validate", False)
            except crud.RoleConflictError as exc:
                check(
                    "S1",
                    f"a {state} job -> import_not_validated",
                    exc.code == "import_not_validated",
                )
        # a job naming another version, project or organization does not validate
        loose = e.version("weights", validated=False, artifact=e.artifact(char="2"))
        e.job(loose, project=PROJECT2, state="succeeded")
        e.job(loose, org=ORG2, state="succeeded")
        e.job(v, result=uuid.uuid4())
        try:
            e.assign(loose, "baseline", "loose")
            check("S1", "a job of another project or organization does not validate", False)
        except crud.RoleConflictError as exc:
            check(
                "S1",
                "a job of another project or organization -> import_not_validated",
                exc.code == "import_not_validated",
            )
        factory = e.version("factory", artifact=e.artifact(char="3"))
        check(
            "S1",
            "a factory trained version needs no job",
            e.assign(factory, "baseline", "fac").replayed is False,
        )
        # missing and foreign versions
        for label, kwargs in (("a wrong project", {"project_id": PROJECT2}),):
            try:
                e.assign(v, "teacher", "m-1", **kwargs)
                check("S1", f"{label} is refused", False)
            except version_crud.ModelVersionNotFoundError:
                check("S1", f"{label} -> ModelVersionNotFoundError", True)
        ghost = SimpleNamespace(id=uuid.uuid4(), project_id=PROJECT)
        try:
            e.assign(ghost, "teacher", "m-2")
            check("S1", "a missing version is refused", False)
        except version_crud.ModelVersionNotFoundError:
            check("S1", "a missing version -> ModelVersionNotFoundError", True)
        foreign = e.version("weights", org=ORG2, artifact=e.artifact(org=ORG2, char="4"))
        try:
            e.assign(foreign, "teacher", "m-3")
            check("S1", "a foreign organization's version is refused", False)
        except version_crud.ModelVersionNotFoundError:
            check("S1", "a foreign organization's version -> ModelVersionNotFoundError", True)
        # a key is matched exactly: case and surrounding spaces make it a different key
        for variant in ("K-1", "k-1 ", " k-1", "k-1\x7f"):
            vv = e.version("weights", artifact=e.artifact(char="c"))
            res_v = e.assign(vv, "baseline", variant)
            check(
                "S1",
                f"key {variant!r} is not the stored key 'k-1' (a new assignment, no replay)",
                res_v.replayed is False and res_v.assignment.idempotency_key == variant,
            )

    # ---- the database is the authority: violated name -> code ---------------------------------
    names = {
        "uq_model_role_assignments_active": "role_already_assigned",
        "uq_model_role_assignments_incumbent": "incumbent_exists",
        "tr_model_role_assignments_self_judging": "self_judging_role_conflict",
        "tr_model_role_assignments_insert_guard_validated": "import_not_validated",
    }
    for name, code in names.items():
        with Env(crud).patched() as e:
            e.session.close()
            e.session = FaultySession(e.engine)
            v = e.version("weights", artifact=e.artifact())
            FaultySession.fail_with = (name,)
            try:
                e.assign(v, "baseline", "d-1")
                check("S2", f"{name} maps to {code}", False)
            except crud.RoleConflictError as exc:
                check("S2", f"{name} maps to {code}", exc.code == code)
            finally:
                FaultySession.fail_with = None
            check("S2", f"{name}: nothing was audited", e.audit.events == [])
    for name in ("ck_model_role_assignments_role", None, "uq_unknown"):
        with Env(crud).patched() as e:
            e.session = FaultySession(e.engine)
            v = e.version("weights", artifact=e.artifact())
            FaultySession.fail_with = (name,)
            try:
                e.assign(v, "baseline", "d-2")
                check("S2", f"an unmapped violation {name!r} is not swallowed", False)
            except IntegrityError:
                check("S2", f"an unmapped violation {name!r} is re-raised as is", True)
            except crud.RoleConflictError:
                check("S2", f"an unmapped violation {name!r} is not a conflict code", False)
            finally:
                FaultySession.fail_with = None
    for name in [*names, "uq_model_role_assignments_idempotency"]:
        for variant in (name.upper(), name + " ", " " + name, name.replace("_", "-")):
            with Env(crud).patched() as e:
                e.session = FaultySession(e.engine)
                v = e.version("weights", artifact=e.artifact())
                FaultySession.fail_with = (variant,)
                try:
                    e.assign(v, "baseline", "d-3")
                    check("S2", f"variant {variant!r} is not swallowed", False)
                except IntegrityError:
                    check("S2", f"variant {variant!r} of a mapped name is re-raised as is", True)
                except crud.RoleConflictError:
                    check(
                        "S2", f"variant {variant!r} of a mapped name must not map to a code", False
                    )
                finally:
                    FaultySession.fail_with = None
    # the idempotency index race: re-read and replay / reuse / re-raise when nothing is there
    for label, stored_digest, expect in (
        ("same digest", "match", "replay"),
        ("other digest", "x" * 64, "reuse"),
        ("nothing stored", None, "raise"),
    ):
        with Env(crud).patched() as e:
            e.session = FaultySession(e.engine)
            v = e.version("weights", artifact=e.artifact())
            if stored_digest is not None:
                digest = (
                    schema.request_sha256(PROJECT, v.id, "baseline")
                    if stored_digest == "match"
                    else stored_digest
                )
                e.session.add(
                    schema.ModelRoleAssignment(
                        organization_id=ORG,
                        project_id=PROJECT,
                        model_version_id=v.id,
                        role="baseline",
                        assigned_by="raced",
                        request_id="r",
                        idempotency_key="race-key",
                        request_sha256=digest,
                    )
                )
                original_find = crud._find_by_idempotency_key
                calls = {"n": 0}

                def late(
                    session: Any,
                    ctx: Any,
                    project: Any,
                    key: Any,
                    _orig: Any = original_find,
                    _c: Any = calls,
                ) -> Any:
                    _c["n"] += 1
                    return None if _c["n"] == 1 else _orig(session, ctx, project, key)

                crud._find_by_idempotency_key = late
                e.session.flush()
            FaultySession.fail_with = ("uq_model_role_assignments_idempotency",)
            try:
                got = e.assign(v, "baseline", "race-key")
                check(
                    "S2",
                    f"idempotency race ({label})",
                    expect == "replay"
                    and got.replayed is True
                    and got.assignment.assigned_by == "raced",
                )
            except crud.RoleConflictError as exc:
                check(
                    "S2",
                    f"idempotency race ({label})",
                    expect == "reuse" and exc.code == "idempotency_key_reused",
                )
            except IntegrityError:
                check("S2", f"idempotency race ({label})", expect == "raise")
            finally:
                FaultySession.fail_with = None
                if stored_digest is not None:
                    crud._find_by_idempotency_key = original_find

    # a case / space variant of the idempotency index name is NOT the idempotency index, even when
    # a raced row is waiting: it must be re-raised, never replayed
    for variant in (
        "UQ_MODEL_ROLE_ASSIGNMENTS_IDEMPOTENCY",
        "uq_model_role_assignments_idempotency ",
        " uq_model_role_assignments_idempotency",
        "uq-model-role-assignments-idempotency",
    ):
        with Env(crud).patched() as e:
            e.session = FaultySession(e.engine)
            v = e.version("weights", artifact=e.artifact())
            e.session.add(
                schema.ModelRoleAssignment(
                    organization_id=ORG,
                    project_id=PROJECT,
                    model_version_id=v.id,
                    role="baseline",
                    assigned_by="raced",
                    request_id="r",
                    idempotency_key="race-key",
                    request_sha256=schema.request_sha256(PROJECT, v.id, "baseline"),
                )
            )
            e.session.flush()
            original_find = crud._find_by_idempotency_key
            calls = {"n": 0}

            def late_find(
                session: Any,
                ctx: Any,
                project: Any,
                key: Any,
                _o: Any = original_find,
                _c: Any = calls,
            ) -> Any:
                _c["n"] += 1
                return None if _c["n"] == 1 else _o(session, ctx, project, key)

            crud._find_by_idempotency_key = late_find
            FaultySession.fail_with = (variant,)
            try:
                e.assign(v, "baseline", "race-key")
                check("S2", f"variant {variant!r} with a raced row is not replayed", False)
            except IntegrityError:
                check("S2", f"variant {variant!r} with a raced row is re-raised", True)
            except crud.RoleConflictError:
                check("S2", f"variant {variant!r} must not map to a conflict code", False)
            finally:
                FaultySession.fail_with = None
                crud._find_by_idempotency_key = original_find

    # ---- revoke --------------------------------------------------------------------------------
    with Env(crud).patched() as e:
        v = e.version("weights", artifact=e.artifact())
        a = e.assign(v, "current_production", "r-1").assignment
        other_role = e.assign(v, "teacher", "r-2").assignment
        v2 = e.version("weights", artifact=e.artifact(char="d"))
        other_version = e.assign(v2, "current_production", "r-3").assignment
        e.audit.events.clear()
        e.statements.clear()
        got = crud.revoke_role(
            e.session,
            e.ctx,
            project_id=PROJECT,
            model_version_id=v.id,
            role="current_production",
            actor=real_crud.ActorInfo("user", "admin-9", "req-9"),
        )
        check(
            "R1",
            "revoke returns the same row with the revocation columns set",
            got.id == a.id
            and got.revoked_by == "admin-9"
            and got.revoke_reason_code == "revoked_by_actor"
            and got.revoked_at is not None,
        )
        check(
            "R1",
            "only the named role of the named version was revoked",
            other_role.revoked_at is None and other_version.revoked_at is None,
        )
        check(
            "R1",
            "one audit event for the revoke",
            e.audit.actions() == ["model_role.revoke"]
            and e.audit.events[0]["resource_id"] == str(a.id)
            and e.audit.events[0]["actor_id"] == "admin-9"
            and e.audit.events[0]["request_id"] == "req-9",
        )
        check(
            "R1",
            "the active row is selected FOR UPDATE",
            any(getattr(st, "_for_update_arg", None) is not None for st in e.statements),
        )
        for label, kwargs in (
            ("a second revoke", {"role": "current_production"}),
            ("an unassigned role", {"role": "judge"}),
        ):
            try:
                crud.revoke_role(
                    e.session,
                    e.ctx,
                    project_id=PROJECT,
                    model_version_id=v.id,
                    actor=e.actor,
                    **kwargs,
                )
                check("R1", f"{label} is refused", False)
            except crud.RoleConflictError as exc:
                check(
                    "R1",
                    f"{label} -> role_not_assigned",
                    exc.code == "role_not_assigned" and str(exc) == "role_not_assigned",
                )
        try:
            crud.revoke_role(
                e.session,
                e.ctx,
                project_id=PROJECT2,
                model_version_id=v.id,
                role="teacher",
                actor=e.actor,
            )
            check("R1", "a wrong project is refused", False)
        except version_crud.ModelVersionNotFoundError:
            check("R1", "a wrong project -> ModelVersionNotFoundError", True)
        again = e.assign(v, "current_production", "r-4")
        check(
            "R1",
            "assigning after a revoke is a new row",
            again.replayed is False and again.assignment.id != a.id,
        )
        listing = crud.list_roles(e.session, e.ctx, PROJECT, v.id)
        check(
            "R1",
            "list_roles returns this version's rows oldest first, revoked included",
            [r.id for r in listing]
            == sorted(
                [a.id, other_role.id, again.assignment.id],
                key=lambda i: next(r.assigned_at for r in listing if r.id == i),
            )
            and len(listing) == 3,
        )
        check(
            "R1", "list_roles omits other versions", other_version.id not in [r.id for r in listing]
        )
        try:
            crud.list_roles(e.session, e.ctx, PROJECT2, v.id)
            check("R1", "list_roles on a wrong project is refused", False)
        except version_crud.ModelVersionNotFoundError:
            check("R1", "list_roles on a wrong project is refused", True)

    # ---- non-canonical origins fail closed (no case folding) ----------------------------------
    with Env(crud).patched() as e:
        for bad in (
            "FACTORY_TRAINED",
            "Factory_Trained",
            " factory_trained",
            "factory_trained ",
            "factory-trained",
        ):
            v = e.version("factory", artifact=e.artifact())
            v.origin = bad
            e.session.flush()
            check(
                "F0",
                f"origin {bad!r} is not validated even without a job",
                crud.is_validated(e.session, e.ctx, v) is False,
            )
            try:
                crud.build_action_facts(e.session, e.ctx, v)
                check("F0", f"origin {bad!r} raises in build_action_facts", False)
            except ContinuationFactsErrorType:
                check(
                    "F0",
                    f"origin {bad!r} raises ContinuationFactsError in build_action_facts",
                    True,
                )
        base = e.version("weights", artifact=e.artifact(char="b"))
        ad = e.version("adapter", artifact=e.artifact(char="d"), parent=base)
        check(
            "F0",
            "control: the exact parent origin is a compatible base",
            crud.build_action_facts(e.session, e.ctx, ad).base_compatible is True,
        )
        for bad in (
            "IMPORTED_WEIGHTS",
            "Imported_Weights",
            "imported_weights ",
            " imported_weights",
        ):
            base.origin = bad
            e.session.flush()
            check(
                "F0",
                f"parent origin {bad!r} is not a base",
                crud.build_action_facts(e.session, e.ctx, ad).base_compatible is False,
            )
        base.origin = "imported_weights"
        e.session.flush()
        # the early guards cost no query
        art = e.session.query(TrainableArtifact).first()
        e.statements.clear()
        no_parent = SimpleNamespace(
            parent_model_version_id=None, project_id=PROJECT, id=uuid.uuid4()
        )
        check(
            "F0",
            "no parent -> incompatible without a query",
            crud._base_compatible(e.session, e.ctx, no_parent, art) is False and e.statements == [],
        )
        e.statements.clear()
        has_parent = SimpleNamespace(
            parent_model_version_id=base.id, project_id=PROJECT, id=uuid.uuid4()
        )
        check(
            "F0",
            "no artifact -> incompatible without a query",
            crud._base_compatible(e.session, e.ctx, has_parent, None) is False
            and e.statements == [],
        )
        crud._base_compatible(
            e.session, e.ctx, ad, e.session.get(TrainableArtifact, ad.trainable_artifact_id)
        )
        check("F0", "both present -> the parent is looked up", len(e.statements) > 0)

    # ---- facts ---------------------------------------------------------------------------------
    with Env(crud).patched() as e:
        ep = e.version("endpoint", ref="provider/ft:lora")
        f = crud.build_action_facts(e.session, e.ctx, ep)
        check(
            "F1",
            "endpoint facts: origin, validated, no license, no base, the ref",
            f.origin == "external_endpoint"
            and f.validated is True
            and f.license_accepted is None
            and f.base_compatible is None
            and f.model_ref == "provider/ft:lora"
            and f.model_version_id == str(ep.id),
        )
        check(
            "F1",
            "no checkpoint and no prerequisite is ever assumed",
            f.checkpoint is None and dict(f.prerequisites) == {},
        )
        f2 = crud.build_action_facts(e.session, e.ctx, ep)
        check(
            "F1",
            "each call proposes a new run id",
            f.new_run_id != f2.new_run_id and uuid.UUID(f.new_run_id),
        )
        unv = e.version("endpoint", validated=False)
        check(
            "F1",
            "an endpoint without a succeeded job is not validated",
            crud.build_action_facts(e.session, e.ctx, unv).validated is False,
        )
        w_art = e.artifact()
        w = e.version("weights", artifact=w_art)
        f = crud.build_action_facts(e.session, e.ctx, w)
        check(
            "F1",
            "weights facts: validated, license accepted, no base fact, no ref",
            f.validated is True
            and f.license_accepted is True
            and f.base_compatible is None
            and f.model_ref is None,
        )
        w.external_provider_model_ref = "stray/ft:lora-trainable"
        e.session.flush()
        check(
            "F1",
            "a stray reference on a weight-backed row is never carried into the facts",
            crud.build_action_facts(e.session, e.ctx, w).model_ref is None,
        )
        w.external_provider_model_ref = None
        e.session.flush()
        w_art.license_accepted_by = None
        e.session.flush()
        check(
            "F1",
            "a revoked acceptor -> license not accepted",
            crud.build_action_facts(e.session, e.ctx, w).license_accepted is False,
        )
        w_art.license_accepted_by = "seed"
        w_art.license_accepted_at = None
        e.session.flush()
        check(
            "F1",
            "a missing accepted_at -> license not accepted",
            crud.build_action_facts(e.session, e.ctx, w).license_accepted is False,
        )
        w_art.license_accepted_at = NOW
        e.session.flush()
        check(
            "F1",
            "restored -> accepted",
            crud.build_action_facts(e.session, e.ctx, w).license_accepted is True,
        )
        e.session.delete(w_art)
        e.session.flush()
        check(
            "F1",
            "a missing artifact -> license not accepted (fail closed)",
            crud.build_action_facts(e.session, e.ctx, w).license_accepted is False,
        )
        # artifact of another organization is not read
        foreign_art = e.artifact(org=ORG2, char="9")
        wf = e.version("weights", artifact=foreign_art)
        check(
            "F1",
            "another organization's artifact is not read",
            crud.build_action_facts(e.session, e.ctx, wf).license_accepted is False,
        )
        fac = e.version("factory", artifact=e.artifact(char="8"))
        f = crud.build_action_facts(e.session, e.ctx, fac)
        check(
            "F1",
            "factory trained facts: validated with no job",
            f.origin == "factory_trained" and f.validated is True and f.license_accepted is True,
        )
        # roles
        w2 = e.version("weights", artifact=e.artifact(char="7"))
        for role, key in (("teacher", "a"), ("judge", "b"), ("current_production", "c")):
            e.assign(w2, role, key)
        old = e.assign(w2, "baseline", "d").assignment
        old.revoked_at, old.revoked_by, old.revoke_reason_code = NOW, "x", "x"
        e.session.flush()
        other = e.version("weights", artifact=e.artifact(char="6"))
        e.assign(other, "candidate", "e")
        f = crud.build_action_facts(e.session, e.ctx, w2)
        check(
            "F1",
            "facts carry the active roles only, of this version only",
            f.roles == frozenset({"teacher", "judge", "current_production"}),
        )

        # adapter base
        base_art = e.artifact(char="b")
        base = e.version("weights", artifact=base_art)
        ad_art = e.artifact(char="d")
        ad = e.version("adapter", artifact=ad_art, parent=base)
        f = crud.build_action_facts(e.session, e.ctx, ad)
        check(
            "F2",
            "adapter on its exact base: compatible, license accepted, validated",
            f.base_compatible is True and f.license_accepted is True and f.validated is True,
        )
        no_parent = e.version("adapter", artifact=e.artifact(char="5"), parent=None)
        check(
            "F2",
            "no parent -> incompatible",
            crud.build_action_facts(e.session, e.ctx, no_parent).base_compatible is False,
        )
        ghost_parent = e.version(
            "adapter", artifact=e.artifact(char="4"), parent=SimpleNamespace(id=uuid.uuid4())
        )
        check(
            "F2",
            "a missing parent row -> incompatible",
            crud.build_action_facts(e.session, e.ctx, ghost_parent).base_compatible is False,
        )
        other_proj_base = e.version("weights", project=PROJECT2, artifact=e.artifact(char="3"))
        cross = e.version("adapter", artifact=e.artifact(char="2"), parent=other_proj_base)
        check(
            "F2",
            "a parent in another project -> incompatible",
            crud.build_action_facts(e.session, e.ctx, cross).base_compatible is False,
        )
        endpoint_parent = e.version("endpoint")
        bad_origin = e.version("adapter", artifact=e.artifact(char="1"), parent=endpoint_parent)
        check(
            "F2",
            "a parent that is not imported weights -> incompatible",
            crud.build_action_facts(e.session, e.ctx, bad_origin).base_compatible is False,
        )
        fac_parent = e.version("factory", artifact=e.artifact(char="0"))
        bad_origin2 = e.version("adapter", artifact=e.artifact(char="a"), parent=fac_parent)
        check(
            "F2",
            "a factory trained parent is not an imported base -> incompatible",
            crud.build_action_facts(e.session, e.ctx, bad_origin2).base_compatible is False,
        )
        unval_base = e.version("weights", validated=False, artifact=e.artifact(char="e"))
        unval_ad = e.version("adapter", artifact=e.artifact(char="f"), parent=unval_base)
        check(
            "F2",
            "an unvalidated parent base -> incompatible",
            crud.build_action_facts(e.session, e.ctx, unval_ad).base_compatible is False,
        )
        # base artifact states
        for label, mutate in (
            ("base license revoked", lambda a_: setattr(a_, "license_accepted_at", None)),
            ("base family differs", lambda a_: setattr(a_, "model_family", "mistral")),
            (
                "base revision drifted",
                lambda a_: setattr(a_, "base_model_revision", "ffffffffffff"),
            ),
            ("base not ready", lambda a_: setattr(a_, "status", "registered")),
        ):
            b_art = e.artifact(char="1" if label == "base license revoked" else "2")
            b = e.version("weights", artifact=b_art)
            a_art = e.artifact(char="d" if label != "base license revoked" else "c")
            a_row = e.version("adapter", artifact=a_art, parent=b)
            check(
                "F2",
                f"{label}: control compatible",
                crud.build_action_facts(e.session, e.ctx, a_row).base_compatible is True,
            )
            mutate(b_art)
            e.session.flush()
            check(
                "F2",
                f"{label} -> incompatible",
                crud.build_action_facts(e.session, e.ctx, a_row).base_compatible is False,
            )
        no_art = e.version("adapter", artifact=e.artifact(char="9"), parent=base)
        e.session.query(TrainableArtifact).filter(
            TrainableArtifact.weights_digest == "sha256:" + "9" * 64
        ).delete()
        e.session.flush()
        check(
            "F2",
            "a missing adapter artifact -> incompatible",
            crud.build_action_facts(e.session, e.ctx, no_art).base_compatible is False,
        )
        same_digest = e.version("adapter", artifact=e.artifact(char="b"), parent=base)
        check(
            "F2",
            "an adapter whose digest equals its base -> incompatible (lineage manifest)",
            crud.build_action_facts(e.session, e.ctx, same_digest).base_compatible is False,
        )


# -- routes, on a real server ---------------------------------------------------------------


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
        except Exception:  # noqa: BLE001
            return None


def http_request(
    base: str,
    method: str,
    path: str,
    *,
    principal: str | None,
    payload: Any = None,
    raw: bytes | None = None,
) -> Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body = raw
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return Http(response.status, response.read())
    finally:
        conn.close()


@contextlib.contextmanager
def serve(app: Any) -> Any:
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


ORG_ROLES = (
    "organization_admin",
    "workspace_admin",
    "ml_engineer",
    "evaluator",
    "approver",
    "viewer",
)


def routes_suite(routes: Any) -> None:
    from fastapi import FastAPI

    with Env().patched(routes) as e:
        principals = {
            f"{r}1": (AuthenticatedPrincipal(issuer=ISSUER, subject=f"{r}-1"), Role(r), ORG)
            for r in ORG_ROLES
        }
        principals["admin2"] = (
            AuthenticatedPrincipal(issuer=ISSUER, subject="admin-2"),
            Role.ORGANIZATION_ADMIN,
            ORG2,
        )

        def resolver(request: Any) -> Any:
            entry = principals.get(request.headers.get("x-test-principal", ""))
            return None if entry is None else entry[0]

        def lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Any:
            for principal, role, org in principals.values():
                if principal.subject == subject and org == organization_id:
                    return role
            return None

        app = FastAPI()
        app.include_router(
            routes.build_model_role_router(
                lambda: None, principal_resolver=resolver, role_lookup=lookup
            )
        )
        art = e.artifact()
        base = e.version("weights", artifact=art)
        versions = {
            "endpoint": lambda project=PROJECT: e.version("endpoint", project=project),
            "weights": lambda project=PROJECT: e.version(
                "weights", project=project, artifact=e.artifact(char="c")
            ),
            "ftf1001": lambda project=PROJECT: e.version(
                "endpoint", project=project, validated=False
            ),
        }

        def roles_path(version: Any, org: uuid.UUID = ORG, project: uuid.UUID = PROJECT) -> str:
            return f"/api/v1/organizations/{org}/projects/{project}/model-versions/{version}/roles"

        def post(b: str, who: str | None, version: Any, role: Any, key: Any, **kw: Any) -> Http:
            return http_request(
                b,
                "POST",
                roles_path(version, **kw),
                principal=who,
                payload={"role": role, "idempotency_key": key},
            )

        with serve(app) as b:
            check(
                "RT",
                "401 without a principal",
                post(b, None, base.id, "baseline", "k").status == 401,
            )
            for kind, make in versions.items():
                for role in schema_roles():
                    for org_role in ORG_ROLES:
                        proj = uuid.uuid4() if role == "current_production" else PROJECT
                        v = make(proj)
                        r = post(
                            b,
                            f"{org_role}1",
                            v.id,
                            role,
                            f"t-{uuid.uuid4().hex}",
                            project=proj,
                        )
                        forbidden = org_role not in ("organization_admin", "ml_engineer") or (
                            role in ("current_production", "judge")
                            and org_role != "organization_admin"
                        )
                        if forbidden:
                            want: tuple[int, Any] = (403, "x")
                        elif kind == "ftf1001":
                            want = (409, "import_not_validated")
                        else:
                            want = (201, None)
                        got = (
                            r.status,
                            r.detail
                            if r.status not in (201, 403)
                            else None
                            if r.status == 201
                            else "x",
                        )
                        check(
                            "RT", f"{kind} {role} as {org_role}: want {want} got {got}", got == want
                        )
            v = versions["weights"]()
            for bad in ("Judge", "judge ", "ｊudge", "judge​", "", "owner"):
                r = post(b, "organization_admin1", v.id, bad, "k")
                check(
                    "RB",
                    f"role {bad!r} -> 400 invalid_role",
                    r.status == 400 and r.detail == "invalid_role",
                )
            for bad in (None, 1, True, ["judge"]):
                r = post(b, "organization_admin1", v.id, bad, "k")
                check(
                    "RB",
                    f"role {bad!r} -> 400 invalid_body",
                    r.status == 400 and r.detail == "invalid_body",
                )
            for key in ("", "a" * 129, "é", None, 1):
                r = post(b, "organization_admin1", v.id, "teacher", key)
                check(
                    "RB",
                    f"key {str(key)[:6]!r} -> 400 invalid_body",
                    r.status == 400 and r.detail == "invalid_body",
                )
            for payload in (
                {"role": "baseline"},
                {"idempotency_key": "k"},
                {},
                {"role": "baseline", "idempotency_key": "k", "x": 1},
                [],
                "x",
                1,
                None,
            ):
                r = http_request(
                    b, "POST", roles_path(v.id), principal="organization_admin1", payload=payload
                )
                check(
                    "RB",
                    f"body {payload!r} -> 400 invalid_body",
                    r.status == 400 and r.detail == "invalid_body",
                )
            for raw in (b"{", b"", b"nope", b"[1]"):
                r = http_request(
                    b, "POST", roles_path(v.id), principal="organization_admin1", raw=raw
                )
                check(
                    "RB",
                    f"raw {raw!r} -> 400 invalid_body",
                    r.status == 400 and r.detail == "invalid_body",
                )
            r = post(b, "ml_engineer1", v.id, "Judge", "k")
            check("RB", "the enum is checked before the admin rule", r.status == 400)
            check(
                "RB",
                "a refused body wrote nothing",
                all(row.model_version_id != v.id for row in e.rows()),
            )
            # replay / reuse / conflicts
            r1 = post(b, "ml_engineer1", v.id, "baseline", "idem")
            r2 = post(b, "ml_engineer1", v.id, "baseline", "idem")
            check(
                "RI",
                "first 201 then replay 200 with the same id",
                r1.status == 201 and r2.status == 200 and r1.json()["id"] == r2.json()["id"],
            )
            stored = [row for row in e.rows() if row.idempotency_key == "idem"]
            check(
                "RI",
                "the 201 body's id is the stored row's id",
                len(stored) == 1 and r1.json()["id"] == str(stored[0].id),
            )
            assign_events = [x for x in e.audit.events if x["action"] == "model_role.assign"]
            last = assign_events[-1]
            check(
                "RI",
                "the route's audit actor is the authenticated subject, type user",
                last["actor_type"] == "user"
                and last["actor_id"] == "ml_engineer-1"
                and last["organization_id"] == ORG
                and last["resource_id"] == str(stored[0].id),
            )
            check(
                "RI",
                "each request carries its own 32-hex request id",
                re.fullmatch(r"[0-9a-f]{32}", last["request_id"]) is not None
                and last["request_id"] == stored[0].request_id
                and len({x["request_id"] for x in assign_events}) == len(assign_events),
            )
            r3 = post(b, "ml_engineer1", v.id, "candidate", "idem")
            check(
                "RI",
                "same key + other role -> 409 idempotency_key_reused",
                r3.status == 409 and r3.json() == {"detail": "idempotency_key_reused"},
            )
            # the database is the authority for every conflict: each stable code the store raises
            # is mapped to its 409, and a missing/foreign row to the identical 404
            real_assign = routes.assign_role
            from app.auth.tenant_isolation_query_guards import TenantContextError

            for code in (
                "role_already_assigned",
                "incumbent_exists",
                "self_judging_role_conflict",
                "import_not_validated",
                "idempotency_key_reused",
            ):

                def conflict(*a: Any, _code: str = code, **k: Any) -> Any:
                    raise real_routes.RoleConflictError(_code)

                routes.assign_role = conflict
                try:
                    r4 = post(b, "ml_engineer1", v.id, "teacher", f"map-{code}")
                finally:
                    routes.assign_role = real_assign
                check(
                    "RI",
                    f"{code} is mapped to 409 with the code only",
                    r4.status == 409 and r4.json() == {"detail": code},
                )
            for exc_type in (
                version_crud.ModelVersionNotFoundError,
                version_crud.ProjectNotFoundError,
                TenantContextError,
            ):

                def missing(*a: Any, _t: Any = exc_type, **k: Any) -> Any:
                    raise _t("leaky detail")

                routes.assign_role = missing
                try:
                    r4 = post(b, "ml_engineer1", v.id, "teacher", "map-404")
                finally:
                    routes.assign_role = real_assign
                check(
                    "RI",
                    f"{exc_type.__name__} is mapped to the identical 404",
                    r4.status == 404 and r4.json() == {"detail": "not_found"},
                )
            body = r1.json()
            check(
                "RI",
                "the 201 body is the serialized assignment",
                set(body)
                == {
                    "id",
                    "organization_id",
                    "project_id",
                    "model_version_id",
                    "role",
                    "assigned_by",
                    "assigned_at",
                    "idempotency_key",
                    "revoked_at",
                    "revoked_by",
                    "revoke_reason_code",
                }
                and body["role"] == "baseline"
                and body["assigned_by"] == "ml_engineer-1"
                and body["idempotency_key"] == "idem"
                and body["organization_id"] == str(ORG)
                and body["project_id"] == str(PROJECT)
                and body["model_version_id"] == str(v.id)
                and body["revoked_at"] is None
                and body["revoked_by"] is None
                and body["revoke_reason_code"] is None,
            )
            # 404s
            for label, org, project, who in (
                ("foreign project", ORG, PROJECT2, "organization_admin1"),
                ("foreign org path", ORG2, PROJECT, "admin2"),
            ):
                r = post(b, who, v.id, "baseline", "n-1", org=org, project=project)
                check(
                    "RN",
                    f"{label}: 404 not_found",
                    r.status == 404 and r.json() == {"detail": "not_found"},
                )
            r = post(b, "organization_admin1", uuid.uuid4(), "baseline", "n-2")
            check(
                "RN",
                "a missing version: 404 not_found",
                r.status == 404 and r.detail == "not_found",
            )
            r = post(b, "admin2", v.id, "baseline", "n-3")
            check("RN", "another org's principal on this org's path: 403", r.status == 403)

            # revoke
            def revoke(who: str, version: Any, role: str, **kw: Any) -> Http:
                return http_request(
                    b,
                    "POST",
                    f"{roles_path(version)}/{urllib.parse.quote(role, safe='')}/revoke",
                    principal=who,
                    **kw,
                )

            r = revoke("ml_engineer1", v.id, "baseline")
            check(
                "RR",
                "revoke 200 with the revocation set",
                r.status == 200
                and r.json()["revoked_by"] == "ml_engineer-1"
                and r.json()["revoke_reason_code"] == "revoked_by_actor"
                and r.json()["revoked_at"],
            )
            r = revoke("ml_engineer1", v.id, "baseline")
            check(
                "RR",
                "second revoke 409 role_not_assigned",
                r.status == 409 and r.detail == "role_not_assigned",
            )
            r = revoke("ml_engineer1", v.id, "Baseline")
            check(
                "RR",
                "inexact path role -> 400 invalid_role",
                r.status == 400 and r.detail == "invalid_role",
            )
            r = revoke("ml_engineer1", v.id, "judge")
            check("RR", "ml_engineer cannot revoke judge -> 403", r.status == 403)
            r = revoke("ml_engineer1", v.id, "current_production")
            check("RR", "ml_engineer cannot revoke current_production -> 403", r.status == 403)
            for who in ("workspace_admin1", "evaluator1", "approver1", "viewer1"):
                check(
                    "RR", f"{who} cannot revoke -> 403", revoke(who, v.id, "teacher").status == 403
                )
            check(
                "RR",
                "no principal -> 401",
                http_request(b, "POST", f"{roles_path(v.id)}/teacher/revoke", principal=None).status
                == 401,
            )
            post(b, "organization_admin1", v.id, "judge", "j-1")
            r = revoke("organization_admin1", v.id, "judge")
            check("RR", "an organization_admin may revoke judge", r.status == 200)
            for raw in (b'{"reason": "x"}', b"[]", b"nope", b'"x"'):
                r = revoke("organization_admin1", v.id, "teacher", raw=raw)
                check(
                    "RR",
                    f"revoke body {raw!r} -> 400 invalid_body",
                    r.status == 400 and r.detail == "invalid_body",
                )
            r = revoke("organization_admin1", v.id, "teacher", raw=b"{}")
            check(
                "RR",
                "an empty object body is accepted (then 409: nothing assigned)",
                r.status == 409 and r.detail == "role_not_assigned",
            )
            r = revoke("organization_admin1", uuid.uuid4(), "teacher")
            check("RR", "a missing version -> 404", r.status == 404 and r.detail == "not_found")
            # list
            lst = http_request(b, "GET", roles_path(v.id), principal="viewer1")
            check(
                "RL",
                "GET roles lists the version's assignments for a viewer",
                lst.status == 200 and [x["role"] for x in lst.json()] == ["baseline", "judge"],
            )
            check(
                "RL",
                "GET roles: 401 / 404",
                http_request(b, "GET", roles_path(v.id), principal=None).status == 401
                and http_request(b, "GET", roles_path(uuid.uuid4()), principal="viewer1").status
                == 404
                and http_request(
                    b, "GET", roles_path(v.id, project=PROJECT2), principal="viewer1"
                ).status
                == 404,
            )

            # actions
            def actions(version: Any, who: str = "viewer1", **kw: Any) -> Http:
                org = kw.get("org", ORG)
                project = kw.get("project", PROJECT)
                return http_request(
                    b,
                    "GET",
                    f"/api/v1/organizations/{org}/projects/{project}/model-versions/{version}/actions",
                    principal=who,
                )

            ep = e.version("endpoint")
            r = actions(ep.id)
            doc = r.json() if r.status == 200 else {}
            check(
                "RA",
                "actions: 200 with the full decision document",
                r.status == 200
                and list(doc["actions"]) == list(ACTIONS)
                and doc["origin"] == "external_endpoint"
                and doc["validated"] is True
                and doc["roles"] == []
                and doc["model_version_id"] == str(ep.id),
            )
            check(
                "RA",
                "actions: endpoint decisions",
                doc["actions"]["evaluate"] == {"allowed": True, "code": None, "plan": None}
                and doc["actions"]["continue_tuning"]
                == {"allowed": False, "code": "endpoint_not_trainable", "plan": None}
                and doc["actions"]["exact_resume"]["code"] == "no_trusted_checkpoint",
            )
            wv = e.version("weights", artifact=e.artifact(char="c"))
            doc = actions(wv.id).json()
            plan = doc["actions"]["continue_tuning"]["plan"]
            check(
                "RA",
                "actions: weights continue_tuning plan is serialized in full",
                doc["actions"]["continue_tuning"]["allowed"] is True
                and set(plan)
                == {
                    "kind",
                    "run_id",
                    "start_step",
                    "parent_model_version_id",
                    "optimizer_state",
                    "separate_cost",
                }
                and plan["kind"] == "new_run"
                and plan["start_step"] == 0
                and plan["optimizer_state"] == "none"
                and plan["parent_model_version_id"] == str(wv.id)
                and plan["separate_cost"] is True,
            )
            e.assign(wv, "current_production", "inc")
            doc = actions(wv.id).json()
            check(
                "RA",
                "actions: roles reflect active assignments and enable retain_incumbent",
                doc["roles"] == ["current_production"]
                and doc["actions"]["retain_incumbent"]["allowed"] is True,
            )
            uv_ = e.version("endpoint", validated=False)
            doc = actions(uv_.id).json()
            check(
                "RA",
                "actions: an unvalidated row denies everything",
                doc["validated"] is False
                and all(d["code"] == "import_not_validated" for d in doc["actions"].values()),
            )
            for who in ORG_ROLES:
                check("RA", f"{who} may read actions", actions(wv.id, f"{who}1").status == 200)
            check(
                "RA",
                "actions: 401 / 404 / 404",
                actions(wv.id, None).status == 401
                and actions(uuid.uuid4()).status == 404
                and actions(wv.id, project=PROJECT2).status == 404
                and actions(wv.id, "admin2", org=ORG2).status == 404,
            )
            bad = e.version("endpoint")
            bad.external_provider_model_ref = "a\x00b"
            e.session.flush()
            r = actions(bad.id)
            check(
                "RA",
                "a stored row failing the fact checks -> 409 model_facts_invalid (fail closed)",
                r.status == 409 and r.detail == "model_facts_invalid",
            )


def schema_roles() -> tuple[str, ...]:
    return role_schema.ROLES


# -- migration SQL, contract, wiring -----------------------------------------------------------


def offline_sql(action: str, revision_range: str) -> str:
    env = {**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check(
        "MG",
        f"alembic {action} {revision_range} --sql exits 0 ({result.stderr[-200:]})",
        result.returncode == 0,
    )
    return " ".join(result.stdout.split())


def static_suite() -> None:
    up = offline_sql("upgrade", "0035_dataset_mappings:0036_model_role_assignments")
    for needle in (
        "ALTER TABLE model_versions ADD CONSTRAINT uq_model_versions_organization_id_project_id_id UNIQUE (organization_id, project_id, id)",
        "CREATE TABLE model_role_assignments (",
        "id UUID DEFAULT gen_random_uuid() NOT NULL",
        "organization_id UUID NOT NULL",
        "project_id UUID NOT NULL",
        "model_version_id UUID NOT NULL",
        "role VARCHAR(32) NOT NULL",
        "assigned_by VARCHAR(200) NOT NULL",
        "assigned_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "request_id VARCHAR(128) NOT NULL",
        "idempotency_key VARCHAR(128) NOT NULL",
        "request_sha256 VARCHAR(64) NOT NULL",
        "revoked_at TIMESTAMP WITH TIME ZONE",
        "revoked_by VARCHAR(200)",
        "revoke_reason_code VARCHAR(64)",
        "CONSTRAINT fk_model_role_assignments_org FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE",
        "CONSTRAINT fk_model_role_assignments_model_version FOREIGN KEY(organization_id, project_id, model_version_id) REFERENCES model_versions (organization_id, project_id, id) ON DELETE RESTRICT",
        "CONSTRAINT ck_model_role_assignments_role CHECK (role IN ('current_production','baseline','candidate','teacher','judge'))",
        "CONSTRAINT ck_model_role_assignments_text_not_blank CHECK (assigned_by !~ '^\\s*$' AND request_id !~ '^\\s*$')",
        "CONSTRAINT ck_model_role_assignments_idempotency_key CHECK (idempotency_key ~ '^[ -~]{1,128}$')",
        "CONSTRAINT ck_model_role_assignments_request_sha256 CHECK (request_sha256 ~ '^[0-9a-f]{64}$')",
        "CONSTRAINT ck_model_role_assignments_revocation_columns CHECK ((revoked_at IS NULL) = (revoked_by IS NULL) AND (revoked_at IS NULL) = (revoke_reason_code IS NULL))",
        "CONSTRAINT ck_model_role_assignments_revocation_text CHECK ((revoked_by IS NULL OR revoked_by !~ '^\\s*$') AND (revoke_reason_code IS NULL OR revoke_reason_code ~ '^[a-z][a-z0-9_]{0,63}$'))",
        "CREATE INDEX ix_model_role_assignments_organization_id ON model_role_assignments (organization_id)",
        "CREATE INDEX ix_model_role_assignments_model_version_id ON model_role_assignments (model_version_id)",
        "CREATE UNIQUE INDEX uq_model_role_assignments_active ON model_role_assignments (project_id, model_version_id, role) WHERE revoked_at IS NULL",
        "CREATE UNIQUE INDEX uq_model_role_assignments_incumbent ON model_role_assignments (project_id) WHERE role = 'current_production' AND revoked_at IS NULL",
        "CREATE UNIQUE INDEX uq_model_role_assignments_idempotency ON model_role_assignments (organization_id, project_id, idempotency_key)",
        'GRANT SELECT, INSERT ON model_role_assignments TO "sovra_app"',
        'GRANT UPDATE (revoked_at, revoked_by, revoke_reason_code) ON model_role_assignments TO "sovra_app"',
        "CREATE TRIGGER tr_model_role_assignments_insert_guard BEFORE INSERT ON model_role_assignments FOR EACH ROW EXECUTE FUNCTION model_role_assignments_insert_guard()",
        "CREATE TRIGGER tr_model_role_assignments_update_guard BEFORE UPDATE ON model_role_assignments FOR EACH ROW EXECUTE FUNCTION model_role_assignments_update_guard()",
        "ALTER TABLE model_role_assignments ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE model_role_assignments FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON model_role_assignments FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid)",
        "pg_advisory_xact_lock(hashtextextended(NEW.model_version_id::text, 36))",
        "IF NEW.revoked_at IS NOT NULL THEN RAISE EXCEPTION 'a role is assigned active' USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_model_role_assignments_insert_guard_active'",
        "RAISE EXCEPTION 'a role needs a validated model version' USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_model_role_assignments_insert_guard_validated'",
        "RAISE EXCEPTION 'a model version is never both judge and candidate' USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_model_role_assignments_self_judging'",
        "RAISE EXCEPTION 'a revoked role never changes' USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_model_role_assignments_update_guard_final'",
        "RAISE EXCEPTION 'a role assignment can only be revoked' USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_model_role_assignments_update_guard_transition'",
        "RAISE EXCEPTION 'a role assignment is immutable except its revocation' USING ERRCODE = 'check_violation', CONSTRAINT = 'tr_model_role_assignments_update_guard_frozen'",
        "(to_jsonb(NEW) - 'revoked_at' - 'revoked_by' - 'revoke_reason_code') IS DISTINCT FROM (to_jsonb(OLD) - 'revoked_at' - 'revoked_by' - 'revoke_reason_code')",
        "UPDATE alembic_version SET version_num='0036_model_role_assignments'",
    ):
        check("MG", f"upgrade SQL contains: {needle[:90]}", needle in up)
    check(
        "MG",
        "no DELETE or TRUNCATE grant, no table-wide UPDATE grant",
        "GRANT DELETE" not in up
        and "GRANT TRUNCATE" not in up
        and "GRANT UPDATE ON" not in up
        and "GRANT ALL" not in up,
    )
    check(
        "MG",
        "both judge/candidate directions are in the insert guard",
        "r.role = 'candidate'" in up
        and "r.role = 'judge'" in up
        and "NEW.role = 'judge'" in up
        and "NEW.role = 'candidate'" in up,
    )
    down = offline_sql("downgrade", "0036_model_role_assignments:0035_dataset_mappings")
    for needle in (
        "DROP TRIGGER IF EXISTS tr_model_role_assignments_update_guard ON model_role_assignments",
        "DROP FUNCTION IF EXISTS model_role_assignments_update_guard()",
        "DROP TRIGGER IF EXISTS tr_model_role_assignments_insert_guard ON model_role_assignments",
        "DROP FUNCTION IF EXISTS model_role_assignments_insert_guard()",
        "DROP POLICY IF EXISTS tenant_isolation ON model_role_assignments",
        'REVOKE ALL ON model_role_assignments FROM "sovra_app"',
        "DROP TABLE model_role_assignments",
        "ALTER TABLE model_versions DROP CONSTRAINT uq_model_versions_organization_id_project_id_id",
        "UPDATE alembic_version SET version_num='0035_dataset_mappings'",
    ):
        check("MG", f"downgrade SQL contains: {needle[:80]}", needle in down)
    src = MIGRATION_PATH.read_text()
    check(
        "MG",
        "revision id and parent",
        'revision = "0036_model_role_assignments"' in src
        and 'down_revision = "0035_dataset_mappings"' in src,
    )
    check(
        "MG", "revision id fits alembic_version (<= 32)", len("0036_model_role_assignments") <= 32
    )
    revisions: dict[str, str] = {}
    for path in sorted((ROOT / "apps" / "api" / "alembic" / "versions").glob("0*.py")):
        text = path.read_text()
        rev = re.search(r'^revision = "([^"]+)"', text, re.M)
        parent = re.search(r'^down_revision = (?:"([^"]+)"|None)', text, re.M)
        if rev and parent:
            revisions[rev.group(1)] = parent.group(1) or ""
    parents = set(revisions.values())
    heads = [r for r in revisions if r not in parents]
    check(
        "MG",
        f"exactly one Alembic head, 0036 on its chain ({heads})",
        len(heads) == 1
        and "0036_model_role_assignments" in revisions
        and revisions["0036_model_role_assignments"] == "0035_dataset_mappings",
    )

    # contract
    import yaml

    doc = yaml.safe_load(INTERFACE_PATH.read_text())
    base = "/api/v1/organizations/{organization_id}/projects/{project_id}/model-versions/{model_version_id}"
    paths = doc["paths"]
    check(
        "CT",
        "the roles path declares GET and POST",
        set(paths[base + "/roles"]) == {"parameters", "get", "post"},
    )
    check(
        "CT",
        "the revoke path declares POST only",
        set(paths[base + "/roles/{role}/revoke"]) == {"parameters", "post"},
    )
    check(
        "CT",
        "the actions path declares GET only",
        set(paths[base + "/actions"]) == {"parameters", "get"},
    )
    check(
        "CT",
        "POST roles documents 200/201/400/401/403/404/409",
        set(paths[base + "/roles"]["post"]["responses"])
        == {"200", "201", "400", "401", "403", "404", "409"},
    )
    schemas = doc["components"]["schemas"]
    check(
        "CT",
        "ModelRole enum equals the module's roles",
        tuple(schemas["ModelRole"]["enum"]) == role_schema.ROLES,
    )
    check(
        "CT",
        "ModelActionName enum equals the module's actions",
        tuple(schemas["ModelActionName"]["enum"]) == ACTIONS,
    )
    request = schemas["ModelRoleAssignRequest"]
    check(
        "CT",
        "the request is closed with exactly role and idempotency_key",
        request["additionalProperties"] is False
        and set(request["properties"]) == {"role", "idempotency_key"}
        and sorted(request["required"]) == ["idempotency_key", "role"]
        and request["properties"]["idempotency_key"]["maxLength"] == 128
        and request["properties"]["idempotency_key"]["minLength"] == 1,
    )
    route_src = ROUTES_PATH.read_text()
    emitted = set(
        re.findall(
            r'^        "([a-z_]+)":',
            route_src.split("def _serialize(")[1].split("def _serialize_decision")[0],
            re.M,
        )
    )
    check(
        "CT",
        f"the assignment schema equals the route's emitted fields ({sorted(emitted ^ set(schemas['ModelRoleAssignment']['properties']))})",
        emitted == set(schemas["ModelRoleAssignment"]["properties"]),
    )
    check(
        "CT",
        "the assignment response is closed",
        schemas["ModelRoleAssignment"]["additionalProperties"] is False,
    )
    codes_emitted = set(
        re.findall(
            r'^CODE_[A-Z_]+ = "([a-z_]+)"',
            (ROOT / "workers" / "training" / "continuation.py").read_text(),
            re.M,
        )
    ) - {"unknown_action"}
    check(
        "CT",
        f"ModelActionCode equals the codes the module emits ({sorted(codes_emitted ^ set(schemas['ModelActionCode']['enum']))})",
        set(schemas["ModelActionCode"]["enum"]) == codes_emitted,
    )
    decision_props = set(schemas["ModelActionDecision"]["properties"])
    check(
        "CT",
        "the decision schema is closed over allowed, code, plan",
        decision_props == {"allowed", "code", "plan"}
        and schemas["ModelActionDecision"]["additionalProperties"] is False,
    )
    actions_doc = schemas["ModelVersionActions"]
    check(
        "CT",
        "the actions document names every action once",
        tuple(actions_doc["properties"]["actions"]["properties"]) == ACTIONS
        and tuple(actions_doc["properties"]["actions"]["required"]) == ACTIONS,
    )
    plan_fields = set(schemas["ModelContinuationPlan"]["properties"])
    import dataclasses

    from workers.training import continuation as cont

    check(
        "CT",
        "the plan schema covers every plan field",
        plan_fields
        == {f.name for f in dataclasses.fields(cont.NewRunPlan)}
        | {f.name for f in dataclasses.fields(cont.ExactResumePlan)},
    )
    main_src = MAIN_PATH.read_text()
    check(
        "CT",
        "the router is imported and mounted with the shared resolver and role lookup",
        "from app.model_versions.model_role_routes import build_model_role_router" in main_src
        and "build_model_role_router(\n            session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\n        )"
        in main_src
        and main_src.count("build_model_role_router") == 2,
    )


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


def create_app_suite() -> None:
    """The real, unmodified `create_app()` mounts the roles and actions routes (no database:
    `create_engine` is lazy and the role gate runs before any session is opened)."""
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
    check("WR", "create_app returns a real app", app is not None)
    vid = uuid.uuid4()
    base_path = f"/api/v1/organizations/{ORG}/projects/{PROJECT}/model-versions/{vid}"
    with serve(app) as b:
        for method, suffix, payload in (
            ("GET", "/roles", None),
            ("POST", "/roles", {"role": "baseline", "idempotency_key": "k"}),
            ("POST", "/roles/baseline/revoke", None),
            ("GET", "/actions", None),
        ):
            result = http_request(b, method, base_path + suffix, principal=None, payload=payload)
            check(
                "WR",
                f"{method} {suffix} is mounted on the real app (401, not 404)",
                result.status == 401,
            )


def db_free_groups() -> None:
    store_suite(real_crud)
    routes_suite(real_routes)
    create_app_suite()
    static_suite()


# Fault injection (H1): each (old, new) must make the behavioural suite fail.
FAULTS: list[tuple[str, str, str]] = [
    (
        "endpoint-continue-on-trainable-ref",
        'if facts.origin is ORIGIN_EXTERNAL_ENDPOINT:\n        return _deny("continue_tuning"',
        'if facts.origin is ORIGIN_EXTERNAL_ENDPOINT and not any(w in (facts.model_ref or "").lower() for w in ("lora", "ft:", "llama", "trainable", "teacher")):\n        return _deny("continue_tuning"',
    ),
    (
        "endpoint-continue-on-teacher-role",
        'if facts.origin is ORIGIN_EXTERNAL_ENDPOINT:\n        return _deny("continue_tuning"',
        'if facts.origin is ORIGIN_EXTERNAL_ENDPOINT and "teacher" not in facts.roles:\n        return _deny("continue_tuning"',
    ),
    (
        "imported-adapter-exact-resume",
        "if facts.origin is not ORIGIN_FACTORY_TRAINED or checkpoint is None:",
        "if facts.origin not in (ORIGIN_FACTORY_TRAINED, ORIGIN_IMPORTED_ADAPTER) or checkpoint is None:",
    ),
    (
        "imported-weights-exact-resume",
        "if facts.origin is not ORIGIN_FACTORY_TRAINED or checkpoint is None:",
        "if facts.origin is ORIGIN_EXTERNAL_ENDPOINT or checkpoint is None:",
    ),
    (
        "exact-resume-without-checkpoint-fact",
        "if facts.origin is not ORIGIN_FACTORY_TRAINED or checkpoint is None:",
        "if facts.origin is not ORIGIN_FACTORY_TRAINED:",
    ),
    ("revoked-license-ignored", "if facts.license_accepted is False:", "if False:"),
    ("base-incompatibility-ignored", "if facts.base_compatible is False:", "if False:"),
    (
        "unvalidated-row-given-actions",
        "if not facts.validated:\n        return _deny(action, CODE_IMPORT_NOT_VALIDATED)",
        "if False:\n        return _deny(action, CODE_IMPORT_NOT_VALIDATED)",
    ),
    (
        "unknown-prerequisite-allowed",
        "    except ValueError:\n        return None",
        "    except ValueError:\n        return PREREQUISITE_AVAILABLE",
    ),
    (
        "missing-prerequisite-allowed",
        "facts.prerequisites.get(_PREREQUISITE_OF[action])",
        "facts.prerequisites.get(_PREREQUISITE_OF[action], PREREQUISITE_AVAILABLE)",
    ),
    (
        "prerequisite-casefolded",
        "return _PREREQUISITE_STATES[_PREREQUISITE_STATES.index(state)]",
        "return _PREREQUISITE_STATES[_PREREQUISITE_STATES.index(state.strip().lower())]",
    ),
    (
        "prerequisite-str-subclass-allowed",
        "    if type(state) is not str:\n        return None",
        "    if False:\n        return None",
    ),
    (
        "resume-start-step-zero",
        "checkpoint_id=checkpoint.checkpoint_id, start_step=checkpoint.step, run_id=checkpoint.run_id",
        "checkpoint_id=checkpoint.checkpoint_id, start_step=0, run_id=checkpoint.run_id",
    ),
    ("new-run-start-step-nonzero", "start_step: int = 0", "start_step: int = 1"),
    (
        "new-run-keeps-optimizer-state",
        'optimizer_state: str = "none"',
        'optimizer_state: str = "carried"',
    ),
    ("new-run-shares-cost", "separate_cost: bool = True", "separate_cost: bool = False"),
    (
        "new-run-reuses-checkpoint-run-id",
        "plan = NewRunPlan(parent_model_version_id=facts.model_version_id, run_id=facts.new_run_id)",
        "plan = NewRunPlan(parent_model_version_id=facts.model_version_id, run_id=(facts.checkpoint.run_id if facts.checkpoint else facts.new_run_id))",
    ),
    ("incumbent-check-dropped", 'if "current_production" in facts.roles:', "if True:"),
    ("incumbent-by-any-role", 'if "current_production" in facts.roles:', "if facts.roles:"),
    (
        "retain-incumbent-not-a-use-action",
        '"retain_incumbent": True,',
        '"retain_incumbent": False,',
    ),
    (
        "base-before-license",
        "    if facts.license_accepted is False:\n        return _deny(action, CODE_LICENSE_NOT_ACCEPTED)\n    if facts.base_compatible is False:\n        return _deny(action, CODE_ADAPTER_BASE_INCOMPATIBLE)",
        "    if facts.base_compatible is False:\n        return _deny(action, CODE_ADAPTER_BASE_INCOMPATIBLE)\n    if facts.license_accepted is False:\n        return _deny(action, CODE_LICENSE_NOT_ACCEPTED)",
    ),
    (
        "run-id-collision-allowed",
        "if self.checkpoint.run_id == self.new_run_id:",
        "if False:",
    ),
    (
        "origin-casefolded",
        "return ORIGINS[ORIGINS.index(value)]",
        "return ORIGINS[ORIGINS.index(value.strip().lower())]",
    ),
    (
        "origin-str-subclass-allowed",
        '    if type(value) is not str:\n        raise ContinuationFactsError("origin_invalid")',
        '    if False:\n        raise ContinuationFactsError("origin_invalid")',
    ),
    (
        "roles-accept-any-string",
        "if type(role) is not str or role not in ROLES:",
        "if type(role) is not str:",
    ),
    (
        "decision-mapping-mutable",
        "return MappingProxyType({action: _decide(action, facts) for action in ACTIONS})",
        "return {action: _decide(action, facts) for action in ACTIONS}",
    ),
    (
        "unknown-action-allowed",
        "if type(action) is not str or action not in ACTIONS:",
        "if type(action) is not str:",
    ),
    (
        "error-text-leaks-action-detail",
        "super().__init__(code)",
        "super().__init__(f'{code}: {action}')",
    ),
    (
        "denied-decision-names-another-action",
        "return ActionDecision(action=action, allowed=False, code=code)",
        'return ActionDecision(action="evaluate", allowed=False, code=code)',
    ),
    (
        "compare-rule-is-evaluate",
        '"compare": _compare,',
        '"compare": _evaluate,',
    ),
]


# H1 for the store and the routes: each (id, old, new) patches a copy of the production module
# and the DB-free suite must go RED against it.
CRUD_FAULTS: list[tuple[str, str, str]] = [
    (
        "validation-check-dropped",
        '    if not is_validated(session, context, version):\n        raise RoleConflictError("import_not_validated")',
        '    if False:\n        raise RoleConflictError("import_not_validated")',
    ),
    (
        "replay-ignores-the-request-digest",
        "if not hmac.compare_digest(existing.request_sha256.encode(), digest.encode()):",
        "if False:",
    ),
    (
        "replay-always-reuse",
        "if not hmac.compare_digest(existing.request_sha256.encode(), digest.encode()):",
        "if True:",
    ),
    (
        "every-violation-is-role-already-assigned",
        "code = _CONFLICT_BY_NAME.get(name) if name is not None else None",
        'code = "role_already_assigned"',
    ),
    (
        "unmapped-violation-swallowed-as-conflict",
        "        if code is None:\n            raise\n",
        '        if code is None:\n            code = "role_already_assigned"\n',
    ),
    (
        "idempotency-name-casefolded",
        "if name == _IDEMPOTENCY_INDEX:",
        'if (name or "").strip().lower() == _IDEMPOTENCY_INDEX:',
    ),
    (
        "race-with-nothing-stored-swallowed",
        "            if raced is None:\n                raise\n",
        "            if raced is None:\n                return AssignmentResult(assignment=assignment, replayed=True)\n",
    ),
    (
        "audit-action-renamed",
        'action="model_role.assign",',
        'action="model_role.assigned",',
    ),
    (
        "assigned-by-not-the-actor",
        "assigned_by=actor.actor_id,",
        'assigned_by="someone",',
    ),
    (
        "revoke-without-row-lock",
        "        .with_for_update()\n",
        "",
    ),
    (
        "revoke-matches-revoked-rows",
        "            ModelRoleAssignment.revoked_at.is_(None),\n        )\n        .with_for_update()",
        "        )\n        .with_for_update()",
    ),
    (
        "revoke-reason-changed",
        "assignment.revoke_reason_code = REVOKE_REASON_BY_ACTOR",
        'assignment.revoke_reason_code = "x"',
    ),
    (
        "factory-trained-check-dropped",
        "if _origin_of(version) is ORIGIN_FACTORY_TRAINED:",
        "if True:",
    ),
    (
        "any-succeeded-job-validates-any-project",
        "        ModelImportJob.project_id == version.project_id,\n",
        "",
    ),
    (
        "job-state-ignored",
        '        ModelImportJob.state == "succeeded",\n',
        "",
    ),
    (
        "job-result-ignored",
        "        ModelImportJob.result_model_version_id == version.id,\n",
        "",
    ),
    (
        "license-gate-ignored",
        "return artifact is not None and evaluate_license_gate(artifact).accepted",
        "return True",
    ),
    (
        "missing-artifact-accepted",
        "return artifact is not None and evaluate_license_gate(artifact).accepted",
        "return artifact is None or evaluate_license_gate(artifact).accepted",
    ),
    (
        "lineage-failure-ignored",
        "    except ArtifactLineageError:\n        return False",
        "    except ArtifactLineageError:\n        return True",
    ),
    (
        "unvalidated-base-allowed",
        "    if not is_validated(session, context, parent):\n        return False",
        "    if False:\n        return False",
    ),
    (
        "base-license-ignored",
        "    if not _license_accepted(base_artifact):\n        return False",
        "    if False:\n        return False",
    ),
    (
        "base-origin-ignored",
        "if parent is None or _origin_of(parent) is not ORIGIN_IMPORTED_WEIGHTS:",
        "if parent is None:",
    ),
    (
        "parent-guard-removed",
        "    if version.parent_model_version_id is None or artifact is None:\n        return False",
        "    if False:\n        return False",
    ),
    (
        "revoked-roles-counted",
        "        ModelRoleAssignment.model_version_id == version.id,\n        ModelRoleAssignment.revoked_at.is_(None),\n",
        "        ModelRoleAssignment.model_version_id == version.id,\n",
    ),
    (
        "constant-run-id",
        "new_run_id=str(uuid.uuid4()),",
        "new_run_id=str(version.id),",
    ),
    (
        "endpoint-ref-always-passed",
        "model_ref=version.external_provider_model_ref if is_endpoint else None,",
        "model_ref=version.external_provider_model_ref,",
    ),
]

ROUTES_FAULTS: list[tuple[str, str, str]] = [
    (
        "admin-only-rule-dropped",
        "if is_admin_only(role) and ctx.role is not Role.ORGANIZATION_ADMIN:",
        "if False:",
    ),
    (
        "ml-engineer-may-assign-admin-roles",
        "if is_admin_only(role) and ctx.role is not Role.ORGANIZATION_ADMIN:",
        "if is_admin_only(role) and ctx.role not in (Role.ORGANIZATION_ADMIN, Role.ML_ENGINEER):",
    ),
    (
        "exact-keys-check-dropped",
        "if set(payload) != _POST_BODY_KEYS:",
        "if False:",
    ),
    (
        "extra-keys-allowed",
        "if set(payload) != _POST_BODY_KEYS:",
        "if not set(payload) >= _POST_BODY_KEYS:",
    ),
    (
        "role-casefolded",
        'role = validate_role(payload["role"])',
        'role = validate_role(payload["role"].strip().lower())',
    ),
    (
        "non-str-role-accepted",
        '            if type(payload["role"]) is not str:\n                raise ModelRoleValidationError("invalid_body")\n',
        "",
    ),
    (
        "replay-reported-as-created",
        "status_code=200 if result.replayed else 201",
        "status_code=201",
    ),
    (
        "creation-reported-as-replay",
        "status_code=200 if result.replayed else 201",
        "status_code=200",
    ),
    (
        "revoke-body-rule-dropped",
        "if raw and not raw.isspace() and await _read_object(request) != {}:",
        "if False:",
    ),
    (
        "revoke-path-role-unchecked",
        "checked_role = validate_role(role)",
        "checked_role = role",
    ),
    (
        "model-facts-error-unmapped",
        '        except ContinuationFactsError as exc:\n            raise HTTPException(status_code=409, detail="model_facts_invalid") from exc',
        "        except ContinuationFactsError:\n            raise",
    ),
    (
        "assign-open-to-read-roles",
        "        model_version_id: uuid.UUID,\n        ctx: AuthorizedContext = write_dependency,\n    ) -> JSONResponse:\n        del organization_id\n        payload = await _read_object(request)",
        "        model_version_id: uuid.UUID,\n        ctx: AuthorizedContext = read_dependency,\n    ) -> JSONResponse:\n        del organization_id\n        payload = await _read_object(request)",
    ),
    (
        "revoked-by-dropped-from-the-body",
        '        "revoked_by": assignment.revoked_by,\n',
        "",
    ),
    (
        "id-not-the-assignment-id",
        '        "id": str(assignment.id),',
        '        "id": str(assignment.model_version_id),',
    ),
    (
        "actor-type-changed",
        'actor_type="user", actor_id=ctx.principal.subject',
        'actor_type="system", actor_id=ctx.principal.subject',
    ),
    (
        "plan-not-serialized",
        '"plan": None if plan is None else dict(vars(plan)),',
        '"plan": None,',
    ),
    (
        "actions-key-order-changed",
        'body: dict[str, object] = {\n                    "model_version_id": str(version.id),',
        'body: dict[str, object] = {\n                    "model_version_id": str(version.project_id),',
    ),
]


def run_suite(path: Path, name: str) -> list[str]:
    _failures.clear()
    module = load_module(path, name)
    suite(module, path.read_text(encoding="utf-8"))
    return list(_failures)


def main() -> int:
    if not MODULE_PATH.exists():
        print(f"FAIL [{CID}] module missing: {MODULE_PATH.relative_to(ROOT)}")
        return 1
    failures = run_suite(MODULE_PATH, "continuation_under_test")
    if failures:
        print("\n".join(failures[:60]))
        print(f"{len(failures)} failure(s) of {CHECK_COUNT[0]} checks")
        return 1
    _failures.clear()
    db_free_groups()
    if _failures:
        print("\n".join(_failures[:60]))
        print(f"{len(_failures)} failure(s) of {CHECK_COUNT[0]} checks")
        return 1
    if not BEHAVIOUR_ONLY:
        import tempfile

        for label, path, table, runner in (
            ("crud", CRUD_PATH, CRUD_FAULTS, lambda module: store_suite(module)),
            ("routes", ROUTES_PATH, ROUTES_FAULTS, lambda module: routes_suite(module)),
        ):
            module_source = path.read_text(encoding="utf-8")
            with tempfile.TemporaryDirectory() as tmp_dir:
                for index, (fid, old, new) in enumerate(table):
                    if module_source.count(old) != 1:
                        print(
                            f"FAIL [H1] {label}:{fid}: patch text occurs "
                            f"{module_source.count(old)} time(s), expected 1"
                        )
                        return 1
                    copy_path = Path(tmp_dir) / f"{label}_fault_{index}.py"
                    copy_path.write_text(module_source.replace(old, new), encoding="utf-8")
                    _failures.clear()
                    try:
                        runner(load_module(copy_path, f"{label}_fault_{index}"))
                    except Exception:  # noqa: BLE001
                        _failures.append("raised")
                    CHECK_COUNT[0] += 1
                    if not _failures:
                        print(f"FAIL [H1] {label} fault {fid} stayed GREEN")
                        return 1
        _failures.clear()

        source = MODULE_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            for index, (fid, old, new) in enumerate(FAULTS):
                if source.count(old) != 1:
                    print(
                        f"FAIL [H1] {fid}: patch text occurs {source.count(old)} time(s), expected 1"
                    )
                    return 1
                copy = Path(tmp) / f"fault_{index}.py"
                copy.write_text(source.replace(old, new), encoding="utf-8")
                try:
                    red = bool(run_suite(copy, f"fault_{index}"))
                except Exception:  # noqa: BLE001
                    red = True
                CHECK_COUNT[0] += 1
                if not red:
                    print(f"FAIL [H1] fault {fid} stayed GREEN")
                    return 1
    print(f"OK {CID}: {CHECK_COUNT[0]} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
