#!/usr/bin/env python3
"""FTF-1505 -- Secret scanning, round 3 (Section 45-47, 52-53, 65-66, 77, 81).

Acceptance: "Scan current source/history/artifacts/log samples with
seeded harmless secret markers." Negative: "Scanner omissions/excluded
production dirs and actual exposed credential are release blockers."

Round 3 (Control-plane return 2026-10-15T14:14Z, QUEUE.md "0h"): round 2
scanned every tracked file/blob/artifact with no path exclusion, but left
several fail-OPEN paths (a quoted/escaped non-ASCII git path silently
skipped, oversized content silently truncated, a corrupt archive member
aborting every later member in the same archive, a history blob that is
itself an archive never getting member extraction at all), an incomplete
quick-gate (missed `sk_live_`, `private_key=`, `session_key=`, a tab after
`Bearer`), a reversible `value_sha256` printed for brand-new findings, a
`parse_upload_artifact_paths` regex that missed `actions/upload-artifact/
merge@...` and a path-traversal upload path, committed seed literals that
themselves needed baselining, a live-log lane that encoded canaries as
base64/hex but never actually decoded either layer before checking the
canary shape (so those probes could not fail), and a history lane that
scanned every fetched ref -- including another PR's own unmerged branch
-- so one PR's secret-shaped fixture reddened every other open PR.

Reuse decisions (Section 84.3 "reuse an existing compatible module
instead of creating a duplicate"):

* `secret_rules` (this directory) -- round 3's own extraction: every rule
  pattern, the quick-gate derivation, runtime seed generation and the
  dependency-free single-file CLI now live there so the `mutation-gate`/
  `ci-verdict` CI jobs can scan their own freshly written report with no
  `pyyaml`/`uvicorn` dependency chain (F-ARTIFACTS). This module imports
  every name from it; nothing is duplicated.
* `ScenarioResult`/`build_evidence` -- from `tests/security/
  tenant_isolation_suite.py`, this task's own `contract_owner: FTF-1501`.
* `SOURCE_ROOTS` -- from `scripts/product_truth_check.py`.
* `install_redaction` -- the already-certified FTF-0904 pipeline.
* `generate_canary`/`encode_canary`/`fragment_canary` -- FTF-0905's own
  canary generator, for the log-lane leak check.
* `StructuredEvent`/`emit_to_stream` -- FTF-0006's content-free
  observability seam, cross-checked directly: it carries no redaction of
  its own (by design its fields cannot hold free text), so this suite's
  own canary detection is the only backstop if a caller ever misused it.

Targets:
  source    -- every file `git ls-files -z` reports at HEAD, text and
               binary, plus zip/tar/gz/whl archive members (bounded,
               bomb-safe). No path exclusion.
  history   -- every unique blob reachable from the checked-out HEAD by
               default (`--scope pr`, the in-CI default: a secret-shaped
               fixture on another fetched-but-unmerged ref can never
               redden an unrelated PR); `--scope all-refs` additionally
               covers every ref, attributing a HEAD-unreachable finding
               to the branch(es) that carry it. Refuses a shallow clone
               either way. Now also extracts archive members from a
               blob that is itself an archive (round 2 never did).
  artifacts -- every file under `ARTIFACT_ROOTS`, plus a completeness
               check (structural YAML parse of ci.yml, not a fixed-
               window regex) that every `actions/upload-artifact(/merge)`
               `path:` lies under a declared root, with no `..` escape.
  log_samples -- (a) a local logger wired with the real FTF-0904
               pipeline, covering every one of this suite's own rule
               families (round 2 covered 5); (b) a real `uvicorn` boot
               probed with every family's seed and FTF-0905 canaries
               (literal, base64, hex, url, fragmented) across the query
               string, a header, a cookie, a JSON body and an invalid-
               path-param validation error, against 401/404/422; (c) a
               direct `StructuredEvent`/`emit_to_stream` cross-check.

negative_acceptance made executable:
  - `assert_dirs_covered` / `assert_scanned_set_matches_tracked_files`
    fail closed the moment this suite's own inclusion logic silently
    yields fewer files than `git ls-files -z` actually reports.
  - `assert_artifact_uploads_covered` fails closed the moment a ci.yml
    upload path is not under a declared artifact root.
  - `assert_not_shallow` fails closed on a shallow clone.
  - A `scan_incomplete` finding is itself blocking: oversized content, a
    decode-cap overrun, or a corrupt archive member past which other
    members could not be read are never silently treated as "clean".
  - `evaluate`/`main` raise `SecretScanError` the moment one real
    (non-baselined) secret shape is found anywhere; a stale baseline
    entry is itself a blocking finding.

Run directly:
    python3 tests/security/secret_scanning.py [--scope pr|all-refs]
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import logging
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.canary_secret_generator import (  # noqa: E402
    encode_canary,
    fragment_canary,
    generate_canary,
)
from packages.security.log_redaction import install_redaction  # noqa: E402
from scripts.product_truth_check import SOURCE_ROOTS as _PRODUCT_SOURCE_ROOTS  # noqa: E402
from secret_rules import FORBIDDEN_BASELINE_KEYS as _FORBIDDEN_BASELINE_KEYS  # noqa: E402
from secret_rules import (  # noqa: E402
    MAX_SCAN_BYTES,
    RULES,
    BaselineEntry,
    SecretFinding,
    SecretScanError,
    baseline_fingerprint,
    generate_seed_for_rule,
    scan_line,  # noqa: E402
)
from secret_rules import _matches_long_line as _matches_text  # noqa: E402
from secret_rules import value_sha256 as _value_sha256  # noqa: E402
from tenant_isolation_suite import (  # noqa: E402
    ScenarioResult,
    TenantIsolationEvidence,
    TenantIsolationSuiteError,
    build_evidence,
)

from app.config.observability_foundation import (  # noqa: E402
    StructuredEvent,
    emit_to_stream,
)

__all__ = [
    "SOURCE_ROOTS",
    "ARTIFACT_ROOTS",
    "RULES",
    "SecretScanError",
    "SecretFinding",
    "scan_line",
    "iter_tracked_files",
    "iter_tracked_files_under",
    "BaselineEntry",
    "load_baseline",
    "is_baselined",
    "assert_dirs_covered",
    "assert_not_shallow",
    "assert_scanned_set_matches_tracked_files",
    "parse_upload_artifact_paths",
    "assert_artifact_uploads_covered",
    "scan_source",
    "scan_artifact_roots",
    "scan_history",
    "scan_log_samples",
    "scan_live_app_log_samples",
    "scan_structured_event_cross_check",
    "evaluate",
    "generate_seed_values",
]

SOURCE_ROOTS: tuple[str, ...] = tuple(_PRODUCT_SOURCE_ROOTS)
ARTIFACT_ROOTS: tuple[str, ...] = ("docs/certifications", "docs/progress", "reports")
BASELINE_PATH = Path(__file__).resolve().parent / "secret_scan_baseline.yaml"
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"

# MAX_SCAN_BYTES / MAX_BASE64_ATTEMPTS_PER_UNIT / quick_gate_hints_for_rule are imported above
# for reuse by tests/tasks/test_ftf_1505.py, not re-exported as public API in __all__.


# ---------------------------------------------------------------------------
# Baseline: entries keyed by sha256(rule NUL value) -- never the raw
# value, a file path or a glob. A reviewed fixture is waived everywhere it
# recurs, never scoped to one location.
# ---------------------------------------------------------------------------


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, BaselineEntry]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_entries = data.get("entries") or []
    entries: dict[str, BaselineEntry] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise SecretScanError(f"malformed secret-scan baseline entry: {raw!r}")
        for forbidden in _FORBIDDEN_BASELINE_KEYS:
            if forbidden in raw:
                raise SecretScanError(
                    f"secret-scan baseline entry carries a {forbidden!r} key -- baseline "
                    f"entries are keyed by fingerprint only, never a value/path/glob: {raw!r}"
                )
        fingerprint = str(raw.get("fingerprint") or "")
        reason = str(raw.get("reason") or "")
        reviewed_by = str(raw.get("reviewed_by") or "")
        reviewed_on = str(raw.get("reviewed_on") or "")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            or not reason.strip()
            or not reviewed_by.strip()
            or not reviewed_on.strip()
        ):
            raise SecretScanError(
                "malformed secret-scan baseline entry (needs a 64-hex-char fingerprint and "
                f"non-empty reason/reviewed_by/reviewed_on): {raw!r}"
            )
        if fingerprint in entries:
            raise SecretScanError(f"duplicate secret-scan baseline fingerprint: {fingerprint}")
        entries[fingerprint] = BaselineEntry(fingerprint, reason, reviewed_by, reviewed_on)
    return entries


def is_baselined(rule: str, value: str, baseline: dict[str, BaselineEntry]) -> bool:
    return baseline_fingerprint(rule, value) in baseline


def assert_baseline_fresh(baseline: dict[str, BaselineEntry], seen_fingerprints: set[str]) -> None:
    stale = set(baseline) - seen_fingerprints
    if stale:
        raise SecretScanError(
            f"{len(stale)} stale secret-scan baseline entr{'y' if len(stale) == 1 else 'ies'} "
            f"matched nothing this run: {sorted(stale)}"
        )


# ---------------------------------------------------------------------------
# File inclusion: every git-tracked path, with no exclusion. `-z` (NUL-
# separated, raw bytes) is required: plain `git ls-files` applies
# `core.quotePath` by default and octal-escapes/quotes any non-ASCII
# filename (e.g. `"caf\303\251.txt"`), which then never resolves back to
# a real path on disk -- `full_path.is_file()` silently returns False and
# the file is skipped with no signal at all (F-SCOPE fail-open, fixed).
# ---------------------------------------------------------------------------


def iter_tracked_files(root: Path) -> list[str]:
    """Every git-tracked file at HEAD, raw (unquoted) paths. No path
    exclusion exists."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8", errors="surrogateescape").split("\0") if p]


def iter_tracked_files_under(root: Path, dirs: tuple[str, ...]) -> list[str]:
    return [
        f for f in iter_tracked_files(root) if any(f == d or f.startswith(d + "/") for d in dirs)
    ]


def assert_dirs_covered(root: Path, dirs: tuple[str, ...]) -> None:
    for directory in dirs:
        if not (root / directory).is_dir():
            continue
        if not iter_tracked_files_under(root, (directory,)):
            raise SecretScanError(
                f"secret scan omission: {directory!r} exists but the scanner's own "
                "file-inclusion logic covers zero tracked files in it"
            )


_REGULAR_FILE_MODES = ("100644", "100755")


def _tracked_regular_files(root: Path) -> set[str]:
    """`git ls-files -s -z`, filtered to real blobs (mode 100644/100755)
    -- a symlink (120000) or a submodule gitlink (160000) is excluded by
    design, everything else is expected to be scanned."""
    out = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    paths: set[str] = set()
    for entry in out.decode("utf-8", errors="surrogateescape").split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        mode = meta.split(" ", 1)[0]
        if mode in _REGULAR_FILE_MODES:
            paths.add(path)
    return paths


def assert_scanned_set_matches_tracked_files(root: Path, scanned_paths: set[str]) -> None:
    """negative_acceptance, made executable: the set of files this run
    actually scanned must equal every regular tracked file `git` itself
    reports -- a silently-skipped path (encoding failure, an unreadable
    file, a future inclusion-logic bug) is a release blocker, never a
    quiet gap."""
    expected = _tracked_regular_files(root)
    missing = expected - scanned_paths
    if missing:
        raise SecretScanError(
            f"secret scan omission: {len(missing)} tracked file(s) were never scanned: "
            f"{sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}"
        )


def assert_not_shallow(root: Path) -> None:
    out = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if out == "true":
        raise SecretScanError(
            "secret-scan history lane refuses a shallow clone (git rev-parse "
            "--is-shallow-repository == true) -- fetch full history (fetch-depth: 0) first; "
            "NOT_EXERCISED is a release blocker, never silently narrowed to HEAD"
        )


# ---------------------------------------------------------------------------
# Artifact-upload completeness: a structural YAML parse of ci.yml's own
# jobs/steps (round 2 used a fixed 9-line regex lookahead window, which
# missed a `path:` placed further away and the real `actions/upload-
# artifact/merge@...` action name entirely).
# ---------------------------------------------------------------------------


def parse_upload_artifact_paths(ci_yml_text: str) -> list[str]:
    """Every `with.path` value of every `actions/upload-artifact` (or its
    `/merge` variant) step, in every job, parsed structurally."""
    workflow = yaml.safe_load(ci_yml_text) or {}
    paths: list[str] = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            uses = str(step.get("uses") or "")
            if not re.match(r"^actions/upload-artifact(?:/merge)?@", uses):
                continue
            raw_path = (step.get("with") or {}).get("path")
            if raw_path is None:
                continue
            for line in str(raw_path).splitlines():
                stripped = line.strip().strip("\"'")
                if stripped:
                    paths.append(stripped)
    return paths


def _is_path_traversal(path: str) -> bool:
    return any(part == ".." for part in Path(path).parts)


def assert_artifact_uploads_covered(ci_yml_text: str, roots: tuple[str, ...]) -> None:
    """negative_acceptance: a newly uploaded CI artifact with no declared
    scan root -- or one that tries to escape its declared root via `..`
    -- fails closed instead of silently going unscanned forever."""
    for upload_path in parse_upload_artifact_paths(ci_yml_text):
        if _is_path_traversal(upload_path):
            raise SecretScanError(
                f"ci.yml uploads artifact path {upload_path!r}, which contains a '..' "
                "path-traversal segment -- refused outright"
            )
        if not any(upload_path == root or upload_path.startswith(root + "/") for root in roots):
            raise SecretScanError(
                f"ci.yml uploads artifact path {upload_path!r}, which lies under none of the "
                f"declared artifact roots {roots!r} -- add its root or it is never scanned"
            )


# ---------------------------------------------------------------------------
# Binary-safe content scanning, with bounded, fail-closed archive-member
# recursion -- shared by source/artifact/history lanes alike (round 2
# only gave archive extraction to source/artifact; a history blob that
# is itself an archive was never opened at all).
# ---------------------------------------------------------------------------

_MAX_ARCHIVE_MEMBERS = 5000
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 500 * 1024 * 1024


def _looks_like_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04" or data[:4] == b"PK\x05\x06"


def _looks_like_gzip(data: bytes) -> bool:
    return data[:2] == b"\x1f\x8b"


def _bounded_gzip_decompress(data: bytes, max_bytes: int) -> bytes:
    import gzip

    out = io.BytesIO()
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        remaining = max_bytes
        while remaining > 0:
            chunk = gz.read(min(65536, remaining))
            if not chunk:
                break
            out.write(chunk)
            remaining -= len(chunk)
    return out.getvalue()


_ARCHIVE_SENTINEL_NAME = "<archive>"
# Round 4 (Control-plane return 2026-10-16, points 3-4): round 3 caught a
# corrupt MEMBER inside an already-open archive, but two fail-OPEN gaps
# remained -- (a) a corrupt DEFLATED member's `zlib.error` was not in
# either per-member except tuple, so it escaped past the member loop,
# landed on the function's own outer `except Exception: return`, and
# silently abandoned every member after it (the exact class round 3's own
# member-loop fix was supposed to close); (b) a TRUNCATED archive (bad
# header, EOF mid-central-directory, a corrupt gzip header) failed the
# OPEN call itself, which the same outer bare `except: return` swallowed
# with no signal at all, and an oversized MEMBER was silently truncated
# with no `scan_incomplete` (round 2's top-level `content_truncated` flag
# was never threaded down into archive-member extraction). Both are now
# fail-closed: an open failure yields one `(_ARCHIVE_SENTINEL_NAME, None,
# False)` row; a member/content-cap overrun yields `(name, data, True)`.
_ARCHIVE_OPEN_ERRORS = (
    zipfile.BadZipFile,
    tarfile.TarError,
    OSError,
    EOFError,
    zlib.error,
)
_ARCHIVE_MEMBER_READ_ERRORS = (
    zipfile.BadZipFile,
    tarfile.TarError,
    OSError,
    RuntimeError,
    EOFError,
    zlib.error,
)


def _iter_archive_members(data: bytes, name: str) -> Iterator[tuple[str, bytes | None, bool]]:
    """Bomb-safe bounded extraction of zip/whl/jar/egg and tar/tar.gz
    members, plus a bare .gz. Declared member count/uncompressed size is
    checked BEFORE any member is read. Yields `(member_name, content,
    truncated)`: `content is None` means the member (or, with
    `member_name == _ARCHIVE_SENTINEL_NAME`, the archive itself) could not
    be read at all -- a fail-closed signal the caller turns into a
    blocking finding -- and extraction CONTINUES to the next member,
    rather than abandoning every member after it. `truncated=True` means
    the member read past `MAX_SCAN_BYTES`: the (bounded) content is still
    yielded for scanning, but the caller must also raise `scan_incomplete`
    for it, the same way the top-level per-file/blob bound already does."""
    lower = name.lower()
    try:
        if lower.endswith((".zip", ".whl", ".jar", ".egg")) or _looks_like_zip(data):
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except _ARCHIVE_OPEN_ERRORS:
                yield _ARCHIVE_SENTINEL_NAME, None, False
                return
            with zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                if len(infos) > _MAX_ARCHIVE_MEMBERS:
                    raise SecretScanError(f"archive {name!r} exceeds member cap: {len(infos)}")
                if sum(i.file_size for i in infos) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise SecretScanError(f"archive {name!r} exceeds uncompressed-size cap")
                for info in infos:
                    try:
                        with zf.open(info) as handle:
                            member_data = handle.read(MAX_SCAN_BYTES + 1)
                    except _ARCHIVE_MEMBER_READ_ERRORS:
                        yield info.filename, None, False
                        continue
                    yield (
                        info.filename,
                        member_data[:MAX_SCAN_BYTES],
                        len(member_data) > MAX_SCAN_BYTES,
                    )
            return
        if lower.endswith((".tar.gz", ".tgz", ".tar")):
            mode = "r:gz" if lower.endswith((".tar.gz", ".tgz")) else "r:"
            try:
                tf = tarfile.open(fileobj=io.BytesIO(data), mode=mode)
            except _ARCHIVE_OPEN_ERRORS:
                yield _ARCHIVE_SENTINEL_NAME, None, False
                return
            with tf:
                members = [m for m in tf.getmembers() if m.isfile()]
                if len(members) > _MAX_ARCHIVE_MEMBERS:
                    raise SecretScanError(f"archive {name!r} exceeds member cap: {len(members)}")
                if sum(m.size for m in members) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise SecretScanError(f"archive {name!r} exceeds uncompressed-size cap")
                for member in members:
                    try:
                        extracted = tf.extractfile(member)
                        member_data = (
                            extracted.read(MAX_SCAN_BYTES + 1) if extracted is not None else b""
                        )
                    except _ARCHIVE_MEMBER_READ_ERRORS:
                        yield member.name, None, False
                        continue
                    yield (
                        member.name,
                        member_data[:MAX_SCAN_BYTES],
                        len(member_data) > MAX_SCAN_BYTES,
                    )
            return
        if lower.endswith(".gz") and _looks_like_gzip(data):
            try:
                content = _bounded_gzip_decompress(data, MAX_SCAN_BYTES + 1)
            except _ARCHIVE_OPEN_ERRORS:
                yield _ARCHIVE_SENTINEL_NAME, None, False
                return
            yield name[:-3], content[:MAX_SCAN_BYTES], len(content) > MAX_SCAN_BYTES
    except SecretScanError:
        raise
    except Exception:
        yield _ARCHIVE_SENTINEL_NAME, None, False


def _scan_unit_text(
    data: bytes, *, include_canary: bool
) -> tuple[list[tuple[str, str, str]], bool]:
    """(rule, value, location_suffix) tuples for one file/blob/member's
    content, plus whether a decode cap was hit anywhere in it. Text
    content is scanned per line (":<lineno>" suffix); content that is
    not valid UTF-8 is scanned whole, as latin-1 (a 1:1 byte mapping that
    never raises, so a binary file's ASCII-shaped secrets are still
    found) with no line suffix."""
    incomplete = False

    def _mark() -> None:
        nonlocal incomplete
        incomplete = True

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("latin-1")
        results = [
            (rule, value, "")
            for rule, value in _matches_text(
                content, include_canary=include_canary, on_incomplete=_mark
            )
        ]
        return results, incomplete
    results = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, value in _matches_text(line, include_canary=include_canary, on_incomplete=_mark):
            results.append((rule, value, f":{lineno}"))
    return results, incomplete


def _scan_content_and_members(
    location_prefix: str,
    data: bytes,
    *,
    include_canary: bool,
    content_truncated: bool,
) -> tuple[list[tuple[str, str, str]], bool]:
    """`(rule, value, location)` tuples for `data` itself plus any
    archive members inside it. Returns `(results, incomplete)`."""
    all_results: list[tuple[str, str, str]] = []
    matches, incomplete = _scan_unit_text(data, include_canary=include_canary)
    incomplete = incomplete or content_truncated
    for rule, value, loc_suffix in matches:
        all_results.append((rule, value, f"{location_prefix}{loc_suffix}"))
    for member_name, member_data, member_truncated in _iter_archive_members(data, location_prefix):
        if member_data is None:
            incomplete = True
            all_results.append(
                ("scan_incomplete", "", f"{location_prefix}!{member_name} (unreadable member)")
            )
            continue
        if member_truncated:
            incomplete = True
            all_results.append(
                (
                    "scan_incomplete",
                    "",
                    f"{location_prefix}!{member_name} (member exceeds scan bound)",
                )
            )
        member_matches, m_incomplete = _scan_unit_text(member_data, include_canary=include_canary)
        incomplete = incomplete or m_incomplete
        for rule, value, loc_suffix in member_matches:
            all_results.append((rule, value, f"{location_prefix}!{member_name}{loc_suffix}"))
    if content_truncated:
        all_results.append(
            ("scan_incomplete", "", f"{location_prefix} (content exceeds scan bound)")
        )
    return all_results, incomplete


def _record_results(
    raw_results: list[tuple[str, str, str]],
    baseline: dict[str, BaselineEntry],
    target: str,
    seen_fingerprints: set[str],
) -> tuple[list[SecretFinding], bool]:
    findings: list[SecretFinding] = []
    blocking_any = False
    for rule, value, location in raw_results:
        if rule == "scan_incomplete":
            findings.append(
                SecretFinding(
                    rule="scan_incomplete",
                    target=target,
                    location=location,
                    value_sha256="",
                    blocking=True,
                )
            )
            blocking_any = True
            continue
        fingerprint = baseline_fingerprint(rule, value)
        seen_fingerprints.add(fingerprint)
        blocking = fingerprint not in baseline
        findings.append(
            SecretFinding(
                rule=rule,
                target=target,
                location=location,
                value_sha256=_value_sha256(value),
                blocking=blocking,
            )
        )
        if blocking:
            blocking_any = True
    return findings, blocking_any


# ---------------------------------------------------------------------------
# source / artifacts: scan a real file tree.
# ---------------------------------------------------------------------------


def _read_bytes_bounded(path: Path) -> tuple[bytes, bool]:
    with path.open("rb") as handle:
        data = handle.read(MAX_SCAN_BYTES + 1)
    truncated = len(data) > MAX_SCAN_BYTES
    return data[:MAX_SCAN_BYTES], truncated


def scan_source(
    root: Path,
    baseline: dict[str, BaselineEntry],
    *,
    target: str = "source",
    seen_fingerprints: set[str] | None = None,
    scanned_paths: set[str] | None = None,
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Scans every git-tracked file, with no path exclusion. One
    `ScenarioResult` per scanned FILE (never per finding, so an all-clean
    scan still yields real, non-vacuous evidence)."""
    seen_fingerprints = seen_fingerprints if seen_fingerprints is not None else set()
    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []
    for rel_path in iter_tracked_files(root):
        full_path = root / rel_path
        if not full_path.is_file():
            continue
        data, truncated = _read_bytes_bounded(full_path)
        raw_results, _incomplete = _scan_content_and_members(
            rel_path, data, include_canary=False, content_truncated=truncated
        )
        file_findings, file_blocking = _record_results(
            raw_results, baseline, target, seen_fingerprints
        )
        findings.extend(file_findings)
        if scanned_paths is not None:
            scanned_paths.add(rel_path)
        results.append(
            ScenarioResult(
                resource_kind=target,
                check=next((f.rule for f in file_findings if f.blocking), "clean"),
                method="SCAN",
                path=rel_path,
                status=1 if file_blocking else 0,
                expected_denied=True,
                passed=not file_blocking,
            )
        )
    return findings, results


def scan_artifact_roots(
    root: Path,
    baseline: dict[str, BaselineEntry],
    *,
    roots: tuple[str, ...] = ARTIFACT_ROOTS,
    target: str = "artifact",
    seen_fingerprints: set[str] | None = None,
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    seen_fingerprints = seen_fingerprints if seen_fingerprints is not None else set()
    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []
    for base in roots:
        base_path = root / base
        if not base_path.is_dir():
            continue
        for file_path in sorted(p for p in base_path.rglob("*") if p.is_file()):
            rel_path = file_path.relative_to(root).as_posix()
            data, truncated = _read_bytes_bounded(file_path)
            raw_results, _incomplete = _scan_content_and_members(
                rel_path, data, include_canary=True, content_truncated=truncated
            )
            file_findings, file_blocking = _record_results(
                raw_results, baseline, target, seen_fingerprints
            )
            findings.extend(file_findings)
            results.append(
                ScenarioResult(
                    resource_kind=target,
                    check=next((f.rule for f in file_findings if f.blocking), "clean"),
                    method="SCAN",
                    path=rel_path,
                    status=1 if file_blocking else 0,
                    expected_denied=True,
                    passed=not file_blocking,
                )
            )
    return findings, results


# ---------------------------------------------------------------------------
# history: every unique blob reachable from HEAD (`--scope pr`, the CI
# default) or every ref (`--scope all-refs`), with branch attribution for
# a finding HEAD cannot reach.
# ---------------------------------------------------------------------------


def iter_history_blob_ids(root: Path, refs: tuple[str, ...] = ("HEAD",)) -> list[tuple[str, str]]:
    """Returns [(blob_sha, path_hint)] for every unique blob reachable
    from `refs` (default: HEAD only -- the PR's own checked-out merge
    ref). Pass `refs=("--all",)` for every ref."""
    rev_list = subprocess.run(
        ["git", "rev-list", "--objects", *refs],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    path_hint: dict[str, str] = {}
    order: list[str] = []
    for line in rev_list.splitlines():
        sha, _, path = line.partition(" ")
        if sha not in path_hint:
            path_hint[sha] = path or sha[:12]
            order.append(sha)
    if not order:
        return []
    batch_check = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        cwd=root,
        input="\n".join(order),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    blobs: list[tuple[str, str]] = []
    for line in batch_check.splitlines():
        sha, _, kind = line.partition(" ")
        if kind == "blob":
            blobs.append((sha, path_hint[sha]))
    return blobs


def _iter_ref_names(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line and not line.endswith("/HEAD")]


def _attribute_strays_to_refs(root: Path, stray_blob_ids: set[str]) -> dict[str, list[str]]:
    """For each blob id in `stray_blob_ids` (already known to be
    unreachable from HEAD), the ref name(s) whose own history contains
    it -- "reports each finding with the branch that carries it"."""
    if not stray_blob_ids:
        return {}
    attribution: dict[str, list[str]] = {sha: [] for sha in stray_blob_ids}
    for ref in _iter_ref_names(root):
        try:
            ref_blob_ids = {sha for sha, _ in iter_history_blob_ids(root, refs=(ref,))}
        except subprocess.CalledProcessError:
            continue
        for sha in stray_blob_ids & ref_blob_ids:
            attribution[sha].append(ref)
    return attribution


def _iter_history_blob_contents(
    root: Path, blob_ids: list[str]
) -> Iterator[tuple[str, bytes, bool]]:
    """Streams every blob's content (and whether it was truncated) from
    one long-lived `git cat-file --batch` process."""
    if not blob_ids:
        return
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None

    def _feed() -> None:
        assert proc.stdin is not None
        try:
            for sha in blob_ids:
                proc.stdin.write((sha + "\n").encode("ascii"))
            proc.stdin.flush()
        finally:
            proc.stdin.close()

    feeder = threading.Thread(target=_feed, daemon=True)
    feeder.start()
    try:
        for sha in blob_ids:
            header = proc.stdout.readline().decode("ascii", errors="replace")
            parts = header.split()
            if len(parts) != 3 or parts[1] != "blob":
                raise SecretScanError(f"git cat-file --batch desynced on {sha}: {header!r}")
            size = int(parts[2])
            remaining = size
            chunks: list[bytes] = []
            total_kept = 0
            while remaining > 0:
                chunk = proc.stdout.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                if total_kept < MAX_SCAN_BYTES:
                    keep = chunk[: MAX_SCAN_BYTES - total_kept]
                    chunks.append(keep)
                    total_kept += len(keep)
            proc.stdout.read(1)  # trailing newline after the object data
            yield sha, b"".join(chunks), size > MAX_SCAN_BYTES
    finally:
        feeder.join(timeout=5)
        if proc.stdout:
            proc.stdout.close()
        proc.wait(timeout=30)


def scan_history(
    root: Path,
    baseline: dict[str, BaselineEntry],
    *,
    target: str = "history",
    seen_fingerprints: set[str] | None = None,
    scope: str = "pr",
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """`scope="pr"` (default, matches the in-CI `secret-scanning` job):
    scans every unique blob reachable from HEAD only -- a secret-shaped
    fixture that exists only on another fetched ref (another open PR's
    own unmerged branch) can never redden this one. `scope="all-refs"`:
    every unique blob reachable from any ref, with a HEAD-unreachable
    finding's location naming the branch(es) that actually carry it."""
    if scope not in ("pr", "all-refs"):
        raise ValueError(f"scan_history scope must be 'pr' or 'all-refs', got {scope!r}")
    assert_not_shallow(root)
    seen_fingerprints = seen_fingerprints if seen_fingerprints is not None else set()
    refs = ("HEAD",) if scope == "pr" else ("--all",)
    blob_ids = iter_history_blob_ids(root, refs=refs)
    path_hints = dict(blob_ids)
    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []

    head_blob_ids: set[str] = set()
    stray_blob_ids: set[str] = set()
    if scope == "all-refs":
        head_blob_ids = {sha for sha, _ in iter_history_blob_ids(root, refs=("HEAD",))}
        stray_blob_ids = {sha for sha, _ in blob_ids} - head_blob_ids
    attribution = _attribute_strays_to_refs(root, stray_blob_ids) if stray_blob_ids else {}

    for sha, data, truncated in _iter_history_blob_contents(root, [sha for sha, _ in blob_ids]):
        rel_path = path_hints.get(sha, sha[:12])
        branch_suffix = ""
        if sha in attribution:
            refs_for_sha = attribution[sha]
            branch_suffix = f" (branch: {', '.join(sorted(refs_for_sha)) or 'unknown'})"
        location = f"{rel_path}@{sha[:12]}{branch_suffix}"
        raw_results, _incomplete = _scan_content_and_members(
            location, data, include_canary=False, content_truncated=truncated
        )
        blob_findings, blob_blocking = _record_results(
            raw_results, baseline, target, seen_fingerprints
        )
        findings.extend(blob_findings)
        results.append(
            ScenarioResult(
                resource_kind=target,
                check=next((f.rule for f in blob_findings if f.blocking), "clean"),
                method="SCAN",
                path=location,
                status=1 if blob_blocking else 0,
                expected_denied=True,
                passed=not blob_blocking,
            )
        )
    return findings, results


# ---------------------------------------------------------------------------
# log_samples: (a) a local logger through the real FTF-0904 pipeline over
# EVERY rule family; (b) a real uvicorn boot; (c) a direct StructuredEvent
# cross-check.
# ---------------------------------------------------------------------------


def generate_seed_values() -> dict[str, str]:
    """One runtime-generated, harmless marker per rule family (F-SEEDS --
    nothing here is a fixed literal)."""
    return {rule: generate_seed_for_rule(rule) for rule in RULES}


def scan_log_samples(
    seed_values: Iterable[tuple[str, str]] | None = None, *, target: str = "log_sample"
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Proves this suite is not vacuously passing, and that the real,
    already-certified FTF-0904 pipeline still redacts a seeded
    secret-shaped marker before it reaches a log sink -- now covering
    every rule family in `RULES` (round 2 covered 5 by construction of
    its fixed literal list; round 3's seeds are generated, one per
    family, covering all of them)."""
    if seed_values is None:
        seed_values = list(generate_seed_values().items())
    buffer = io.StringIO()
    logger = logging.getLogger(f"ftf_1505_log_sample_{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    install_redaction(logger)

    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []
    for rule, value in seed_values:
        raw_line = f"request completed token={value} status=ok"
        raw_hits = list(_matches_text(raw_line, include_canary=False, on_incomplete=None))
        detects_shape = any(hit_rule == rule for hit_rule, _ in raw_hits)
        results.append(
            ScenarioResult(
                resource_kind=target,
                check=f"pre_redaction_detects_seeded_shape[{rule}]",
                method="SCAN",
                path=f"<pre-redaction:{rule}>",
                status=0 if detects_shape else 1,
                expected_denied=True,
                passed=detects_shape,
            )
        )

        buffer.seek(0)
        buffer.truncate(0)
        logger.info("request completed token=%s status=ok", value)
        sink_output = buffer.getvalue()
        sink_hits = list(scan_line(sink_output))
        clean = not sink_hits
        for hit_rule, _matched in sink_hits:
            findings.append(
                SecretFinding(
                    rule=hit_rule,
                    target=target,
                    location=f"<post-redaction:{rule}>",
                    value_sha256="",
                    blocking=True,
                )
            )
        results.append(
            ScenarioResult(
                resource_kind=target,
                check=f"post_redaction_clean[{rule}]",
                method="SCAN",
                path=f"<post-redaction:{rule}>",
                status=1 if sink_hits else 0,
                expected_denied=True,
                passed=clean,
            )
        )
    logger.removeHandler(handler)
    return findings, results


def scan_structured_event_cross_check(
    *, target: str = "log_sample"
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """FTF-0006's `StructuredEvent` is content-free BY FIELD SHAPE (every
    field is validated against a short safe-identifier pattern) -- it has
    no redaction of its own. A canary-shaped value happens to satisfy
    that identifier pattern, so this is the one place a caller misusing
    `identity`/`event` could leak one unredacted. This suite's own canary
    detection is the backstop: proven directly against a real
    `emit_to_stream` write, not assumed."""
    marker = generate_canary()
    event = StructuredEvent(
        event="probe.ftf_1505",
        correlation_id=uuid.uuid4().hex,
        identity=marker.value,
        status="ok",
        duration_ms=1.0,
        timestamp_utc="2026-01-01T00:00:00+00:00",
    )
    buffer = io.StringIO()
    emit_to_stream(event, stream=buffer)
    output = buffer.getvalue()
    hits = scan_line(output, include_canary=True)
    detected = any(rule == "canary_leaked" for rule, _ in hits)
    findings: list[SecretFinding] = []
    if not detected:
        findings.append(
            SecretFinding(
                rule="structured_event_cross_check_blind",
                target=target,
                location="<structured-event>",
                value_sha256="",
                blocking=True,
            )
        )
    return findings, [
        ScenarioResult(
            resource_kind=target,
            check="structured_event_canary_detected",
            method="SCAN",
            path="<structured-event>",
            status=0 if detected else 1,
            expected_denied=True,
            passed=detected,
        )
    ]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, proc: subprocess.Popen[bytes], deadline: float) -> bool:
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _live_app_env() -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("SOVRA_")}
    env.update(
        {
            "SOVRA_ENVIRONMENT": "staging",
            "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
            "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
            "SOVRA_PRIVACY_POLICY": "standard",
            "SOVRA_TLS_VERIFY": "true",
            "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
            "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
            "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
            "SOVRA_OIDC_ISSUER": "https://idp.internal.example",
            "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
            "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
            "SOVRA_OIDC_REDIRECT_URI": "https://factory.internal.example/auth/callback",
            "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "https://idp.internal.example/authorize",
            "SOVRA_OIDC_TOKEN_ENDPOINT": "https://idp.internal.example/token",
            "SOVRA_OIDC_JWKS_URI": "https://idp.internal.example/jwks",
            "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
            "SOVRA_OIDC_ALLOWED_ORIGIN": "https://factory.internal.example",
            "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
            "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


_LiveAppProbe = tuple[str, str, dict[str, str], str, bytes | None, int]


def _build_live_app_probes() -> list[_LiveAppProbe]:
    marker = generate_canary()
    encoded = {
        encoding: encode_canary(marker, encoding)
        for encoding in ("literal", "base64", "hex", "url")
    }
    fragments = fragment_canary(marker, 2)
    family_seeds = generate_seed_values()

    probes: list[_LiveAppProbe] = [
        (
            "query_literal",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={encoded['literal']}",
            {},
            "GET",
            None,
            401,
        ),
        (
            "query_base64",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={encoded['base64']}",
            {},
            "GET",
            None,
            401,
        ),
        (
            "query_hex",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={encoded['hex']}",
            {},
            "GET",
            None,
            401,
        ),
        (
            "query_url",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={encoded['url']}",
            {},
            "GET",
            None,
            401,
        ),
        (
            "query_fragmented",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={fragments[0]}&token2={fragments[1]}",
            {},
            "GET",
            None,
            401,
        ),
        (
            "header_authorization",
            "/no-such-route-auth",
            {"Authorization": f"Bearer {encoded['literal']}"},
            "GET",
            None,
            404,
        ),
        (
            "header_cookie",
            "/no-such-route-cookie",
            {"Cookie": f"session={encoded['literal']}"},
            "GET",
            None,
            404,
        ),
        (
            "body_json",
            f"/api/v1/organizations/{uuid.uuid4()}/projects",
            {"Content-Type": "application/json"},
            "POST",
            json.dumps({"name": encoded["literal"]}).encode(),
            401,
        ),
        # Round 4 (Control-plane return 2026-10-16, point 2): round 3's own
        # docstring claimed every reachable route requires auth, so a 422/
        # 4xx validation-error path "stays unreached" here. That disclosure
        # was false: `/auth/callback` (apps/api/app/auth/oidc_login.py)
        # has no auth dependency at all (it IS the login flow) and its
        # `code`/`state` are required, un-defaulted function parameters --
        # FastAPI's own request-validation layer answers before the
        # handler body runs.
        (
            "auth_callback_missing_code",
            "/auth/callback?state=x",
            {},
            "GET",
            None,
            422,
        ),
        (
            "auth_callback_invalid_txn",
            "/auth/callback?code=x&state=x",
            {},
            "GET",
            None,
            400,
        ),
    ]
    for rule, seed in family_seeds.items():
        # Same placement convention every other probe above already uses
        # (`?token=`, a key name FTF-0904's own `_SECRET_KEY_WORDS` covers)
        # -- this is a coverage sweep over every rule family's SHAPE, not a
        # search for an unrelated gap in FTF-0904's key-NAME heuristics
        # (that is FTF-0904-F1's own, separately active, out-of-scope lane).
        probes.append(
            (
                f"family_{rule}",
                f"/no-such-route-family?token={quote(seed, safe='')}",
                {},
                "GET",
                None,
                404,
            )
        )
    return probes


def _access_log_evidence_present(captured: str, static_path: str, expected_status: int) -> bool:
    """F-LIVE-POSITIVE-CONTROL (round 4, point 2): a real, non-redacted
    uvicorn access-log line for this exact probe -- its static path (the
    part before any `?query`, which never itself carries a secret value)
    together with the real status code, on the same line. uvicorn's
    default access logger (`get_path_with_query_string`) always logs the
    full request line BEFORE any redaction this process's own logging
    config applies, so this line's static portion is never itself a
    reason to redact -- its absence means nothing reached this stream for
    this request at all (suppressed access logging -- F14's `--no-
    access-log`; stderr discarded before merge -- F15), which a scan
    reporting 'clean' over NOTHING would never catch (Law 1: nothing
    proven until a test can fail)."""
    needle_status = f" {expected_status}"
    for line in captured.splitlines():
        if static_path in line and needle_status in line:
            return True
    return False


def _evaluate_live_app_output(
    *,
    target: str,
    booted: bool,
    captured: str,
    probes: list[_LiveAppProbe],
    statuses: dict[str, int | None],
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Pure (no subprocess/network): turns one live-app boot's captured
    stdout+stderr and per-probe HTTP statuses into findings/results.
    Deliberately separated from the real boot/probe orchestration below
    (`scan_live_app_log_samples`) so this round's positive control and
    the F14/F15/F22 fault rows can be proven directly against a
    fabricated `captured` string -- this sandbox has no `uvicorn`
    available for a real boot, and even where one is, a real boot should
    never be the ONLY way to exercise this evaluation logic."""
    results: list[ScenarioResult] = [
        ScenarioResult(
            resource_kind=target,
            check="live_app_boot",
            method="GET",
            path="<startup>",
            status=0 if booted else 1,
            expected_denied=True,
            passed=booted,
        )
    ]
    findings: list[SecretFinding] = []
    if not booted:
        return findings, results

    for name, path, _headers, _method, _body, expected in probes:
        got = statuses.get(name)
        ok = got == expected
        results.append(
            ScenarioResult(
                resource_kind=target,
                check=f"live_app_status_{name}",
                method="GET",
                path=name,
                status=0 if ok else 1,
                expected_denied=True,
                passed=ok,
            )
        )
        static_path = path.split("?", 1)[0]
        logged = _access_log_evidence_present(captured, static_path, expected)
        results.append(
            ScenarioResult(
                resource_kind=target,
                check=f"live_app_access_logged_{name}",
                method="GET",
                path=name,
                status=0 if logged else 1,
                expected_denied=True,
                passed=logged,
            )
        )

    hits = scan_line(captured, include_canary=True)
    clean = not hits
    for rule, _value in hits:
        findings.append(
            SecretFinding(
                rule=rule,
                target=target,
                location="<live-app-output>",
                value_sha256="",
                blocking=True,
            )
        )
    results.append(
        ScenarioResult(
            resource_kind=target,
            check="live_app_output_clean",
            method="SCAN",
            path="<live-app-output>",
            status=1 if hits else 0,
            expected_denied=True,
            passed=clean,
        )
    )
    return findings, results


def _capture_subprocess_output(
    cmd: list[str], *, cwd: Path, env: dict[str, str], out_path: Path
) -> subprocess.Popen[bytes]:
    """F15 fixed: stdout AND stderr are both routed into `out_path` (never
    `subprocess.DEVNULL` for either) -- a secret printed only to stderr
    (an uncaught traceback, a logging call misconfigured to write there)
    must still reach the scanned output, never silently discarded. The
    file descriptor is duplicated into the child at spawn time, so
    closing this process's own handle right after `Popen` returns is
    safe -- the child keeps writing to it regardless."""
    out = out_path.open("wb")
    try:
        return subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT)
    finally:
        out.close()


def scan_live_app_log_samples(
    root: Path, *, target: str = "log_sample"
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Boots the real `uvicorn app.main:create_app --factory` and probes
    it with every rule family's seed (behind the same `?token=` key name
    the canary probes already use -- a coverage sweep over this suite's
    own 16 shapes, not a search for FTF-0904-F1's own, separately-scoped,
    key-NAME-heuristic gaps), FTF-0905 canaries (literal, base64, hex,
    url, fragmented) across the query string, a header, a cookie and a
    JSON request body, and (round 4) `/auth/callback`'s own reachable,
    unauthenticated 422/400 validation paths. Scans the process's
    captured stdout+stderr (`_capture_subprocess_output`, F15) and proves
    a real, non-redacted access-log line exists for every probe
    (`_evaluate_live_app_output`'s own positive control, point 2) --
    never just that nothing bad turned up."""
    probes = _build_live_app_probes()
    port = _free_port()

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "out.log"
        proc = _capture_subprocess_output(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--app-dir",
                str(root / "apps" / "api"),
            ],
            cwd=root,
            env=_live_app_env(),
            out_path=out_path,
        )
        try:
            booted = _wait_for_port(port, proc, time.monotonic() + 60)
            statuses: dict[str, int | None] = {}
            if booted:
                for name, path, headers, method, body, _expected in probes:
                    try:
                        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                        conn.request(method, path, body=body, headers=headers)
                        resp = conn.getresponse()
                        resp.read()
                        statuses[name] = resp.status
                        conn.close()
                    except (OSError, http.client.HTTPException):
                        statuses[name] = None
                time.sleep(0.5)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        captured = out_path.read_text(encoding="utf-8", errors="replace")

    return _evaluate_live_app_output(
        target=target, booted=booted, captured=captured, probes=probes, statuses=statuses
    )


def evaluate(results: list[ScenarioResult]) -> TenantIsolationEvidence:
    try:
        return build_evidence(results)
    except TenantIsolationSuiteError as exc:
        raise SecretScanError(str(exc)) from exc


_ScanLane = tuple[str, Callable[[], tuple[list[SecretFinding], list[ScenarioResult]]]]
_MainGuard = tuple[str, Callable[[], None]]


def _build_pre_scan_guards(root: Path) -> list[_MainGuard]:
    """Every completeness/coverage guard `main()` runs BEFORE scanning, as
    (name, zero-arg callable) pairs -- round 4 fix (F19/F20, Control-plane
    return 2026-10-16, point 1): round 3's own proof that `main()` still
    calls these was `"assert_dirs_covered(" in inspect.getsource(main)`,
    a textual grep that a copy-pasted call with the SAME name but
    different (wrong) arguments would still satisfy. A real list of real,
    already-bound callables lets a test invoke each one directly and
    confirm it reaches the real, underlying guard function -- and lets a
    dropped entry be reproduced as a DATA fault, not a source-text one."""
    return [
        ("dirs_covered", lambda: assert_dirs_covered(root, SOURCE_ROOTS)),
        (
            "artifact_uploads_covered",
            lambda: assert_artifact_uploads_covered(
                CI_WORKFLOW_PATH.read_text(encoding="utf-8"), ARTIFACT_ROOTS
            ),
        ),
    ]


def _build_scan_lanes(
    root: Path,
    baseline: dict[str, BaselineEntry],
    seen_fingerprints: set[str],
    scanned_paths: set[str],
    scope: str,
) -> list[_ScanLane]:
    """Every scan lane `main()` runs, as (name, zero-arg callable) pairs
    returning `(findings, results)` -- round 4 fix (F16/F17/F20b, same
    return as `_build_pre_scan_guards` above): `main()` iterates this list
    uniformly, so a lane silently dropped from orchestration (a forgotten
    `.extend()` call, the old shape six near-identical copy-pasted blocks
    invites) is now a list a test can inspect, filter and re-run, never a
    gap only a source-text grep of `main` could have caught. The
    `scanned_set_matches` guard is bound into the SOURCE lane's own
    closure (not a separate list entry) because it is meaningless without
    that exact scan having just populated `scanned_paths` -- dropping the
    source lane drops its own guard with it, which is the correct,
    coupled behaviour (F20b)."""

    def _source_lane() -> tuple[list[SecretFinding], list[ScenarioResult]]:
        findings, results = scan_source(
            root, baseline, seen_fingerprints=seen_fingerprints, scanned_paths=scanned_paths
        )
        assert_scanned_set_matches_tracked_files(root, scanned_paths)
        return findings, results

    return [
        ("source", _source_lane),
        (
            "artifact",
            lambda: scan_artifact_roots(root, baseline, seen_fingerprints=seen_fingerprints),
        ),
        (
            "history",
            lambda: scan_history(root, baseline, seen_fingerprints=seen_fingerprints, scope=scope),
        ),
        ("log_samples", lambda: scan_log_samples()),
        ("structured_event", lambda: scan_structured_event_cross_check()),
        ("live_app", lambda: scan_live_app_log_samples(root)),
    ]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["pr", "all-refs"], default="pr")
    args = parser.parse_args(argv)

    baseline = load_baseline()
    for _name, guard in _build_pre_scan_guards(ROOT):
        guard()

    all_findings: list[SecretFinding] = []
    all_results: list[ScenarioResult] = []
    seen_fingerprints: set[str] = set()
    scanned_paths: set[str] = set()

    for _name, lane in _build_scan_lanes(
        ROOT, baseline, seen_fingerprints, scanned_paths, args.scope
    ):
        findings, results = lane()
        all_findings.extend(findings)
        all_results.extend(results)

    evidence = evaluate(all_results)
    print(
        json.dumps(
            {
                "evidence": evidence.to_dict(),
                "findings": [f.to_dict() for f in all_findings],
            },
            indent=2,
        )
    )

    assert_baseline_fresh(baseline, seen_fingerprints)

    blocking = [f for f in all_findings if f.blocking]
    if blocking:
        print(f"SECRET SCAN: {len(blocking)} blocking finding(s), cannot pass:", file=sys.stderr)
        for finding in blocking:
            print(f"  [{finding.target}] {finding.rule} at {finding.location}", file=sys.stderr)
        raise SecretScanError(
            f"{len(blocking)} secret-shaped finding(s) with no valid baseline entry -- "
            "cannot be silently ignored (negative_acceptance)"
        )
    failed_scenarios = evidence.scenarios_failed
    if failed_scenarios:
        raise SecretScanError(
            f"{failed_scenarios} scan scenario(s) failed -- see ScenarioResult evidence above"
        )
    print(
        f"SECRET SCAN: {evidence.scenarios_total} unit(s) scanned, "
        f"{len(all_findings)} finding(s), 0 blocking -- clean (scope={args.scope})"
    )


if __name__ == "__main__":
    try:
        main()
    except SecretScanError as exc:
        print(f"FTF-1505 secret scan FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
