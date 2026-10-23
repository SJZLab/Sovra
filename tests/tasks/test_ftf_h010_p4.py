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
            "",
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

    # ---- D1 -------------------------------------------------------------------------------
    class S(str):
        pass

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


# Fault injection (H1): each (old, new) must make the behavioural suite fail.
FAULTS: list[tuple[str, str, str]] = [
    (
        "endpoint-continue-on-trainable-ref",
        'if facts.origin == ORIGIN_EXTERNAL_ENDPOINT:\n        return _deny("continue_tuning"',
        'if facts.origin == ORIGIN_EXTERNAL_ENDPOINT and not any(w in (facts.model_ref or "").lower() for w in ("lora", "ft:", "llama", "trainable", "teacher")):\n        return _deny("continue_tuning"',
    ),
    (
        "endpoint-continue-on-teacher-role",
        'if facts.origin == ORIGIN_EXTERNAL_ENDPOINT:\n        return _deny("continue_tuning"',
        'if facts.origin == ORIGIN_EXTERNAL_ENDPOINT and "teacher" not in facts.roles:\n        return _deny("continue_tuning"',
    ),
    (
        "imported-adapter-exact-resume",
        "if facts.origin != ORIGIN_FACTORY_TRAINED or checkpoint is None:",
        "if facts.origin not in (ORIGIN_FACTORY_TRAINED, ORIGIN_IMPORTED_ADAPTER) or checkpoint is None:",
    ),
    (
        "imported-weights-exact-resume",
        "if facts.origin != ORIGIN_FACTORY_TRAINED or checkpoint is None:",
        "if facts.origin == ORIGIN_EXTERNAL_ENDPOINT or checkpoint is None:",
    ),
    (
        "exact-resume-without-checkpoint-fact",
        "if facts.origin != ORIGIN_FACTORY_TRAINED or checkpoint is None:",
        "if facts.origin != ORIGIN_FACTORY_TRAINED:",
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
        "if type(state) is str and state == PREREQUISITE_AVAILABLE:",
        'if state != "unavailable":',
    ),
    (
        "prerequisite-casefolded",
        "if type(state) is str and state == PREREQUISITE_AVAILABLE:",
        "if type(state) is str and state.casefold().strip() == PREREQUISITE_AVAILABLE:",
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
        '        "retain_incumbent",\n        "deploy_route",\n    }',
        '        "deploy_route",\n    }',
    ),
    (
        "base-before-license",
        "        if facts.license_accepted is False:\n            return _deny(action, CODE_LICENSE_NOT_ACCEPTED)\n        if facts.base_compatible is False:\n            return _deny(action, CODE_ADAPTER_BASE_INCOMPATIBLE)",
        "        if facts.base_compatible is False:\n            return _deny(action, CODE_ADAPTER_BASE_INCOMPATIBLE)\n        if facts.license_accepted is False:\n            return _deny(action, CODE_LICENSE_NOT_ACCEPTED)",
    ),
    ("run-id-collision-allowed", "if self.checkpoint.run_id == self.new_run_id:", "if False:"),
    (
        "origin-casefolded",
        "if type(self.origin) is not str or self.origin not in ORIGINS:",
        "if type(self.origin) is not str or self.origin.strip().lower() not in ORIGINS:",
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
    if not BEHAVIOUR_ONLY:
        import tempfile

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
