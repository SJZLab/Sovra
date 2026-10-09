#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,var-annotated,dict-item"
# ruff: noqa: E501
"""FTF-R004-P1 -- Inspect bridge generation-config parity comparator.

Runnable without any database or network:
`capture_effective_generation_settings`/`compare_generation_parity` are pure,
synchronous, no-I/O functions (no independent schema change, no migration,
no OpenAPI path, no UI).

Acceptance ("Compare effective target generation settings before and after
the Inspect bridge; preserve the approved temperature/top_p/token/reasoning
settings or record an explicit candidate configuration change in the locked
compatibility tuple. Do not silently inherit bridge defaults."):
test_preserved_when_every_key_matches_after,
test_dropped_temperature_blocks (fault FA),
test_changed_max_tokens_blocks (fault FB),
test_inherited_default_top_p_blocks (fault FC),
test_declared_change_with_matching_after_value_passes,
test_raw_max_tokens_family_keys_are_kept_independently.

Negative acceptance ("Reject silent generation-config dropping,
undeclared ... and lost pre-filter forbidden-action evidence in the
bridge" -- the generation-config half of it):
test_declared_change_value_mismatch_still_blocks (fault FD),
test_declared_change_without_a_valid_digest_is_refused (fault FE),
test_missing_observation_gives_insufficient_evidence_never_preserved (fault FF),
test_canary_prompt_string_never_appears_in_capture_or_result (fault FG),
test_values_compared_exactly_not_by_python_equality,
test_malformed_input_raises_never_type_error_never_allow.

Round 2 (L2 return, 2026-10-09T07:48Z, QUEUE 0n):
test_content_free_capture_covers_every_content_key (F5/F6 and the new
  instructions/prompt/prediction/conversation/previous_response_id/
  extra_body.messages content keys),
test_near_equal_float_and_declared_bool_int_are_not_equal (F8/F10),
test_before_after_mismatch_blocks_even_when_approved_matches_after (item 3),
test_stale_declared_change_blocks (item 4),
test_reason_codes_and_compatibility_tuple_are_stable (item 5),
test_cyclic_or_over_deep_values_raise_typed_error,
test_declared_change_values_are_frozen_and_deep_copied,
test_captured_settings_are_deep_copied (item 6).

Run directly: python3 tests/tasks/test_ftf_r004_p1.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.evaluation.adapters.inspect_adapter.generation_config_parity import (  # noqa: E402
    CONTENT_KEYS,
    REASON_BRIDGE_DEFAULT_INHERITED,
    REASON_GENERATION_CONFIG_CHANGED,
    REASON_GENERATION_CONFIG_DROPPED,
    DeclaredGenerationChange,
    GenerationConfigParityError,
    GenerationParityResult,
    capture_effective_generation_settings,
    compare_generation_parity,
)

FAILURES: list[str] = []
CID = "FTF-R004-P1"
VALID_DIGEST = "a" * 64


def _nested(depth: int, leaf: object = 1) -> object:
    """`depth` dict wrappers around `leaf`. Used to probe the exact nesting-
    depth boundary in _scrub_content/_exact_equal (MAX_NESTING_DEPTH=64):
    `_nested(64)` must never raise, `_nested(65)` always must."""
    value = leaf
    for _ in range(depth):
        value = {"n": value}
    return value


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(fn: Any) -> bool:
    try:
        fn()
    except GenerationConfigParityError:
        return True
    except Exception:  # noqa: BLE001 -- the wrong exception type is a failed check
        return False
    return False


def raises_any(fn: Any) -> bool:
    """Like raises(), but any exception counts -- used where the refusal is
    enforced by a stdlib type (MappingProxyType raising TypeError), not by
    this module's own GenerationConfigParityError."""
    try:
        fn()
    except Exception:  # noqa: BLE001 -- any exception means the write was refused
        return True
    return False


def compare(
    approved: Any,
    before: Any,
    after: Any,
    declared: DeclaredGenerationChange | None = None,
) -> GenerationParityResult:
    return compare_generation_parity(approved, before, after, declared_change=declared)


# --------------------------------------------------------------------------
# capture_effective_generation_settings
# --------------------------------------------------------------------------


def test_capture_reads_named_settings_keys_verbatim() -> None:
    body = {
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 500,
        "reasoning_effort": "medium",
        "seed": 42,
        "stop": ["\n\n"],
        "presence_penalty": 0.1,
        "frequency_penalty": 0.2,
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": "do the thing"}],
        "tools": [{"type": "function", "name": "lookup"}],
        "model": "candidate-v1",
        "stream": True,
        "user": "user-123",
        "metadata": {"trace": "abc"},
    }
    settings = capture_effective_generation_settings(body)
    check(
        CID,
        "every named settings key is captured verbatim",
        settings["temperature"] == 0.7
        and settings["top_p"] == 0.9
        and settings["max_tokens"] == 500
        and settings["reasoning_effort"] == "medium"
        and settings["seed"] == 42
        and settings["stop"] == ["\n\n"]
        and settings["presence_penalty"] == 0.1
        and settings["frequency_penalty"] == 0.2
        and settings["tool_choice"] == "auto"
        and settings["parallel_tool_calls"] is False
        and settings["response_format"] == {"type": "json_object"},
    )
    check(
        CID,
        "messages/tools/model/stream/user/metadata are never read",
        "messages" not in settings
        and "tools" not in settings
        and "model" not in settings
        and "stream" not in settings
        and "user" not in settings
        and "metadata" not in settings,
    )
    check(
        CID,
        "CONTENT_KEYS names exactly what was excluded",
        CONTENT_KEYS >= {"messages", "tools", "model", "stream", "user", "metadata"},
    )


def test_raw_max_tokens_family_keys_are_kept_independently() -> None:
    """Acceptance: raw key names are kept, never normalized to one field."""
    body = {"max_tokens": 1, "max_completion_tokens": 2, "max_output_tokens": 3}
    settings = capture_effective_generation_settings(body)
    check(
        CID,
        "max_tokens/max_completion_tokens/max_output_tokens stay three distinct keys",
        settings == {"max_tokens": 1, "max_completion_tokens": 2, "max_output_tokens": 3},
    )


def test_unknown_settings_key_is_kept_verbatim() -> None:
    settings = capture_effective_generation_settings({"logit_bias": {"50256": -100}, "n": 2})
    check(
        CID,
        "a forward-compatible, unnamed settings key is still captured, not silently dropped",
        settings == {"logit_bias": {"50256": -100}, "n": 2},
    )


def test_capture_malformed_input_fails_closed() -> None:
    for bad in (None, "x", 5, [("temperature", 0.7)], b"x"):
        check(
            CID,
            f"request_body={bad!r} is refused",
            raises(lambda b=bad: capture_effective_generation_settings(b)),
        )
    check(
        CID,
        "a non-string key is refused, never silently stringified",
        raises(lambda: capture_effective_generation_settings({5: 0.7})),
    )
    check(
        CID,
        "an empty-string key is refused",
        raises(lambda: capture_effective_generation_settings({"": 0.7})),
    )


# --------------------------------------------------------------------------
# compare_generation_parity -- preserved / dropped / changed / inherited
# --------------------------------------------------------------------------


def test_preserved_when_every_key_matches_after() -> None:
    approved = {"temperature": 0.7, "top_p": 0.9, "max_tokens": 500}
    result = compare(approved, dict(approved), dict(approved))
    check(
        CID,
        "identical approved/before/after is preserved with nothing blocking",
        result.verdict == "preserved"
        and result.dropped_keys == ()
        and result.changed_keys == ()
        and result.inherited_default_keys == ()
        and result.blocking_keys == (),
    )


def test_dropped_temperature_blocks() -> None:
    """Fault FA: silently skip recording a dropped key -> this must go RED."""
    approved = {"temperature": 0.7, "top_p": 0.9}
    result = compare(approved, dict(approved), {"top_p": 0.9})
    check(
        CID,
        "a bridge that drops temperature blocks, naming it as dropped",
        result.verdict == "blocked" and result.dropped_keys == ("temperature",),
    )
    check(CID, "...and it is in blocking_keys", "temperature" in result.blocking_keys)
    check(
        CID,
        "...never in changed or inherited",
        "temperature" not in result.changed_keys
        and "temperature" not in result.inherited_default_keys,
    )


def test_changed_max_tokens_blocks() -> None:
    """Fault FB: silently skip the equality check -> this must go RED."""
    approved = {"max_tokens": 500}
    result = compare(approved, dict(approved), {"max_tokens": 4096})
    check(
        CID,
        "a bridge that renames/replaces max_tokens with its own default blocks",
        result.verdict == "blocked" and result.changed_keys == ("max_tokens",),
    )
    check(
        CID,
        "...not reported as dropped or inherited",
        result.dropped_keys == () and result.inherited_default_keys == (),
    )


def test_inherited_default_top_p_blocks() -> None:
    """Fault FC: silently skip recording an inherited key -> this must go RED."""
    approved = {"temperature": 0.7}
    result = compare(approved, dict(approved), {"temperature": 0.7, "top_p": 1.0})
    check(
        CID,
        "a bridge-inserted default that neither approved nor before ever named blocks",
        result.verdict == "blocked" and result.inherited_default_keys == ("top_p",),
    )
    check(
        CID,
        "...not reported as dropped or changed",
        result.dropped_keys == () and result.changed_keys == (),
    )


def test_before_after_mismatch_blocks_even_when_approved_matches_after() -> None:
    """Round 2 (QUEUE 0n, item 3): approved=0.7, before=0.8, after=0.7 used to
    be `preserved` (approved alone decided it). A disagreeing before is a
    real bridge rewrite and must block even though after happens to match
    approved again; the approved-vs-before disagreement itself must also be
    surfaced, never just silently resolved by picking approved's side.
    Supersedes the old test_approved_takes_precedence_over_before_on_disagreement,
    which pinned exactly this now-fixed reading."""
    result = compare({"temperature": 0.7}, {"temperature": 0.8}, {"temperature": 0.7})
    check(
        CID,
        "before (0.8) disagreeing with after (0.7) blocks, even though approved (0.7) matches after",
        result.verdict == "blocked" and result.changed_keys == ("temperature",),
    )
    check(
        CID,
        "the approved-vs-before disagreement is surfaced on the result",
        result.approved_before_disagreement_keys == ("temperature",),
    )
    check(
        CID,
        "the changed reason code is present",
        REASON_GENERATION_CONFIG_CHANGED in result.reason_codes,
    )

    agreeing = compare({"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.7})
    check(
        CID,
        "no disagreement is surfaced when approved and before already agree",
        agreeing.approved_before_disagreement_keys == () and agreeing.verdict == "preserved",
    )

    only_before = compare({}, {"temperature": 0.7}, {"temperature": 0.7})
    check(
        CID,
        "a key named only by before (not approved) has nothing to disagree with -- preserved",
        only_before.approved_before_disagreement_keys == () and only_before.verdict == "preserved",
    )


# --------------------------------------------------------------------------
# declared_change
# --------------------------------------------------------------------------


def test_declared_change_with_matching_after_value_passes() -> None:
    approved = {"temperature": 0.7}
    declared = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"temperature": 0.9}
    )
    result = compare(approved, dict(approved), {"temperature": 0.9}, declared)
    check(
        CID,
        "a declared change whose after value matches exactly is declared_change, not blocked",
        result.verdict == "declared_change"
        and result.declared_change_keys == ("temperature",)
        and result.blocking_keys == (),
    )


def test_declared_change_value_mismatch_still_blocks() -> None:
    """Fault FD: accept a declared key regardless of its actual after value
    -> this must go RED."""
    approved = {"temperature": 0.7}
    declared = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"temperature": 0.9}
    )
    result = compare(approved, dict(approved), {"temperature": 0.95}, declared)
    check(
        CID,
        "a declared change whose after value differs from the declared one still blocks",
        result.verdict == "blocked" and result.changed_keys == ("temperature",),
    )
    check(CID, "...not excused into declared_change_keys", result.declared_change_keys == ())


def test_declared_change_covers_only_its_own_key() -> None:
    approved = {"temperature": 0.7, "top_p": 0.9}
    declared = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"temperature": 0.9}
    )
    result = compare(approved, dict(approved), {"temperature": 0.9, "top_p": 1.0}, declared)
    check(
        CID,
        "a declared change excuses only its own key; an undeclared sibling difference still blocks",
        result.verdict == "blocked"
        and result.changed_keys == ("top_p",)
        and result.declared_change_keys == ("temperature",),
    )


def test_declared_change_can_cover_an_inherited_key() -> None:
    approved: dict[str, object] = {}
    declared = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"top_p": 1.0}
    )
    result = compare(approved, dict(approved), {"top_p": 1.0}, declared)
    check(
        CID,
        "a declared change can cover a key that only appears after, not only a changed one",
        result.verdict == "declared_change" and result.declared_change_keys == ("top_p",),
    )


def test_declared_change_without_a_valid_digest_is_refused() -> None:
    """Fault FE: skip the hex64 validation in __post_init__ -> this must go RED."""
    for bad_digest in ("", "not-hex", "a" * 63, "A" * 64, None, 5):
        check(
            CID,
            f"compatibility_tuple_sha256={bad_digest!r} is refused",
            raises(
                lambda d=bad_digest: DeclaredGenerationChange(
                    compatibility_tuple_sha256=d, changed_values={"temperature": 0.9}
                )
            ),
        )
    check(
        CID,
        "a valid hex64 digest is accepted",
        DeclaredGenerationChange(
            compatibility_tuple_sha256=VALID_DIGEST, changed_values={"temperature": 0.9}
        ).compatibility_tuple_sha256
        == VALID_DIGEST,
    )
    check(
        CID,
        "an empty changed_values mapping is refused (nothing declared)",
        raises(
            lambda: DeclaredGenerationChange(
                compatibility_tuple_sha256=VALID_DIGEST, changed_values={}
            )
        ),
    )
    check(
        CID,
        "a non-mapping changed_values is refused",
        raises(
            lambda: DeclaredGenerationChange(
                compatibility_tuple_sha256=VALID_DIGEST, changed_values=["temperature"]
            )
        ),
    )
    check(
        CID,
        "declared_change of the wrong type is refused by compare_generation_parity",
        raises(
            lambda: compare(
                {"temperature": 0.7},
                {"temperature": 0.7},
                {"temperature": 0.7},
                {"temperature": 0.9},
            )
        ),
    )


# --------------------------------------------------------------------------
# insufficient_evidence
# --------------------------------------------------------------------------


def test_missing_observation_gives_insufficient_evidence_never_preserved() -> None:
    """Fault FF: treat a missing observation as an empty dict instead of
    insufficient_evidence -> this must go RED."""
    approved = {"temperature": 0.7}
    for before, after in ((None, {"temperature": 0.7}), ({"temperature": 0.7}, None), (None, None)):
        result = compare(approved, before, after)
        check(
            CID,
            f"before={before!r} after={after!r} gives insufficient_evidence, never preserved",
            result.verdict == "insufficient_evidence",
        )
        check(
            CID,
            "...with nothing blocking or declared (no finding was actually made)",
            result.blocking_keys == () and result.declared_change_keys == (),
        )


def test_approved_is_required_not_optional() -> None:
    check(
        CID,
        "a None approved is refused (only before/after may be missing)",
        raises(lambda: compare(None, {}, {})),
    )


# --------------------------------------------------------------------------
# content-free result / no prompt leakage
# --------------------------------------------------------------------------


def test_canary_prompt_string_never_appears_in_capture_or_result() -> None:
    """Fault FG: remove 'messages' from CONTENT_KEYS (or drop the exclusion
    check entirely) -> this must go RED."""
    canary = "CANARY-7f3c1e9a-do-not-leak"
    body_before = {
        "temperature": 0.7,
        "messages": [{"role": "user", "content": canary}],
        "tools": [{"type": "function", "name": canary}],
    }
    body_after = {
        "temperature": 0.7,
        "messages": [{"role": "user", "content": canary}, {"role": "assistant", "content": canary}],
    }
    before_settings = capture_effective_generation_settings(body_before)
    after_settings = capture_effective_generation_settings(body_after)
    check(
        CID,
        "the canary never survives capture on either side of the bridge",
        canary not in repr(before_settings) and canary not in repr(after_settings),
    )
    result = compare({"temperature": 0.7}, before_settings, after_settings)
    check(
        CID,
        "the comparator result never holds the canary (it holds key names only)",
        canary not in repr(result),
    )
    check(
        CID,
        "...and the comparison is otherwise unaffected (still preserved)",
        result.verdict == "preserved",
    )


def test_result_holds_only_key_names_never_values() -> None:
    approved = {
        "temperature": 0.7,
        "response_format": {"type": "json_object", "detail": "secret-ish value"},
    }
    result = compare(approved, dict(approved), {"temperature": 0.9})
    check(
        CID,
        "a dict-valued setting's content never appears in the result, only its key name",
        "secret-ish value" not in repr(result),
    )
    check(
        CID,
        "response_format (dropped, by name only) and temperature (changed) are both reported by key name",
        result.dropped_keys == ("response_format",) and result.changed_keys == ("temperature",),
    )


# --------------------------------------------------------------------------
# exact equality
# --------------------------------------------------------------------------


def test_values_compared_exactly_not_by_python_equality() -> None:
    check(
        CID,
        "0.7 (float) vs '0.7' (str) is a change, not preserved",
        compare({"temperature": 0.7}, {"temperature": 0.7}, {"temperature": "0.7"}).verdict
        == "blocked",
    )
    check(
        CID,
        "True (bool) vs 1 (int) is a change, not preserved (Python's own == would say True == 1)",
        compare(
            {"parallel_tool_calls": True}, {"parallel_tool_calls": True}, {"parallel_tool_calls": 1}
        ).verdict
        == "blocked",
    )
    check(
        CID,
        "a changed nested response_format shape is caught, not waved through by dict ==",
        compare(
            {"response_format": {"type": "json_object"}},
            {"response_format": {"type": "json_object"}},
            {"response_format": {"type": "text"}},
        ).verdict
        == "blocked",
    )
    check(
        CID,
        "an identical nested stop list is preserved",
        compare(
            {"stop": ["\n\n", "END"]}, {"stop": ["\n\n", "END"]}, {"stop": ["\n\n", "END"]}
        ).verdict
        == "preserved",
    )
    check(
        CID,
        "a reordered stop list is a change (list equality is positional)",
        compare({"stop": ["a", "b"]}, {"stop": ["a", "b"]}, {"stop": ["b", "a"]}).verdict
        == "blocked",
    )


# --------------------------------------------------------------------------
# malformed input / fail-closed
# --------------------------------------------------------------------------


def test_malformed_input_raises_never_type_error_never_allow() -> None:
    for bad in (None, "x", 5, [("temperature", 0.7)], b"x"):
        check(CID, f"approved={bad!r} is refused", raises(lambda b=bad: compare(b, {}, {})))
    # None is a legitimate "missing observation" for before/after (-> insufficient_evidence,
    # tested separately), so only the genuinely malformed non-None/non-mapping values belong here.
    for bad in ("x", 5, [("temperature", 0.7)], b"x"):
        check(CID, f"observed_before={bad!r} is refused", raises(lambda b=bad: compare({}, b, {})))
        check(CID, f"observed_after={bad!r} is refused", raises(lambda b=bad: compare({}, {}, b)))
    check(CID, "a non-string key in approved is refused", raises(lambda: compare({5: 0.7}, {}, {})))
    check(
        CID,
        "a non-string key in observed_before is refused",
        raises(lambda: compare({}, {5: 0.7}, {})),
    )
    check(
        CID,
        "a non-string key in observed_after is refused",
        raises(lambda: compare({}, {}, {5: 0.7})),
    )
    check(
        CID,
        "an unhashable setting value (a list) never crashes the comparator -- it is compared, not hashed",
        not raises(lambda: compare({"stop": []}, {"stop": []}, {"stop": []})),
    )
    check(
        CID,
        "an unhashable setting value (a dict) never crashes the comparator",
        not raises(
            lambda: compare(
                {"response_format": {}}, {"response_format": {}}, {"response_format": {}}
            )
        ),
    )


def test_empty_mappings_are_preserved() -> None:
    check(
        CID,
        "approved/before/after all empty is preserved (nothing to compare)",
        compare({}, {}, {}).verdict == "preserved",
    )


def test_determinism_and_key_ordering() -> None:
    approved = {"b_key": 1, "a_key": 2, "c_key": 3}
    result = compare(approved, {}, {})
    check(
        CID,
        "dropped_keys is sorted, independent of input ordering",
        result.dropped_keys == ("a_key", "b_key", "c_key"),
    )
    again = compare(approved, {}, {})
    check(CID, "the result is deterministic", again == result)


# --------------------------------------------------------------------------
# mutation-gate-driven: case/whitespace exactness and nested-type exactness
# --------------------------------------------------------------------------


def test_whitespace_only_key_is_legal_and_not_merged_with_a_stripped_key() -> None:
    """GRADER CHECKLIST (same-class sweep): keys are never normalized before
    a membership/equality check. A whitespace-only key is a legal, distinct
    key, same precedent as BackendEffect's own whitespace-only id/action."""
    check(
        CID,
        "a whitespace-only key is accepted, not treated as an empty key",
        capture_effective_generation_settings({" ": 1}) == {" ": 1},
    )
    check(
        CID,
        "a tab-only key is accepted",
        capture_effective_generation_settings({"\t": 1}) == {"\t": 1},
    )
    result = compare(
        {" temperature": 0.7, "temperature ": 0.8}, {" temperature": 0.7, "temperature ": 0.8}, {}
    )
    check(
        CID,
        "two keys differing only by surrounding whitespace are distinct, both dropped",
        set(result.dropped_keys) == {" temperature", "temperature "},
    )


def test_content_key_exclusion_is_case_and_whitespace_exact() -> None:
    """A key that is not byte-for-byte 'messages'/'tools'/etc. is not the
    real content field -- it is captured as its own (unknown) setting,
    never silently excluded by a case/whitespace-normalized match."""
    settings = capture_effective_generation_settings(
        {
            "Messages": "not-the-real-field",
            " messages": "also-not-the-real-field",
            "messages": "the real content",
        }
    )
    check(
        CID,
        "only the byte-exact 'messages' key is excluded as content",
        settings == {"Messages": "not-the-real-field", " messages": "also-not-the-real-field"}
        and "messages" not in settings,
    )


def test_main_comparison_loop_key_lookups_are_case_and_whitespace_exact() -> None:
    """Same class as above, inside compare_generation_parity's own key
    bucketing (dropped/changed/declared/inherited), not just capture."""
    result = compare({"Temperature": 0.7}, {"Temperature": 0.7}, {"temperature": 0.7})
    check(
        CID,
        "'Temperature' (approved/before) and 'temperature' (after) are distinct keys: "
        "one dropped, the other inherited -- never matched to each other",
        result.dropped_keys == ("Temperature",)
        and result.inherited_default_keys == ("temperature",),
    )
    declared = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"Temperature": 0.9}
    )
    declared_result = compare(
        {"Temperature": 0.7}, {"Temperature": 0.7}, {"temperature": 0.9}, declared
    )
    check(
        CID,
        "a declared change under one exact key spelling does not excuse a differently-spelled key",
        declared_result.verdict == "blocked" and declared_result.declared_change_keys == (),
    )


def test_nested_bool_vs_int_is_a_change_not_equal() -> None:
    """Kills the 'skip the dict/list recursion entirely' mutants: plain `==`
    on a dict/list already treats True == 1, so disabling the type-exact
    recursive branch must still be caught by a nested bool/int difference."""
    check(
        CID,
        "a nested dict value True vs 1 is a change (plain dict == would say equal)",
        compare(
            {"response_format": {"strict": True}},
            {"response_format": {"strict": True}},
            {"response_format": {"strict": 1}},
        ).verdict
        == "blocked",
    )
    check(
        CID,
        "a nested list element True vs 1 is a change (plain list == would say equal)",
        compare({"stop": [True]}, {"stop": [True]}, {"stop": [1]}).verdict == "blocked",
    )
    check(
        CID,
        "an added key inside a nested dict is a change even though the shared key matches",
        compare(
            {"response_format": {"type": "json_object"}},
            {"response_format": {"type": "json_object"}},
            {"response_format": {"type": "json_object", "extra": "x"}},
        ).verdict
        == "blocked",
    )


def test_result_verdict_is_validated_directly() -> None:
    """GenerationParityResult is frozen but still validates verdict on
    construction, independent of compare_generation_parity's own callers."""

    def build(verdict: str) -> GenerationParityResult:
        return GenerationParityResult(
            verdict=verdict,
            dropped_keys=(),
            changed_keys=(),
            inherited_default_keys=(),
            declared_change_keys=(),
            blocking_keys=(),
        )

    for bad_verdict in ("Preserved", " preserved", "preserved ", "unknown", "", 5):
        check(CID, f"verdict={bad_verdict!r} is refused", raises(lambda v=bad_verdict: build(v)))
    for good_verdict in _verdicts_for_test():
        check(
            CID,
            f"verdict={good_verdict!r} is accepted",
            build(good_verdict).verdict == good_verdict,
        )


def _verdicts_for_test() -> tuple[str, ...]:
    return ("preserved", "declared_change", "blocked", "insufficient_evidence")


def test_expected_value_lookup_key_is_not_normalized_across_branches() -> None:
    """Kills the 'key -> key.strip().lower()' mutant on the
    approved_present/before_present membership checks: cross-contaminating
    case-distinct keys with different values on both the approved-present
    and the before-only branch.

    Round 2 note: the old third check here (`only_mixed_case`) used
    `compare({"Alpha": 1}, {"Alpha": 5}, {"Alpha": 1})` and asserted
    "preserved" -- exactly the now-fixed approved-overrides-before reading
    (see test_before_after_mismatch_blocks_even_when_approved_matches_after,
    which replaces it). Replaced below with sibling-key scenarios that
    isolate the membership-check-folding mutant without depending on that
    now-blocking behaviour."""
    cross = {"Alpha": 1, "alpha": 999}
    true_branch = compare(cross, dict(cross), dict(cross))
    check(
        CID,
        "approved-present branch: 'Alpha' is looked up as 'Alpha', never folded to 'alpha'",
        true_branch.verdict == "preserved",
    )
    false_branch = compare({}, dict(cross), dict(cross))
    check(
        CID,
        "before-only branch: 'Alpha' is looked up as 'Alpha', never folded to 'alpha'",
        false_branch.verdict == "preserved",
    )
    # 'alpha' is before-only (not in approved); a folded membership check
    # would wrongly treat 'Alpha' (approved) as covering it too, pulling in
    # approved's mismatching value (1 != 999) and wrongly blocking.
    before_only_sibling = compare(
        {"Alpha": 1}, {"Alpha": 1, "alpha": 999}, {"Alpha": 1, "alpha": 999}
    )
    check(
        CID,
        "'alpha' (before-only) is never folded into approved's 'Alpha' entry",
        before_only_sibling.verdict == "preserved",
    )
    # Symmetric: 'alpha' is approved-only (not in before); a folded
    # membership check would wrongly treat before's 'Alpha' as covering it,
    # pulling in before's mismatching value (1 != 999) and wrongly blocking.
    approved_only_sibling = compare(
        {"Alpha": 1, "alpha": 999}, {"Alpha": 1}, {"Alpha": 1, "alpha": 999}
    )
    check(
        CID,
        "'alpha' (approved-only) is never folded into before's 'Alpha' entry",
        approved_only_sibling.verdict == "preserved",
    )


def test_declared_key_lookup_is_never_case_or_whitespace_normalized() -> None:
    """Kills the 'key -> key.strip().lower()' mutants on the two
    `key in declared_values` membership checks (changed-loop and
    inherited-loop): a declared key spelled differently in case must never
    be treated as covering the actually-observed key's exact spelling."""
    declared_lower = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"temperature": 0.95}
    )
    changed_loop = compare(
        {"Temperature": 0.7}, {"Temperature": 0.7}, {"Temperature": 0.95}, declared_lower
    )
    check(
        CID,
        "changed-loop: a lowercase declared key never excuses a differently-cased observed key",
        changed_loop.verdict == "blocked" and changed_loop.declared_change_keys == (),
    )
    declared_lower_top_p = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"top_p": 1.0}
    )
    inherited_loop = compare({}, {}, {"Top_P": 1.0}, declared_lower_top_p)
    check(
        CID,
        "inherited-loop: a lowercase declared key never excuses a differently-cased inherited key",
        inherited_loop.verdict == "blocked" and inherited_loop.declared_change_keys == (),
    )


def test_malformed_observation_still_validated_when_the_other_side_is_none() -> None:
    """Fault: drop the _validate_settings_mapping call made on the non-None
    side before returning insufficient_evidence -- malformed input must
    still raise even though the overall verdict would be insufficient_evidence."""
    check(
        CID,
        "a malformed observed_before is refused even though observed_after is None",
        raises(lambda: compare({"temperature": 0.7}, "not-a-mapping", None)),
    )
    check(
        CID,
        "a malformed observed_after is refused even though observed_before is None",
        raises(lambda: compare({"temperature": 0.7}, None, "not-a-mapping")),
    )
    check(
        CID,
        "a non-string key in observed_before is refused even though observed_after is None",
        raises(lambda: compare({"temperature": 0.7}, {5: 0.7}, None)),
    )
    check(
        CID,
        "a non-string key in observed_after is refused even though observed_before is None",
        raises(lambda: compare({"temperature": 0.7}, None, {5: 0.7})),
    )


# --------------------------------------------------------------------------
# Round 2 (L2 return, 2026-10-09T07:48Z, QUEUE 0n)
# --------------------------------------------------------------------------


def test_content_free_capture_covers_every_content_key() -> None:
    """Round 2 item 1: the round-1 CONTENT_KEYS missed real content-bearing
    keys -- Responses `instructions`, `prompt` template variables, chat
    `prediction`, legacy `prompt`, `conversation`, `previous_response_id`
    -- and never scrubbed a nested `extra_body.messages`. A canary test per
    key, including the two faults the round-2 review named directly: F5
    (remove 'input' from CONTENT_KEYS) and F6 (remove 'functions'/
    'function_call') must go RED if CONTENT_KEYS ever shrinks."""
    canary = "CANARY-r004p1-r2-content-key"
    for key, value in (
        ("messages", [{"role": "user", "content": canary}]),
        ("input", [{"type": "message", "content": canary}]),
        ("tools", [{"type": "function", "name": canary}]),
        ("functions", [{"name": canary}]),
        ("function_call", {"name": canary}),
        ("model", canary),
        ("stream", canary),
        ("user", canary),
        ("metadata", {"trace": canary}),
        ("instructions", canary),
        ("prompt", canary),
        ("prediction", {"content": canary}),
        ("conversation", canary),
        ("previous_response_id", canary),
    ):
        body = {"temperature": 0.7, key: value}
        settings = capture_effective_generation_settings(body)
        check(
            CID,
            f"{key!r} is excluded from capture -- its canary value never appears",
            key not in settings and canary not in repr(settings),
        )
    check(
        CID,
        "CONTENT_KEYS names every key this test just exercised",
        CONTENT_KEYS
        >= {
            "messages",
            "input",
            "tools",
            "functions",
            "function_call",
            "model",
            "stream",
            "user",
            "metadata",
            "instructions",
            "prompt",
            "prediction",
            "conversation",
            "previous_response_id",
        },
    )
    # extra_body.messages: a nested passthrough content leak, not itself a
    # top-level CONTENT_KEYS member -- extra_body's OTHER fields must still
    # be captured (digest/scrub, not a blanket exclusion of the whole key).
    nested = capture_effective_generation_settings(
        {
            "temperature": 0.7,
            "extra_body": {"top_k": 5, "messages": [{"role": "user", "content": canary}]},
        }
    )
    check(
        CID,
        "extra_body.messages is scrubbed out but extra_body.top_k survives",
        canary not in repr(nested) and nested["extra_body"] == {"top_k": 5},
    )


def test_near_equal_float_and_declared_bool_int_are_not_equal() -> None:
    """Round 2 item 2 (F8/F10): a near-equal float difference still blocks
    (no implicit tolerance), and a declared value is compared by exact
    type -- never plain `==` -- so a declared bool is never excused by an
    observed int."""
    near_equal = compare({"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.700001})
    check(
        CID,
        "a near-equal (but not exactly equal) float still blocks -- no implicit tolerance",
        near_equal.verdict == "blocked" and near_equal.changed_keys == ("temperature",),
    )

    declared_bool = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"parallel_tool_calls": True}
    )
    result = compare(
        {"parallel_tool_calls": False},
        {"parallel_tool_calls": False},
        {"parallel_tool_calls": 1},
        declared_bool,
    )
    check(
        CID,
        "a declared bool (True) does not excuse an observed int (1) -- exact type, not plain ==",
        result.verdict == "blocked"
        and result.changed_keys == ("parallel_tool_calls",)
        and result.declared_change_keys == (),
    )


def test_stale_declared_change_blocks() -> None:
    """Round 2 item 4: a declared change that does not match reality must
    block, even when nothing else in the comparison touched that key."""
    declared_wrong_value = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"temperature": 0.5}
    )
    result = compare(
        {"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.7}, declared_wrong_value
    )
    check(
        CID,
        "a declared 0.5 with 0.7 actually observed (unchanged) blocks, not preserved",
        result.verdict == "blocked" and result.stale_declared_keys == ("temperature",),
    )
    check(CID, "...and it is in blocking_keys", "temperature" in result.blocking_keys)

    declared_never_observed = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"reasoning_effort": "high"}
    )
    result2 = compare(
        {"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.7}, declared_never_observed
    )
    check(
        CID,
        "a declared key that never appears anywhere in after blocks, not silently ignored",
        result2.verdict == "blocked" and result2.stale_declared_keys == ("reasoning_effort",),
    )

    declared_applied = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"temperature": 0.9}
    )
    result3 = compare(
        {"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.9}, declared_applied
    )
    check(
        CID,
        "a declared change that is actually applied is never marked stale",
        result3.stale_declared_keys == () and result3.verdict == "declared_change",
    )


def test_reason_codes_and_compatibility_tuple_are_stable() -> None:
    """Round 2 item 5: stable reason codes describing which blocking
    categories are present, and a declared_change verdict carries the
    excusing compatibility_tuple_sha256."""
    dropped_result = compare({"temperature": 0.7}, {"temperature": 0.7}, {})
    check(
        CID,
        "a dropped key gives exactly the dropped reason code",
        dropped_result.reason_codes == (REASON_GENERATION_CONFIG_DROPPED,),
    )
    changed_result = compare({"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.8})
    check(
        CID,
        "a changed key gives exactly the changed reason code",
        changed_result.reason_codes == (REASON_GENERATION_CONFIG_CHANGED,),
    )
    inherited_result = compare({}, {}, {"top_p": 1.0})
    check(
        CID,
        "an inherited-default key gives exactly the inherited reason code",
        inherited_result.reason_codes == (REASON_BRIDGE_DEFAULT_INHERITED,),
    )
    mixed_result = compare(
        {"temperature": 0.7, "seed": 1}, {"temperature": 0.7, "seed": 1}, {"top_p": 1.0}
    )
    check(
        CID,
        "a dropped-and-inherited mix gives both codes, in stable order",
        mixed_result.reason_codes
        == (REASON_GENERATION_CONFIG_DROPPED, REASON_BRIDGE_DEFAULT_INHERITED),
    )
    preserved_result = compare({"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.7})
    check(
        CID,
        "preserved carries no reason codes and no compatibility tuple",
        preserved_result.reason_codes == () and preserved_result.compatibility_tuple_sha256 is None,
    )

    declared = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"temperature": 0.9}
    )
    declared_result = compare(
        {"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.9}, declared
    )
    check(
        CID,
        "a declared_change verdict carries the excusing compatibility_tuple_sha256",
        declared_result.verdict == "declared_change"
        and declared_result.compatibility_tuple_sha256 == VALID_DIGEST,
    )
    check(
        CID,
        "...and no blocking reason code, since nothing is actually blocking",
        declared_result.reason_codes == (),
    )


def test_cyclic_or_over_deep_values_raise_typed_error() -> None:
    """Round 2 item 6: a cyclic or over-deep value must raise this
    module's own typed error, never a bare RecursionError."""
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    check(
        CID,
        "a cyclic value in capture raises GenerationConfigParityError, never RecursionError",
        raises(lambda: capture_effective_generation_settings({"response_format": cyclic})),
    )
    check(
        CID,
        "a cyclic value in compare raises GenerationConfigParityError, never RecursionError",
        raises(
            lambda: compare(
                {"response_format": cyclic}, {"response_format": cyclic}, {"response_format": {}}
            )
        ),
    )

    over_deep: dict[str, object] = {}
    node = over_deep
    for _ in range(200):
        node["next"] = {}
        node = node["next"]  # type: ignore[assignment]
    check(
        CID,
        "an over-deep (but non-cyclic) value also raises the typed error, not RecursionError",
        raises(lambda: capture_effective_generation_settings({"response_format": over_deep})),
    )

    reasonable = {"a": {"b": {"c": 1}}}
    check(
        CID,
        "a reasonably-nested value is unaffected by the depth guard",
        capture_effective_generation_settings({"response_format": reasonable})
        == {"response_format": reasonable},
    )

    # Exact boundary (kills the '_depth > MAX -> _depth >= MAX' off-by-one
    # mutants in both _scrub_content and _exact_equal): depth 64 must never
    # raise, depth 65 always must.
    boundary_ok = _nested(64)
    check(
        CID,
        "_scrub_content: exactly the maximum allowed nesting depth does not raise",
        capture_effective_generation_settings({"response_format": boundary_ok})
        == {"response_format": boundary_ok},
    )
    boundary_too_deep = _nested(65)
    check(
        CID,
        "_scrub_content: one level past the maximum allowed depth raises the typed error",
        raises(
            lambda: capture_effective_generation_settings({"response_format": boundary_too_deep})
        ),
    )
    exact_equal_boundary_ok = _nested(64)
    boundary_compare = compare(
        {"response_format": exact_equal_boundary_ok},
        {"response_format": exact_equal_boundary_ok},
        {"response_format": exact_equal_boundary_ok},
    )
    check(
        CID,
        "_exact_equal: exactly the maximum allowed nesting depth does not raise",
        boundary_compare.verdict == "preserved",
    )
    exact_equal_too_deep = _nested(65)
    check(
        CID,
        "_exact_equal: one level past the maximum allowed depth raises the typed error",
        raises(
            lambda: compare(
                {"response_format": exact_equal_too_deep},
                {"response_format": exact_equal_too_deep},
                {"response_format": exact_equal_too_deep},
            )
        ),
    )


def test_scrub_content_recurses_into_lists_tuples_and_preserves_nested_keys() -> None:
    """Kills the 'disable the list/tuple recursion branch' and 'fold a
    nested dict key' mutants in _scrub_content: a CONTENT_KEYS leak nested
    inside a list or tuple element must still be scrubbed, and a nested
    dict's own key must survive byte-exact, never stripped or lowercased."""
    canary = "CANARY-r004p1-r2-nested-container"
    list_settings = capture_effective_generation_settings(
        {"logit_bias": [{"messages": canary}, {"ok": 1}]}
    )
    check(
        CID,
        "a CONTENT_KEYS leak nested inside a list element is scrubbed",
        list_settings == {"logit_bias": [{}, {"ok": 1}]},
    )
    tuple_settings = capture_effective_generation_settings(
        {"logit_bias": ("a", {"messages": canary})}
    )
    check(
        CID,
        "a CONTENT_KEYS leak nested inside a tuple element is scrubbed",
        tuple_settings == {"logit_bias": ("a", {})},
    )
    key_settings = capture_effective_generation_settings({"response_format": {" Type ": "x"}})
    check(
        CID,
        "a nested dict's own key survives byte-exact, never stripped or lowercased",
        key_settings == {"response_format": {" Type ": "x"}},
    )
    # The nested exclusion check itself (not just the output key) must be
    # byte-exact: a key that only matches a CONTENT_KEYS member after
    # folding (case/whitespace) is a different, legitimate key and must
    # survive, not be wrongly dropped.
    nested_fold_settings = capture_effective_generation_settings(
        {"extra_body": {" Messages ": "keep", "messages": "drop"}}
    )
    check(
        CID,
        "a nested key that only folds to a content key (not an exact match) is kept, "
        "while the exact 'messages' is scrubbed",
        nested_fold_settings == {"extra_body": {" Messages ": "keep"}},
    )


def test_approved_and_before_presence_checks_cannot_be_folded_to_mask_a_real_mismatch() -> None:
    """Kills the 'key -> key.strip().lower()' mutants on `approved_present`
    and `before_present` in the main comparison loop. Both checks feed a
    vacuous-true OR (`not approved_present or ...`), so a naive same-case
    test can't distinguish a folded membership check from a correct one --
    this needs a real mismatch that a wrongly-vacuous branch would hide."""
    approved_folded_hides_mismatch = compare({"Alpha": 1}, {}, {"Alpha": 999})
    check(
        CID,
        "a folded approved-presence check would wrongly treat 'Alpha' as absent, hiding "
        "the real 1-vs-999 mismatch behind a vacuous match",
        approved_folded_hides_mismatch.verdict == "blocked"
        and approved_folded_hides_mismatch.changed_keys == ("Alpha",),
    )
    before_folded_hides_mismatch = compare({}, {"Alpha": 1}, {"Alpha": 999})
    check(
        CID,
        "a folded before-presence check would wrongly treat 'Alpha' as absent, hiding "
        "the real 1-vs-999 mismatch behind a vacuous match",
        before_folded_hides_mismatch.verdict == "blocked"
        and before_folded_hides_mismatch.changed_keys == ("Alpha",),
    )


def test_compatibility_tuple_sha256_is_never_leaked_on_a_blocked_verdict() -> None:
    """Kills the 'and -> or' mutant on the compatibility_tuple_sha256
    ternary: a declared_change that is present but does not actually
    excuse anything (verdict stays 'blocked') must never leak its digest."""
    declared = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values={"unrelated_key": "x"}
    )
    result = compare({"temperature": 0.7}, {"temperature": 0.7}, {"temperature": 0.8}, declared)
    check(
        CID,
        "a declared_change that excuses nothing leaves the verdict blocked",
        result.verdict == "blocked",
    )
    check(
        CID,
        "...and never leaks compatibility_tuple_sha256 just because declared_change was passed",
        result.compatibility_tuple_sha256 is None,
    )


def test_declared_change_values_are_frozen_and_deep_copied() -> None:
    """Round 2 item 6: DeclaredGenerationChange.changed_values is frozen
    (MappingProxyType) and deep-copied at construction -- mutating the
    caller's own dict, or writing directly through the attribute, must
    never change what was already locked in."""
    source: dict[str, Any] = {"temperature": 0.9, "response_format": {"type": "json"}}
    declared = DeclaredGenerationChange(
        compatibility_tuple_sha256=VALID_DIGEST, changed_values=source
    )
    source["temperature"] = 0.1
    source["response_format"]["type"] = "tampered"
    source["new_key"] = "injected"
    check(
        CID,
        "mutating the caller's own dict after construction never changes changed_values",
        declared.changed_values["temperature"] == 0.9
        and declared.changed_values["response_format"] == {"type": "json"}
        and "new_key" not in declared.changed_values,
    )
    check(
        CID,
        "changed_values itself refuses a direct write (frozen, MappingProxyType)",
        raises_any(lambda: declared.changed_values.__setitem__("temperature", 0.2)),  # type: ignore[index]
    )


def test_captured_settings_are_deep_copied() -> None:
    """Round 2 item 6: capture_effective_generation_settings returns an
    independent snapshot -- mutating the caller's own request_body after
    capture must never change the already-captured settings."""
    body: dict[str, Any] = {
        "temperature": 0.7,
        "stop": ["a", "b"],
        "response_format": {"type": "json"},
    }
    settings = capture_effective_generation_settings(body)
    body["temperature"] = 0.1
    body["stop"].append("c")
    body["response_format"]["type"] = "tampered"
    check(
        CID,
        "mutating the caller's request_body after capture never changes the captured settings",
        settings["temperature"] == 0.7
        and settings["stop"] == ["a", "b"]
        and settings["response_format"] == {"type": "json"},
    )
    # dict/list are already rebuilt fresh by the recursive scrub, so they
    # cannot expose a missing leaf-level deepcopy. A genuinely mutable
    # *leaf* (not a dict/list/tuple) is the only thing that can: a
    # bytearray is returned as-is without copy.deepcopy, so mutating the
    # caller's original object would otherwise mutate the captured one too.
    mutable_leaf_body: dict[str, Any] = {"logit_bias": bytearray(b"abc")}
    leaf_settings = capture_effective_generation_settings(mutable_leaf_body)
    mutable_leaf_body["logit_bias"].extend(b"xyz")
    check(
        CID,
        "a mutable non-container leaf (bytearray) is deep-copied, not returned by reference",
        leaf_settings["logit_bias"] == bytearray(b"abc"),
    )


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crash is a failed test, reported by name
            FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
