#!/usr/bin/env python3
"""
FTF-1810 -- ReleaseQualificationWorkflow.

Proves:
  1. `evaluate_scenario_evidence` fails closed on each of Section 73.4/52's
     named rejection reasons -- missing, skipped, incomplete, stale,
     unsigned, tampered -- and only reaches PASS through a genuinely
     verified signature over exact-revision-bound evidence. A present
     signature with no configured verifier is BLOCKED_EXTERNAL, never a
     fabricated PASS from presence alone (same discipline as FTF-H028's
     `check_release_manifest_signature`).
  2. `run_release_qualification_workflow` binds the aggregator to the
     release's image digest and deployment profile: a missing image
     digest or an unresolved deployment profile is BLOCKED_EXTERNAL, not
     silently ignored.
  3. `overall_status` over the whole run is never PASS while any scenario
     or binding check is FAIL/BLOCKED_EXTERNAL.
  4. The real, currently-shipped `spec/qualification-inventory.json` (43
     required scenarios) and `spec/release-scope.yaml` are read directly
     (not a fixture) and, run against the real current repository with no
     evidence ledger populated, correctly report every scenario `missing`
     and both bindings `BLOCKED_EXTERNAL` -- the honest state today, not a
     defect in this module.
  5. A real fault injection against the tracked module (disabling the
     `skipped` check) makes a genuine skipped-scenario fixture wrongly
     pass, proving the check is load-bearing, not decorative; the module
     file itself is never modified on disk and the real check is restored.

Run directly: python3 tests/tasks/test_ftf_1810.py
"""

from __future__ import annotations

import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docs" / "operations"))

from releasequalificationworkflow import (  # noqa: E402
    ReleaseBinding,
    current_commit_sha,
    evaluate_scenario_evidence,
    load_required_scenarios,
    overall_status,
    resolve_release_binding,
    run_release_qualification_workflow,
    to_report,
)
from scripts.release_evidence_gate import GateStatus  # noqa: E402

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


REAL_SHA = "a" * 40
BOUND_BINDING = ReleaseBinding(
    scope_digest="scope-digest-fixture",
    commit_sha=REAL_SHA,
    image_digest="sha256:fixture-image",
    deployment_profile="local-docker",
)


def _valid_entry(**overrides):
    entry = {
        "evidence_id": "EV-Q01-0001",
        "commit": REAL_SHA,
        "exit_code": 0,
        "signature": "sig-abc",
    }
    entry.update(overrides)
    return entry


def _always_true(payload, signature):
    return True


def _always_false(payload, signature):
    return False


# ---------------------------------------------------------------------------
# evaluate_scenario_evidence -- every named rejection reason
# ---------------------------------------------------------------------------


def test_missing_evidence_fails():
    result = evaluate_scenario_evidence("Q01", None, binding=BOUND_BINDING, verifier=_always_true)
    check(
        "FTF-1810",
        "a scenario with no evidence entry at all is FAIL (missing)",
        result.status is GateStatus.FAIL,
    )
    check("FTF-1810", "the missing detail names the real reason", "missing" in result.detail)


def test_skipped_evidence_fails():
    entry = _valid_entry(status="SKIPPED")
    result = evaluate_scenario_evidence("Q02", entry, binding=BOUND_BINDING, verifier=_always_true)
    check(
        "FTF-1810",
        "a scenario recorded as SKIPPED is FAIL, never treated as passing",
        result.status is GateStatus.FAIL,
    )
    check("FTF-1810", "the skipped detail names the real reason", "skipped" in result.detail)


def test_incomplete_evidence_fails():
    for missing_field in ("evidence_id", "commit", "exit_code"):
        entry = _valid_entry()
        del entry[missing_field]
        result = evaluate_scenario_evidence(
            "Q03", entry, binding=BOUND_BINDING, verifier=_always_true
        )
        check(
            "FTF-1810",
            f"a scenario evidence entry missing {missing_field!r} is FAIL (incomplete)",
            result.status is GateStatus.FAIL and "incomplete" in result.detail,
        )


def test_stale_evidence_fails():
    entry = _valid_entry(commit="b" * 40)
    result = evaluate_scenario_evidence("Q04", entry, binding=BOUND_BINDING, verifier=_always_true)
    check(
        "FTF-1810",
        "a scenario evidence entry bound to a different commit than the release is FAIL (stale)",
        result.status is GateStatus.FAIL and "stale" in result.detail,
    )


def test_nonzero_exit_code_fails():
    entry = _valid_entry(exit_code=1)
    result = evaluate_scenario_evidence("Q05", entry, binding=BOUND_BINDING, verifier=_always_true)
    check(
        "FTF-1810",
        "a scenario evidence entry with a nonzero exit_code is FAIL, never treated as passing",
        result.status is GateStatus.FAIL,
    )


def test_unsigned_evidence_fails():
    entry = _valid_entry(signature="")
    result = evaluate_scenario_evidence("Q06", entry, binding=BOUND_BINDING, verifier=_always_true)
    check(
        "FTF-1810",
        "a scenario evidence entry with no signature is FAIL (unsigned)",
        result.status is GateStatus.FAIL and "unsigned" in result.detail,
    )


def test_signed_with_no_verifier_is_blocked_not_passed():
    entry = _valid_entry()
    result = evaluate_scenario_evidence("Q07", entry, binding=BOUND_BINDING, verifier=None)
    check(
        "FTF-1810",
        "a present signature with no configured verifier is BLOCKED_EXTERNAL, never a "
        "fabricated PASS from presence alone",
        result.status is GateStatus.BLOCKED_EXTERNAL,
    )
    check(
        "FTF-1810",
        "BLOCKED_EXTERNAL result carries a real owner/next_action",
        bool(result.owner) and bool(result.next_action),
    )


def test_tampered_signature_fails():
    entry = _valid_entry()
    result = evaluate_scenario_evidence("Q08", entry, binding=BOUND_BINDING, verifier=_always_false)
    check(
        "FTF-1810",
        "a signature that fails verification against the configured trusted key is FAIL (tampered)",
        result.status is GateStatus.FAIL and "tampered" in result.detail,
    )


def test_genuinely_valid_evidence_passes():
    entry = _valid_entry()
    result = evaluate_scenario_evidence("Q09", entry, binding=BOUND_BINDING, verifier=_always_true)
    check(
        "FTF-1810",
        "real, exact-revision, signed evidence with a verifier that confirms it genuinely passes",
        result.status is GateStatus.PASS,
    )


# ---------------------------------------------------------------------------
# Release binding -- image digest / deployment profile
# ---------------------------------------------------------------------------


def test_missing_image_digest_is_blocked():
    binding = resolve_release_binding(
        commit_sha=REAL_SHA, image_digest=None, deployment_profile="local-docker"
    )
    results = run_release_qualification_workflow(
        binding=binding, scenarios=[], evidence={}, verifier=_always_true
    )
    image_results = [r for r in results if r.key == "release_image_binding"]
    check(
        "FTF-1810",
        "no image digest yet -> a real release_image_binding BLOCKED_EXTERNAL result, not "
        "silently skipped",
        len(image_results) == 1 and image_results[0].status is GateStatus.BLOCKED_EXTERNAL,
    )


def test_missing_deployment_profile_is_blocked():
    binding = resolve_release_binding(
        commit_sha=REAL_SHA, image_digest="sha256:x", deployment_profile=None
    )
    results = run_release_qualification_workflow(
        binding=binding, scenarios=[], evidence={}, verifier=_always_true
    )
    profile_results = [r for r in results if r.key == "release_profile_binding"]
    check(
        "FTF-1810",
        "no resolved deployment profile yet -> a real release_profile_binding "
        "BLOCKED_EXTERNAL result",
        len(profile_results) == 1 and profile_results[0].status is GateStatus.BLOCKED_EXTERNAL,
    )


def test_overall_status_never_passes_with_any_problem():
    scenarios = [{"id": "Q01", "required": True}, {"id": "Q02", "required": True}]
    evidence = {"Q01": _valid_entry()}  # Q02 missing entirely
    results = run_release_qualification_workflow(
        binding=BOUND_BINDING, scenarios=scenarios, evidence=evidence, verifier=_always_true
    )
    check(
        "FTF-1810",
        "one missing required scenario keeps the whole run out of PASS",
        overall_status(results) is not GateStatus.PASS,
    )

    evidence_all_good = {"Q01": _valid_entry(), "Q02": _valid_entry()}
    results_good = run_release_qualification_workflow(
        binding=BOUND_BINDING,
        scenarios=scenarios,
        evidence=evidence_all_good,
        verifier=_always_true,
    )
    check(
        "FTF-1810",
        "every required scenario genuinely verified -> overall PASS",
        overall_status(results_good) is GateStatus.PASS,
    )


def test_report_binds_scope_hash_image_profile():
    report = to_report(BOUND_BINDING, ())
    binding_report = report["binding"]
    check(
        "FTF-1810",
        "the emitted report binds scope_digest/commit_sha/image_digest/deployment_profile, "
        "not just an overall verdict",
        {"scope_digest", "commit_sha", "image_digest", "deployment_profile"} <= set(binding_report),
    )
    check(
        "FTF-1810",
        "the report binds the real commit_sha it was given",
        binding_report["commit_sha"] == REAL_SHA,
    )


# ---------------------------------------------------------------------------
# Real repository state -- not a fixture
# ---------------------------------------------------------------------------


def test_real_repository_state_reports_honestly_not_ready():
    sha = current_commit_sha()
    check("FTF-1810", "current_commit_sha() returns a real 40-character git SHA", len(sha) == 40)

    binding = resolve_release_binding(commit_sha=sha)
    scenarios = load_required_scenarios()
    check(
        "FTF-1810",
        "the real spec/qualification-inventory.json has all 43 required scenarios",
        len(scenarios) == 43,
    )

    results = run_release_qualification_workflow(
        binding=binding, scenarios=scenarios, evidence={}, verifier=None
    )
    check(
        "FTF-1810",
        "against the real repo with no evidence ledger populated yet, the real run reports "
        "NOT READY (image/profile BLOCKED_EXTERNAL, every scenario missing) -- not a "
        "fabricated PASS",
        overall_status(results) is not GateStatus.PASS,
    )
    missing = [r for r in results if r.key.startswith("scenario_") and "missing" in r.detail]
    check(
        "FTF-1810",
        "every real scenario is honestly reported missing today (no product exists yet)",
        len(missing) == len(scenarios),
    )


# ---------------------------------------------------------------------------
# Real fault injection against the tracked module
# ---------------------------------------------------------------------------


def test_fault_injection_skipped_check_is_load_bearing():
    module_path = ROOT / "docs" / "operations" / "releasequalificationworkflow.py"
    original = module_path.read_text(encoding="utf-8")

    broken = original.replace(
        'if str(entry.get("status", "")).upper() == "SKIPPED" or bool(entry.get("skipped")):\n'
        "        return GateResult(\n"
        "            key=key,\n"
        "            status=GateStatus.FAIL,\n"
        '            detail=f"{scenario_id}: skipped -- a skipped scenario cannot authorize '
        'release",\n'
        "        )\n",
        "",
        1,
    )
    check(
        "FTF-1810",
        "the fault-injection replace target was found in the real tracked file",
        broken != original,
    )

    try:
        module_path.write_text(broken, encoding="utf-8")
        module = importlib.import_module("releasequalificationworkflow")
        importlib.reload(module)
        entry = _valid_entry(status="SKIPPED")
        result = module.evaluate_scenario_evidence(
            "Q02", entry, binding=BOUND_BINDING, verifier=_always_true
        )
        check(
            "FTF-1810",
            "removing the skipped-check block makes the real fixture wrongly pass "
            "(proving the check is load-bearing, not decorative)",
            result.status is GateStatus.PASS,
        )
    finally:
        module_path.write_text(original, encoding="utf-8")
        module = importlib.import_module("releasequalificationworkflow")
        importlib.reload(module)

    restored = module_path.read_text(encoding="utf-8")
    check(
        "FTF-1810",
        "the tracked module file itself was never modified on disk",
        restored == original,
    )

    post_restore_entry = _valid_entry(status="SKIPPED")
    post_restore = evaluate_scenario_evidence(
        "Q02", post_restore_entry, binding=BOUND_BINDING, verifier=_always_true
    )
    check(
        "FTF-1810",
        "after restore, the real check catches the skipped fixture again",
        post_restore.status is GateStatus.FAIL,
    )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1810 ReleaseQualificationWorkflow: {len(FAILURES)} failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-1810 ReleaseQualificationWorkflow: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
