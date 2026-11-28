#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg"
# ruff: noqa: E501
"""FTF-H010-P6 -- model version panel: contract-parity (presence) tests.

The panel is proven by `apps/web/tests/ftf_h010_p6.spec.ts` (jsdom),
`ftf_h010_p6_browser.spec.ts` (real Chromium) and the real-API job
(`tests/integration/test_ftf_h010_p6_ui_real_api.py`). This file pins what only the Python side can
know:

  P6-CODES   every `CODE_*` constant of workers/training/continuation.py and every error code the
             FTF-H010-P4 role/action routes can emit (`detail=` literals, `RoleConflictError`,
             `ModelRoleValidationError`, the violated-index map) has an `en` and an `ar` entry in
             the component's REASON_MESSAGES (a missing code or language is RED).
  P6-CONST   MODEL_ROLES and MODEL_ACTIONS equal `model_role_schema.ROLES` and
             `continuation.ACTIONS`, in order.
  P6-ITEM    the FTF-1001 item serializer carries the lineage fields the panel reads (origin,
             lineage_completeness, parent_model_version_id, external_provider_model_ref,
             observed_deployment_fingerprint) verbatim, and an endpoint version carries no digest.
  P6-NEG     the panel source has no effect route for any action (no fetch to an action path and no
             `/actions/` POST), and no hardcoded digest-shaped literal.
  P6-SELF    each check above turns RED on an in-memory fault of the component source.

Run directly: python3 tests/tasks/test_ftf_h010_p6.py
"""

from __future__ import annotations

import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from workers.training import continuation  # noqa: E402

from app.model_versions import model_role_crud, model_role_routes, model_role_schema  # noqa: E402
from app.model_versions.model_version_routes import _serialize  # noqa: E402
from app.model_versions.model_version_schema import ModelVersion  # noqa: E402

COMPONENT = ROOT / "apps/web/components/model-import/model_version_panel_ui.tsx"
FAILURES: list[str] = []
CID = "FTF-H010-P6"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def backend_codes() -> set[str]:
    codes = {
        value
        for name, value in vars(continuation).items()
        if name.startswith("CODE_") and isinstance(value, str)
    }
    for module in (model_role_routes, model_role_crud, model_role_schema):
        src = Path(module.__file__).read_text()
        codes.update(re.findall(r'detail="([a-z_]+)"', src))
        codes.update(re.findall(r'RoleConflictError\("([a-z_]+)"\)', src))
        codes.update(re.findall(r'ModelRoleValidationError\("([a-z_]+)"\)', src))
    codes.update(v for v in model_role_crud._CONFLICT_BY_NAME.values())
    codes.discard(model_role_crud._IDEMPOTENCY_RACE)  # internal: always replayed, never emitted
    return codes


def reason_table(src: str) -> dict[str, tuple[bool, bool]]:
    """code -> (has_en, has_ar), read from the `REASON_MESSAGES` literal."""
    start = src.index("export const REASON_MESSAGES")
    end = src.index("\n};", start)
    body = src[start:end]
    out: dict[str, tuple[bool, bool]] = {}
    for m in re.finditer(r"^\s{2}([a-z_]+): \{(.*?)\},\s*$", body, re.S | re.M):
        entry = m.group(2)
        en = re.search(r'\ben: "[^"]+"', entry) is not None
        ar = re.search(r'\bar: "[^"]*[؀-ۿ][^"]*"', entry) is not None
        out[m.group(1)] = (en, ar)
    return out


def code_problems(src: str) -> list[str]:
    table = reason_table(src)
    problems = []
    for code in sorted(backend_codes()):
        has = table.get(code)
        if has is None:
            problems.append(f"{code}: no entry")
        elif not has[0]:
            problems.append(f"{code}: no en text")
        elif not has[1]:
            problems.append(f"{code}: no ar text")
    return problems


def array(src: str, name: str) -> list[str]:
    m = re.search(rf"export const {name}(?:: [^=]+)? =\s*\[(.*?)\]", src, re.S)
    return re.findall(r'"([a-z_]+)"', m.group(1)) if m else []


def const_problems(src: str) -> list[str]:
    problems = []
    if array(src, "MODEL_ROLES") != list(model_role_schema.ROLES):
        problems.append("MODEL_ROLES differs from model_role_schema.ROLES")
    if array(src, "MODEL_ACTIONS") != list(continuation.ACTIONS):
        problems.append("MODEL_ACTIONS differs from continuation.ACTIONS")
    return problems


def negative_problems(src: str) -> list[str]:
    problems = []
    code_only = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code_only = re.sub(r"^\s*//.*$", "", code_only, flags=re.M)
    for action in continuation.ACTIONS:
        if re.search(rf"[`/\"']{action}[`/\"']?\s*\)?\s*,\s*\{{\s*method", code_only):
            problems.append(f"an effect request for {action}")
    if re.search(r"actionsUrl\([^)]*\)\s*,\s*\{\s*method", code_only):
        problems.append("a write to the actions route")
    if re.search(r"[0-9a-f]{64}|repeat\(64\)", code_only):
        problems.append("a digest-shaped literal")
    return problems


def test_codes() -> None:
    src = COMPONENT.read_text()
    problems = code_problems(src)
    check("P6-CODES", f"every backend reason/error code has en+ar text ({problems})", not problems)
    check(
        "P6-CODES",
        "the backend code set is non-trivial (continuation + route codes were read)",
        {"endpoint_not_trainable", "incumbent_exists", "forbidden", "role_not_assigned"}
        <= backend_codes(),
    )


def test_consts() -> None:
    problems = const_problems(COMPONENT.read_text())
    check("P6-CONST", f"roles/actions equal the backend ({problems})", not problems)


def test_negative() -> None:
    problems = negative_problems(COMPONENT.read_text())
    check("P6-NEG", f"no effect route and no placeholder digest ({problems})", not problems)


def _version(**kw: object) -> ModelVersion:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "version_number": 1,
        "created_by": "seed",
        "created_at": datetime.now(UTC),
    }
    base.update(kw)
    return ModelVersion(**base)


def test_item_serializer() -> None:
    endpoint = _version(
        import_kind="endpoint",
        origin="external_endpoint",
        external_provider_model_ref="ft:acme:teacher-lora",
        observed_deployment_fingerprint="fp_1",
        lineage_completeness="unknown",
    )
    body = _serialize(endpoint)
    check(
        "P6-ITEM",
        f"the endpoint item carries origin/lineage/ref/fingerprint verbatim ({body})",
        body.get("origin") == "external_endpoint"
        and body.get("lineage_completeness") == "unknown"
        and body.get("external_provider_model_ref") == "ft:acme:teacher-lora"
        and body.get("observed_deployment_fingerprint") == "fp_1"
        and body.get("parent_model_version_id") is None
        and body.get("weights_digest") is None,
    )
    parent = uuid.uuid4()
    adapter = _version(
        import_kind="adapter",
        origin="imported_adapter",
        adapter_only=True,
        parent_model_version_id=parent,
        trainable_artifact_id=uuid.uuid4(),
        weights_digest="sha256:" + "d" * 64,
        base_model_revision="a1b2c3d4e5f6",
        lineage_completeness="partial",
    )
    body = _serialize(adapter)
    check(
        "P6-ITEM",
        "the adapter item carries its parent id as a string and its digest",
        body.get("parent_model_version_id") == str(parent)
        and body.get("weights_digest") == "sha256:" + "d" * 64
        and body.get("adapter_only") is True
        and body.get("lineage_completeness") == "partial",
    )
    for key in (
        "id",
        "version_number",
        "import_kind",
        "trainable_artifact_id",
        "endpoint_reference",
    ):
        check("P6-ITEM", f"the FTF-1001 key {key} is still present (additive change)", key in body)


def test_self_faults() -> None:
    src = COMPONENT.read_text()
    faults = [
        (
            "drop the endpoint_not_trainable entry",
            lambda s: re.sub(
                r"\n  endpoint_not_trainable: \{.*?\n  \},", "", s, count=1, flags=re.S
            ),
            code_problems,
        ),
        (
            "blank the ar text of forbidden",
            lambda s: re.sub(
                r'(\n  forbidden: \{.*?ar: ")[^"]+(")', r"\1Forbidden\2", s, count=1, flags=re.S
            ),
            code_problems,
        ),
        (
            "drop the judge role",
            lambda s: s.replace('  "judge",\n] as const;', "] as const;", 1),
            const_problems,
        ),
        (
            "reorder actions",
            lambda s: s.replace('  "evaluate",\n  "compare",', '  "compare",\n  "evaluate",', 1),
            const_problems,
        ),
        (
            "a POST to an action route",
            lambda s: s + '\nvoid send(`${base}/continue_tuning`, { method: "POST" });\n',
            negative_problems,
        ),
        (
            "a placeholder digest",
            lambda s: s + '\nconst PLACEHOLDER = "sha256:" + "0".repeat(64);\n',
            negative_problems,
        ),
    ]
    for name, mutate, probe in faults:
        mutated = mutate(src)
        check("P6-SELF", f"the fault '{name}' changes the source", mutated != src)
        check("P6-SELF", f"the fault '{name}' is caught", bool(probe(mutated)))


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"{CID} contract-parity tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"{CID} contract-parity tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
