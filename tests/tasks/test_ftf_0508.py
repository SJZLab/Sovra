#!/usr/bin/env python3
"""FTF-0508 -- Record schema validator.

Runnable without any database and without any external network egress:
`validate_record_batch` is a pure, synchronous, no-I/O function (this
task's own `database_changes` clause -- "No independent schema change.
Consume frozen contract.").

This task's own acceptance clause ("Validate schema/dialect/content size
and required provenance/quality fields per canonical version") is covered
by `test_accepts_a_fully_valid_batch` (the positive path across all five
checks) and one dedicated negative test per check:
`test_rejects_unknown_schema_version`, `test_rejects_unknown_dialect`,
`test_rejects_content_too_large`, `test_rejects_missing_provenance_fields`,
`test_rejects_invalid_quality_status`, `test_rejects_invalid_pii_status`,
`test_rejects_invalid_tool_schema_status`.

This task's own negative_acceptance ("Unknown content or schema version
and invalid sequence produces blocking issues with row evidence") is
covered by `test_rejects_unknown_content_wrong_shape`,
`test_rejects_unknown_content_not_json_serializable`,
`test_rejects_unknown_schema_version` (row evidence),
`test_rejects_invalid_sequence_non_int`,
`test_rejects_invalid_sequence_negative_int`,
`test_rejects_invalid_sequence_bool`,
`test_rejects_duplicate_record_index`, and
`test_row_evidence_uses_batch_position_when_record_index_itself_invalid`
(proving the report never raises on a per-row violation and always
carries usable row evidence).

Run directly: python3 tests/tasks/test_ftf_0508.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.canonical_conversation_normalizer import SCHEMA_VERSION  # noqa: E402
from workers.data.record_schema_validator import (  # noqa: E402
    CANONICAL_SCHEMA_VERSIONS,
    PII_STATUS_VALUES,
    QUALITY_STATUS_VALUES,
    TOOL_SCHEMA_STATUS_VALUES,
    RecordSchemaValidationError,
    validate_record_batch,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _valid_candidate(**overrides: object) -> dict[str, object]:
    candidate: dict[str, object] = {
        "record_index": 0,
        "schema_version": SCHEMA_VERSION,
        "source_type": "upload",
        "content": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "metadata": {"source": "unit-test"},
        },
        "record_sha256": "a" * 64,
        "normalized_sha256": "b" * 64,
        "quality_status": "passed",
        "pii_status": "clean",
        "tool_schema_status": "not_applicable",
    }
    candidate.update(overrides)
    return candidate


def _issue_codes(report: object, batch_position: int = 0) -> set[str]:
    return {
        issue.code
        for issue in report.issues  # type: ignore[attr-defined]
        if issue.batch_position == batch_position
    }


def test_accepts_a_fully_valid_batch() -> None:
    report = validate_record_batch([_valid_candidate(), _valid_candidate(record_index=1)])
    check("FTF-0508", "a fully valid batch produces zero issues", report.is_clean)
    check(
        "FTF-0508",
        "both records are accepted, by batch position",
        report.accepted_batch_positions == (0, 1) and report.rejected_batch_positions == (),
    )


def test_rejects_unknown_schema_version() -> None:
    report = validate_record_batch([_valid_candidate(schema_version="99.9")])
    check(
        "FTF-0508",
        "acceptance: an unrecognized schema_version is rejected, per canonical version",
        "unknown_schema_version" in _issue_codes(report),
    )
    check(
        "FTF-0508",
        "negative_acceptance: the issue carries real row evidence (record_index)",
        any(
            issue.code == "unknown_schema_version" and issue.record_index == 0
            for issue in report.issues
        ),
    )
    check("FTF-0508", "the row is rejected", report.rejected_batch_positions == (0,))


def test_rejects_unknown_dialect() -> None:
    report = validate_record_batch([_valid_candidate(source_type="ftp")])
    check(
        "FTF-0508",
        "acceptance: an unrecognized source_type (dialect) is rejected",
        "unknown_dialect" in _issue_codes(report),
    )


def test_rejects_content_too_large() -> None:
    oversized_text = "x" * (2 * 1024 * 1024 + 1)
    report = validate_record_batch(
        [
            _valid_candidate(
                content={
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": oversized_text}]}
                    ],
                    "metadata": {},
                }
            )
        ]
    )
    check(
        "FTF-0508",
        "acceptance: content exceeding Section 67.3's 2MiB single-record max is rejected",
        "content_too_large" in _issue_codes(report),
    )


def test_rejects_unknown_content_wrong_shape() -> None:
    report = validate_record_batch([_valid_candidate(content={"messages": []})])
    check(
        "FTF-0508",
        "negative_acceptance: content missing the required 'metadata' key is "
        "'unknown content', not silently accepted",
        "unknown_content" in _issue_codes(report),
    )

    report_extra = validate_record_batch(
        [_valid_candidate(content={"messages": [], "metadata": {}, "extra": 1})]
    )
    check(
        "FTF-0508",
        "content with an extra, unrecognized top-level key is also 'unknown content'",
        "unknown_content" in _issue_codes(report_extra),
    )

    report_not_mapping = validate_record_batch([_valid_candidate(content="not-a-mapping")])
    check(
        "FTF-0508",
        "non-mapping content is 'unknown content'",
        "unknown_content" in _issue_codes(report_not_mapping),
    )


def test_rejects_unknown_content_not_json_serializable() -> None:
    report = validate_record_batch(
        [_valid_candidate(content={"messages": {1, 2, 3}, "metadata": {}})]
    )
    check(
        "FTF-0508",
        "negative_acceptance: content that is not JSON-serializable is 'unknown content'",
        "unknown_content" in _issue_codes(report),
    )


def test_rejects_missing_provenance_fields() -> None:
    report = validate_record_batch([_valid_candidate(record_sha256="", normalized_sha256=None)])
    codes = [issue.code for issue in report.issues if issue.batch_position == 0]
    check(
        "FTF-0508",
        "acceptance: an empty/missing required provenance field is rejected, "
        "for both fields independently",
        codes.count("missing_provenance_field") == 2,
    )


def test_rejects_invalid_quality_status() -> None:
    report = validate_record_batch([_valid_candidate(quality_status="looks_fine_to_me")])
    check(
        "FTF-0508",
        "acceptance: an arbitrary, unrecognized quality_status string is rejected, "
        "never silently accepted",
        "invalid_quality_status" in _issue_codes(report),
    )
    check(
        "FTF-0508",
        "the real enum this task disclosed is non-empty and self-consistent",
        len(QUALITY_STATUS_VALUES) == 4,
    )


def test_rejects_invalid_pii_status() -> None:
    report = validate_record_batch([_valid_candidate(pii_status="probably_ok")])
    check(
        "FTF-0508",
        "acceptance: an arbitrary, unrecognized pii_status string is rejected",
        "invalid_pii_status" in _issue_codes(report),
    )
    check("FTF-0508", "the disclosed pii_status enum is non-empty", len(PII_STATUS_VALUES) == 4)


def test_rejects_invalid_tool_schema_status() -> None:
    report = validate_record_batch([_valid_candidate(tool_schema_status="sort_of")])
    check(
        "FTF-0508",
        "acceptance: an arbitrary, unrecognized tool_schema_status string is rejected",
        "invalid_tool_schema_status" in _issue_codes(report),
    )
    check(
        "FTF-0508",
        "the disclosed tool_schema_status enum is non-empty",
        len(TOOL_SCHEMA_STATUS_VALUES) == 3,
    )


def test_rejects_invalid_sequence_non_int() -> None:
    report = validate_record_batch([_valid_candidate(record_index="0")])
    check(
        "FTF-0508",
        "negative_acceptance: a non-int record_index is 'invalid sequence', not coerced",
        "invalid_sequence" in _issue_codes(report),
    )


def test_rejects_invalid_sequence_negative_int() -> None:
    report = validate_record_batch([_valid_candidate(record_index=-1)])
    check(
        "FTF-0508",
        "negative_acceptance: a negative record_index is 'invalid sequence'",
        "invalid_sequence" in _issue_codes(report),
    )


def test_rejects_invalid_sequence_bool() -> None:
    report = validate_record_batch([_valid_candidate(record_index=True)])
    check(
        "FTF-0508",
        "a bool record_index is 'invalid sequence' (bool is an int subclass "
        "but not a real row ordinal)",
        "invalid_sequence" in _issue_codes(report),
    )


def test_rejects_duplicate_record_index() -> None:
    report = validate_record_batch(
        [_valid_candidate(record_index=5), _valid_candidate(record_index=5)]
    )
    check(
        "FTF-0508",
        "negative_acceptance: a record_index duplicating another row in the same "
        "batch is 'invalid sequence', ordering is ambiguous",
        "invalid_sequence" in _issue_codes(report, batch_position=1),
    )
    check(
        "FTF-0508",
        "the first occurrence of the duplicated record_index is not itself "
        "penalized -- only the later, ambiguous duplicate is",
        "invalid_sequence" not in _issue_codes(report, batch_position=0),
    )


def test_row_evidence_uses_batch_position_when_record_index_itself_invalid() -> None:
    report = validate_record_batch(
        [_valid_candidate(), _valid_candidate(record_index="not-an-int")]
    )
    bad_row_issues = [issue for issue in report.issues if issue.batch_position == 1]
    check(
        "FTF-0508",
        "negative_acceptance: row evidence always includes batch_position, even "
        "when record_index itself is the invalid field and cannot be trusted",
        all(issue.record_index is None for issue in bad_row_issues) and len(bad_row_issues) > 0,
    )


def test_rejects_non_mapping_candidate() -> None:
    report = validate_record_batch(["not-a-mapping"])  # type: ignore[list-item]
    check(
        "FTF-0508",
        "a non-mapping candidate record is rejected with row evidence, not a crash",
        "invalid_candidate" in _issue_codes(report),
    )


def test_mixed_batch_partitions_accepted_and_rejected() -> None:
    report = validate_record_batch(
        [_valid_candidate(record_index=0), _valid_candidate(record_index=1, source_type="bogus")]
    )
    check(
        "FTF-0508",
        "a mixed batch accepts the clean row and rejects only the bad one",
        report.accepted_batch_positions == (0,) and report.rejected_batch_positions == (1,),
    )


def test_raises_on_non_sequence_batch() -> None:
    try:
        validate_record_batch({"not": "a-sequence"})  # type: ignore[arg-type]
    except RecordSchemaValidationError:
        pass
    else:
        check(
            "FTF-0508",
            "a non-sequence batch raises, there is no row to attach evidence to",
            False,
        )


def test_raises_on_empty_batch() -> None:
    try:
        validate_record_batch([])
    except RecordSchemaValidationError:
        pass
    else:
        check("FTF-0508", "an empty batch raises rather than silently reporting nothing", False)


def test_canonical_schema_versions_reuses_ftf_0506_constant() -> None:
    check(
        "FTF-0508",
        "this task's own canonical schema-version set is reused directly from "
        "FTF-0506's already-CERTIFIED SCHEMA_VERSION, not a second invented constant",
        CANONICAL_SCHEMA_VERSIONS == {SCHEMA_VERSION},
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0508 record schema validator: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0508 record schema validator: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
