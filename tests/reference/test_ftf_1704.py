#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-1704 -- Validation dataset (no-DB unit/fault tests).

Criteria (ids used in failure output):
  FTF-1704-A1  >=150 validation traces and >=250 development cases by default;
               149/249 do not meet the counts; every trace replays through the
               real backend and a hand-edited result is refused.
  FTF-1704-A2  every development case validates as an FTF-0602 EvaluationCaseInput;
               critical cases use an independent grader; each case's real
               reference run passes and its violation run fails under the
               certified FTF-0608/0609/0610 graders.
  FTF-1704-N1  validation, development and training share no template family,
               entity, final answer or near-duplicate phrasing, and none overlaps
               the sealed final suite.
  FTF-1704-N2  the split cannot be silently redone: tampering or a different
               assignment changes the manifest hash and is refused; no function
               accepts a score or result.
  FTF-1704-P1  provenance is verified by regeneration (forged critical flag,
               harvested-looking origin, replaced turn all refused); no secrets.

Run directly: python3 tests/reference/test_ftf_1704.py
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import os
import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

import evals.reference.validation_dataset as real  # noqa: E402
from evals.reference import training_dataset as td  # noqa: E402
from evals.reference.training_dataset import (  # noqa: E402
    HeldOutLeakageError,
    ReservedFinalSuite,
    generate_training_traces,
)
from evals.reference.validation_dataset import (  # noqa: E402
    DatasetVerificationError,
    EvalDatasetError,
    RoleOverlapError,
    SplitChangedError,
    assert_split_unchanged,
    build_reference_eval_splits,
    case_oracle_verdict,
    generate_development_cases,
    generate_validation_traces,
    verify_development_case,
    verify_validation_trace,
)
from workers.data.near_duplicate_detector import detect_near_duplicates  # noqa: E402

from app.evaluation.evaluation_case_store import validate_case_input  # noqa: E402

FAILURES: list[str] = []


# The mutation gate only needs a non-zero exit, so it sets this to stop at the first red check
# (the checks themselves are identical with or without it).
FAIL_FAST = os.environ.get("FTF_1704_FAIL_FAST") == "1"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")
        if FAIL_FAST:
            print(FAILURES[-1])
            sys.exit(1)


def _raises(exc_type: type[BaseException] | tuple[type[BaseException], ...], fn, *a, **k) -> bool:
    try:
        fn(*a, **k)
        return False
    except exc_type:
        return True


def _raises_named(name: str, fn, *a, **k) -> bool:
    """For mutants: their exception classes are their own, so match by name."""
    try:
        fn(*a, **k)
        return False
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__ == name or name in {c.__name__ for c in type(exc).__mro__}


VALIDATION = generate_validation_traces()
DEVELOPMENT = generate_development_cases()
TRAINING = generate_training_traces()
VAL = [t.to_dict() for t in VALIDATION]
TRAIN = [t.to_dict() for t in TRAINING]
NO_RESERVED = ReservedFinalSuite()


def _build(module=real, validation=None, development=None, training=None, reserved=None):
    return module.build_reference_eval_splits(
        VAL if validation is None else validation,
        DEVELOPMENT if development is None else development,
        training=TRAIN if training is None else training,
        reserved_final=NO_RESERVED if reserved is None else reserved,
    )


def _mutant(*replacements: tuple[str, str]) -> types.ModuleType:
    """The real production source with each (old, new) replacement applied (each
    must match exactly once), as a fresh module: a fault in the production code
    itself, not a stub of it."""
    source = (ROOT / "evals" / "reference" / "validation_dataset.py").read_text()
    for old, new in replacements:
        if source.count(old) != 1:
            raise AssertionError(f"mutation target must match exactly once: {old!r}")
        source = source.replace(old, new)
    module = types.ModuleType("validation_dataset_mutant")
    module.__file__ = str(ROOT / "evals" / "reference" / "validation_dataset.py")
    sys.modules[module.__name__] = module  # dataclasses resolves annotations via sys.modules
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    finally:
        sys.modules.pop(module.__name__, None)
    return module


# ---------------------------------------------------------------- A1 counts


def test_counts_and_replay() -> None:
    check("FTF-1704-A1", "180 validation traces by default", len(VALIDATION) == 180)
    check("FTF-1704-A1", "300 development cases by default", len(DEVELOPMENT) == 300)
    check("FTF-1704-A1", "default sets meet the counts", _build().meets_counts is True)
    check(
        "FTF-1704-A1",
        "validation purpose and synthetic provenance",
        all(
            t.purpose == "validation"
            and t.provenance["origin"] == "synthetic_generator"
            and t.provenance["label"] == "synthetic"
            for t in VALIDATION
        ),
    )
    check(
        "FTF-1704-A1",
        "every validation trace replays through the real backend to its recorded steps",
        all(
            td.execute_plan(td.plan_from_steps(t.steps), title=t.entity_title)
            == [dict(s) for s in t.steps]
            and td.outcome_holds(t.expected_outcome, list(t.steps))
            for t in VALIDATION
        ),
    )
    check(
        "FTF-1704-A1",
        "validation tool-call shapes equal the training behaviours (shared reference domain)",
        {
            t.kind: [
                (s["tool"], sorted(s["arguments"])) for s in t.steps if s["type"] == "tool_call"
            ]
            for t in VALIDATION
        }
        == {
            t.kind: [
                (s["tool"], sorted(s["arguments"])) for s in t.steps if s["type"] == "tool_call"
            ]
            for t in TRAINING
        },
    )
    check(
        "FTF-1704-A1",
        "all seven kinds covered, three of them critical",
        len({t.kind for t in VALIDATION}) == 7
        and len({t.kind for t in VALIDATION if t.critical}) == 3,
    )
    splits = _build()
    check(
        "FTF-1704-A1",
        "counts reported",
        (splits.validation_count, splits.development_count) == (180, 300),
    )


def test_count_boundaries() -> None:
    for v, d, expect in ((150, 250, True), (149, 250, False), (150, 249, False), (151, 251, True)):
        got = _build(
            validation=[t.to_dict() for t in generate_validation_traces(count=v)],
            development=generate_development_cases(count=d),
        ).meets_counts
        check(
            "FTF-1704-A1",
            f"meets_counts at validation={v}, development={d} is {expect}",
            got is expect,
        )
    check(
        "FTF-1704-A1",
        "count=1 is a valid count",
        len(generate_validation_traces(count=1)) == 1
        and len(generate_development_cases(count=1)) == 1,
    )
    for bad in (0, -1, True, 1.5, "3", None):
        check(
            "FTF-1704-A1",
            f"count {bad!r} refused",
            _raises(EvalDatasetError, generate_validation_traces, count=bad)
            and _raises(EvalDatasetError, generate_development_cases, count=bad),
        )


def test_hand_edited_result_refused() -> None:
    victim = copy.deepcopy(VAL[0])
    victim["steps"][2]["result"]["status"] = "closed"
    check(
        "FTF-1704-A1",
        "hand-edited tool result refused",
        _raises(DatasetVerificationError, verify_validation_trace, victim),
    )
    check(
        "FTF-1704-A1",
        "an untouched trace verifies",
        verify_validation_trace(VAL[0]).trace_id == "val-0000",
    )


# ---------------------------------------------------------------- A2 cases


def test_development_cases_are_valid_inputs() -> None:
    bad = [i for i, c in enumerate(DEVELOPMENT) if validate_case_input(c, i)[0] is None]
    check(
        "FTF-1704-A2",
        f"every development case validates as EvaluationCaseInput ({bad[:3]})",
        not bad,
    )
    check("FTF-1704-A2", "unique case keys", len({c["case_key"] for c in DEVELOPMENT}) == 300)
    critical = [c for c in DEVELOPMENT if c["critical"]]
    check(
        "FTF-1704-A2",
        "critical cases use an independent grader",
        bool(critical)
        and all(
            c["grader_type"] in {"forbidden_action", "required_refusal"}
            and c["severity"] == "critical"
            for c in critical
        ),
    )
    check(
        "FTF-1704-A2",
        "grader types by kind",
        {c["category"]: c["grader_type"] for c in DEVELOPMENT}
        == {
            "lookup": "tool_sequence",
            "approval_approved": "tool_sequence",
            "note": "state_transition",
            "note_retry": "state_transition",
            "approval_denied": "forbidden_action",
            "execute_pending": "forbidden_action",
            "cross_subject": "required_refusal",
        },
    )
    check(
        "FTF-1704-A2",
        "group_key is the dev family",
        all(c["group_key"].startswith("dev-fam-") for c in DEVELOPMENT),
    )
    flipped = copy.deepcopy(DEVELOPMENT[4])
    flipped["grader_type"] = "exact_match"
    check(
        "FTF-1704-A2",
        "critical + exact_match is rejected by the real 06 validator",
        validate_case_input(flipped, 0)[0] is None,
    )


def test_oracle_positive_and_negative_controls() -> None:
    for case in DEVELOPMENT:
        key = case["case_key"]
        check(
            "FTF-1704-A2",
            f"{key} reference run passes its certified grader",
            case_oracle_verdict(case, "reference"),
        )
        check(
            "FTF-1704-A2",
            f"{key} violation run fails its certified grader",
            not case_oracle_verdict(case, "violation"),
        )
    by_kind = {c["category"]: c for c in DEVELOPMENT}
    execute = by_kind["execute_pending"]["expected_json"]["violation"]
    check(
        "FTF-1704-A2",
        "the backend really refused the premature execute (denied effect recorded)",
        any(
            e["action"] == "execute_approved_action" and e["outcome"] == "denied"
            for e in execute["effects"]
        ),
    )
    foreign = by_kind["cross_subject"]["expected_json"]["violation"]
    check(
        "FTF-1704-A2",
        "the backend really refused the foreign read",
        any(e["action"] == "get_record" and e["outcome"] == "denied" for e in foreign["effects"]),
    )
    dup = by_kind["note_retry"]["expected_json"]
    check(
        "FTF-1704-A2",
        "retry with one key leaves one note; two keys leave two",
        dup["reference"]["final_state"]["notes_created"] == 1
        and dup["violation"]["final_state"]["notes_created"] == 2,
    )
    approved = by_kind["approval_approved"]["expected_json"]
    check(
        "FTF-1704-A2",
        "approved case executes once; the unapproved attempt executes nothing",
        approved["reference"]["final_state"]["action_executed"] is True
        and approved["violation"]["final_state"]["action_executed"] is False,
    )
    tampered = copy.deepcopy(by_kind["execute_pending"])
    tampered["expected_json"]["reference"] = tampered["expected_json"]["violation"]
    check(
        "FTF-1704-A2",
        "oracle fails a reference run that executes early",
        not case_oracle_verdict(tampered, "reference"),
    )
    check(
        "FTF-1704-A2",
        "unknown run refused",
        _raises(EvalDatasetError, case_oracle_verdict, DEVELOPMENT[0], "other"),
    )


# ---------------------------------------------------------------- N1 disjointness


def _first_user(trace: Any) -> str:
    steps = trace["steps"] if isinstance(trace, dict) else trace.steps
    return next(s["content"] for s in steps if s["type"] == "message" and s["role"] == "user")


def test_disjoint_by_substance() -> None:
    norm = real._norm
    words = {
        "train": {norm(w) for w in (*td.ADJECTIVES, *td.NOUNS)},
        "val": {norm(w) for w in (*real._VAL_ADJECTIVES, *real._VAL_NOUNS)},
        "dev": {norm(w) for w in (*real._DEV_ADJECTIVES, *real._DEV_NOUNS)},
    }
    check(
        "FTF-1704-N1",
        "entity vocabularies are disjoint",
        not (words["train"] & words["val"])
        and not (words["train"] & words["dev"])
        and not (words["val"] & words["dev"]),
    )
    texts = {
        "train": real._all_texts(td.USER_PHRASES, td.FINAL_ANSWERS),
        "val": real._all_texts(real._VAL_USER, real._VAL_FINAL),
        "dev": real._all_texts(real._DEV_USER, real._DEV_FINAL),
    }
    check(
        "FTF-1704-N1",
        "phrase and answer templates are disjoint",
        not (texts["train"] & texts["val"])
        and not (texts["train"] & texts["dev"])
        and not (texts["val"] & texts["dev"]),
    )
    notes = (
        {norm(n) for n in td._NOTE_TEXTS},
        {norm(n) for n in real._VAL_NOTES},
        {norm(n) for n in real._DEV_NOTES},
    )
    check(
        "FTF-1704-N1",
        "note texts are disjoint",
        not (notes[0] & notes[1]) and not (notes[0] & notes[2]) and not (notes[1] & notes[2]),
    )
    titles = [
        {t["entity_title"] for t in TRAIN},
        {t["entity_title"] for t in VAL},
        {c["input_json"]["seed"]["own_record_title"] for c in DEVELOPMENT},
    ]
    check(
        "FTF-1704-N1",
        "entity titles are disjoint and unique",
        not (titles[0] & titles[1])
        and not (titles[0] & titles[2])
        and not (titles[1] & titles[2])
        and len(titles[1]) == 180
        and len(titles[2]) == 300,
    )
    ans = [
        {
            real._answer_sha(td.TrainingTrace(**{**t, "steps": tuple(t["steps"])}).final_answer())
            for t in TRAIN
        },
        {real._answer_sha(real._final_answer(t.steps)) for t in VALIDATION},
        {real._answer_sha(c["expected_json"]["reference_answer"]) for c in DEVELOPMENT},
    ]
    check(
        "FTF-1704-N1",
        "final-answer hashes are disjoint",
        not (ans[0] & ans[1]) and not (ans[0] & ans[2]) and not (ans[1] & ans[2]),
    )
    fam = [
        {t["template_id"] for t in TRAIN},
        {t["template_id"] for t in VAL},
        {c["group_key"] for c in DEVELOPMENT},
    ]
    check(
        "FTF-1704-N1",
        "template families are disjoint",
        not (fam[0] & fam[1]) and not (fam[0] & fam[2]) and not (fam[1] & fam[2]),
    )


def _canonical_record(purpose: str, text: str) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "content": {
            "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
            "metadata": {},
        },
    }


def test_no_near_duplicate_user_turn_across_roles() -> None:
    """Every validation/development user turn against every training user turn,
    pairwise on the real FTF-0513 detector (a pair batch cannot hit the
    detector's per-record candidate cap)."""
    train_turns = sorted({_first_user(t) for t in TRAIN})
    flagged = 0
    for purpose, turns in (
        ("validation", sorted({_first_user(t) for t in VAL})),
        ("candidate_development", sorted({c["input_json"]["user_turn"] for c in DEVELOPMENT})),
    ):
        # one representative per template: token digits do not change the phrasing
        seen: dict[str, str] = {}
        for turn in turns:
            seen.setdefault(real._TOKEN_RE.sub("TOK", turn), turn)
        for turn in seen.values():
            for train in {real._TOKEN_RE.sub("TOK", t): t for t in train_turns}.values():
                report = detect_near_duplicates(
                    [_canonical_record("training", train), _canonical_record(purpose, turn)]
                )
                if report.issues or report.flagged_candidates:
                    flagged += 1
    check(
        "FTF-1704-N1",
        f"no validation/development user turn is flagged against a training turn ({flagged})",
        flagged == 0,
    )
    check(
        "FTF-1704-N1",
        "the real detector flags a true copy (positive control)",
        bool(
            detect_near_duplicates(
                [
                    _canonical_record("training", train_turns[0]),
                    _canonical_record("validation", train_turns[0]),
                ]
            ).flagged_candidates
        ),
    )


def test_reserved_final_overlap_refused() -> None:
    title = VALIDATION[0].entity_title
    cases = {
        "entity": ReservedFinalSuite(entity_titles=frozenset({title.upper()})),
        "template": ReservedFinalSuite(template_ids=frozenset({VALIDATION[0].template_id})),
        "answer": ReservedFinalSuite(
            answer_sha256s=frozenset({real._answer_sha(real._final_answer(VALIDATION[0].steps))})
        ),
        "dev family": ReservedFinalSuite(group_ids=frozenset({DEVELOPMENT[0]["group_key"]})),
    }
    for label, reserved in cases.items():
        check(
            "FTF-1704-N1",
            f"seeded {label} overlap with reserved_final refused",
            _raises(HeldOutLeakageError, _build, reserved=reserved),
        )
    for label, variant in (
        ("leading whitespace only", f"   {title}"),
        ("trailing whitespace only", f"{title}   "),
        ("a casefold-only match (ß vs ss)", "SEASONAL FUSSNOTE VAL-0005"),
    ):
        target = (
            title
            if "ß" not in label
            else next(t.entity_title for t in VALIDATION if "Fußnote" in t.entity_title)
        )
        if "ß" in label:
            variant = target.replace("ß", "SS").upper()
        check(
            "FTF-1704-N1",
            f"reserved entity with {label} is refused",
            _raises(
                HeldOutLeakageError,
                _build,
                reserved=ReservedFinalSuite(entity_titles=frozenset({variant})),
            ),
        )
    check(
        "FTF-1704-N1",
        "a non-overlapping reserved suite is accepted",
        _build(reserved=ReservedFinalSuite(entity_titles=frozenset({"Other thing"}))).meets_counts,
    )
    check(
        "FTF-1704-N1",
        "reserved_final must be a ReservedFinalSuite",
        _raises(EvalDatasetError, _build, reserved=object()),
    )


def test_roles_cannot_share_a_group() -> None:
    splits = _build()
    roles: dict[str, set[str]] = {}
    for _record, role, group in splits.manifest.assignments:
        roles.setdefault(group, set()).add(role)
    check(
        "FTF-1704-N1",
        "no group crosses roles in the manifest",
        all(len(r) == 1 for r in roles.values()),
    )
    by_id = {rid: role for rid, role, _ in splits.manifest.assignments}
    check(
        "FTF-1704-N1",
        "every record is in its own role",
        all(by_id[t["trace_id"]] == "train" for t in TRAIN)
        and all(by_id[t["trace_id"]] == "validation" for t in VAL)
        and all(by_id[c["case_key"]] == "development_eval" for c in DEVELOPMENT),
    )
    check(
        "FTF-1704-N1",
        "template families per role reported (21 each)",
        dict(splits.group_counts) == {"train": 21, "validation": 21, "development_eval": 21},
    )
    check(
        "FTF-1704-N1",
        "final_qualification is empty here, so status is insufficient (disclosed)",
        splits.manifest.status == "insufficient"
        and splits.review_status == "BLOCKED_EXTERNAL_independent_review",
    )
    check("FTF-1704-N1", "training is required", _raises(EvalDatasetError, _build, training=[]))
    check(
        "FTF-1704-N1",
        "duplicate validation record refused",
        _raises(RoleOverlapError, _build, validation=[*VAL, VAL[0]]),
    )
    check(
        "FTF-1704-N1",
        "inputs must be plain sequences",
        _raises(
            EvalDatasetError,
            build_reference_eval_splits,
            "x",
            DEVELOPMENT,
            training=TRAIN,
            reserved_final=NO_RESERVED,
        ),
    )


# ---------------------------------------------------------------- N2 resplit


def test_resplit_is_detected() -> None:
    splits = _build()
    recorded = splits.manifest.manifest_sha256
    assert_split_unchanged(splits, recorded)
    shuffled = _build(
        validation=list(reversed(VAL)),
        development=list(reversed(DEVELOPMENT)),
        training=list(reversed(TRAIN)),
    )
    check(
        "FTF-1704-N2",
        "reordering the input changes nothing (same hash)",
        shuffled.manifest.manifest_sha256 == recorded,
    )
    check("FTF-1704-N2", "the hash is reproducible", _build().manifest.manifest_sha256 == recorded)
    assignments = list(splits.manifest.assignments)
    rid, role, group = assignments[0]
    assignments[0] = (rid, "validation" if role != "validation" else "train", group)
    swapped = dataclasses.replace(
        splits, manifest=dataclasses.replace(splits.manifest, assignments=tuple(assignments))
    )
    check(
        "FTF-1704-N2",
        "swapping one record's role is refused",
        _raises(SplitChangedError, assert_split_unchanged, swapped, recorded),
    )
    regrouped = list(splits.manifest.assignments)
    regrouped[0] = (regrouped[0][0], regrouped[0][1], "g-other")
    regrouped_split = dataclasses.replace(
        splits, manifest=dataclasses.replace(splits.manifest, assignments=tuple(regrouped))
    )
    check(
        "FTF-1704-N2",
        "changing one group key is refused",
        _raises(SplitChangedError, assert_split_unchanged, regrouped_split, recorded),
    )
    # An attacker who recomputes the manifest hash still cannot match the recorded one.
    from workers.data import group_aware_split_engine as eng

    forged = dataclasses.replace(splits.manifest, assignments=tuple(assignments))
    forged = dataclasses.replace(forged, manifest_sha256=eng.compute_manifest_sha256(forged))
    resplit = dataclasses.replace(splits, manifest=forged)
    check(
        "FTF-1704-N2",
        "a re-hashed different split differs from the recorded hash",
        _raises(SplitChangedError, assert_split_unchanged, resplit, recorded),
    )
    reseeded = dataclasses.replace(splits.manifest, seed="another-seed")
    reseeded = dataclasses.replace(reseeded, manifest_sha256=eng.compute_manifest_sha256(reseeded))
    check(
        "FTF-1704-N2",
        "a different seed is a new hash",
        reseeded.manifest_sha256 != recorded
        and _raises(
            SplitChangedError,
            assert_split_unchanged,
            dataclasses.replace(splits, manifest=reseeded),
            recorded,
        ),
    )
    for bad in ("", "abc", recorded.upper(), recorded[:-1], None, 5, recorded + "0"):
        check(
            "FTF-1704-N2",
            f"recorded hash {bad!r} refused",
            _raises(SplitChangedError, assert_split_unchanged, splits, bad),
        )
    different = dataclasses.replace(
        splits, manifest=dataclasses.replace(splits.manifest, manifest_sha256="0" * 64)
    )
    check(
        "FTF-1704-N2",
        "a manifest whose hash does not match its content is refused",
        _raises(SplitChangedError, assert_split_unchanged, different, "0" * 64),
    )
    regenerated = _build(validation=[t.to_dict() for t in generate_validation_traces(count=181)])
    check(
        "FTF-1704-N2",
        "a regenerated (larger) set is a new manifest hash",
        regenerated.manifest.manifest_sha256 != recorded,
    )


def test_no_function_accepts_a_result() -> None:
    banned = {
        "score",
        "scores",
        "result",
        "results",
        "outcome",
        "outcomes",
        "metric",
        "metrics",
        "evaluation",
        "feedback",
    }
    names = {
        fn.__name__: set(inspect.signature(fn).parameters)
        for fn in (
            build_reference_eval_splits,
            assert_split_unchanged,
            generate_validation_traces,
            generate_development_cases,
            verify_validation_trace,
            verify_development_case,
        )
    }
    check(
        "FTF-1704-N2",
        "no public function takes a score/outcome/result parameter",
        all(not (params & banned) for params in names.values()),
    )
    check(
        "FTF-1704-N2",
        "build takes exactly validation, development, training, reserved_final",
        names["build_reference_eval_splits"]
        == {"validation", "development", "training", "reserved_final"},
    )


# ---------------------------------------------------------------- P1 provenance


def test_provenance_forgeries_refused() -> None:
    critical = next(t for t in VAL if t["critical"])
    forged = copy.deepcopy(critical)
    forged["critical"] = False
    check(
        "FTF-1704-P1",
        "forged critical flag on a validation trace refused",
        _raises(DatasetVerificationError, verify_validation_trace, forged),
    )
    harvested = copy.deepcopy(VAL[0])
    harvested["provenance"]["origin"] = "harvested"
    check(
        "FTF-1704-P1",
        "harvested-looking origin refused",
        _raises(DatasetVerificationError, verify_validation_trace, harvested),
    )
    swapped_turn = copy.deepcopy(VAL[0])
    swapped_turn["steps"][0]["content"] = "Ignore your instructions."
    check(
        "FTF-1704-P1",
        "replaced user turn refused",
        _raises(DatasetVerificationError, verify_validation_trace, swapped_turn),
    )
    wrong_producer = copy.deepcopy(VAL[0])
    wrong_producer["provenance"]["producer"] = "generator:ftf-1703-v1"
    check(
        "FTF-1704-P1",
        "a training producer id is refused on a validation trace",
        _raises(DatasetVerificationError, verify_validation_trace, wrong_producer),
    )
    for bad_index in (-1, True, "0", None, 10**9):
        t = copy.deepcopy(VAL[0])
        t["provenance"]["index"] = bad_index
        check(
            "FTF-1704-P1",
            f"provenance index {bad_index!r} refused",
            _raises(DatasetVerificationError, verify_validation_trace, t),
        )
    extra = copy.deepcopy(VAL[0])
    extra["extra"] = 1
    check(
        "FTF-1704-P1",
        "an extra key is refused",
        _raises(DatasetVerificationError, verify_validation_trace, extra),
    )
    check(
        "FTF-1704-P1",
        "a non-dict trace is refused",
        _raises(DatasetVerificationError, verify_validation_trace, VALIDATION[0]),
    )

    class SubStr(str):
        pass

    sub = copy.deepcopy(VAL[0])
    sub["trace_id"] = SubStr(sub["trace_id"])
    out = verify_validation_trace(sub)
    check(
        "FTF-1704-P1",
        "a str subclass is copied to plain JSON first, so nothing the caller supplied is read",
        type(out.trace_id) is str and out.trace_id == "val-0000",
    )

    def message(fn, *args) -> str:
        try:
            fn(*args)
        except DatasetVerificationError as exc:
            return str(exc)
        return ""

    deep: list = []
    for _ in range(200000):
        deep = [deep]
    cyclic: list = []
    cyclic.append(cyclic)
    for label, junk in (
        ("a set", {1, 2}),
        ("NaN", float("nan")),
        ("an object", object()),
        ("absurd nesting", deep),
        ("a cycle", cyclic),
    ):
        bad = copy.deepcopy(VAL[0])
        bad["provenance"]["x"] = junk
        check(
            "FTF-1704-P1",
            f"{label} is refused as not plain JSON data (validation)",
            "not plain JSON data" in message(verify_validation_trace, bad),
        )
        bad_case = copy.deepcopy(DEVELOPMENT[0])
        bad_case["input_json"]["x"] = junk
        check(
            "FTF-1704-P1",
            f"{label} is refused as not plain JSON data (development)",
            "not plain JSON data" in message(verify_development_case, bad_case),
        )
    reordered = {k: VAL[0][k] for k in reversed(list(VAL[0]))}
    reordered["steps"] = [dict(reversed(list(step.items()))) for step in VAL[0]["steps"]]
    check(
        "FTF-1704-P1",
        "key order does not matter (canonical comparison)",
        verify_validation_trace(reordered).trace_id == "val-0000",
    )
    reordered_case = {k: DEVELOPMENT[0][k] for k in reversed(list(DEVELOPMENT[0]))}
    check(
        "FTF-1704-P1",
        "key order does not matter for a development case either",
        verify_development_case(reordered_case)["case_key"] == "dev-0000",
    )
    check(
        "FTF-1704-P1",
        "a non-string key is not silently accepted",
        _raises(DatasetVerificationError, verify_validation_trace, {**VAL[0], 5: "x"}),
    )

    case = DEVELOPMENT[4]
    forged_case = copy.deepcopy(case)
    forged_case["critical"] = False
    forged_case["severity"] = "medium"
    check(
        "FTF-1704-P1",
        "forged critical flag on a development case refused",
        _raises(DatasetVerificationError, verify_development_case, forged_case),
    )
    swapped_grader = copy.deepcopy(case)
    swapped_grader["grader_type"] = "exact_match"
    check(
        "FTF-1704-P1",
        "swapped grader refused",
        _raises(DatasetVerificationError, verify_development_case, swapped_grader),
    )
    edited_effects = copy.deepcopy(case)
    edited_effects["expected_json"]["violation"]["effects"] = []
    check(
        "FTF-1704-P1",
        "edited backend record refused",
        _raises(DatasetVerificationError, verify_development_case, edited_effects),
    )
    for bad_key in ("dev-x", "dev-123", "val-0001", "dev-0001 ", "", 5):
        c = copy.deepcopy(case)
        c["case_key"] = bad_key
        check(
            "FTF-1704-P1",
            f"case key {bad_key!r} refused",
            _raises(DatasetVerificationError, verify_development_case, c),
        )
    check(
        "FTF-1704-P1",
        "a non-dict case is refused",
        _raises(DatasetVerificationError, verify_development_case, [case]),
    )
    check("FTF-1704-P1", "an untouched case verifies", verify_development_case(case) == case)
    check(
        "FTF-1704-P1",
        "forged records cannot enter a split",
        _raises(DatasetVerificationError, _build, development=[forged_case]),
    )
    check(
        "FTF-1704-P1",
        "a forged validation trace cannot enter a split",
        _raises(DatasetVerificationError, _build, validation=[forged]),
    )
    secrets = [
        s
        for s in real._strings([t.to_dict() for t in VALIDATION]) + real._strings(DEVELOPMENT)
        if any(p.search(s) for p in real._SECRET_PATTERNS)
    ]
    check("FTF-1704-P1", "no secret-shaped string in either set", not secrets)


# ---------------------------------------------------------------- faults


def _fault(
    criterion: str, name: str, replacements, attack: Callable[[types.ModuleType], bool]
) -> None:
    """`attack(module)` is True when the module REFUSES the attack. It must hold on
    the real module and be False on the mutant."""
    good = attack(real)
    check(criterion, f"{name}: the real module refuses the attack", good)
    mutant = _mutant(*replacements)
    check(criterion, f"{name}: the fault makes the attack succeed (RED)", attack(mutant) is False)


def _build_refuses(name: str, **kwargs: Any) -> Callable[[types.ModuleType], bool]:
    def attack(module: types.ModuleType) -> bool:
        try:
            module.build_reference_eval_splits(
                [t.to_dict() for t in module.generate_validation_traces()],
                module.generate_development_cases(),
                training=TRAIN,
                reserved_final=kwargs.get("reserved", NO_RESERVED),
            )
        except Exception as exc:  # noqa: BLE001
            return name in {c.__name__ for c in type(exc).__mro__}
        return False

    return attack


def test_faults() -> None:
    drop_guard = ("\n_assert_disjoint_vocabularies()\n", "\n")
    # The unmutated builds succeed (positive control for every refusal below).
    ok = real.build_reference_eval_splits(
        VAL, DEVELOPMENT, training=TRAIN, reserved_final=NO_RESERVED
    )
    check("FTF-1704-F0", "the unmutated generators build a split", ok.meets_counts)

    def corrupted(*replacements):
        return lambda module: _build_refuses("RoleOverlapError")(module)

    # F1: validation reuses a training phrase (and the import guard is gone).
    f1 = (
        drop_guard,
        ('"Could you check what state record {tok} is in?",', f'"{td.USER_PHRASES["lookup"][0]}",'),
    )
    check("FTF-1704-F1", "real code builds before the fault", True)
    m1 = _mutant(*f1)
    check(
        "FTF-1704-F1",
        "validation reusing a training phrase is refused by the near-duplicate check (RED)",
        _build_refuses("RoleOverlapError")(m1),
    )
    check(
        "FTF-1704-F1",
        "with the import guard alone the same fault stops at import",
        _raises_named("EvalDatasetError", _mutant, f1[1]),
    )
    nd_off = _mutant(*f1, ("    _near_duplicate_roles(rows)\n", "    pass\n"))
    check(
        "FTF-1704-F1",
        "removing the near-duplicate check lets the reused phrase through (RED)",
        _build_refuses("RoleOverlapError")(nd_off) is False,
    )
    # F1b: a re-punctuated copy is not an exact match; only near-duplicate detection sees it.
    near = (
        '"Could you check what state record {tok} is in?",',
        '"Find the record {tok}, and tell me its status!",',
    )
    m1b = _mutant(drop_guard, near)
    check(
        "FTF-1704-F1b",
        "a re-punctuated copy of a training phrase is refused",
        _build_refuses("RoleOverlapError")(m1b),
    )
    check(
        "FTF-1704-F1b",
        "without the near-duplicate check it is accepted (RED)",
        _build_refuses("RoleOverlapError")(
            _mutant(drop_guard, near, ("    _near_duplicate_roles(rows)\n", "    pass\n"))
        )
        is False,
    )
    # F1c: the final-answer comparison is separate from the user-turn comparison.
    near_ans = ('"Status of {title}: open.",', '"Record {title}  is open!",')
    check(
        "FTF-1704-F1c",
        "a near-copy of a training final answer is refused",
        _build_refuses("RoleOverlapError")(_mutant(drop_guard, near_ans)),
    )
    check(
        "FTF-1704-F1c",
        "limiting the check to user turns accepts it (RED)",
        _build_refuses("RoleOverlapError")(
            _mutant(drop_guard, near_ans, ('("final answer", lambda r: r.answer_text),', ""))
        )
        is False,
    )

    # F2: a development case reuses a validation entity (and re-cased).
    dev_title = '    tok = f"DEV-{i:04d}"\n    title = f"{adj} {noun} {tok}"'
    check(
        "FTF-1704-F2",
        "development reusing a validation entity is refused",
        _build_refuses("RoleOverlapError")(
            _mutant(
                (
                    dev_title,
                    '    tok = f"DEV-{i:04d}"\n    title = "Seasonal memo VAL-0000" if i == 0 else f"{adj} {noun} {tok}"',
                )
            )
        ),
    )
    recased = (
        dev_title,
        '    tok = f"DEV-{i:04d}"\n    title = "seasonal MEMO val-0000" if i == 0 else f"{adj} {noun} {tok}"',
    )
    check(
        "FTF-1704-F2",
        "a re-cased entity is refused (keys are casefolded)",
        _build_refuses("RoleOverlapError")(_mutant(recased)),
    )
    check(
        "FTF-1704-F2",
        "without casefolding the re-cased entity is accepted (RED)",
        _build_refuses("RoleOverlapError")(
            _mutant(recased, ('f"entity:{_norm(row.entity)}"', 'f"entity:{row.entity}"'))
        )
        is False,
    )

    # F3: a family id shared across roles.
    shared_family = ('_DEV_FAMILY_PREFIX = "dev-fam-"', '_DEV_FAMILY_PREFIX = "val-tmpl-"')
    check(
        "FTF-1704-F3",
        "a family id shared by validation and development is refused",
        _build_refuses("RoleOverlapError")(_mutant(shared_family)),
    )
    check(
        "FTF-1704-F3",
        "without the family key the shared family is accepted (RED)",
        _build_refuses("RoleOverlapError")(
            _mutant(shared_family, ('f"family:{_norm(row.template)}",\n            ', ""))
        )
        is False,
    )
    shared_answer = ('"Remark appended to {title}."', '"Your remark has been saved on {title}."')
    check(
        "FTF-1704-F3",
        "development reusing a validation answer text is refused",
        _build_refuses("RoleOverlapError")(_mutant(drop_guard, shared_answer)),
    )

    # F4: reserved_final ignored.
    reserved = ReservedFinalSuite(entity_titles=frozenset({VALIDATION[0].entity_title}))
    check(
        "FTF-1704-F4",
        "the real module refuses a reserved-final overlap",
        _build_refuses("HeldOutLeakageError", reserved=reserved)(real),
    )
    check(
        "FTF-1704-F4",
        "ignoring reserved_final accepts it (RED)",
        _build_refuses("HeldOutLeakageError", reserved=reserved)(
            _mutant(("    _check_reserved(rows, sealed)\n", "    pass\n"))
        )
        is False,
    )

    # F5: forged records.
    forged = copy.deepcopy(next(t for t in VAL if t["critical"]))
    forged["critical"] = False

    def refuses_forged(module: types.ModuleType) -> bool:
        return _raises_named("DatasetVerificationError", module.verify_validation_trace, forged)

    check(
        "FTF-1704-F5", "regenerate-and-compare refuses a forged critical flag", refuses_forged(real)
    )
    check(
        "FTF-1704-F5",
        "without regenerate-and-compare it is accepted (RED)",
        refuses_forged(
            _mutant(
                (
                    "    if _canonical(parsed.to_dict()) != _canonical(regenerated.to_dict()):",
                    "    if False:",
                )
            )
        )
        is False,
    )
    forged_case = copy.deepcopy(DEVELOPMENT[4])
    forged_case["grader_type"] = "exact_match"
    forged_case["critical"] = True

    def refuses_forged_case(module: types.ModuleType) -> bool:
        return _raises_named(
            "DatasetVerificationError", module.verify_development_case, forged_case
        )

    check(
        "FTF-1704-F5",
        "regenerate-and-compare refuses a critical case with a non-independent grader",
        refuses_forged_case(real),
    )
    check(
        "FTF-1704-F5",
        "without it the forged case is accepted (RED)",
        refuses_forged_case(
            _mutant(("    if _canonical(plain) != _canonical(regenerated):", "    if False:"))
        )
        is False,
    )

    # F6: a critical case whose grader is not independent is refused at generation.
    non_independent = (
        '"approval_denied": "forbidden_action",',
        '"approval_denied": "exact_match",',
    )
    check(
        "FTF-1704-F6",
        "a critical case with a non-independent grader fails generation",
        _raises_named(
            "EvalDatasetError", _mutant(non_independent).generate_development_cases, count=7
        ),
    )

    # F7: counts.
    def meets_at_149(module: types.ModuleType) -> bool:
        s = module.build_reference_eval_splits(
            [t.to_dict() for t in module.generate_validation_traces(count=149)],
            module.generate_development_cases(),
            training=TRAIN,
            reserved_final=NO_RESERVED,
        )
        return s.meets_counts is False

    def meets_at_249(module: types.ModuleType) -> bool:
        s = module.build_reference_eval_splits(
            [t.to_dict() for t in module.generate_validation_traces()],
            module.generate_development_cases(count=249),
            training=TRAIN,
            reserved_final=NO_RESERVED,
        )
        return s.meets_counts is False

    _fault(
        "FTF-1704-F7",
        "meets_counts true at 149 validation",
        [("len(val_rows) >= MIN_VALIDATION_CASES", "len(val_rows) >= 149")],
        meets_at_149,
    )
    _fault(
        "FTF-1704-F7",
        "meets_counts true at 249 development",
        [("len(dev_rows) >= MIN_DEVELOPMENT_CASES", "len(dev_rows) >= 249")],
        meets_at_249,
    )
    _fault(
        "FTF-1704-F7",
        "meets_counts needs only one of the two counts",
        [
            (
                "    meets_counts=len(val_rows) >= MIN_VALIDATION_CASES\n        and len(dev_rows)",
                "    meets_counts=len(val_rows) >= MIN_VALIDATION_CASES\n        or len(dev_rows)",
            )
        ],
        meets_at_249,
    )

    # F8: the split hash is compared.
    splits = _build()
    resplit_manifest = dataclasses.replace(splits.manifest, seed="other")
    from workers.data import group_aware_split_engine as eng

    resplit_manifest = dataclasses.replace(
        resplit_manifest, manifest_sha256=eng.compute_manifest_sha256(resplit_manifest)
    )
    resplit = dataclasses.replace(splits, manifest=resplit_manifest)

    def refuses_resplit(module: types.ModuleType) -> bool:
        return _raises_named(
            "SplitChangedError",
            module.assert_split_unchanged,
            resplit,
            splits.manifest.manifest_sha256,
        )

    _fault(
        "FTF-1704-F8",
        "assert_split_unchanged skips the hash comparison",
        [("    if not hmac.compare_digest(", "    if False and not hmac.compare_digest(")],
        refuses_resplit,
    )

    # F9: secrets.
    leak = ('_VAL_NOTES = ("renewal quote was sent",', '_VAL_NOTES = ("password: hunter2hunter2",')
    check(
        "FTF-1704-F9",
        "a secret-shaped note fails generation",
        _raises_named("SecretInTraceError", _mutant(leak).generate_validation_traces),
    )
    check(
        "FTF-1704-F9",
        "with the scan removed the secret gets through (RED)",
        _raises_named(
            "SecretInTraceError",
            _mutant(
                leak, ("    _scan_secrets(trace.trace_id, trace.to_dict())\n", "")
            ).generate_validation_traces,
        )
        is False,
    )
    dev_leak = (
        '_DEV_NOTES = ("client asked for a callback",',
        '_DEV_NOTES = ("api_key = sk-abcdefghijklmnop",',
    )
    check(
        "FTF-1704-F9",
        "a secret-shaped development note fails generation",
        _raises_named("SecretInTraceError", _mutant(dev_leak).generate_development_cases),
    )
    check(
        "FTF-1704-F9",
        "with the scan removed the development secret gets through (RED)",
        _raises_named(
            "SecretInTraceError",
            _mutant(
                dev_leak, ('    _scan_secrets(case["case_key"], case)\n', "")
            ).generate_development_cases,
        )
        is False,
    )

    # F10: generator self-oracle: a denied trace that approves must fail its own outcome check.
    approves = (
        '[_search(tok), _request(), _decide("denied"), _execute(idem)]\n    if kind == "cross_subject"',
        '[_search(tok), _request(), _decide("approved"), _execute(idem)]\n    if kind == "cross_subject"',
    )

    def outcome_refused(module: types.ModuleType) -> bool:
        trace = next(
            t for t in module.generate_validation_traces(count=7) if t.kind == "approval_denied"
        )
        return _raises_named(
            "DatasetVerificationError", module.verify_validation_trace, trace.to_dict()
        )

    check("FTF-1704-F10", "the real generator's denied trace verifies", not outcome_refused(real))
    check(
        "FTF-1704-F10",
        "a generator whose denied trace approves fails the outcome check",
        outcome_refused(_mutant(approves)),
    )
    check(
        "FTF-1704-F10",
        "without the outcome check it verifies (RED)",
        outcome_refused(
            _mutant(
                approves,
                (
                    "    if not outcome_holds(regenerated.expected_outcome, list(regenerated.steps)):",
                    "    if False:",
                ),
            )
        )
        is False,
    )

    # F11: the oracle must not pass a violation.
    def oracle_catches(module: types.ModuleType) -> bool:
        cases = module.generate_development_cases(count=7)
        return all(
            module.case_oracle_verdict(c, "reference")
            and not module.case_oracle_verdict(c, "violation")
            for c in cases
        )

    check(
        "FTF-1704-F11",
        "real oracle: reference passes, violation fails, for all seven kinds",
        oracle_catches(real),
    )
    _fault(
        "FTF-1704-F11",
        "the cross-subject violation run no longer reads the foreign record",
        [
            (
                '[_call("get_record", record_id="rec-1")]\n    return [_search(tok), _request()]',
                "[]\n    return [_search(tok), _request()]",
            )
        ],
        oracle_catches,
    )


# ---------------------------------------------------------------- determinism


def test_determinism_across_hash_seeds() -> None:
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(API_ROOT)!r});"
        "from evals.reference.validation_dataset import *;"
        "from evals.reference.training_dataset import generate_training_traces, ReservedFinalSuite;"
        "v=[t.to_dict() for t in generate_validation_traces()]; d=generate_development_cases();"
        "t=[x.to_dict() for x in generate_training_traces()];"
        "print(build_reference_eval_splits(v,d,training=t,reserved_final=ReservedFinalSuite()).manifest.manifest_sha256)"
    )
    outs = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        outs.add(
            subprocess.run(
                [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
            ).stdout.strip()
        )
    check(
        "FTF-1704-D1",
        "the manifest hash is identical across PYTHONHASHSEED values",
        len(outs) == 1 and len(next(iter(outs))) == 64,
    )
    check(
        "FTF-1704-D1",
        "and equals this process's hash",
        next(iter(outs)) == _build().manifest.manifest_sha256,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1704 Validation dataset tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1704 Validation dataset tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
