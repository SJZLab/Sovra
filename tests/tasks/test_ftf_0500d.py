#!/usr/bin/env python3
"""FTF-0500D -- Dataset source security-boundary preview + audit evidence.

Runnable without any database and without any external network egress:
`build_dataset_source_copy_preview`/`authorize_dataset_source_copy` are
pure, synchronous, no-I/O functions (this task's own `database_changes`
clause -- "No independent schema change. Consume frozen contract.").

This task's own acceptance clause ("Display source classification,
bytes/records, boundary, retention, transformations and purpose before
copying") is covered by `test_preview_renders_section_7a10_field_list`
(the positive path across every Section 7A.10 field) and
`test_retention_policy_status_is_honest_not_configured`.

This task's own negative_acceptance ("UI warning alone cannot allow
forbidden egress; backend checks grant and destination again at import")
is covered by `test_authorize_raises_on_purpose_denied`,
`test_authorize_raises_on_security_boundary_crossing`,
`test_authorize_raises_on_both_failures_simultaneously`, and
`test_authorize_does_not_read_a_previously_built_preview` (the independent
re-derivation this negative_acceptance names literally).

2026-10-01 L2 review finding, fixed here: narrowing the real boundary
check inside `authorize_dataset_source_copy` to a literal
`security_classification == "restricted"` test (instead of the real
`would_cross_security_boundary(effective_profile, security_classification)`
rank comparison) left the whole suite above green, because every
existing boundary-crossing test used `restricted` paired with
`standard` -- the one classification that check still happens to catch.
A confidential source into a standard project would have been silently
authorized despite `preview.blocked=True`, with no test to notice.
`test_authorize_raises_whenever_preview_blocks_across_classification_profile_matrix`
closes this: every `security_classification` x `security_profile`
pairing (16 combinations) is checked, not just the one pairing the
original suite happened to exercise.
`test_fault_injection_narrowed_classification_check_is_caught_by_the_matrix_test`
reproduces the finding directly against the real on-disk module in an
isolated subprocess (so this test process's own already-imported
functions/exception classes are never touched by the mutation): it
narrows the real module's boundary check exactly as the review
described, confirms the original single-case test is genuinely blind to
it, confirms the new matrix check genuinely catches it, then restores
the module file byte-identical.

2026-10-04T11:00Z L2 review finding, fixed here: the matrix test above
only ever held `organization_default_security_profile ==
project_security_profile` (so `resolve_effective_project_security_profile`'s
own org/project-divergence branch was never exercised by a
boundary-crossing assertion) and only ever used a single, always-granted
purpose (`"training"`) -- the review's own words, "an early return when
org != project, or when purpose == 'validation', stays GREEN."
`test_authorize_raises_whenever_preview_blocks_across_full_matrix` closes
this: every `security_classification` x `organization_default_security_
profile` x `project_security_profile` x `connection_status` x grant x
`requested_purpose` combination is checked -- the review's own prose cited
"1,440 cases", but the module's real, already-closed enums multiply out to
4 x 3 x 3 x 2 x 2 x 5 = 720, not 1,440 (fixed 2026-10-06T13:45Z round-3).
`test_fault_injection_additive_org_project_divergence_early_return`
reproduces the finding directly against the real on-disk module in an
isolated subprocess: it adds the exact additive early-return fault the
review named (skip both checks whenever org != project), confirms the
original, narrower classification x profile matrix (which always held
org == project) is genuinely blind to it, confirms the new full matrix
genuinely catches it, then restores the module file byte-identical.

2026-10-06T13:45Z L2 review finding (round 3), fixed here: every matrix
test above (including the full 720-case one) compares `preview.blocked`
against whether `authorize_dataset_source_copy` raises -- but both sides
call the SAME shared helpers (`would_cross_security_boundary`,
`check_purpose_authorized`), so an additive bug inside either helper (a
hardcoded `return False` for `restricted`+`zero_egress`, or an additive
purpose/status allow) makes both sides wrong identically and the
self-referential comparison still passes.
`test_independent_oracle_boundary_crossing_classification_profile_matrix`
and `test_independent_oracle_purpose_authorization_status_grant_purpose_matrix`
close this: both build an expected value from a literal table written in
this test file (never calling the production module's own
`would_cross_security_boundary`/`check_purpose_authorized`/
`resolve_effective_project_security_profile`/`security_profile_rank`), and
assert it against both `preview` and `authorize`.
`test_fault_injection_boundary_check_additive_bypass_caught_by_independent_oracle`
and `test_fault_injection_purpose_check_additive_bypass_caught_by_independent_oracle`
inject exactly the two additive-allow fault classes the round-3 review
named, directly into the real on-disk module in an isolated subprocess,
and confirm the independent oracle (unlike the self-referential matrix
tests) genuinely catches each one, then restore the module file
byte-identical.

Run directly: python3 tests/tasks/test_ftf_0500d.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.dataset_source_security_boundary_preview_audit_evidence import (  # noqa: E402
    MINIMUM_REQUIRED_SECURITY_PROFILE,
    RETENTION_POLICY_STATUS,
    SUPPORTED_PROJECT_SECURITY_PROFILES,
    SUPPORTED_PURPOSES,
    SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS,
    DatasetSourceCopyAuthorizationError,
    DatasetSourceCopyPreview,
    DatasetSourceSecurityBoundaryError,
    SecurityBoundaryCrossingError,
    SourceCopyManifest,
    authorize_dataset_source_copy,
    build_dataset_source_copy_preview,
    resolve_effective_project_security_profile,
    would_cross_security_boundary,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _manifest(**overrides: object) -> SourceCopyManifest:
    defaults: dict[str, object] = {
        "estimated_bytes": 4096,
        "estimated_records": 10,
        "expected_transformations": ("lowercase_normalize",),
    }
    defaults.update(overrides)
    return SourceCopyManifest(**defaults)  # type: ignore[arg-type]


def _preview(**overrides: object) -> DatasetSourceCopyPreview:
    defaults: dict[str, object] = {
        "source_display_name": "Support tickets export",
        "source_type": "upload",
        "security_classification": "public",
        "connection_status": "active",
        "allowed_purposes": ["training"],
        "requested_purpose": "training",
        "organization_default_security_profile": "standard",
        "project_security_profile": "standard",
        "manifest": _manifest(),
    }
    defaults.update(overrides)
    return build_dataset_source_copy_preview(**defaults)  # type: ignore[arg-type]


def _authorize(**overrides: object) -> None:
    defaults: dict[str, object] = {
        "security_classification": "public",
        "connection_status": "active",
        "allowed_purposes": ["training"],
        "requested_purpose": "training",
        "organization_default_security_profile": "standard",
        "project_security_profile": "standard",
    }
    defaults.update(overrides)
    authorize_dataset_source_copy(**defaults)  # type: ignore[arg-type]


def test_preview_renders_section_7a10_field_list() -> None:
    preview = _preview(
        source_display_name="Support tickets export",
        source_type="s3",
        manifest=_manifest(estimated_bytes=123456, estimated_records=42),
    )
    check(
        "FTF-0500D",
        "preview carries source_display_name",
        preview.source_display_name == "Support tickets export",
    )
    check("FTF-0500D", "preview carries source_type", preview.source_type == "s3")
    check(
        "FTF-0500D",
        "preview carries security_classification",
        preview.security_classification == "public",
    )
    check("FTF-0500D", "preview carries requested_purpose", preview.requested_purpose == "training")
    check("FTF-0500D", "preview carries estimated_bytes", preview.estimated_bytes == 123456)
    check("FTF-0500D", "preview carries estimated_records", preview.estimated_records == 42)
    check(
        "FTF-0500D",
        "preview carries expected_transformations",
        preview.expected_transformations == ("lowercase_normalize",),
    )
    check(
        "FTF-0500D",
        "preview carries retention_policy_status",
        preview.retention_policy_status == RETENTION_POLICY_STATUS,
    )
    check(
        "FTF-0500D",
        "preview carries whether data would leave its security boundary",
        preview.would_cross_security_boundary is False,
    )
    check(
        "FTF-0500D", "an authorized, in-boundary preview is not blocked", preview.blocked is False
    )
    check(
        "FTF-0500D",
        "an authorized preview reports blocking_reasons empty",
        preview.blocking_reasons == (),
    )


def test_retention_policy_status_is_honest_not_configured() -> None:
    check(
        "FTF-0500D",
        "retention_policy_status is a single honest sentinel, never a fabricated policy value",
        RETENTION_POLICY_STATUS == "not_yet_configured",
    )
    preview_public = _preview(security_classification="public")
    preview_restricted = _preview(
        security_classification="restricted",
        project_security_profile="air_gapped",
        organization_default_security_profile="air_gapped",
    )
    check(
        "FTF-0500D",
        "retention_policy_status never varies by classification -- no fabricated per-tier policy",
        preview_public.retention_policy_status == preview_restricted.retention_policy_status,
    )


def test_preview_marks_blocked_when_purpose_not_authorized() -> None:
    preview = _preview(allowed_purposes=["validation"], requested_purpose="training")
    check(
        "FTF-0500D",
        "purpose not granted marks purpose_authorized False",
        preview.purpose_authorized is False,
    )
    check("FTF-0500D", "purpose not granted marks the preview blocked", preview.blocked is True)
    check(
        "FTF-0500D",
        "blocking_reasons names the purpose denial",
        any("training" in reason for reason in preview.blocking_reasons),
    )


def test_preview_marks_blocked_when_connection_revoked() -> None:
    preview = _preview(connection_status="revoked")
    check(
        "FTF-0500D",
        "a revoked connection marks purpose_authorized False",
        preview.purpose_authorized is False,
    )
    check("FTF-0500D", "a revoked connection marks the preview blocked", preview.blocked is True)


def test_preview_marks_blocked_when_security_boundary_crossed() -> None:
    preview = _preview(
        security_classification="restricted",
        project_security_profile="standard",
        organization_default_security_profile="standard",
    )
    check(
        "FTF-0500D",
        "restricted data into a standard project crosses the boundary",
        preview.would_cross_security_boundary is True,
    )
    check("FTF-0500D", "a boundary-crossing preview is blocked", preview.blocked is True)
    check(
        "FTF-0500D",
        "blocking_reasons names the boundary crossing",
        any("security boundary" in reason for reason in preview.blocking_reasons),
    )


def test_preview_not_blocked_when_classification_matches_profile() -> None:
    preview = _preview(
        security_classification="restricted",
        project_security_profile="air_gapped",
        organization_default_security_profile="air_gapped",
    )
    check(
        "FTF-0500D",
        "restricted data into an air_gapped project does not cross the boundary",
        preview.would_cross_security_boundary is False,
    )
    check("FTF-0500D", "a fully compliant preview is not blocked", preview.blocked is False)


def test_blocking_reasons_name_both_failures_when_both_present() -> None:
    preview = _preview(
        connection_status="revoked",
        security_classification="restricted",
        project_security_profile="standard",
        organization_default_security_profile="standard",
    )
    check(
        "FTF-0500D",
        "both a purpose denial and a boundary crossing are both surfaced",
        len(preview.blocking_reasons) == 2,
    )


def test_effective_profile_is_more_restrictive_of_org_and_project() -> None:
    effective = resolve_effective_project_security_profile("zero_egress", "standard")
    check(
        "FTF-0500D",
        "the effective profile is the more restrictive of org default and project choice",
        effective == "zero_egress",
    )


def test_effective_profile_project_can_be_stricter_than_org() -> None:
    effective = resolve_effective_project_security_profile("standard", "air_gapped")
    check(
        "FTF-0500D",
        "a project may choose a stricter profile than its org's own floor",
        effective == "air_gapped",
    )


def test_required_minimum_security_profile_mapping_is_closed_and_documented() -> None:
    check(
        "FTF-0500D",
        "the classification->profile mapping covers exactly the four disclosed classifications",
        set(MINIMUM_REQUIRED_SECURITY_PROFILE)
        == {"public", "internal", "confidential", "restricted"},
    )
    check(
        "FTF-0500D",
        "public requires only standard",
        MINIMUM_REQUIRED_SECURITY_PROFILE["public"] == "standard",
    )
    check(
        "FTF-0500D",
        "internal requires only standard",
        MINIMUM_REQUIRED_SECURITY_PROFILE["internal"] == "standard",
    )
    check(
        "FTF-0500D",
        "confidential requires at least zero_egress",
        MINIMUM_REQUIRED_SECURITY_PROFILE["confidential"] == "zero_egress",
    )
    check(
        "FTF-0500D",
        "restricted requires air_gapped",
        MINIMUM_REQUIRED_SECURITY_PROFILE["restricted"] == "air_gapped",
    )


def test_would_cross_security_boundary_is_the_real_check_the_preview_reuses() -> None:
    check(
        "FTF-0500D",
        "confidential data into a standard project crosses the boundary",
        would_cross_security_boundary("standard", "confidential") is True,
    )
    check(
        "FTF-0500D",
        "confidential data into a zero_egress project does not cross the boundary",
        would_cross_security_boundary("zero_egress", "confidential") is False,
    )


def test_supported_purposes_mirrors_ftf_0500_vocabulary() -> None:
    check(
        "FTF-0500D",
        "this module's own mirrored purpose vocabulary matches FTF-0500's closed set exactly",
        set(SUPPORTED_PURPOSES)
        == {
            "training",
            "validation",
            "held_out_evaluation",
            "candidate_development",
            "baseline_only",
        },
    )


def test_rejects_unknown_security_classification() -> None:
    try:
        _preview(security_classification="top_secret")
    except DatasetSourceSecurityBoundaryError:
        pass
    else:
        check("FTF-0500D", "an unrecognized security_classification is rejected", False)


def test_rejects_unknown_project_security_profile() -> None:
    try:
        _preview(project_security_profile="fort_knox")
    except DatasetSourceSecurityBoundaryError:
        pass
    else:
        check("FTF-0500D", "an unrecognized project security profile is rejected", False)


def test_rejects_unknown_purpose() -> None:
    try:
        _preview(requested_purpose="production_inference")
    except DatasetSourceSecurityBoundaryError:
        pass
    else:
        check("FTF-0500D", "an unrecognized purpose is rejected", False)


def test_rejects_negative_estimated_bytes() -> None:
    try:
        _manifest(estimated_bytes=-1)
    except DatasetSourceSecurityBoundaryError:
        pass
    else:
        check("FTF-0500D", "a negative estimated_bytes is rejected", False)


def test_rejects_bool_estimated_records() -> None:
    try:
        _manifest(estimated_records=True)
    except DatasetSourceSecurityBoundaryError:
        pass
    else:
        check(
            "FTF-0500D", "a bool is never silently accepted as an int for estimated_records", False
        )


def test_rejects_non_string_transformation_entries() -> None:
    try:
        _manifest(expected_transformations=(1, 2))
    except DatasetSourceSecurityBoundaryError:
        pass
    else:
        check("FTF-0500D", "a non-string transformation entry is rejected", False)


def test_empty_transformations_is_a_legitimate_state() -> None:
    manifest = _manifest(expected_transformations=())
    check(
        "FTF-0500D",
        "an empty expected_transformations tuple is a legitimate real state, not an error",
        manifest.expected_transformations == (),
    )


def test_authorize_raises_on_purpose_denied() -> None:
    try:
        _authorize(allowed_purposes=["validation"], requested_purpose="training")
    except DatasetSourceCopyAuthorizationError:
        pass
    else:
        check("FTF-0500D", "authorize_dataset_source_copy raises on an unauthorized purpose", False)


def test_authorize_raises_on_revoked_connection() -> None:
    try:
        _authorize(connection_status="revoked")
    except DatasetSourceCopyAuthorizationError:
        pass
    else:
        check("FTF-0500D", "authorize_dataset_source_copy raises on a revoked connection", False)


def test_authorize_raises_on_security_boundary_crossing() -> None:
    try:
        _authorize(
            security_classification="restricted",
            project_security_profile="standard",
            organization_default_security_profile="standard",
        )
    except SecurityBoundaryCrossingError:
        pass
    else:
        check(
            "FTF-0500D",
            "authorize_dataset_source_copy raises on a security boundary crossing",
            False,
        )


def test_authorize_raises_on_both_failures_simultaneously() -> None:
    # The purpose check runs first; either real failure must still raise,
    # never silently pass because the other check would also have failed.
    try:
        _authorize(
            connection_status="revoked",
            security_classification="restricted",
            project_security_profile="standard",
            organization_default_security_profile="standard",
        )
    except (DatasetSourceCopyAuthorizationError, SecurityBoundaryCrossingError):
        pass
    else:
        check(
            "FTF-0500D", "authorize_dataset_source_copy raises when both checks would fail", False
        )


def test_authorize_succeeds_when_both_checks_pass() -> None:
    try:
        _authorize()
    except Exception as exc:  # noqa: BLE001 - this path must not raise at all
        check(
            "FTF-0500D",
            f"authorize_dataset_source_copy must not raise when compliant, got {exc!r}",
            False,
        )


def test_authorize_does_not_read_a_previously_built_preview() -> None:
    # This task's own negative_acceptance in full: "UI warning alone cannot
    # allow forbidden egress; backend checks grant and destination again at
    # import." A preview showing blocked=False must never be treated as an
    # authorization -- authorize_dataset_source_copy takes no preview
    # object at all and independently re-derives both checks from its own
    # arguments every time it is called.
    compliant_preview = _preview()
    check(
        "FTF-0500D",
        "sanity: the compliant preview is indeed not blocked",
        compliant_preview.blocked is False,
    )
    try:
        _authorize(allowed_purposes=["validation"], requested_purpose="training")
    except DatasetSourceCopyAuthorizationError:
        pass
    else:
        check(
            "FTF-0500D",
            "a denial for different inputs still raises even though an unrelated preview passed",
            False,
        )


def _classification_profile_matrix_violations() -> list[str]:
    """For every `security_classification` x `security_profile` pairing
    (org default == project profile, so the effective profile is just
    that profile) -- with purpose/connection held compliant, so only the
    boundary dimension is exercised -- assert that
    `authorize_dataset_source_copy` raises `SecurityBoundaryCrossingError`
    exactly when the independently-computed preview's own
    `would_cross_security_boundary` says it should. Returns a list of
    human-readable violation descriptions (empty when the invariant holds
    everywhere)."""
    violations: list[str] = []
    for classification in SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS:
        for profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
            preview = build_dataset_source_copy_preview(
                source_display_name="matrix",
                source_type="upload",
                security_classification=classification,
                connection_status="active",
                allowed_purposes=["training"],
                requested_purpose="training",
                organization_default_security_profile=profile,
                project_security_profile=profile,
                manifest=_manifest(),
            )
            raised = False
            try:
                authorize_dataset_source_copy(
                    security_classification=classification,
                    connection_status="active",
                    allowed_purposes=["training"],
                    requested_purpose="training",
                    organization_default_security_profile=profile,
                    project_security_profile=profile,
                )
            except SecurityBoundaryCrossingError:
                raised = True
            if preview.would_cross_security_boundary != raised:
                violations.append(
                    f"classification={classification!r} profile={profile!r}: "
                    f"preview.would_cross_security_boundary="
                    f"{preview.would_cross_security_boundary} but authorize raised={raised}"
                )
    return violations


def test_authorize_raises_whenever_preview_blocks_across_classification_profile_matrix() -> None:
    violations = _classification_profile_matrix_violations()
    check(
        "FTF-0500D",
        "authorize_dataset_source_copy raises SecurityBoundaryCrossingError in exactly the "
        f"(classification, profile) pairs the preview marks would_cross_security_boundary -- "
        f"violations: {violations}",
        violations == [],
    )


_MATRIX_FAULT_INJECTION_CHECK_SCRIPT = """
import sys
sys.path.insert(0, "__ROOT__")
from workers.data.dataset_source_security_boundary_preview_audit_evidence import (
    SUPPORTED_PROJECT_SECURITY_PROFILES,
    SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS,
    SecurityBoundaryCrossingError,
    SourceCopyManifest,
    authorize_dataset_source_copy,
    build_dataset_source_copy_preview,
)

violations = []
for classification in SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS:
    for profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
        preview = build_dataset_source_copy_preview(
            source_display_name="matrix",
            source_type="upload",
            security_classification=classification,
            connection_status="active",
            allowed_purposes=["training"],
            requested_purpose="training",
            organization_default_security_profile=profile,
            project_security_profile=profile,
            manifest=SourceCopyManifest(
                estimated_bytes=1, estimated_records=1, expected_transformations=()
            ),
        )
        raised = False
        try:
            authorize_dataset_source_copy(
                security_classification=classification,
                connection_status="active",
                allowed_purposes=["training"],
                requested_purpose="training",
                organization_default_security_profile=profile,
                project_security_profile=profile,
            )
        except SecurityBoundaryCrossingError:
            raised = True
        if preview.would_cross_security_boundary != raised:
            violations.append(f"{classification}:{profile}")
print("VIOLATIONS=" + ",".join(violations))
"""


def _run_matrix_check_subprocess() -> list[str]:
    """Runs the exact same matrix invariant as
    `_classification_profile_matrix_violations` but as a fresh, isolated
    subprocess that imports the real on-disk module from scratch -- used
    by the fault-injection test below so a temporary on-disk mutation of
    the production module never touches this test process's own already
    -imported functions/exception classes."""
    script = _MATRIX_FAULT_INJECTION_CHECK_SCRIPT.replace("__ROOT__", str(ROOT))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"matrix check subprocess failed (exit {result.returncode}): {result.stderr}"
        )
    for line in result.stdout.splitlines():
        if line.startswith("VIOLATIONS="):
            payload = line[len("VIOLATIONS=") :]
            return [item for item in payload.split(",") if item]
    raise RuntimeError(f"matrix check subprocess produced no VIOLATIONS= line: {result.stdout!r}")


def test_fault_injection_narrowed_classification_check_is_caught_by_the_matrix_test() -> None:
    import workers.data.dataset_source_security_boundary_preview_audit_evidence as _module

    module_path = Path(_module.__file__)
    original_bytes = module_path.read_bytes()
    original_hash = hashlib.md5(original_bytes).hexdigest()

    target_line = (
        "    if would_cross_security_boundary(effective_profile, security_classification):"
    )
    narrowed_line = '    if security_classification == "restricted":'
    original_text = original_bytes.decode("utf-8")
    check(
        "FTF-0500D",
        "the exact boundary-check line this fault injection narrows exists exactly once in the "
        "real on-disk module before injection",
        original_text.count(target_line) == 1,
    )
    mutated_text = original_text.replace(target_line, narrowed_line, 1)

    try:
        module_path.write_text(mutated_text, encoding="utf-8")

        narrowed_violations = _run_matrix_check_subprocess()
        check(
            "FTF-0500D",
            "[L2 finding, reproduced] the original suite's own single restricted+standard case "
            "alone would still pass under the injected classification=='restricted' narrowing "
            "(restricted always raises under the narrowed check, masking the real gap)",
            "restricted:standard" not in narrowed_violations,
        )
        check(
            "FTF-0500D",
            "[L2 finding, reproduced] the classification x profile matrix test genuinely catches "
            f"the injected narrowing (a confidential source into a standard project is silently "
            f"authorized despite the preview blocking it); violations found: {narrowed_violations}",
            "confidential:standard" in narrowed_violations,
        )
    finally:
        module_path.write_bytes(original_bytes)
        restored_hash = hashlib.md5(module_path.read_bytes()).hexdigest()
        check(
            "FTF-0500D",
            "the module file is restored byte-identical after fault injection",
            restored_hash == original_hash,
        )

    clean_violations = _run_matrix_check_subprocess()
    check(
        "FTF-0500D",
        f"the matrix check is clean again once the fault is reverted; "
        f"violations: {clean_violations}",
        clean_violations == [],
    )


def _allowed_purposes_for(requested_purpose: str, granted: bool) -> list[str]:
    """Either exactly the requested purpose (granted) or every other
    supported purpose (not granted, and never including the requested
    one) -- holds the grant dimension independent of which purpose is
    requested."""
    if granted:
        return [requested_purpose]
    return [purpose for purpose in SUPPORTED_PURPOSES if purpose != requested_purpose]


def _full_matrix_violations() -> tuple[list[str], int]:
    """Full classification x org-default x project x connection_status x
    grant x requested_purpose matrix. The 2026-10-04T11:00Z L2 review's
    own prose cited "1,440 cases"; the module's real, already-closed
    enums multiply out to 4 x 3 x 3 x 2 x 2 x 5 = 720, not 1,440 -- fixed
    here (2026-10-06T13:45Z round-3 finding) rather than left stale.
    `_classification_profile_matrix_violations` above only ever
    held org_default == project (so `resolve_effective_project_security_
    profile`'s own divergence branch was never exercised by a
    boundary-crossing assertion) and only ever used a single,
    always-granted purpose -- the exact two narrowing gaps the
    2026-10-04T11:00Z L2 review named literally. This varies all six
    dimensions independently and asserts, for every combination, that
    `authorize_dataset_source_copy` raises exactly when the
    independently built preview's own `blocked` flag says it should.
    Returns `(violations, executed_cases)` -- `executed_cases` is an
    actual count of loop-body executions, not a separately computed
    multiplication, per the round-3 DoD's "count actual loop
    iterations"."""
    violations: list[str] = []
    executed_cases = 0
    for classification in SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS:
        for org_profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
            for project_profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
                for connection_status in ("active", "revoked"):
                    for requested_purpose in SUPPORTED_PURPOSES:
                        for granted in (True, False):
                            executed_cases += 1
                            allowed_purposes = _allowed_purposes_for(requested_purpose, granted)
                            preview = build_dataset_source_copy_preview(
                                source_display_name="matrix",
                                source_type="upload",
                                security_classification=classification,
                                connection_status=connection_status,
                                allowed_purposes=allowed_purposes,
                                requested_purpose=requested_purpose,
                                organization_default_security_profile=org_profile,
                                project_security_profile=project_profile,
                                manifest=_manifest(),
                            )
                            raised = False
                            try:
                                authorize_dataset_source_copy(
                                    security_classification=classification,
                                    connection_status=connection_status,
                                    allowed_purposes=allowed_purposes,
                                    requested_purpose=requested_purpose,
                                    organization_default_security_profile=org_profile,
                                    project_security_profile=project_profile,
                                )
                            except (
                                DatasetSourceCopyAuthorizationError,
                                SecurityBoundaryCrossingError,
                            ):
                                raised = True
                            if preview.blocked != raised:
                                violations.append(
                                    f"classification={classification!r} "
                                    f"org={org_profile!r} project={project_profile!r} "
                                    f"status={connection_status!r} "
                                    f"purpose={requested_purpose!r} granted={granted}: "
                                    f"preview.blocked={preview.blocked} but "
                                    f"authorize raised={raised}"
                                )
    return violations, executed_cases


def test_full_matrix_enumerates_every_real_value_of_every_dimension() -> None:
    # The 2026-10-04T11:00Z review's own prose cited "1,440 cases"; the
    # module's real, already-closed enums (4 classifications x 3 org
    # profiles x 3 project profiles x 2 connection statuses x 2 grant
    # states x 5 purposes) multiply out to 4*3*3*2*2*5 = 720, not 1,440 --
    # disclosed here rather than silently padded to match the review's own
    # prose number. What matters, and what this asserts, is that every
    # real value of every one of the six named dimensions is covered at
    # least once with every other dimension held fixed (true full-factorial
    # coverage, not a narrower subset) -- the actual gap the review named.
    case_count = (
        len(SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS)
        * len(SUPPORTED_PROJECT_SECURITY_PROFILES)
        * len(SUPPORTED_PROJECT_SECURITY_PROFILES)
        * 2  # connection_status: active, revoked
        * len(SUPPORTED_PURPOSES)
        * 2  # grant: granted, not granted
    )
    check(
        "FTF-0500D",
        "the full matrix's own six dimensions multiply out to the real full-factorial case "
        f"count across every closed enum this module defines (4x3x3x2x2x5): {case_count}",
        case_count == 720,
    )
    check(
        "FTF-0500D",
        "every dimension's own real value count matches this module's closed vocabularies "
        "exactly (4 classifications, 3 security profiles, 5 purposes)",
        len(SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS) == 4
        and len(SUPPORTED_PROJECT_SECURITY_PROFILES) == 3
        and len(SUPPORTED_PURPOSES) == 5,
    )
    # Round-3 DoD: "count actual loop iterations", not only a separately
    # computed multiplication that could silently drift from what the loop
    # actually runs (e.g. a dimension accidentally iterated over a stale
    # or truncated collection). `executed_cases` is incremented once per
    # real loop-body execution inside `_full_matrix_violations`.
    _, executed_cases = _full_matrix_violations()
    check(
        "FTF-0500D",
        f"the full matrix's loop body actually executed exactly 720 times (not merely "
        f"computed by multiplication): executed_cases={executed_cases}",
        executed_cases == 720 and executed_cases == case_count,
    )


def test_authorize_raises_whenever_preview_blocks_across_full_matrix() -> None:
    violations, executed_cases = _full_matrix_violations()
    shown = violations[:5]
    more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
    check(
        "FTF-0500D",
        "authorize_dataset_source_copy raises in exactly the (classification, org_default, "
        "project, connection_status, grant, purpose) combinations the preview marks blocked, "
        f"across the full 720-case matrix (executed_cases={executed_cases}) -- "
        f"violations: {shown}{more}",
        violations == [] and executed_cases == 720,
    )


_FULL_MATRIX_FAULT_INJECTION_CHECK_SCRIPT = """
import sys
sys.path.insert(0, "__ROOT__")
from workers.data.dataset_source_security_boundary_preview_audit_evidence import (
    SUPPORTED_PROJECT_SECURITY_PROFILES,
    SUPPORTED_PURPOSES,
    SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS,
    DatasetSourceCopyAuthorizationError,
    SecurityBoundaryCrossingError,
    SourceCopyManifest,
    authorize_dataset_source_copy,
    build_dataset_source_copy_preview,
)

manifest = SourceCopyManifest(estimated_bytes=1, estimated_records=1, expected_transformations=())


def allowed_purposes_for(requested_purpose, granted):
    if granted:
        return [requested_purpose]
    return [p for p in SUPPORTED_PURPOSES if p != requested_purpose]


# The original, narrower matrix -- always org_profile == project_profile,
# always purpose="training" granted. Used to show this narrower check
# stays blind to the injected fault.
narrow_violations = []
for classification in SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS:
    for profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
        preview = build_dataset_source_copy_preview(
            source_display_name="matrix",
            source_type="upload",
            security_classification=classification,
            connection_status="active",
            allowed_purposes=["training"],
            requested_purpose="training",
            organization_default_security_profile=profile,
            project_security_profile=profile,
            manifest=manifest,
        )
        raised = False
        try:
            authorize_dataset_source_copy(
                security_classification=classification,
                connection_status="active",
                allowed_purposes=["training"],
                requested_purpose="training",
                organization_default_security_profile=profile,
                project_security_profile=profile,
            )
        except SecurityBoundaryCrossingError:
            raised = True
        if preview.would_cross_security_boundary != raised:
            narrow_violations.append(f"{classification}:{profile}")

# The full 720-case matrix.
full_violations = []
for classification in SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS:
    for org_profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
        for project_profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
            for connection_status in ("active", "revoked"):
                for requested_purpose in SUPPORTED_PURPOSES:
                    for granted in (True, False):
                        allowed_purposes = allowed_purposes_for(requested_purpose, granted)
                        preview = build_dataset_source_copy_preview(
                            source_display_name="matrix",
                            source_type="upload",
                            security_classification=classification,
                            connection_status=connection_status,
                            allowed_purposes=allowed_purposes,
                            requested_purpose=requested_purpose,
                            organization_default_security_profile=org_profile,
                            project_security_profile=project_profile,
                            manifest=manifest,
                        )
                        raised = False
                        try:
                            authorize_dataset_source_copy(
                                security_classification=classification,
                                connection_status=connection_status,
                                allowed_purposes=allowed_purposes,
                                requested_purpose=requested_purpose,
                                organization_default_security_profile=org_profile,
                                project_security_profile=project_profile,
                            )
                        except (DatasetSourceCopyAuthorizationError, SecurityBoundaryCrossingError):
                            raised = True
                        if preview.blocked != raised:
                            full_violations.append(
                                f"{classification}:{org_profile}:{project_profile}:"
                                f"{connection_status}:{requested_purpose}:{granted}"
                            )

print("NARROW=" + str(len(narrow_violations)))
print("FULL=" + str(len(full_violations)))
"""


def _run_full_matrix_fault_script() -> tuple[int, int]:
    script = _FULL_MATRIX_FAULT_INJECTION_CHECK_SCRIPT.replace("__ROOT__", str(ROOT))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"full-matrix fault script failed (exit {result.returncode}): {result.stderr}"
        )
    narrow_count: int | None = None
    full_count: int | None = None
    for line in result.stdout.splitlines():
        if line.startswith("NARROW="):
            narrow_count = int(line[len("NARROW=") :])
        elif line.startswith("FULL="):
            full_count = int(line[len("FULL=") :])
    if narrow_count is None or full_count is None:
        raise RuntimeError(
            f"full-matrix fault script produced no NARROW=/FULL= line: {result.stdout!r}"
        )
    return narrow_count, full_count


def test_fault_injection_additive_org_project_divergence_early_return() -> None:
    import workers.data.dataset_source_security_boundary_preview_audit_evidence as _module

    module_path = Path(_module.__file__)
    original_bytes = module_path.read_bytes()
    original_hash = hashlib.md5(original_bytes).hexdigest()
    original_text = original_bytes.decode("utf-8")

    anchor = (
        "    check_purpose_authorized(connection_status, allowed_purposes, requested_purpose)\n"
        "\n"
        "    effective_profile = resolve_effective_project_security_profile(\n"
        "        organization_default_security_profile, project_security_profile\n"
        "    )"
    )
    check(
        "FTF-0500D",
        "the exact anchor this additive-fault injection targets exists exactly once in the "
        "real on-disk module before injection (authorize_dataset_source_copy's own "
        "unwrapped check_purpose_authorized call, distinct from the preview function's own "
        "try/except-wrapped call to the same helper)",
        original_text.count(anchor) == 1,
    )
    # Additive fault: a brand-new early return, not an edit of an existing
    # line -- exactly the class of fault this review round's own new DoD
    # rule requires a test for.
    mutated_anchor = (
        "    if organization_default_security_profile != project_security_profile:\n"
        "        return\n"
    ) + anchor
    mutated_text = original_text.replace(anchor, mutated_anchor, 1)

    try:
        module_path.write_text(mutated_text, encoding="utf-8")

        narrow_count, full_count = _run_full_matrix_fault_script()
        check(
            "FTF-0500D",
            "[L2 finding, reproduced] the original, narrower classification x profile matrix "
            "(which always holds org_profile == project_profile) stays genuinely blind to the "
            f"injected org != project early-return fault; narrow violations found: {narrow_count}",
            narrow_count == 0,
        )
        check(
            "FTF-0500D",
            "[L2 finding, reproduced] the full 720-case matrix genuinely catches the injected "
            f"org != project early-return fault; full-matrix violations found: {full_count}",
            full_count > 0,
        )
    finally:
        module_path.write_bytes(original_bytes)
        restored_hash = hashlib.md5(module_path.read_bytes()).hexdigest()
        check(
            "FTF-0500D",
            "the module file is restored byte-identical after fault injection",
            restored_hash == original_hash,
        )

    _, clean_full_count = _run_full_matrix_fault_script()
    check(
        "FTF-0500D",
        f"the full matrix check is clean again once the fault is reverted; "
        f"violations: {clean_full_count}",
        clean_full_count == 0,
    )


# ---------------------------------------------------------------------------
# 2026-10-06T13:45Z L2 review finding (round 3): every matrix test above
# compares `preview.blocked`/`preview.would_cross_security_boundary`/
# `preview.purpose_authorized` against whether `authorize_dataset_source_
# copy` raises -- but both sides call the SAME shared helpers
# (`would_cross_security_boundary`, `check_purpose_authorized`), so an
# additive bug inside either helper makes both sides wrong identically and
# the self-referential comparison still passes. Example named by the
# review: an additive `return False` for `restricted`+`zero_egress` in
# `would_cross_security_boundary` would AUTHORIZE restricted data into
# zero_egress, with every matrix test above staying green.
#
# The tests below build an INDEPENDENT expected-value oracle: literal
# tables computed by this test file's own separately written logic, never
# calling `would_cross_security_boundary`, `check_purpose_authorized`,
# `resolve_effective_project_security_profile` or `security_profile_rank`.
# A bug in any of those four production helpers cannot also corrupt this
# oracle's own expectation, because the oracle never calls them.
# ---------------------------------------------------------------------------

# Independent of the module's own `_PROFILE_RANK` -- a separately written
# literal rank table.
_ORACLE_PROFILE_RANK: dict[str, int] = {"standard": 0, "zero_egress": 1, "air_gapped": 2}

# Independent of the module's own `MINIMUM_REQUIRED_SECURITY_PROFILE`
# dict object identity -- a separately written literal copy of the same
# disclosed mapping (see module docstring for the mapping's own
# rationale). Cross-checked against the real export below, once, by
# value -- not relied upon by any oracle computation.
_ORACLE_MINIMUM_PROFILE: dict[str, str] = {
    "public": "standard",
    "internal": "standard",
    "confidential": "zero_egress",
    "restricted": "air_gapped",
}


def test_oracle_minimum_profile_table_matches_the_real_export() -> None:
    # Confirms the independent literal table above is not silently stale
    # relative to the module's own disclosed mapping -- a one-time value
    # comparison, never used inside the oracle computations themselves.
    check(
        "FTF-0500D",
        "the independent oracle's own literal MINIMUM_REQUIRED_SECURITY_PROFILE copy matches "
        "the module's real export by value",
        _ORACLE_MINIMUM_PROFILE == dict(MINIMUM_REQUIRED_SECURITY_PROFILE),
    )


def _oracle_would_cross(classification: str, org_profile: str, project_profile: str) -> bool:
    """Literal, independently-derived expected value for whether copying
    `classification` data into a project whose effective security profile
    is the more restrictive of `org_profile`/`project_profile` would leave
    that classification's own required minimum security boundary. Never
    calls `would_cross_security_boundary`, `resolve_effective_project_
    security_profile` or `security_profile_rank`."""
    effective_rank = max(_ORACLE_PROFILE_RANK[org_profile], _ORACLE_PROFILE_RANK[project_profile])
    required_rank = _ORACLE_PROFILE_RANK[_ORACLE_MINIMUM_PROFILE[classification]]
    return effective_rank < required_rank


def _oracle_effective_profile(org_profile: str, project_profile: str) -> str:
    """Literal, independently-derived expected effective profile: the
    more restrictive (higher-ranked) of the two, project profile winning
    ties -- never calls `resolve_effective_project_security_profile`."""
    if _ORACLE_PROFILE_RANK[project_profile] >= _ORACLE_PROFILE_RANK[org_profile]:
        return project_profile
    return org_profile


def test_independent_oracle_boundary_crossing_classification_profile_matrix() -> None:
    """36 (classification, org_profile, project_profile) cells -- purpose
    always granted and connection always active, so only the
    boundary-crossing path is exercised and isolated from the purpose
    path. Every expected value comes from the independent oracle helpers
    above, never from the production module's own helpers. Also covers
    round-3 DoD item (2): per-field preview assertions with non-default
    (org != project) values -- `effective_project_security_profile` and
    `required_minimum_security_profile` are asserted on every cell, most
    of which have org != project."""
    executed_cases = 0
    violations: list[str] = []
    for classification in SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS:
        for org_profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
            for project_profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
                executed_cases += 1
                expected_cross = _oracle_would_cross(classification, org_profile, project_profile)
                expected_effective = _oracle_effective_profile(org_profile, project_profile)
                expected_required = _ORACLE_MINIMUM_PROFILE[classification]

                preview = build_dataset_source_copy_preview(
                    source_display_name="oracle",
                    source_type="upload",
                    security_classification=classification,
                    connection_status="active",
                    allowed_purposes=["training"],
                    requested_purpose="training",
                    organization_default_security_profile=org_profile,
                    project_security_profile=project_profile,
                    manifest=_manifest(),
                )
                raised_boundary = False
                try:
                    authorize_dataset_source_copy(
                        security_classification=classification,
                        connection_status="active",
                        allowed_purposes=["training"],
                        requested_purpose="training",
                        organization_default_security_profile=org_profile,
                        project_security_profile=project_profile,
                    )
                except SecurityBoundaryCrossingError:
                    raised_boundary = True

                if (
                    preview.would_cross_security_boundary != expected_cross
                    or raised_boundary != expected_cross
                    or preview.effective_project_security_profile != expected_effective
                    or preview.required_minimum_security_profile != expected_required
                ):
                    violations.append(
                        f"classification={classification!r} org={org_profile!r} "
                        f"project={project_profile!r}: expected_cross={expected_cross} "
                        f"preview.would_cross={preview.would_cross_security_boundary} "
                        f"authorize_raised_boundary={raised_boundary} "
                        f"expected_effective={expected_effective!r} "
                        f"preview.effective={preview.effective_project_security_profile!r} "
                        f"expected_required={expected_required!r} "
                        f"preview.required={preview.required_minimum_security_profile!r}"
                    )
    shown = violations[:5]
    more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
    check(
        "FTF-0500D",
        f"independent oracle (literal table, never calling the production helpers): all "
        f"{executed_cases} classification x org-profile x project-profile cells (4x3x3=36) "
        f"match the expected would_cross/effective/required values for both preview and "
        f"authorize -- violations: {shown}{more}",
        executed_cases == 36 and violations == [],
    )


def test_independent_oracle_purpose_authorization_status_grant_purpose_matrix() -> None:
    """20 (connection_status, requested_purpose, granted) cases -- 2
    statuses x 2 grant states x 5 purposes, covering the round-3 review's
    own "4 grant states" (status x grant) across every purpose.
    classification/profiles held fixed at a non-crossing case (public /
    standard / standard) so only the purpose-authorization path is
    exercised, isolated from the boundary path. Expected value is a
    literal boolean (`status == "active" and granted`), never calling
    `check_purpose_authorized`."""
    executed_cases = 0
    violations: list[str] = []
    for connection_status in ("active", "revoked"):
        for granted in (True, False):
            for requested_purpose in SUPPORTED_PURPOSES:
                executed_cases += 1
                allowed_purposes = _allowed_purposes_for(requested_purpose, granted)
                expected_authorized = connection_status == "active" and granted

                preview = build_dataset_source_copy_preview(
                    source_display_name="oracle",
                    source_type="upload",
                    security_classification="public",
                    connection_status=connection_status,
                    allowed_purposes=allowed_purposes,
                    requested_purpose=requested_purpose,
                    organization_default_security_profile="standard",
                    project_security_profile="standard",
                    manifest=_manifest(),
                )
                raised_auth = False
                try:
                    authorize_dataset_source_copy(
                        security_classification="public",
                        connection_status=connection_status,
                        allowed_purposes=allowed_purposes,
                        requested_purpose=requested_purpose,
                        organization_default_security_profile="standard",
                        project_security_profile="standard",
                    )
                except DatasetSourceCopyAuthorizationError:
                    raised_auth = True

                if preview.purpose_authorized != expected_authorized or raised_auth != (
                    not expected_authorized
                ):
                    violations.append(
                        f"status={connection_status!r} purpose={requested_purpose!r} "
                        f"granted={granted}: expected_authorized={expected_authorized} "
                        f"preview.purpose_authorized={preview.purpose_authorized} "
                        f"authorize_raised_auth_error={raised_auth}"
                    )
    shown = violations[:5]
    more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
    check(
        "FTF-0500D",
        f"independent oracle (literal table, never calling the production helpers): all "
        f"{executed_cases} connection_status x grant x requested_purpose cases (2x2x5=20, the "
        f"review's own 4 status-x-grant states x every purpose) match the expected purpose-"
        f"authorization value for both preview and authorize -- violations: {shown}{more}",
        executed_cases == 20 and violations == [],
    )


_BOUNDARY_ADDITIVE_BYPASS_FAULT_CHECK_SCRIPT = """
import sys
sys.path.insert(0, "__ROOT__")
from workers.data.dataset_source_security_boundary_preview_audit_evidence import (
    SecurityBoundaryCrossingError,
    SourceCopyManifest,
    authorize_dataset_source_copy,
    build_dataset_source_copy_preview,
)

manifest = SourceCopyManifest(estimated_bytes=1, estimated_records=1, expected_transformations=())

# restricted + effective zero_egress: independent oracle expects
# would_cross=True (restricted requires air_gapped; zero_egress is one
# rank below it). The injected fault hardcodes a `return False` for
# exactly this pairing.
preview = build_dataset_source_copy_preview(
    source_display_name="fault",
    source_type="upload",
    security_classification="restricted",
    connection_status="active",
    allowed_purposes=["training"],
    requested_purpose="training",
    organization_default_security_profile="zero_egress",
    project_security_profile="zero_egress",
    manifest=manifest,
)
raised = False
try:
    authorize_dataset_source_copy(
        security_classification="restricted",
        connection_status="active",
        allowed_purposes=["training"],
        requested_purpose="training",
        organization_default_security_profile="zero_egress",
        project_security_profile="zero_egress",
    )
except SecurityBoundaryCrossingError:
    raised = True

EXPECTED_CROSS = True
MATCHES_ORACLE = (preview.would_cross_security_boundary == EXPECTED_CROSS) and (
    raised == EXPECTED_CROSS
)
print("MATCHES_ORACLE=" + str(MATCHES_ORACLE))
"""


def _run_boundary_fault_check_script() -> bool:
    script = _BOUNDARY_ADDITIVE_BYPASS_FAULT_CHECK_SCRIPT.replace("__ROOT__", str(ROOT))
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"boundary fault script failed (exit {result.returncode}): {result.stderr}"
        )
    for line in result.stdout.splitlines():
        if line.startswith("MATCHES_ORACLE="):
            return line[len("MATCHES_ORACLE=") :] == "True"
    raise RuntimeError(f"boundary fault script produced no MATCHES_ORACLE= line: {result.stdout!r}")


def test_fault_injection_boundary_check_additive_bypass_caught_by_independent_oracle() -> None:
    """Round-3 DoD item (3): a fault test for the named additive-allow
    class in `would_cross_security_boundary` ("an additive `return False`
    for restricted+zero_egress ... which AUTHORIZES restricted data into
    zero_egress"). Injects it directly into the real on-disk module,
    confirms the independent oracle catches it (unlike the self-
    referential matrix tests, which would stay green), then restores the
    module file byte-identical."""
    import workers.data.dataset_source_security_boundary_preview_audit_evidence as _module

    module_path = Path(_module.__file__)
    original_bytes = module_path.read_bytes()
    original_hash = hashlib.md5(original_bytes).hexdigest()
    original_text = original_bytes.decode("utf-8")

    anchor = (
        "    required = "
        "required_minimum_security_profile_for_classification(security_classification)\n"
    )
    check(
        "FTF-0500D",
        "the exact anchor this additive-fault injection targets exists exactly once in the "
        "real on-disk module before injection",
        original_text.count(anchor) == 1,
    )
    mutated_text = original_text.replace(
        anchor,
        anchor
        + '    if security_classification == "restricted" and '
        + 'effective_project_security_profile == "zero_egress":\n'
        + "        return False\n",
        1,
    )

    try:
        module_path.write_text(mutated_text, encoding="utf-8")
        matches_oracle = _run_boundary_fault_check_script()
        check(
            "FTF-0500D",
            "[round-3 finding, reproduced] the independent oracle genuinely catches the "
            "injected additive restricted+zero_egress bypass in would_cross_security_boundary "
            f"(the self-referential matrix tests would not); matches_oracle={matches_oracle}",
            matches_oracle is False,
        )
    finally:
        module_path.write_bytes(original_bytes)
        restored_hash = hashlib.md5(module_path.read_bytes()).hexdigest()
        check(
            "FTF-0500D",
            "the module file is restored byte-identical after fault injection",
            restored_hash == original_hash,
        )

    clean_matches_oracle = _run_boundary_fault_check_script()
    check(
        "FTF-0500D",
        f"the independent oracle is clean again once the fault is reverted; "
        f"matches_oracle={clean_matches_oracle}",
        clean_matches_oracle is True,
    )


_PURPOSE_ADDITIVE_BYPASS_FAULT_CHECK_SCRIPT = """
import sys
sys.path.insert(0, "__ROOT__")
from workers.data.dataset_source_security_boundary_preview_audit_evidence import (
    DatasetSourceCopyAuthorizationError,
    SourceCopyManifest,
    authorize_dataset_source_copy,
    build_dataset_source_copy_preview,
)

manifest = SourceCopyManifest(estimated_bytes=1, estimated_records=1, expected_transformations=())

# connection revoked, purpose not granted, requested purpose is the one
# the injected fault bypasses unconditionally. Independent oracle expects
# purpose_authorized=False (status alone already denies it).
preview = build_dataset_source_copy_preview(
    source_display_name="fault",
    source_type="upload",
    security_classification="public",
    connection_status="revoked",
    allowed_purposes=["training"],
    requested_purpose="baseline_only",
    organization_default_security_profile="standard",
    project_security_profile="standard",
    manifest=manifest,
)
raised = False
try:
    authorize_dataset_source_copy(
        security_classification="public",
        connection_status="revoked",
        allowed_purposes=["training"],
        requested_purpose="baseline_only",
        organization_default_security_profile="standard",
        project_security_profile="standard",
    )
except DatasetSourceCopyAuthorizationError:
    raised = True

EXPECTED_AUTHORIZED = False
MATCHES_ORACLE = (preview.purpose_authorized == EXPECTED_AUTHORIZED) and (
    raised == (not EXPECTED_AUTHORIZED)
)
print("MATCHES_ORACLE=" + str(MATCHES_ORACLE))
"""


def _run_purpose_fault_check_script() -> bool:
    script = _PURPOSE_ADDITIVE_BYPASS_FAULT_CHECK_SCRIPT.replace("__ROOT__", str(ROOT))
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"purpose fault script failed (exit {result.returncode}): {result.stderr}"
        )
    for line in result.stdout.splitlines():
        if line.startswith("MATCHES_ORACLE="):
            return line[len("MATCHES_ORACLE=") :] == "True"
    raise RuntimeError(f"purpose fault script produced no MATCHES_ORACLE= line: {result.stdout!r}")


def test_fault_injection_purpose_check_additive_bypass_caught_by_independent_oracle() -> None:
    """Round-3 DoD item (3): a fault test for the named additive-allow
    class in `check_purpose_authorized` ("purpose-specific allows, or a
    revoked-status allow"). Injects a purpose-specific unconditional
    bypass directly into the real on-disk module, confirms the
    independent oracle catches it, then restores the module file
    byte-identical."""
    import workers.data.dataset_source_security_boundary_preview_audit_evidence as _module

    module_path = Path(_module.__file__)
    original_bytes = module_path.read_bytes()
    original_hash = hashlib.md5(original_bytes).hexdigest()
    original_text = original_bytes.decode("utf-8")

    anchor = '    if connection_status != "active":\n'
    check(
        "FTF-0500D",
        "the exact anchor this additive-fault injection targets exists exactly once in the "
        "real on-disk module before injection",
        original_text.count(anchor) == 1,
    )
    mutated_text = original_text.replace(
        anchor,
        '    if requested_purpose == "baseline_only":\n        return\n' + anchor,
        1,
    )

    try:
        module_path.write_text(mutated_text, encoding="utf-8")
        matches_oracle = _run_purpose_fault_check_script()
        check(
            "FTF-0500D",
            "[round-3 finding, reproduced] the independent oracle genuinely catches the "
            "injected additive purpose-specific bypass in check_purpose_authorized (the "
            f"self-referential matrix tests would not); matches_oracle={matches_oracle}",
            matches_oracle is False,
        )
    finally:
        module_path.write_bytes(original_bytes)
        restored_hash = hashlib.md5(module_path.read_bytes()).hexdigest()
        check(
            "FTF-0500D",
            "the module file is restored byte-identical after fault injection",
            restored_hash == original_hash,
        )

    clean_matches_oracle = _run_purpose_fault_check_script()
    check(
        "FTF-0500D",
        f"the independent oracle is clean again once the fault is reverted; "
        f"matches_oracle={clean_matches_oracle}",
        clean_matches_oracle is True,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0500D dataset source security-boundary preview: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0500D dataset source security-boundary preview: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
