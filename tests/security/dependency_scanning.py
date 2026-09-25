#!/usr/bin/env python3
"""FTF-1506 -- Dependency scanning (Section 45-47, 52-53, 65-66, 77, 81).

Scans this repository's real pinned runtime/development dependencies
(`uv.lock` for PyPI, `pnpm-lock.yaml` for npm) against OSV.dev's public
vulnerability database and enforces a recorded vulnerability/patch
policy (`tests/security/dependency_scan_policy.yaml`): a CRITICAL/HIGH
or severity-UNKNOWN finding blocks unless a matching, non-expired,
justified policy exception names that exact (ecosystem, package, vuln
id) tuple (negative_acceptance -- "Unknown/unpatched critical/high
finding cannot be silently ignored to satisfy cost or schedule").

`shared_lock_required` is empty for this task (confirmed via
`tools/r1_handoff.py packet FTF-1506`) -- it reads the section-15
security domain contract already frozen by FTF-1501 but adds no new
schema/interface entity of its own; this is a CI security tool, not a
product API surface, so `packages/contracts/openapi/15-interface.yaml`
and `packages/contracts/jsonschema/15-domain.schema.json` are read-only
context here, never touched.

Network reality (disclosed, not assumed): this repository's interactive
dev sandbox routes outbound HTTPS through a restricted agent proxy that
blocks `api.osv.dev` directly (`curl` to it returns a 403 CONNECT-tunnel
failure from the proxy itself, not from OSV.dev) -- the same class of
external-network gap already disclosed for FTF-0004's container-registry
pull. Per this relay's own operating instructions ("route live external
proofs through CI Actions workflows"), the real OSV.dev call in `main()`
below is proven for real only by the dedicated
`dependency-scanning-integration` CI job, which runs on a GitHub-hosted
runner with real internet egress. Every other function in this module
(lockfile parsing, CVSS v3.1 base-score computation, severity bucketing,
policy-exception validity, blocking evaluation) is pure/no-network and is
proven directly, without a live provider, by
`tests/tasks/test_ftf_1506.py`.

Run directly (real network egress to OSV.dev required):
    python3 tests/security/dependency_scanning.py
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import tomllib
import urllib.error
import urllib.request
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
UV_LOCK_PATH = ROOT / "uv.lock"
PNPM_LOCK_PATH = ROOT / "pnpm-lock.yaml"
POLICY_PATH = Path(__file__).resolve().parent / "dependency_scan_policy.yaml"

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
OSV_REQUEST_TIMEOUT_SECONDS = 15.0

SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
BLOCKING_SEVERITIES = frozenset({"CRITICAL", "HIGH", "UNKNOWN"})

_PNPM_PACKAGE_KEY_RE = re.compile(r"^(?P<name>@[^@]+/[^@]+|[^@]+)@(?P<version>[^(]+?)(?:\(.*\))?$")


class DependencyScanError(Exception):
    """Raised when the scan cannot produce trustworthy evidence: zero
    dependencies scanned (Law 1 -- an empty scan proves nothing), a
    malformed policy exception, a provider failure while querying
    vulnerability data (Law 17 -- never silently converted to success),
    or one or more findings block release under the recorded policy."""


@dataclasses.dataclass(frozen=True)
class PinnedDependency:
    ecosystem: str  # "PyPI" | "npm"
    name: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {"ecosystem": self.ecosystem, "name": self.name, "version": self.version}


@dataclasses.dataclass(frozen=True)
class PolicyException:
    vuln_id: str
    ecosystem: str
    name: str
    justification: str
    expires_on: date

    def is_valid(self, today: date) -> bool:
        return bool(self.justification.strip()) and self.expires_on >= today

    def covers(self, ecosystem: str, name: str, vuln_id: str) -> bool:
        return self.ecosystem == ecosystem and self.name == name and self.vuln_id == vuln_id


@dataclasses.dataclass(frozen=True)
class VulnerabilityFinding:
    ecosystem: str
    name: str
    version: str
    vuln_id: str
    severity: str
    fixed_versions: tuple[str, ...]
    blocking: bool
    excepted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "name": self.name,
            "version": self.version,
            "vuln_id": self.vuln_id,
            "severity": self.severity,
            "fixed_versions": list(self.fixed_versions),
            "blocking": self.blocking,
            "excepted": self.excepted,
        }


@dataclasses.dataclass(frozen=True)
class DependencyScanEvidence:
    suite_id: str
    executed_at: str
    dependencies_scanned: int
    findings_total: int
    findings_blocking: int
    findings: tuple[VulnerabilityFinding, ...]
    content_free: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "executed_at": self.executed_at,
            "dependencies_scanned": self.dependencies_scanned,
            "findings_total": self.findings_total,
            "findings_blocking": self.findings_blocking,
            "findings": [f.to_dict() for f in self.findings],
            "content_free": self.content_free,
        }


def parse_uv_lock(path: Path = UV_LOCK_PATH) -> list[PinnedDependency]:
    """Real third-party PyPI dependencies pinned in `uv.lock` -- workspace
    members (this repo's own `apps/api` etc.) have no `registry` source
    and are excluded: they have no published-advisory surface of their
    own to scan."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps: list[PinnedDependency] = []
    for pkg in data.get("package", []):
        name = pkg.get("name")
        version = pkg.get("version")
        source = pkg.get("source") or {}
        if not name or not version or "registry" not in source:
            continue
        deps.append(PinnedDependency(ecosystem="PyPI", name=name, version=version))
    return deps


def parse_pnpm_lock(path: Path = PNPM_LOCK_PATH) -> list[PinnedDependency]:
    """Real third-party npm dependencies resolved in `pnpm-lock.yaml`'s
    own `packages:` map -- keys are `name@version` (scoped names carry
    one embedded `/`), optionally suffixed with a peer-dependency
    disambiguation in parentheses that carries no separate advisory
    surface of its own and is discarded."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    packages = data.get("packages") or {}
    deps: list[PinnedDependency] = []
    seen: set[tuple[str, str]] = set()
    for key in packages:
        match = _PNPM_PACKAGE_KEY_RE.match(key)
        if not match:
            continue
        name = match.group("name")
        version = match.group("version")
        if (name, version) in seen:
            continue
        seen.add((name, version))
        deps.append(PinnedDependency(ecosystem="npm", name=name, version=version))
    return deps


def _roundup(value: float) -> float:
    """CVSS v3.1 Section 8.1 `Roundup` -- round up to the nearest 0.1,
    computed in fixed-point to avoid binary-float misrounding."""
    int_value = round(value * 100000)
    if int_value % 10000 == 0:
        return int_value / 100000.0
    return (int_value // 10000 + 1) / 10.0


def cvss_v3_base_score(vector: str) -> float | None:
    """A faithful CVSS v3.0/3.1 base-score calculator (the published
    formula, not a lookup table or heuristic) -- returns `None` for a
    vector this cannot parse (unsupported version, missing metric)
    rather than guessing a score."""
    parts = vector.split("/")
    if not parts or not parts[0].startswith("CVSS:3."):
        return None
    metrics: dict[str, str] = {}
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        metrics[key] = value

    try:
        av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[metrics["AV"]]
        ac = {"L": 0.77, "H": 0.44}[metrics["AC"]]
        ui = {"N": 0.85, "R": 0.62}[metrics["UI"]]
        scope_changed = metrics["S"] == "C"
        pr_raw = metrics["PR"]
        if pr_raw == "N":
            pr = 0.85
        elif pr_raw == "L":
            pr = 0.68 if scope_changed else 0.62
        elif pr_raw == "H":
            pr = 0.50 if scope_changed else 0.27
        else:
            return None
        impact = {"H": 0.56, "L": 0.22, "N": 0.0}
        c = impact[metrics["C"]]
        i = impact[metrics["I"]]
        a = impact[metrics["A"]]
    except KeyError:
        return None

    isc_base = 1 - ((1 - c) * (1 - i) * (1 - a))
    if isc_base <= 0:
        return 0.0

    if scope_changed:
        isc = 7.52 * (isc_base - 0.029) - 3.25 * ((isc_base - 0.02) ** 15)
    else:
        isc = 6.42 * isc_base
    exploitability = 8.22 * av * ac * pr * ui

    if isc <= 0:
        return 0.0
    if scope_changed:
        base = min(1.08 * (isc + exploitability), 10.0)
    else:
        base = min(isc + exploitability, 10.0)
    return _roundup(base)


def bucket_cvss_score(score: float) -> str:
    """The published CVSS qualitative severity-rating scale."""
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    return "LOW"


def severity_from_vuln(vuln: dict[str, Any]) -> str:
    """`database_specific.severity` (GHSA's own enum) takes priority when
    present; otherwise this derives a rating from the first parseable
    CVSS v3 vector. Anything with neither is `UNKNOWN` -- never silently
    treated as low-risk (negative_acceptance)."""
    db_severity = (vuln.get("database_specific") or {}).get("severity")
    if isinstance(db_severity, str):
        normalized = db_severity.strip().upper()
        mapping = {
            "CRITICAL": "CRITICAL",
            "HIGH": "HIGH",
            "MODERATE": "MEDIUM",
            "MEDIUM": "MEDIUM",
            "LOW": "LOW",
        }
        if normalized in mapping:
            return mapping[normalized]

    for entry in vuln.get("severity") or []:
        if entry.get("type") == "CVSS_V3":
            score = cvss_v3_base_score(str(entry.get("score", "")))
            if score is not None:
                return bucket_cvss_score(score)

    return "UNKNOWN"


def fixed_versions_for(vuln: dict[str, Any], ecosystem: str, name: str) -> tuple[str, ...]:
    versions: set[str] = set()
    for affected in vuln.get("affected") or []:
        pkg = affected.get("package") or {}
        if pkg.get("ecosystem") != ecosystem or pkg.get("name") != name:
            continue
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                fixed = event.get("fixed")
                if fixed:
                    versions.add(str(fixed))
    return tuple(sorted(versions))


def query_osv(
    dep: PinnedDependency, *, timeout: float = OSV_REQUEST_TIMEOUT_SECONDS
) -> list[dict[str, Any]]:
    """Real OSV.dev query for one pinned dependency. Never returns an
    empty result on a provider/network failure -- raises instead (Law
    17: provider failure stays visible as blocked/failed, never
    converted to a false pass)."""
    body = json.dumps(
        {"version": dep.version, "package": {"name": dep.name, "ecosystem": dep.ecosystem}}
    ).encode("utf-8")
    request = urllib.request.Request(
        OSV_QUERY_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise DependencyScanError(
            f"OSV query failed for {dep.ecosystem}:{dep.name}=={dep.version}: {exc}"
        ) from exc
    vulns = payload.get("vulns")
    return list(vulns) if isinstance(vulns, list) else []


def _parse_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise DependencyScanError(
                f"policy exception has an unparseable expires_on: {value!r}"
            ) from exc
    raise DependencyScanError(f"policy exception has a missing/invalid expires_on: {value!r}")


def load_policy_exceptions(path: Path = POLICY_PATH) -> list[PolicyException]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_exceptions = data.get("exceptions") or []
    exceptions: list[PolicyException] = []
    for raw in raw_exceptions:
        vuln_id = str(raw.get("vuln_id") or "")
        ecosystem = str(raw.get("ecosystem") or "")
        name = str(raw.get("name") or "")
        if not vuln_id or not ecosystem or not name:
            raise DependencyScanError(
                f"policy exception is missing vuln_id/ecosystem/name: {raw!r}"
            )
        exceptions.append(
            PolicyException(
                vuln_id=vuln_id,
                ecosystem=ecosystem,
                name=name,
                justification=str(raw.get("justification") or ""),
                expires_on=_parse_date(raw.get("expires_on")),
            )
        )
    return exceptions


def evaluate(
    dependencies: list[PinnedDependency],
    vulns_by_dependency: dict[PinnedDependency, list[dict[str, Any]]],
    exceptions: list[PolicyException],
    *,
    today: date | None = None,
) -> DependencyScanEvidence:
    if not dependencies:
        raise DependencyScanError(
            "refusing to build evidence from zero scanned dependencies -- "
            "an empty scan proves nothing (Law 1)"
        )
    today = today if today is not None else datetime.now(UTC).date()
    valid_exceptions = [exc for exc in exceptions if exc.is_valid(today)]

    findings: list[VulnerabilityFinding] = []
    for dep in dependencies:
        for vuln in vulns_by_dependency.get(dep, []):
            vuln_id = str(vuln.get("id") or "")
            if not vuln_id:
                continue
            severity = severity_from_vuln(vuln)
            fixed = fixed_versions_for(vuln, dep.ecosystem, dep.name)
            excepted = any(exc.covers(dep.ecosystem, dep.name, vuln_id) for exc in valid_exceptions)
            blocking = severity in BLOCKING_SEVERITIES and not excepted
            findings.append(
                VulnerabilityFinding(
                    ecosystem=dep.ecosystem,
                    name=dep.name,
                    version=dep.version,
                    vuln_id=vuln_id,
                    severity=severity,
                    fixed_versions=fixed,
                    blocking=blocking,
                    excepted=excepted,
                )
            )

    blocking_count = sum(1 for f in findings if f.blocking)
    return DependencyScanEvidence(
        suite_id=str(uuid.uuid4()),
        executed_at=datetime.now(UTC).isoformat(),
        dependencies_scanned=len(dependencies),
        findings_total=len(findings),
        findings_blocking=blocking_count,
        findings=tuple(findings),
        content_free=True,
    )


def main() -> None:
    dependencies = [*parse_uv_lock(), *parse_pnpm_lock()]
    exceptions = load_policy_exceptions()
    vulns_by_dependency: dict[PinnedDependency, list[dict[str, Any]]] = {}
    for dep in dependencies:
        vulns_by_dependency[dep] = query_osv(dep)

    evidence = evaluate(dependencies, vulns_by_dependency, exceptions)
    print(json.dumps(evidence.to_dict(), indent=2))

    if evidence.findings_blocking:
        blocking = [f for f in evidence.findings if f.blocking]
        print(
            f"DEPENDENCY SCAN: {len(blocking)} blocking finding(s), cannot pass:",
            file=sys.stderr,
        )
        for finding in blocking:
            print(
                f"  {finding.ecosystem}:{finding.name}=={finding.version} "
                f"{finding.vuln_id} severity={finding.severity}",
                file=sys.stderr,
            )
        raise DependencyScanError(
            f"{len(blocking)} unknown/unpatched critical/high finding(s) present with no "
            "valid policy exception -- cannot be silently ignored (negative_acceptance)"
        )

    print(
        f"DEPENDENCY SCAN: {evidence.dependencies_scanned} dependencies, "
        f"{evidence.findings_total} finding(s), 0 blocking -- clean"
    )


if __name__ == "__main__":
    try:
        main()
    except DependencyScanError as exc:
        print(f"FTF-1506 dependency scan FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
