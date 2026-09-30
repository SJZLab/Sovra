#!/usr/bin/env python3
"""FTF-0514 -- PII/secrets detector.

Runnable without any database and without any external network egress:
`detect_pii_and_secrets` is a pure, synchronous, no-I/O function (this
task's own `database_changes` clause -- "No independent schema change.
Consume frozen contract.").

This task's own acceptance clause ("Run secrets/PII detectors inside
private boundary and quarantine required findings with human resolution")
is covered by `test_email_detected`, `test_phone_number_detected`,
`test_credit_card_with_valid_luhn_detected`, `test_us_ssn_detected`,
`test_aws_access_key_detected`, `test_bearer_token_detected`,
`test_private_key_block_detected`, `test_high_entropy_secret_detected`,
`test_finding_marks_requires_quarantine_but_status_stays_detected_not_quarantined`,
`test_pii_status_for_clean_record`, and
`test_json_content_and_tool_call_arguments_are_scanned`.

This task's own negative_acceptance ("Detection absence is not proof of no
PII; false negatives/residual risks recorded and real secrets not logged")
is covered by `test_residual_risk_disclosure_present_and_nonempty`,
`test_low_entropy_secret_like_string_is_a_genuine_false_negative`,
`test_bare_personal_name_is_a_genuine_false_negative`,
`test_credit_card_invalid_luhn_not_detected`, and
`test_finding_never_carries_raw_or_partial_secret_value`.

Run directly: python3 tests/tasks/test_ftf_0514.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.data.pii_secrets_detector import (  # noqa: E402
    PII_SECRET_CATEGORIES,
    RESIDUAL_RISK_DISCLOSURE,
    PiiSecretsDetectionError,
    detect_pii_and_secrets,
)
from workers.data.record_schema_validator import PII_STATUS_VALUES  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _text_record(text: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "record_index": 0,
        "content": {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": text}]},
            ],
            "metadata": {},
        },
    }
    record.update(overrides)
    return record


PLANTED_EMAIL = "jane.doe@example.com"
PLANTED_PHONE = "+1-555-123-4567"
PLANTED_VALID_CC = "4111111111111111"
PLANTED_INVALID_CC = "4111111111111112"
PLANTED_SSN = "123-45-6789"
PLANTED_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
PLANTED_BEARER = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
PLANTED_PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----"
PLANTED_HIGH_ENTROPY = "Zk8mQ2xRt9LpN4vWc7Yj"
PLANTED_LOW_ENTROPY = "aaaaaaaaaaaaaaaaaaaa"


def test_email_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"Contact me at {PLANTED_EMAIL} please.")])
    check(
        "FTF-0514",
        "an embedded email address is detected as category 'email'",
        any(f.category == "email" for f in report.findings),
    )


def test_phone_number_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"Call me at {PLANTED_PHONE} tomorrow.")])
    check(
        "FTF-0514",
        "an embedded phone number is detected as category 'phone_number'",
        any(f.category == "phone_number" for f in report.findings),
    )


def test_credit_card_with_valid_luhn_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"Card on file: {PLANTED_VALID_CC}.")])
    check(
        "FTF-0514",
        "a Luhn-valid credit card number is detected as category 'credit_card'",
        any(f.category == "credit_card" for f in report.findings),
    )


def test_credit_card_invalid_luhn_not_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"Reference number: {PLANTED_INVALID_CC}.")])
    check(
        "FTF-0514",
        "negative_acceptance: a Luhn-invalid 16-digit number is never flagged as a "
        "credit card -- a real, disclosed false-negative control, not a bug",
        not any(f.category == "credit_card" for f in report.findings),
    )


def test_us_ssn_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"SSN on file: {PLANTED_SSN}.")])
    check(
        "FTF-0514",
        "an embedded US SSN-shaped value is detected as category 'us_ssn'",
        any(f.category == "us_ssn" for f in report.findings),
    )


def test_aws_access_key_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"key={PLANTED_AWS_KEY}")])
    check(
        "FTF-0514",
        "an AWS access key ID shape is detected as category 'aws_access_key_id'",
        any(f.category == "aws_access_key_id" for f in report.findings),
    )


def test_bearer_token_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"Authorization: {PLANTED_BEARER}")])
    check(
        "FTF-0514",
        "a Bearer-token-shaped value is detected as category 'bearer_token'",
        any(f.category == "bearer_token" for f in report.findings),
    )


def test_private_key_block_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"{PLANTED_PRIVATE_KEY}\nMIIBogIBAAJ...")])
    check(
        "FTF-0514",
        "a PEM private-key block header is detected as category 'private_key_block'",
        any(f.category == "private_key_block" for f in report.findings),
    )


def test_high_entropy_secret_detected() -> None:
    report = detect_pii_and_secrets([_text_record(f"token: {PLANTED_HIGH_ENTROPY}")])
    check(
        "FTF-0514",
        "a high-entropy 20+ char token matching no named pattern is still caught by "
        "the generic 'high_entropy_secret' heuristic",
        any(f.category == "high_entropy_secret" for f in report.findings),
    )


def test_finding_marks_requires_quarantine_but_status_stays_detected_not_quarantined() -> None:
    report = detect_pii_and_secrets([_text_record(f"email {PLANTED_EMAIL}")])
    check(
        "FTF-0514",
        "acceptance: every finding is marked requires_quarantine=True",
        all(f.requires_quarantine for f in report.findings),
    )
    check(
        "FTF-0514",
        "acceptance: quarantine needs human resolution -- this pure detector asserts "
        "'detected', never manufactures 'quarantined' itself",
        report.pii_status_for(0) == "detected",
    )
    check(
        "FTF-0514",
        "pii_status_for()'s own return values stay inside FTF-0508's real "
        "PII_STATUS_VALUES vocabulary, reused directly rather than re-declared",
        report.pii_status_for(0) in PII_STATUS_VALUES,
    )


def test_pii_status_for_clean_record() -> None:
    report = detect_pii_and_secrets([_text_record("Thanks for reaching out, have a nice day.")])
    check(
        "FTF-0514",
        "a record with no findings reports pii_status_for() == 'clean'",
        report.pii_status_for(0) == "clean" and report.is_clean,
    )


def test_json_content_and_tool_call_arguments_are_scanned() -> None:
    record: dict[str, object] = {
        "record_index": 0,
        "content": {
            "messages": [
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "send_email",
                            "arguments": {"to": PLANTED_EMAIL, "subject": "hi"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": [{"type": "json", "value": {"contact": PLANTED_EMAIL}}],
                },
            ],
            "metadata": {},
        },
    }
    report = detect_pii_and_secrets([record])
    check(
        "FTF-0514",
        "negative_acceptance-adjacent breadth: a tool call's own arguments are scanned, "
        "not only prose text content",
        any(f.content_path.startswith("tool_calls[0].arguments") for f in report.findings),
    )
    check(
        "FTF-0514",
        "a json content part's own nested string leaves are scanned, not treated as "
        "opaque business data the way FTF-0512's dedup hash treats them",
        any(f.content_path.startswith("content[0].value") for f in report.findings),
    )


def test_residual_risk_disclosure_present_and_nonempty() -> None:
    report = detect_pii_and_secrets([_text_record("hello")])
    check(
        "FTF-0514",
        "negative_acceptance: RESIDUAL_RISK_DISCLOSURE is a real, non-empty, "
        "machine-readable field on every report -- not just a code comment",
        isinstance(report.residual_risk_disclosure, str)
        and len(report.residual_risk_disclosure) > 40
        and report.residual_risk_disclosure == RESIDUAL_RISK_DISCLOSURE,
    )


def test_low_entropy_secret_like_string_is_a_genuine_false_negative() -> None:
    report = detect_pii_and_secrets([_text_record(f"token: {PLANTED_LOW_ENTROPY}")])
    check(
        "FTF-0514",
        "negative_acceptance: a 20-char low-entropy repeated-character string is a real, "
        "disclosed false negative -- demonstrating the residual-risk disclosure is true, "
        "not decorative",
        not report.findings,
    )


def test_bare_personal_name_is_a_genuine_false_negative() -> None:
    report = detect_pii_and_secrets(
        [_text_record("My name is Jordan Alexander Rivera and I was born in Ohio.")]
    )
    check(
        "FTF-0514",
        "negative_acceptance: unstructured PII (a bare personal name) matches none of "
        "this module's named/entropy patterns -- a real, disclosed limitation",
        not report.findings,
    )


def test_finding_never_carries_raw_or_partial_secret_value() -> None:
    planted_secrets = [
        PLANTED_EMAIL,
        PLANTED_PHONE,
        PLANTED_VALID_CC,
        PLANTED_SSN,
        PLANTED_AWS_KEY,
        PLANTED_BEARER,
        PLANTED_HIGH_ENTROPY,
    ]
    text = " | ".join(planted_secrets)
    report = detect_pii_and_secrets([_text_record(text)])

    check(
        "FTF-0514",
        "sanity: the planted-secret record actually produced findings",
        bool(report.findings),
    )

    for finding in report.findings:
        rendered = repr(finding)
        for secret in planted_secrets:
            check(
                "FTF-0514",
                f"negative_acceptance: real secrets not logged -- a {finding.category} "
                "finding's own repr never contains a planted raw secret substring",
                secret not in rendered,
            )
            # Also guard against a partial/prefix leak (the first several
            # characters of a planted secret appearing verbatim).
            check(
                "FTF-0514",
                f"negative_acceptance: real secrets not logged -- a {finding.category} "
                "finding's own repr never contains a 6+ char prefix of a planted secret",
                secret[:6] not in rendered if len(secret) >= 6 else True,
            )


def test_malformed_candidate_reported_as_issue_not_raised() -> None:
    report = detect_pii_and_secrets(["not-an-object"])  # type: ignore[list-item]
    check(
        "FTF-0514",
        "a non-object candidate is reported as an issue, not raised",
        any(issue.code == "invalid_candidate" for issue in report.issues),
    )
    check(
        "FTF-0514",
        "a malformed batch never produces findings for the bad row",
        not report.findings,
    )


def test_invalid_content_shape_reported_as_issue() -> None:
    report = detect_pii_and_secrets([{"record_index": 0, "content": "not-an-object"}])
    check(
        "FTF-0514",
        "a candidate with malformed content is reported as an 'unknown_content' issue, "
        "never raised",
        any(issue.code == "unknown_content" for issue in report.issues),
    )


def test_invalid_message_role_reported_as_issue() -> None:
    record = {
        "record_index": 0,
        "content": {
            "messages": [{"role": "narrator", "content": [{"type": "text", "text": "x"}]}],
            "metadata": {},
        },
    }
    report = detect_pii_and_secrets([record])
    check(
        "FTF-0514",
        "an unmapped role is reported as an 'invalid_message' issue, never raised",
        any(issue.code == "invalid_message" for issue in report.issues),
    )


def test_non_sequence_batch_raises() -> None:
    raised = False
    try:
        detect_pii_and_secrets(object())  # type: ignore[arg-type]
    except PiiSecretsDetectionError:
        raised = True
    check(
        "FTF-0514",
        "a non-sequence batch raises PiiSecretsDetectionError outright -- there is no "
        "row to attach that failure's evidence to",
        raised,
    )


def test_empty_batch_raises() -> None:
    raised = False
    try:
        detect_pii_and_secrets([])
    except PiiSecretsDetectionError:
        raised = True
    check("FTF-0514", "an empty batch raises PiiSecretsDetectionError outright", raised)


def test_multiple_findings_in_one_record() -> None:
    text = f"email {PLANTED_EMAIL} and card {PLANTED_VALID_CC}"
    report = detect_pii_and_secrets([_text_record(text)])
    categories = {f.category for f in report.findings}
    check(
        "FTF-0514",
        "a record with two distinct planted secrets produces findings for both categories",
        {"email", "credit_card"} <= categories,
    )


def test_pii_secret_categories_is_closed_and_matches_named_patterns() -> None:
    check(
        "FTF-0514",
        "PII_SECRET_CATEGORIES names exactly the closed set this module actually "
        "implements -- not a placeholder superset",
        PII_SECRET_CATEGORIES
        == frozenset(
            {
                "email",
                "phone_number",
                "credit_card",
                "us_ssn",
                "aws_access_key_id",
                "bearer_token",
                "private_key_block",
                "high_entropy_secret",
            }
        ),
    )


def test_finding_categories_stay_inside_declared_closed_set() -> None:
    text = " | ".join(
        [
            PLANTED_EMAIL,
            PLANTED_PHONE,
            PLANTED_VALID_CC,
            PLANTED_SSN,
            PLANTED_AWS_KEY,
            PLANTED_BEARER,
            PLANTED_PRIVATE_KEY,
            PLANTED_HIGH_ENTROPY,
        ]
    )
    report = detect_pii_and_secrets([_text_record(text)])
    check(
        "FTF-0514",
        "every produced finding's category is a member of the declared closed "
        "PII_SECRET_CATEGORIES set",
        all(f.category in PII_SECRET_CATEGORIES for f in report.findings),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0514 PII/secrets detector: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0514 PII/secrets detector: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
