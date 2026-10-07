"""Local repository connector with root allowlist/path protections
(FTF-04A06, Section 6.8A/7A.1/7A.3/9/66/77/81).

Section 7A.3's own "Local repository connector" -- the second of the two
"Minimum production connectors required by DoD" Section 7A.1 names. This
module owns exactly the real, DB-free capture logic a `connector_type=
'local_repository'` `TargetApplicationConnection` (FTF-04A01) needs to
produce the fields `target_source_snapshot_crud.create_target_source_snapshot`
(FTF-04A07) requires: `commit_sha`/`tree_hash`/`capture_method`/`branch`/
`source_archive_object_key`/`source_archive_sha256`. It deliberately does
not persist a `TargetSourceSnapshot` row itself, does not assign a real
tenant-scoped object-storage key (`packages/storage/tenant_policy.py`'s own
`build_tenant_scoped_object_key` needs a real `organization_id`/
`resource_id` this connector has no reason to know), and does not upload
the archive bytes it produces to the real S3-compatible fixture
(`apps/api/app/services/storage_semantics_client.py`, FTF-R017) -- that
composition is a later, separately-scoped caller's job (the same "no live
caller yet" class already accepted for FTF-0005/FTF-0006/FTF-0102/
FTF-0103/FTF-0104/FTF-0105 before each one's own first live caller
existed, and for `storage_semantics_client.py` itself). Declaring any of
that here without a real backing wire-up would be exactly the "placeholder
endpoint" Law 11 forbids; this module ships only what it can make real
today -- a genuine local read, a genuine git inspection, and genuine
content hashing.

Path refined from the task packet's own generic suggestion
(`packages/target-connectors/local_repository_connector_with_root_allowlist_path_protections.py`)
to `apps/api/app/target_connectors/local_repository_connector.py`, matching
FTF-04A01's/FTF-04A07's own real `apps/api/app/target_connectors/*.py`
layout precedent exactly.

Section 7A.3's own literal required capabilities map onto this module's
real functions:

- "user explicitly selects/allowlists repository root during deployment
  configuration" -- `resolve_allowed_root` takes the caller-supplied
  allowlist as an explicit argument; this module never reads or invents
  its own allowlist source (a later deployment-configuration task's job).
- "resolve current Git commit when repository is a Git checkout" /
  "support detached HEAD" -- `_git_head_commit_sha`/`_git_current_branch`.
- "reject paths outside allowlisted roots" / "do not follow symlinks
  outside allowlisted root" -- `resolve_allowed_root` (the root itself) and
  `_walk_repository_content` (every file/directory encountered while
  building the content manifest), both real `os.path.realpath` checks
  against the resolved allowlist, never a string-prefix check a
  `../`-style traversal could defeat.
- "read files without modifying working tree" -- every filesystem
  operation in this module is a read (`open(..., "rb")`, `os.scandir`,
  `os.stat`) or a real, documented read-only git plumbing command
  (`git rev-parse`, `git symbolic-ref`, `git status --porcelain`); nothing
  here ever calls `git add`/`git commit`/`git checkout` or opens a file for
  writing inside the connection's own root.
- "report dirty-tree state" / "for dirty trees, create a content-hashed
  snapshot and visibly mark it non_committed_source" -- `capture_local_repository`
  returns `capture_method='local_dirty_snapshot'` (the schema's own real
  closed-set marker, see `target_source_snapshot_schema.py`) whenever the
  working tree differs from `HEAD` in any way (`git status --porcelain`
  reports anything at all, tracked or untracked), together with a real
  `sha256`-hashed, deterministic `tar` archive of the actual working-tree
  content at the moment of capture -- not merely git's own `HEAD^{tree}`
  object, which would describe the last *commit*, not the real dirty tree
  a production evaluation would actually run against.

This task's own negative_acceptance ("Path/symlink traversal and
concurrent file drift fail; never edit the target working tree") is two
real, independently-tested guards:

1. Path/symlink traversal: `resolve_allowed_root` refuses (before any
   directory is even opened) a root that does not resolve, after following
   every real symlink, inside one of the caller's own allowlisted roots.
   `_walk_repository_content` applies the identical real-path check to
   every symlink it encounters while walking the tree -- a symlink whose
   real target resolves outside the connection's own root is refused, not
   silently skipped or silently followed.
2. Concurrent file drift: `capture_local_repository` records the working
   tree's real git identity (`HEAD` commit sha plus the exact `git status
   --porcelain` output) both immediately before and immediately after
   building the content manifest/archive; a real change during that window
   (a file added/edited/removed by a concurrent process while this capture
   was in flight) makes the two identities disagree, and the capture is
   refused (`ConcurrentModificationError`) rather than silently returning a
   snapshot that matches neither the before nor the after state.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ConcurrentModificationError",
    "DangerousGitConfigError",
    "EmptyRepositoryError",
    "GitCommandError",
    "LocalRepositoryCapture",
    "LocalRepositoryConnectorError",
    "ManifestEntry",
    "NotAGitRepositoryError",
    "PathAllowlistError",
    "PopulatedSubmoduleError",
    "capture_local_repository",
    "resolve_allowed_root",
    "scan_submodule_declarations",
]

_GIT_DIRECTORY_NAME = ".git"
_SUBPROCESS_TIMEOUT_SECONDS = 30.0

# A real, closed, named set of git config keys that can make an otherwise
# read-only git invocation execute a repo-controlled command: `core.fsmonitor`/
# `core.hooksPath`/`core.sshCommand`, or any `filter.<name>.(clean|smudge|
# process|required)` driver (FTF-H007/FTF-04A06's own L2 round-2/round-3
# findings). `filter.<name>.clean`/`process` in particular can run during a
# plain `git status` on a stat-dirty tracked file carrying a matching
# `.gitattributes` `filter=<name>` entry -- `_run_git`'s own `-c` overrides
# neutralize `core.fsmonitor`/`core.hooksPath` for every call this process
# makes, but cannot neutralize an arbitrary, repo-chosen filter driver *name*
# the same way, since the name itself is not known in advance. The only
# reliable defense is to detect the key's mere presence before ever running a
# git command that would invoke it, and refuse outright (see
# `capture_local_repository`, which calls `_scan_dangerous_git_config` before
# its own first `_git_dirty_signature` call -- the first command in this
# module able to trigger a filter driver). `filter.<name>.required` alone
# (no clean/smudge/process) is not itself an execution vector -- it never
# runs a repo-controlled command -- but round-4's own direct confirmation
# against a real git binary corrects the round-3 claim above: a same-size
# stat-dirty tracked file (content byte-identical, mtime changed -- git's
# own stat cache can no longer short-circuit the comparison) carrying a
# matching `.gitattributes` `filter=<name>` entry makes a plain `git
# status` genuinely FAIL non-zero ("clean filter '<name>' failed"), since
# `required=true` demands a clean/smudge/process driver that was never
# configured. This is a failure, never an execution, so it still stays out
# of `_UNNEUTRALIZABLE_FILTER_DRIVER_KEY_PATTERN` below (that pattern names
# only keys needing to block capture before an execution vector is
# reached) -- but it does mean `capture_local_repository`'s own
# `_git_dirty_signature` call can raise `GitCommandError` for this key
# alone. `scan_local_repository_boundary` (the continue-on-finding scanner)
# catches exactly that failure and still reports the key via
# `_scan_dangerous_git_config` below, rather than letting the exception
# propagate uncaught (round-4 fix, FTF-H007/FTF-04A06).
_DANGEROUS_GIT_CONFIG_KEY_PATTERN = (
    r"^(core\.fsmonitor|core\.hookspath|core\.sshcommand|"
    r"filter\..+\.(clean|smudge|process|required))$"
)

# The strict subset of the pattern above that `_run_git`'s own `-c`
# overrides cannot neutralize: a `filter.<name>.(clean|smudge|process)`
# driver's *name* is chosen by the untrusted repository itself, so there is
# no fixed `-c filter.<name>.clean=...` override this module could pass in
# advance the way it can for the two fixed, already-known keys `core.
# fsmonitor`/`core.hooksPath`. `capture_local_repository` pre-scans for
# exactly this subset (never the full pattern above -- `core.fsmonitor`/
# `core.hooksPath` are already safely neutralized for every call this
# module makes, and FTF-04A06's own established behavior is to proceed with
# the capture once neutralized, not refuse outright) and refuses before its
# own first git call able to trigger one. `required` alone is excluded, see
# the docstring on the pattern above.
_UNNEUTRALIZABLE_FILTER_DRIVER_KEY_PATTERN = r"^filter\..+\.(clean|smudge|process)$"

# A real gitlink (mode 160000) in the index -- `git ls-files -s -z`'s own
# first whitespace-delimited field for an entry whose path follows a tab.
_GITLINK_MODE = "160000"


class LocalRepositoryConnectorError(Exception):
    """Base class for every real, refused local-repository capture
    outcome this module raises."""


class PathAllowlistError(LocalRepositoryConnectorError):
    """The requested root, or a real path encountered while walking it,
    resolves outside every allowlisted root -- this task's own real
    "reject paths outside allowlisted roots" / "do not follow symlinks
    outside allowlisted root" negative_acceptance."""


class NotAGitRepositoryError(LocalRepositoryConnectorError):
    """The allowlisted root is not a real Git working tree (or is a
    subdirectory of one rather than a real top level) -- Section 7A.3's own
    "resolve current Git commit when repository is a Git checkout" implies
    a real Git checkout is required; this connector does not invent
    provenance for a plain, non-versioned directory."""


class EmptyRepositoryError(LocalRepositoryConnectorError):
    """A real Git working tree with no commits at all (`HEAD` does not
    resolve) -- there is no real commit_sha to report, so no snapshot can
    be captured yet."""


class GitCommandError(LocalRepositoryConnectorError):
    """A real git plumbing command exited non-zero for a reason other than
    the specific, already-named cases above -- never silently swallowed."""


class ConcurrentModificationError(LocalRepositoryConnectorError):
    """The working tree's real git identity changed between the start and
    the end of this capture -- this task's own real "concurrent file drift"
    negative_acceptance."""


class DangerousGitConfigError(LocalRepositoryConnectorError):
    """The repository's own resolved git config (local scope, worktree
    scope, and every `include.path`/`includeIf.*.path` it names -- never
    system/global, which `_run_git`'s own `GIT_CONFIG_NOSYSTEM`/
    `GIT_CONFIG_GLOBAL` isolation already excludes) carries a key that could
    make a real git invocation execute a repo-controlled command
    (FTF-H007/FTF-04A06's own L2 round-2 finding). Raised *before* this
    module ever runs a git command capable of triggering one -- never
    discovered only after the fact."""


class PopulatedSubmoduleError(LocalRepositoryConnectorError):
    """A real, populated gitlink (a `<path>/.git` genuinely exists on disk,
    plain or registered) was found in the index (FTF-H007/FTF-04A06's own
    L2 round-3 finding 1). `git status` recurses into exactly this kind of
    path, loading the submodule's own config and reaching its own filter/
    hook drivers -- confirmed directly: a real `filter.<name>.clean`
    command set only inside a populated submodule's own config genuinely
    executes during the *superproject's* plain `git status`, both for a
    manually-added gitlink and for one registered via `git submodule add`.
    `--ignore-submodules=all`/`-c submodule.recurse=false` (see
    `_git_dirty_signature`) already stop this for every status call this
    module makes; this is a second, independent layer -- raised *before*
    that call, from a pure plumbing read (`git ls-files -s`) that cannot
    itself trigger a submodule's filter driver -- since submodules are out
    of scope for an untrusted-source capture entirely (Control-plane
    decision), not merely something to neutralize and proceed past the way
    `core.fsmonitor`/`core.hooksPath` are."""


# Mutable at the module level, not bound as a function-default value, so a
# test can monkeypatch the actual constant and exercise the real `_run_git`
# code path unmodified (FTF-H007/FTF-04A06's own L2 round-3 finding 2: a
# test that instead swaps out `_run_git` itself for a stub proves nothing
# about the real call). A `tuple` default parameter value is bound once, at
# function-definition time -- reassigning the module attribute afterward
# would never be seen by a plain `config_overrides: tuple = _SAFETY_CONFIG_
# OVERRIDES` default, so `_run_git` below instead looks this name up fresh,
# as a global, on every call.
_SAFETY_CONFIG_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("core.fsmonitor", "false"),
    ("core.hooksPath", "/dev/null"),
    ("submodule.recurse", "false"),
)

# Same rationale as `_SAFETY_CONFIG_OVERRIDES` above, for the two ambient
# environment variables `_run_git` sets unconditionally: a test exercising
# "env isolation removed" must flip these real module attributes and call
# the real `_run_git`/`capture_local_repository`, never a stand-in function.
# `None` means "do not override -- leave this process's own ambient
# environment variable exactly as inherited" (as opposed to setting it to
# the empty string, which git treats differently).
_GIT_CONFIG_NOSYSTEM_ENV_VALUE: str | None = "1"
_GIT_CONFIG_GLOBAL_ENV_VALUE: str | None = "/dev/null"

# Same rationale again, for `_git_dirty_signature`'s own `--ignore-
# submodules=all` flag (FTF-H007/FTF-04A06's own L2 round-3 finding 1).
_GIT_STATUS_IGNORE_SUBMODULES_FLAG: str | None = "--ignore-submodules=all"


def _run_git(
    root: Path,
    *args: str,
    config_overrides: tuple[tuple[str, str], ...] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Every invocation is hardened against a repo-controlled config value
    making this supposedly read-only call execute an arbitrary command
    (FTF-H007/FTF-04A06): `core.fsmonitor` can be set to a command git
    itself runs on every `status`-family call (a real, documented git
    feature, not a hypothetical), and `core.hooksPath` can point hook-style
    execution at attacker-controlled scripts. `-c` values are highest
    config precedence, so these override any value the untrusted
    repository's own `.git/config` sets, regardless of its own content.
    `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null` additionally stop
    this process's own ambient system/global git config -- which this
    untrusted scan has no business reading at all -- from influencing the
    result; unlike `config_overrides`, this isolation is unconditional and
    cannot be turned off by a caller.

    `config_overrides` defaults to the two safety overrides above, applied
    to every working-tree-content git command this module makes (`status`,
    the clean-checkout `HEAD^{tree}` resolution). `_scan_dangerous_git_config`
    below passes `()` instead: a `-c core.fsmonitor=false` override is
    itself a real, highest-precedence config value, so leaving it in would
    make `git config --get-regexp` echo back *this process's own* benign
    override rather than the real value (if any) the untrusted repository
    itself set for that exact key -- silently masking the one thing this
    scan exists to detect. A pure config read never triggers a filter/hook/
    fsmonitor command on its own, so omitting these two overrides for that
    one call is still safe. `config_overrides=None` (the default) resolves
    to the real, current `_SAFETY_CONFIG_OVERRIDES` module global at call
    time, not a value bound when this function was defined -- see that
    global's own docstring for why. The two env-isolation values are
    resolved the same way, from `_GIT_CONFIG_NOSYSTEM_ENV_VALUE`/
    `_GIT_CONFIG_GLOBAL_ENV_VALUE`."""
    git_binary = shutil.which("git")
    if git_binary is None:
        raise GitCommandError("no 'git' binary found on PATH")
    if config_overrides is None:
        config_overrides = _SAFETY_CONFIG_OVERRIDES
    env = dict(os.environ)
    # `None` leaves this process's own ambient environment variable exactly
    # as `dict(os.environ)` already copied it above -- neither set nor
    # popped -- so a fault-injection test can set a real, external value in
    # `os.environ` beforehand (standing in for whatever an unisolated
    # ambient environment might already contain) and observe it leak
    # through once this module's own override is turned off, without this
    # line then erasing that external value itself.
    if _GIT_CONFIG_NOSYSTEM_ENV_VALUE is not None:
        env["GIT_CONFIG_NOSYSTEM"] = _GIT_CONFIG_NOSYSTEM_ENV_VALUE
    if _GIT_CONFIG_GLOBAL_ENV_VALUE is not None:
        env["GIT_CONFIG_GLOBAL"] = _GIT_CONFIG_GLOBAL_ENV_VALUE
    config_args: list[str] = []
    for key, value in config_overrides:
        config_args.extend(["-c", f"{key}={value}"])
    try:
        return subprocess.run(  # noqa: S603
            [git_binary, *config_args, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitCommandError(f"git {' '.join(args)} could not be run: {exc}") from exc


def _scan_dangerous_git_config(
    root: Path, pattern: str = _DANGEROUS_GIT_CONFIG_KEY_PATTERN
) -> tuple[str, ...]:
    """Reads (never executes) the repository's own fully-resolved git
    config for `pattern`'s own keys (`_DANGEROUS_GIT_CONFIG_KEY_PATTERN` by
    default -- the full reportable set; `capture_local_repository`'s own
    pre-scan instead passes `_UNNEUTRALIZABLE_FILTER_DRIVER_KEY_PATTERN`,
    the strict subset it must refuse on rather than merely report),
    deliberately
    **unscoped** -- no `--local`/`--worktree` restriction, and no
    `--no-includes` -- so this one real git invocation naturally resolves
    every scope and `include.path`/`includeIf.*.path` directive the
    repository's own local config and `.git/config.worktree` (when
    `extensions.worktreeConfig` is enabled) actually name, exactly the way
    any later git command in this process would resolve them. `--local`
    alone, as this module used before this fix, follows neither includes
    nor worktree scope by default (confirmed directly against a real git
    binary) -- the exact round-2 L2 finding (b) this function closes.

    System/global config is still excluded: `_run_git`'s own
    `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` environment
    isolation applies here too (this call goes through `_run_git`), so
    "unscoped" in practice means "every scope this untrusted repository
    itself can define," never this process's own ambient config.

    Uses `_run_git`'s own hardened invocation to do the reading -- a pure
    config read never triggers a filter/hook/fsmonitor command on its own,
    so this call itself cannot be the vector it is checking for. Passes
    `config_overrides=()`: see `_run_git`'s own docstring for why the
    default safety overrides must NOT be applied to this specific call --
    they would mask the real `core.fsmonitor`/`core.hooksPath` value (if
    any) the untrusted repository itself set with this process's own benign
    override, defeating detection for exactly those two keys.

    Round-3 fixes: `--null` (NUL-terminated records, key and value split on
    the first newline, never a space) replaces the prior "split the line on
    the first space" parsing -- confirmed directly against a real git
    binary that a quoted subsection containing a space (`[filter "a.b c"]`)
    makes the old parsing truncate the key at the wrong point, since the
    *value* can also contain spaces and the old code could not tell the two
    apart. `git config --get-regexp` exits `1` for "no key matched" (a real,
    documented, genuinely benign outcome) and non-zero for a real failure
    (a malformed config, for instance) -- only the former returns `()`; any
    other non-zero exit now fails closed (`GitCommandError`), not a silent
    `()` that would make `is_safe_to_lock` incorrectly `True`."""
    result = _run_git(root, "config", "--null", "--get-regexp", pattern, config_overrides=())
    if result.returncode == 1:
        return ()
    if result.returncode != 0:
        raise GitCommandError(
            f"{root}: git config --get-regexp failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    keys: list[str] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        key = record.split("\n", 1)[0]
        if key not in keys:
            keys.append(key)
    return tuple(sorted(keys))


def _scan_populated_gitlinks(root: Path) -> tuple[str, ...]:
    """Pure plumbing read (`git ls-files -s`, never executed, never a
    working-tree-*content* command -- but confirmed directly against a
    real git binary to still trigger `core.fsmonitor`, so this call keeps
    the default `_SAFETY_CONFIG_OVERRIDES`, unlike `_scan_dangerous_git_
    config`'s own config-reading call, which must omit them to avoid
    masking the very value it is checking for) of every real gitlink (mode
    `160000`) the index records, filtered to the ones genuinely populated
    on disk -- a real `<path>/.git` (file, for a registered submodule, or
    directory, for a plain one) actually exists. `git status` recurses into
    exactly a populated gitlink, loading its own config and reaching its
    own filter/hook drivers (FTF-H007/FTF-04A06's own L2 round-3 finding 1,
    confirmed directly against a real git binary for both a plain and a
    registered submodule). An unpopulated gitlink (declared but never
    checked out) has no `.git` of its own, so there is nothing for `git
    status` to recurse into and nothing unsafe to refuse here --
    `source_boundary.py`'s own `scan_submodule_declarations` still reports
    its mere declaration as a blocking lock-readiness finding regardless of
    population state."""
    result = _run_git(root, "ls-files", "-s", "-z")
    if result.returncode != 0:
        raise GitCommandError(f"{root}: git ls-files -s failed: {result.stderr.strip()}")
    populated: list[str] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        mode = meta.split(" ", 1)[0]
        if mode != _GITLINK_MODE:
            continue
        if (root / path / ".git").exists():
            populated.append(path)
    return tuple(sorted(populated))


def _scan_gitlink_paths(root: Path, *args: str) -> tuple[str, ...]:
    """Shared parsing for the two real, NUL-terminated plumbing commands
    `scan_submodule_declarations` below reads: `git ls-tree -r -z HEAD`
    (`<mode> <type> <sha>\\t<path>`) and `git ls-files -s -z`
    (`<mode> <sha> <stage>\\t<path>`) -- in both, the mode is the first
    whitespace-delimited field before the tab. Round-5 fix (FTF-H007/
    FTF-04A06 finding 2): a non-zero exit now raises `GitCommandError`,
    matching `_scan_populated_gitlinks` above -- a failed read (e.g. a
    corrupt index, so `ls-files` cannot enumerate it) must never be
    silently treated as "no entries," which previously made a repository
    this scan could not actually read look clean. A genuinely unborn
    `HEAD` never reaches this call: `capture_local_repository`'s own
    `_git_head_commit_sha` already raises `EmptyRepositoryError` for that
    case before `scan_local_repository_boundary` ever calls
    `scan_submodule_declarations`. Keeps the default
    `_SAFETY_CONFIG_OVERRIDES`, same reasoning as `_scan_populated_gitlinks`
    above."""
    result = _run_git(root, *args)
    if result.returncode != 0:
        raise GitCommandError(
            f"{root}: git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    paths: list[str] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        mode = meta.split(" ", 1)[0]
        if mode == _GITLINK_MODE:
            paths.append(path)
    return tuple(paths)


def scan_submodule_declarations(root: Path) -> tuple[str, ...]:
    """Every real submodule this repository declares -- a gitlink (mode
    `160000`) in `HEAD^{tree}` (committed) or the index (staged, possibly
    uncommitted), or a plain `.gitmodules` file with no matching gitlink
    yet -- populated or not. Control-plane decision (round-3 route for
    finding 1): submodules are out of scope for an untrusted-source capture
    entirely, so `source_boundary.scan_local_repository_boundary` reports
    every one of these as a blocking lock-readiness finding, never only the
    unresolved ones `snapshot_inventory.build_local_snapshot_inventory`
    already reports separately."""
    paths: set[str] = set()
    paths.update(_scan_gitlink_paths(root, "ls-tree", "-r", "-z", "HEAD"))
    paths.update(_scan_gitlink_paths(root, "ls-files", "-s", "-z"))
    if (root / ".gitmodules").is_file():
        paths.add(".gitmodules")
    return tuple(sorted(paths))


def resolve_allowed_root(root: os.PathLike[str] | str, allowed_roots: list[Path]) -> Path:
    """Real-path-resolves `root` (following every symlink) and refuses it
    unless it is, or is a real descendant of, one of `allowed_roots`
    (themselves real-path-resolved first, so an allowlist entry that is
    itself a symlink cannot be used to smuggle in a wider boundary than the
    deployment operator actually approved).

    Never a string-prefix comparison: `/approved-evil` is not treated as a
    descendant of `/approved` just because the text matches.
    """
    if not allowed_roots:
        raise PathAllowlistError("no local repository root is allowlisted -- refusing by default")

    try:
        real_root = Path(os.path.realpath(root, strict=True))
    except OSError as exc:
        raise PathAllowlistError(f"root {root!r} does not resolve to a real path: {exc}") from exc

    for candidate in allowed_roots:
        try:
            real_allowed = Path(os.path.realpath(candidate, strict=True))
        except OSError:
            continue
        if real_root == real_allowed or real_allowed in real_root.parents:
            return real_root

    raise PathAllowlistError(
        f"root {real_root} is not inside any allowlisted root ({allowed_roots!r})"
    )


@dataclass(frozen=True)
class ManifestEntry:
    """One real file's identity in the content manifest: a POSIX-style
    path relative to the captured root, its real content sha256, its exact
    byte size, and whether its owner-execute bit was set."""

    relative_path: str
    sha256: str
    size: int
    executable: bool


def _iter_repository_files(root: Path) -> list[ManifestEntry]:
    """Real, recursive, symlink-safe walk of every regular file under
    `root`, excluding `.git` itself (git's own internal object store is not
    source content). A symlink (file or directory) whose real target
    resolves outside `root` is refused outright; one that resolves inside
    `root` is followed exactly once (cycle-safe via `_visited_directories`).
    """
    entries: list[ManifestEntry] = []
    visited_directories: set[Path] = set()

    def _walk(directory: Path) -> None:
        real_directory = Path(os.path.realpath(directory, strict=True))
        if real_directory in visited_directories:
            return
        visited_directories.add(real_directory)

        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda e: e.name)
        except OSError as exc:
            raise LocalRepositoryConnectorError(
                f"cannot read directory {directory}: {exc}"
            ) from exc

        for entry in children:
            if directory == root and entry.name == _GIT_DIRECTORY_NAME:
                continue

            entry_path = Path(entry.path)
            if entry.is_symlink():
                try:
                    real_target = Path(os.path.realpath(entry_path, strict=True))
                except OSError as exc:
                    raise PathAllowlistError(
                        f"symlink {entry_path} does not resolve to a real path: {exc}"
                    ) from exc
                if real_target != root and root not in real_target.parents:
                    raise PathAllowlistError(
                        f"symlink {entry_path} resolves outside the captured root {root}: "
                        f"{real_target}"
                    )
                if real_target.is_dir():
                    _walk(entry_path)
                    continue
                target_for_read = real_target
            else:
                target_for_read = entry_path

            if entry.is_dir(follow_symlinks=True) and not entry.is_symlink():
                _walk(entry_path)
                continue

            if not target_for_read.is_file():
                continue

            try:
                data = target_for_read.read_bytes()
            except OSError as exc:
                raise LocalRepositoryConnectorError(
                    f"cannot read file {entry_path}: {exc}"
                ) from exc
            relative = entry_path.relative_to(root).as_posix()
            mode = target_for_read.stat().st_mode
            entries.append(
                ManifestEntry(
                    relative_path=relative,
                    sha256=hashlib.sha256(data).hexdigest(),
                    size=len(data),
                    executable=bool(mode & 0o100),
                )
            )

    _walk(root)
    return sorted(entries, key=lambda e: e.relative_path)


def _manifest_hash(entries: list[ManifestEntry]) -> str:
    """A real, deterministic sha256 over the sorted (path, hash, size,
    executable) tuples -- passes `TargetSourceSnapshot`'s own
    `validate_tree_hash` (a 64-lowercase-hex value is an accepted git
    object-id-shaped identifier, exactly like a real sha256 git object
    would be), while actually describing this connector's own real
    file-level content manifest rather than git's own committed tree."""
    canonical = json.dumps(
        [[e.relative_path, e.sha256, e.size, e.executable] for e in entries],
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _build_archive(root: Path, entries: list[ManifestEntry]) -> bytes:
    """A real, deterministic tar archive of every file the manifest
    describes -- fixed timestamps/ownership/ordering so two captures of
    byte-identical content always produce byte-identical archive bytes and
    therefore the same `source_archive_sha256`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            data = (root / entry.relative_path).read_bytes()
            # A file edited between the manifest walk and this read (an
            # already-dirty or untracked file keeps the same `git status`
            # line) would otherwise yield a torn snapshot: archive bytes that
            # disagree with the manifest/`tree_hash` (FTF-04A32 N2).
            if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
                raise ConcurrentModificationError(
                    f"{root}: {entry.relative_path} changed while the archive was being built"
                )
            info = tarfile.TarInfo(name=entry.relative_path)
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o755 if entry.executable else 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _git_is_real_worktree_root(root: Path) -> bool:
    result = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if result.returncode != 0 or result.stdout.strip() != "true":
        return False
    toplevel = _run_git(root, "rev-parse", "--show-toplevel")
    if toplevel.returncode != 0:
        return False
    try:
        real_toplevel = Path(os.path.realpath(toplevel.stdout.strip(), strict=True))
    except OSError:
        return False
    return real_toplevel == root


def _git_head_commit_sha(root: Path) -> str:
    result = _run_git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    if result.returncode != 0 or not result.stdout.strip():
        raise EmptyRepositoryError(f"{root} has no commits yet (HEAD does not resolve)")
    return result.stdout.strip()


def _git_head_tree_hash(root: Path) -> str:
    result = _run_git(root, "rev-parse", "--verify", "--quiet", "HEAD^{tree}")
    if result.returncode != 0 or not result.stdout.strip():
        raise GitCommandError(f"{root}: HEAD^{{tree}} did not resolve")
    return result.stdout.strip()


def _git_current_branch(root: Path) -> str | None:
    result = _run_git(root, "symbolic-ref", "-q", "--short", "HEAD")
    if result.returncode != 0:
        return None  # detached HEAD -- Section 7A.3's own "support detached HEAD"
    branch = result.stdout.strip()
    return branch or None


def _git_dirty_signature(root: Path) -> str:
    """Real working-tree status text (tracked and untracked changes alike)
    -- the identity this module compares before/after a capture to detect
    concurrent drift. Not parsed further; only ever compared for equality.

    `--ignore-submodules=all` (FTF-H007/FTF-04A06's own L2 round-3 finding
    1): without it, `git status` recurses into a real, populated gitlink
    and loads *its own* config, reaching a filter driver this module's own
    `-c`/env overrides cannot neutralize generically (the driver name is
    submodule-chosen, same reasoning as `_UNNEUTRALIZABLE_FILTER_DRIVER_KEY_
    PATTERN`'s own docstring) -- confirmed directly against a real git
    binary, for both a plain and a registered submodule, that this flag
    alone (with `-c submodule.recurse=false`, already in `_SAFETY_CONFIG_
    OVERRIDES`, confirmed alone NOT sufficient) stops the recursion.
    `capture_local_repository`'s own `_scan_populated_gitlinks` pre-scan is
    a second, independent layer, refusing before this call runs at all.

    Sourced from the module-level `_GIT_STATUS_IGNORE_SUBMODULES_FLAG`, not
    a literal in this function body, for the identical reason
    `_SAFETY_CONFIG_OVERRIDES` is: a test proving this one flag is
    load-bearing must flip the real module attribute and call the real
    `_git_dirty_signature`/`capture_local_repository`, never a stand-in."""
    status_args = ["status", "--porcelain=v1", "--untracked-files=all"]
    if _GIT_STATUS_IGNORE_SUBMODULES_FLAG is not None:
        status_args.append(_GIT_STATUS_IGNORE_SUBMODULES_FLAG)
    result = _run_git(root, *status_args)
    if result.returncode != 0:
        raise GitCommandError(f"{root}: git status failed: {result.stderr.strip()}")
    return result.stdout


@dataclass(frozen=True)
class LocalRepositoryCapture:
    """The real fields a caller needs to persist a `TargetSourceSnapshot`
    (`target_source_snapshot_crud.create_target_source_snapshot`) for this
    capture -- this module never calls that function itself (see this
    module's own docstring for the disclosed scope boundary)."""

    capture_method: str
    commit_sha: str
    tree_hash: str
    branch: str | None
    manifest_entries: tuple[ManifestEntry, ...]
    source_archive_bytes: bytes | None
    source_archive_sha256: str | None


def capture_local_repository(
    root: os.PathLike[str] | str, allowed_roots: list[Path]
) -> LocalRepositoryCapture:
    """Real end-to-end local-repository capture: validate the root against
    the allowlist, confirm it is a real Git working-tree top level, then
    either resolve the clean committed state or build a real content-hashed
    dirty-tree archive -- never both, and never a silent third outcome.
    """
    real_root = resolve_allowed_root(root, allowed_roots)

    if not _git_is_real_worktree_root(real_root):
        raise NotAGitRepositoryError(
            f"{real_root} is not a real Git working-tree top level -- "
            "Section 7A.3's local repository connector requires a real Git checkout"
        )

    # Checked before any command below that can trigger a filter driver
    # (`_git_dirty_signature`'s own `git status`, first) -- never after.
    # `rev-parse`/`symbolic-ref` above are pure ref reads and cannot invoke
    # a working-tree filter, so running this check here, rather than before
    # them too, still closes the window completely. Only the strict,
    # unneutralizable filter-driver subset blocks the capture itself --
    # `core.fsmonitor`/`core.hooksPath` stay handled purely by `_run_git`'s
    # own `-c` overrides, exactly as FTF-04A06's own already-established
    # behavior expects (neutralized, capture proceeds); the full set
    # (including those two) is still reported as blocking for *lock*
    # readiness by `source_boundary.scan_local_repository_boundary`.
    dangerous_keys = _scan_dangerous_git_config(
        real_root, pattern=_UNNEUTRALIZABLE_FILTER_DRIVER_KEY_PATTERN
    )
    if dangerous_keys:
        raise DangerousGitConfigError(
            f"{real_root} sets unneutralizable filter driver key(s) {list(dangerous_keys)} -- "
            "refusing before any working-tree-content git command could trigger one"
        )

    # Checked for the identical reason, same place in the call order: a
    # populated gitlink's own config is reached by `git status`'s recursion
    # into it, not by anything this process's own `-c`/env overrides can
    # name in advance (FTF-H007/FTF-04A06's own L2 round-3 finding 1). Pure
    # plumbing (`git ls-files -s`), cannot itself trigger a filter driver.
    populated_gitlinks = _scan_populated_gitlinks(real_root)
    if populated_gitlinks:
        raise PopulatedSubmoduleError(
            f"{real_root} has populated submodule path(s) {list(populated_gitlinks)} -- "
            "submodules are out of scope for an untrusted-source capture; refusing before "
            "any working-tree-content git command could recurse into one"
        )

    commit_sha = _git_head_commit_sha(real_root)
    branch = _git_current_branch(real_root)
    dirty_before = _git_dirty_signature(real_root)

    if not dirty_before:
        tree_hash = _git_head_tree_hash(real_root)
        dirty_after = _git_dirty_signature(real_root)
        head_after = _run_git(real_root, "rev-parse", "--verify", "--quiet", "HEAD").stdout.strip()
        if dirty_after != dirty_before or head_after != commit_sha:
            raise ConcurrentModificationError(
                f"{real_root} changed while this clean capture was in flight"
            )
        return LocalRepositoryCapture(
            capture_method="local_git_commit",
            commit_sha=commit_sha,
            tree_hash=tree_hash,
            branch=branch,
            manifest_entries=(),
            source_archive_bytes=None,
            source_archive_sha256=None,
        )

    entries = _iter_repository_files(real_root)
    archive_bytes = _build_archive(real_root, entries)
    tree_hash = _manifest_hash(entries)

    dirty_after = _git_dirty_signature(real_root)
    head_after = _run_git(real_root, "rev-parse", "--verify", "--quiet", "HEAD").stdout.strip()
    if dirty_after != dirty_before or head_after != commit_sha:
        raise ConcurrentModificationError(
            f"{real_root} changed while this dirty-tree capture was in flight"
        )
    # `git status` text cannot see a content edit to an already-dirty or
    # untracked file; re-walking the content manifest can (FTF-04A32 N2).
    if _manifest_hash(_iter_repository_files(real_root)) != tree_hash:
        raise ConcurrentModificationError(
            f"{real_root} content changed while this dirty-tree capture was in flight"
        )

    return LocalRepositoryCapture(
        capture_method="local_dirty_snapshot",
        commit_sha=commit_sha,
        tree_hash=tree_hash,
        branch=branch,
        manifest_entries=tuple(entries),
        source_archive_bytes=archive_bytes,
        source_archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
    )
