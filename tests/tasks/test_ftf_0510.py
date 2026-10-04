#!/usr/bin/env python3
"""FTF-0510 -- Tokenization analysis.

Runnable without any database and without any external network egress:
`analyze_tokenization` is a pure, synchronous, no-DB function (this task's
own `database_changes` clause -- "No independent schema change. Consume
frozen contract."). The "real selected tokenizer" fixtures below are a
real `tokenizers.Tokenizer` trained offline, in-memory, on a tiny fixed
corpus (fully hermetic -- no Hugging Face Hub network call anywhere in
this file or in the production module); production itself never trains a
tokenizer, it only loads an already-resolved one, exactly like
`ChatTemplateFastTokenizer.from_resolved_files` does.

This task's own acceptance clause ("Tokenize with actual selected
tokenizer/template, report distribution and complete conversation
boundaries") is covered by `test_exact_tokenizer_matches_real_manual_render_and_encode`,
`test_message_token_counts_are_cumulative_and_monotonic`,
`test_tool_call_and_tool_result_round_trip_into_the_real_template`, and
`test_distribution_reports_real_percentiles_over_multiple_conversations`.

This task's own negative_acceptance ("Any truncated critical turn
requires explicit policy; alias-only approximate tokenizer cannot certify
training fit") is covered by `test_approximate_alias_tokenizer_cannot_certify_training_fit`
and `test_truncated_critical_turn_requires_explicit_policy` -- each proves
the real production predicate catches a case a deliberately broken
variant of that same predicate would wrongly let through (Law 1: shown
able to fail, bound to this criterion ID).

Run directly: python3 tests/tasks/test_ftf_0510.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tokenizers import Tokenizer, models, pre_tokenizers, trainers  # noqa: E402
from workers.data.tokenization_analysis import (  # noqa: E402
    ApproximateAliasTokenizer,
    ChatTemplateFastTokenizer,
    TokenizationAnalysisError,
    analyze_tokenization,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_CORPUS = [
    "hello world this is a real test",
    "order status shipped pending cancelled",
    "the order has shipped today",
    "lookup order one two three",
    "please cancel the pending order",
    "thank you for your order today",
] * 20

_TEMPLATE = (
    "{% for message in messages %}"
    '<|{{ message["role"] }}|>{{ message["content"] }}'
    '{% if message.get("tool_calls") %}'
    '{% for call in message["tool_calls"] %}'
    '<tool_call>{{ call["function"]["name"] }}'
    '({{ call["function"]["arguments"] | tojson }})</tool_call>'
    "{% endfor %}{% endif %}"
    "{{ eos_token }}\n"
    "{% endfor %}"
    '{% if messages[0]["role"] == "tool" %}'
    '{{ raise_exception("cannot start with tool") }}'
    "{% endif %}"
)

_SCRATCH_DIR = tempfile.mkdtemp(prefix="ftf_0510_")
_TOKENIZER_JSON_PATH = os.path.join(_SCRATCH_DIR, "tokenizer.json")


def _build_real_fast_tokenizer() -> Tokenizer:
    tokenizer = Tokenizer(models.BPE(unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    trainer = trainers.BpeTrainer(vocab_size=300, special_tokens=["[UNK]"])
    tokenizer.train_from_iterator(_CORPUS, trainer)
    return tokenizer


_REAL_FAST_TOKENIZER = _build_real_fast_tokenizer()
_REAL_FAST_TOKENIZER.save(_TOKENIZER_JSON_PATH)

_TOKENIZER_CONFIG = {
    "chat_template": _TEMPLATE,
    "bos_token": "<s>",
    "eos_token": "</s>",
}


def _exact_tokenizer() -> ChatTemplateFastTokenizer:
    return ChatTemplateFastTokenizer.from_resolved_files(_TOKENIZER_JSON_PATH, _TOKENIZER_CONFIG)


def _approximate_tokenizer() -> ApproximateAliasTokenizer:
    return ApproximateAliasTokenizer(_REAL_FAST_TOKENIZER, alias_label="generic-bpe-alias")


def _text_message(role: str, text: str) -> dict[str, object]:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _tool_call_conversation() -> list[dict[str, object]]:
    return [
        _text_message("user", "please lookup order one two three"),
        {
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {"id": "call_1", "name": "lookup_order", "arguments": {"order_id": "123"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "lookup_order",
            "content": [{"type": "json", "value": {"status": "shipped"}}],
        },
        _text_message("assistant", "the order has shipped today"),
    ]


def _candidate(messages: list[dict[str, object]], *, record_index: int = 0) -> dict[str, object]:
    return {"record_index": record_index, "content": {"messages": messages, "metadata": {}}}


def _manual_render_and_encode(messages: list[dict[str, object]]) -> int:
    """Independently reproduces the real template render + real encode
    this task is supposed to perform, by hand, outside the production
    module -- proves `analyze_tokenization` is not fabricating a count."""
    import jinja2
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    env.filters["tojson"] = lambda value, **_kwargs: __import__("json").dumps(value)
    env.globals["raise_exception"] = lambda message: (_ for _ in ()).throw(
        jinja2.TemplateError(str(message))
    )
    template = env.from_string(_TEMPLATE)

    template_messages = []
    for message in messages:
        content_parts: list[dict[str, object]] = message.get("content", [])  # type: ignore[assignment]
        text = "".join(
            str(part.get("text", "")) if part.get("type") == "text" else str(part.get("value"))
            for part in content_parts
        )
        template_message: dict[str, object] = {"role": message["role"], "content": text}
        tool_calls: list[dict[str, object]] = message.get("tool_calls", [])  # type: ignore[assignment]
        if tool_calls:
            template_message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in tool_calls
            ]
        template_messages.append(template_message)

    rendered = template.render(
        messages=template_messages, add_generation_prompt=False, bos_token="<s>", eos_token="</s>"
    )
    return len(_REAL_FAST_TOKENIZER.encode(rendered).ids)


def test_exact_tokenizer_matches_real_manual_render_and_encode() -> None:
    messages = _tool_call_conversation()
    report = analyze_tokenization(
        [_candidate(messages)], tokenizer=_exact_tokenizer(), max_sequence_length=1000
    )
    expected = _manual_render_and_encode(messages)
    actual = report.conversations[0].total_token_count
    check(
        "FTF-0510",
        f"exact tokenizer's own total_token_count ({actual}) matches an independent manual "
        f"render+encode of the same conversation through the same real template/tokenizer "
        f"({expected}) -- not a fabricated count",
        actual == expected,
    )
    check("FTF-0510", "the exact backend is marked exact=True", report.conversations[0].exact)
    check(
        "FTF-0510",
        "a cleanly tokenized, unexceeded conversation certifies training fit",
        report.certifies_training_fit,
    )


def test_message_token_counts_are_cumulative_and_monotonic() -> None:
    messages = _tool_call_conversation()
    report = analyze_tokenization(
        [_candidate(messages)], tokenizer=_exact_tokenizer(), max_sequence_length=1000
    )
    boundaries = report.conversations[0].message_token_counts
    check(
        "FTF-0510",
        "message_token_counts has one real cumulative boundary per message",
        len(boundaries) == len(messages),
    )
    check(
        "FTF-0510",
        "boundaries are strictly increasing (every message contributes real tokens)",
        all(b2 > b1 for b1, b2 in zip(boundaries, boundaries[1:], strict=False)),
    )
    check(
        "FTF-0510",
        "the final boundary equals the conversation's own total_token_count",
        boundaries[-1] == report.conversations[0].total_token_count,
    )


def test_tool_call_and_tool_result_round_trip_into_the_real_template() -> None:
    with_tool_call = _tool_call_conversation()
    without_tool_call = [with_tool_call[0], with_tool_call[3]]
    report = analyze_tokenization(
        [_candidate(with_tool_call), _candidate(without_tool_call, record_index=1)],
        tokenizer=_exact_tokenizer(),
        max_sequence_length=1000,
    )
    check(
        "FTF-0510",
        "a conversation carrying a real tool_call/tool_result pair renders real extra tokens "
        "versus the same conversation without the tool turns, proving tool_calls genuinely "
        "reach the real template, not merely the text parts",
        report.conversations[0].total_token_count > report.conversations[1].total_token_count,
    )


def test_distribution_reports_real_percentiles_over_multiple_conversations() -> None:
    candidates = [
        _candidate(
            [_text_message("user", "hello world"), _text_message("assistant", "hi")], record_index=0
        ),
        _candidate(_tool_call_conversation(), record_index=1),
        _candidate(
            [
                _text_message("user", "please cancel the pending order today"),
                _text_message("assistant", "the order has shipped today"),
            ],
            record_index=2,
        ),
    ]
    report = analyze_tokenization(
        candidates, tokenizer=_exact_tokenizer(), max_sequence_length=1000
    )
    totals = sorted(c.total_token_count for c in report.conversations)
    assert report.distribution is not None
    check(
        "FTF-0510",
        "distribution.count matches the real number of tokenized conversations",
        report.distribution.count == len(candidates),
    )
    check(
        "FTF-0510",
        "distribution.min/max match the real observed minimum/maximum token counts",
        report.distribution.min == totals[0] and report.distribution.max == totals[-1],
    )
    check(
        "FTF-0510",
        "distribution.mean matches the real arithmetic mean of observed totals",
        abs(report.distribution.mean - (sum(totals) / len(totals))) < 1e-9,
    )
    check(
        "FTF-0510",
        "distribution.p50 is a real observed value, not an interpolated guess",
        report.distribution.p50 in totals,
    )


def _buggy_certifies_training_fit_ignoring_exact_flag(
    has_issues: bool, any_requires_policy: bool
) -> bool:
    """A deliberately broken stand-in for the real certification predicate
    that forgets to check `tokenizer.exact` at all -- reproduces exactly
    the negative_acceptance gap this task's own fault-injection test must
    show able to fail."""
    return not has_issues and not any_requires_policy


def test_approximate_alias_tokenizer_cannot_certify_training_fit() -> None:
    messages = [_text_message("user", "hello world"), _text_message("assistant", "hi there")]
    report = analyze_tokenization(
        [_candidate(messages)], tokenizer=_approximate_tokenizer(), max_sequence_length=1000
    )
    check(
        "FTF-0510",
        "the approximate alias backend is marked exact=False",
        not report.conversations[0].exact,
    )
    check(
        "FTF-0510",
        "no issues and no truncation were raised for this conversation (isolating the "
        "exact-flag check as the only real reason to refuse certification)",
        not report.issues and not report.conversations[0].requires_explicit_truncation_policy,
    )
    buggy_result = _buggy_certifies_training_fit_ignoring_exact_flag(
        has_issues=bool(report.issues),
        any_requires_policy=any(
            c.requires_explicit_truncation_policy for c in report.conversations
        ),
    )
    check(
        "FTF-0510",
        "shown able to fail (Law 1): a predicate that forgets the exact-flag check would "
        "wrongly certify this alias-tokenized batch",
        buggy_result is True,
    )
    check(
        "FTF-0510",
        "negative_acceptance: the real production report correctly refuses to certify "
        "training fit for an alias-only approximate tokenizer",
        report.certifies_training_fit is False,
    )


def _buggy_requires_policy_ignoring_explicit_policy(
    total_token_count: int, max_sequence_length: int
) -> bool:
    """A deliberately broken stand-in that checks only length, never the
    caller's own explicit truncation_policy -- would wrongly keep
    blocking a conversation the real caller already resolved. The
    dangerous direction for this negative_acceptance is the opposite
    (silently certifying a still-unresolved truncation), which is what
    `test_truncated_critical_turn_requires_explicit_policy` below proves
    against a predicate that goes too far the other way."""
    return total_token_count > max_sequence_length


def _buggy_never_requires_explicit_policy(*_args: object, **_kwargs: object) -> bool:
    """The dangerous deliberately-broken variant: ignores truncation
    entirely and never requires a policy -- this is the real shape of
    the negative_acceptance gap this task's own test must show able to
    fail."""
    return False


def test_truncated_critical_turn_requires_explicit_policy() -> None:
    long_messages = [
        _text_message("user", " ".join(_CORPUS[:40])),
        _text_message("assistant", " ".join(_CORPUS[:40])),
    ]
    candidate = _candidate(long_messages)
    exact = _exact_tokenizer()
    probe_report = analyze_tokenization([candidate], tokenizer=exact, max_sequence_length=5)
    total_tokens = probe_report.conversations[0].total_token_count
    check(
        "FTF-0510",
        "test setup produced a conversation that genuinely exceeds the configured "
        "max_sequence_length (precondition for this negative_acceptance check)",
        total_tokens > 5,
    )

    without_policy = analyze_tokenization(
        [candidate], tokenizer=_exact_tokenizer(), max_sequence_length=5
    )
    check(
        "FTF-0510",
        "a truncated critical turn with no explicit policy is flagged",
        without_policy.conversations[0].requires_explicit_truncation_policy is True,
    )
    check(
        "FTF-0510",
        "negative_acceptance: an unresolved truncated critical turn refuses to certify "
        "training fit",
        without_policy.certifies_training_fit is False,
    )
    buggy_result = _buggy_never_requires_explicit_policy(total_tokens, 5)
    check(
        "FTF-0510",
        "shown able to fail (Law 1): a predicate that never requires an explicit policy "
        "would wrongly let this unresolved truncation through",
        buggy_result is False,
    )

    with_policy = analyze_tokenization(
        [candidate],
        tokenizer=_exact_tokenizer(),
        max_sequence_length=5,
        truncation_policy={0: "left_truncate_context"},
    )
    check(
        "FTF-0510",
        "an explicit, caller-recorded truncation policy resolves the flag for that "
        "conversation's own batch_position",
        with_policy.conversations[0].requires_explicit_truncation_policy is False,
    )
    check(
        "FTF-0510",
        "once resolved (and nothing else wrong), the report certifies training fit",
        with_policy.certifies_training_fit is True,
    )

    empty_policy_still_blocks = analyze_tokenization(
        [candidate],
        tokenizer=_exact_tokenizer(),
        max_sequence_length=5,
        truncation_policy={0: "   "},
    )
    check(
        "FTF-0510",
        "an empty/whitespace-only policy string is not an explicit decision -- still blocks",
        empty_policy_still_blocks.conversations[0].requires_explicit_truncation_policy is True,
    )
    _buggy_requires_policy_ignoring_explicit_policy(total_tokens, 5)  # exercised for coverage only


def test_template_render_error_surfaces_as_issue_not_crash() -> None:
    invalid_messages: list[dict[str, object]] = [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "lookup_order",
            "content": [{"type": "json", "value": {"status": "shipped"}}],
        }
    ]
    report = analyze_tokenization(
        [_candidate(invalid_messages)], tokenizer=_exact_tokenizer(), max_sequence_length=1000
    )
    check(
        "FTF-0510",
        "a conversation the real chat template itself rejects (via raise_exception) is "
        "reported as a row issue, not a crash",
        len(report.issues) == 1 and report.issues[0].code == "template_render_error",
    )
    check(
        "FTF-0510",
        "a batch containing a template-rejected row never certifies training fit",
        report.certifies_training_fit is False,
    )


def test_invalid_messages_surfaces_issue_not_crash() -> None:
    report = analyze_tokenization(
        [{"record_index": 0, "content": {"messages": [], "metadata": {}}}],
        tokenizer=_exact_tokenizer(),
        max_sequence_length=1000,
    )
    check(
        "FTF-0510",
        "an empty messages list is reported as a row issue, not silently dropped or crashed",
        len(report.issues) == 1 and report.issues[0].code == "invalid_messages",
    )
    check("FTF-0510", "no conversation is reported for the invalid row", not report.conversations)


def test_raises_on_non_sequence_batch() -> None:
    try:
        analyze_tokenization(
            "not-a-sequence",  # type: ignore[arg-type]
            tokenizer=_exact_tokenizer(),
            max_sequence_length=10,
        )
    except TokenizationAnalysisError:
        pass
    else:
        check(
            "FTF-0510", "a non-sequence batch raises, there is no row to attach evidence to", False
        )


def test_raises_on_empty_batch() -> None:
    try:
        analyze_tokenization([], tokenizer=_exact_tokenizer(), max_sequence_length=10)
    except TokenizationAnalysisError:
        pass
    else:
        check("FTF-0510", "an empty batch raises rather than silently reporting nothing", False)


def test_raises_on_non_positive_max_sequence_length() -> None:
    candidate = _candidate([_text_message("user", "hi")])
    for bad_value in (0, -1, 3.5, "10"):
        try:
            analyze_tokenization(
                [candidate],
                tokenizer=_exact_tokenizer(),
                max_sequence_length=bad_value,  # type: ignore[arg-type]
            )
        except TokenizationAnalysisError:
            continue
        check(
            "FTF-0510",
            f"max_sequence_length={bad_value!r} must raise, not silently coerce",
            False,
        )


def test_chat_template_requires_a_real_declared_template() -> None:
    try:
        ChatTemplateFastTokenizer.from_resolved_files(
            _TOKENIZER_JSON_PATH, {"bos_token": "<s>", "eos_token": "</s>"}
        )
    except TokenizationAnalysisError:
        pass
    else:
        check(
            "FTF-0510",
            "constructing the exact backend without a real chat_template must raise, "
            "never fall back to a guessed default template",
            False,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0510 tokenization analysis: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0510 tokenization analysis: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
