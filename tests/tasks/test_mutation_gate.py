#!/usr/bin/env python3
"""Mutation gate (scripts/mutation_gate.py) -- negative-test proof.

The gate exists because relay submissions kept coming back 3-5 times with
tests that stayed GREEN while the protection under test was broken. This file
proves the gate itself is not that kind of test: it builds a tiny throwaway
git repository holding one validator module (apps/api/guard.py, so it sits
under product_truth_check.SOURCE_ROOTS) plus two test scripts, and shows:

* a WEAK test (calls the code, asserts almost nothing) leaves surviving
  mutants and the gate exits 1, naming them;
* a STRONG test (pins every guard, every returned field, the exact-match
  comparison) kills every mutant and the gate exits 0;
* every operator M1-M10 produces at least one mutant on suitable code, each
  mutant is one localized edit that still parses;
* diff mode mutates only functions overlapping the changed hunks, never a
  test file or a file outside SOURCE_ROOTS;
* the target file is byte-identical after every run (including a non-ASCII
  comment), after a TIMEOUT, and after SIGINT/SIGTERM mid-mutant;
* a reviewed allowlist suppresses exactly its entries; a malformed one is a
  usage error (2);
* a dirty target file, an unknown operator, a file outside SOURCE_ROOTS and a
  missing --test are refused with exit 2; a baseline-red test exits 1;
* Law 1 on the gate itself: copies of the gate with (a) the SURVIVED verdict
  removed and (b) the restore removed are run through the same weak scenario,
  and this file's own scenario predicate rejects both.

Run directly: python3 tests/tasks/test_mutation_gate.py   (exit 0 = all pass)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts import mutation_gate as mg  # noqa: E402

GATE = ROOT / "scripts" / "mutation_gate.py"
CID = "MUTATION-GATE"
FAILURES: list[str] = []
PY = f'"{sys.executable}"'


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


GUARD_BASE = '''"""Access guard fixture -- \u00fcn\u00efcode keeps restores honest."""

from dataclasses import dataclass


class AccessError(Exception):
    pass


@dataclass
class Result:
    ok: bool = False
    name: str = ""
    level: int = 0
    note: str = "none"


_NORMALIZERS = {
    "trim": lambda v: v.strip(),
    "fold": str.casefold,
}


def normalize(kind, value):
    return _NORMALIZERS[kind](value)


def _validate_name(name):
    if not isinstance(name, str) or not name:
        raise AccessError("name required")


def check_access(name: str, role, roles, allowed: bool):
    # \u00fcn\u00efcode comment, kept verbatim by the splice-based mutator
    _validate_name(name)
    if role not in roles:
        raise AccessError("unknown role")
    if name == "admin" and allowed:
        return {"ok": True, "name": name, "level": 2}
    return Result(ok=True, name=name, level=1, note=f"role {role}")
'''

# Feature branch change: only check_access gains a deny branch.
GUARD = GUARD_BASE.replace(
    '    return Result(ok=True, name=name, level=1, note=f"role {role}")\n',
    "    if not allowed:\n"
    '        return {"ok": False, "name": name, "level": 0}\n'
    '    return Result(ok=True, name=name, level=1, note=f"role {role}")\n',
)

TEST_PRELUDE = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api"))
from guard import AccessError, Result, _validate_name, check_access, normalize

def raises(fn, *a):
    try:
        fn(*a)
    except AccessError:
        return True
    return False
"""

WEAK_TEST = (
    TEST_PRELUDE
    + """
assert check_access("bob", "r", {"r"}, True) is not None
assert check_access("admin", "r", {"r"}, True)
"""
)

STRONG_TEST = (
    TEST_PRELUDE
    + """
R = {"r"}
assert raises(_validate_name, "")
assert raises(_validate_name, 5)
assert not raises(_validate_name, "bob")
assert raises(check_access, "", "r", R, True)
assert raises(check_access, "bob", "x", R, True)
assert raises(check_access, "bob", "R", R, True)
assert raises(check_access, "bob", " r", R, True)
assert check_access("admin", "r", R, True) == {"ok": True, "name": "admin", "level": 2}
assert check_access("admin", "r", R, False) == {"ok": False, "name": "admin", "level": 0}
assert check_access("bob", "r", R, False) == {"ok": False, "name": "bob", "level": 0}
assert check_access("bob", "r", R, True) == Result(ok=True, name="bob", level=1, note="role r")
assert check_access("Admin", "r", R, True) == Result(True, "Admin", 1, "role r")
assert check_access(" admin", "r", R, True) == Result(True, " admin", 1, "role r")
assert normalize("trim", " a ") == "a"
assert normalize("fold", "Stra\u00dfe") == "strasse"
"""
)

SLOW_WHEN_MUTATED = """import hashlib, sys, time
from pathlib import Path
src = Path(__file__).resolve().parent.parent / "apps" / "api" / "guard.py"
if hashlib.sha256(src.read_bytes()).hexdigest() != sys.argv[1]:
    time.sleep(60)
"""


def _git(repo: Path, *args: str) -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_repo(base: Path) -> Path:
    """main = GUARD_BASE; HEAD (branch feature) = GUARD + tests + an out-of-roots file."""
    repo = base / "repo"
    (repo / "apps" / "api").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "tools").mkdir()
    _git(repo, "init", "-q")
    _git(repo, "checkout", "-q", "-b", "main")
    (repo / "apps" / "api" / "guard.py").write_text(GUARD_BASE, encoding="utf-8")
    (repo / "tests" / "test_weak.py").write_text(WEAK_TEST, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "apps" / "api" / "guard.py").write_text(GUARD, encoding="utf-8")
    (repo / "tests" / "test_weak.py").write_text(WEAK_TEST + "\n# touched\n", encoding="utf-8")
    (repo / "tests" / "test_strong.py").write_text(STRONG_TEST, encoding="utf-8")
    (repo / "tests" / "slow_when_mutated.py").write_text(SLOW_WHEN_MUTATED, encoding="utf-8")
    (repo / "tools" / "helper.py").write_text(
        "def f(x):\n    if x:\n        raise ValueError(x)\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feature")
    return repo


def run_gate(repo: Path, *args: str, gate: Path = GATE) -> tuple[int, str, dict]:
    out_json = repo.parent / "out.json"
    out_json.unlink(missing_ok=True)
    proc = subprocess.run(
        [sys.executable, str(gate), *args, "--json", str(out_json)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=600,
    )
    data = json.loads(out_json.read_text()) if out_json.exists() else {}
    return proc.returncode, proc.stdout + proc.stderr, data


def guard_bytes(repo: Path) -> bytes:
    return (repo / "apps" / "api" / "guard.py").read_bytes()


def tree_clean(repo: Path) -> bool:
    return _git(repo, "status", "--porcelain", "--untracked-files=no").strip() == ""


def weak_scenario_ok(repo: Path, code: int, out: str, data: dict) -> bool:
    """What a correct gate must do on the weak test: fail, name survivors, restore."""
    survivors = [m for m in data.get("mutants", []) if m["status"] == "SURVIVED"]
    return (
        code == 1
        and len(survivors) > 0
        and "SURVIVED" in out
        and guard_bytes(repo) == GUARD.encode()
        and tree_clean(repo)
    )


WEAK = f"{PY} tests/test_weak.py"
STRONG = f"{PY} tests/test_strong.py"
FILES = ["--files", "apps/api/guard.py"]


def test_every_operator_generates_a_localized_parseable_mutant() -> None:
    src = GUARD.encode()
    mutants = mg.generate_mutants("apps/api/guard.py", src, None)
    by_op = {op: [m for m in mutants if m.operator == op] for op in mg.OPERATORS}
    for op in mg.OPERATORS:
        check(CID, f"operator {op} generates at least one mutant on the fixture", bool(by_op[op]))
    for m in mutants:
        mutated = m.apply(src)
        check(CID, f"{m.operator}@{m.line} changes the source", mutated != src)
        check(
            CID,
            f"{m.operator}@{m.line} keeps the bytes before the splice",
            mutated[: m.start] == src[: m.start],
        )
        check(
            CID,
            f"{m.operator}@{m.line} keeps the bytes after the splice",
            mutated[m.start + len(m.replacement) :] == src[m.end :],
        )
        try:
            compile(mutated, "guard.py", "exec")
        except SyntaxError:
            check(CID, f"{m.operator}@{m.line} mutant parses", False)
    summaries = {m.operator: [x.summary for x in by_op[m.operator]] for m in mutants}
    check(CID, "M1 turns `raise` into `pass`", any("-> pass" in s for s in summaries["M1"]))
    check(CID, "M2 bypasses a guard with `if False`", any("if False" in s for s in summaries["M2"]))
    check(
        CID,
        "M4 flips `not in` to `in`",
        any("role not in roles" in s and "(role in roles)" in s for s in summaries["M4"]),
    )
    check(
        CID,
        "M9 wraps the compared Name in .strip().lower()",
        any("name.strip().lower()" in s for s in summaries["M9"]),
    )
    check(
        CID,
        "M10 reaches a lambda in a module-level table (.strip() -> .lstrip())",
        any("<lambda@" in m.function and "lstrip" in m.summary for m in by_op["M10"]),
    )
    check(
        CID,
        "M10 swaps a bare str.casefold table entry for str.lower",
        any("str.casefold -> str.lower" in m.summary for m in by_op["M10"]),
    )
    check(
        CID,
        "M9 also normalizes the probe of a membership test (`role not in roles`)",
        any("role -> role.strip().lower()" in s for s in summaries["M9"]),
    )
    check(
        CID,
        "M6 drops the _validate_name(...) call",
        any("_validate_name" in s for s in summaries["M6"]),
    )
    check(CID, "M8 drops a constructor keyword", any("drop Result(" in s for s in summaries["M8"]))
    m3 = [m for m in by_op["M3"] if m.function == "_validate_name"]
    check(
        CID,
        "M3 inserts an early `return None` into the raising validator",
        bool(m3) and b"return None" in m3[0].replacement,
    )
    sub = mg.generate_mutants("apps/api/guard.py", src, None, ("M1",))
    check(CID, "--operators restricts generation", {m.operator for m in sub} == {"M1"})


def test_weak_test_reports_survivors_and_fails(repo: Path) -> None:
    code, out, data = run_gate(repo, *FILES, "--test", WEAK)
    check(
        CID,
        f"weak test: gate exits 1 with survivors listed (got {code})",
        weak_scenario_ok(repo, code, out, data),
    )
    survived_ops = {m["operator"] for m in data.get("mutants", []) if m["status"] == "SURVIVED"}
    for op in ("M1", "M6", "M9"):
        check(
            CID,
            f"weak test lets a {op} mutant survive (the gap the verifier keeps finding)",
            op in survived_ops,
        )


def test_strong_test_kills_every_mutant(repo: Path) -> None:
    code, out, data = run_gate(repo, *FILES, "--test", WEAK, "--test", STRONG)
    statuses = [m["status"] for m in data.get("mutants", [])]
    check(CID, f"strong test: gate exits 0 (got {code}): {out[-1500:]}", code == 0)
    check(CID, "strong test: every mutant KILLED", bool(statuses) and set(statuses) == {"KILLED"})
    check(
        CID,
        "strong run covered all ten operators",
        {m["operator"] for m in data.get("mutants", [])} == set(mg.OPERATORS),
    )
    check(
        CID, "strong run: guard.py byte-identical afterwards", guard_bytes(repo) == GUARD.encode()
    )
    check(CID, "strong run: git tree clean afterwards", tree_clean(repo))


def test_diff_mode_targets_only_changed_functions(repo: Path) -> None:
    code, out, data = run_gate(repo, "--base", "main", "--test", WEAK, "--dry-run")
    funcs = {m["function"] for m in data.get("mutants", [])}
    check(CID, f"diff mode dry-run exits 0 (got {code}): {out[-800:]}", code == 0)
    check(
        CID,
        "diff mode targets only apps/api/guard.py (not tests/, not tools/)",
        list(data.get("targets", {})) == ["apps/api/guard.py"],
    )
    check(CID, f"diff mode mutates only check_access (got {funcs})", funcs == {"check_access"})
    lines = set(data.get("targets", {}).get("apps/api/guard.py", []))
    deny = GUARD.splitlines().index("    if not allowed:") + 1
    check(
        CID,
        f"diff mode records the changed hunk lines (got {sorted(lines)})",
        {deny, deny + 1} <= lines and deny - 2 not in lines,
    )
    code, out, _ = run_gate(repo, "--base", "HEAD", "--test", WEAK)
    check(
        CID,
        "no changed production function -> exit 0 with a clear message",
        code == 0 and "nothing to mutate" in out,
    )


def test_allowlist_suppresses_exactly_its_entries(repo: Path) -> None:
    _, _, data = run_gate(repo, *FILES, "--test", WEAK)
    survivors = [m for m in data.get("mutants", []) if m["status"] == "SURVIVED"]
    entries = [
        {
            "file": m["file"],
            "function": m["function"],
            "operator": m["operator"],
            "line": m["line"],
            "reason": "reviewed in test",
        }
        for m in survivors
    ]
    if not entries:
        check(CID, "allowlist test needs the weak run's survivors -- the gate reported none", False)
        return
    allow = repo.parent / "allow.yaml"
    allow.write_text(json.dumps({"entries": entries}), encoding="utf-8")  # JSON is YAML
    code, out, data2 = run_gate(repo, *FILES, "--test", WEAK, "--allowlist", str(allow))
    marked = [m for m in data2.get("mutants", []) if m["allowlisted"]]
    check(CID, f"full allowlist: gate exits 0 (got {code})", code == 0)
    check(
        CID,
        "full allowlist: survivors still reported, marked allowlisted",
        len(marked) == len(survivors) > 0 and "SURVIVED-allowlisted" in out,
    )

    # An entry keys on (file, function, operator, line); two survivors can share one key.
    def key(e: dict) -> tuple:
        return (e["file"], e["function"], e["operator"], e["line"])

    dropped = key(entries[0])
    allow.write_text(
        json.dumps({"entries": [e for e in entries if key(e) != dropped]}), encoding="utf-8"
    )
    code, _, data3 = run_gate(repo, *FILES, "--test", WEAK, "--allowlist", str(allow))
    unlisted = [
        m for m in data3.get("mutants", []) if m["status"] == "SURVIVED" and not m["allowlisted"]
    ]
    expected = [m for m in survivors if key(m) == dropped]
    check(
        CID,
        "partial allowlist: exactly the survivors of the dropped entry still fail the gate",
        code == 1
        and len(unlisted) == len(expected) >= 1
        and {key(m) for m in unlisted} == {dropped},
    )
    allow.write_text(json.dumps({"entries": [dict(entries[0], reason="")]}), encoding="utf-8")
    code, out, _ = run_gate(repo, *FILES, "--test", WEAK, "--allowlist", str(allow))
    check(CID, "allowlist entry without a reason is a usage error (2)", code == 2)
    real = ROOT / mg.DEFAULT_ALLOWLIST
    committed = mg.load_allowlist(ROOT, mg.DEFAULT_ALLOWLIST) if real.is_file() else None
    check(CID, "the committed allowlist exists and parses", committed is not None)
    for i, e in enumerate(committed or []):
        where = f"committed allowlist entry {i} ({e.get('file')}:{e.get('line')})"
        # A Control-plane ruling on a survivor in a file that only exists on an open [relay] PR's
        # branch carries `pending_pr: <n>` (a [relay] PR may not edit this file, and the
        # mutation gate runs only on [relay] PRs, so the entry must land on main first). The
        # existence check applies as soon as the file exists on main.
        pending = e.get("pending_pr")
        if isinstance(pending, int) and not isinstance(pending, bool) and pending > 0:
            if not (ROOT / e["file"]).is_file():
                continue
        check(CID, f"{where} names an existing production file", (ROOT / e["file"]).is_file())
        check(CID, f"{where} is inside the mutation roots", mg.under_source_roots(e["file"]))
        # Pinned to a line, so the entry goes stale and the survivor resurfaces if the code moves.
        check(CID, f"{where} is pinned to an exact line", isinstance(e.get("line"), int))
        check(CID, f"{where} explains why the mutant is equivalent", len(e["reason"]) >= 40)
    check(CID, "guard.py byte-identical after allowlist runs", guard_bytes(repo) == GUARD.encode())


def test_refusals_and_usage_errors(repo: Path) -> None:
    path = repo / "apps" / "api" / "guard.py"
    dirty = GUARD + "# uncommitted edit\n"
    path.write_text(dirty, encoding="utf-8")
    code, out, _ = run_gate(repo, *FILES, "--test", WEAK)
    check(CID, f"dirty target refused with exit 2 (got {code})", code == 2)
    check(CID, "dirty refusal says why", "uncommitted" in out)
    check(CID, "dirty target left exactly as the user had it", path.read_text() == dirty)
    _git(repo, "checkout", "--", "apps/api/guard.py")
    code, _, _ = run_gate(repo, "--files", "tools/helper.py", "--test", WEAK)
    check(CID, "file outside SOURCE_ROOTS refused with exit 2", code == 2)
    code, _, _ = run_gate(repo, *FILES, "--test", WEAK, "--operators", "M1,M42")
    check(CID, "unknown operator refused with exit 2", code == 2)
    proc = subprocess.run([sys.executable, str(GATE), *FILES], cwd=repo, capture_output=True)
    check(CID, "missing --test is a usage error (2)", proc.returncode == 2)
    code, out, _ = run_gate(repo, *FILES, "--test", f'{PY} -c "raise SystemExit(1)"')
    check(CID, "baseline-red tests exit 1, never a pass", code == 1 and "BASELINE RED" in out)
    check(
        CID, "tree clean after refusals", tree_clean(repo) and guard_bytes(repo) == GUARD.encode()
    )


def test_timeout_counts_as_killed_and_restores(repo: Path) -> None:
    digest = hashlib.sha256(GUARD.encode()).hexdigest()
    slow = f"{PY} tests/slow_when_mutated.py {digest}"
    t0 = time.monotonic()
    code, out, data = run_gate(
        repo, *FILES, "--test", slow, "--timeout", "2", "--operators", "M1", "--max-mutants", "1"
    )
    elapsed = time.monotonic() - t0
    statuses = [m["status"] for m in data.get("mutants", [])]
    check(CID, f"timeout mutant reported TIMEOUT (got {statuses})", statuses == ["TIMEOUT"])
    check(CID, f"timeout counts as killed -> exit 0 (got {code})", code == 0)
    check(CID, f"timed-out process group is killed promptly ({elapsed:.1f}s)", elapsed < 30)
    check(CID, "guard.py restored after a timeout", guard_bytes(repo) == GUARD.encode())


def _interrupt(repo: Path, sig: int) -> None:
    digest = hashlib.sha256(GUARD.encode()).hexdigest()
    slow = f"{PY} tests/slow_when_mutated.py {digest}"
    path = repo / "apps" / "api" / "guard.py"
    proc = subprocess.Popen(
        [sys.executable, str(GATE), *FILES, "--test", slow, "--operators", "M1"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 30
    mutated_seen = False
    while time.monotonic() < deadline:
        if path.read_bytes() != GUARD.encode():
            mutated_seen = True
            break
        time.sleep(0.05)
    time.sleep(0.3)
    proc.send_signal(sig)
    try:
        proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
    name = signal.Signals(sig).name
    check(CID, f"{name}: a mutant was on disk when the signal arrived", mutated_seen)
    check(CID, f"{name}: gate exits 130 (got {proc.returncode})", proc.returncode == 130)
    check(
        CID, f"{name}: guard.py byte-identical after interrupt", path.read_bytes() == GUARD.encode()
    )
    check(CID, f"{name}: git tree clean after interrupt", tree_clean(repo))


def test_interrupt_restores_tree(repo: Path) -> None:
    _interrupt(repo, signal.SIGINT)
    _interrupt(repo, signal.SIGTERM)


def _broken_gate(base: Path, old: str, new: str) -> Path:
    """A copy of the real gate with one fault injected (Law 1 on the gate)."""
    scripts = base / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "__init__.py").write_text("")
    shutil.copy(ROOT / "scripts" / "product_truth_check.py", scripts / "product_truth_check.py")
    src = GATE.read_text(encoding="utf-8")
    check(CID, f"fault-injection target present in the gate: {old!r}", old in src)
    (scripts / "mutation_gate.py").write_text(src.replace(old, new), encoding="utf-8")
    return scripts / "mutation_gate.py"


def test_fault_injection_this_test_rejects_a_broken_gate(repo: Path, base: Path) -> None:
    always_killed = _broken_gate(base / "fi1", 'm.status = "SURVIVED"\n', 'm.status = "KILLED"\n')
    code, out, data = run_gate(repo, *FILES, "--test", WEAK, gate=always_killed)
    check(
        CID,
        "fault injection: an always-KILLED gate is rejected by the weak-scenario predicate",
        not weak_scenario_ok(repo, code, out, data),
    )
    no_restore = _broken_gate(
        base / "fi2", "                restore(path, original)\n", "                pass\n"
    )
    code, out, data = run_gate(repo, *FILES, "--test", WEAK, gate=no_restore)
    check(
        CID,
        "fault injection: a gate that skips restore is rejected by the predicate",
        not weak_scenario_ok(repo, code, out, data),
    )
    _git(repo, "checkout", "--", "apps/api/guard.py")
    check(CID, "fixture repaired after fault injection", guard_bytes(repo) == GUARD.encode())


def test_mutation_roots_cover_every_production_python_tree() -> None:
    # FTF-0902 round 4 passed vacuously ("targets=[]") because packages/security was outside the
    # roots the gate mutates. Every package and the gate/sync scripts must be in scope; the gate
    # itself and tests never are.
    for rel in (
        "packages/security/network_policy_templates.py",
        "packages/litellm_client/connection_validation.py",
        "packages/storage/tenant_policy.py",
        "scripts/task_claim.py",
        "apps/api/app/main.py",
        "workers/data/record_schema_validator.py",
    ):
        check(CID, f"{rel} is in mutation scope", mg.under_source_roots(rel))
    check(
        CID, "the gate never mutates itself", not mg.under_source_roots("scripts/mutation_gate.py")
    )
    check(CID, "files outside every root stay out of scope", not mg.under_source_roots("docs/x.py"))


def main() -> None:
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mutation-gate-test-") as tmp:
        base = Path(tmp)
        repo = make_repo(base)
        test_mutation_roots_cover_every_production_python_tree()
        test_every_operator_generates_a_localized_parseable_mutant()
        test_weak_test_reports_survivors_and_fails(repo)
        test_strong_test_kills_every_mutant(repo)
        test_diff_mode_targets_only_changed_functions(repo)
        test_allowlist_suppresses_exactly_its_entries(repo)
        test_refusals_and_usage_errors(repo)
        test_timeout_counts_as_killed_and_restores(repo)
        test_interrupt_restores_tree(repo)
        test_fault_injection_this_test_rejects_a_broken_gate(repo, base)

    if FAILURES:
        print(f"mutation gate: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"mutation gate: all checks passed ({time.monotonic() - t0:.1f}s)")
    sys.exit(0)


if __name__ == "__main__":
    main()
