#!/usr/bin/env python3
"""FTF-04A06 -- Local repository connector with root allowlist/path
protections (no-DB unit/negative-acceptance tests).

Runnable without Postgres, network egress, or any object-storage fixture --
this task's own scope is deliberately a pure, DB-free capture connector
(see `apps/api/app/target_connectors/local_repository_connector.py`'s own
docstring for the disclosed "no live caller/no object-storage upload yet"
scope boundary). Every check here uses a *real* local Git repository built
with the real `git` binary in a real temporary directory -- never a mocked
git command or a canned capture result. This suite proves:

1. `resolve_allowed_root` accepts a root inside an allowlisted directory and
   refuses one outside it, including a symlink crafted to point outside the
   allowlist -- this task's own real "reject paths outside allowlisted
   roots"/"do not follow symlinks outside allowlisted root" negative
   acceptance, checked before any repository content is ever read.
2. `capture_local_repository` on a real, clean Git checkout returns
   `capture_method='local_git_commit'` with the real `HEAD` commit sha and
   the real `HEAD^{tree}` git tree hash, correctly reports `None` for a
   detached-HEAD checkout, and never emits an archive.
3. `capture_local_repository` on a real dirty working tree (an untracked
   file, matching Section 7A.3's own "report dirty-tree state") returns
   `capture_method='local_dirty_snapshot'` with a real, deterministic
   `sha256`-hashed manifest/archive derived from the actual on-disk bytes --
   re-running the capture on byte-identical content produces the identical
   `tree_hash`/`source_archive_sha256`.
4. A symlink inside the repository pointing outside the allowlisted root is
   refused (`PathAllowlistError`), and the working tree is provably
   untouched by the attempt (`git status` before/after the raise is
   identical) -- this task's own real "never edit the target working tree"
   half of its negative_acceptance.
5. A real concurrent modification during capture (a file changed between
   the connector's own before/after git-identity check) is refused
   (`ConcurrentModificationError`) -- this task's own real "concurrent file
   drift" negative_acceptance, exercised by monkeypatching the module's own
   dirty-signature probe to simulate a real mid-capture change rather than
   asserting on an untestable race window.

Run directly: python3 tests/tasks/test_ftf_04a06.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors import local_repository_connector as _connector_module  # noqa: E402
from app.target_connectors.local_repository_connector import (  # noqa: E402
    ConcurrentModificationError,
    DangerousGitConfigError,
    EmptyRepositoryError,
    LocalRepositoryCapture,
    NotAGitRepositoryError,
    PathAllowlistError,
    PopulatedSubmoduleError,
    capture_local_repository,
    resolve_allowed_root,
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
    _run(path, "config", "user.name", "FTF-04A06 test")


def test_resolve_allowed_root_accepts_inside_and_refuses_outside() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        allowed = base / "allowed"
        outside = base / "outside"
        (allowed / "repo").mkdir(parents=True)
        outside.mkdir(parents=True)

        real_root = resolve_allowed_root(allowed / "repo", [allowed])
        check(
            "FTF-04A06",
            "a real root strictly inside an allowlisted directory is accepted",
            real_root == (allowed / "repo").resolve(),
        )

        check(
            "FTF-04A06",
            "a root outside every allowlisted directory is refused",
            _raises(PathAllowlistError, resolve_allowed_root, outside, [allowed]),
        )

        check(
            "FTF-04A06",
            "an empty allowlist refuses every root, fail-closed",
            _raises(PathAllowlistError, resolve_allowed_root, allowed / "repo", []),
        )


def test_resolve_allowed_root_refuses_a_prefix_colliding_sibling() -> None:
    """This task's own `resolve_allowed_root` docstring claims "never a
    string-prefix comparison" -- a root under a differently-named sibling
    directory that merely shares a text prefix with an allowlisted one
    (`allowed-evil` vs `allowed`) must be refused exactly like any other
    outside root, distinct from the symlink-escape case covered above.
    This is this task's own real, literal "path traversal" negative
    acceptance, as distinct from "symlink traversal"."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        allowed = base / "allowed"
        prefix_colliding_sibling = base / "allowed-evil"
        allowed.mkdir()
        (prefix_colliding_sibling / "repo").mkdir(parents=True)

        check(
            "FTF-04A06",
            "a root under a prefix-colliding sibling directory is refused, "
            "never accepted by a naive string-prefix comparison",
            _raises(
                PathAllowlistError,
                resolve_allowed_root,
                prefix_colliding_sibling / "repo",
                [allowed],
            ),
        )


def test_resolve_allowed_root_refuses_a_symlinked_escape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        allowed = base / "allowed"
        outside_target = base / "outside_target"
        allowed.mkdir()
        outside_target.mkdir()
        escape_link = allowed / "escape"
        escape_link.symlink_to(outside_target, target_is_directory=True)

        check(
            "FTF-04A06",
            "an allowlisted-looking root that is itself a symlink to outside is refused",
            _raises(PathAllowlistError, resolve_allowed_root, escape_link, [allowed]),
        )


def _raises(exc_type: type[Exception], fn, *args: object) -> bool:
    try:
        fn(*args)
        return False
    except exc_type:
        return True


def test_capture_clean_git_checkout_reports_real_commit_and_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _run(repo, "add", "README.md")
        _run(repo, "commit", "--quiet", "-m", "initial")

        capture = capture_local_repository(repo, [base])

        check(
            "FTF-04A06",
            "a clean checkout is captured as local_git_commit",
            capture.capture_method == "local_git_commit",
        )
        real_head = _run(repo, "rev-parse", "HEAD").stdout.strip()
        real_tree = _run(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
        check(
            "FTF-04A06",
            "commit_sha is the real HEAD commit sha",
            capture.commit_sha == real_head,
        )
        check(
            "FTF-04A06",
            "tree_hash is the real git HEAD^{tree} object id for a clean checkout",
            capture.tree_hash == real_tree,
        )
        check(
            "FTF-04A06",
            "branch is the real current branch name",
            capture.branch == "main",
        )
        check(
            "FTF-04A06",
            "a clean capture never emits an archive",
            capture.source_archive_bytes is None and capture.source_archive_sha256 is None,
        )


def test_capture_detached_head_reports_none_branch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "a.txt").write_text("a\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "first")
        head_sha = _run(repo, "rev-parse", "HEAD").stdout.strip()
        _run(repo, "checkout", "--quiet", head_sha)

        capture = capture_local_repository(repo, [base])
        check(
            "FTF-04A06",
            "a real detached HEAD checkout is captured with branch=None",
            capture.branch is None and capture.capture_method == "local_git_commit",
        )


def test_capture_dirty_tree_is_deterministic_and_content_hashed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "committed.txt").write_text("committed\n")
        _run(repo, "add", "committed.txt")
        _run(repo, "commit", "--quiet", "-m", "initial")
        (repo / "untracked.txt").write_text("dirty content\n")

        capture_one = capture_local_repository(repo, [base])
        check(
            "FTF-04A06",
            "an untracked file makes the tree dirty and reports local_dirty_snapshot",
            capture_one.capture_method == "local_dirty_snapshot",
        )
        check(
            "FTF-04A06",
            "a dirty capture always carries a real archive and its own sha256",
            capture_one.source_archive_bytes is not None
            and capture_one.source_archive_sha256
            == __import__("hashlib").sha256(capture_one.source_archive_bytes).hexdigest(),
        )
        check(
            "FTF-04A06",
            "the manifest includes both the committed and the untracked file",
            {e.relative_path for e in capture_one.manifest_entries}
            == {"committed.txt", "untracked.txt"},
        )

        capture_two = capture_local_repository(repo, [base])
        check(
            "FTF-04A06",
            "re-capturing byte-identical content yields the identical tree_hash",
            capture_one.tree_hash == capture_two.tree_hash,
        )
        check(
            "FTF-04A06",
            "re-capturing byte-identical content yields the identical archive sha256",
            capture_one.source_archive_sha256 == capture_two.source_archive_sha256,
        )

        (repo / "untracked.txt").write_text("changed content\n")
        capture_three = capture_local_repository(repo, [base])
        check(
            "FTF-04A06",
            "changing file content changes the tree_hash",
            capture_three.tree_hash != capture_one.tree_hash,
        )


def test_capture_never_reads_the_git_internal_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "a.txt").write_text("a\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "first")
        (repo / "b.txt").write_text("dirty\n")

        capture = capture_local_repository(repo, [base])
        check(
            "FTF-04A06",
            "no manifest entry ever comes from inside .git",
            all(not e.relative_path.startswith(".git/") for e in capture.manifest_entries),
        )


def test_symlink_escape_inside_repository_is_refused_and_never_edits_the_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        outside = base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside secret\n")
        _init_repo(repo)
        (repo / "a.txt").write_text("a\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "first")
        (repo / "escape.txt").symlink_to(outside / "secret.txt")

        status_before = _run(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout

        check(
            "FTF-04A06",
            "a symlink inside the repository pointing outside its own root is refused",
            _raises(PathAllowlistError, capture_local_repository, repo, [base]),
        )

        status_after = _run(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
        check(
            "FTF-04A06",
            "a refused capture never edits the target working tree",
            status_before == status_after,
        )


def test_malicious_core_fsmonitor_never_executes_and_never_edits_the_tree() -> None:
    """L2 finding (2026-10-01, shared root cause with FTF-H007):
    `capture_local_repository`'s own `_git_dirty_signature` runs `git
    status` on the untrusted tree during every capture. `core.fsmonitor`
    is a real, documented git feature letting the repository's own
    `.git/config` name an arbitrary command git runs on every
    `status`-family call -- before the fix, nothing neutralized this, so
    a malicious `core.fsmonitor` command genuinely executed and could
    edit the target working tree, breaking this task's own
    negative_acceptance ("never edit the target working tree") even
    though `capture_local_repository` itself never writes anything.
    `_run_git` now passes `-c core.fsmonitor=false -c
    core.hooksPath=/dev/null` on every invocation (highest config
    precedence, overriding the repository's own value regardless of
    content)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker_dir = base / "markers"
        marker_dir.mkdir()
        marker = marker_dir / "fsmonitor_executed.marker"
        _init_repo(repo)
        (repo / "a.txt").write_text("a\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "first")
        _run(repo, "config", "core.fsmonitor", f"sh -c 'touch {marker}'")

        # The test's own before/after status snapshots must themselves be
        # hardened the same way production `_run_git` is -- an unhardened
        # `git status` probe here would trigger the malicious hook itself
        # (as a side effect of the test's own instrumentation, not of the
        # code under test) and falsely blame `capture_local_repository`.
        status_before = _run(
            repo, "-c", "core.fsmonitor=false", "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        capture = capture_local_repository(repo, [base])
        status_after = _run(
            repo, "-c", "core.fsmonitor=false", "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout

        check(
            "FTF-04A06",
            "a malicious core.fsmonitor command is never actually executed during capture",
            not marker.exists(),
        )
        check(
            "FTF-04A06",
            "a capture with a malicious core.fsmonitor config still never edits the working tree",
            status_before == status_after,
        )
        check(
            "FTF-04A06",
            "the capture itself still succeeds normally once the malicious config is neutralized",
            capture.capture_method == "local_git_commit",
        )


def test_malicious_filter_driver_never_executes_and_capture_is_refused() -> None:
    """L2 round-2 finding (a) (2026-10-01T13:40Z): `_run_git`'s own `-c
    core.fsmonitor=false -c core.hooksPath=/dev/null` neutralizes those two
    named vectors, but not a repo-defined `filter.<name>.clean`/`process`
    driver declared via `.gitattributes` on a stat-dirty tracked file --
    git genuinely runs that driver while comparing the working tree against
    the index during a plain `git status`, before the fix. Reproduced for
    real against the pre-fix code path (see
    `test_fault_injection_filter_driver_check_is_load_bearing` below for the
    same exploit proven to succeed once the new pre-scan is bypassed).
    `capture_local_repository` now runs `_scan_dangerous_git_config` before
    its own first working-tree-content git call and refuses outright."""
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
        # Make the tracked file stat-dirty so git must re-compare its
        # content (through the clean filter) against the index during the
        # very next `status`-family call.
        (repo / "a.secret").write_text("hello\n")
        (repo / "a.secret").touch()

        check(
            "FTF-H007",
            "a repo-defined filter driver is detected and capture is refused "
            "before any working-tree git command could trigger it",
            _raises(DangerousGitConfigError, capture_local_repository, repo, [base]),
        )
        check(
            "FTF-H007",
            "the repo-controlled filter.evil.clean command was never actually executed",
            not marker.exists(),
        )


def test_fault_injection_filter_driver_check_is_load_bearing() -> None:
    """Law 1 -- shown able to fail: with the new pre-scan stubbed out
    (simulating its absence, the genuine pre-fix behavior), the identical
    malicious filter driver from the test above is proven to actually
    execute during `capture_local_repository`'s own `git status` call,
    confirming the pre-scan -- not some unrelated side effect -- is what
    prevents it in the real, unpatched call path."""
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

        original = _connector_module._scan_dangerous_git_config
        _connector_module._scan_dangerous_git_config = lambda root, pattern="": ()
        try:
            capture_local_repository(repo, [base])
        except DangerousGitConfigError:
            pass
        finally:
            _connector_module._scan_dangerous_git_config = original

        check(
            "FTF-H007",
            "with the pre-scan stubbed out, the malicious filter driver genuinely "
            "executes during git status -- proving the pre-scan is load-bearing",
            marker.exists(),
        )


def test_fault_injection_hookspath_override_is_load_bearing() -> None:
    """L2 round-3 finding 2 (round-2 finding (c), re-proven through the real
    code path): the round-2 version of this test replaced `_run_git`
    entirely with a hand-written stand-in, which the round-3 review
    correctly rejected as proving nothing about the real call ("swaps
    _run_git for a stub and never exercise the real constants"). This
    version instead monkeypatches the real module-level `_SAFETY_CONFIG_
    OVERRIDES` tuple -- removing only the `core.hooksPath` entry, keeping
    `core.fsmonitor`/`submodule.recurse` -- and calls the real, unmodified
    `_run_git`/`capture_local_repository`. A real, executable `post-index-
    change` hook placed under a repo-controlled `core.hooksPath` genuinely
    fires during `git status` (git refreshes the index's stat cache during
    `status` and runs this real, documented hook afterward)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        evil_hooks_dir = base / "evil-hooks"
        evil_hooks_dir.mkdir()
        marker = base / "post_index_change.marker"
        _init_repo(repo)
        (repo / "a.txt").write_text("a\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "initial")
        hook_path = evil_hooks_dir / "post-index-change"
        hook_path.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook_path.chmod(0o755)
        _run(repo, "config", "core.hooksPath", str(evil_hooks_dir))
        (repo / "a.txt").touch()  # force a stat-dirty index refresh during status

        original_overrides = _connector_module._SAFETY_CONFIG_OVERRIDES
        _connector_module._SAFETY_CONFIG_OVERRIDES = tuple(
            kv for kv in original_overrides if kv[0] != "core.hooksPath"
        )
        try:
            capture_local_repository(repo, [base])
        except _connector_module.LocalRepositoryConnectorError:
            pass
        finally:
            _connector_module._SAFETY_CONFIG_OVERRIDES = original_overrides

        check(
            "FTF-H007",
            "with only the hooksPath override removed from the real "
            "_SAFETY_CONFIG_OVERRIDES global, through the real, unmodified "
            "_run_git, the repo-controlled post-index-change hook genuinely "
            "fires during the real capture call path",
            marker.exists(),
        )


def _with_empty_git_config_system(fn: Callable[[], None]) -> None:
    """Runs `fn()` with the ambient `GIT_CONFIG_SYSTEM` environment variable
    this *test process* inherits pointed at a genuinely empty temp file --
    standing in for "a neutral host with no system-level git config at all"
    -- restoring the real ambient value afterward. L2 round-4's own explicit
    requirement for the new baseline tests below: each must pass both as
    this sandbox's real ambient host currently is (which, confirmed
    directly, has a real `/etc/gitconfig` carrying `filter.lfs.(clean|
    smudge|process|required)`) and independently on a neutral host, never
    relying on whatever a given CI runner's own system-level git config
    happens to contain."""
    with tempfile.TemporaryDirectory() as tmp:
        empty_system_config = Path(tmp) / "empty-system.gitconfig"
        empty_system_config.write_text("")
        original = os.environ.get("GIT_CONFIG_SYSTEM")
        os.environ["GIT_CONFIG_SYSTEM"] = str(empty_system_config)
        try:
            fn()
        finally:
            if original is None:
                os.environ.pop("GIT_CONFIG_SYSTEM", None)
            else:
                os.environ["GIT_CONFIG_SYSTEM"] = original


def _assert_hookspath_attack_is_defeated_by_real_defaults() -> None:
    """L2 round-4 finding F4: `test_fault_injection_hookspath_override_is_
    load_bearing` above only proves the protection is load-bearing once it
    is deliberately *removed* -- nothing ran this exact attack against the
    real, unmodified default `_SAFETY_CONFIG_OVERRIDES`/`_run_git` to prove
    the shipped defaults themselves still include it (and would turn RED,
    not silently stay GREEN, the moment a future edit genuinely dropped
    `core.hooksPath` from that tuple). This is that missing baseline: no
    module attribute is monkeypatched anywhere in this function."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        evil_hooks_dir = base / "evil-hooks"
        evil_hooks_dir.mkdir()
        marker = base / "post_index_change_default.marker"
        _init_repo(repo)
        (repo / "a.txt").write_text("a\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "initial")
        hook_path = evil_hooks_dir / "post-index-change"
        hook_path.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook_path.chmod(0o755)
        _run(repo, "config", "core.hooksPath", str(evil_hooks_dir))
        (repo / "a.txt").touch()  # force a stat-dirty index refresh during status

        capture = capture_local_repository(repo, [base])

        check(
            "FTF-H007",
            "with the real, unmodified default _SAFETY_CONFIG_OVERRIDES (no "
            "toggle flipped anywhere in this test), a repo-controlled "
            "core.hooksPath hook never actually fires",
            not marker.exists(),
        )
        check(
            "FTF-H007",
            "the capture itself still succeeds normally once the malicious "
            "hooksPath config is neutralized by the real defaults",
            capture.capture_method == "local_git_commit",
        )


def test_default_constants_defeat_hookspath_attack_on_host_as_is() -> None:
    """Round-4 F4 baseline, run on this sandbox's own real ambient host
    environment, completely unmodified."""
    _assert_hookspath_attack_is_defeated_by_real_defaults()


def test_default_constants_defeat_hookspath_attack_on_neutral_host() -> None:
    """Round-4 F4 baseline, independently re-run with `GIT_CONFIG_SYSTEM`
    forced to a genuinely empty file -- a neutral host -- proving the
    result does not depend on this sandbox's own real host-level git
    config (confirmed directly to carry `filter.lfs`) either way."""
    _with_empty_git_config_system(_assert_hookspath_attack_is_defeated_by_real_defaults)


def test_fault_injection_global_env_isolation_is_load_bearing() -> None:
    """L2 round-3 finding 2: a real, external `GIT_CONFIG_GLOBAL` pointing
    at a temp file this test controls (never the host's own real global
    config, and never a stubbed `_run_git`) defines a malicious
    `filter.evil.clean`. With `_run_git`'s own `GIT_CONFIG_GLOBAL=/dev/null`
    override in place (the normal, unmodified default), this external value
    is neutralized and the capture succeeds safely. With only the module-
    level `_GIT_CONFIG_GLOBAL_ENV_VALUE` toggle flipped to `None` (so
    `_run_git` leaves the ambient `GIT_CONFIG_GLOBAL` environment variable
    alone instead of overriding it) -- the real pre-scan, reading through
    the same real `_run_git`, would otherwise also see this key and refuse
    before the vulnerable call is reached, so it is stubbed out here too,
    isolating this one layer exactly as the round-2 version of this test
    already did -- the external value leaks through and the filter
    genuinely executes."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "filter_executed.marker"
        fake_global_config = base / "fake-global.gitconfig"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        (repo / "a.secret").write_text("hello\n")
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        fake_global_config.write_text(f"[filter \"evil\"]\n\tclean = sh -c 'touch {marker}'\n")
        (repo / "a.secret").write_text("hello\n")
        (repo / "a.secret").touch()

        original_global_value = os.environ.get("GIT_CONFIG_GLOBAL")
        original_global_toggle = _connector_module._GIT_CONFIG_GLOBAL_ENV_VALUE
        original_scan = _connector_module._scan_dangerous_git_config
        os.environ["GIT_CONFIG_GLOBAL"] = str(fake_global_config)
        _connector_module._GIT_CONFIG_GLOBAL_ENV_VALUE = None
        _connector_module._scan_dangerous_git_config = lambda root, pattern="": ()
        try:
            capture_local_repository(repo, [base])
        except _connector_module.LocalRepositoryConnectorError:
            pass
        finally:
            if original_global_value is None:
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
            else:
                os.environ["GIT_CONFIG_GLOBAL"] = original_global_value
            _connector_module._GIT_CONFIG_GLOBAL_ENV_VALUE = original_global_toggle
            _connector_module._scan_dangerous_git_config = original_scan

        check(
            "FTF-H007",
            "with only the GIT_CONFIG_GLOBAL override toggle removed, through "
            "the real _run_git, an externally-set GIT_CONFIG_GLOBAL genuinely "
            "leaks through and executes",
            marker.exists(),
        )


def test_default_constants_defeat_global_env_leak_attack() -> None:
    """L2 round-4 finding F5: nothing ran this exact ambient-`GIT_CONFIG_
    GLOBAL` attack against the real, unmodified default `_GIT_CONFIG_
    GLOBAL_ENV_VALUE`/`_run_git`/`_scan_dangerous_git_config` to prove the
    shipped defaults themselves still neutralize it -- the fault-injection
    test above only proves it is load-bearing once *removed*. Uses its own
    self-contained temp file for the ambient `GIT_CONFIG_GLOBAL`, never the
    real host's own actual global config, so this one test is already
    independent of the host by construction -- no separate host-condition
    variant is needed, matching round-4's own explicit F6 instruction ("the
    same ... independent of the host"), which applies identically here. No
    module attribute is monkeypatched anywhere in this function."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "filter_executed_global_default.marker"
        fake_global_config = base / "fake-global-default.gitconfig"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        (repo / "a.secret").write_text("hello\n")
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        fake_global_config.write_text(f"[filter \"evil\"]\n\tclean = sh -c 'touch {marker}'\n")
        (repo / "a.secret").write_text("hello\n")
        (repo / "a.secret").touch()

        original_global_value = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = str(fake_global_config)
        try:
            capture = capture_local_repository(repo, [base])
        finally:
            if original_global_value is None:
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
            else:
                os.environ["GIT_CONFIG_GLOBAL"] = original_global_value

        check(
            "FTF-H007",
            "with the real, unmodified default GIT_CONFIG_GLOBAL=/dev/null "
            "override in place, an ambient GIT_CONFIG_GLOBAL pointed at a "
            "malicious filter config never leaks through and never executes",
            not marker.exists(),
        )
        check(
            "FTF-H007",
            "the clean repo's capture still succeeds normally, not refused "
            "or blocked by the (correctly) out-of-scope global config",
            capture.capture_method == "local_git_commit",
        )


def test_fault_injection_nosystem_env_isolation_is_load_bearing() -> None:
    """L2 round-3 finding 3 ("Use GIT_CONFIG_SYSTEM similarly for
    NOSYSTEM"): a real, external `GIT_CONFIG_SYSTEM` pointing at a temp
    file this test controls stands in for "whatever the real host's own
    `/etc/gitconfig` might contain" -- deterministic regardless of the
    host, never relying on its real content. With the normal, unmodified
    `GIT_CONFIG_NOSYSTEM=1` override in place, this redirected "system"
    config is excluded entirely (confirmed directly against a real git
    binary: `GIT_CONFIG_NOSYSTEM=1` suppresses `GIT_CONFIG_SYSTEM` too, not
    only the real `/etc/gitconfig`). With only the module-level `_GIT_
    CONFIG_NOSYSTEM_ENV_VALUE` toggle flipped to `None`, through the real,
    unmodified `_run_git`, the redirected config leaks through."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "filter_executed.marker"
        fake_system_config = base / "fake-system.gitconfig"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        (repo / "a.secret").write_text("hello\n")
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        fake_system_config.write_text(f"[filter \"evil\"]\n\tclean = sh -c 'touch {marker}'\n")
        (repo / "a.secret").write_text("hello\n")
        (repo / "a.secret").touch()

        original_system_value = os.environ.get("GIT_CONFIG_SYSTEM")
        original_nosystem_toggle = _connector_module._GIT_CONFIG_NOSYSTEM_ENV_VALUE
        original_scan = _connector_module._scan_dangerous_git_config
        os.environ["GIT_CONFIG_SYSTEM"] = str(fake_system_config)
        _connector_module._GIT_CONFIG_NOSYSTEM_ENV_VALUE = None
        _connector_module._scan_dangerous_git_config = lambda root, pattern="": ()
        try:
            capture_local_repository(repo, [base])
        except _connector_module.LocalRepositoryConnectorError:
            pass
        finally:
            if original_system_value is None:
                os.environ.pop("GIT_CONFIG_SYSTEM", None)
            else:
                os.environ["GIT_CONFIG_SYSTEM"] = original_system_value
            _connector_module._GIT_CONFIG_NOSYSTEM_ENV_VALUE = original_nosystem_toggle
            _connector_module._scan_dangerous_git_config = original_scan

        check(
            "FTF-H007",
            "with only the GIT_CONFIG_NOSYSTEM override toggle removed, through "
            "the real _run_git, a redirected GIT_CONFIG_SYSTEM genuinely leaks "
            "through and executes, independent of the real host's own "
            "/etc/gitconfig",
            marker.exists(),
        )


def test_default_constants_defeat_nosystem_env_leak_attack() -> None:
    """L2 round-4 finding F6: the fault-injection test above is deliberately
    self-contained (its own ambient `GIT_CONFIG_SYSTEM` temp file, never
    this sandbox's own real `/etc/gitconfig`) and only proves
    `GIT_CONFIG_NOSYSTEM=1` is load-bearing once removed. Round-4's own
    review found "Removing GIT_CONFIG_NOSYSTEM is RED only on a host whose
    /etc/gitconfig has filter.lfs. It is GREEN on a neutral host" -- this
    sandbox's own real `/etc/gitconfig`, confirmed directly, does carry
    `[filter "lfs"]` with `clean`/`smudge`/`process`/`required` all set, and
    a real `git-lfs` binary is on `PATH` -- exactly the host-dependent
    condition the finding names. This baseline test, per round-4's own
    explicit instruction ("the same [as F5], using an ambient GIT_CONFIG_
    SYSTEM set to the test's own file, independent of the host"), uses its
    own self-contained temp file rather than relying on that real ambient
    `/etc/gitconfig` at all -- so it is, by construction, independent of
    whether the host running it has LFS configured system-wide or not,
    resolving the host-dependence this exact finding named. No module
    attribute is monkeypatched anywhere in this function."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "filter_executed_system_default.marker"
        fake_system_config = base / "fake-system-default.gitconfig"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        (repo / "a.secret").write_text("hello\n")
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        fake_system_config.write_text(f"[filter \"evil\"]\n\tclean = sh -c 'touch {marker}'\n")
        (repo / "a.secret").write_text("hello\n")
        (repo / "a.secret").touch()

        original_system_value = os.environ.get("GIT_CONFIG_SYSTEM")
        os.environ["GIT_CONFIG_SYSTEM"] = str(fake_system_config)
        try:
            capture = capture_local_repository(repo, [base])
        finally:
            if original_system_value is None:
                os.environ.pop("GIT_CONFIG_SYSTEM", None)
            else:
                os.environ["GIT_CONFIG_SYSTEM"] = original_system_value

        check(
            "FTF-H007",
            "with the real, unmodified default GIT_CONFIG_NOSYSTEM=1 override "
            "in place, a redirected ambient GIT_CONFIG_SYSTEM pointed at a "
            "malicious filter config never leaks through and never executes",
            not marker.exists(),
        )
        check(
            "FTF-H007",
            "the clean repo's capture still succeeds normally, not refused "
            "or blocked by the (correctly) out-of-scope system config",
            capture.capture_method == "local_git_commit",
        )


def test_malicious_filter_process_driver_never_executes_and_capture_is_refused() -> None:
    """L2 round-3 finding 4 (round-2 finding (a), extended to `process`):
    confirmed directly against a real git binary that a `filter.<name>.
    process` driver (no `clean`) genuinely runs during a plain `git status`
    on a racily-clean tracked file (identical content, only the mtime
    changed) -- the same vector `clean` already proved, under a different
    key. `_UNNEUTRALIZABLE_FILTER_DRIVER_KEY_PATTERN` already names
    `process` (not only `clean`), so `capture_local_repository` refuses
    before ever reaching the vulnerable `git status` call."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        marker = base / "process_executed.marker"
        _init_repo(repo)
        (repo / ".gitattributes").write_text("*.secret filter=evil\n")
        (repo / "a.secret").write_text("hello\n")
        _run(repo, "add", ".gitattributes", "a.secret")
        _run(repo, "commit", "--quiet", "-m", "initial")
        _run(repo, "config", "filter.evil.process", f"sh -c 'touch {marker}; exit 1'")
        (repo / "a.secret").write_text("hello\n")
        (repo / "a.secret").touch()

        check(
            "FTF-H007",
            "a repo-defined process filter driver is detected and capture is "
            "refused before any working-tree git command could trigger it",
            _raises(DangerousGitConfigError, capture_local_repository, repo, [base]),
        )
        check(
            "FTF-H007",
            "the repo-controlled filter.evil.process command was never actually executed",
            not marker.exists(),
        )


def _make_populated_submodule(sub_repo: Path, marker: Path) -> None:
    """A real, standalone git repository a test turns into either a plain
    (manual `git update-index --cacheinfo`) or a registered (`git submodule
    add`) gitlink -- genuinely populated (a real `.git` of its own), with a
    `filter.evil.clean` driver set only inside *its own* config, never the
    superproject's."""
    _init_repo(sub_repo)
    (sub_repo / ".gitattributes").write_text("*.secret filter=evil\n")
    (sub_repo / "a.secret").write_text("hello\n")
    _run(sub_repo, "add", ".gitattributes", "a.secret")
    _run(sub_repo, "commit", "--quiet", "-m", "sub-initial")
    _run(sub_repo, "config", "filter.evil.clean", f"sh -c 'touch {marker}'")


def test_populated_plain_gitlink_submodule_filter_never_executes_and_capture_is_refused() -> None:
    """L2 round-3 finding 1: a plain gitlink -- a real, populated nested
    repository added via `git update-index --cacheinfo 160000,<sha>,<path>`
    directly, never `git submodule add`, so no `.gitmodules` exists at all
    -- with a `filter.evil.clean` driver set only in the sub-repository's
    own config. Confirmed directly against a real git binary: the
    superproject's own plain `git status` (pre-fix) recurses into this
    populated gitlink and genuinely runs that driver. `capture_local_
    repository` now refuses before any working-tree-content git command,
    via a pure `git ls-files -s` plumbing read."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        sub = repo / "sub"
        marker = base / "submodule_filter_executed.marker"
        _init_repo(repo)
        (repo / "top.txt").write_text("top\n")
        _run(repo, "add", "top.txt")
        _run(repo, "commit", "--quiet", "-m", "top-initial")
        _make_populated_submodule(sub, marker)
        sub_head = _run(sub, "rev-parse", "HEAD").stdout.strip()
        _run(repo, "update-index", "--add", "--cacheinfo", f"160000,{sub_head},sub")
        _run(repo, "commit", "--quiet", "-m", "add plain gitlink")
        (sub / "a.secret").write_text("hello\n")
        (sub / "a.secret").touch()

        check(
            "FTF-H007",
            "a populated plain gitlink is detected and capture is refused "
            "before any working-tree git command could recurse into it",
            _raises(PopulatedSubmoduleError, capture_local_repository, repo, [base]),
        )
        check(
            "FTF-H007",
            "the repo-controlled filter.evil.clean command inside the plain "
            "gitlink was never actually executed",
            not marker.exists(),
        )


def test_populated_registered_submodule_filter_never_executes_and_capture_is_refused() -> None:
    """L2 round-3 finding 1, the `git submodule add`-registered variant
    (distinct real path: `.gitmodules` exists, and the submodule's own git
    directory lives at `.git/modules/sub`, referenced from `sub/.git` as a
    gitfile, not a real nested `.git` directory). Confirmed directly against
    a real git binary: this variant is exploitable identically to the plain
    gitlink above, and is stopped the same way."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        sub_source = base / "sub-source"
        marker = base / "submodule_filter_executed.marker"
        _init_repo(repo)
        (repo / "top.txt").write_text("top\n")
        _run(repo, "add", "top.txt")
        _run(repo, "commit", "--quiet", "-m", "top-initial")
        _make_populated_submodule(sub_source, marker)
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
        (repo / "sub" / "a.secret").write_text("hello\n")
        (repo / "sub" / "a.secret").touch()

        check(
            "FTF-H007",
            "a populated registered submodule is detected and capture is "
            "refused before any working-tree git command could recurse into it",
            _raises(PopulatedSubmoduleError, capture_local_repository, repo, [base]),
        )
        check(
            "FTF-H007",
            "the repo-controlled filter.evil.clean command inside the "
            "registered submodule was never actually executed",
            not marker.exists(),
        )


def test_fault_injection_ignore_submodules_flag_is_load_bearing() -> None:
    """Law 1 -- shown able to fail, and the DoD's own explicit "test (i)
    alone" requirement: with only the module-level `_GIT_STATUS_IGNORE_
    SUBMODULES_FLAG` toggle removed (through the real, unmodified
    `_run_git`/`_git_dirty_signature`) -- and `_scan_populated_gitlinks`
    stubbed out, isolating this one layer, since the real pre-scan would
    otherwise independently refuse first and mask whether the flag itself
    does anything -- the populated-submodule filter driver from the test
    above genuinely executes, proving the flag itself (not merely the
    pre-scan) is load-bearing."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        sub = repo / "sub"
        marker = base / "submodule_filter_executed.marker"
        _init_repo(repo)
        (repo / "top.txt").write_text("top\n")
        _run(repo, "add", "top.txt")
        _run(repo, "commit", "--quiet", "-m", "top-initial")
        _make_populated_submodule(sub, marker)
        sub_head = _run(sub, "rev-parse", "HEAD").stdout.strip()
        _run(repo, "update-index", "--add", "--cacheinfo", f"160000,{sub_head},sub")
        _run(repo, "commit", "--quiet", "-m", "add plain gitlink")
        (sub / "a.secret").write_text("hello\n")
        (sub / "a.secret").touch()

        original_flag = _connector_module._GIT_STATUS_IGNORE_SUBMODULES_FLAG
        original_scan = _connector_module._scan_populated_gitlinks
        _connector_module._GIT_STATUS_IGNORE_SUBMODULES_FLAG = None
        _connector_module._scan_populated_gitlinks = lambda root: ()
        try:
            capture_local_repository(repo, [base])
        except _connector_module.LocalRepositoryConnectorError:
            pass
        finally:
            _connector_module._GIT_STATUS_IGNORE_SUBMODULES_FLAG = original_flag
            _connector_module._scan_populated_gitlinks = original_scan

        check(
            "FTF-H007",
            "with only the --ignore-submodules=all flag removed, through the "
            "real _run_git, git status genuinely recurses into the populated "
            "submodule and its filter driver executes",
            marker.exists(),
        )


def _assert_ignore_submodules_flag_defeats_attack_by_real_defaults() -> None:
    """L2 round-4 finding F1: the fault-injection test above only proves
    `_GIT_STATUS_IGNORE_SUBMODULES_FLAG` is load-bearing once it is
    deliberately removed. This is the missing baseline the round-4 DoD asks
    for verbatim: "stub only `_scan_populated_gitlinks` to return `()`,
    populate a submodule filter, keep the flag, run capture, and assert no
    marker." The real `_GIT_STATUS_IGNORE_SUBMODULES_FLAG` default is never
    touched here -- only the independent pre-scan is stubbed out, isolating
    this one layer exactly as the fault-injection test does, so this test
    proves the flag ITSELF (under its own real, unmodified default) still
    defeats the attack even when the separate pre-scan that would otherwise
    also catch it first is bypassed."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        sub = repo / "sub"
        marker = base / "submodule_filter_executed_default.marker"
        _init_repo(repo)
        (repo / "top.txt").write_text("top\n")
        _run(repo, "add", "top.txt")
        _run(repo, "commit", "--quiet", "-m", "top-initial")
        _make_populated_submodule(sub, marker)
        sub_head = _run(sub, "rev-parse", "HEAD").stdout.strip()
        _run(repo, "update-index", "--add", "--cacheinfo", f"160000,{sub_head},sub")
        _run(repo, "commit", "--quiet", "-m", "add plain gitlink")
        (sub / "a.secret").write_text("hello\n")
        (sub / "a.secret").touch()

        original_scan = _connector_module._scan_populated_gitlinks
        _connector_module._scan_populated_gitlinks = lambda root: ()
        try:
            capture_local_repository(repo, [base])
        except _connector_module.LocalRepositoryConnectorError:
            pass
        finally:
            _connector_module._scan_populated_gitlinks = original_scan

        check(
            "FTF-H007",
            "with the real, unmodified default _GIT_STATUS_IGNORE_SUBMODULES_"
            "FLAG (only the independent pre-scan stubbed out, per the DoD's "
            "own isolation requirement), git status still never recurses "
            "into the populated submodule and its filter driver never fires",
            not marker.exists(),
        )


def test_default_constants_defeat_submodule_recursion_attack_on_host_as_is() -> None:
    """Round-4 F1 baseline, run on this sandbox's own real ambient host
    environment, completely unmodified."""
    _assert_ignore_submodules_flag_defeats_attack_by_real_defaults()


def test_default_constants_defeat_submodule_recursion_attack_on_neutral_host() -> None:
    """Round-4 F1 baseline, independently re-run with `GIT_CONFIG_SYSTEM`
    forced to a genuinely empty file -- a neutral host -- proving the
    result does not depend on this sandbox's own real host-level git
    config (confirmed directly to carry `filter.lfs`) either way."""
    _with_empty_git_config_system(_assert_ignore_submodules_flag_defeats_attack_by_real_defaults)


def test_symlink_pointing_inside_root_is_followed_safely() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "real_dir").mkdir()
        (repo / "real_dir" / "inner.txt").write_text("inner\n")
        (repo / "a.txt").write_text("a\n")
        _run(repo, "add", "a.txt", "real_dir/inner.txt")
        _run(repo, "commit", "--quiet", "-m", "first")
        (repo / "link_to_real_dir").symlink_to(repo / "real_dir", target_is_directory=True)
        (repo / "untracked.txt").write_text("dirty\n")

        capture = capture_local_repository(repo, [base])
        check(
            "FTF-04A06",
            "a symlink pointing inside the captured root is followed, not refused",
            "link_to_real_dir/inner.txt" in {e.relative_path for e in capture.manifest_entries},
        )


def test_not_a_git_repository_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        plain = base / "plain"
        plain.mkdir()
        (plain / "file.txt").write_text("no git here\n")

        check(
            "FTF-04A06",
            "a plain, non-Git directory is refused rather than silently captured",
            _raises(NotAGitRepositoryError, capture_local_repository, plain, [base]),
        )


def test_a_subdirectory_of_a_larger_repo_is_refused_as_not_the_real_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "sub").mkdir()
        (repo / "sub" / "file.txt").write_text("nested\n")
        _run(repo, "add", "sub/file.txt")
        _run(repo, "commit", "--quiet", "-m", "first")

        check(
            "FTF-04A06",
            "a real subdirectory of a larger repository is refused, not silently "
            "treated as its own root",
            _raises(NotAGitRepositoryError, capture_local_repository, repo / "sub", [base]),
        )


def test_empty_repository_with_no_commits_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)

        check(
            "FTF-04A06",
            "a real Git checkout with no commits yet is refused, not silently captured",
            _raises(EmptyRepositoryError, capture_local_repository, repo, [base]),
        )


def test_concurrent_drift_during_capture_is_refused(monkeypatch=None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "a.txt").write_text("a\n")
        _run(repo, "add", "a.txt")
        _run(repo, "commit", "--quiet", "-m", "first")
        (repo / "b.txt").write_text("dirty\n")

        real_signature = _connector_module._git_dirty_signature
        calls = {"count": 0}

        def _flaky_signature(root: Path) -> str:
            calls["count"] += 1
            result = real_signature(root)
            if calls["count"] == 2:
                # Simulate a real concurrent change landing between this
                # connector's own before/after checks, without relying on
                # an actual, unreliable filesystem race in a test.
                return result + "\n?? phantom-concurrent-file.txt"
            return result

        original = _connector_module._git_dirty_signature
        _connector_module._git_dirty_signature = _flaky_signature
        try:
            check(
                "FTF-04A06",
                "a real signature change between the before/after checks is refused",
                _raises(ConcurrentModificationError, capture_local_repository, repo, [base]),
            )
        finally:
            _connector_module._git_dirty_signature = original


def test_capture_local_repository_is_the_real_exported_callable() -> None:
    check(
        "FTF-04A06",
        "capture_local_repository and resolve_allowed_root are the real, callable public surface",
        callable(capture_local_repository) and callable(resolve_allowed_root),
    )
    check(
        "FTF-04A06",
        "LocalRepositoryCapture is a real frozen dataclass, not a loose dict",
        LocalRepositoryCapture.__dataclass_params__.frozen,  # type: ignore[attr-defined]
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A06 local repository connector tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A06 local repository connector tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
