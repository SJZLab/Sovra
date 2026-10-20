#!/usr/bin/env python3
"""FTF-H007 -- Untrusted source acquisition and complete immutable analysis
(no-DB unit/negative-acceptance tests).

Runnable without Postgres, network egress, or any object-storage fixture --
this task's own scope is a pure, DB-free analysis pass over an already-
allowlisted local repository (see `apps/api/app/target_connectors/
source_boundary.py`'s own docstring for the disclosed "no live caller yet,
pending a real persisted-evidence column" scope boundary). Every check
here builds a *real* local Git repository with the real `git` binary in a
real temporary directory -- never a mocked git command or a canned scan
result -- matching `test_ftf_04a06.py`'s own established fixture
convention exactly.

This suite proves this task's own real acceptance clause: "Real malicious
reference repo includes an executable import/hook and escaping symlink;
scan produces evidence without execution/escape and incomplete critical
snapshot blocks lock":

1. A clean, ordinary repository scans as safe-to-lock with zero findings,
   and `evaluate_manifest_lock_readiness` does not raise.
2. A real malicious repository -- a Python file whose source contains an
   `os.system(...)` call, an active (non-`.sample`, executable)
   `.git/hooks/pre-push` script, a symlink whose real target resolves
   outside the repository root, a `.gitattributes`-declared Git-LFS path
   whose real on-disk content is still an unresolved pointer stub, and a
   `.gitmodules`-declared submodule path that is a real, empty, un-
   initialized directory -- is scanned end to end:
   - every one of the five findings is present in the real returned
     `SourceBoundaryReport`;
   - `is_safe_to_lock` is `False` and `evaluate_manifest_lock_readiness`
     raises `SourceBoundaryUnsafeError` naming every real reason -- this
     task's own "incomplete critical snapshot blocks lock";
   - the `os.system` call was never actually executed (its own marker file
     is confirmed absent) and the `.git/hooks/pre-push` script was never
     actually executed (its own, separate marker file is confirmed
     absent) -- this task's own "without execution";
   - the real secret content behind the escaping symlink is never read
     into any part of the returned report -- this task's own "without ...
     escape."
3. `test_escaping_symlink_alone_blocks_lock_in_isolation` isolates the
   escaping-symlink check from the other four detectors, so a regression in
   that one check specifically cannot hide behind one of the other
   independently-blocking findings the combined malicious-repository case
   above also exercises. Own fault injection (Law 1, performed manually
   against the real source during this task's own development, reverted
   byte-identical before this submission -- see PROGRESS.md/QUEUE.md for
   the exact reproduction): disabling `snapshot_inventory`'s own escaping-
   symlink check made this isolated case genuinely fail (`escaping_symlinks`
   became empty and `is_safe_to_lock` incorrectly became `True`), while the
   combined malicious-repository case above still failed for its own other
   four reasons -- confirming the isolated case, not only the combined one,
   is required to actually prove this one check is load-bearing.

Run directly: python3 tests/tasks/test_ftf_h007.py
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors import local_repository_connector as _connector_module  # noqa: E402
from app.target_connectors.source_boundary import (  # noqa: E402
    SourceBoundaryUnsafeError,
    evaluate_manifest_lock_readiness,
    scan_local_repository_boundary,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    git_binary = shutil.which("git")
    assert git_binary is not None, "git must be on PATH for this test suite to run at all"
    return subprocess.run(  # noqa: S603
        [git_binary, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "--quiet", "--initial-branch=main")
    _run(path, "config", "user.email", "test@example.invalid")
    _run(path, "config", "user.name", "FTF-H007 test")


def _raises(exc_type: type[Exception], fn, *args: object) -> bool:
    try:
        fn(*args)
        return False
    except exc_type:
        return True


def test_clean_repository_is_safe_to_lock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")

        report = scan_local_repository_boundary(repo, [base])

        check("FTF-H007", "a clean repository reports tree_complete", report.tree_complete)
        check(
            "FTF-H007",
            "a clean repository has zero escaping symlinks/hooks/dangerous calls",
            not report.escaping_symlinks
            and not report.executable_hooks
            and not report.dangerous_calls,
        )
        check("FTF-H007", "a clean repository is safe to lock", report.is_safe_to_lock)
        try:
            evaluate_manifest_lock_readiness(report)
            passed = True
        except SourceBoundaryUnsafeError:
            passed = False
        check(
            "FTF-H007",
            "evaluate_manifest_lock_readiness does not raise for a safe report",
            passed,
        )


def _build_malicious_repository(repo: Path, base: Path, marker_dir: Path) -> None:
    _init_repo(repo)

    hook_marker = marker_dir / "hook_executed.marker"
    exec_marker = marker_dir / "os_system_executed.marker"

    (repo / "plugin.py").write_text(
        "import os\n"
        "\n"
        "def register() -> None:\n"
        "    pass\n"
        "\n"
        "if True:\n"
        f"    os.system('touch {exec_marker}')\n"
    )

    (repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
    (repo / "model.bin").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 4096\n"
    )

    (repo / ".gitmodules").write_text(
        '[submodule "vendor/lib"]\n'
        "\tpath = vendor/lib\n"
        "\turl = https://example.invalid/vendor/lib.git\n"
    )
    (repo / "vendor" / "lib").mkdir(parents=True)

    secret = base / "secret_outside_repo.txt"
    secret.write_text("TOP_SECRET_MARKER_CONTENT_zzz123\n")
    (repo / "link_to_secret").symlink_to(Path("..") / "secret_outside_repo.txt")

    _run(repo, "add", "plugin.py", ".gitattributes", "model.bin", ".gitmodules")
    _run(repo, "add", "-f", "link_to_secret")
    _run(repo, "commit", "--quiet", "-m", "malicious content")

    hooks_dir = repo / ".git" / "hooks"
    hook_path = hooks_dir / "pre-push"
    hook_path.write_text(f"#!/bin/sh\ntouch {hook_marker}\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_malicious_repository_reports_every_finding_without_execution_or_escape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker_dir = base / "markers"
        marker_dir.mkdir()
        _build_malicious_repository(repo, base, marker_dir)

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a dangerous os.system call is found in plugin.py",
            any(
                f.relative_path == "plugin.py" and "os.system" in f.reason
                for f in report.dangerous_calls
            ),
        )
        check(
            "FTF-H007",
            "the active .git/hooks/pre-push script is reported as an executable hook",
            any(h.endswith("pre-push") for h in report.executable_hooks),
        )
        check(
            "FTF-H007",
            "the escaping symlink is reported by its own repo-relative path",
            "link_to_secret" in report.escaping_symlinks,
        )
        check(
            "FTF-H007",
            "the unresolved Git LFS pointer path is reported",
            "model.bin" in report.inventory.unresolved_lfs_paths,
        )
        check(
            "FTF-H007",
            "the unresolved (empty, uninitialized) submodule path is reported",
            "vendor/lib" in report.inventory.unresolved_submodule_paths,
        )
        check(
            "FTF-H007",
            "a repository with any real unsafe/incomplete finding is not safe to lock",
            not report.is_safe_to_lock,
        )
        check(
            "FTF-H007",
            "evaluate_manifest_lock_readiness refuses (incomplete critical snapshot blocks lock)",
            _raises(SourceBoundaryUnsafeError, evaluate_manifest_lock_readiness, report),
        )

        check(
            "FTF-H007",
            "the os.system call inside plugin.py was never actually executed",
            not (marker_dir / "os_system_executed.marker").exists(),
        )
        check(
            "FTF-H007",
            "the .git/hooks/pre-push script was never actually executed",
            not (marker_dir / "hook_executed.marker").exists(),
        )

        report_text = repr(report)
        check(
            "FTF-H007",
            "the real secret content behind the escaping symlink is never read into the report",
            "TOP_SECRET_MARKER_CONTENT_zzz123" not in report_text,
        )
        check(
            "FTF-H007",
            "the escaping symlink entry itself carries no content (never dereferenced for bytes)",
            all(
                e.content is None
                for e in report.inventory.entries
                if e.relative_path == "link_to_secret"
            ),
        )


def test_escaping_symlink_alone_blocks_lock_in_isolation() -> None:
    """Isolates the escaping-symlink check from every other detector this
    module owns, so a regression in that one check specifically cannot
    hide behind one of the other four independently-blocking findings --
    the same isolation discipline `test_ftf_04a05.py`'s own integration
    suite already established for an analogous "one guard, confounded by a
    second" risk."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        secret = base / "isolated_secret.txt"
        secret.write_text("ISOLATED_SECRET\n")
        (repo / "escape").symlink_to(Path("..") / "isolated_secret.txt")
        _run(repo, "add", "README.md")
        _run(repo, "add", "-f", "escape")
        _run(repo, "commit", "--quiet", "-m", "only an escaping symlink")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "in isolation, only the escaping symlink is reported (no other detector fires)",
            report.escaping_symlinks == ("escape",)
            and not report.executable_hooks
            and not report.dangerous_calls
            and not report.inventory.unresolved_lfs_paths
            and not report.inventory.unresolved_submodule_paths,
        )
        check(
            "FTF-H007",
            "the escaping symlink alone is sufficient to block lock",
            not report.is_safe_to_lock,
        )


def test_dirty_working_tree_alone_does_not_block_lock() -> None:
    """Section 7A.3 requires a dirty tree be "visibly marked," not
    refused -- this task's own negative case for its own positive "blocks
    lock" claim: an otherwise-safe repository with only an uncommitted,
    ordinary file must still be safe to lock."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "committed.txt").write_text("committed\n")
        _run(repo, "add", "committed.txt")
        _run(repo, "commit", "--quiet", "-m", "initial")
        (repo / "untracked.txt").write_text("just an ordinary untracked file\n")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a dirty working tree is recorded on the inventory",
            report.inventory.is_dirty_working_tree,
        )
        check(
            "FTF-H007",
            "a dirty tree with no other unsafe finding is still safe to lock",
            report.is_safe_to_lock,
        )


def test_core_fsmonitor_config_never_executes_and_blocks_lock() -> None:
    """A real, documented git feature this task's own finding named
    directly: `core.fsmonitor` can be set in the repository's own
    `.git/config` to an arbitrary command git itself runs on every
    `status`-family call -- exactly what `capture_local_repository`'s own
    `_git_dirty_signature` makes during every scan. Before the fix, nothing
    neutralized this; `_run_git` now passes `-c core.fsmonitor=false` on
    every invocation (highest config precedence, overriding the repo's own
    value), and this scan's own `_scan_dangerous_git_config` separately
    reports the key's mere presence as a blocking finding."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker_dir = base / "markers"
        marker_dir.mkdir()
        marker = marker_dir / "fsmonitor_executed.marker"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")
        _run(repo, "config", "core.fsmonitor", f"sh -c 'touch {marker}'")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "the repo-controlled core.fsmonitor command was never actually executed",
            not marker.exists(),
        )
        check(
            "FTF-H007",
            "core.fsmonitor is reported as a dangerous git config key",
            "core.fsmonitor" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "a repository with a dangerous git config key is not safe to lock",
            not report.is_safe_to_lock,
        )
        check(
            "FTF-H007",
            "evaluate_manifest_lock_readiness refuses for a dangerous git config key",
            _raises(SourceBoundaryUnsafeError, evaluate_manifest_lock_readiness, report),
        )


def test_core_hookspath_config_blocks_lock() -> None:
    """`core.hooksPath` can redirect git's own hook execution to an
    attacker-chosen directory -- reported as a blocking finding the same
    way `core.fsmonitor` is, independent of `_run_git`'s own
    `-c core.hooksPath=/dev/null` override already neutralizing it for
    every call this process makes."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")
        evil_hooks_dir = base / "evil-hooks"
        evil_hooks_dir.mkdir()
        _run(repo, "config", "core.hooksPath", str(evil_hooks_dir))

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "core.hooksPath is reported as a dangerous git config key",
            "core.hookspath" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "a repository with core.hooksPath set is not safe to lock",
            not report.is_safe_to_lock,
        )


def test_filter_driver_config_never_executes_and_blocks_lock() -> None:
    """L2 round-2 finding (a) (2026-10-01T13:40Z): a repo-defined
    `filter.<name>.clean` driver declared via `.gitattributes` on a
    stat-dirty tracked file genuinely runs during a plain `git status`,
    before this fix -- `_run_git`'s own fsmonitor/hooksPath overrides
    cannot neutralize it generically, since the driver *name* is chosen by
    the untrusted repository itself. `capture_local_repository` now
    pre-scans for exactly this (`_UNNEUTRALIZABLE_FILTER_DRIVER_KEY_
    PATTERN`) and refuses before ever running `git status`; this scanner's
    own separate, pure `_scan_dangerous_git_config` call (never the
    execution path) reports the same key as a blocking finding here."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "filter_executed.marker"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        (repo / "a.secret").write_text("hello\n")
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        _run(repo, "config", "filter.evil.clean", f"sh -c 'touch {marker}'")
        (repo / "a.secret").write_text("hello\n")
        (repo / "a.secret").touch()

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "the repo-controlled filter.evil.clean command was never actually executed",
            not marker.exists(),
        )
        check(
            "FTF-H007",
            "filter.evil.clean is reported as a dangerous git config key",
            "filter.evil.clean" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "a repository with a filter driver config key is not safe to lock",
            not report.is_safe_to_lock,
        )
        check(
            "FTF-H007",
            "evaluate_manifest_lock_readiness refuses for a filter driver config key",
            _raises(SourceBoundaryUnsafeError, evaluate_manifest_lock_readiness, report),
        )


def test_include_path_config_is_detected_and_blocks_lock() -> None:
    """L2 round-2 finding (b): `_scan_dangerous_git_config` previously used
    `git config --local --get-regexp` with no `--includes`, which (confirmed
    directly against a real git binary) does not resolve `include.path` by
    default -- a dangerous key hidden behind an include evaded detection
    entirely while it still applied to every real git call. The fixed scan
    is unscoped, which resolves includes by default."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "fsmonitor_executed.marker"
        included = base / "included.gitconfig"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")
        included.write_text(f"[core]\n\tfsmonitor = sh -c 'touch {marker}'\n")
        _run(repo, "config", "include.path", str(included))

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a dangerous key reached only through include.path is detected",
            "core.fsmonitor" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "a repository with a dangerous key hidden behind include.path is not safe to lock",
            not report.is_safe_to_lock,
        )
        check(
            "FTF-H007",
            "the included fsmonitor command was never actually executed",
            not marker.exists(),
        )


def test_worktree_scope_config_is_detected_and_blocks_lock() -> None:
    """L2 round-2 finding (b): the prior scan never read `.git/config.
    worktree` (the `extensions.worktreeConfig` scope) at all -- a dangerous
    key set only there was invisible to detection while still applying to
    every real git call made against this worktree."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")
        _run(repo, "config", "extensions.worktreeConfig", "true")
        (repo / ".git" / "config.worktree").write_text("[core]\n\thooksPath = /dev/null-evil\n")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a dangerous key set only in .git/config.worktree is detected",
            "core.hookspath" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "a repository with a dangerous key set only in .git/config.worktree "
            "is not safe to lock",
            not report.is_safe_to_lock,
        )


def test_symlinked_hook_is_treated_as_active() -> None:
    """Before the fix, `_scan_git_hooks` skipped every `.git/hooks/*`
    symlink outright (`if entry.is_symlink() or not entry.is_file()`),
    so a hook that is a symlink to a real executable script was reported
    safe. A symlinked hook is just as real as a plain executable file --
    this is the specific case the earlier combined malicious-repository
    test (`test_malicious_repository_reports_every_finding_without_
    execution_or_escape`) never covered, since its own `pre-push` hook was
    a plain file, not a symlink."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")

        real_script = base / "real_hook_script.sh"
        real_script.write_text("#!/bin/sh\necho should-never-run\n")
        real_script.chmod(real_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        hook_path = repo / ".git" / "hooks" / "pre-commit"
        hook_path.symlink_to(real_script)

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a symlinked hook pointing at a real executable is reported as active",
            any(h.endswith("pre-commit") for h in report.executable_hooks),
        )
        check(
            "FTF-H007",
            "a repository with a symlinked active hook is not safe to lock",
            not report.is_safe_to_lock,
        )


def test_aliased_dangerous_import_is_detected() -> None:
    """Before the fix, `_scan_dangerous_python_calls` only matched a direct
    `os.system(...)`/`subprocess.run(...)`-shaped call -- `import os as o`
    then calling `o.system(...)`, or `from os import system` then calling
    the bare `system(...)`, evaded detection entirely despite being
    exactly as dangerous. Both aliasing forms this task's own finding
    named explicitly are covered in one file so a regression in either
    specific alias-resolution path is caught."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "plugin.py").write_text(
            "import os as o\n"
            "from os import system\n"
            "import subprocess as sp\n"
            "\n"
            "if True:\n"
            "    o.system('echo one')\n"
            "    system('echo two')\n"
            "    sp.run(['echo', 'three'])\n"
        )
        _run(repo, "add", "plugin.py")
        _run(repo, "commit", "--quiet", "-m", "aliased dangerous imports")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "'import os as o' then 'o.system(...)' is detected",
            any(f.relative_path == "plugin.py" and f.line == 6 for f in report.dangerous_calls),
        )
        check(
            "FTF-H007",
            "'from os import system' then bare 'system(...)' is detected",
            any(f.relative_path == "plugin.py" and f.line == 7 for f in report.dangerous_calls),
        )
        check(
            "FTF-H007",
            "'import subprocess as sp' then 'sp.run(...)' is detected",
            any(f.relative_path == "plugin.py" and f.line == 8 for f in report.dangerous_calls),
        )
        check(
            "FTF-H007",
            "a repository with only aliased dangerous imports is not safe to lock",
            not report.is_safe_to_lock,
        )


def test_plain_gitlink_submodule_declaration_blocks_lock() -> None:
    """L2 round-3 finding 1, route item (ii): Control-plane decision --
    submodules are out of scope for an untrusted-source capture entirely,
    so a bare gitlink (no `.gitmodules` at all, added via `git update-index
    --cacheinfo` directly) is reported as a blocking `submodule_
    declarations` finding regardless of whether it is populated or
    resolved -- distinct from, and a superset of, `snapshot_inventory`'s
    own `unresolved_submodule_paths`, which only fires for a `.gitmodules`-
    declared path that is empty/unreadable."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        sub = repo / "sub"
        _init_repo(repo)
        (repo / "top.txt").write_text("top\n")
        _run(repo, "add", "top.txt")
        _run(repo, "commit", "--quiet", "-m", "top-initial")
        _init_repo(sub)
        (sub / "a.txt").write_text("a\n")
        _run(sub, "add", "a.txt")
        _run(sub, "commit", "--quiet", "-m", "sub-initial")
        sub_head = _run(sub, "rev-parse", "HEAD").stdout.strip()
        _run(repo, "update-index", "--add", "--cacheinfo", f"160000,{sub_head},sub")
        _run(repo, "commit", "--quiet", "-m", "add plain gitlink")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a bare gitlink with no .gitmodules is reported as a submodule declaration",
            "sub" in report.submodule_declarations,
        )
        check(
            "FTF-H007",
            "a repository with any submodule declaration is not safe to lock, "
            "even a fully-populated/resolved one",
            not report.is_safe_to_lock,
        )
        check(
            "FTF-H007",
            "evaluate_manifest_lock_readiness refuses for a submodule declaration",
            _raises(SourceBoundaryUnsafeError, evaluate_manifest_lock_readiness, report),
        )


def test_registered_submodule_declaration_and_nested_hooks_block_lock() -> None:
    """L2 round-3 finding 1, route item (ii), the `git submodule add`
    variant (`.gitmodules` present) -- combined with minor item 5 ("report
    nested .git/modules/*/hooks"): a real, executable hook placed under the
    registered submodule's own `.git/modules/sub/hooks/` (a real path a
    plain `.git/hooks` scan never reaches) must be reported with its own
    real, locatable relative path."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        sub_source = base / "sub-source"
        _init_repo(repo)
        (repo / "top.txt").write_text("top\n")
        _run(repo, "add", "top.txt")
        _run(repo, "commit", "--quiet", "-m", "top-initial")
        _init_repo(sub_source)
        (sub_source / "a.txt").write_text("a\n")
        _run(sub_source, "add", "a.txt")
        _run(sub_source, "commit", "--quiet", "-m", "sub-initial")
        _run(
            repo,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "--quiet",
            str(sub_source),
            "sub",
        )
        _run(repo, "commit", "--quiet", "-m", "add registered submodule")

        nested_hooks_dir = repo / ".git" / "modules" / "sub" / "hooks"
        nested_hook = nested_hooks_dir / "pre-commit"
        nested_hook.write_text("#!/bin/sh\necho should-never-run\n")
        nested_hook.chmod(nested_hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        report = scan_local_repository_boundary(repo, [base])

        is_declared = (
            "sub" in report.submodule_declarations or ".gitmodules" in report.submodule_declarations
        )
        check(
            "FTF-H007",
            "a registered submodule (.gitmodules present) is reported as a submodule declaration",
            is_declared,
        )
        check(
            "FTF-H007",
            "an executable hook nested under .git/modules/<name>/hooks/ is "
            "reported, with its own real relative path",
            any(h.endswith("modules/sub/hooks/pre-commit") for h in report.executable_hooks),
        )
        check(
            "FTF-H007",
            "a repository with a nested submodule hook is not safe to lock",
            not report.is_safe_to_lock,
        )


def test_filter_smudge_driver_is_detected_and_blocks_lock() -> None:
    """L2 round-3 finding 4 ("process and smudge driver tests, so that
    narrowing the pattern turns RED"): confirmed directly against a real
    git binary that a bare `filter.<name>.smudge` driver (no clean/process)
    does not itself fire during a plain `git status` -- smudge is the
    checkout direction, status only ever needs the clean direction -- so
    this is a pure detection-coverage test: if `_DANGEROUS_GIT_CONFIG_KEY_
    PATTERN` were ever narrowed back to `clean` only, this key would stop
    being reported and this test would go red, even though no execution
    claim is being made for it."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")
        _run(repo, "config", "filter.evil.smudge", "sh -c 'echo should-never-run'")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a bare filter.evil.smudge key is reported as a dangerous git config key",
            "filter.evil.smudge" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "a repository with only a smudge filter driver is not safe to lock",
            not report.is_safe_to_lock,
        )


def test_filter_required_alone_is_reported_never_raises() -> None:
    """L2 round-3 minor item: `filter.<name>.required` alone (no clean/
    smudge/process) is never itself an execution vector -- it never runs a
    repo-controlled command. The fix adds `required` to `_DANGEROUS_GIT_
    CONFIG_KEY_PATTERN` so it is reported like any other filter key; this
    test (a real content *modification*, not a same-size stat-dirty touch
    -- see `test_filter_required_alone_with_same_size_stat_dirty_file_is_
    reported_never_raises` below for round-4's own corrected, load-bearing
    case) proves the scan completes normally (no `GitCommandError`, no
    crash) and still reports the finding."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        (repo / "a.secret").write_text("hello\n")
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        _run(repo, "config", "filter.evil.required", "true")
        (repo / "a.secret").write_text("modified now\n")

        try:
            report = scan_local_repository_boundary(repo, [base])
            raised = False
        except Exception:  # noqa: BLE001 -- this test's own point is "never raises"
            raised = True

        check(
            "FTF-H007",
            "a bare filter.evil.required key never makes the boundary scan raise",
            not raised,
        )
        check(
            "FTF-H007",
            "filter.evil.required is reported as a dangerous git config key",
            raised or "filter.evil.required" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "a repository with only filter.evil.required set is not safe to lock",
            raised or not report.is_safe_to_lock,
        )


def test_filter_required_alone_with_same_size_stat_dirty_file_is_reported_never_raises() -> None:
    """L2 round-4 finding 5: the round-3 test above modifies the tracked
    file's real *content*, which this round's direct confirmation against a
    real git binary shows does NOT reproduce the actual defect -- a bare
    `filter.<name>.required` key only makes `git status` genuinely FAIL
    ("clean filter '<name>' failed", non-zero exit) on a tracked file that
    is *stat-dirty* (its on-disk mtime no longer matches git's own cached
    stat info) while its content stays byte-identical, after an earlier
    `git status` call has already populated that stat cache. Before
    round-4's fix, that real `GitCommandError` propagated straight out of
    `scan_local_repository_boundary` uncaught, instead of being reported
    like every other dangerous key. This test reproduces the exact
    sequence (an initial status to populate the cache, then config the
    required key, then rewrite identical content and `touch` it) and
    proves the scan still completes normally and still reports the real
    finding, exactly like the content-modified case above."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        content = "hello\n"
        (repo / "a.secret").write_text(content)
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        # Populate git's own stat cache with a real, successful status call
        # *before* `required` is set -- without this, the fault this test
        # targets does not reproduce on a fresh checkout.
        _run(repo, "status", "--porcelain=v1", "--untracked-files=all")
        _run(repo, "config", "filter.evil.required", "true")
        # Same-size, byte-identical rewrite, then touch: the file's content
        # never changes, only its mtime -- this is what makes git's own
        # stat-cache short-circuit fail and force a real clean-filter check.
        (repo / "a.secret").write_text(content)
        (repo / "a.secret").touch()

        try:
            report = scan_local_repository_boundary(repo, [base])
            raised = False
        except Exception:  # noqa: BLE001 -- this test's own point is "never raises"
            raised = True

        check(
            "FTF-H007",
            "a same-size stat-dirty file with only filter.evil.required set "
            "never makes the boundary scan raise",
            not raised,
        )
        check(
            "FTF-H007",
            "filter.evil.required is reported as a dangerous git config key "
            "for the same-size stat-dirty case too",
            raised or "filter.evil.required" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "a same-size stat-dirty repository with only filter.evil.required "
            "set is not safe to lock",
            raised or not report.is_safe_to_lock,
        )


def test_fault_injection_capture_local_repository_raises_for_required_filter_precondition() -> None:
    """Round-5 rename (Control-plane finding: the prior name claimed this
    test exercised `scan_local_repository_boundary`'s own `except
    GitCommandError` branch, but it only ever calls `capture_local_repository`
    directly and never removes or bypasses that branch -- it flips nothing
    about the branch itself. What it genuinely proves is the branch's own
    *precondition*: that the exact same-size stat-dirty `filter.<name>.
    required` scenario above really does make `capture_local_repository`
    raise `GitCommandError`, so the sibling test above is exercising a real
    exception path, not one that can never actually occur."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        content = "hello\n"
        (repo / "a.secret").write_text(content)
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        _run(repo, "status", "--porcelain=v1", "--untracked-files=all")
        _run(repo, "config", "filter.evil.required", "true")
        (repo / "a.secret").write_text(content)
        (repo / "a.secret").touch()

        from app.target_connectors.local_repository_connector import (
            GitCommandError as _GitCommandError,
        )

        check(
            "FTF-H007",
            "without the round-4 except GitCommandError branch, "
            "capture_local_repository itself genuinely raises for this "
            "exact same-size stat-dirty filter.required scenario -- "
            "confirming the branch this test's sibling relies on is real, "
            "not a no-op",
            _raises(
                _GitCommandError,
                _connector_module.capture_local_repository,
                repo,
                [base],
            ),
        )


def test_scan_dangerous_git_config_fails_closed_on_real_git_failure() -> None:
    """L2 round-3 minor item: the prior `_scan_dangerous_git_config`
    returned `()` ("safe") on ANY non-zero `git config --get-regexp` exit,
    including a real failure (a malformed config), which would make
    `is_safe_to_lock` incorrectly `True`. Exit `1` ("no key matched") is
    the only genuinely benign non-zero exit and still returns `()`; this
    test proves a real failure now fails closed (`GitCommandError`), using
    a real, syntactically-broken `.git/config` (an unterminated section
    header) that makes git's own `config --get-regexp` genuinely exit
    non-1."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        config_path = repo / ".git" / "config"
        config_path.write_text(config_path.read_text() + "\n[broken\n")

        check(
            "FTF-H007",
            "a real git config failure (not 'no match') makes the scan fail "
            "closed with GitCommandError, never silently return an empty, "
            "'safe' result",
            _raises(
                _connector_module.GitCommandError,
                _connector_module._scan_dangerous_git_config,
                repo,
            ),
        )


def test_dangerous_git_config_key_with_spaced_subsection_is_not_truncated() -> None:
    """L2 round-3 minor item: the prior line-based parsing
    (`line.split(" ", 1)[0]`) truncated a key whose quoted subsection name
    itself contains a space (`[filter "a.b c"]` -> key `filter.a.b c.clean`)
    at the first space, reporting only `filter.a.b` -- confirmed directly
    against a real git binary. The `--null` (NUL-record, newline-separated
    key/value) fix reports the real, complete key."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")
        _run(repo, "config", "--local", "filter.a.b c.clean", "sh -c 'echo should-never-run'")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a key whose subsection name contains a space is reported in full, "
            "never truncated at the embedded space",
            "filter.a.b c.clean" in report.dangerous_git_config_keys,
        )
        check(
            "FTF-H007",
            "the truncated (wrong) key is never reported",
            "filter.a.b" not in report.dangerous_git_config_keys,
        )


def _delete_loose_object(repo: Path, sha: str) -> None:
    """Deletes a real loose git object by its sha -- used below to simulate
    a repository git genuinely cannot read (a missing tree/commit object),
    never a mocked git failure."""
    object_path = repo / ".git" / "objects" / sha[:2] / sha[2:]
    assert object_path.is_file(), f"expected a real loose object at {object_path}"
    object_path.unlink()


def test_round5_corrupt_index_boundary_scan_fails_closed() -> None:
    """Round-5 DoD repro R1 (2026-10-05T00:55Z): `printf 'DIRCgarbage' >
    .git/index` makes both `git status` and `git ls-files -s` genuinely
    exit 128 (confirmed directly against a real git binary). Before this
    fix, `scan_local_repository_boundary`'s own `except GitCommandError`
    swallowed the `git status` failure from `capture_local_repository` and
    simply treated it as "dirty, nothing further to report" -- with no
    other finding present, `is_safe_to_lock` was incorrectly `True` for a
    repository this scan could not actually read at all."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "a.txt").write_text("hello\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "initial")
        (repo / ".git" / "index").write_text("DIRCgarbage")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "R1: a corrupt index makes the boundary scan report a git_read_error",
            bool(report.git_read_errors),
        )
        check(
            "FTF-H007",
            "R1: a repository with a corrupt index is never safe to lock",
            not report.is_safe_to_lock,
        )
        check(
            "FTF-H007",
            "R1: evaluate_manifest_lock_readiness raises for a corrupt index",
            _raises(SourceBoundaryUnsafeError, evaluate_manifest_lock_readiness, report),
        )


def test_round5_missing_head_tree_object_boundary_scan_fails_closed() -> None:
    """Round-5 DoD repro R2: deleting the real `HEAD^{tree}` loose object
    makes `git status` genuinely exit 128 ("fatal: bad tree object HEAD",
    confirmed directly against a real git binary) and `git ls-tree -r -z
    HEAD` genuinely exit 128 too ("fatal: not a tree object") -- both
    reached from `scan_local_repository_boundary`, neither silently
    treated as "no entries" or "merely dirty" after this fix."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "a.txt").write_text("hello\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "initial")
        tree_sha = _run(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
        _delete_loose_object(repo, tree_sha)

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "R2: a missing HEAD tree object makes the boundary scan report a git_read_error",
            bool(report.git_read_errors),
        )
        check(
            "FTF-H007",
            "R2: a repository with a missing HEAD tree object is never safe to lock",
            not report.is_safe_to_lock,
        )


def test_round5_missing_head_commit_object_boundary_scan_fails_closed() -> None:
    """Round-5 DoD repro R6: deleting the real `HEAD` commit loose object
    makes `git status` genuinely exit 128 ("fatal: bad object HEAD",
    confirmed directly against a real git binary) while `git rev-parse
    --verify --quiet HEAD` still happily prints the (now-dangling) sha --
    the failure only surfaces once something actually tries to read the
    commit/tree content, exactly the kind of failure the round-4 catch-all
    swallowed."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "a.txt").write_text("hello\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "initial")
        head_sha = _run(repo, "rev-parse", "HEAD").stdout.strip()
        _delete_loose_object(repo, head_sha)

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "R6: a missing HEAD commit object makes the boundary scan report a git_read_error",
            bool(report.git_read_errors),
        )
        check(
            "FTF-H007",
            "R6: a repository with a missing HEAD commit object is never safe to lock",
            not report.is_safe_to_lock,
        )


def test_round5_corrupt_index_with_staged_gitlink_is_not_safe() -> None:
    """Round-5 DoD finding 2: `_scan_gitlink_paths` previously treated any
    non-zero exit (including a real read failure, not only a genuinely
    empty result) as "no entries" -- so a corrupt index that makes `git
    ls-files -s` itself fail (confirmed above, R1) silently hid a real
    staged gitlink from `scan_submodule_declarations` instead of reporting
    it, or at least failing closed. This test has a real staged submodule
    gitlink on record (via `.gitmodules` + a committed gitlink entry)
    before the index is corrupted, so the only way this can be safe is if
    the read failure is itself swallowed."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        sub = base / "sub"
        _init_repo(sub)
        (sub / "f.txt").write_text("sub\n")
        _run(sub, "add", "f.txt")
        _run(sub, "commit", "--quiet", "-m", "sub initial")

        _init_repo(repo)
        (repo / "a.txt").write_text("hello\n")
        _run(repo, "add", "a.txt")
        _run(
            repo,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "--quiet",
            str(sub),
            "vendor/sub",
        )
        _run(repo, "commit", "--quiet", "-m", "add submodule")
        (repo / ".git" / "index").write_text("DIRCgarbage")

        report = scan_local_repository_boundary(repo, [base])

        check(
            "FTF-H007",
            "a corrupt index with a real staged gitlink on record reports "
            "a git_read_error rather than silently finding no submodules",
            bool(report.git_read_errors),
        )
        check(
            "FTF-H007",
            "a corrupt index with a real staged gitlink on record is never safe to lock",
            not report.is_safe_to_lock,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-H007 source boundary tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H007 source boundary tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
