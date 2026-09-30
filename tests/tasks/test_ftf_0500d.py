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

Run directly: python3 tests/tasks/test_ftf_0500d.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.dataset_source_security_boundary_preview_audit_evidence import (  # noqa: E402
    MINIMUM_REQUIRED_SECURITY_PROFILE,
    RETENTION_POLICY_STATUS,
    SUPPORTED_PURPOSES,
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
