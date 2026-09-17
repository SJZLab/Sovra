#!/usr/bin/env python3
"""
FTF-R011 -- Define immutable connected and air-gap assertion-level release
matrix.

Proves `scripts/validate_profile_scope.py` actually rejects the four
negative_acceptance cases named in the task's own resolved contract
(`tools/r1_handoff.py packet FTF-R011`):

  1. turning a required scenario's result into "N/A"
  2. using the air-gap denial as if it satisfied the required full-product
     connected-positive test
  3. calling an external/connected provider from the AIR_GAPPED profile
  4. dropping an old (already-frozen) scenario ID

and that the real, currently-committed `spec/deployment-profiles.yaml`
passes cleanly with all 43 required scenarios covered.

Run directly: python3 tests/security/test_profile_scope.py
"""

import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.validate_profile_scope import (  # noqa: E402
    AIRGAP_PROFILE_ID,
    ALLOWED_STATUSES,
    CANONICAL_PROFILE_IDS,
    CANONICAL_SCENARIO_IDS,
    NON_AIRGAP_PROFILE_IDS,
    assert_full_product_done,
    assert_profile_qualified,
    generate_expected_checks,
    load_deployment_profiles,
    validate_deployment_profiles,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def real_document():
    return load_deployment_profiles()


def all_pass_results(document):
    """A results dict where every assertion named anywhere in the document
    is recorded PASS -- the maximal honest 'everything passed' fixture."""
    results = {}
    for entry in document["assertion_applicability"].values():
        if entry["kind"] == "uniform":
            results[entry["assertion"]] = "PASS"
        else:
            results[entry["connected_assertion"]] = "PASS"
            results[entry["airgap_assertion"]] = "PASS"
    return results


def test_real_committed_document_is_valid_and_complete():
    document = real_document()
    errors = validate_deployment_profiles(document)
    check(
        "FTF-R011",
        f"the real committed spec/deployment-profiles.yaml validates cleanly, got: {errors}",
        errors == [],
    )
    check(
        "FTF-R011",
        "all 43 canonical scenario IDs are present",
        set(document["assertion_applicability"]) == set(CANONICAL_SCENARIO_IDS),
    )
    check(
        "FTF-R011",
        "all four canonical profiles are present",
        set(document["profiles"]) == set(CANONICAL_PROFILE_IDS),
    )


def test_worked_examples_preserved():
    """Section 79.5's own two worked examples (Q12, Q03) must survive
    FTF-R011's extension byte-for-byte -- the negative_acceptance clause
    forbids dropping an old ID, and these are the frozen text this file
    already carried before this task touched it."""
    document = real_document()
    q12 = document["assertion_applicability"]["Q12"]
    check(
        "FTF-R011",
        "Q12's original worked-example variant names are preserved unchanged",
        q12["connected_assertion"] == "connected_real_customization"
        and q12["airgap_assertion"] == "airgap_external_customization_denied",
    )
    q03 = document["assertion_applicability"]["Q03"]
    check(
        "FTF-R011",
        "Q03's original worked-example variant names are preserved unchanged",
        q03["connected_assertion"] == "connected_private_github_snapshot"
        and q03["airgap_assertion"] == "airgap_internal_or_offline_snapshot",
    )


def test_dropping_an_old_id_is_rejected():
    document = copy.deepcopy(real_document())
    del document["assertion_applicability"]["Q10"]
    errors = validate_deployment_profiles(document)
    check(
        "FTF-R011",
        "removing a previously-frozen scenario ID (Q10) is rejected as missing_scenario",
        any("missing_scenario Q10" in e for e in errors),
    )


def test_na_status_is_rejected():
    document = copy.deepcopy(real_document())
    document["assertion_applicability"]["Q08"]["status"] = "N/A"
    errors = validate_deployment_profiles(document)
    check(
        "FTF-R011",
        "a required scenario's status cannot be turned into 'N/A'",
        any("Q08" in e and "status" in e for e in errors),
    )
    # Also enforced at the results-reporting layer, not just the frozen
    # document: assert_profile_qualified must reject an 'N/A' result too.
    real_doc = real_document()
    expected = generate_expected_checks("INTERNAL_PRODUCTION", real_doc)
    bad_results = dict.fromkeys(expected, "PASS")
    bad_results[expected[0]] = "N/A"
    errors = assert_profile_qualified("INTERNAL_PRODUCTION", bad_results, real_doc)
    check(
        "FTF-R011",
        "assert_profile_qualified rejects an 'N/A' result even when every other check passes",
        any("N/A" in e for e in errors),
    )


def test_split_scenario_cannot_collapse_to_one_name():
    document = copy.deepcopy(real_document())
    document["assertion_applicability"]["Q12"]["airgap_assertion"] = "connected_real_customization"
    errors = validate_deployment_profiles(document)
    check(
        "FTF-R011",
        "a split scenario whose airgap_assertion is renamed to equal its connected_assertion "
        "is rejected (an air-gap denial can never be recorded as satisfying the connected test)",
        any("genuinely distinct" in e for e in errors),
    )


def test_airgap_denial_cannot_satisfy_connected_requirement():
    """Real fixture proving negative_acceptance #2: 'use disconnected denial
    instead of required full-product positive test'. Only the airgap
    assertions pass; every connected assertion is missing. A connected
    profile's qualification must fail, and full-product completion must
    fail, even though Q12/Q03's airgap side is fully green."""
    document = real_document()
    results = {}
    for entry in document["assertion_applicability"].values():
        if entry["kind"] == "split":
            results[entry["airgap_assertion"]] = "PASS"
        else:
            results[entry["assertion"]] = "PASS"

    connected_errors = assert_profile_qualified("INTERNAL_PRODUCTION", results, document)
    check(
        "FTF-R011",
        "INTERNAL_PRODUCTION cannot be qualified from air-gap-denial results alone -- the "
        "required connected_* checks are reported missing, not satisfied by the denial",
        any("missing required check 'connected_" in e for e in connected_errors),
    )

    full_product_errors = assert_full_product_done(results, document)
    check(
        "FTF-R011",
        "full-product completion is denied when only air-gap denial evidence exists for split "
        "scenarios",
        any("requires 'connected_" in e for e in full_product_errors),
    )


def test_external_provider_never_expected_under_airgap():
    """negative_acceptance #3: 'call external provider from air gap'. The
    preapproved AIR_GAPPED expected-check manifest must never contain a
    connected_* assertion, and the connected profiles' manifests must never
    contain an airgap_* assertion -- structurally, not just by convention."""
    document = real_document()
    airgap_expected = generate_expected_checks(AIRGAP_PROFILE_ID, document)
    connected_only_assertions = {
        entry["connected_assertion"]
        for entry in document["assertion_applicability"].values()
        if entry["kind"] == "split"
    }
    check(
        "FTF-R011",
        "no connected-only assertion ever appears in the AIR_GAPPED expected-check manifest",
        connected_only_assertions.isdisjoint(airgap_expected),
    )
    airgap_only_assertions = {
        entry["airgap_assertion"]
        for entry in document["assertion_applicability"].values()
        if entry["kind"] == "split"
    }
    for profile in NON_AIRGAP_PROFILE_IDS:
        expected = generate_expected_checks(profile, document)
        check(
            "FTF-R011",
            f"no air-gap-only assertion ever appears in {profile}'s expected-check manifest",
            airgap_only_assertions.isdisjoint(expected),
        )


def test_profile_only_pass_is_not_full_product():
    """Section 79.5: 'AIR_GAP_PROFILE_QUALIFIED ... must never be displayed
    as FULL_PRODUCT_DONE'. Passing every AIR_GAPPED-expected check qualifies
    that profile but must still fail full-product completion, since the
    connected-positive halves of the five split scenarios are untested."""
    document = real_document()
    airgap_expected = generate_expected_checks(AIRGAP_PROFILE_ID, document)
    results = dict.fromkeys(airgap_expected, "PASS")

    profile_errors = assert_profile_qualified(AIRGAP_PROFILE_ID, results, document)
    check(
        "FTF-R011",
        "AIR_GAPPED profile qualifies cleanly from its own expected checks",
        profile_errors == [],
    )

    full_product_errors = assert_full_product_done(results, document)
    check(
        "FTF-R011",
        "the same AIR_GAPPED-only results never qualify as full-product done",
        len(full_product_errors) > 0,
    )


def test_genuinely_complete_results_pass_both_gates():
    """Positive control: when every assertion in the document is actually
    recorded PASS (both halves of every split scenario), every profile
    qualifies and full-product completion passes with zero errors -- proving
    the checks above fail for the injected defect, not for everything."""
    document = real_document()
    results = all_pass_results(document)
    for profile in CANONICAL_PROFILE_IDS:
        errors = assert_profile_qualified(profile, results, document)
        check(
            "FTF-R011",
            f"{profile} qualifies when every applicable check genuinely passed",
            errors == [],
        )
    full_product_errors = assert_full_product_done(results, document)
    check(
        "FTF-R011",
        "full-product completion passes when every scenario's required assertion(s) "
        "genuinely passed",
        full_product_errors == [],
    )


def test_missing_owner_is_rejected():
    document = copy.deepcopy(real_document())
    document["assertion_applicability"]["Q20"]["owner"] = ""
    errors = validate_deployment_profiles(document)
    check(
        "FTF-R011",
        "an empty owner is rejected",
        any("Q20" in e and "owner" in e for e in errors),
    )


def test_dropping_a_profile_is_rejected():
    document = copy.deepcopy(real_document())
    del document["profiles"][AIRGAP_PROFILE_ID]
    errors = validate_deployment_profiles(document)
    check(
        "FTF-R011",
        "removing the AIR_GAPPED profile itself is rejected as missing_profile",
        any("missing_profile AIR_GAPPED" in e for e in errors),
    )


def test_allowed_statuses_never_include_na():
    check(
        "FTF-R011",
        "the allowed result-status enum never contains 'N/A'",
        "N/A" not in ALLOWED_STATUSES and "n/a" not in {s.lower() for s in ALLOWED_STATUSES},
    )


def test_fault_injection_against_tracked_module():
    """Real fault injection against the actual file on disk (not a
    reimplementation): temporarily remove the connected/airgap
    distinctness guard from `validate_deployment_profiles`, confirm the
    collapsed-name defect from `test_split_scenario_cannot_collapse_to_one_name`
    wrongly passes, then restore byte-identical."""
    module_path = ROOT / "scripts" / "validate_profile_scope.py"
    original = module_path.read_text(encoding="utf-8")
    marker = "\n".join(
        [
            "            if connected and airgap and connected == airgap:",
            "                errors.append(",
            '                    f"assertion_applicability[{scenario_id}]: '
            'connected_assertion and "',
            '                    f"airgap_assertion must be genuinely distinct '
            'names -- an air-gap denial "',
            '                    f"can never be recorded as satisfying the '
            'required connected positive test"',
            "                )",
            "",
        ]
    )
    check(
        "FTF-R011",
        "the distinctness guard block is present in the tracked module before injection",
        marker in original,
    )
    mutated = original.replace(marker, "")
    module_path.write_text(mutated, encoding="utf-8")
    try:
        import importlib

        import scripts.validate_profile_scope as mod

        importlib.reload(mod)
        document = copy.deepcopy(mod.load_deployment_profiles())
        document["assertion_applicability"]["Q12"]["airgap_assertion"] = (
            "connected_real_customization"
        )
        errors = mod.validate_deployment_profiles(document)
        check(
            "FTF-R011",
            "with the guard removed, the collapsed-name defect wrongly passes validation",
            not any("genuinely distinct" in e for e in errors),
        )
    finally:
        module_path.write_text(original, encoding="utf-8")
        import importlib

        import scripts.validate_profile_scope as mod

        importlib.reload(mod)

    check(
        "FTF-R011",
        "the tracked module file is restored byte-identical after fault injection",
        module_path.read_text(encoding="utf-8") == original,
    )
    errors_after_restore = mod.validate_deployment_profiles(mod.load_deployment_profiles())
    check(
        "FTF-R011",
        "validation is clean again once the guard is restored",
        errors_after_restore == [],
    )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-R011 deployment-profile assertion-level release matrix: {len(FAILURES)} failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-R011 deployment-profile assertion-level release matrix: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
