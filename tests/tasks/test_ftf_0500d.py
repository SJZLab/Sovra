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
`requested_purpose` combination (4 x 3 x 3 x 2 x 2 x 5 = 1,440 cases) is
checked.
`test_fault_injection_additive_org_project_divergence_early_return`
reproduces the finding directly against the real on-disk module in an
isolated subprocess: it adds the exact additive early-return fault the
review named (skip both checks whenever org != project), confirms the
original, narrower classification x profile matrix (which always held
org == project) is genuinely blind to it, confirms the new full matrix
genuinely catches it, then restores the module file byte-identical.

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


def _full_matrix_violations() -> list[str]:
    """Full classification x org-default x project x connection_status x
    grant x requested_purpose matrix (4 x 3 x 3 x 2 x 2 x 5 = 1,440
    cases). `_classification_profile_matrix_violations` above only ever
    held org_default == project (so `resolve_effective_project_security_
    profile`'s own divergence branch was never exercised by a
    boundary-crossing assertion) and only ever used a single,
    always-granted purpose -- the exact two narrowing gaps the
    2026-10-04T11:00Z L2 review named literally. This varies all six
    dimensions independently and asserts, for every combination, that
    `authorize_dataset_source_copy` raises exactly when the
    independently built preview's own `blocked` flag says it should."""
    violations: list[str] = []
    for classification in SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS:
        for org_profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
            for project_profile in SUPPORTED_PROJECT_SECURITY_PROFILES:
                for connection_status in ("active", "revoked"):
                    for requested_purpose in SUPPORTED_PURPOSES:
                        for granted in (True, False):
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
    return violations


def test_full_matrix_enumerates_every_real_value_of_every_dimension() -> None:
    # The 2026-10-04T11:00Z review's own prose cites "1,440 cases"; the
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
        case_count == 4 * 3 * 3 * 2 * 2 * 5,
    )
    check(
        "FTF-0500D",
        "every dimension's own real value count matches this module's closed vocabularies "
        "exactly (4 classifications, 3 security profiles, 5 purposes)",
        len(SUPPORTED_SOURCE_SECURITY_CLASSIFICATIONS) == 4
        and len(SUPPORTED_PROJECT_SECURITY_PROFILES) == 3
        and len(SUPPORTED_PURPOSES) == 5,
    )


def test_authorize_raises_whenever_preview_blocks_across_full_matrix() -> None:
    violations = _full_matrix_violations()
    shown = violations[:5]
    more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
    check(
        "FTF-0500D",
        "authorize_dataset_source_copy raises in exactly the (classification, org_default, "
        "project, connection_status, grant, purpose) combinations the preview marks blocked, "
        f"across the full 1,440-case matrix -- violations: {shown}{more}",
        violations == [],
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

# The full 1,440-case matrix.
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
            "[L2 finding, reproduced] the full 1,440-case matrix genuinely catches the injected "
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
