#!/usr/bin/env python3
"""mutation_gate.py -- machine-enforced fault-injection (mutation) gate.

Law 1 ("nothing proven until test shown able to fail") made mechanical: for
every production function a change touches, this gate breaks the protection
one small, independent way at a time (a "mutant"), re-runs the submitted
tests, and reports every mutant the tests did NOT notice. A surviving mutant
is a protection the tests claim to prove but would stay green without -- the
exact defect the L2 verifier otherwise finds one review round at a time.

Usage:
  python3 scripts/mutation_gate.py --base origin/main \\
      --test "uv run python3 tests/tasks/test_ftf_0307.py" [--test ...] \\
      [--files path ...] [--timeout 300] [--max-mutants 200] \\
      [--allowlist docs/certifications/mutation-allowlist.yaml] [--json out.json]

Target selection:
  * default: production .py files changed in `--base...HEAD`, restricted to
    product_truth_check.SOURCE_ROOTS and never a test file; only
    functions/methods whose line range overlaps a changed hunk
    (`git diff -U0`) are mutated (a pure deletion marks the lines around it,
    so deleting a guard still targets its function).
  * `--files`: mutate every function in exactly those files (whole-file mode,
    used to audit already-merged code); each must sit under SOURCE_ROOTS.

Operators (each mutant is ONE independent AST edit; the edited node is
re-rendered with `ast.unparse` and spliced into the original bytes, so the
rest of the file -- comments, formatting, and any exact-text targets a test's
own fault injection uses -- stays byte-identical):
  M1 guard removal        `raise ...` -> `pass`
  M2 guard bypass         `if <cond>:` whose body raises/returns -> `if False:`
  M3 additive early-return insert `return <literal/Name returned elsewhere,
                          else None>` as the first statement (fail-open)
  M4 comparison flip      == <-> !=, < <-> <=, > <-> >=, is <-> is not,
                          in <-> not in
  M5 boolean op           and <-> or; `not x` -> `x`
  M6 call drop            expression-statement call to validate*/check*/
                          require*/verify*/assert*/ensure*/guard* -> `pass`
  M7 constant hardcode    a value in a returned dict / keyword call ->
                          same-type constant (str->"x", int->0, float->0.0,
                          bool->not); one mutant per key
  M8 field drop           remove one key from a returned dict literal / one
                          keyword from a returned constructor call
  M9 normalization bypass `a == b` -> `a.strip().lower() == b` (Name/Attribute
                          operand), modelling a case/whitespace-tolerant match

Verdicts: KILLED (some --test exited non-zero), TIMEOUT (a --test exceeded
--timeout; counts as killed, reported separately), SURVIVED (every --test
exited 0). A SURVIVED mutant matching a reviewed allowlist entry
(file, function, operator[, line]) is reported but does not fail the gate.

Exit: 0 = no unallowlisted survivor; 1 = survivor(s), or the tests are
already red on the unmutated tree; 2 = usage/configuration error (including
a dirty target file); 3 = the tree could not be restored byte-identical
(never expected; `git checkout -- <file>` recovers, since the gate refuses
to run on uncommitted target files); 130 = interrupted (tree restored).

Safety: the original bytes are restored in a `finally` block after every
mutant (signals masked while writing), verified byte-identical, and SIGINT/
SIGTERM/SIGHUP are converted into an exception so the restore always runs.
Test processes run in their own session and the whole process group is
killed on timeout/interrupt. Bytecode goes to a private PYTHONPYCACHEPREFIX
whose target-module .pyc files are deleted before every run, so a stale
.pyc can never mask a mutant (or the restored original). stdlib only (PyYAML
is used for a non-empty allowlist, already a dev dependency).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.product_truth_check import SOURCE_ROOTS  # noqa: E402

DEFAULT_ALLOWLIST = "docs/certifications/mutation-allowlist.yaml"
OPERATORS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9")
GUARD_CALL = re.compile(r"^(_?validate|_?check|_?require|_?verify|_?assert|_?ensure|_?guard)")
_FLIP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}
_ATOMS = (ast.Name, ast.Constant, ast.Attribute, ast.Call, ast.Subscript, ast.Dict, ast.List)
_SIMPLE_TYPES = {"str", "int", "float", "bool"}
_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


class UsageError(Exception):
    """Bad invocation or configuration -> exit 2."""


class GateInterrupted(BaseException):
    """Raised from a signal handler so every `finally` restore still runs."""


class IntegrityError(Exception):
    """A target file could not be restored byte-identical -> exit 3."""


@dataclass
class Mutant:
    file: str
    function: str
    operator: str
    line: int
    summary: str
    start: int
    end: int
    replacement: bytes
    status: str = "PENDING"
    killed_by: str | None = None
    duration_s: float = 0.0
    allowlisted: bool = False
    allowlist_reason: str | None = None
    output_tail: str | None = None

    def apply(self, original: bytes) -> bytes:
        return original[: self.start] + self.replacement + original[self.end :]

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["replacement"] = self.replacement.decode("utf-8", "replace")
        return d


# --------------------------------------------------------------------------
# git helpers
# --------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise UsageError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_root(cwd: Path) -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise UsageError("not inside a git repository")
    return Path(proc.stdout.strip())


def is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (
        "tests" in parts[:-1]
        or "test" in parts[:-1]
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def under_source_roots(rel: str) -> bool:
    return any(rel == r or rel.startswith(r.rstrip("/") + "/") for r in SOURCE_ROOTS)


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(root: Path, base: str, rel: str) -> set[int]:
    out = _git(root, "diff", "-U0", "--no-color", "--no-ext-diff", f"{base}...HEAD", "--", rel)
    lines: set[int] = set()
    for raw in out.splitlines():
        m = _HUNK.match(raw)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if count == 0:  # pure deletion: mark the lines on both sides of it
            lines.update({start, start + 1})
        else:
            lines.update(range(start, start + count))
    return lines


def select_targets(root: Path, base: str, files: list[str] | None) -> dict[str, set[int] | None]:
    """{repo-relative path: changed new-side lines, or None for whole file}."""
    targets: dict[str, set[int] | None] = {}
    if files:
        for f in files:
            p = Path(f)
            abs_p = (p if p.is_absolute() else Path.cwd() / p).resolve()
            try:
                rel = abs_p.relative_to(root.resolve()).as_posix()
            except ValueError as exc:
                raise UsageError(f"--files: {f} is outside the repository") from exc
            if not (root / rel).is_file():
                raise UsageError(f"--files: {rel} does not exist")
            if not rel.endswith(".py"):
                raise UsageError(f"--files: {rel} is not a .py file")
            if not under_source_roots(rel) or is_test_path(rel):
                raise UsageError(
                    f"--files: {rel} is not production code under SOURCE_ROOTS {SOURCE_ROOTS}"
                )
            targets[rel] = None
        return targets
    _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    names = _git(root, "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD").splitlines()
    for rel in sorted(n.strip() for n in names if n.strip()):
        if rel.endswith(".py") and under_source_roots(rel) and not is_test_path(rel):
            targets[rel] = changed_lines(root, base, rel)
    return targets


def refuse_dirty(root: Path, rels: list[str]) -> None:
    if not rels:
        return
    status = _git(root, "status", "--porcelain", "--", *rels)
    if status.strip():
        raise UsageError(
            "refusing to run: target file(s) have uncommitted changes -- commit or stash "
            "first so the original is always recoverable:\n" + status.rstrip()
        )


# --------------------------------------------------------------------------
# mutant generation
# --------------------------------------------------------------------------


def _line_starts(src: bytes) -> list[int]:
    starts = [0]
    for line in src.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _render(node: ast.expr) -> str:
    text = ast.unparse(node)
    return text if isinstance(node, _ATOMS) else f"({text})"


def _annotation_type(ann: ast.expr | None) -> str | None:
    if ann is None:
        return None
    if isinstance(ann, ast.Name) and ann.id in _SIMPLE_TYPES:
        return ann.id
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            return _annotation_type(ast.parse(ann.value, mode="eval").body)
        except SyntaxError:
            return None
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        sides = [_annotation_type(ann.left), _annotation_type(ann.right)]
        nones = [isinstance(s, ast.Constant) and s.value is None for s in (ann.left, ann.right)]
        if any(nones):
            return next((s for s in sides if s), None)
    if (
        isinstance(ann, ast.Subscript)
        and isinstance(ann.value, ast.Name)
        and ann.value.id == "Optional"
    ):
        return _annotation_type(ann.slice)
    return None


def _expr_type(e: ast.expr, env: dict[str, str]) -> str | None:
    if isinstance(e, ast.Constant):
        v = e.value
        if isinstance(v, bool):
            return "bool"
        for name, typ in (("str", str), ("int", int), ("float", float)):
            if isinstance(v, typ):
                return name
        return None
    if isinstance(e, ast.JoinedStr):
        return "str"
    if isinstance(e, ast.Compare | ast.BoolOp) or (
        isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not)
    ):
        return "bool"
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Name):
        if e.func.id in _SIMPLE_TYPES:
            return e.func.id
        if e.func.id == "len":
            return "int"
    if isinstance(e, ast.Name):
        return env.get(e.id)
    return None


def _own_nodes(func: _FuncDef) -> Iterator[ast.AST]:
    """Every node in func's body, not descending into nested defs/classes."""
    stack: list[ast.AST] = list(func.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                stack.append(child)


def _type_env(func: _FuncDef) -> dict[str, str]:
    env: dict[str, str] = {}
    a = func.args
    for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
        t = _annotation_type(arg.annotation)
        if t:
            env[arg.arg] = t
    nodes = sorted(
        (n for n in _own_nodes(func) if isinstance(n, ast.Assign | ast.AnnAssign)),
        key=lambda n: (n.lineno, n.col_offset),
    )
    for n in nodes:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            t = _annotation_type(n.annotation) or (_expr_type(n.value, env) if n.value else None)
            if t:
                env.setdefault(n.target.id, t)
        elif isinstance(n, ast.Assign) and len(n.targets) == 1:
            tgt = n.targets[0]
            if isinstance(tgt, ast.Name):
                t = _expr_type(n.value, env)
                if t:
                    env.setdefault(tgt.id, t)
    return env


def _local_names(func: _FuncDef) -> set[str]:
    names: set[str] = set()
    for n in _own_nodes(func):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
    return names


def _early_return_value(func: _FuncDef) -> str:
    params = {a.arg for a in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]}
    local_only = _local_names(func) - params
    returns = sorted(
        (n for n in _own_nodes(func) if isinstance(n, ast.Return) and n.value is not None),
        key=lambda n: (n.lineno, n.col_offset),
    )
    for r in returns:
        v = r.value
        assert v is not None
        if isinstance(v, ast.Constant):
            return ast.unparse(v)
        root = v
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id not in local_only:
            # a parameter, a global, or an enum member like Capability.UNKNOWN:
            # bound at the top of the function, so the mutant is a real
            # fail-open return and not a trivially-crashing UnboundLocalError.
            return ast.unparse(v)
    return "None"


class _Collector:
    """Walk a module, attributing each node to its innermost function."""

    def __init__(self, tree: ast.Module) -> None:
        self.items: list[tuple[ast.AST, _FuncDef, str]] = []
        self.funcs: list[tuple[_FuncDef, str]] = []
        self._visit_block(tree.body, [], None)

    def _visit_block(self, nodes: list[ast.stmt], scope: list[str], fn: _FuncDef | None) -> None:
        for n in nodes:
            self._visit(n, scope, fn)

    def _visit(self, node: ast.AST, scope: list[str], fn: _FuncDef | None) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for d in node.decorator_list:
                self._visit(d, scope, fn)
            for d in [*node.args.defaults, *[k for k in node.args.kw_defaults if k]]:
                self._visit(d, scope, fn)
            qual = ".".join([*scope, node.name])
            self.funcs.append((node, qual))
            self._visit_block(node.body, [*scope, node.name], node)
            return
        if isinstance(node, ast.ClassDef):
            self._visit_block(node.body, [*scope, node.name], None)
            return
        if isinstance(node, ast.JoinedStr):
            return  # f-string internals have unreliable positions on 3.11
        if fn is not None:
            self.items.append((node, fn, ".".join(scope)))
        for child in ast.iter_child_nodes(node):
            self._visit(child, scope, fn)


def _func_lines(fn: _FuncDef) -> range:
    first = min([fn.lineno, *[d.lineno for d in fn.decorator_list]])
    return range(first, (fn.end_lineno or fn.lineno) + 1)


def generate_mutants(
    rel: str, source: bytes, lines: set[int] | None, operators: tuple[str, ...] = OPERATORS
) -> list[Mutant]:
    tree = ast.parse(source, filename=rel)
    baseline_dump = ast.dump(tree)
    starts = _line_starts(source)
    col = _Collector(tree)

    def in_scope(fn: _FuncDef) -> bool:
        return lines is None or any(ln in lines for ln in _func_lines(fn))

    def span(node: ast.AST) -> tuple[int, int]:
        n: Any = node
        return starts[n.lineno - 1] + n.col_offset, starts[n.end_lineno - 1] + n.end_col_offset

    out: list[Mutant] = []
    seen: set[bytes] = set()

    def add(op: str, node: ast.AST, qual: str, summary: str, s: int, e: int, rep: str) -> None:
        if op not in operators:
            return
        m = Mutant(rel, qual, op, getattr(node, "lineno", 0), summary, s, e, rep.encode())
        mutated = m.apply(source)
        if mutated in seen:
            return
        try:
            if ast.dump(ast.parse(mutated)) == baseline_dump:
                return  # equivalent at the AST level -- not a real mutant
        except SyntaxError:
            return
        seen.add(mutated)
        out.append(m)

    def expr_mut(op: str, node: ast.expr, qual: str, new: ast.expr, note: str = "") -> None:
        s, e = span(node)
        rep = _render(new)
        summ = note or f"{_short(ast.unparse(node), 40)} -> {_short(rep, 40)}"
        add(op, node, qual, summ, s, e, rep)

    for fn, qual in col.funcs:
        if not in_scope(fn) or not fn.body:
            continue
        body = fn.body
        first = body[0]
        is_doc = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        value = _early_return_value(fn)
        if is_doc and len(body) == 1:
            s, e = span(first)
            line_start = starts[first.lineno - 1]
            indent = source[line_start : line_start + first.col_offset].decode()
            if indent.strip():
                continue
            add(
                "M3",
                fn,
                qual,
                f"insert `return {_short(value, 40)}` first",
                e,
                e,
                f"\n{indent}return {value}",
            )
            continue
        target = body[1] if is_doc else first
        line_start = starts[target.lineno - 1]
        indent_b = source[line_start : line_start + target.col_offset]
        if indent_b.strip():
            continue  # body shares the `def` line: no safe insertion point
        line = source[line_start : starts[target.lineno]]
        nl = "\r\n" if line.endswith(b"\r\n") else "\n"
        add(
            "M3",
            fn,
            qual,
            f"insert `return {_short(value, 40)}` first",
            line_start,
            line_start,
            f"{indent_b.decode()}return {value}{nl}",
        )

    envs: dict[int, dict[str, str]] = {}

    for node, fn, qual in col.items:
        if not in_scope(fn):
            continue
        if isinstance(node, ast.Raise):
            s, e = span(node)
            add("M1", node, qual, f"{_short(ast.unparse(node), 45)} -> pass", s, e, "pass")
        elif isinstance(node, ast.If):
            if any(isinstance(b, ast.Raise | ast.Return) for b in node.body):
                s, e = span(node.test)
                add(
                    "M2",
                    node,
                    qual,
                    f"if {_short(ast.unparse(node.test), 40)} -> if False",
                    s,
                    e,
                    "False",
                )
        elif isinstance(node, ast.Compare):
            for i, cop in enumerate(node.ops):
                flipped = _FLIP.get(type(cop))
                if flipped is None:
                    continue
                ops = list(node.ops)
                ops[i] = flipped()
                expr_mut("M4", node, qual, ast.Compare(node.left, ops, node.comparators))
            operands = [node.left, *node.comparators]
            for i, cop in enumerate(node.ops):
                if not isinstance(cop, ast.Eq | ast.NotEq):
                    continue
                for j, k in ((i, i + 1), (i + 1, i)):
                    operand, peer = operands[j], operands[k]
                    if not isinstance(operand, ast.Name | ast.Attribute):
                        continue
                    if isinstance(peer, ast.Constant) and not isinstance(peer.value, str):
                        continue
                    wrapped = ast.Call(
                        ast.Attribute(
                            ast.Call(ast.Attribute(operand, "strip", ast.Load()), [], []),
                            "lower",
                            ast.Load(),
                        ),
                        [],
                        [],
                    )
                    new_ops = list(operands)
                    new_ops[j] = wrapped
                    expr_mut(
                        "M9",
                        node,
                        qual,
                        ast.Compare(new_ops[0], node.ops, new_ops[1:]),
                        f"{ast.unparse(operand)} -> {ast.unparse(operand)}.strip().lower()",
                    )
        elif isinstance(node, ast.BoolOp):
            flipped_op: ast.boolop = ast.Or() if isinstance(node.op, ast.And) else ast.And()
            expr_mut("M5", node, qual, ast.BoolOp(flipped_op, node.values))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            expr_mut(
                "M5",
                node,
                qual,
                node.operand,
                f"not {_short(ast.unparse(node.operand), 40)} -> drop `not`",
            )
        elif isinstance(node, ast.Expr):
            call = node.value.value if isinstance(node.value, ast.Await) else node.value
            if isinstance(call, ast.Call):
                f = call.func
                name = (
                    f.id
                    if isinstance(f, ast.Name)
                    else f.attr
                    if isinstance(f, ast.Attribute)
                    else ""
                )
                if GUARD_CALL.match(name):
                    s, e = span(node)
                    add("M6", node, qual, f"{_short(ast.unparse(node), 45)} -> pass", s, e, "pass")
        elif isinstance(node, ast.Return) and node.value is not None:
            env = envs.setdefault(id(fn), _type_env(fn))
            _return_mutants(node.value, qual, env, expr_mut)

    out.sort(key=lambda m: (m.file, m.line, m.operator, m.start))
    return out


def _hardcode(value: ast.expr, env: dict[str, str]) -> ast.expr | None:
    t = _expr_type(value, env)
    is_const = isinstance(value, ast.Constant)
    cv = value.value if isinstance(value, ast.Constant) else object()
    if t == "bool":
        return ast.Constant(not cv) if is_const else ast.UnaryOp(ast.Not(), value)
    if t == "str":
        return ast.Constant("y" if cv == "x" else "x")
    if t == "int":
        return ast.Constant(1 if cv == 0 else 0)
    if t == "float":
        return ast.Constant(1.0 if cv == 0.0 else 0.0)
    return None


def _return_mutants(value: ast.expr, qual: str, env: dict[str, str], expr_mut: Any) -> None:
    if isinstance(value, ast.Dict):
        for i, (k, v) in enumerate(zip(value.keys, value.values, strict=True)):
            if k is None:
                continue
            key = ast.unparse(k)
            new = _hardcode(v, env)
            if new is not None:
                expr_mut(
                    "M7",
                    v,
                    qual,
                    new,
                    f"{key}: {_short(ast.unparse(v), 25)} -> {_short(_render(new), 25)}",
                )
            keys = value.keys[:i] + value.keys[i + 1 :]
            vals = value.values[:i] + value.values[i + 1 :]
            expr_mut("M8", value, qual, ast.Dict(keys, vals), f"drop key {_short(key, 40)}")
    elif isinstance(value, ast.Call) and value.keywords:
        f = value.func
        fname = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        for i, kw in enumerate(value.keywords):
            if kw.arg is None:
                continue
            new = _hardcode(kw.value, env)
            if new is not None:
                expr_mut(
                    "M7",
                    kw.value,
                    qual,
                    new,
                    f"{kw.arg}={_short(ast.unparse(kw.value), 25)} -> "
                    f"{_short(_render(new), 25)}",
                )
            if fname[:1].isupper():
                kws = value.keywords[:i] + value.keywords[i + 1 :]
                expr_mut(
                    "M8", value, qual, ast.Call(f, value.args, kws), f"drop {fname}({kw.arg}=...)"
                )


# --------------------------------------------------------------------------
# allowlist
# --------------------------------------------------------------------------


def load_allowlist(root: Path, path: str | None) -> list[dict[str, Any]]:
    explicit = path is not None
    p = root / (path or DEFAULT_ALLOWLIST)
    if not p.is_file():
        if explicit:
            raise UsageError(f"--allowlist {p} does not exist")
        return []
    text = p.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml is a dev dependency
        raise UsageError("PyYAML is required to read the allowlist") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise UsageError(f"allowlist {p} is not valid YAML: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("entries")
    if data is None:
        return []
    if not isinstance(data, list):
        raise UsageError(f"allowlist {p}: `entries` must be a list")
    entries: list[dict[str, Any]] = []
    for i, e in enumerate(data):
        if not isinstance(e, dict):
            raise UsageError(f"allowlist {p} entry {i}: must be a mapping")
        for k in ("file", "function", "operator", "reason"):
            if not isinstance(e.get(k), str) or not e[k].strip():
                raise UsageError(f"allowlist {p} entry {i}: `{k}` must be a non-empty string")
        if e["operator"] not in OPERATORS:
            raise UsageError(f"allowlist {p} entry {i}: unknown operator {e['operator']!r}")
        if "line" in e and not (isinstance(e["line"], int) and not isinstance(e["line"], bool)):
            raise UsageError(f"allowlist {p} entry {i}: `line` must be an integer")
        entries.append(e)
    return entries


def match_allowlist(m: Mutant, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for e in entries:
        if (e["file"], e["function"], e["operator"]) != (m.file, m.function, m.operator):
            continue
        if "line" in e and e["line"] != m.line:
            continue
        return e
    return None


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------


_BLOCK = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def _write_masked(path: Path, data: bytes) -> None:
    masked = hasattr(signal, "pthread_sigmask")
    if masked:
        old = signal.pthread_sigmask(signal.SIG_BLOCK, _BLOCK)
    try:
        path.write_bytes(data)
    finally:
        if masked:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)


def restore(path: Path, original: bytes) -> None:
    for _ in range(2):
        _write_masked(path, original)
        if path.read_bytes() == original:
            return
    raise IntegrityError(f"{path} could not be restored byte-identical")


def _clear_pyc(prefix: Path, stems: set[str]) -> None:
    for stem in stems:
        for pyc in prefix.rglob(f"{stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def run_command(
    cmd: str, root: Path, env: dict[str, str], timeout: float
) -> tuple[str, float, str]:
    """Return (verdict, seconds, output tail): PASS, FAIL or TIMEOUT."""
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    verdict = "FAIL"
    try:
        out, _ = proc.communicate(timeout=timeout)
        verdict = "PASS" if proc.returncode == 0 else "FAIL"
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, _ = proc.communicate()
        verdict = "TIMEOUT"
    except BaseException:
        _kill_group(proc)
        proc.communicate()
        raise
    tail = "\n".join(out.decode("utf-8", "replace").splitlines()[-15:])
    return verdict, time.monotonic() - t0, tail


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _sample(mutants: list[Mutant], cap: int) -> list[Mutant]:
    if cap <= 0 or len(mutants) <= cap:
        return mutants
    if cap == 1:
        return mutants[:1]
    idx = sorted({round(i * (len(mutants) - 1) / (cap - 1)) for i in range(cap)})
    return [mutants[i] for i in idx]


def _row(m: Mutant) -> str:
    status = m.status + ("*" if m.allowlisted else "")
    return f"{status:<10} {m.operator:<3} {m.file}:{m.line:<5} {m.function:<34} {m.summary}"


def _on_signal(signum: int, _frame: Any) -> None:
    raise GateInterrupted(signum)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--base", default="origin/main", help="diff base (default origin/main)")
    ap.add_argument(
        "--test",
        action="append",
        required=True,
        metavar="CMD",
        help="test command (shell); repeatable; non-zero exit = mutant killed",
    )
    ap.add_argument(
        "--files",
        nargs="+",
        metavar="PATH",
        help="mutate every function in these files instead of the diff vs --base",
    )
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per test command")
    ap.add_argument(
        "--max-mutants",
        type=int,
        default=200,
        help="cap; evenly samples across the generated mutants (0 = no cap)",
    )
    ap.add_argument("--allowlist", default=None, help=f"default {DEFAULT_ALLOWLIST}")
    ap.add_argument(
        "--operators", default=",".join(OPERATORS), help="comma-separated subset of M1..M9"
    )
    ap.add_argument("--json", dest="json_out", default=None, help="write full results here")
    ap.add_argument("--dry-run", action="store_true", help="list mutants, run nothing")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    for sig in _BLOCK:
        signal.signal(sig, _on_signal)
    t_start = time.monotonic()
    try:
        return _run(args, t_start)
    except UsageError as exc:
        print(f"mutation_gate: usage error: {exc}", file=sys.stderr)
        return 2
    except IntegrityError as exc:
        print(
            f"mutation_gate: INTEGRITY FAILURE: {exc} -- run `git checkout -- <file>`",
            file=sys.stderr,
        )
        return 3
    except (GateInterrupted, KeyboardInterrupt):
        print("mutation_gate: interrupted -- tree restored", file=sys.stderr)
        return 130


def _run(args: argparse.Namespace, t_start: float) -> int:
    ops = tuple(o.strip().upper() for o in args.operators.split(",") if o.strip())
    bad = [o for o in ops if o not in OPERATORS]
    if bad or not ops:
        raise UsageError(f"--operators: unknown {bad or 'empty'}; choose from {OPERATORS}")
    if args.timeout <= 0 or args.max_mutants < 0:
        raise UsageError("--timeout must be > 0 and --max-mutants >= 0")
    root = repo_root(Path.cwd())
    targets = select_targets(root, args.base, args.files)
    refuse_dirty(root, list(targets))
    allow = load_allowlist(root, args.allowlist)

    originals: dict[str, bytes] = {rel: (root / rel).read_bytes() for rel in targets}
    generated: list[Mutant] = []
    for rel, lines in targets.items():
        try:
            generated.extend(generate_mutants(rel, originals[rel], lines, ops))
        except SyntaxError as exc:
            raise UsageError(f"{rel} does not parse: {exc}") from exc
    mutants = _sample(generated, args.max_mutants)

    print(
        f"mutation_gate: base={args.base} mode={'files' if args.files else 'diff'} "
        f"targets={list(targets) or '[]'}"
    )
    print(
        f"mutation_gate: {len(generated)} mutants generated, {len(mutants)} to run"
        + (
            f" (capped by --max-mutants {args.max_mutants})"
            if len(mutants) < len(generated)
            else ""
        )
    )
    if args.dry_run:
        for m in mutants:
            print(_row(m))
        _write_json(args, root, targets, [], mutants, generated, t_start)
        return 0
    if not mutants:
        print("mutation_gate: nothing to mutate (no changed function under SOURCE_ROOTS) -- PASS")
        _write_json(args, root, targets, [], mutants, generated, t_start)
        return 0

    with tempfile.TemporaryDirectory(prefix="mutation-gate-pyc-") as pyc_dir:
        env = dict(os.environ, PYTHONPYCACHEPREFIX=pyc_dir)
        stems = {Path(rel).stem for rel in targets}
        baseline = []
        for cmd in args.test:
            verdict, secs, tail = run_command(cmd, root, env, args.timeout)
            baseline.append({"test": cmd, "verdict": verdict, "duration_s": round(secs, 2)})
            print(f"baseline   {verdict:<7} {secs:6.1f}s  {cmd}")
            if verdict != "PASS":
                print(
                    f"mutation_gate: BASELINE RED -- tests fail on the unmutated tree; "
                    f"the gate cannot judge them.\n{tail}"
                )
                _write_json(args, root, targets, baseline, mutants, generated, t_start)
                return 1
        order = list(args.test)
        for i, m in enumerate(mutants, 1):
            path = root / m.file
            original = originals[m.file]
            t0 = time.monotonic()
            try:
                _write_masked(path, m.apply(original))
                m.status = "SURVIVED"
                for cmd in list(order):
                    _clear_pyc(Path(pyc_dir), stems)
                    verdict, _, tail = run_command(cmd, root, env, args.timeout)
                    if verdict != "PASS":
                        m.status = "KILLED" if verdict == "FAIL" else "TIMEOUT"
                        m.killed_by, m.output_tail = cmd, tail
                        order.remove(cmd)
                        order.insert(0, cmd)  # the killer runs first next time
                        break
            finally:
                restore(path, original)
                _clear_pyc(Path(pyc_dir), stems)
            m.duration_s = round(time.monotonic() - t0, 2)
            if m.status == "SURVIVED":
                entry = match_allowlist(m, allow)
                if entry is not None:
                    m.allowlisted, m.allowlist_reason = True, str(entry["reason"])
            print(f"[{i:>3}/{len(mutants)}] {_row(m)}  ({m.duration_s:.1f}s)", flush=True)

    for rel, original in originals.items():
        if (root / rel).read_bytes() != original:
            raise IntegrityError(f"{rel} differs from its original after the run")
    if _git(root, "status", "--porcelain", "--", *targets).strip():
        raise IntegrityError("target files show as modified in git after the run")

    survivors = [m for m in mutants if m.status == "SURVIVED" and not m.allowlisted]
    allowed = [m for m in mutants if m.status == "SURVIVED" and m.allowlisted]
    killed = sum(m.status == "KILLED" for m in mutants)
    timeouts = sum(m.status == "TIMEOUT" for m in mutants)
    score = 100.0 * (killed + timeouts) / len(mutants)
    print("\nOPERATOR  FILE:LINE  FUNCTION  MUTATION -- survivors (tests stayed GREEN):")
    for m in survivors + allowed:
        print("  " + _row(m) + (f"   [allowlisted: {m.allowlist_reason}]" if m.allowlisted else ""))
    if not survivors and not allowed:
        print("  (none)")
    print(
        f"\nmutation_gate: {len(mutants)} run | {killed} KILLED | {timeouts} TIMEOUT | "
        f"{len(survivors)} SURVIVED | {len(allowed)} SURVIVED-allowlisted | "
        f"score {score:.1f}% | {time.monotonic() - t_start:.1f}s"
    )
    _write_json(args, root, targets, baseline, mutants, generated, t_start)
    if survivors:
        print(
            "mutation_gate: FAIL -- strengthen the tests until each survivor is KILLED, or "
            f"record a reviewed equivalent mutant in {DEFAULT_ALLOWLIST}"
        )
        return 1
    print("mutation_gate: PASS")
    return 0


def _write_json(
    args: argparse.Namespace,
    root: Path,
    targets: dict[str, set[int] | None],
    baseline: list[dict[str, Any]],
    mutants: list[Mutant],
    generated: list[Mutant],
    t_start: float,
) -> None:
    if not args.json_out:
        return
    counts = {s: sum(m.status == s for m in mutants) for s in ("KILLED", "TIMEOUT", "SURVIVED")}
    counts["SURVIVED_ALLOWLISTED"] = sum(m.allowlisted for m in mutants)
    doc = {
        "base": args.base,
        "mode": "files" if args.files else "diff",
        "head": _git(root, "rev-parse", "HEAD").strip(),
        "targets": {k: (sorted(v) if v is not None else "all") for k, v in targets.items()},
        "tests": args.test,
        "baseline": baseline,
        "generated": len(generated),
        "run": len(mutants),
        "summary": counts,
        "runtime_s": round(time.monotonic() - t_start, 2),
        "mutants": [m.to_json() for m in mutants],
    }
    Path(args.json_out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
