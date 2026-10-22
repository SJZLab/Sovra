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
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import IO
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
from secret_rules import matches_in_unit as _matches_text  # noqa: E402
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

# MAX_SCAN_BYTES / quick_gate_hints_for_rule are imported above for reuse by
# tests/tasks/test_ftf_1505.py, not re-exported as public API in __all__.


# ---------------------------------------------------------------------------
# Baseline: entries keyed by sha256(rule NUL value) -- never the raw
# value, a file path or a glob. A reviewed fixture is waived everywhere it
# recurs, never scoped to one location.
# ---------------------------------------------------------------------------


_BASELINE_ENTRY_KEYS = frozenset({"fingerprint", "reason", "reviewed_by", "reviewed_on"})


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, BaselineEntry]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_entries = data.get("entries") or []
    entries: dict[str, BaselineEntry] = {}
    if set(data) - {"entries"}:
        raise SecretScanError(f"secret-scan baseline has unknown top-level keys: {sorted(data)}")
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise SecretScanError(f"malformed secret-scan baseline entry: {raw!r}")
        unknown = set(raw) - _BASELINE_ENTRY_KEYS
        if unknown - set(_FORBIDDEN_BASELINE_KEYS):
            raise SecretScanError(
                f"secret-scan baseline entry has unknown keys {sorted(unknown)} -- only "
                f"{sorted(_BASELINE_ENTRY_KEYS)} are allowed (strict schema)"
            )
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


UNREADABLE_ARCHIVE = "<unreadable archive>"
_ZIP_SUFFIXES = (".zip", ".whl", ".jar", ".egg")
_TAR_SUFFIXES = (".tar.gz", ".tgz", ".tar")


def _read_bounded(handle: IO[bytes]) -> tuple[bytes, bool]:
    data = handle.read(MAX_SCAN_BYTES + 1)
    return data[:MAX_SCAN_BYTES], len(data) > MAX_SCAN_BYTES


def _iter_archive_members(data: bytes, name: str) -> Iterator[tuple[str, bytes | None, bool]]:
    """Bomb-safe bounded extraction of zip/whl/jar/egg and tar/tar.gz
    members, plus a bare .gz: `(member_name, content, truncated)`. `name`
    is the file's real path (never a history location label, whose `@sha`
    suffix would hide the extension). Declared member count/uncompressed
    size is checked BEFORE any member is read.

    Fail closed, never silent (round 4, F-ARCHIVE-ZLIB / F-ARCHIVE-CORRUPT /
    OVERSIZE): a member that cannot be read -- for ANY reason, `zlib.error`
    from a corrupt deflate stream included -- yields `(name, None, False)`
    and extraction continues with the next member; an archive that cannot
    be opened at all (truncated, corrupt central directory, EOF mid-stream)
    yields `(UNREADABLE_ARCHIVE, None, False)`; a member whose content is
    longer than `MAX_SCAN_BYTES` yields its first `MAX_SCAN_BYTES` with
    `truncated=True`. The caller turns each of these into a blocking
    `scan_incomplete` finding."""
    lower = name.lower()
    if lower.endswith(_ZIP_SUFFIXES) or _looks_like_zip(data):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            infos = [i for i in zf.infolist() if not i.is_dir()]
        except Exception:
            yield UNREADABLE_ARCHIVE, None, False
            return
        with zf:
            if len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise SecretScanError(f"archive {name!r} exceeds member cap: {len(infos)}")
            if sum(i.file_size for i in infos) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise SecretScanError(f"archive {name!r} exceeds uncompressed-size cap")
            for info in infos:
                try:
                    with zf.open(info) as handle:
                        content, truncated = _read_bounded(handle)
                except Exception:
                    yield info.filename, None, False
                    continue
                yield info.filename, content, truncated
        return
    if lower.endswith(_TAR_SUFFIXES):
        tar_bytes = data
        if lower.endswith((".tar.gz", ".tgz")):
            # FTF-1505-F1 (GZIP-INTEGRITY): tarfile's "r:gz" never checks the gzip CRC, so a
            # corrupted trailer or a bit flip mid-stream was scanned as if intact. Decompress the
            # whole stream first (bounded by the uncompressed-size cap): GzipFile verifies the CRC
            # and length at end of stream, so any corruption makes the archive unreadable.
            try:
                tar_bytes = _bounded_gzip_decompress(data, _MAX_ARCHIVE_UNCOMPRESSED_BYTES + 1)
            except Exception:
                yield UNREADABLE_ARCHIVE, None, False
                return
            if len(tar_bytes) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise SecretScanError(f"archive {name!r} exceeds uncompressed-size cap")
        try:
            tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
            members = [m for m in tf.getmembers() if m.isfile()]
        except Exception:
            yield UNREADABLE_ARCHIVE, None, False
            return
        with tf:
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise SecretScanError(f"archive {name!r} exceeds member cap: {len(members)}")
            if sum(m.size for m in members) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise SecretScanError(f"archive {name!r} exceeds uncompressed-size cap")
            for member in members:
                try:
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        raise tarfile.ExtractError(f"{member.name}: no file object")
                    content, truncated = _read_bounded(extracted)
                except Exception:
                    yield member.name, None, False
                    continue
                yield member.name, content, truncated
        return
    if lower.endswith(".gz"):
        member_name = Path(name).name[:-3]
        if not _looks_like_gzip(data):
            yield UNREADABLE_ARCHIVE, None, False
            return
        try:
            content = _bounded_gzip_decompress(data, MAX_SCAN_BYTES + 1)
        except Exception:
            yield member_name, None, False
            return
        yield member_name, content[:MAX_SCAN_BYTES], len(content) > MAX_SCAN_BYTES


def _scan_unit_text(data: bytes, *, include_canary: bool) -> list[tuple[str, str, str]]:
    """(rule, value, location_suffix) tuples for one file/blob/member's
    content. Text content is scanned per line (":<lineno>" suffix), each
    line whole however long; content that is not valid UTF-8 is scanned
    whole, as latin-1 (a 1:1 byte mapping that never raises, so a binary
    file's ASCII-shaped secrets are still found) with no line suffix."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("latin-1")
        return [
            (rule, value, "")
            for rule, value in _matches_text(content, include_canary=include_canary)
        ]
    results = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, value in _matches_text(line, include_canary=include_canary):
            results.append((rule, value, f":{lineno}"))
    return results


def _scan_content_and_members(
    location_prefix: str,
    data: bytes,
    *,
    include_canary: bool,
    content_truncated: bool,
    archive_name: str | None = None,
) -> list[tuple[str, str, str]]:
    """`(rule, value, location)` tuples for `data` itself plus any archive
    members inside it, plus a `scan_incomplete` entry for every part that
    could not be scanned in full. `archive_name` is the real path used to
    recognise an archive by extension (defaults to `location_prefix`)."""
    all_results: list[tuple[str, str, str]] = []
    for rule, value, loc_suffix in _scan_unit_text(data, include_canary=include_canary):
        all_results.append((rule, value, f"{location_prefix}{loc_suffix}"))
    archive_path = archive_name if archive_name is not None else location_prefix
    for member_name, member_data, member_truncated in _iter_archive_members(data, archive_path):
        if member_data is None:
            if member_name == UNREADABLE_ARCHIVE:
                label = f"{location_prefix} (unreadable archive)"
            else:
                label = f"{location_prefix}!{member_name} (unreadable member)"
            all_results.append(("scan_incomplete", "", label))
            continue
        for rule, value, loc_suffix in _scan_unit_text(member_data, include_canary=include_canary):
            all_results.append((rule, value, f"{location_prefix}!{member_name}{loc_suffix}"))
        if member_truncated:
            all_results.append(
                (
                    "scan_incomplete",
                    "",
                    f"{location_prefix}!{member_name} (member exceeds scan bound)",
                )
            )
    if content_truncated:
        all_results.append(
            ("scan_incomplete", "", f"{location_prefix} (content exceeds scan bound)")
        )
    return all_results


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
        raw_results = _scan_content_and_members(
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
            raw_results = _scan_content_and_members(
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
        raw_results = _scan_content_and_members(
            location,
            data,
            include_canary=False,
            content_truncated=truncated,
            archive_name=rel_path,
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
    secret-shaped marker before it reaches a log sink -- every rule family
    in `RULES`, from runtime-generated seeds.

    Per seed, three checks, all through the same detector:
      * `pre_redaction_detects_seeded_shape[rule]` -- the raw log line fires
        the seed's own rule (a seed or rule that stopped matching is a
        recall miss, never a vacuous pass);
      * `control_sink_detects_seed[rule]` -- the same record written to an
        UNREDACTED control sink IS found when that sink is scanned
        (positive control: proves the sink scan itself can fail). The record
        is built with the `LogRecord` constructor and handed straight to the
        control handler: `install_redaction` also scrubs every record at
        ingress (the record factory), so a record made by a logger would
        already be redacted;
      * `post_redaction_clean[rule]` -- the record written through the real
        FTF-0904 pipeline leaves nothing a rule fires on.
    Both sinks are scanned by the same single `scan_line` call, so the scan
    cannot be disabled for one without the other's check reddening."""
    if seed_values is None:
        seed_values = list(generate_seed_values().items())

    def _sink(redacted: bool) -> tuple[logging.Logger, io.StringIO, logging.Handler]:
        buffer = io.StringIO()
        logger = logging.getLogger(
            f"ftf_1505_log_sample_{'redacted' if redacted else 'control'}_{uuid.uuid4().hex}"
        )
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = logging.StreamHandler(buffer)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        if redacted:
            install_redaction(logger)
        return logger, buffer, handler

    sinks = (("control", *_sink(False)), ("redacted", *_sink(True)))

    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []
    for rule, value in seed_values:
        raw_line = f"request completed token={value} status=ok"
        raw_hits = list(_matches_text(raw_line, include_canary=False))
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

        for sink_name, logger, buffer, handler in sinks:
            buffer.seek(0)
            buffer.truncate(0)
            if sink_name == "control":
                handler.handle(
                    logging.LogRecord(
                        logger.name,
                        logging.INFO,
                        __file__,
                        0,
                        "request completed token=%s status=ok",
                        (value,),
                        None,
                    )
                )
            else:
                logger.info("request completed token=%s status=ok", value)
            sink_output = buffer.getvalue()
            sink_hits = list(scan_line(sink_output))
            if sink_name == "control":
                control_found = any(hit_rule == rule for hit_rule, _ in sink_hits)
                results.append(
                    ScenarioResult(
                        resource_kind=target,
                        check=f"control_sink_detects_seed[{rule}]",
                        method="SCAN",
                        path=f"<control-sink:{rule}>",
                        status=0 if control_found else 1,
                        expected_denied=True,
                        passed=control_found,
                    )
                )
                continue
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
                    passed=not sink_hits,
                )
            )
    for _sink_name, logger, _buffer, handler in sinks:
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


def _live_probes(
    marker_values: dict[str, str], fragments: Sequence[str], family_seeds: dict[str, str]
) -> list[tuple[str, str, dict[str, str], str, bytes | None, int]]:
    """Every probe is tagged in its PATH (`/<name>` suffix or a `probe=` query
    parameter, neither of which the redactor touches) so its own access-log
    line can be found afterwards (`live_app_access_line[...]`)."""
    org = uuid.uuid4
    probes: list[tuple[str, str, dict[str, str], str, bytes | None, int]] = [
        (
            f"query_{encoding}",
            f"/api/v1/organizations/{org()}/projects?token={value}&probe=query_{encoding}",
            {},
            "GET",
            None,
            401,
        )
        for encoding, value in marker_values.items()
    ]
    probes += [
        (
            "query_fragmented",
            f"/api/v1/organizations/{org()}/projects?token={fragments[0]}&token2={fragments[1]}"
            "&probe=query_fragmented",
            {},
            "GET",
            None,
            401,
        ),
        (
            "header_authorization",
            "/no-such-route/header_authorization",
            {"Authorization": f"Bearer {marker_values['literal']}"},
            "GET",
            None,
            404,
        ),
        (
            "header_cookie",
            "/no-such-route/header_cookie",
            {"Cookie": f"session={marker_values['literal']}"},
            "GET",
            None,
            404,
        ),
        (
            "body_json",
            f"/api/v1/organizations/{org()}/projects?probe=body_json",
            {"Content-Type": "application/json"},
            "POST",
            json.dumps({"name": marker_values["literal"]}).encode(),
            401,
        ),
        # Unauthenticated error paths (round 4, F-LIVE-POSITIVE-CONTROL: the
        # round-3 disclosure that none was reachable was false). The OIDC
        # callback validates its query before any auth: no `code` -> 422 (a
        # request-validation error), `code` + `state` that match no login -> 400.
        (
            "callback_validation_422",
            f"/auth/callback?state={marker_values['literal']}&probe=callback_validation_422",
            {},
            "GET",
            None,
            422,
        ),
        (
            "callback_rejected_400",
            f"/auth/callback?code={marker_values['literal']}&state={marker_values['base64']}"
            "&probe=callback_rejected_400",
            {},
            "GET",
            None,
            400,
        ),
    ]
    for rule, seed in family_seeds.items():
        # Same placement convention the probes above use (`?token=`, a key name
        # FTF-0904's own `_SECRET_KEY_WORDS` covers): a coverage sweep over every
        # rule family's SHAPE, not a search for gaps in FTF-0904's key-NAME
        # heuristics (that is FTF-0904-F1's own lane).
        probes.append(
            (
                f"family_{rule}",
                f"/no-such-route/family_{rule}?token={quote(seed, safe='')}",
                {},
                "GET",
                None,
                404,
            )
        )
        probes.append(
            (
                f"error_family_{rule}",
                f"/auth/callback?code={quote(seed, safe='')}&state=x&probe=error_family_{rule}",
                {},
                "GET",
                None,
                400,
            )
        )
    return probes


_STARTUP_LINE = "Application startup complete."


def _access_line_seen(probe_name: str, access_lines: list[str]) -> bool:
    """`probe_name` appears in some access-log line as a whole tag (not as part
    of a longer tag: `family_x` must not be satisfied by `error_family_x`)."""
    tag = re.compile(rf"(?<![\w-]){re.escape(probe_name)}(?![\w-])")
    return any(tag.search(line) for line in access_lines)


def _live_output_leaks(
    hits: list[tuple[str, str]], positive_control_value: str
) -> list[tuple[str, str]]:
    """Every hit in the live output except the positive-control canary's own
    exact value -- the one thing the lane put there on purpose."""
    return [(rule, value) for rule, value in hits if value != positive_control_value]


def _live_decisions(
    captured: str,
    booted: bool,
    statuses: dict[str, int | None],
    probes: list[tuple[str, str, dict[str, str], str, bytes | None, int]],
    positive_control_value: str,
    *,
    target: str = "log_sample",
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Every decision the live lane makes, on what the app run produced: the
    captured stdout+stderr text, whether it booted, and each probe's status.
    Kept apart from the boot so a synthetic capture can prove every check can
    fail (round 6): no startup line, a wrong status, a missing access line, no
    positive control, or any other hit each turn a result red."""
    results: list[ScenarioResult] = []
    findings: list[SecretFinding] = []

    def _result(check: str, path: str, passed: bool, method: str = "SCAN") -> None:
        results.append(
            ScenarioResult(
                resource_kind=target,
                check=check,
                method=method,
                path=path,
                status=0 if passed else 1,
                expected_denied=True,
                passed=passed,
            )
        )

    _result("live_app_boot", "<startup>", booted, method="GET")
    if not booted:
        return findings, results

    _result("live_app_stderr_captured", "<stderr>", _STARTUP_LINE in captured)
    access_lines = [line for line in captured.splitlines() if "HTTP/1.1" in line]
    for name, _path, _headers, method, _body, expected in probes:
        got = statuses.get(name)
        _result(f"live_app_status_{name}", name, got == expected, method=method)
        _result(
            f"live_app_access_line[{name}]",
            name,
            _access_line_seen(name, access_lines),
            method=method,
        )

    hits = scan_line(captured, include_canary=True)
    control_found = ("canary_leaked", positive_control_value) in hits
    leaks = _live_output_leaks(hits, positive_control_value)
    _result("live_app_scan_detects_positive_control", "<positive-control>", control_found)
    for rule, _value in leaks:
        findings.append(
            SecretFinding(
                rule=rule,
                target=target,
                location="<live-app-output>",
                value_sha256="",
                blocking=True,
            )
        )
    _result("live_app_output_clean", "<live-app-output>", not leaks)
    return findings, results


def scan_live_app_log_samples(
    root: Path, *, target: str = "log_sample"
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Boots the real `uvicorn app.main:create_app --factory` and probes it
    with every rule family's seed and FTF-0905 canaries (literal, base64,
    hex, url, fragmented) across the query string, a header, a cookie and a
    JSON request body, against 401/404 and the two unauthenticated error
    paths of the OIDC callback (422 validation error, 400 rejection).
    Scans the process's captured stdout AND stderr.

    Positive controls (round 4): a lane that sees nothing must fail, not
    pass --
      * `live_app_stderr_captured` -- uvicorn's own startup line (logged by
        `uvicorn.error` to stderr) is in the captured output;
      * `live_app_access_line[<probe>]` -- every probe's own tag appears, so
        each request really produced a (redacted) access-log line;
      * `live_app_scan_detects_positive_control` -- one fresh canary placed in
        a URL PATH (which no redactor rewrites, so it is printed verbatim) is
        found by the same scan of the captured output that must find nothing
        else; only that exact value is exempt from being a blocking leak."""
    port = _free_port()
    marker = generate_canary()
    marker_values = {
        encoding: encode_canary(marker, encoding)
        for encoding in ("literal", "base64", "hex", "url")
    }
    positive_control = generate_canary()
    probes = _live_probes(marker_values, fragment_canary(marker, 2), generate_seed_values())
    probes.append(
        (
            "positive_control",
            f"/no-such-route/positive_control/{positive_control.value}",
            {},
            "GET",
            None,
            404,
        )
    )

    statuses: dict[str, int | None] = {}
    booted = False

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "out.log"
        with out_path.open("wb") as out:
            proc = subprocess.Popen(
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
                cwd=str(root),
                env=_live_app_env(),
                stdout=out,
                stderr=subprocess.STDOUT,
            )
            try:
                booted = _wait_for_port(port, proc, time.monotonic() + 60)
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

    return _live_decisions(
        captured, booted, statuses, probes, positive_control.value, target=target
    )


# ---------------------------------------------------------------------------
# seeded recall: every rule family, planted fresh at run time in a disposable
# git repository, must be found by every lane that scans files.
# ---------------------------------------------------------------------------

_RECALL_GIT_ENV = {
    "GIT_AUTHOR_NAME": "ftf-1505-recall",
    "GIT_AUTHOR_EMAIL": "recall@example.invalid",
    "GIT_COMMITTER_NAME": "ftf-1505-recall",
    "GIT_COMMITTER_EMAIL": "recall@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _recall_git(root: Path, *args: str) -> None:
    import os

    env = dict(os.environ)
    env.update(_RECALL_GIT_ENV)
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)


def _zip_bytes(member: str, content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, content)
    return buf.getvalue()


def scan_seeded_recall(*, target: str = "seeded_recall") -> list[ScenarioResult]:
    """F-SEEDS-PER-LANE: one runtime-generated seed per rule family, planted
    in a disposable git repository, in every place a file lane must look:

      * `source`     -- a tracked file at HEAD (`scan_source`);
      * `base64`     -- the same, wrapped in one layer of base64;
      * `zip_member` -- inside a tracked zip archive;
      * `history`    -- committed, then deleted: reachable only through
                        history (`scan_history`, `--scope pr` default);
      * `artifact`   -- an untracked file under an artifact root
                        (`scan_artifact_roots`).

    One `seeded_recall[<lane>][<rule>]` result per pair, failing if that
    lane did not report that rule at that seed's location. (The log-line
    lane's recall is `scan_log_samples`' `pre_redaction_detects_seeded_
    shape` / `control_sink_detects_seed`.) The seeds are never part of the
    real scan's findings."""
    import base64 as _b64
    import shutil

    seeds = generate_seed_values()
    tmp = Path(tempfile.mkdtemp(prefix="ftf1505_recall_"))
    results: list[ScenarioResult] = []
    try:
        _recall_git(tmp, "init", "-q", "-b", "main")
        for rule, seed in seeds.items():
            path = tmp / "history" / f"{rule}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            # distinct content: an identical blob at HEAD would be the same object
            path.write_text(f"history_value = {seed}\n", encoding="utf-8")
        _recall_git(tmp, "add", "-A")
        _recall_git(tmp, "commit", "-q", "-m", "history-only seeds")
        shutil.rmtree(tmp / "history")
        for rule, seed in seeds.items():
            for sub, content in (
                ("source", f"value = {seed}\n"),
                ("base64", _b64.b64encode(f"value = {seed}".encode()).decode() + "\n"),
            ):
                path = tmp / sub / f"{rule}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            zip_path = tmp / "zip_member" / f"{rule}.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            zip_path.write_bytes(_zip_bytes("seed.txt", f"value = {seed}\n"))
            artifact = tmp / ARTIFACT_ROOTS[-1] / f"{rule}.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"value = {seed}\n", encoding="utf-8")
        (tmp / ".gitignore").write_text(f"/{ARTIFACT_ROOTS[-1]}/\n", encoding="utf-8")
        _recall_git(tmp, "add", "-A")
        _recall_git(tmp, "commit", "-q", "-m", "tracked seeds; history seeds deleted")

        source_findings, _ = scan_source(tmp, {})
        history_findings, _ = scan_history(tmp, {}, scope="pr")
        artifact_findings, _ = scan_artifact_roots(tmp, {})
        lanes = (
            ("source", source_findings, "source/{rule}.txt:"),
            ("base64", source_findings, "base64/{rule}.txt:"),
            ("zip_member", source_findings, "zip_member/{rule}.zip!seed.txt:"),
            ("history", history_findings, "history/{rule}.txt@"),
            ("artifact", artifact_findings, ARTIFACT_ROOTS[-1] + "/{rule}.txt:"),
        )
        for lane, lane_findings, location_template in lanes:
            for rule in seeds:
                location = location_template.format(rule=rule)
                found = any(
                    f.rule == rule and f.location.startswith(location) for f in lane_findings
                )
                results.append(
                    ScenarioResult(
                        resource_kind=target,
                        check=f"seeded_recall[{lane}][{rule}]",
                        method="SCAN",
                        path=location,
                        status=0 if found else 1,
                        expected_denied=True,
                        passed=found,
                    )
                )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


def evaluate(results: list[ScenarioResult]) -> TenantIsolationEvidence:
    try:
        return build_evidence(results)
    except TenantIsolationSuiteError as exc:
        raise SecretScanError(str(exc)) from exc


def main(argv: list[str] | None = None) -> None:
    """`--root` / `--baseline` default to this repository and its committed
    baseline (what CI runs); `test_ftf_1505.py` points them at disposable
    repositories to prove every lane's result actually decides the run."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["pr", "all-refs"], default="pr")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args(argv)
    root: Path = args.root

    baseline = load_baseline(args.baseline)
    assert_dirs_covered(root, SOURCE_ROOTS)
    ci_workflow = root / CI_WORKFLOW_PATH.relative_to(ROOT)
    assert_artifact_uploads_covered(ci_workflow.read_text(encoding="utf-8"), ARTIFACT_ROOTS)

    all_findings: list[SecretFinding] = []
    all_results: list[ScenarioResult] = []
    seen_fingerprints: set[str] = set()
    scanned_paths: set[str] = set()

    source_findings, source_results = scan_source(
        root, baseline, seen_fingerprints=seen_fingerprints, scanned_paths=scanned_paths
    )
    all_findings.extend(source_findings)
    all_results.extend(source_results)
    assert_scanned_set_matches_tracked_files(root, scanned_paths)

    artifact_findings, artifact_results = scan_artifact_roots(
        root, baseline, seen_fingerprints=seen_fingerprints
    )
    all_findings.extend(artifact_findings)
    all_results.extend(artifact_results)

    history_findings, history_results = scan_history(
        root, baseline, seen_fingerprints=seen_fingerprints, scope=args.scope
    )
    all_findings.extend(history_findings)
    all_results.extend(history_results)

    log_findings, log_results = scan_log_samples()
    all_findings.extend(log_findings)
    all_results.extend(log_results)

    structured_findings, structured_results = scan_structured_event_cross_check()
    all_findings.extend(structured_findings)
    all_results.extend(structured_results)

    live_findings, live_results = scan_live_app_log_samples(root)
    all_findings.extend(live_findings)
    all_results.extend(live_results)

    all_results.extend(scan_seeded_recall())

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
    failed = [r for r in all_results if not r.passed]
    if failed:
        print(f"SECRET SCAN: {len(failed)} scan scenario(s) failed:", file=sys.stderr)
        for result in failed[:50]:
            print(f"  [{result.resource_kind}] {result.check} at {result.path}", file=sys.stderr)
        raise SecretScanError(
            f"{len(failed)} scan scenario(s) failed -- see ScenarioResult evidence above"
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
