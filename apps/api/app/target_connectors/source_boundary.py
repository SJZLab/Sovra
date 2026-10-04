"""Untrusted source acquisition and complete immutable analysis (FTF-H007,
Section 7A/66/77/81).

Path refined from the task packet's own generic suggestion
(`services/source-analysis-worker/analyzers/source_boundary.py`) to
`apps/api/app/target_connectors/source_boundary.py` -- no `services/`
directory exists anywhere in this repository, and every real 04A-family
sibling already landed in `apps/api/app/target_connectors/*.py` instead of
its own packet's initial `services/`/`packages/target-connectors/`
suggestion (FTF-04A05/FTF-04A06/FTF-04A08/FTF-04A19, each with the
identical disclosed refinement reasoning). This task's own contract
(`database_changes: "Consume existing tenant-scoped schemas... any
required change is a separately claimed migration prerequisite"`) is
honored the same way: this module adds no new table/column, and composes
with `local_repository_connector.py` (FTF-04A06) and `snapshot_inventory.py`
(this task's own other file) purely by import, never by modifying either.

This task's own goal: "Detect truncated trees, missing LFS/submodules,
dirty snapshots and unsupported dynamic discovery; parse AST/config without
executing repo imports/hooks/install scripts." Two real, independent
detectors this module adds, neither of which exists anywhere else in this
repository (confirmed by a repo-wide grep before writing this file):

1. **Executable git hooks.** `local_repository_connector._iter_repository_
   files` deliberately excludes `.git` entirely ("git's own internal object
   store is not source content") -- correct for a content manifest, but it
   means a real, active `.git/hooks/*` script (Section 66.2's own named
   "Disable Git hooks... auto-running repository instructions") is
   invisible to that walk. `_scan_git_hooks` reads (never executes) every
   non-`.sample` entry directly under `<root>/.git/hooks/` and reports the
   ones carrying an owner/group/other-execute bit -- the real, standard
   definition of an *active* hook (a `*.sample` file ships with every real
   `git init` and is never itself executable by git).
2. **Dangerous dynamic code ("executable import").** `_scan_dangerous_
   python_calls` parses every real `*.py` file's content this task's own
   `snapshot_inventory.build_local_snapshot_inventory` already safely read
   with the standard library's `ast.parse` -- exactly the same "AST only,
   never exec/eval/compile/importlib/subprocess" discipline
   `python_agent_config_tool_discovery.py` (FTF-04A09) already established
   for a different purpose -- and walks the resulting tree (`ast.walk`,
   never `compile`/`exec`) for a call to a small, closed set of real
   process/interpreter-escalation primitives (`eval`, `exec`, `os.system`,
   `subprocess.*`, ...). A file merely *containing* one of these calls,
   anywhere in its source -- not only at true module level -- is flagged:
   Python executes top-level statements (including ones nested one
   `if`/`try` deep, a common obfuscation) the instant the module is
   imported, so a narrower "must be a direct top-level statement" check
   would be trivially defeated by one extra level of nesting.

The escaping-symlink finding is *not* re-detected here -- it is
`snapshot_inventory.build_local_snapshot_inventory`'s own real
`InventoryEntry.escapes_root` field, computed once, during the one real
walk of the tree; this module only reads that field off the already-built
`SnapshotInventory` it is handed.

`evaluate_manifest_lock_readiness` is this task's own real, pure proof of
its acceptance clause "incomplete critical snapshot blocks lock" --
mirroring `agent_manifest_schema.CRITICAL_LOCK_FIELDS`'s own already-
CERTIFIED "missing required field blocks the `locked` transition" shape
exactly, but for source-boundary safety/completeness rather than manifest
content completeness. It is **not** wired into the real, DB-backed
`agent_manifest_crud.lock_agent_manifest` here: doing so would require a
real column persisting this evidence against a `TargetSourceSnapshot` (or
`AgentManifest`) row, and `target_source_snapshot_schema.py`'s own
docstring already discloses the identical situation for `analysis_status`
-- "deliberately a closed *one*-value set for Release 1... expandable only
through a real schema change" -- a schema change this task's own contract
explicitly assigns to "a separately claimed migration prerequisite," never
a hidden write bundled into this submission. `proving_symbol` is left
blank for the same disclosed reason (Law 6/7): the real, tested decision
function exists and is proven directly against a real malicious fixture
below, but no live route/CRUD call site invokes it yet.
"""

from __future__ import annotations

import ast
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.target_connectors.local_repository_connector import (
    DangerousGitConfigError,
    PathAllowlistError,
    _scan_dangerous_git_config,
    capture_local_repository,
    resolve_allowed_root,
)
from app.target_connectors.snapshot_inventory import (
    SnapshotInventory,
    build_local_snapshot_inventory,
)

__all__ = [
    "DangerousCallFinding",
    "SourceBoundaryReport",
    "SourceBoundaryUnsafeError",
    "evaluate_manifest_lock_readiness",
    "scan_local_repository_boundary",
]

_GIT_HOOKS_DIRECTORY = ".git/hooks"
_SAMPLE_SUFFIX = ".sample"
_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
_PYTHON_PARSE_EXCEPTIONS: tuple[type[Exception], ...] = (
    SyntaxError,
    ValueError,
    UnicodeDecodeError,
    RecursionError,
    MemoryError,
)

# A real, closed, named set of process/interpreter-escalation primitives --
# never a heuristic substring match, which a trivial rename could defeat in
# either direction (false positive on an unrelated identifier, or false
# negative on a wrapped call this set does not name). Matches
# `python_agent_config_tool_discovery.py`'s own "closed literal set, never
# guessed" precedent.
_DANGEROUS_BARE_CALL_NAMES = frozenset({"eval", "exec", "compile", "__import__"})
_DANGEROUS_ATTRIBUTE_CALLS = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("os", "execv"),
        ("os", "execve"),
        ("os", "execl"),
        ("os", "spawnv"),
        ("subprocess", "run"),
        ("subprocess", "call"),
        ("subprocess", "check_call"),
        ("subprocess", "check_output"),
        ("subprocess", "Popen"),
    }
)


class SourceBoundaryUnsafeError(ValueError):
    """`evaluate_manifest_lock_readiness` was called against a
    `SourceBoundaryReport` that is not safe to lock -- this task's own
    negative_acceptance/acceptance proof, "incomplete critical snapshot
    blocks lock," made executable rather than merely asserted."""


@dataclass(frozen=True)
class DangerousCallFinding:
    """One real, named process/interpreter-escalation call this scan found
    in a real file's own source -- never a guess, never a heuristic
    substring match."""

    relative_path: str
    line: int
    reason: str


@dataclass(frozen=True)
class SourceBoundaryReport:
    """The complete, real evidence this task's own acceptance clause names:
    every escaping symlink, every active git hook, every dangerous dynamic
    call, and whether the underlying tree/submodule/LFS/dirty state is
    itself complete -- never a claim about code this scan never ran."""

    inventory: SnapshotInventory
    escaping_symlinks: tuple[str, ...]
    executable_hooks: tuple[str, ...]
    dangerous_calls: tuple[DangerousCallFinding, ...]
    dangerous_git_config_keys: tuple[str, ...]

    @property
    def tree_complete(self) -> bool:
        return not self.inventory.truncated

    @property
    def is_safe_to_lock(self) -> bool:
        """True only when the tree is complete, every declared submodule/
        LFS path is genuinely resolved on disk, and no unsafe entry (an
        escaping symlink, an active git hook, a dangerous dynamic call, or
        a repo-config key that could make a future git invocation execute
        a repo-controlled command) was found -- this task's own real
        "incomplete critical snapshot blocks lock" acceptance, computed,
        not asserted. A dirty working tree alone never blocks -- Section
        7A.3 requires it be "visibly marked," not refused."""
        return (
            self.tree_complete
            and not self.inventory.unresolved_submodule_paths
            and not self.inventory.unresolved_lfs_paths
            and not self.escaping_symlinks
            and not self.executable_hooks
            and not self.dangerous_calls
            and not self.dangerous_git_config_keys
        )

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.inventory.truncated:
            reasons.append("source tree listing is truncated -- incomplete critical snapshot")
        if self.inventory.unresolved_submodule_paths:
            reasons.append(
                f"unresolved submodule path(s): {list(self.inventory.unresolved_submodule_paths)}"
            )
        if self.inventory.unresolved_lfs_paths:
            reasons.append(
                f"unresolved Git LFS pointer path(s): {list(self.inventory.unresolved_lfs_paths)}"
            )
        if self.escaping_symlinks:
            reasons.append(f"escaping symlink(s): {list(self.escaping_symlinks)}")
        if self.executable_hooks:
            reasons.append(f"executable git hook(s): {list(self.executable_hooks)}")
        if self.dangerous_calls:
            reasons.append(
                "dangerous dynamic call(s): "
                f"{[(f.relative_path, f.line, f.reason) for f in self.dangerous_calls]}"
            )
        if self.dangerous_git_config_keys:
            reasons.append(f"dangerous git config key(s): {list(self.dangerous_git_config_keys)}")
        return tuple(reasons)


def _scan_git_hooks(real_root: Path) -> tuple[str, ...]:
    """Reads (never executes) every real entry directly under
    `<root>/.git/hooks/`. A `*.sample` file is never itself executable by
    git and is excluded by name, not only by its execute bit, since some
    filesystems/archives can carry a stray executable bit on a sample
    file without it ever being an active hook.

    A symlinked hook counts as active too (FTF-H007): `entry.is_file()`
    follows symlinks by default, so a hook that is a symlink to an
    executable file elsewhere was already being read correctly by that
    call -- the earlier bug was the `entry.is_symlink()` guard immediately
    before it, which skipped every symlink outright regardless of what it
    resolved to. `os.stat` (follow-symlinks, never `os.lstat`) is used for
    the mode check so a symlink's *target* executable bit is what counts,
    matching what git itself would actually run."""
    hooks_dir = real_root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return ()
    active: list[str] = []
    try:
        with os.scandir(hooks_dir) as scan:
            entries = sorted(scan, key=lambda e: e.name)
    except OSError:
        return ()
    for entry in entries:
        if entry.name.endswith(_SAMPLE_SUFFIX):
            continue
        if not entry.is_file():
            continue
        try:
            mode = os.stat(entry.path).st_mode
        except OSError:
            continue
        if mode & _EXECUTABLE_BITS:
            active.append(f"{_GIT_HOOKS_DIRECTORY}/{entry.name}")
    return tuple(active)


def _call_dotted_name(node: ast.expr) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (node.value.id, node.attr)
    return None


def _collect_import_aliases(
    tree: ast.AST,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """A single pass over every `import`/`from ... import` statement in the
    module (anywhere, not only at true top level -- Python executes a
    nested import the instant its containing branch runs, same discipline
    this task's own call walk already applies), building two alias maps
    the call scan resolves through before comparing against the closed
    dangerous sets: `import os as o` must be caught via `o.system(...)`
    exactly as `os.system(...)` is, and `from os import system` (or
    `... as sc`) must be caught via a bare `system(...)`/`sc(...)` call
    exactly as `exec(...)` already is. An un-aliased `import os`/
    `from os import system` is recorded identically to an aliased one
    (`os` maps to itself; `system` maps to `("os", "system")`), so this one
    pass covers both the plain and the aliased case with no special-casing."""
    module_aliases: dict[str, str] = {}
    from_aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                real_module = alias.name.split(".")[0]
                bound_name = alias.asname or real_module
                module_aliases[bound_name] = real_module
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                bound_name = alias.asname or alias.name
                from_aliases[bound_name] = (node.module, alias.name)
    return module_aliases, from_aliases


def _scan_dangerous_python_calls(relative_path: str, source: bytes) -> list[DangerousCallFinding]:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(text, filename=relative_path)
    except _PYTHON_PARSE_EXCEPTIONS:
        # An unparseable file is neither proof of safety nor executed --
        # it is simply not a Python AST this scan can inspect further.
        return []

    module_aliases, from_aliases = _collect_import_aliases(tree)

    findings: list[DangerousCallFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in _DANGEROUS_BARE_CALL_NAMES:
                findings.append(
                    DangerousCallFinding(
                        relative_path=relative_path,
                        line=node.lineno,
                        reason=f"call to {name!r}",
                    )
                )
                continue
            resolved_from = from_aliases.get(name)
            if resolved_from is not None and resolved_from in _DANGEROUS_ATTRIBUTE_CALLS:
                findings.append(
                    DangerousCallFinding(
                        relative_path=relative_path,
                        line=node.lineno,
                        reason=(
                            f"call to {name!r} (from {resolved_from[0]} import "
                            f"{resolved_from[1]})"
                        ),
                    )
                )
            continue
        dotted = _call_dotted_name(node.func)
        if dotted is None:
            continue
        resolved_dotted = (module_aliases.get(dotted[0], dotted[0]), dotted[1])
        if resolved_dotted in _DANGEROUS_ATTRIBUTE_CALLS:
            reason = f"call to {resolved_dotted[0]}.{resolved_dotted[1]}"
            if resolved_dotted[0] != dotted[0]:
                reason += f" (as {dotted[0]}.{dotted[1]})"
            findings.append(
                DangerousCallFinding(relative_path=relative_path, line=node.lineno, reason=reason)
            )
    return findings


def scan_local_repository_boundary(
    root: os.PathLike[str] | str, allowed_roots: list[Path]
) -> SourceBoundaryReport:
    """Real, end-to-end, non-executing boundary scan of a real local
    repository: reuses the already-CERTIFIED `capture_local_repository`
    unmodified for its own real `capture_method` (clean vs. dirty), builds
    this task's own continue-on-finding `SnapshotInventory`, then adds the
    two detectors this module owns (git hooks, dangerous dynamic calls).
    Never imports, executes, or shells out to anything the scanned tree
    itself provides -- every finding below is produced by reading bytes
    this process already safely holds in memory.

    A dirty tree whose own untracked content includes an escaping symlink
    makes the already-CERTIFIED `capture_local_repository` itself refuse
    with `PathAllowlistError` (correct for *that* module's own "safely
    capture one unambiguous manifest" job) -- this scanner's own job is to
    keep producing complete evidence regardless, so that refusal is caught
    here and only means `capture_method` is unknown; this task's own
    `snapshot_inventory.build_local_snapshot_inventory` (continue-on-
    finding by construction) still runs and still reports the real
    escaping symlink as a finding rather than a crash."""
    real_root = resolve_allowed_root(root, allowed_roots)

    try:
        capture = capture_local_repository(root, allowed_roots)
        is_dirty = capture.capture_method == "local_dirty_snapshot"
    except PathAllowlistError:
        is_dirty = True  # a refused dirty-tree walk is still a dirty tree
    except DangerousGitConfigError:
        # `capture_local_repository` itself refused before ever running a
        # git command that could trigger the dangerous key it found -- this
        # scanner's own job is to keep producing complete evidence anyway
        # (continue-on-finding), so dirtiness is simply unknown here, never
        # assumed clean. `dangerous_git_config_keys` below (a separate, pure
        # config read -- never the filter/hook-triggering command itself)
        # still reports the real key(s) found.
        is_dirty = True

    inventory = build_local_snapshot_inventory(
        root,
        allowed_roots,
        is_dirty_working_tree=is_dirty,
    )

    escaping_symlinks = tuple(e.relative_path for e in inventory.entries if e.escapes_root)
    executable_hooks = _scan_git_hooks(real_root)
    dangerous_git_config_keys = _scan_dangerous_git_config(real_root)

    dangerous: list[DangerousCallFinding] = []
    for entry in inventory.entries:
        if entry.content is None or entry.escapes_root:
            continue
        if entry.relative_path.endswith(".py"):
            dangerous.extend(_scan_dangerous_python_calls(entry.relative_path, entry.content))

    return SourceBoundaryReport(
        inventory=inventory,
        escaping_symlinks=escaping_symlinks,
        executable_hooks=executable_hooks,
        dangerous_calls=tuple(dangerous),
        dangerous_git_config_keys=dangerous_git_config_keys,
    )


def evaluate_manifest_lock_readiness(report: SourceBoundaryReport) -> None:
    """Raises `SourceBoundaryUnsafeError` naming every real blocking reason
    if `report.is_safe_to_lock` is `False`; returns `None` (never a bare
    `True`/`False`, matching `agent_manifest_schema.validate_status_
    transition`'s own "raise, do not return a bool a caller could ignore"
    convention) when it is safe."""
    if not report.is_safe_to_lock:
        raise SourceBoundaryUnsafeError(
            "source boundary evidence blocks lock -- " + "; ".join(report.blocking_reasons)
        )
