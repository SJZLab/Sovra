#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg"
"""FTF-H010-P5 -- import wizard UI: contract-parity (presence) tests.

The wizard itself is proven by `apps/web/tests/ftf_h010_p5.spec.ts` (jsdom),
`ftf_h010_p5_browser.spec.ts` (real Chromium) and the real-API job. This file pins what only the
Python side can know -- that the wizard's own tables cover every value the certified P1/P2/P3
backend can emit, and stay equal to the backend's constants:

  P5-CODES   every `CODE_*` constant of model_import_validation.py,
             model_import_weights_validation.py and import_artifact_validators.py, plus the P1 route error codes, has an `en` and an
             `ar` entry in the component's FAILURE_MESSAGES (a missing code is RED).
  P5-CONST   origins, job states, ref length cap and the write/admin role sets equal the backend's.
  P5-NEG     the component never reads a connection's `base_url`, and the endpoint branch has no
             digest/hash/artifact input.

Run directly: python3 tests/tasks/test_ftf_h010_p5.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.litellm.connection_routes import CONNECTION_ADMIN_ROLES  # noqa: E402
from app.model_versions import (  # noqa: E402
    import_artifact_validators,
    model_import_schema,
    model_import_validation,
    model_import_weights_validation,
)
from app.model_versions.model_version_routes import MODEL_VERSION_WRITE_ROLES  # noqa: E402

COMPONENT = ROOT / "apps/web/components/model-import/import_model_wizard_ui.tsx"
FAILURES: list[str] = []
CID = "FTF-H010-P5"

# Codes the P1 routes emit as `detail` that the wizard maps, plus a documented alias.
ROUTE_CODES = ("invalid_body", "not_found")
ALIAS_CODES = ("adapter_base_incompatible",)


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def backend_codes() -> list[str]:
    codes: list[str] = []
    for module in (
        model_import_validation,
        model_import_weights_validation,
        import_artifact_validators,
    ):
        for name, value in vars(module).items():
            if name.startswith("CODE_") and isinstance(value, str):
                codes.append(value)
    return codes


def failure_table(src: str) -> dict[str, tuple[bool, bool]]:
    """code -> (has_en, has_ar), read from the `FAILURE_MESSAGES` literal."""
    start = src.index("export const FAILURE_MESSAGES")
    end = src.index("\n};", start)
    body = src[start:end]
    out: dict[str, tuple[bool, bool]] = {}
    for m in re.finditer(r"^\s{2}([a-z_]+): \{(.*?)\},\s*$", body, re.S | re.M):
        entry = m.group(2)
        en = re.search(r'\ben: "[^"]+"', entry) is not None
        ar = re.search(r'\bar: "[^"]+"', entry) is not None
        out[m.group(1)] = (en, ar)
    return out


def code_problems(src: str) -> list[str]:
    table = failure_table(src)
    problems = []
    for code in sorted({*backend_codes(), *ROUTE_CODES, *ALIAS_CODES}):
        has = table.get(code)
        if has is None:
            problems.append(f"{code}: no entry")
        elif not has[0]:
            problems.append(f"{code}: no en text")
        elif not has[1]:
            problems.append(f"{code}: no ar text")
    return problems


def const_problems(src: str) -> list[str]:
    problems = []

    def array(name: str) -> list[str]:
        m = re.search(rf"export const {name}(?:: [^=]+)? =\s*\[(.*?)\]", src, re.S)
        return re.findall(r'"([a-z_]+)"', m.group(1)) if m else []

    if sorted(array("IMPORT_ORIGINS")) != sorted(model_import_schema.ALLOWED_ORIGINS):
        problems.append("IMPORT_ORIGINS differs from the backend")
    if sorted(array("IMPORT_JOB_STATES")) != sorted(model_import_schema.ALLOWED_JOB_STATES):
        problems.append("IMPORT_JOB_STATES differs from the backend")
    if sorted(array("WRITE_ROLES")) != sorted(r.value for r in MODEL_VERSION_WRITE_ROLES):
        problems.append("WRITE_ROLES differs from MODEL_VERSION_WRITE_ROLES")
    if sorted(array("ADMIN_ROLES")) != sorted(r.value for r in CONNECTION_ADMIN_ROLES):
        problems.append("ADMIN_ROLES differs from CONNECTION_ADMIN_ROLES")
    m = re.search(r"export const MAX_REF_LENGTH = (\d+)", src)
    if not m or int(m.group(1)) != model_import_schema.EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH:
        problems.append("MAX_REF_LENGTH differs from the backend")
    return problems


def negative_problems(src: str) -> list[str]:
    problems = []
    code_only = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code_only = re.sub(r"^\s*//.*$", "", code_only, flags=re.M)
    # base_url may only appear in the deliberate "never read" comment, which is stripped above.
    if re.search(r"\bbase_url\b", code_only):
        problems.append("base_url is referenced in code")
    if re.search(r"artifact_sha256|endpoint_reference", code_only):
        problems.append("a digest/reference column of the endpoint branch is referenced")
    return problems


def test_p5_codes() -> None:
    src = COMPONENT.read_text()
    check(CID + "-CODES", "backend exposes failure codes", len(set(backend_codes())) >= 27)
    check(CID + "-CODES", f"every code has en+ar: {code_problems(src)}", not code_problems(src))
    # Faults: a missing entry, a missing en, a missing ar must each be detected.
    first = backend_codes()[0]
    check(
        CID + "-CODES",
        "FAULT missing entry is detected",
        bool(code_problems(re.sub(rf"^\s{{2}}{first}: \{{.*?\}},\s*$", "", src, flags=re.S | re.M, count=1))),
    )
    check(
        CID + "-CODES",
        "FAULT missing ar is detected",
        bool(code_problems(re.sub(rf'({first}: \{{ en: "[^"]+",) ar: "[^"]+"', r"\1", src, count=1))),
    )
    check(
        CID + "-CODES",
        "FAULT missing en is detected",
        bool(code_problems(re.sub(rf'({first}: \{{) en: "[^"]+",', r"\1", src, count=1))),
    )


def test_p5_const() -> None:
    src = COMPONENT.read_text()
    check(CID + "-CONST", f"constants equal the backend: {const_problems(src)}", not const_problems(src))
    check(
        CID + "-CONST",
        "FAULT drifted origin list is detected",
        bool(const_problems(src.replace('"imported_adapter"', '"imported_adaptor"', 1))),
    )
    check(
        CID + "-CONST",
        "FAULT drifted ref cap is detected",
        bool(const_problems(src.replace("MAX_REF_LENGTH = 2048", "MAX_REF_LENGTH = 4096"))),
    )
    check(
        CID + "-CONST",
        "FAULT drifted admin role is detected",
        bool(const_problems(src.replace('ADMIN_ROLES: readonly string[] = ["organization_admin"]', 'ADMIN_ROLES: readonly string[] = ["ml_engineer"]'))),
    )


def test_p5_negative() -> None:
    src = COMPONENT.read_text()
    check(CID + "-NEG", f"no base_url / digest column read: {negative_problems(src)}", not negative_problems(src))
    check(CID + "-NEG", "FAULT base_url read is detected", bool(negative_problems(src + "\nconst x = c.base_url;")))
    check(CID + "-NEG", "FAULT artifact_sha256 read is detected", bool(negative_problems(src + "\nconst y = j.artifact_sha256;")))


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    if FAILURES:
        print(f"{len(FAILURES)} failing check(s):")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print(f"OK -- {len(tests)} test functions, 0 failing checks ({CID})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
