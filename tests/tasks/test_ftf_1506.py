#!/usr/bin/env python3
"""FTF-1506 -- Dependency scanning (no-network unit/fault tests).

Runnable without any network egress. This suite proves every pure/
structural part of `tests/security/dependency_scanning.py` directly,
against real fixture data (a known CVSS v3.1 reference vector, this
repo's own real `uv.lock`/`pnpm-lock.yaml`, and OSV-shaped stub
responses) -- the module's own real, live OSV.dev call (`query_osv`,
exercised end-to-end by `main()`) is proven separately, with real
network egress, by the dedicated `dependency-scanning-integration` CI
job (see that module's own docstring for why this sandbox itself cannot
make that call directly).

Run directly: python3 tests/tasks/test_ftf_1506.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

from dependency_scanning import (  # noqa: E402
    DependencyScanError,
    PinnedDependency,
    PolicyException,
    bucket_cvss_score,
    cvss_v3_base_score,
    evaluate,
    fixed_versions_for,
    load_policy_exceptions,
    parse_pnpm_lock,
    parse_uv_lock,
    severity_from_vuln,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# ---------------------------------------------------------------------------
# CVSS v3.1 base-score calculator: real published formula, checked against a
# well-known, independently published reference score (Log4Shell,
# CVE-2021-44228, NVD-published CVSS v3.1 base score 10.0) -- not a value
# this suite invented.
# ---------------------------------------------------------------------------


def test_cvss_v3_matches_known_published_reference_score() -> None:
    log4shell_vector = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"
    score = cvss_v3_base_score(log4shell_vector)
    check(
        "FTF-1506",
        f"CVSS v3.1 base-score formula reproduces the published NVD score "
        f"(10.0) for CVE-2021-44228's real vector, got {score!r}",
        score == 10.0,
    )
    check(
        "FTF-1506",
        "a base score of 10.0 buckets as CRITICAL",
        bucket_cvss_score(10.0) == "CRITICAL",
    )


def test_cvss_v3_bucket_boundaries() -> None:
    check(
        "FTF-1506",
        "8.9 buckets HIGH (below the 9.0 CRITICAL boundary)",
        bucket_cvss_score(8.9) == "HIGH",
    )
    check("FTF-1506", "9.0 buckets CRITICAL", bucket_cvss_score(9.0) == "CRITICAL")
    check(
        "FTF-1506",
        "6.9 buckets MEDIUM (below the 7.0 HIGH boundary)",
        bucket_cvss_score(6.9) == "MEDIUM",
    )
    check("FTF-1506", "7.0 buckets HIGH", bucket_cvss_score(7.0) == "HIGH")
    check(
        "FTF-1506",
        "3.9 buckets LOW (below the 4.0 MEDIUM boundary)",
        bucket_cvss_score(3.9) == "LOW",
    )
    check("FTF-1506", "0.0 buckets LOW, never UNKNOWN", bucket_cvss_score(0.0) == "LOW")


def test_cvss_v3_rejects_unparseable_vectors() -> None:
    check(
        "FTF-1506",
        "a CVSS v2 vector (unsupported version) returns None, never a guessed score",
        cvss_v3_base_score("AV:N/AC:L/Au:N/C:C/I:C/A:C") is None,
    )
    check(
        "FTF-1506",
        "a v3 vector missing a required metric returns None, never a guessed score",
        cvss_v3_base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H") is None,
    )


# ---------------------------------------------------------------------------
# severity_from_vuln / fixed_versions_for: real OSV-shaped stub payloads.
# ---------------------------------------------------------------------------


def test_severity_prefers_database_specific_over_cvss() -> None:
    vuln = {
        "database_specific": {"severity": "MODERATE"},
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"}],
    }
    check(
        "FTF-1506",
        "database_specific.severity (GHSA's own enum) is used when present, "
        "even though the CVSS vector here would independently bucket CRITICAL "
        "-- MODERATE normalizes to MEDIUM",
        severity_from_vuln(vuln) == "MEDIUM",
    )


def test_severity_falls_back_to_cvss_vector() -> None:
    vuln = {
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"}]
    }
    check(
        "FTF-1506",
        "with no database_specific.severity, a real CRITICAL CVSS vector buckets CRITICAL",
        severity_from_vuln(vuln) == "CRITICAL",
    )


def test_severity_unknown_when_neither_source_present() -> None:
    check(
        "FTF-1506",
        "negative_acceptance: a vuln with no database_specific.severity and "
        "no parseable CVSS vector is UNKNOWN, never silently treated as low-risk",
        severity_from_vuln({}) == "UNKNOWN",
    )


def test_fixed_versions_scoped_to_the_matching_package() -> None:
    vuln = {
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": "widget"},
                "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.0.0"}]}],
            },
            {
                "package": {"ecosystem": "npm", "name": "widget"},
                "ranges": [{"events": [{"fixed": "9.9.9"}]}],
            },
        ]
    }
    check(
        "FTF-1506",
        "fixed_versions_for only collects fixed events for the exact "
        "(ecosystem, name) pair asked about, not every affected entry in the vuln",
        fixed_versions_for(vuln, "PyPI", "widget") == ("2.0.0",),
    )
    check(
        "FTF-1506",
        "a package/ecosystem with no matching affected entry has no fixed versions",
        fixed_versions_for(vuln, "PyPI", "other-package") == (),
    )


# ---------------------------------------------------------------------------
# evaluate(): the actual blocking decision -- the negative_acceptance clause.
# ---------------------------------------------------------------------------

_DEP = PinnedDependency(ecosystem="PyPI", name="widget", version="1.0.0")


def _vuln(vuln_id: str, severity_block: dict[str, object]) -> dict[str, object]:
    return {"id": vuln_id, **severity_block}


def test_evaluate_refuses_zero_dependencies() -> None:
    raised = False
    try:
        evaluate([], {}, [])
    except DependencyScanError:
        raised = True
    check(
        "FTF-1506",
        "Law 1: evaluate refuses to build evidence from zero scanned "
        "dependencies -- an empty scan proves nothing",
        raised,
    )


def test_evaluate_blocks_an_unpatched_critical_finding_with_no_exception() -> None:
    vulns = {_DEP: [_vuln("GHSA-1", {"database_specific": {"severity": "CRITICAL"}})]}
    evidence = evaluate([_DEP], vulns, [])
    check(
        "FTF-1506",
        "negative_acceptance: an unpatched CRITICAL finding with no policy " "exception blocks",
        evidence.findings_blocking == 1 and evidence.findings[0].blocking is True,
    )


def test_evaluate_never_blocks_on_medium_or_low_severity() -> None:
    vulns = {
        _DEP: [
            _vuln("GHSA-2", {"database_specific": {"severity": "MODERATE"}}),
            _vuln("GHSA-3", {"database_specific": {"severity": "LOW"}}),
        ]
    }
    evidence = evaluate([_DEP], vulns, [])
    check(
        "FTF-1506",
        "MEDIUM and LOW severity findings are recorded but never block release",
        evidence.findings_total == 2 and evidence.findings_blocking == 0,
    )


def test_evaluate_blocks_unknown_severity_by_default() -> None:
    vulns = {_DEP: [_vuln("GHSA-4", {})]}
    evidence = evaluate([_DEP], vulns, [])
    check(
        "FTF-1506",
        "negative_acceptance: a finding with UNKNOWN severity (no "
        "database_specific.severity, no parseable CVSS vector) blocks by "
        "default -- fail-closed, never silently ignored",
        evidence.findings_blocking == 1,
    )


def test_evaluate_honors_a_valid_matching_exception() -> None:
    vulns = {_DEP: [_vuln("GHSA-5", {"database_specific": {"severity": "CRITICAL"}})]}
    exceptions = [
        PolicyException(
            vuln_id="GHSA-5",
            ecosystem="PyPI",
            name="widget",
            justification="Real, recorded reason.",
            expires_on=date(2999, 1, 1),
        )
    ]
    evidence = evaluate([_DEP], vulns, exceptions, today=date(2026, 1, 1))
    check(
        "FTF-1506",
        "a valid (justified, not-yet-expired), exactly-matching policy "
        "exception voids a CRITICAL finding's blocking status",
        evidence.findings_blocking == 0 and evidence.findings[0].excepted is True,
    )


def test_evaluate_expired_exception_still_blocks() -> None:
    vulns = {_DEP: [_vuln("GHSA-6", {"database_specific": {"severity": "HIGH"}})]}
    exceptions = [
        PolicyException(
            vuln_id="GHSA-6",
            ecosystem="PyPI",
            name="widget",
            justification="Was valid once.",
            expires_on=date(2020, 1, 1),
        )
    ]
    evidence = evaluate([_DEP], vulns, exceptions, today=date(2026, 1, 1))
    check(
        "FTF-1506",
        "negative_acceptance: an expired exception is void -- staleness "
        "never grants a silent, permanent pass",
        evidence.findings_blocking == 1,
    )


def test_evaluate_unjustified_exception_still_blocks() -> None:
    vulns = {_DEP: [_vuln("GHSA-7", {"database_specific": {"severity": "HIGH"}})]}
    exceptions = [
        PolicyException(
            vuln_id="GHSA-7",
            ecosystem="PyPI",
            name="widget",
            justification="   ",
            expires_on=date(2999, 1, 1),
        )
    ]
    evidence = evaluate([_DEP], vulns, exceptions, today=date(2026, 1, 1))
    check(
        "FTF-1506",
        "negative_acceptance: an exception with a blank/whitespace-only "
        "justification is void -- cost/schedule pressure cannot silently "
        "suppress a finding with no real justification",
        evidence.findings_blocking == 1,
    )


def test_evaluate_exception_does_not_cover_a_different_package_or_vuln() -> None:
    vulns = {_DEP: [_vuln("GHSA-8", {"database_specific": {"severity": "HIGH"}})]}
    mismatched_vuln = PolicyException(
        vuln_id="GHSA-DIFFERENT",
        ecosystem="PyPI",
        name="widget",
        justification="real",
        expires_on=date(2999, 1, 1),
    )
    mismatched_package = PolicyException(
        vuln_id="GHSA-8",
        ecosystem="PyPI",
        name="a-different-package",
        justification="real",
        expires_on=date(2999, 1, 1),
    )
    evidence = evaluate(
        [_DEP], vulns, [mismatched_vuln, mismatched_package], today=date(2026, 1, 1)
    )
    check(
        "FTF-1506",
        "an exception naming a different vuln id or a different package "
        "never covers this finding -- exceptions cannot silently over-apply",
        evidence.findings_blocking == 1,
    )


def test_evidence_is_content_free_by_construction() -> None:
    forbidden = {
        "body",
        "payload",
        "secret",
        "secret_ref",
        "password",
        "token",
        "raw",
        "summary",
        "description",
    }
    from dependency_scanning import DependencyScanEvidence, VulnerabilityFinding

    check(
        "FTF-1506",
        "VulnerabilityFinding's own fields never include a raw vuln "
        "description/body -- content-free by construction",
        set(VulnerabilityFinding.__dataclass_fields__).isdisjoint(forbidden),
    )
    check(
        "FTF-1506",
        "DependencyScanEvidence's own fields never include a raw body/secret",
        set(DependencyScanEvidence.__dataclass_fields__).isdisjoint(forbidden),
    )


# ---------------------------------------------------------------------------
# Real lockfile parsing -- this repo's own committed uv.lock/pnpm-lock.yaml,
# no network required.
# ---------------------------------------------------------------------------


def test_parse_uv_lock_reads_this_repos_real_lockfile() -> None:
    deps = parse_uv_lock()
    names = {d.name for d in deps}
    check(
        "FTF-1506",
        f"uv.lock parses to a real, non-trivial set of pinned PyPI packages, got {len(deps)}",
        len(deps) > 10,
    )
    check(
        "FTF-1506",
        "every parsed dependency is PyPI-ecosystem with a non-empty version",
        all(d.ecosystem == "PyPI" and d.version for d in deps),
    )
    check(
        "FTF-1506",
        "a real, known pinned dependency (pyyaml) is present",
        "pyyaml" in names,
    )
    check(
        "FTF-1506",
        "no (name, version) pair is duplicated",
        len(deps) == len({(d.name, d.version) for d in deps}),
    )


def test_parse_pnpm_lock_reads_this_repos_real_lockfile() -> None:
    deps = parse_pnpm_lock()
    by_name = {d.name: d.version for d in deps}
    check(
        "FTF-1506",
        f"pnpm-lock.yaml parses to a real, non-trivial set of pinned npm packages, got {len(deps)}",
        len(deps) > 10,
    )
    check(
        "FTF-1506",
        "every parsed dependency is npm-ecosystem with a non-empty version",
        all(d.ecosystem == "npm" and d.version for d in deps),
    )
    check(
        "FTF-1506",
        "a real, known pinned dependency (react) is present with its real major version",
        by_name.get("react", "").startswith("18."),
    )
    check(
        "FTF-1506",
        "a real scoped dependency (@testing-library/react) is parsed correctly, "
        "with the scope kept as part of the package name, not dropped",
        "@testing-library/react" in by_name,
    )
    check(
        "FTF-1506",
        "no (name, version) pair is duplicated",
        len(deps) == len({(d.name, d.version) for d in deps}),
    )


def test_load_policy_exceptions_default_policy_is_empty() -> None:
    exceptions = load_policy_exceptions()
    check(
        "FTF-1506",
        "this repo's committed default policy has zero exceptions -- nothing "
        "is silently pre-excused",
        exceptions == [],
    )


def main() -> None:
    test_cvss_v3_matches_known_published_reference_score()
    test_cvss_v3_bucket_boundaries()
    test_cvss_v3_rejects_unparseable_vectors()
    test_severity_prefers_database_specific_over_cvss()
    test_severity_falls_back_to_cvss_vector()
    test_severity_unknown_when_neither_source_present()
    test_fixed_versions_scoped_to_the_matching_package()
    test_evaluate_refuses_zero_dependencies()
    test_evaluate_blocks_an_unpatched_critical_finding_with_no_exception()
    test_evaluate_never_blocks_on_medium_or_low_severity()
    test_evaluate_blocks_unknown_severity_by_default()
    test_evaluate_honors_a_valid_matching_exception()
    test_evaluate_expired_exception_still_blocks()
    test_evaluate_unjustified_exception_still_blocks()
    test_evaluate_exception_does_not_cover_a_different_package_or_vuln()
    test_evidence_is_content_free_by_construction()
    test_parse_uv_lock_reads_this_repos_real_lockfile()
    test_parse_pnpm_lock_reads_this_repos_real_lockfile()
    test_load_policy_exceptions_default_policy_is_empty()

    if FAILURES:
        print(f"FTF-1506 dependency scanning (no-network) tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1506 dependency scanning (no-network) tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
