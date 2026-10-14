#!/usr/bin/env python3
"""FTF-1505 -- Secret scanning (Section 45-47, 52-53, 65-66, 77, 81).

Acceptance: "Scan current source/history/artifacts/log samples with
seeded harmless secret markers." Negative: "Scanner omissions/excluded
production dirs and actual exposed credential are release blockers."

Reuse decisions (Section 84.3 "reuse an existing compatible module
instead of creating a duplicate"), made after checking, not assuming:

* `ScenarioResult`/`build_evidence` are imported directly from
  `tests/security/tenant_isolation_suite.py`, exactly as this task's own
  `contract_owner: FTF-1501` names. A `ScenarioResult` here is one
  scanned UNIT (one file, one history-touching commit, or one seeded
  log-sample check) -- `resource_kind` carries the scan target
  ("source"/"history"/"artifact"/"log_sample"), `method`/`path` carry
  "SCAN"/the unit's location, `expected_denied` is always True (a clean
  scan is the expected, "denied-secret" outcome), and `passed` is False
  exactly when that unit had a blocking finding. This keeps
  `build_evidence`'s own Law-1 "zero results proves nothing" check
  meaningful here too: it is zero *scanned units*, never zero *findings*
  (an all-clean scan is a real, non-vacuous result).
* FTF-0904's own `redact_text` was tried first as the detector (it
  already classifies most of the same secret shapes) and rejected after
  reproducing the failure directly against this repository's own
  `apps/api`: it fires on any `<identifier containing a secret
  keyword>: <anything>` pair, which floods a static source scan with
  ordinary type annotations (`tokenizer: LineageFact`, `credentials:
  S3Credentials`) -- correct behaviour for redacting free-form log prose,
  wrong precision for a release-blocking static scan. This module
  instead uses a dedicated, shape-only rule set (`_matches`) that
  requires an actual credential-shaped VALUE (a real AWS/GitHub/Slack/
  Google/Hugging Face/OpenAI/Stripe token shape, a PEM block, a
  three-part JWT, a bearer/basic header, or a quoted non-placeholder
  value assigned to a secret-named key) before firing -- the same
  shape-over-keyword discipline gitleaks/trufflehog/detect-secrets use.
  `redact_text`/`install_redaction` are still reused, unmodified, for the
  one place they are the right tool: proving the REAL, already-certified
  FTF-0904 log-redaction pipeline actually strips a seeded secret-shaped
  marker before it reaches a log sink (`scan_log_samples`).

Targets, and their honest scope:
  source    -- every git-tracked file under the real `SOURCE_ROOTS`
               production directories (this repo's own CLAUDE.md list).
  history   -- every added line, in every commit that ever touched those
               same directories, across the *entire* history
               (`git log --all -p`) -- catches a secret committed and
               later removed, not only what the working tree shows now.
  artifacts -- this repository's own real, checked-in evidence artifacts
               (`docs/certifications/`, `docs/progress/`): the actual
               generated-and-retained output this repository produces.
               DISCLOSED, NOT MET for one narrower artifact class: the
               ephemeral CI-run JSON artifacts (`reports/mutation-gate.
               json`, `reports/ci-verdict.json`) are never committed to
               git, so a from-clean-checkout run of this suite covers
               zero of them; `scan_source`/`scan_artifacts` scan them too
               automatically whenever a report file is actually present
               on disk at scan time (e.g. inside the same CI job that
               just generated one), but that is incidental, not a claim
               of guaranteed coverage -- the same disclosed-gap class
               FTF-1501 already carries for worker/storage/cache paths
               that do not exist yet.
  log_samples -- a real sample captured from the actual installed
               FTF-0904 redaction pipeline, proving this suite is not
               vacuously passing (seeded markers must be detected on the
               raw, pre-redaction line) while confirming the certified
               pipeline's real sink output is clean.

negative_acceptance made executable:
  - `assert_dirs_covered` fails closed the moment any directory in a
    covered set exists on disk but this suite's own file-inclusion logic
    (`iter_tracked_files` -- the same function the real scan uses, not a
    separate approximation) yields zero tracked files from it.
  - `evaluate`/`main` raise `SecretScanError` -- never a silent pass --
    the moment one real (non-allowlisted) secret shape is found anywhere.
    The only allowlist is `secret_scan_allowlist.yaml`
    (rule + exact value + file glob + required justification, the same
    shape as FTF-1506's `dependency_scan_policy.yaml`); it is checked
    against this repository's real history and tree before being trusted
    (see that file's own header).

Run directly:
    python3 tests/security/secret_scanning.py
"""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import io
import logging
import re
import subprocess
import sys
import uuid
from collections.abc import Iterable, Iterator
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

from packages.security.log_redaction import install_redaction  # noqa: E402
from tenant_isolation_suite import (  # noqa: E402
    ScenarioResult,
    TenantIsolationEvidence,
    TenantIsolationSuiteError,
    build_evidence,
)

__all__ = [
    "SOURCE_ROOTS",
    "ARTIFACT_DIRS",
    "RULES",
    "SecretScanError",
    "SecretFinding",
    "scan_line",
    "iter_tracked_files",
    "AllowlistEntry",
    "load_allowlist",
    "is_allowlisted",
    "assert_dirs_covered",
    "scan_source",
    "scan_history",
    "scan_log_samples",
    "evaluate",
]

SOURCE_ROOTS: tuple[str, ...] = (
    "apps/web",
    "apps/api",
    "workers/training",
    "workers/evaluation",
    "workers/data",
    "workers/deploy",
    "packages/shared",
)
ARTIFACT_DIRS: tuple[str, ...] = ("docs/certifications", "docs/progress")
ALLOWLIST_PATH = Path(__file__).resolve().parent / "secret_scan_allowlist.yaml"

_MAX_LINE_LENGTH = 4000  # guard against pathological minified/generated lines


class SecretScanError(Exception):
    """Refuses to produce trustworthy evidence, or reports a blocking
    (non-allowlisted) finding -- never silently converted to a pass
    (Law 17)."""


_SHAPE_RULES: dict[str, re.Pattern[str]] = {
    "aws_access_key_id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "github_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "bearer_or_basic_header": re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{20,}\b"),
    "pem_private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "jwt_shaped": re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    ),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "huggingface_token": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "stripe_style_key": re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{20,}\b"),
}

_QUOTED_SECRET_ASSIGNMENT_RULE = "quoted_secret_assignment"
_QUOTED_SECRET_ASSIGNMENT = re.compile(
    r"""(?i)\b(?:secret|password|passwd|api[_-]?key|access[_-]?token|private[_-]?key|auth[_-]?token)\b"""
    r"""\s*[:=]\s*["']([A-Za-z0-9+/_=\-]{16,})["']"""
)
_PLACEHOLDER_VALUE = re.compile(
    r"(?i)^(?:changeme|placeholder|example|dummy|test|fake|xxx+|your[_-].*|none|null|<.*>|\$\{.*\})$"
)

RULES: tuple[str, ...] = (*sorted(_SHAPE_RULES), _QUOTED_SECRET_ASSIGNMENT_RULE)


def _matches(line: str) -> Iterator[tuple[str, str]]:
    """Yields (rule_name, matched_value) for every rule firing on `line`.
    Shape-only: a rule needs an actual credential-shaped VALUE, never
    merely a key NAME containing a secret word -- see module docstring."""
    if len(line) > _MAX_LINE_LENGTH:
        return
    for name, rx in _SHAPE_RULES.items():
        match = rx.search(line)
        if match:
            yield name, match.group(0)
    for match in _QUOTED_SECRET_ASSIGNMENT.finditer(line):
        value = match.group(1)
        if _PLACEHOLDER_VALUE.match(value):
            continue
        yield _QUOTED_SECRET_ASSIGNMENT_RULE, value


def scan_line(line: str) -> list[tuple[str, str]]:
    """Pure, public entry point so tests can exercise the exact rule set
    with no filesystem/git access."""
    return list(_matches(line))


def _value_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


@dataclasses.dataclass(frozen=True)
class AllowlistEntry:
    rule: str
    value: str
    file_glob: str
    justification: str


def load_allowlist(path: Path = ALLOWLIST_PATH) -> list[AllowlistEntry]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_entries = data.get("exceptions") or []
    entries: list[AllowlistEntry] = []
    for raw in raw_entries:
        rule = str(raw.get("rule") or "")
        value = str(raw.get("value") or "")
        file_glob = str(raw.get("file_glob") or "")
        justification = str(raw.get("justification") or "")
        if not rule or not value or not file_glob or not justification.strip():
            raise SecretScanError(
                f"malformed secret-scan allowlist entry (needs rule/value/file_glob/"
                f"justification, all non-empty): {raw!r}"
            )
        entries.append(AllowlistEntry(rule, value, file_glob, justification))
    return entries


def is_allowlisted(
    rule: str, value: str, file_path: str, entries: Iterable[AllowlistEntry]
) -> bool:
    return any(
        entry.rule == rule and entry.value == value and fnmatch.fnmatch(file_path, entry.file_glob)
        for entry in entries
    )


def iter_tracked_files(root: Path, dirs: tuple[str, ...]) -> list[str]:
    """Real git-tracked files under any of `dirs` -- the one inclusion
    function every scan target and `assert_dirs_covered` both use, so a
    directory omission cannot hide behind two diverging implementations."""
    all_files = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [f for f in all_files if any(f == d or f.startswith(d + "/") for d in dirs)]


def assert_dirs_covered(root: Path, dirs: tuple[str, ...]) -> None:
    """negative_acceptance, made executable: fails closed if a directory
    that actually exists on disk yields zero tracked files from this
    suite's own inclusion logic -- "scanner omissions/excluded production
    dirs" is a release blocker, not a warning."""
    for directory in dirs:
        if not (root / directory).is_dir():
            continue
        if not iter_tracked_files(root, (directory,)):
            raise SecretScanError(
                f"secret scan omission: {directory!r} exists but the scanner's own "
                "file-inclusion logic covers zero tracked files in it"
            )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def scan_source(
    root: Path,
    dirs: tuple[str, ...],
    allowlist: list[AllowlistEntry],
    *,
    target: str,
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Scans every tracked file under `dirs`. One `ScenarioResult` per
    scanned FILE (never per finding, so an all-clean scan still yields
    real, non-vacuous evidence): `passed` is False iff that file has at
    least one blocking finding."""
    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []
    for rel_path in iter_tracked_files(root, dirs):
        text = _read_text(root / rel_path)
        if text is None:
            continue
        file_blocking = False
        file_check = "clean"
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rule, value in _matches(line):
                blocking = not is_allowlisted(rule, value, rel_path, allowlist)
                findings.append(
                    SecretFinding(
                        rule=rule,
                        target=target,
                        location=f"{rel_path}:{lineno}",
                        value_sha256=_value_sha256(value),
                        blocking=blocking,
                    )
                )
                if blocking:
                    file_blocking = True
                    file_check = rule
        results.append(
            ScenarioResult(
                resource_kind=target,
                check=file_check,
                method="SCAN",
                path=rel_path,
                status=1 if file_blocking else 0,
                expected_denied=True,
                passed=not file_blocking,
            )
        )
    return findings, results


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_history(
    root: Path,
    dirs: tuple[str, ...],
    allowlist: list[AllowlistEntry],
    *,
    target: str = "history",
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Scans every ADDED line in every commit that ever touched `dirs`,
    across the entire history -- catches a secret committed and later
    removed, which a working-tree-only scan would miss entirely. One
    `ScenarioResult` per commit touching `dirs` (git's own `-- <paths>`
    filter already restricts the log to those commits)."""
    output = subprocess.run(
        ["git", "log", "--all", "--unified=0", "--pretty=format:COMMIT:%H", "-p", "--", *dirs],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    findings: list[SecretFinding] = []
    results: list[ScenarioResult] = []
    commit = ""
    current_file = ""
    new_lineno = 0
    commit_blocking = False
    commit_check = "clean"
    seen_commit = False

    def flush_commit() -> None:
        if seen_commit:
            results.append(
                ScenarioResult(
                    resource_kind=target,
                    check=commit_check,
                    method="SCAN",
                    path=commit[:12],
                    status=1 if commit_blocking else 0,
                    expected_denied=True,
                    passed=not commit_blocking,
                )
            )

    for raw_line in output.splitlines():
        if raw_line.startswith("COMMIT:"):
            flush_commit()
            commit = raw_line[len("COMMIT:") :]
            commit_blocking = False
            commit_check = "clean"
            seen_commit = True
            continue
        if raw_line.startswith("+++ "):
            current_file = raw_line[4:]
            if current_file.startswith("b/"):
                current_file = current_file[2:]
            continue
        if raw_line.startswith(("--- ", "diff ", "index ")):
            continue
        hunk_match = _HUNK_HEADER.match(raw_line)
        if hunk_match:
            new_lineno = int(hunk_match.group(1))
            continue
        if raw_line.startswith("+"):
            content = raw_line[1:]
            for rule, value in _matches(content):
                blocking = not is_allowlisted(rule, value, current_file, allowlist)
                findings.append(
                    SecretFinding(
                        rule=rule,
                        target=target,
                        location=f"{current_file}:{new_lineno}@{commit[:12]}",
                        value_sha256=_value_sha256(value),
                        blocking=blocking,
                    )
                )
                if blocking:
                    commit_blocking = True
                    commit_check = rule
            new_lineno += 1
    flush_commit()
    return findings, results


def scan_log_samples(
    seed_values: Iterable[str], *, target: str = "log_sample"
) -> tuple[list[SecretFinding], list[ScenarioResult]]:
    """Proves this suite is not vacuously passing, and that the real,
    already-certified FTF-0904 pipeline (`install_redaction`, reused
    unmodified -- never re-implemented) still redacts a seeded
    secret-shaped marker before it reaches a log sink.

    For each seed value: the RAW line (before any redaction) must trip
    this suite's own rule set -- if it does not, this suite's rules
    cannot detect the shape it was asked to seed, and the post-redaction
    check below would be meaningless. The real sink output (through a
    fresh, isolated logger wired with the real `install_redaction`) must
    then be clean. Both are release blockers if violated: the first is a
    detection gap in this suite, the second is a regression in an
    already-certified control.
    """
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


def evaluate(results: list[ScenarioResult]) -> TenantIsolationEvidence:
    """Thin wrapper over the reused `build_evidence` -- kept as this
    module's own public name so callers never need to know which sibling
    module actually owns the evidence shape. Re-raises the reused
    function's own `TenantIsolationSuiteError` as `SecretScanError`, so
    every caller of this module only ever needs to catch one exception
    type."""
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
        "AKIA" + "CANARYDONOTUSE00",  # exactly 16 chars after AKIA
        "ghp_" + "CANARYDONOTUSE" + "0" * 22,  # >= 36 chars after ghp_
        "Bearer CANARY-DO-NOT-USE-0000000000000000000000",
        "sk-CANARYDONOTUSE000000000000000000000000",
        "hf_CANARYDONOTUSE00000000000000",
    ]


def main() -> None:
    allowlist = load_allowlist()
    assert_dirs_covered(ROOT, SOURCE_ROOTS)
    assert_dirs_covered(ROOT, ARTIFACT_DIRS)

    all_findings: list[SecretFinding] = []
    all_results: list[ScenarioResult] = []

    for scan, dirs, target in (
        (scan_source, SOURCE_ROOTS, "source"),
        (scan_source, ARTIFACT_DIRS, "artifact"),
    ):
        findings, results = scan(ROOT, dirs, allowlist, target=target)
        all_findings.extend(findings)
        all_results.extend(results)

    history_findings, history_results = scan_history(ROOT, SOURCE_ROOTS + ARTIFACT_DIRS, allowlist)
    all_findings.extend(history_findings)
    all_results.extend(history_results)

    log_findings, log_results = scan_log_samples(_default_seed_values())
    all_findings.extend(log_findings)
    all_results.extend(log_results)

    evidence = evaluate(all_results)
    print(
        __import__("json").dumps(
            {
                "evidence": evidence.to_dict(),
                "findings": [f.to_dict() for f in all_findings],
            },
            indent=2,
        )
    )

    blocking = [f for f in all_findings if f.blocking]
    if blocking:
        print(f"SECRET SCAN: {len(blocking)} blocking finding(s), cannot pass:", file=sys.stderr)
        for finding in blocking:
            print(f"  [{finding.target}] {finding.rule} at {finding.location}", file=sys.stderr)
        raise SecretScanError(
            f"{len(blocking)} secret-shaped finding(s) with no valid allowlist entry -- "
            "cannot be silently ignored (negative_acceptance)"
        )
    failed_scenarios = evidence.scenarios_failed
    if failed_scenarios:
        raise SecretScanError(
            f"{failed_scenarios} scan scenario(s) failed (log-sample detection/redaction "
            "check) -- see ScenarioResult evidence above"
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
