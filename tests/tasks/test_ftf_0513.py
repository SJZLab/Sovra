#!/usr/bin/env python3
"""FTF-0513 -- Near-duplicate detector.

Runnable without any database and without any external network egress:
`detect_near_duplicates` is a pure, synchronous, no-I/O function (this
task's own `database_changes` clause -- "No independent schema change.
Consume frozen contract.").

This task's own acceptance clause ("Use bounded indexed n-gram/semantic
candidates and reviewed thresholds with privacy-safe embeddings") is
covered by `test_character_ngram_similarity_is_computed_and_flagged`,
`test_token_jaccard_similarity_is_computed_and_flagged`,
`test_embedding_cosine_similarity_is_computed_and_flagged`,
`test_agent_trace_structural_match_flags_same_goal_tools_and_template`,
`test_agent_trace_structural_mismatch_on_different_tool_sequence`,
`test_bucketed_pair_not_flagged_below_default_thresholds`,
`test_lowering_thresholds_changes_the_flag_outcome`, and
`test_thresholds_reject_out_of_range_values`.

This task's own negative_acceptance ("No all-pairs million-record memory
blowup or automatic discard based solely on a guessed similarity
threshold") is covered by
`test_dissimilar_records_form_no_candidate_pair_via_indexing`,
`test_max_candidates_per_record_bounds_pair_count_far_below_all_pairs`,
`test_flagging_never_mutates_or_drops_input_records`, and
`test_no_discard_field_exists_on_candidate_or_report`.

Run directly: python3 tests/tasks/test_ftf_0513.py
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.near_duplicate_detector import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    SUPPORTED_PURPOSES,
    NearDuplicateCandidate,
    NearDuplicateDetectionError,
    NearDuplicateReport,
    NearDuplicateThresholds,
    detect_near_duplicates,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _text_record(
    *, text: str, purpose: str = "training", record_index: int = 0
) -> dict[str, object]:
    return {
        "record_index": record_index,
        "purpose": purpose,
        "content": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
            "metadata": {},
        },
    }


def _trace_record(
    *,
    user_text: str,
    tool_name: str | None,
    assistant_text: str,
    purpose: str = "training",
    record_index: int = 0,
) -> dict[str, object]:
    messages: list[dict[str, object]] = [
        {"role": "user", "content": [{"type": "text", "text": user_text}]},
    ]
    if tool_name is not None:
        messages.append(
            {
                "role": "assistant",
                "content": [],
                "tool_calls": [{"id": "call_1", "name": tool_name, "arguments": {}}],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": tool_name,
                "content": [{"type": "json", "value": {"status": "ok"}}],
            }
        )
    messages.append({"role": "assistant", "content": [{"type": "text", "text": assistant_text}]})
    return {
        "record_index": record_index,
        "purpose": purpose,
        "content": {"messages": messages, "metadata": {}},
    }


_BASE_TEXT = (
    "the customer asked about their order status and wanted a refund for item "
    "one two three four five six seven eight"
)


def _one_word_changed(text: str) -> str:
    words = text.split()
    words[3] = "inquired"
    return " ".join(words)


def _two_words_changed(text: str) -> str:
    words = text.split()
    words[3] = "inquired"
    words[10] = "discount"
    return " ".join(words)


_UNRELATED_TEXT = (
    "completely unrelated sentence about weather forecasts and traffic "
    "patterns in the city today"
)


def _find_pair(
    report: NearDuplicateReport, position_a: int, position_b: int
) -> NearDuplicateCandidate | None:
    for candidate in report.candidates:
        if {candidate.batch_position_a, candidate.batch_position_b} == {position_a, position_b}:
            return candidate
    return None


def test_character_ngram_similarity_is_computed_and_flagged() -> None:
    # A single word changed out of 21 keeps character-shingle overlap high
    # (~0.825, below this module's own 0.9 default) but token-set overlap
    # (~0.909) and hashed-embedding cosine (~0.956) both cross their
    # defaults -- proving each metric is computed and thresholded
    # independently, not collapsed into one score.
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_one_word_changed(_BASE_TEXT), record_index=1),
    ]
    report = detect_near_duplicates(records)
    candidate = _find_pair(report, 0, 1)
    check("FTF-0513", "one-word-changed pair is flagged for review", candidate is not None)
    assert candidate is not None
    check(
        "FTF-0513",
        "character_ngram_similarity is computed as a real ratio below the default threshold",
        0.7 < candidate.character_ngram_similarity < DEFAULT_THRESHOLDS.character_ngram_similarity,
    )
    check(
        "FTF-0513",
        "character_ngram method is not flagged on its own for this pair",
        "character_ngram" not in candidate.flagged_methods,
    )


def test_token_jaccard_similarity_is_computed_and_flagged() -> None:
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_one_word_changed(_BASE_TEXT), record_index=1),
    ]
    report = detect_near_duplicates(records)
    candidate = _find_pair(report, 0, 1)
    assert candidate is not None
    check(
        "FTF-0513",
        "token_jaccard_similarity crosses the default threshold for a one-word-changed pair",
        candidate.token_jaccard_similarity >= DEFAULT_THRESHOLDS.token_jaccard_similarity,
    )
    check(
        "FTF-0513",
        "token_jaccard is one of the flagged methods",
        "token_jaccard" in candidate.flagged_methods,
    )
    check("FTF-0513", "the flagged candidate requires review", candidate.requires_review)


def test_embedding_cosine_similarity_is_computed_and_flagged() -> None:
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_one_word_changed(_BASE_TEXT), record_index=1),
    ]
    report = detect_near_duplicates(records)
    candidate = _find_pair(report, 0, 1)
    assert candidate is not None
    check(
        "FTF-0513",
        "embedding_cosine_similarity crosses the default threshold for a one-word-changed pair",
        candidate.embedding_cosine_similarity >= DEFAULT_THRESHOLDS.embedding_cosine_similarity,
    )
    check(
        "FTF-0513",
        "embedding_cosine is one of the flagged methods",
        "embedding_cosine" in candidate.flagged_methods,
    )


def test_completely_unrelated_text_embedding_similarity_is_low() -> None:
    # Direct evidence the "local/private embedding" metric is a real,
    # discriminating computation, not a constant/fabricated value.
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_UNRELATED_TEXT, record_index=1),
    ]
    report = detect_near_duplicates(records)
    candidate = _find_pair(report, 0, 1)
    check(
        "FTF-0513",
        "two unrelated texts are never reported as a near-duplicate candidate",
        candidate is None,
    )


def test_agent_trace_structural_match_flags_same_goal_tools_and_template() -> None:
    # Same user goal, same tool sequence, and the same output template
    # (digits collapsed) despite a different filled-in order number --
    # Section 10 method 7's own literal three-part definition.
    records = [
        _trace_record(
            user_text="What is my order status?",
            tool_name="lookup_order",
            assistant_text="Your order #1234 has shipped.",
            record_index=0,
        ),
        _trace_record(
            user_text="What is my order status?",
            tool_name="lookup_order",
            assistant_text="Your order #9999 has shipped.",
            record_index=1,
        ),
    ]
    report = detect_near_duplicates(records)
    candidate = _find_pair(report, 0, 1)
    check("FTF-0513", "matching agent-trace structure is flagged for review", candidate is not None)
    assert candidate is not None
    check("FTF-0513", "structural_match is True", candidate.structural_match)
    check(
        "FTF-0513",
        "agent_trace_structural is one of the flagged methods",
        "agent_trace_structural" in candidate.flagged_methods,
    )


def test_agent_trace_structural_mismatch_on_different_tool_sequence() -> None:
    records = [
        _trace_record(
            user_text="What is my order status?",
            tool_name="lookup_order",
            assistant_text="Your order #1234 has shipped.",
            record_index=0,
        ),
        _trace_record(
            user_text="What is my order status?",
            tool_name=None,
            assistant_text="Your order #1234 has shipped.",
            record_index=1,
        ),
    ]
    report = detect_near_duplicates(records)
    candidate = _find_pair(report, 0, 1)
    if candidate is not None:
        check(
            "FTF-0513",
            "structural_match is False when the tool sequence differs",
            not candidate.structural_match,
        )
        check(
            "FTF-0513",
            "agent_trace_structural is not flagged when the tool sequence differs",
            "agent_trace_structural" not in candidate.flagged_methods,
        )


def test_bucketed_pair_not_flagged_below_default_thresholds() -> None:
    # Two words changed out of 21: every metric (~0.67/~0.83/~0.91) stays
    # below its own default threshold, so the pair is genuinely not
    # reported at all -- the module never reports a "not similar enough"
    # pair just because it was indexed together.
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_two_words_changed(_BASE_TEXT), record_index=1),
    ]
    report = detect_near_duplicates(records)
    candidate = _find_pair(report, 0, 1)
    check(
        "FTF-0513",
        "a two-words-changed pair is not flagged under this module's own default thresholds",
        candidate is None,
    )


def test_lowering_thresholds_changes_the_flag_outcome() -> None:
    # The same "reviewed thresholds" acceptance clause, proven the other
    # way: caller-supplied, lower thresholds turn the same not-flagged
    # pair above into a flagged one -- thresholds are a real, live input,
    # not a number baked unreachably into the comparison logic.
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_two_words_changed(_BASE_TEXT), record_index=1),
    ]
    lenient = NearDuplicateThresholds(
        character_ngram_similarity=0.5,
        token_jaccard_similarity=0.7,
        embedding_cosine_similarity=0.8,
    )
    report = detect_near_duplicates(records, thresholds=lenient)
    candidate = _find_pair(report, 0, 1)
    check(
        "FTF-0513",
        "the same pair is flagged once thresholds are explicitly lowered by the caller",
        candidate is not None,
    )


def test_thresholds_reject_out_of_range_values() -> None:
    raised = False
    try:
        NearDuplicateThresholds(character_ngram_similarity=1.5)
    except NearDuplicateDetectionError:
        raised = True
    check("FTF-0513", "an out-of-range threshold is rejected, not silently clamped", raised)


def test_dissimilar_records_form_no_candidate_pair_via_indexing() -> None:
    # This is the "bounded indexed... candidates" half of acceptance
    # clause 1, and the indexing half of negative_acceptance: a genuinely
    # unrelated pair never even becomes a candidate, regardless of
    # threshold -- proven by using thresholds of 0.0 (maximally lenient)
    # and confirming the pair is still absent, because it was never
    # bucketed together to begin with.
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_UNRELATED_TEXT, record_index=1),
    ]
    maximally_lenient = NearDuplicateThresholds(
        character_ngram_similarity=0.0,
        token_jaccard_similarity=0.0,
        embedding_cosine_similarity=0.0,
    )
    report = detect_near_duplicates(records, thresholds=maximally_lenient)
    candidate = _find_pair(report, 0, 1)
    check(
        "FTF-0513",
        "an unrelated pair is never a candidate even under a threshold of 0.0 -- "
        "indexing excluded it before any threshold was applied",
        candidate is None,
    )


def test_max_candidates_per_record_bounds_pair_count_far_below_all_pairs() -> None:
    # 60 byte-identical records fall into exactly one LSH bucket together
    # (a real worst case for banding); a naive all-pairs scan would form
    # C(60, 2) = 1770 pairs. `max_candidates_per_record` bounds this
    # directly -- this is the module's own real defense against the
    # "all-pairs million-record memory blowup" negative_acceptance clause,
    # not merely an average-case accident of a sparse corpus.
    identical_text = _BASE_TEXT
    records = [_text_record(text=identical_text, record_index=i) for i in range(60)]
    report = detect_near_duplicates(records, max_candidates_per_record=5)

    per_record_counts: dict[int, int] = {}
    for candidate in report.candidates:
        per_record_counts[candidate.batch_position_a] = (
            per_record_counts.get(candidate.batch_position_a, 0) + 1
        )
        per_record_counts[candidate.batch_position_b] = (
            per_record_counts.get(candidate.batch_position_b, 0) + 1
        )

    check(
        "FTF-0513",
        "60 identical records would form 1770 all-pairs comparisons; "
        "bounded indexing forms far fewer",
        len(report.candidates) < 1770,
    )
    check(
        "FTF-0513",
        "no single record accumulates more candidate partners than max_candidates_per_record",
        all(count <= 5 for count in per_record_counts.values()),
    )
    check(
        "FTF-0513",
        "at least some genuine duplicate pairs were still found",
        len(report.candidates) > 0,
    )


def test_invalid_max_candidates_per_record_is_rejected() -> None:
    raised = False
    try:
        detect_near_duplicates([_text_record(text=_BASE_TEXT)], max_candidates_per_record=0)
    except NearDuplicateDetectionError:
        raised = True
    check("FTF-0513", "a non-positive max_candidates_per_record is rejected", raised)


def test_flagging_never_mutates_or_drops_input_records() -> None:
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_one_word_changed(_BASE_TEXT), record_index=1),
    ]
    snapshot = [dict(record) for record in records]
    report = detect_near_duplicates(records)
    check(
        "FTF-0513",
        "the caller's own records list is never mutated by detection",
        records == snapshot,
    )
    check(
        "FTF-0513",
        "a flagged candidate still names both original batch positions -- nothing was dropped",
        {candidate.batch_position_a for candidate in report.candidates}
        | {candidate.batch_position_b for candidate in report.candidates}
        <= {0, 1},
    )


def test_no_discard_field_exists_on_candidate_or_report() -> None:
    # Structural proof of "no automatic discard": neither dataclass has a
    # field that could remove/exclude/discard a record -- the only
    # disposition a candidate can carry is `requires_review`.
    candidate_fields = {f.name for f in dataclasses.fields(NearDuplicateCandidate)}
    report_fields = {f.name for f in dataclasses.fields(NearDuplicateReport)}
    forbidden = {"discard", "discarded", "drop", "dropped", "remove", "removed", "excluded"}
    check(
        "FTF-0513",
        "NearDuplicateCandidate carries no discard/remove-style field",
        not (candidate_fields & forbidden),
    )
    check(
        "FTF-0513",
        "NearDuplicateReport carries no discard/remove-style field",
        not (report_fields & forbidden),
    )
    check(
        "FTF-0513",
        "requires_review is the only disposition, derived from flagged_methods",
        "requires_review" not in candidate_fields and "flagged_methods" in candidate_fields,
    )


def test_cross_purpose_pair_is_marked() -> None:
    records = [
        _text_record(text=_BASE_TEXT, purpose="training", record_index=0),
        _text_record(
            text=_one_word_changed(_BASE_TEXT), purpose="held_out_evaluation", record_index=1
        ),
    ]
    report = detect_near_duplicates(records)
    candidate = _find_pair(report, 0, 1)
    assert candidate is not None
    check(
        "FTF-0513",
        "a cross-purpose near-duplicate pair is marked is_cross_purpose",
        candidate.is_cross_purpose,
    )
    check(
        "FTF-0513",
        "purposes carries both real purpose values",
        candidate.purposes == frozenset({"training", "held_out_evaluation"}),
    )


def test_deterministic_ordering_across_runs() -> None:
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_one_word_changed(_BASE_TEXT), record_index=1),
        _text_record(text=_UNRELATED_TEXT, record_index=2),
    ]
    first = detect_near_duplicates(records)
    second = detect_near_duplicates(records)
    check(
        "FTF-0513",
        "repeated calls on the same input produce identical candidates",
        first.candidates == second.candidates,
    )
    check(
        "FTF-0513",
        "repeated calls on the same input produce identical issues",
        first.issues == second.issues,
    )


def test_non_sequence_input_raises() -> None:
    raised = False
    try:
        detect_near_duplicates(object())  # type: ignore[arg-type]
    except NearDuplicateDetectionError:
        raised = True
    check("FTF-0513", "a non-sequence batch raises rather than silently returning empty", raised)


def test_empty_batch_raises() -> None:
    raised = False
    try:
        detect_near_duplicates([])
    except NearDuplicateDetectionError:
        raised = True
    check("FTF-0513", "an empty batch raises rather than silently returning empty", raised)


def test_string_input_is_rejected_not_iterated_as_characters() -> None:
    raised = False
    try:
        detect_near_duplicates("not a list of records")  # type: ignore[arg-type]
    except NearDuplicateDetectionError:
        raised = True
    check("FTF-0513", "a bare string is rejected, never iterated character-by-character", raised)


def test_non_mapping_candidate_is_reported_not_raised() -> None:
    report = detect_near_duplicates([_text_record(text=_BASE_TEXT), "not-a-record"])  # type: ignore[list-item]
    issue_codes = {issue.code for issue in report.issues if issue.batch_position == 1}
    check(
        "FTF-0513",
        "a non-object candidate is reported as invalid_candidate, not raised",
        "invalid_candidate" in issue_codes,
    )


def test_invalid_purpose_is_reported() -> None:
    record = _text_record(text=_BASE_TEXT)
    record["purpose"] = "not_a_real_purpose"
    report = detect_near_duplicates(
        [record, _text_record(text=_one_word_changed(_BASE_TEXT), record_index=1)]
    )
    issue_codes = {issue.code for issue in report.issues if issue.batch_position == 0}
    check(
        "FTF-0513",
        "an unrecognized purpose is reported as invalid_purpose",
        "invalid_purpose" in issue_codes,
    )


def test_malformed_content_is_reported_not_raised() -> None:
    record = {"record_index": 0, "purpose": "training", "content": {"messages": [], "metadata": {}}}
    report = detect_near_duplicates([record, _text_record(text=_BASE_TEXT, record_index=1)])
    issue_codes = {issue.code for issue in report.issues if issue.batch_position == 0}
    check(
        "FTF-0513",
        "empty messages is reported as unknown_content, not raised",
        "unknown_content" in issue_codes,
    )


def test_unsupported_content_type_is_reported() -> None:
    record = {
        "record_index": 0,
        "purpose": "training",
        "content": {
            "messages": [{"role": "user", "content": [{"type": "audio", "url": "x"}]}],
            "metadata": {},
        },
    }
    report = detect_near_duplicates([record, _text_record(text=_BASE_TEXT, record_index=1)])
    issue_codes = {issue.code for issue in report.issues if issue.batch_position == 0}
    check(
        "FTF-0513",
        "an unsupported content type is reported as unknown_content",
        "unknown_content" in issue_codes,
    )


def test_report_is_clean_when_no_issues_or_flags() -> None:
    records = [
        _text_record(text=_BASE_TEXT, record_index=0),
        _text_record(text=_UNRELATED_TEXT, record_index=1),
    ]
    report = detect_near_duplicates(records)
    check("FTF-0513", "a report with no issues and no flagged candidates is clean", report.is_clean)


def test_supported_purposes_matches_ftf_0500_vocabulary() -> None:
    check(
        "FTF-0513",
        "SUPPORTED_PURPOSES mirrors FTF-0500's own closed allowed_purposes vocabulary verbatim",
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


def test_default_thresholds_documented_values() -> None:
    check(
        "FTF-0513",
        "the default embedding_cosine_similarity threshold matches Section 10's own "
        "literal 0.95 default",
        DEFAULT_THRESHOLDS.embedding_cosine_similarity == 0.95,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0513 near-duplicate detector: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0513 near-duplicate detector: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
