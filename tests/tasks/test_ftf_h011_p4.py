#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined"
# ruff: noqa: E501
"""FTF-H011-P4 -- dataset mapping wizard UI: contract parity and fault injection.

The wizard is proven by `apps/web/tests/ftf_h011.spec.ts` (jsdom),
`apps/web/tests/ftf_h011_browser.spec.ts` (real Chromium) and the real-API job
(`tests/integration/test_ftf_h011_ui_real_api.py`). This file pins what only the Python side can
know, and shows that the jsdom spec is able to fail:

  P4-CODES  every refusal code of the certified FTF-H011-P2/P3 backend -- the store's `CODES`,
            the request store's `CODES`, every literal `detail="..."` of dataset_mapping_routes.py
            and the RBAC dependency's codes -- has an `en` AND an `ar` entry in the component's
            ERROR_MESSAGES (a missing entry / en / ar is RED).
  P4-CONST  CSV_ENCODINGS / CSV_DELIMITERS equal csv_importer's, PURPOSES equals
            ALLOWED_PURPOSES, MAX_RATIONALE / LIST_LIMIT equal the route caps, ROLE_TARGETS /
            METADATA_SLOTS / MAPPING_STATUSES equal the 05 contract's enums.
  P4-NEG    the component has no eval / Function / innerHTML / dangerouslySetInnerHTML and never
            touches localStorage / sessionStorage / indexedDB (Section 67.2 / 72.2); a fault of each
            is detected.
  P4-FAULT  fault-injected copies of the component make the jsdom spec RED, one behavioural test
            per fault (the spec is run with `vitest -t` against each copy, then the source is
            restored byte for byte). Skipped only when `--no-vitest` is passed.

Run directly: python3 tests/tasks/test_ftf_h011_p4.py
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from workers.data import csv_importer  # noqa: E402

from app.datasets import dataset_mapping_request_store, dataset_mapping_routes  # noqa: E402
from app.datasets.dataset_mapping_store import CODES as STORE_CODES  # noqa: E402
from app.datasets.dataset_source_connection_schema import ALLOWED_PURPOSES  # noqa: E402

COMPONENT = ROOT / "apps/web/components/dataset-factory/dataset_mapping_wizard_ui.tsx"
ROUTES = ROOT / "apps/api/app/datasets/dataset_mapping_routes.py"
RBAC = ROOT / "apps/api/app/auth/rbac_middleware.py"
CONTRACT = ROOT / "packages/contracts/openapi/05-interface.yaml"
SPEC = "apps/web/tests/ftf_h011.spec.ts"
FAILURES: list[str] = []
CID = "FTF-H011-P4"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# ------------------------------------------------------------------------------------ codes
def backend_codes() -> list[str]:
    codes = set(STORE_CODES) | set(dataset_mapping_request_store.CODES)
    for path in (ROUTES, RBAC):
        codes.update(re.findall(r'detail="([a-z_]+)"', path.read_text()))
    return sorted(codes)


def error_table(src: str) -> dict[str, tuple[bool, bool]]:
    start = src.index("export const ERROR_MESSAGES")
    end = src.index("\n};", start)
    body = src[start:end]
    out: dict[str, tuple[bool, bool]] = {}
    for m in re.finditer(r"^\s{2}([a-z_]+): \{(.*?)\},\s*$", body, re.S | re.M):
        entry = m.group(2)
        out[m.group(1)] = (
            re.search(r'\ben:\s*"[^"]+"', entry) is not None,
            re.search(r'\bar:\s*"[^"]+"', entry) is not None,
        )
    return out


def code_problems(src: str) -> list[str]:
    table = error_table(src)
    problems = []
    for code in backend_codes():
        has = table.get(code)
        if has is None:
            problems.append(f"{code}: no entry")
        elif not has[0]:
            problems.append(f"{code}: no en text")
        elif not has[1]:
            problems.append(f"{code}: no ar text")
    return problems


def test_p4_codes() -> None:
    src = COMPONENT.read_text()
    codes = backend_codes()
    check(CID + "-CODES", f"the backend exposes its codes ({len(codes)})", len(codes) >= 35)
    for code in (
        "not_authenticated",
        "role_not_authorized",
        "storage_unavailable",
        "importer_unavailable",
    ):
        check(CID + "-CODES", f"{code} is one of the collected backend codes", code in codes)
    check(CID + "-CODES", f"every code has en+ar: {code_problems(src)}", not code_problems(src))
    first = "purpose_not_authorized"
    check(
        CID + "-CODES",
        "FAULT a missing entry is detected",
        bool(
            code_problems(
                re.sub(rf"^\s{{2}}{first}: \{{.*?\}},\s*$\n", "", src, flags=re.S | re.M, count=1)
            )
        ),
    )
    check(
        CID + "-CODES",
        "FAULT a missing ar is detected",
        bool(
            code_problems(
                re.sub(rf'({first}: \{{\s*en:\s*"[^"]+",)\s*ar:\s*"[^"]+"', r"\1", src, count=1)
            )
        ),
    )
    check(
        CID + "-CODES",
        "FAULT a missing en is detected",
        bool(code_problems(re.sub(rf'({first}: \{{)\s*en:\s*"[^"]+",', r"\1", src, count=1))),
    )


# ------------------------------------------------------------------------------------ constants
def ts_array(src: str, name: str) -> list[str]:
    m = re.search(rf"export const {name}(?:: [^=]+)? =\s*\[(.*?)\](?: as const)?;", src, re.S)
    if not m:
        return []
    return [
        bytes(v, "utf-8").decode("unicode_escape")
        for v in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    ]


def ts_int(src: str, name: str) -> int | None:
    m = re.search(rf"export const {name} = (\d+);", src)
    return int(m.group(1)) if m else None


def const_problems(src: str) -> list[str]:
    contract = yaml.safe_load(CONTRACT.read_text())["components"]["schemas"]
    problems = []
    pairs = [
        ("CSV_ENCODINGS", list(csv_importer.CSV_ENCODINGS)),
        ("CSV_DELIMITERS", list(csv_importer.CSV_DELIMITERS)),
        ("PURPOSES", list(ALLOWED_PURPOSES)),
        ("ROLE_TARGETS", sorted(contract["DatasetMappingRoleMap"]["additionalProperties"]["enum"])),
        ("METADATA_SLOTS", sorted(contract["DatasetMappingMetadata"]["properties"])),
        ("MAPPING_STATUSES", sorted(contract["DatasetMapping"]["properties"]["status"]["enum"])),
        (
            "VERDICT_STATES",
            sorted(contract["DatasetMappingPreview"]["properties"]["state"]["enum"]),
        ),
        (
            "ROW_OUTCOMES",
            sorted(contract["DatasetMappingPreviewRow"]["properties"]["outcome"]["enum"]),
        ),
    ]
    for name, expected in pairs:
        got = ts_array(src, name)
        ordered = name in {"CSV_ENCODINGS", "CSV_DELIMITERS", "PURPOSES"}
        if (got if ordered else sorted(got)) != expected:
            problems.append(f"{name} {got} differs from the backend {expected}")
    if ts_int(src, "MAX_RATIONALE") != dataset_mapping_routes._MAX_RATIONALE:
        problems.append("MAX_RATIONALE differs from the route cap")
    if ts_int(src, "LIST_LIMIT") != dataset_mapping_routes._MAX_LIMIT:
        problems.append("LIST_LIMIT differs from the route's maximum page size")
    return problems


def test_p4_const() -> None:
    src = COMPONENT.read_text()
    check(
        CID + "-CONST",
        f"constants equal the backend: {const_problems(src)}",
        not const_problems(src),
    )
    check(
        CID + "-CONST",
        "FAULT a drifted encoding is detected",
        bool(const_problems(src.replace('"windows-1256"', '"windows-1252"', 1))),
    )
    check(
        CID + "-CONST",
        "FAULT a drifted role target is detected",
        bool(
            const_problems(
                src.replace(
                    'ROLE_TARGETS = ["user", "assistant"', 'ROLE_TARGETS = ["user", "agent"', 1
                )
            )
        ),
    )
    check(
        CID + "-CONST",
        "FAULT a drifted rationale cap is detected",
        bool(const_problems(src.replace("MAX_RATIONALE = 2000;", "MAX_RATIONALE = 4000;", 1))),
    )


# ------------------------------------------------------------------------------------ negatives
def code_only(src: str) -> str:
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", out, flags=re.M)


def negative_problems(src: str) -> list[str]:
    body = code_only(src)
    problems = []
    for pattern, what in (
        (r"\beval\s*\(", "eval"),
        (r"\bnew\s+Function\b|\bFunction\s*\(", "Function constructor"),
        (
            r"dangerouslySetInnerHTML|\.innerHTML\b|\.outerHTML\b|insertAdjacentHTML",
            "HTML injection",
        ),
        (r"\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b|document\.cookie", "browser storage"),
    ):
        if re.search(pattern, body):
            problems.append(what)
    return problems


def test_p4_negative() -> None:
    src = COMPONENT.read_text()
    check(
        CID + "-NEG", f"no eval/HTML/storage: {negative_problems(src)}", not negative_problems(src)
    )
    for fault in (
        "\nconst a = eval(x);",
        "\nconst b = new Function('return 1');",
        "\nconst c = <div dangerouslySetInnerHTML={{ __html: v }} />;",
        "\nlocalStorage.setItem('draft', JSON.stringify(d));",
    ):
        check(
            CID + "-NEG",
            f"FAULT {fault.strip()[:30]} is detected",
            bool(negative_problems(src + fault)),
        )


# ------------------------------------------------------------------------------------ fault injection
# (name, old, new, spec test-name pattern that must go RED)
FAULTS: list[tuple[str, str, str, str]] = [
    (
        "parse: approval columns not checked",
        "if (approved ? columns.some((c) => c === null) : columns.some((c) => c !== null)) return null;",
        "",
        "approval columns must be all set",
    ),
    (
        "bind: the returned document is not compared",
        "return canonicalJson(row.mappingDocument) === canonicalJson(expect.document);",
        "return true;",
        "whose document differs|does not bind",
    ),
    (
        "identity: the remount key drops the dataset",
        "key={`${props.apiBaseUrl}|${props.organizationId}|${props.projectId}|${props.datasetId}`}",
        "key={`${props.apiBaseUrl}|${props.organizationId}|${props.projectId}`}",
        "a new dataset id remounts",
    ),
    (
        "malformed probe 2xx shown as a result",
        "const parsed = r.ok && !r.unreadable ? parseProbe(r.body) : null;",
        "const parsed = r.ok && !r.unreadable ? (parseProbe(r.body) ?? (r.body as Probe)) : null;",
        "probe 2xx malformed",
    ),
    (
        "a fresh Idempotency-Key per attempt",
        "let k = writeKeys.current.get(action);",
        "let k: string | undefined = undefined;",
        "SAME Idempotency-Key",
    ),
    (
        "a masked marker rendered as its JSON",
        "if (isMaskedMarker(value))\n    return (",
        "if (false as boolean)\n    return (",
        "masked values",
    ),
    (
        "approval sent without the confirmation",
        'if (!confirmed) out.push({ fieldId: `${uid}-confirm`, message: tr("needConfirm", locale) });',
        "",
        "approval needs a reason",
    ),
    (
        "a non-UI purpose is approved",
        "purpose: mapping.datasetPurpose,\n            acknowledged_ignored_keys",
        'purpose: "validation",\n            acknowledged_ignored_keys',
        "exactly the contract bodies",
    ),
    (
        "ignored keys not required",
        "if (ignored.some((k) => !acked.includes(k)))",
        "if (false as boolean)",
        "ignored message keys must each be acknowledged",
    ),
    (
        "a decision reply is not bound to the mapping",
        "if (!binds) {",
        "if (false as boolean) {",
        "never shown as approved|never shown as rejected",
    ),
    (
        "a failed probe verdict has no alert",
        '{p.state !== "completed" ? (\n          <AlertMessage',
        "{(false as boolean) ? (\n          <AlertMessage",
        "probe 200 with state failed",
    ),
    (
        "a failed probe verdict reads as Draft",
        'if (probe.state === "ok" && probe.data.state !== "completed")\n      return { state: "validation_required" };',
        "",
        "probe 200 with state failed",
    ),
    (
        "a stored preview that differs still enables approval",
        "preview.data.previewSha256 === mapping.previewSha256 &&",
        "",
        "resume after reload",
    ),
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _apply(src: str, old: str, new: str) -> str | None:
    """Replace `old` once, matching across prettier's line wrapping (whitespace-insensitive)."""
    if old in src:
        return src.replace(old, new, 1)
    tokens = [re.escape(t) for t in old.split()]
    m = re.search(r"\s+".join(tokens), src)
    return None if m is None else src[: m.start()] + new + src[m.end() :]


def test_p4_fault_injection() -> None:
    if "--no-vitest" in sys.argv:
        print("P4-FAULT skipped (--no-vitest)")
        return
    original = COMPONENT.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    src = original.decode("utf-8")
    try:
        baseline = subprocess.run(
            ["pnpm", "exec", "vitest", "run", SPEC],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        check(
            CID + "-FAULT",
            f"the unmodified spec is GREEN ({baseline.stdout[-800:]})",
            baseline.returncode == 0,
        )
        for name, old, new, pattern in FAULTS:
            mutated = _apply(src, old, new)
            check(CID + "-FAULT", f"{name}: the fault applies to the source", mutated is not None)
            if mutated is None:
                continue
            COMPONENT.write_text(mutated, encoding="utf-8")
            ran = subprocess.run(
                ["pnpm", "exec", "vitest", "run", SPEC, "-t", pattern],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=900,
            )
            plain = re.sub(r"\x1b\[[0-9;]*m", "", ran.stdout + ran.stderr)
            red = ran.returncode != 0 and re.search(r"\b[1-9][0-9]* failed\b", plain) is not None
            check(CID + "-FAULT", f"{name}: the spec goes RED on '{pattern}'", red)
            print(f"  fault {'RED  ' if red else 'GREEN'} {name}")
    finally:
        COMPONENT.write_bytes(original)
    check(
        CID + "-FAULT",
        "the component is restored byte for byte",
        hashlib.sha256(COMPONENT.read_bytes()).hexdigest() == digest,
    )


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
