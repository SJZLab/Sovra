#!/usr/bin/env python3
"""FTF-R011 -- generate and validate `spec/deployment-profiles.yaml`'s
`assertion_applicability` section: the immutable connected/air-gap
assertion-level release matrix.

Section 79.5: "Define assertion-level profile applicability in
`spec/deployment-profiles.yaml` before execution... Internal-only variants
do not replace positive connected evidence in a full-product release."
Section 84.7: "A profile denial test demonstrates denial, not successful
implementation of its connected counterpart."

`spec/qualification-inventory.json` (frozen by FTF-H001's bootstrap) is the
single source of truth for the 43 required scenario IDs -- this module never
re-authors that list by hand. `spec/release-scope.yaml` (FTF-H002) is the
single source of truth for each scenario's owning CAP, hence its owner --
this module projects that mapping rather than inventing owners.

What genuinely needs authored judgment, and is recorded in
`SCENARIO_PROFILE_CLASSIFICATION` below (disclosed, not derived): whether a
scenario's evidence is identical in every deployment profile ("uniform") or
must be split into a distinct connected-positive assertion and a distinct
air-gap assertion ("split") because the connected path depends on reaching
an external/public/vendor-managed destination that Section 79.3/79.4 deny
inside `AIR_GAPPED`. Two of the six split scenarios (Q03, Q12) are the
worked examples Section 79.5 itself gives; the other four (Q26, Q37, Q39)
were identified by re-reading each scenario's own blocking assertion against
Section 52 (CI matrix: public GitHub Actions is not available in
`AIR_GAPPED`), Section 79.3 (the offline install/lifecycle scenario is
`AIR_GAPPED`'s defining positive capability) and Section 79.4 (Q39 is the
scenario that proves external destinations are denied). Every other
scenario's own text names no external/public dependency, so it is uniform
across all four profiles -- disclosed here rather than silently assumed.

A split scenario's `airgap_semantics` is either:
  - "denial": the connected path is inherently impossible offline (e.g. a
    managed cloud provider) -- the air-gap assertion proves the call is
    refused, not attempted.
  - "offline_equivalent": the same user-visible capability is still
    required offline, satisfied through an internal/offline mechanism
    instead of the public/connected one (e.g. an internal git mirror
    instead of public GitHub) -- the air-gap assertion is still a positive
    capability proof, just through a different mechanism.
Both semantics are still genuinely distinct assertion names from their
connected counterpart, and neither ever substitutes for the other.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUALIFICATION_INVENTORY_PATH = ROOT / "spec" / "qualification-inventory.json"
RELEASE_SCOPE_PATH = ROOT / "spec" / "release-scope.yaml"
DEPLOYMENT_PROFILES_PATH = ROOT / "spec" / "deployment-profiles.yaml"

CANONICAL_SCENARIO_IDS = tuple(f"Q{i:02d}" for i in range(1, 44))
CANONICAL_PROFILE_IDS = (
    "INTERNAL_COMPACT",
    "INTERNAL_PRODUCTION",
    "AIR_GAPPED",
    "CONNECTED_MANAGED",
)
NON_AIRGAP_PROFILE_IDS = tuple(p for p in CANONICAL_PROFILE_IDS if p != "AIR_GAPPED")
AIRGAP_PROFILE_ID = "AIR_GAPPED"

# "Cannot turn failure into N/A" (negative_acceptance): N/A is deliberately
# absent from this enum. A required scenario is one of these four states,
# never a silent not-applicable.
ALLOWED_STATUSES = {"NOT_TESTED", "PASS", "FAIL", "BLOCKED_EXTERNAL"}

DEFAULT_OWNER = "Factory implementation owner (relay/control-plane per QUEUE.md)"

# Disclosed judgment call (see module docstring). kind="split" entries name
# both a connected_assertion and an airgap_assertion explicitly so they can
# never collapse into the same string by accident.
SCENARIO_PROFILE_CLASSIFICATION: dict[str, dict[str, str]] = {
    "Q03": {
        "kind": "split",
        "connected_assertion": "connected_private_github_snapshot",
        "airgap_assertion": "airgap_internal_or_offline_snapshot",
        "airgap_semantics": "offline_equivalent",
    },
    "Q12": {
        "kind": "split",
        "connected_assertion": "connected_real_customization",
        "airgap_assertion": "airgap_external_customization_denied",
        "airgap_semantics": "denial",
    },
    "Q26": {
        "kind": "split",
        "connected_assertion": "connected_github_actions_ci_gate",
        "airgap_assertion": "airgap_internal_runner_ci_gate",
        "airgap_semantics": "offline_equivalent",
    },
    "Q37": {
        "kind": "split",
        "connected_assertion": "connected_standard_install_and_lifecycle",
        "airgap_assertion": "airgap_offline_install_and_lifecycle",
        "airgap_semantics": "offline_equivalent",
    },
    "Q39": {
        "kind": "split",
        "connected_assertion": "connected_runtime_independence_verified",
        "airgap_assertion": "airgap_external_paths_denied",
        "airgap_semantics": "denial",
    },
}


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def load_qualification_scenarios() -> dict[str, dict[str, Any]]:
    data = json.loads(QUALIFICATION_INVENTORY_PATH.read_text(encoding="utf-8"))
    scenarios = {s["id"]: s for s in data["scenarios"]}
    missing = [sid for sid in CANONICAL_SCENARIO_IDS if sid not in scenarios]
    if missing:
        raise ValueError(
            f"qualification-inventory.json is missing required scenario IDs: {missing}"
        )
    return scenarios


def load_scenario_owners() -> dict[str, str]:
    with RELEASE_SCOPE_PATH.open(encoding="utf-8") as handle:
        release_scope = yaml.safe_load(handle)
    owners: dict[str, str] = {}
    for cap in release_scope.get("capabilities", []):
        owner = cap.get("owner", DEFAULT_OWNER)
        for scenario_id in cap.get("qualification_scenarios", []):
            owners[scenario_id] = owner
    return owners


def load_deployment_profiles() -> dict[str, Any]:
    with DEPLOYMENT_PROFILES_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def generate_assertion_applicability() -> dict[str, Any]:
    scenarios = load_qualification_scenarios()
    owners = load_scenario_owners()
    entries: dict[str, Any] = {}
    for scenario_id in CANONICAL_SCENARIO_IDS:
        owner = owners.get(scenario_id, DEFAULT_OWNER)
        classification = SCENARIO_PROFILE_CLASSIFICATION.get(scenario_id)
        if classification is None:
            assertion = f"internal_{_slug(scenarios[scenario_id]['title'])}"
            entries[scenario_id] = {
                "owner": owner,
                "kind": "uniform",
                "assertion": assertion,
                "applies_to_profiles": list(CANONICAL_PROFILE_IDS),
                "status": "NOT_TESTED",
                "evidence": [],
            }
        else:
            entries[scenario_id] = {
                "owner": owner,
                "kind": "split",
                "connected_assertion": classification["connected_assertion"],
                "connected_profiles": list(NON_AIRGAP_PROFILE_IDS),
                "airgap_assertion": classification["airgap_assertion"],
                "airgap_profiles": [AIRGAP_PROFILE_ID],
                "airgap_semantics": classification["airgap_semantics"],
                "status": "NOT_TESTED",
                "evidence": [],
            }
    return entries


def validate_deployment_profiles(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    profiles = document.get("profiles")
    if not isinstance(profiles, dict):
        errors.append("profiles: must be a mapping")
    else:
        present_profiles = set(profiles)
        canonical_profiles = set(CANONICAL_PROFILE_IDS)
        for missing in sorted(canonical_profiles - present_profiles):
            errors.append(f"profiles: missing_profile {missing}")
        for extra in sorted(present_profiles - canonical_profiles):
            errors.append(f"profiles: extra_profile {extra}")

    entries = document.get("assertion_applicability")
    if not isinstance(entries, dict):
        return errors + ["assertion_applicability: must be a mapping"]

    present_ids = set(entries)
    canonical_ids = set(CANONICAL_SCENARIO_IDS)
    for missing_id in sorted(canonical_ids - present_ids):
        errors.append(f"assertion_applicability: missing_scenario {missing_id}")
    for extra_id in sorted(present_ids - canonical_ids):
        errors.append(f"assertion_applicability: extra_scenario {extra_id}")

    try:
        owners = load_scenario_owners()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"assertion_applicability: could not load source scenario owners: {exc}")
        owners = {}

    for scenario_id, entry in entries.items():
        if scenario_id not in canonical_ids:
            continue
        if not isinstance(entry, dict):
            errors.append(f"assertion_applicability[{scenario_id}]: must be a mapping")
            continue

        owner = entry.get("owner")
        if not owner or not str(owner).strip():
            errors.append(f"assertion_applicability[{scenario_id}].owner: must not be empty")
        expected_owner = owners.get(scenario_id)
        if expected_owner is not None and owner != expected_owner:
            errors.append(
                f"assertion_applicability[{scenario_id}].owner: {owner!r} does not match the "
                f"frozen release-scope.yaml owner {expected_owner!r} for this scenario's CAP"
            )

        status = entry.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(
                f"assertion_applicability[{scenario_id}].status: {status!r} not in "
                f"{sorted(ALLOWED_STATUSES)} -- a required scenario's result is never 'N/A'"
            )

        kind = entry.get("kind")
        if kind == "uniform":
            assertion = entry.get("assertion")
            if not assertion or not str(assertion).strip():
                errors.append(
                    f"assertion_applicability[{scenario_id}].assertion: must not be empty"
                )
            applies_to = entry.get("applies_to_profiles")
            if list(applies_to or []) != list(CANONICAL_PROFILE_IDS):
                errors.append(
                    f"assertion_applicability[{scenario_id}].applies_to_profiles: a uniform "
                    f"scenario must apply to exactly the four canonical profiles"
                )
        elif kind == "split":
            connected = entry.get("connected_assertion")
            airgap = entry.get("airgap_assertion")
            if not connected or not str(connected).strip():
                errors.append(
                    f"assertion_applicability[{scenario_id}].connected_assertion: must not be empty"
                )
            if not airgap or not str(airgap).strip():
                errors.append(
                    f"assertion_applicability[{scenario_id}].airgap_assertion: must not be empty"
                )
            if connected and airgap and connected == airgap:
                errors.append(
                    f"assertion_applicability[{scenario_id}]: connected_assertion and "
                    f"airgap_assertion must be genuinely distinct names -- an air-gap denial "
                    f"can never be recorded as satisfying the required connected positive test"
                )
            if list(entry.get("connected_profiles") or []) != list(NON_AIRGAP_PROFILE_IDS):
                errors.append(
                    f"assertion_applicability[{scenario_id}].connected_profiles: must be exactly "
                    f"the non-air-gap profiles"
                )
            if list(entry.get("airgap_profiles") or []) != [AIRGAP_PROFILE_ID]:
                errors.append(
                    f"assertion_applicability[{scenario_id}].airgap_profiles: must be exactly "
                    f"['{AIRGAP_PROFILE_ID}']"
                )
            if entry.get("airgap_semantics") not in {"denial", "offline_equivalent"}:
                errors.append(
                    f"assertion_applicability[{scenario_id}].airgap_semantics: must be "
                    f"'denial' or 'offline_equivalent'"
                )
        else:
            errors.append(
                f"assertion_applicability[{scenario_id}].kind: {kind!r} "
                f"must be 'uniform' or 'split'"
            )

    return errors


def generate_expected_checks(profile: str, document: dict[str, Any] | None = None) -> list[str]:
    """The preapproved expected-check manifest for `profile`, built entirely
    from the frozen scope -- before any test executes (Section 84.7: stale
    reports/mutable tags are not release proof; the expected set must be
    fixed first). Never includes a connected-only assertion for
    `AIR_GAPPED`, and never includes an air-gap assertion for a connected
    profile -- "call external provider from air gap" and "use disconnected
    denial instead of required full-product positive test" are structurally
    impossible outputs of this function, not merely discouraged.
    """
    if profile not in CANONICAL_PROFILE_IDS:
        raise ValueError(f"unknown profile: {profile!r}")
    document = document or load_deployment_profiles()
    entries = document["assertion_applicability"]
    expected: list[str] = []
    for scenario_id in CANONICAL_SCENARIO_IDS:
        entry = entries[scenario_id]
        if entry["kind"] == "uniform":
            if profile in entry["applies_to_profiles"]:
                expected.append(entry["assertion"])
        else:
            if profile in entry["connected_profiles"]:
                expected.append(entry["connected_assertion"])
            if profile in entry["airgap_profiles"]:
                expected.append(entry["airgap_assertion"])
    return expected


def assert_profile_qualified(
    profile: str, results: dict[str, str], document: dict[str, Any] | None = None
) -> list[str]:
    """`results` maps assertion name -> observed status. Every status must be
    one of ALLOWED_STATUSES (never 'N/A') and every expected check for
    `profile` must be present and PASS. Missing/incomplete evidence is a
    failure, never silently treated as done (Law 5: existence is not
    evidence)."""
    errors: list[str] = []
    for assertion, status in results.items():
        if status not in ALLOWED_STATUSES:
            errors.append(
                f"result[{assertion}]: status {status!r} not in {sorted(ALLOWED_STATUSES)}"
            )
    expected = generate_expected_checks(profile, document)
    for assertion in expected:
        observed = results.get(assertion)
        if observed is None:
            errors.append(f"{profile}: missing required check {assertion!r}")
        elif observed != "PASS":
            errors.append(f"{profile}: required check {assertion!r} is {observed!r}, not PASS")
    return errors


def assert_full_product_done(
    results: dict[str, str], document: dict[str, Any] | None = None
) -> list[str]:
    """Full-product completion requires every scenario's applicable
    assertion(s) to PASS -- for a split scenario, both the connected
    positive AND the air-gap assertion, never one alone (Section 79.5: "An
    internal-only variant does not replace positive connected evidence in a
    full-product release"; Section 84.7: "A profile denial test
    demonstrates denial, not successful implementation of its connected
    counterpart"). Passing only one profile's expected-check set is
    profile-qualified, never full-product done."""
    document = document or load_deployment_profiles()
    entries = document["assertion_applicability"]
    errors: list[str] = []
    for scenario_id in CANONICAL_SCENARIO_IDS:
        entry = entries[scenario_id]
        if entry["kind"] == "uniform":
            required = [entry["assertion"]]
        else:
            required = [entry["connected_assertion"], entry["airgap_assertion"]]
        for assertion in required:
            status = results.get(assertion)
            if status != "PASS":
                errors.append(
                    f"full_product: {scenario_id} requires {assertion!r} to PASS, got {status!r}"
                )
    return errors


def main() -> None:
    if "--write" in sys.argv:
        document = load_deployment_profiles()
        document["assertion_applicability"] = generate_assertion_applicability()
        errors = validate_deployment_profiles(document)
        if errors:
            print("refusing to write an invalid deployment-profiles document:", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            sys.exit(1)
        with DEPLOYMENT_PROFILES_PATH.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False, default_flow_style=False, width=88)
        print(f"wrote {DEPLOYMENT_PROFILES_PATH}")
        return

    if "--expected" in sys.argv:
        idx = sys.argv.index("--expected")
        profile = sys.argv[idx + 1]
        for assertion in generate_expected_checks(profile):
            print(assertion)
        return

    document = load_deployment_profiles()
    errors = validate_deployment_profiles(document)
    if errors:
        print(f"deployment-profiles validation FAILED ({len(errors)} error(s)):")
        for error in errors:
            print(f"  {error}")
        sys.exit(1)
    print(
        f"deployment-profiles validation passed: all {len(CANONICAL_SCENARIO_IDS)} scenario "
        f"entries present across all {len(CANONICAL_PROFILE_IDS)} profiles"
    )


if __name__ == "__main__":
    main()
