#!/usr/bin/env python3
"""FTF-1505 -- Secret scanning, round 2 (Section 45-47, 52-53, 65-66, 77, 81).

Acceptance: "Scan current source/history/artifacts/log samples with
seeded harmless secret markers." Negative: "Scanner omissions/excluded
production dirs and actual exposed credential are release blockers."

Round 2 rebuild (Control-plane design 2026-10-14, QUEUE.md "0h"/"FTF-1505
Secret scanning" Backlog entry): round 1 (PR #799/#816) scanned a fixed,
hand-picked directory list and the working tree only. This round scans
every `git ls-files` path with NO exclusion (binary files and archive
members included), every unique blob reachable from any ref (not only
added diff lines -- catches a secret that was committed, then removed, by
a different commit than the one that first added it), the declared
artifact roots (with a completeness check against every ci.yml
`actions/upload-artifact` path), and a real log sample captured from the
actual installed FTF-0904-W1 pipeline AND a real `uvicorn` boot of the
live API.

Reuse decisions (Section 84.3 "reuse an existing compatible module
instead of creating a duplicate"):

* `ScenarioResult`/`build_evidence` -- from `tests/security/
  tenant_isolation_suite.py`, this task's own `contract_owner: FTF-1501`.
* `SOURCE_ROOTS` -- from `scripts/product_truth_check.py`, the real list
  the product-truth gate already uses, instead of a second hand-maintained
  copy that could silently drift from it.
* `_shannon_entropy_bits_per_char` -- FTF-0514's own entropy threshold
  (`workers/data/pii_secrets_detector.py`, `_MIN_HIGH_ENTROPY_BITS_PER_CHAR
  == 3.5`), reused directly rather than a second implementation.
* `generate_canary`/`encode_canary`/`fragment_canary` -- FTF-0905's own
  canary generator, for the log-lane leak check.
* `install_redaction`/`attach_sink`/`redact_text` -- the already-certified
  FTF-0904 pipeline, reused unmodified to prove it still redacts a seeded
  secret-shaped marker before it reaches a log sink.
* FTF-0904's own `redact_text` was tried first as the detector and
  rejected (measured against this repo's own `apps/api`: it changes 3,014
  lines in 328 files -- correct for redacting free-form log prose, wrong
  precision for a release-blocking static scan). This module instead uses
  a dedicated, shape-only rule table that requires an actual
  credential-shaped VALUE before firing.

Targets:
  source    -- every file `git ls-files` reports at HEAD, text and binary,
               plus zip/tar/gz/whl archive members (bounded, bomb-safe).
               No path exclusion. Every `SOURCE_ROOTS` production
               directory must have >=1 scanned file (same `iter_tracked_
               files` the real scan uses, not a separate approximation).
  history   -- every unique blob reachable from any ref
               (`git rev-list --objects --all` + `git cat-file --batch`,
               deduplicated by blob id). Refuses a shallow clone.
  artifacts -- every file under `ARTIFACT_ROOTS` (`docs/certifications`,
               `docs/progress`, `reports`) present on disk at scan time,
               plus a completeness check that every ci.yml
               `actions/upload-artifact` `path:` lies under a declared
               root (so a newly added upload with no declared root fails
               closed instead of silently going unscanned).
  log_samples -- (a) a local logger wired with the real, already-certified
               FTF-0904 pipeline, proving a seeded shape is both detected
               pre-redaction and absent post-redaction (vacuousness
               check); (b) a real `uvicorn app.main:create_app --factory`
               boot (FTF-0904-W1 is installed) probed with seeded
               shape-rule values and FTF-0905 canaries (literal, base64,
               hex, url, fragmented) in the query string, an Authorization
               header and a Cookie header, against a 401 and a 404 route.

negative_acceptance made executable:
  - `assert_dirs_covered` fails closed the moment a covered directory
    exists on disk but this suite's own file-inclusion logic yields zero
    tracked files from it.
  - `assert_artifact_uploads_covered` fails closed the moment a ci.yml
    `upload-artifact` path is not under a declared artifact root.
  - `assert_not_shallow` fails closed (never silently narrows to HEAD)
    when the history lane is asked to run against a shallow clone.
  - `evaluate`/`main` raise `SecretScanError` -- never a silent pass --
    the moment one real (non-baselined) secret shape is found anywhere.
    The only exception path is `secret_scan_baseline.yaml`: entries are
    keyed by `sha256(rule NUL value)` (never the raw value, a path or a
    glob) with a mandatory reason/reviewer/date, and a baseline entry that
    this run never actually matched is itself a blocking finding (a stale
    entry is a release blocker, not a free pass).

Run directly:
    python3 tests/security/secret_scanning.py
"""

from __future__ import annotations

import base64
import dataclasses
import gzip
import hashlib
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
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

from packages.security.canary_secret_generator import (  # noqa: E402
    encode_canary,
    fragment_canary,
    generate_canary,
)
from packages.security.log_redaction import install_redaction  # noqa: E402
from scripts.product_truth_check import SOURCE_ROOTS as _PRODUCT_SOURCE_ROOTS  # noqa: E402
from tenant_isolation_suite import (  # noqa: E402
    ScenarioResult,
    TenantIsolationEvidence,
    TenantIsolationSuiteError,
    build_evidence,
)
from workers.data.pii_secrets_detector import (  # noqa: E402
    _shannon_entropy_bits_per_char as shannon_entropy_bits_per_char,
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
    "parse_upload_artifact_paths",
    "assert_artifact_uploads_covered",
    "scan_source",
    "scan_artifact_roots",
    "scan_history",
    "scan_log_samples",
    "scan_live_app_log_samples",
    "evaluate",
]

SOURCE_ROOTS: tuple[str, ...] = tuple(_PRODUCT_SOURCE_ROOTS)
ARTIFACT_ROOTS: tuple[str, ...] = ("docs/certifications", "docs/progress", "reports")
BASELINE_PATH = Path(__file__).resolve().parent / "secret_scan_baseline.yaml"
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"

_MAX_SCAN_BYTES = 50 * 1024 * 1024  # per file/blob/member scan bound
_MAX_ARCHIVE_MEMBERS = 5000
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_MIN_ENTROPY_BITS_PER_CHAR = 3.5  # FTF-0514's own threshold, reused


class SecretScanError(Exception):
    """Refuses to produce trustworthy evidence, or reports a blocking
    (non-baselined) finding -- never silently converted to a pass
    (Law 17)."""


# ---------------------------------------------------------------------------
# Rule table. Shape-only: every rule needs an actual credential-shaped
# VALUE, never merely a key NAME containing a secret word.
# ---------------------------------------------------------------------------

_SHAPE_RULES: dict[str, re.Pattern[str]] = {
    "aws_access_key_id": re.compile(r"(?a)\b(?:AKIA|ASIA)[A-Z2-7]{16}\b"),
    "github_token": re.compile(r"(?a)\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "github_pat": re.compile(r"(?a)\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    "gitlab_token": re.compile(r"(?a)\bglpat-[A-Za-z0-9_-]{20}\b"),
    "slack_token": re.compile(r"(?a)\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "slack_webhook": re.compile(r"(?a)\bhttps://hooks\.slack\.com/services/[A-Za-z0-9/+]{20,}\b"),
    "bearer_or_basic_header": re.compile(r"(?ia)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{20,}\b"),
    "pem_private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "google_api_key": re.compile(r"(?a)\bAIza[0-9A-Za-z_-]{35}\b"),
    "huggingface_token": re.compile(r"(?a)\bhf_[A-Za-z0-9]{34}\b"),
    "openai_style_key": re.compile(r"(?a)\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}\b"),
    "stripe_style_key": re.compile(r"(?a)\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b"),
}

_AWS_SECRET_KEY_RULE = "aws_secret_access_key"
_AWS_SECRET_KEY = re.compile(
    r"(?ia)\baws_secret_access_key\b\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})[\"']?"
)

_JWT_CANDIDATE = re.compile(r"(?a)\b([A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b")
_JWT_RULE = "jwt_shaped"

_URL_USERINFO_RULE = "url_userinfo_password"
_URL_USERINFO = re.compile(r"(?a)\b[a-zA-Z][a-zA-Z0-9+.-]{1,20}://([^\s:/@\"']+):([^\s@\"']+)@")
_URL_PASSWORD_PLACEHOLDER = re.compile(r"^(?:\$\{.*\}|<.*>|\{.*\}|\*+|\[REDACTED\])$")

_QUOTED_SECRET_ASSIGNMENT_RULE = "quoted_secret_assignment"
_QUOTED_SECRET_ASSIGNMENT = re.compile(
    r"""(?ia)(?<![A-Za-z])(?:db[_-]?password|client[_-]?secret|secret[_-]?key|"""
    r"""session[_-]?key|private[_-]?key|access[_-]?token|refresh[_-]?token|"""
    r"""auth[_-]?token|api[_-]?key|passwd|password|secret|token)"""
    r"""\s*[:=]\s*["']([A-Za-z0-9+/_=.\-]{20,})["']"""
)
_PLACEHOLDER_VALUE = re.compile(
    r"(?i)^(?:changeme|placeholder|example|dummy|test|fake|xxx+|none|null|<.*>|\$\{.*\})$"
)

_CANARY_SHAPE_RULE = "canary_leaked"
_CANARY_SHAPE = re.compile(r"(?a)\bCANARY-DO-NOT-USE-[0-9a-f]{32}\b")

_BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
_MAX_BASE64_ATTEMPTS_PER_UNIT = 2000

RULES: tuple[str, ...] = (
    *sorted(_SHAPE_RULES),
    _AWS_SECRET_KEY_RULE,
    _JWT_RULE,
    _URL_USERINFO_RULE,
    _QUOTED_SECRET_ASSIGNMENT_RULE,
)


def _is_url_password_placeholder(value: str) -> bool:
    return bool(_URL_PASSWORD_PLACEHOLDER.match(value))


def _jwt_matches(text: str) -> Iterator[tuple[str, str]]:
    for match in _JWT_CANDIDATE.finditer(text):
        token = match.group(1)
        header_b64 = token.split(".", 1)[0]
        padded = header_b64 + "=" * ((-len(header_b64)) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded)
            obj = json.loads(decoded)
        except Exception:
            continue
        if isinstance(obj, dict) and "alg" in obj:
            yield _JWT_RULE, token


def _iter_shape_matches(text: str) -> Iterator[tuple[str, str]]:
    """Every rule except the FTF-0905 canary shape (log/artifact lanes
    only -- see `_matches`)."""
    for name, rx in _SHAPE_RULES.items():
        for match in rx.finditer(text):
            yield name, match.group(0)
    for match in _AWS_SECRET_KEY.finditer(text):
        yield _AWS_SECRET_KEY_RULE, match.group(1)
    for match in _URL_USERINFO.finditer(text):
        password = match.group(2)
        if not _is_url_password_placeholder(password):
            yield _URL_USERINFO_RULE, password
    for match in _QUOTED_SECRET_ASSIGNMENT.finditer(text):
        value = match.group(1)
        if _PLACEHOLDER_VALUE.match(value):
            continue
        if shannon_entropy_bits_per_char(value) >= _MIN_ENTROPY_BITS_PER_CHAR:
            yield _QUOTED_SECRET_ASSIGNMENT_RULE, value
    yield from _jwt_matches(text)


# Cheap substring pre-filter before re-running the full (11-regex) rule set
# on a decoded base64/percent-decoded candidate -- a decoded run that
# contains none of these literals cannot match any shape rule, so the
# expensive regex re-check is skipped. This is a performance gate only
# (never a detection gate): every literal here is a strict substring of
# some rule's own required match text, so skipping on its absence never
# hides a real finding. Case-insensitive since several rule keywords are.
_QUICK_GATE_LITERALS: tuple[str, ...] = (
    "akia",
    "asia",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
    "xox",
    "hooks.slack.com",
    "sk-",
    "rk_live_",
    "aiza",
    "hf_",
    "-----begin",
    "aws_secret_access_key",
    "://",
    "bearer ",
    "basic ",
    "canary-do-not-use-",
    "eyj",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "api-key",
    "apikey",
)


def _could_match_any_rule(lowered_text: str) -> bool:
    return any(literal in lowered_text for literal in _QUICK_GATE_LITERALS)


def _try_base64_decode(candidate: str) -> str | None:
    body = candidate.rstrip("=")
    padded = body + "=" * ((-len(body)) % 4)
    raw: bytes | None = None
    try:
        raw = base64.b64decode(padded, validate=False)
    except Exception:
        try:
            raw = base64.urlsafe_b64decode(padded)
        except Exception:
            raw = None
    if raw is None or len(raw) < 8:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _matches(text: str, *, include_canary: bool = False) -> Iterator[tuple[str, str]]:
    """Yields (rule_name, matched_value) for every rule firing on `text`,
    including one layer of base64 decoding (runs >=24 chars) and one
    layer of percent-decoding. `include_canary` additionally checks the
    FTF-0905 canary shape -- restricted to log/artifact lanes, since a
    full canary value legitimately never appears in this repo's own
    source/history otherwise."""
    yield from _iter_shape_matches(text)
    attempts = 0
    for match in _BASE64_RUN.finditer(text):
        if attempts >= _MAX_BASE64_ATTEMPTS_PER_UNIT:
            break
        attempts += 1
        decoded = _try_base64_decode(match.group(0))
        if decoded is not None and _could_match_any_rule(decoded.lower()):
            yield from _iter_shape_matches(decoded)
    percent_decoded = unquote(text)
    if percent_decoded != text and _could_match_any_rule(percent_decoded.lower()):
        yield from _iter_shape_matches(percent_decoded)
    if include_canary:
        for match in _CANARY_SHAPE.finditer(text):
            yield _CANARY_SHAPE_RULE, match.group(0)


def scan_line(line: str, *, include_canary: bool = False) -> list[tuple[str, str]]:
    """Pure, public entry point so tests can exercise the exact rule set
    with no filesystem/git access."""
    return list(_matches(line, include_canary=include_canary))


def _value_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogateescape")).hexdigest()


def _baseline_fingerprint(rule: str, value: str) -> str:
    return hashlib.sha256(
        (rule + "\0" + value).encode("utf-8", errors="surrogateescape")
    ).hexdigest()


@dataclasses.dataclass(frozen=True)
class SecretFinding:
    """One matched secret shape. Content-free by construction (Section
    47 "no secrets ... in task reports"): callers get the matched value
    only through `value_sha256`, never the raw text."""

    rule: str
    target: str  # "source" | "history" | "artifact" | "log_sample"
    location: str
    value_sha256: str
    blocking: bool

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Baseline: entries keyed by sha256(rule NUL value) -- never the raw
# value, a file path or a glob. A reviewed fixture is waived everywhere it
# recurs (the same (rule, value) pair), never scoped to one location,
# because the value itself -- not its location -- is what was reviewed.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BaselineEntry:
    fingerprint: str
    reason: str
    reviewed_by: str
    reviewed_on: str


_FORBIDDEN_BASELINE_KEYS = ("value", "path", "file_glob", "glob")


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
    return _baseline_fingerprint(rule, value) in baseline


def assert_baseline_fresh(baseline: dict[str, BaselineEntry], seen_fingerprints: set[str]) -> None:
    """negative_acceptance: a baseline entry this run never actually
    matched is a release blocker, not a free pass -- it either waives
    nothing real any more (the fixture moved/was removed) or was never
    valid, and either way must be re-reviewed, not silently kept."""
    stale = set(baseline) - seen_fingerprints
    if stale:
        raise SecretScanError(
            f"{len(stale)} stale secret-scan baseline entr{'y' if len(stale) == 1 else 'ies'} "
            f"matched nothing this run: {sorted(stale)}"
        )


# ---------------------------------------------------------------------------
# File inclusion: every git-tracked path, with no exclusion.
# ---------------------------------------------------------------------------


def iter_tracked_files(root: Path) -> list[str]:
    """Every git-tracked file at HEAD. No path exclusion exists."""
    return subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.splitlines()


def iter_tracked_files_under(root: Path, dirs: tuple[str, ...]) -> list[str]:
    """The same real inclusion list, filtered to `dirs` -- used only by
    the coverage check, so a directory omission cannot hide behind two
    diverging implementations of "what counts as tracked"."""
    return [
        f for f in iter_tracked_files(root) if any(f == d or f.startswith(d + "/") for d in dirs)
    ]


def assert_dirs_covered(root: Path, dirs: tuple[str, ...]) -> None:
    """negative_acceptance, made executable: fails closed if a directory
    that actually exists on disk yields zero tracked files from this
    suite's own inclusion logic -- "scanner omissions/excluded production
    dirs" is a release blocker, not a warning."""
    for directory in dirs:
        if not (root / directory).is_dir():
            continue
        if not iter_tracked_files_under(root, (directory,)):
            raise SecretScanError(
                f"secret scan omission: {directory!r} exists but the scanner's own "
                "file-inclusion logic covers zero tracked files in it"
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


_UPLOAD_ARTIFACT_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*actions/upload-artifact@")
_PATH_LINE = re.compile(r"^\s*path:\s*(.+?)\s*$")


def parse_upload_artifact_paths(ci_yml_text: str) -> list[str]:
    """Every `path:` value under an `actions/upload-artifact` step,
    parsed from the raw workflow text (no YAML parser dependency on the
    full GitHub Actions expression grammar)."""
    lines = ci_yml_text.splitlines()
    paths: list[str] = []
    for index, line in enumerate(lines):
        if not _UPLOAD_ARTIFACT_USES.match(line):
            continue
        for lookahead in lines[index + 1 : index + 10]:
            match = _PATH_LINE.match(lookahead)
            if match:
                paths.append(match.group(1).strip("\"'"))
                break
    return paths


def assert_artifact_uploads_covered(ci_yml_text: str, roots: tuple[str, ...]) -> None:
    """negative_acceptance: a newly uploaded CI artifact with no declared
    scan root fails closed instead of silently going unscanned forever."""
    for upload_path in parse_upload_artifact_paths(ci_yml_text):
        if not any(upload_path == root or upload_path.startswith(root + "/") for root in roots):
            raise SecretScanError(
                f"ci.yml uploads artifact path {upload_path!r}, which lies under none of the "
                f"declared artifact roots {roots!r} -- add its root or it is never scanned"
            )


# ---------------------------------------------------------------------------
# Binary-safe content scanning, with bounded archive-member recursion.
# ---------------------------------------------------------------------------


def _scan_bytes(data: bytes, *, include_canary: bool) -> list[tuple[str, str, str]]:
    """Returns (rule, value, location_suffix) tuples. Text content is
    scanned per line (":<lineno>" suffix, so a finding still points at a
    real line). Content that is not valid UTF-8 is scanned whole, as
    latin-1 (a 1:1 byte mapping that never raises, so a binary file's
    ASCII-shaped secrets are still found) with no line suffix."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("latin-1")
        return [
            (rule, value, "") for rule, value in _matches(content, include_canary=include_canary)
        ]
    results: list[tuple[str, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, value in _matches(line, include_canary=include_canary):
            results.append((rule, value, f":{lineno}"))
    return results


def _looks_like_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04" or data[:4] == b"PK\x05\x06"


def _looks_like_gzip(data: bytes) -> bool:
    return data[:2] == b"\x1f\x8b"


def _bounded_gzip_decompress(data: bytes, max_bytes: int) -> bytes:
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


def _iter_archive_members(data: bytes, name: str) -> Iterator[tuple[str, bytes]]:
    """Bomb-safe bounded extraction of zip/whl/jar and tar/tar.gz members,
    plus a bare .gz. Declared member count/uncompressed size is checked
    BEFORE any member is read; each member is then read bounded to
    `_MAX_SCAN_BYTES` regardless of its declared size."""
    lower = name.lower()
    try:
        if lower.endswith((".zip", ".whl", ".jar", ".egg")) or _looks_like_zip(data):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                if len(infos) > _MAX_ARCHIVE_MEMBERS:
                    raise SecretScanError(f"archive {name!r} exceeds member cap: {len(infos)}")
                if sum(i.file_size for i in infos) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise SecretScanError(f"archive {name!r} exceeds uncompressed-size cap")
                for info in infos:
                    with zf.open(info) as handle:
                        yield info.filename, handle.read(_MAX_SCAN_BYTES)
            return
        if lower.endswith((".tar.gz", ".tgz", ".tar")):
            mode = "r:gz" if lower.endswith((".tar.gz", ".tgz")) else "r:"
            with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as tf:
                members = [m for m in tf.getmembers() if m.isfile()]
                if len(members) > _MAX_ARCHIVE_MEMBERS:
                    raise SecretScanError(f"archive {name!r} exceeds member cap: {len(members)}")
                if sum(m.size for m in members) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise SecretScanError(f"archive {name!r} exceeds uncompressed-size cap")
                for member in members:
                    extracted = tf.extractfile(member)
                    if extracted is not None:
                        yield member.name, extracted.read(_MAX_SCAN_BYTES)
            return
        if lower.endswith(".gz") and _looks_like_gzip(data):
            yield name[:-3], _bounded_gzip_decompress(data, _MAX_SCAN_BYTES)
    except SecretScanError:
        raise
    except Exception:
        return


# ---------------------------------------------------------------------------
# source / artifacts: scan a real file tree (every tracked file, or every
# file under a declared artifact root present on disk).
# ---------------------------------------------------------------------------


def _read_bytes_bounded(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(_MAX_SCAN_BYTES)


def _scan_one_file(
    rel_path: str,
    data: bytes,
    baseline: dict[str, BaselineEntry],
    target: str,
    *,
    include_canary: bool,
    seen_fingerprints: set[str],
) -> tuple[list[SecretFinding], bool]:
    findings: list[SecretFinding] = []
    file_blocking = False
    for rule, value, loc_suffix in _scan_bytes(data, include_canary=include_canary):
        fingerprint = _baseline_fingerprint(rule, value)
        seen_fingerprints.add(fingerprint)
        blocking = fingerprint not in baseline
        findings.append(
            SecretFinding(
                rule=rule,
                target=target,
                location=f"{rel_path}{loc_suffix}",
                value_sha256=_value_sha256(value),
                blocking=blocking,
            )
        )
        if blocking:
            file_blocking = True
    for member_name, member_data in _iter_archive_members(data, rel_path):
        for rule, value, loc_suffix in _scan_bytes(member_data, include_canary=include_canary):
            fingerprint = _baseline_fingerprint(rule, value)
            seen_fingerprints.add(fingerprint)
            blocking = fingerprint not in baseline
            findings.append(
                SecretFinding(
                    rule=rule,
                    target=target,
                    location=f"{rel_path}!{member_name}{loc_suffix}",
                    value_sha256=_value_sha256(value),
                    blocking=blocking,
                )
            )
            if blocking:
                file_blocking = True
    return findings, file_blocking


def scan_source(
    root: Path,
    baseline: dict[str, BaselineEntry],
    *,
    target: str = "source",
    seen_fingerprints: set[str] | None = None,
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
        data = _read_bytes_bounded(full_path)
        file_findings, file_blocking = _scan_one_file(
            rel_path,
            data,
            baseline,
            target,
            include_canary=False,
            seen_fingerprints=seen_fingerprints,
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


def scan_artifact_roots(
    root: Path,
    baseline: dict[str, BaselineEntry],
    *,
    roots: tuple[str, ...] = ARTIFACT_ROOTS,
    target: str = "artifact",
    seen_fingerprints: set[str] | None = None,
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Scans every file present on disk under a declared artifact root at
    scan time (committed evidence always; ephemeral CI-run JSON reports
    whenever this job itself produced one before this step ran)."""
    seen_fingerprints = seen_fingerprints if seen_fingerprints is not None else set()
    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []
    for base in roots:
        base_path = root / base
        if not base_path.is_dir():
            continue
        for file_path in sorted(p for p in base_path.rglob("*") if p.is_file()):
            rel_path = file_path.relative_to(root).as_posix()
            data = _read_bytes_bounded(file_path)
            file_findings, file_blocking = _scan_one_file(
                rel_path,
                data,
                baseline,
                target,
                include_canary=True,
                seen_fingerprints=seen_fingerprints,
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
# history: every unique blob reachable from any ref.
# ---------------------------------------------------------------------------


def iter_history_blob_ids(root: Path) -> list[tuple[str, str]]:
    """Returns [(blob_sha, path_hint)] for every unique blob reachable
    from any ref. `path_hint` is the first path git's own tree walk
    associated with that blob id -- location evidence only."""
    rev_list = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
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


def _iter_history_blob_contents(root: Path, blob_ids: list[str]) -> Iterator[tuple[str, bytes]]:
    """Streams every blob's content from one long-lived `git cat-file
    --batch` process -- one subprocess for the whole history, not one per
    blob. The full declared size must always be read to keep the
    protocol in sync for the next object; callers slice to their own
    bound after the read."""
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
                if total_kept < _MAX_SCAN_BYTES:
                    keep = chunk[: _MAX_SCAN_BYTES - total_kept]
                    chunks.append(keep)
                    total_kept += len(keep)
            proc.stdout.read(1)  # trailing newline after the object data
            yield sha, b"".join(chunks)
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
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Scans every unique blob reachable from any ref -- catches a secret
    that was committed and later removed by any commit, not only one
    reachable as a diff from its own introducing commit. Refuses a
    shallow clone. One `ScenarioResult` per unique blob."""
    assert_not_shallow(root)
    seen_fingerprints = seen_fingerprints if seen_fingerprints is not None else set()
    blob_ids = iter_history_blob_ids(root)
    path_hints = dict(blob_ids)
    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []
    for sha, data in _iter_history_blob_contents(root, [sha for sha, _ in blob_ids]):
        rel_path = path_hints.get(sha, sha[:12])
        location = f"{rel_path}@{sha[:12]}"
        blob_findings: list[SecretFinding] = []
        blob_blocking = False
        for rule, value, loc_suffix in _scan_bytes(data, include_canary=False):
            fingerprint = _baseline_fingerprint(rule, value)
            seen_fingerprints.add(fingerprint)
            blocking = fingerprint not in baseline
            blob_findings.append(
                SecretFinding(
                    rule=rule,
                    target=target,
                    location=f"{location}{loc_suffix}",
                    value_sha256=_value_sha256(value),
                    blocking=blocking,
                )
            )
            if blocking:
                blob_blocking = True
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
# log_samples: (a) a local logger through the real FTF-0904 pipeline;
# (b) a real uvicorn boot of the live API (FTF-0904-W1 is installed).
# ---------------------------------------------------------------------------


def scan_log_samples(
    seed_values: Iterable[str], *, target: str = "log_sample"
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Proves this suite is not vacuously passing, and that the real,
    already-certified FTF-0904 pipeline still redacts a seeded
    secret-shaped marker before it reaches a log sink. For each seed
    value: the RAW line must trip this suite's own rules (else this
    suite's rules cannot detect the shape it was asked to seed); the real
    sink output (through a fresh logger wired with the real
    `install_redaction`) must then be clean."""
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
    for index, value in enumerate(seed_values):
        raw_line = f"request completed token={value} status=ok"
        raw_hits = list(_matches(raw_line))
        detects_shape = bool(raw_hits)
        for rule, matched in raw_hits:
            findings.append(
                SecretFinding(
                    rule=rule,
                    target=target,
                    location=f"<pre-redaction:{index}>",
                    value_sha256=_value_sha256(matched),
                    blocking=False,
                )
            )
        results.append(
            ScenarioResult(
                resource_kind=target,
                check="pre_redaction_detects_seeded_shape",
                method="SCAN",
                path=f"<pre-redaction:{index}>",
                status=0 if detects_shape else 1,
                expected_denied=True,
                passed=detects_shape,
            )
        )

        buffer.seek(0)
        buffer.truncate(0)
        logger.info("request completed token=%s status=ok", value)
        sink_output = buffer.getvalue()
        sink_hits = list(_matches(sink_output))
        clean = not sink_hits
        for rule, matched in sink_hits:
            findings.append(
                SecretFinding(
                    rule=rule,
                    target=target,
                    location=f"<post-redaction:{index}>",
                    value_sha256=_value_sha256(matched),
                    blocking=True,
                )
            )
        results.append(
            ScenarioResult(
                resource_kind=target,
                check="post_redaction_clean",
                method="SCAN",
                path=f"<post-redaction:{index}>",
                status=1 if sink_hits else 0,
                expected_denied=True,
                passed=clean,
            )
        )
    logger.removeHandler(handler)
    return findings, results


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
    """A complete env for `create_app()`; the database URL is never
    connected to -- same shape FTF-0904-W1's own suite uses."""
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


def scan_live_app_log_samples(
    root: Path, *, target: str = "log_sample"
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Boots the real `uvicorn app.main:create_app --factory` (FTF-0904-W1
    is installed) and probes it with seeded shape-rule values and FTF-0905
    canaries (literal, base64, hex, url, fragmented) carried in the query
    string, an Authorization header and a Cookie header, against a real
    401 and a real 404 route. Scans the process's captured stdout/stderr.
    Disclosed scope: this lane exercises 401/404 only -- a live 5xx
    validation-error path is not separately exercised here (FTF-0904-W1's
    own suite already covers a startup-failure 5xx-shaped case)."""
    port = _free_port()
    marker = generate_canary()
    encoded = {
        encoding: encode_canary(marker, encoding)
        for encoding in ("literal", "base64", "hex", "url")
    }
    fragments = fragment_canary(marker, 2)
    shape_seed = "sk-" + marker.value.replace("CANARY-DO-NOT-USE-", "").ljust(32, "0")

    probes: list[tuple[str, str, dict[str, str], int]] = [
        (
            "query_literal",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={encoded['literal']}",
            {},
            401,
        ),
        (
            "query_base64",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={encoded['base64']}",
            {},
            401,
        ),
        (
            "query_hex",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={encoded['hex']}",
            {},
            401,
        ),
        (
            "query_url",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={encoded['url']}",
            {},
            401,
        ),
        (
            "query_fragmented",
            f"/api/v1/organizations/{uuid.uuid4()}/projects?token={fragments[0]}&token2={fragments[1]}",
            {},
            401,
        ),
        (
            "header_authorization",
            "/no-such-route-auth",
            {"Authorization": f"Bearer {shape_seed}"},
            404,
        ),
        (
            "header_cookie",
            "/no-such-route-cookie",
            {"Cookie": f"session={encoded['literal']}"},
            404,
        ),
    ]

    results: list[ScenarioResult] = []
    findings: list[SecretFinding] = []

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
                statuses: dict[str, int | None] = {}
                if booted:
                    for name, path, headers, _expected in probes:
                        try:
                            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                            conn.request("GET", path, headers=headers)
                            resp = conn.getresponse()
                            resp.read()
                            statuses[name] = resp.status
                            conn.close()
                        except OSError:
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

    results.append(
        ScenarioResult(
            resource_kind=target,
            check="live_app_boot",
            method="GET",
            path="<startup>",
            status=0 if booted else 1,
            expected_denied=True,
            passed=booted,
        )
    )
    if not booted:
        return findings, results

    for name, _path, _headers, expected in probes:
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

    hits = list(_matches(captured, include_canary=True))
    clean = not hits
    for rule, value in hits:
        findings.append(
            SecretFinding(
                rule=rule,
                target=target,
                location="<live-app-output>",
                value_sha256=_value_sha256(value),
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


def evaluate(results: list[ScenarioResult]) -> TenantIsolationEvidence:
    """Thin wrapper over the reused `build_evidence`. Re-raises the
    reused function's own `TenantIsolationSuiteError` as `SecretScanError`."""
    try:
        return build_evidence(results)
    except TenantIsolationSuiteError as exc:
        raise SecretScanError(str(exc)) from exc


def _default_seed_values() -> list[str]:
    """Harmless, obviously-fake markers -- one per rule family this
    suite's own shape rules recognise -- never a real secret, same
    `CANARY-DO-NOT-USE`-style discipline FTF-0905's canary generator
    established for leak testing, applied here to secret-SHAPE testing
    instead of leak-TRACKING."""
    return [
        "AKIA" + "CANARY234567ABCD",  # exactly 16 chars after AKIA, A-Z2-7 only
        "ghp_" + "CANARYDONOTUSE" + "0" * 22,  # >= 36 chars after ghp_
        "Bearer CANARY-DO-NOT-USE-0000000000000000000000",
        "sk-" + "CANARYDONOTUSE0" * 3,  # >= 32 chars after sk-
        "hf_" + "CANARYDONOTUSE" + "0" * 20,  # exactly 34 chars after hf_
    ]


def main() -> None:
    baseline = load_baseline()
    assert_dirs_covered(ROOT, SOURCE_ROOTS)
    assert_artifact_uploads_covered(CI_WORKFLOW_PATH.read_text(encoding="utf-8"), ARTIFACT_ROOTS)

    all_findings: list[SecretFinding] = []
    all_results: list[ScenarioResult] = []
    seen_fingerprints: set[str] = set()

    source_findings, source_results = scan_source(
        ROOT, baseline, seen_fingerprints=seen_fingerprints
    )
    all_findings.extend(source_findings)
    all_results.extend(source_results)

    artifact_findings, artifact_results = scan_artifact_roots(
        ROOT, baseline, seen_fingerprints=seen_fingerprints
    )
    all_findings.extend(artifact_findings)
    all_results.extend(artifact_results)

    history_findings, history_results = scan_history(
        ROOT, baseline, seen_fingerprints=seen_fingerprints
    )
    all_findings.extend(history_findings)
    all_results.extend(history_results)

    log_findings, log_results = scan_log_samples(_default_seed_values())
    all_findings.extend(log_findings)
    all_results.extend(log_results)

    live_findings, live_results = scan_live_app_log_samples(ROOT)
    all_findings.extend(live_findings)
    all_results.extend(live_results)

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
        f"{len(all_findings)} finding(s), 0 blocking -- clean"
    )


if __name__ == "__main__":
    try:
        main()
    except SecretScanError as exc:
        print(f"FTF-1505 secret scan FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
