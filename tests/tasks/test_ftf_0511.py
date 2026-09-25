#!/usr/bin/env python3
"""FTF-0511 -- Exact duplicate detector.

Runnable without any database and without any external network egress:
`detect_exact_duplicates` is a pure, synchronous, no-I/O function (this
task's own `database_changes` clause -- "No independent schema change.
Consume frozen contract.").

This task's own acceptance clause ("Raw hash duplicate groups remain
deterministic and trace original record identity") is covered by
`test_groups_duplicates_by_raw_hash`, `test_groups_duplicates_by_normalized_hash`,
`test_groups_trace_original_record_identity`, `test_deterministic_group_ordering`
and `test_no_duplicates_when_all_hashes_distinct`.

This task's own negative_acceptance ("Cross-purpose duplicate detection
cannot be disabled to fill sample minimum; hash collisions/errors
surfaced") is covered by `test_cannot_disable_cross_purpose_detection`
(inspects the real function signature -- there is no bypass parameter),
`test_cross_purpose_raw_duplicate_is_blocking`,
`test_same_purpose_raw_duplicate_is_reported_but_not_blocking`,
`test_normalized_duplicate_always_blocks_even_same_purpose`,
`test_missing_hash_surfaces_issue_not_silently_dropped`,
`test_invalid_hash_format_surfaces_issue`, and
`test_invalid_purpose_surfaces_issue`.

Run directly: python3 tests/tasks/test_ftf_0511.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.exact_duplicate_detector import (  # noqa: E402
    SUPPORTED_PURPOSES,
    ExactDuplicateDetectionError,
    detect_exact_duplicates,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_RAW_A = "a" * 64
_RAW_B = "b" * 64
_NORM_A = "c" * 64
_NORM_B = "d" * 64


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "record_index": 0,
        "record_sha256": _RAW_A,
        "normalized_sha256": _NORM_A,
        "purpose": "training",
    }
    record.update(overrides)
    return record


def _issue_codes(report: object, batch_position: int) -> set[str]:
    return {
        issue.code
        for issue in report.issues  # type: ignore[attr-defined]
        if issue.batch_position == batch_position
    }


def test_groups_duplicates_by_raw_hash() -> None:
    report = detect_exact_duplicates(
        [
            _record(record_index=0, record_sha256=_RAW_A, normalized_sha256="1" * 64),
            _record(record_index=1, record_sha256=_RAW_A, normalized_sha256="2" * 64),
            _record(record_index=2, record_sha256=_RAW_B, normalized_sha256="3" * 64),
        ]
    )
    check(
        "FTF-0511",
        "acceptance: two records sharing record_sha256 form one raw duplicate group",
        len(report.raw_duplicate_groups) == 1 and report.raw_duplicate_groups[0].digest == _RAW_A,
    )
    check(
        "FTF-0511",
        "a singleton raw hash produces no group",
        all(group.digest != _RAW_B for group in report.raw_duplicate_groups),
    )


def test_groups_duplicates_by_normalized_hash() -> None:
    report = detect_exact_duplicates(
        [
            _record(record_index=0, record_sha256="1" * 64, normalized_sha256=_NORM_A),
            _record(record_index=1, record_sha256="2" * 64, normalized_sha256=_NORM_A),
            _record(record_index=2, record_sha256="3" * 64, normalized_sha256=_NORM_B),
        ]
    )
    check(
        "FTF-0511",
        "acceptance: two records sharing normalized_sha256 form one normalized "
        "duplicate group (Section 10 method 2)",
        len(report.normalized_duplicate_groups) == 1
        and report.normalized_duplicate_groups[0].digest == _NORM_A,
    )


def test_groups_trace_original_record_identity() -> None:
    report = detect_exact_duplicates(
        [
            _record(record_index=5, record_sha256=_RAW_A),
            _record(record_index=9, record_sha256=_RAW_A),
        ]
    )
    group = report.raw_duplicate_groups[0]
    check(
        "FTF-0511",
        "acceptance: the group traces original record identity via batch_position",
        group.batch_positions == (0, 1),
    )
    check(
        "FTF-0511",
        "acceptance: the group traces original record identity via record_index",
        group.record_indices == (5, 9),
    )


def test_deterministic_group_ordering() -> None:
    records = [
        _record(record_index=0, record_sha256=_RAW_B),
        _record(record_index=1, record_sha256=_RAW_B),
        _record(record_index=2, record_sha256=_RAW_A),
        _record(record_index=3, record_sha256=_RAW_A),
    ]
    report_one = detect_exact_duplicates(records)
    report_two = detect_exact_duplicates(list(records))
    check(
        "FTF-0511",
        "acceptance: raw duplicate groups remain deterministic across repeated calls "
        "on the same input, ordered by digest",
        [g.digest for g in report_one.raw_duplicate_groups]
        == [g.digest for g in report_two.raw_duplicate_groups]
        == [_RAW_A, _RAW_B],
    )


def test_no_duplicates_when_all_hashes_distinct() -> None:
    report = detect_exact_duplicates(
        [
            _record(record_index=0, record_sha256="1" * 64, normalized_sha256="a" * 64),
            _record(record_index=1, record_sha256="2" * 64, normalized_sha256="b" * 64),
        ]
    )
    check(
        "FTF-0511",
        "no duplicate groups and no issues for an all-distinct batch",
        report.is_clean,
    )


def test_cannot_disable_cross_purpose_detection() -> None:
    signature = inspect.signature(detect_exact_duplicates)
    check(
        "FTF-0511",
        "negative_acceptance: detect_exact_duplicates exposes no parameter that could "
        "disable or downgrade cross-purpose duplicate detection",
        list(signature.parameters) == ["records"],
    )


def test_cross_purpose_raw_duplicate_is_blocking() -> None:
    report = detect_exact_duplicates(
        [
            _record(record_index=0, record_sha256=_RAW_A, purpose="training"),
            _record(record_index=1, record_sha256=_RAW_A, purpose="held_out_evaluation"),
        ]
    )
    group = report.raw_duplicate_groups[0]
    check(
        "FTF-0511",
        "negative_acceptance: a raw-hash duplicate spanning train vs held-out is "
        "flagged cross-purpose",
        group.is_cross_purpose and group.purposes == frozenset({"training", "held_out_evaluation"}),
    )
    check(
        "FTF-0511",
        "negative_acceptance: Section 10 'exact match: always block across train vs "
        "held-out' -- the cross-purpose raw group is blocking",
        group in report.blocking_groups,
    )


def test_same_purpose_raw_duplicate_is_reported_but_not_blocking() -> None:
    report = detect_exact_duplicates(
        [
            _record(record_index=0, record_sha256=_RAW_A, purpose="training"),
            _record(record_index=1, record_sha256=_RAW_A, purpose="training"),
        ]
    )
    group = report.raw_duplicate_groups[0]
    check(
        "FTF-0511",
        "a same-purpose raw duplicate is still reported (not silently dropped)",
        group in report.raw_duplicate_groups,
    )
    check(
        "FTF-0511",
        "a same-purpose raw duplicate is not itself cross-purpose blocking",
        not group.is_cross_purpose and group not in report.blocking_groups,
    )


def test_normalized_duplicate_always_blocks_even_same_purpose() -> None:
    report = detect_exact_duplicates(
        [
            _record(
                record_index=0,
                record_sha256="1" * 64,
                normalized_sha256=_NORM_A,
                purpose="training",
            ),
            _record(
                record_index=1,
                record_sha256="2" * 64,
                normalized_sha256=_NORM_A,
                purpose="training",
            ),
        ]
    )
    group = report.normalized_duplicate_groups[0]
    check(
        "FTF-0511",
        "negative_acceptance: Section 10 'normalized match: always block' -- a "
        "same-purpose normalized duplicate is still blocking",
        not group.is_cross_purpose and group in report.blocking_groups,
    )


def test_missing_hash_surfaces_issue_not_silently_dropped() -> None:
    report = detect_exact_duplicates([_record(record_index=0, record_sha256=None)])
    check(
        "FTF-0511",
        "negative_acceptance: a missing raw hash is surfaced as an issue, not "
        "silently dropped from the report",
        "missing_hash" in _issue_codes(report, 0),
    )


def test_invalid_hash_format_surfaces_issue() -> None:
    report = detect_exact_duplicates([_record(record_index=0, record_sha256="not-a-real-hash")])
    check(
        "FTF-0511",
        "negative_acceptance: a malformed (non-64-hex) hash is surfaced as an "
        "invalid_hash issue, never guessed into a collision",
        "invalid_hash" in _issue_codes(report, 0),
    )


def test_invalid_purpose_surfaces_issue() -> None:
    report = detect_exact_duplicates([_record(record_index=0, purpose="unapproved_purpose")])
    check(
        "FTF-0511",
        "an unrecognized purpose is surfaced as an issue, not silently accepted",
        "invalid_purpose" in _issue_codes(report, 0),
    )


def test_invalid_purpose_reported_once_not_per_hash_field() -> None:
    report = detect_exact_duplicates([_record(record_index=0, purpose="bogus")])
    check(
        "FTF-0511",
        "a single bad purpose produces exactly one issue, not one per hash field",
        len(report.issues) == 1,
    )


def test_raises_on_non_sequence_batch() -> None:
    try:
        detect_exact_duplicates("not-a-sequence")  # type: ignore[arg-type]
    except ExactDuplicateDetectionError:
        pass
    else:
        check(
            "FTF-0511",
            "a non-sequence batch raises, there is no row to attach evidence to",
            False,
        )


def test_raises_on_empty_batch() -> None:
    try:
        detect_exact_duplicates([])
    except ExactDuplicateDetectionError:
        pass
    else:
        check("FTF-0511", "an empty batch raises rather than silently reporting nothing", False)


def test_supported_purposes_matches_ftf_0500_vocabulary() -> None:
    check(
        "FTF-0511",
        "SUPPORTED_PURPOSES mirrors FTF-0500's own closed allowed_purposes vocabulary "
        "verbatim, not a second invented set",
        SUPPORTED_PURPOSES
        == frozenset(
            {
                "training",
                "validation",
                "held_out_evaluation",
                "candidate_development",
                "baseline_only",
            }
        ),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0511 exact duplicate detector: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0511 exact duplicate detector: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
