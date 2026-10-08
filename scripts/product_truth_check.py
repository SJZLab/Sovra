#!/usr/bin/env python3
"""
product_truth_check.py — Law 16 merge-blocking gate.
PRODUCT-TRUE requires:
  (a) real non-test caller from entry point, across ALL source roots
  (b) user-visible assertion (not bare boolean or row-existence)
  (c) fault injection reddens the live-path assertion
Failing (a) = TRACKER — must not count in done denominator.

Caller resolution: text search (deterministic, works in CI without external deps).
The relay may additionally use Cognee MCP recall queries for richer semantic resolution
during shift work — these are complementary, not the same path.
"""

import argparse
import ast
import pathlib
import re
import sys

# Re-derived from the real layout FTF-0001 produced (PR #3), not a guess.
# Each component is currently a flat directory (README.md + manifest only,
# no src/ subdirectory yet — that's a later queue item, not FTF-0001's).
# When a component gains a src/ (or equivalent) convention, narrow its
# entry here rather than leaving the wider root once real code lands.
SOURCE_ROOTS = [
    "apps/web",  # Unified Factory UI
    "apps/api",  # Factory API + policy
    "workers/training",  # Axolotl-backed training worker
    "workers/evaluation",  # Inspect AI-backed evaluation worker
    "workers/data",  # Argilla-backed data/review worker
    "workers/deploy",  # deployment controller (vLLM/LiteLLM)
    "packages/shared",  # shared schema/adapter contracts
    "evals/reference",  # reference agent/tools/discovery (FTF-17xx), imported by apps/api
]

TEST_PATTERNS = ["/test/", "/tests/", ".test.", ".spec.", "_test.", "_tests.", "test_"]


def is_test_file(p):
    return any(x in p for x in TEST_PATTERNS)


SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "dist", "build"}


def _py_references(text, symbol):
    """True when Python source *uses* `symbol`: a loaded Name or an attribute
    access. A def/class of that name, an import, a docstring, a comment or a
    string literal is not a use, and `OidcSettings` is not `Settings`. A use under
    an import alias (`from m import symbol as _s`; `_s(...)`) counts."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    names = {symbol}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(a.asname for a in node.names if a.name == symbol and a.asname)
    for node in ast.walk(tree):
        if not isinstance(getattr(node, "ctx", None), ast.Load):
            continue
        if isinstance(node, ast.Name) and node.id in names:
            return True
        if isinstance(node, ast.Attribute) and node.attr == symbol:
            return True
    return False


_JS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _js_references(text, symbol):
    """True when TS/JS source uses `symbol` outside comments, import/export-from
    lines and its own declaration (whole-word match only)."""
    word = re.compile(r"(?<![\w$])" + re.escape(symbol) + r"(?![\w$])")
    keywords = r"\b(function|class|const|let|var|interface|type|enum)\s+"
    decl = re.compile(keywords + re.escape(symbol) + r"(?![\w$])")
    for line in _JS_COMMENT.sub("", text).splitlines():
        stripped = line.strip()
        reexport = stripped.startswith("export ") and " from " in stripped
        if stripped.startswith("import ") or reexport:
            continue
        line = decl.sub("", line)
        if word.search(line):
            return True
    return False


def find_callers(symbol, roots):
    """
    Non-test source files that USE `symbol` across source roots (Law 5: existence
    is not evidence). Python is resolved by AST, TS/JS by whole-word match with
    comments, imports and the declaration removed. A file that only defines,
    imports, documents or mentions the symbol is not a caller; compiled or
    non-source files (.pyc, .md, ...) are never read.
    Deterministic and dependency-free so it runs in CI. Criteria (b) user-visible
    assertion and (c) fault injection are proven by the Control-plane L2 verifier
    and the mutation gate, not by this script.
    """
    callers = []
    for root in roots:
        rp = pathlib.Path(root)
        if not rp.exists():
            continue
        for f in sorted(rp.rglob("*")):
            if not f.is_file() or f.suffix not in SOURCE_SUFFIXES:
                continue
            rel = f.relative_to(rp)
            if SKIP_DIRS.intersection(rel.parts) or is_test_file(f"/{rp.name}/{rel.as_posix()}"):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if symbol not in text:
                continue
            uses = _py_references if f.suffix == ".py" else _js_references
            if uses(text, symbol):
                callers.append(str(f))
    return callers


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fail-on-unwired", action="store_true")
    p.add_argument("--all-roots", action="store_true")
    p.add_argument("--criteria-file", default="docs/spec-matrix.yaml")
    args = p.parse_args()

    try:
        import yaml

        matrix = yaml.safe_load(open(args.criteria_file)) or {}
    except FileNotFoundError:
        print(f"WARNING: {args.criteria_file} not found")
        sys.exit(0)

    has_symbols = any(e.get("proving_symbol") for e in matrix.values())
    if not SOURCE_ROOTS and has_symbols:
        # Fails closed the moment any criterion claims a proving_symbol — a filled
        # symbol with no roots to search is an unprovable claim, not an empty gate.
        print("ERROR: SOURCE_ROOTS empty but spec-matrix.yaml has proving_symbol entries.")
        print("       Fill SOURCE_ROOTS after FTF-0001 (Initialize monorepo) merges.")
        sys.exit(1)

    results, unwired = [], []
    for cid, entry in matrix.items():
        sym = entry.get("proving_symbol")
        if not sym:
            continue
        callers = find_callers(sym, SOURCE_ROOTS)
        r = {"id": cid, "sym": sym, "callers": callers, "ok": bool(callers)}
        results.append(r)
        if not callers:
            unwired.append(r)

    print(f"Product-truth: {len(results)-len(unwired)}/{len(results)} product-true")
    for r in unwired:
        print(f'  TRACKER: {r["id"]} — {r["sym"]} has no live callers')
        print("           DIAGNOSE why unwired → WIRE into live route")
        print("           NEVER relabel dead code and repoint cert to parallel inline logic")

    if unwired and args.fail_on_unwired:
        sys.exit(1)


if __name__ == "__main__":
    main()
