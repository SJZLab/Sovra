#!/usr/bin/env python3
"""FTF-H002 -- generate and validate `spec/release-scope.yaml`.

Section 64.2: "Create `spec/release-scope.yaml` in the implementation
repository. Required IDs cannot be removed, downgraded or changed to
not-applicable by the implementing agent. Record owner, supported version
profile, implementation tasks, qualification scenarios and status for each."

`spec/qualification-inventory.json` (frozen by FTF-H001's bootstrap) is the
single source of truth for each CAP-01..CAP-21 entry's title/minimum_evidence
/implementation_tasks/qualification_scenarios -- this module never
re-authors that data by hand (avoiding drift between two hand-maintained
copies), it only projects it into the release-scope report and adds the
owner/supported_version_profile columns Section 64.2 also requires.
`validate_release_scope` is the whole-set business rule no YAML schema
keyword alone can express: exactly the 21 canonical CAP IDs, each `required:
true`, each with its implementation_tasks/qualification_scenarios matching
the frozen inventory exactly (rejecting a quietly cut required scope).
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUALIFICATION_INVENTORY_PATH = ROOT / "spec" / "qualification-inventory.json"
RELEASE_SCOPE_PATH = ROOT / "spec" / "release-scope.yaml"

CANONICAL_CAP_IDS = tuple(f"CAP-{i:02d}" for i in range(1, 22))
ALLOWED_STATUSES = {"NOT_IMPLEMENTED", "IN_PROGRESS", "IMPLEMENTED", "QUALIFIED"}

# Real, disclosed placeholder: no CAP's underlying dependency/API/driver
# version has actually been resolved against a live target environment yet
# (Section 64.3 is an onboarding gate that has not been run against real
# infrastructure from this sandbox). This is never used to claim readiness --
# `validate_release_scope` does not accept it as evidence of anything, it is
# only the documented current state of an unresolved column, not a fabricated
# version string.
UNRESOLVED_VERSION_PROFILE = "NOT_YET_RESOLVED (Section 64.3 onboarding gate not yet run)"
DEFAULT_OWNER = "Factory implementation owner (relay/control-plane per QUEUE.md)"


def load_qualification_capabilities() -> dict[str, dict[str, Any]]:
    data = json.loads(QUALIFICATION_INVENTORY_PATH.read_text(encoding="utf-8"))
    capabilities = {cap["id"]: cap for cap in data["capabilities"]}
    missing = [cap_id for cap_id in CANONICAL_CAP_IDS if cap_id not in capabilities]
    if missing:
        raise ValueError(f"qualification-inventory.json is missing required CAP IDs: {missing}")
    return capabilities


def generate_release_scope() -> dict[str, Any]:
    capabilities = load_qualification_capabilities()
    entries = []
    for cap_id in CANONICAL_CAP_IDS:
        source = capabilities[cap_id]
        entries.append(
            {
                "id": cap_id,
                "title": source["title"],
                "owner": DEFAULT_OWNER,
                "supported_version_profile": UNRESOLVED_VERSION_PROFILE,
                "minimum_evidence": source["minimum_evidence"],
                "implementation_tasks": list(source["implementation_tasks"]),
                "qualification_scenarios": list(source["qualification_scenarios"]),
                "required": True,
                "status": source["status"],
            }
        )
    return {
        "schema_version": 1,
        "source": "spec/qualification-inventory.json (frozen by FTF-H001 bootstrap)",
        "capabilities": entries,
    }


def validate_release_scope(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    entries = document.get("capabilities")
    if not isinstance(entries, list):
        return ["capabilities: must be a list"]

    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        cap_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(cap_id, str):
            errors.append("capabilities: entry with no string 'id'")
            continue
        if cap_id in by_id:
            errors.append(f"capabilities: duplicate_capability {cap_id}")
        by_id[cap_id] = entry

    present_ids = set(by_id)
    canonical_ids = set(CANONICAL_CAP_IDS)
    for missing_id in sorted(canonical_ids - present_ids):
        errors.append(f"capabilities: missing_capability {missing_id}")
    for extra_id in sorted(present_ids - canonical_ids):
        errors.append(f"capabilities: extra_capability {extra_id}")

    try:
        source_capabilities = load_qualification_capabilities()
    except (OSError, ValueError, KeyError) as exc:
        errors.append(f"capabilities: could not load source qualification inventory: {exc}")
        return errors

    for cap_id, entry in by_id.items():
        if cap_id not in canonical_ids:
            continue
        if entry.get("required") is not True:
            errors.append(f"capabilities[{cap_id}].required: must be true, never downgraded")
        if entry.get("status") not in ALLOWED_STATUSES:
            errors.append(
                f"capabilities[{cap_id}].status: {entry.get('status')!r} "
                f"not in {sorted(ALLOWED_STATUSES)}"
            )
        source = source_capabilities.get(cap_id, {})
        expected_tasks = list(source.get("implementation_tasks", []))
        if list(entry.get("implementation_tasks", [])) != expected_tasks:
            errors.append(
                f"capabilities[{cap_id}].implementation_tasks: does not match the frozen "
                f"qualification-inventory.json set (required scope cannot be quietly cut)"
            )
        expected_scenarios = list(source.get("qualification_scenarios", []))
        if list(entry.get("qualification_scenarios", [])) != expected_scenarios:
            errors.append(
                f"capabilities[{cap_id}].qualification_scenarios: does not match the frozen "
                f"qualification-inventory.json set (required scope cannot be quietly cut)"
            )
        if not entry.get("owner") or not str(entry.get("owner")).strip():
            errors.append(f"capabilities[{cap_id}].owner: must not be empty")
        if (
            not entry.get("supported_version_profile")
            or not str(entry.get("supported_version_profile")).strip()
        ):
            errors.append(f"capabilities[{cap_id}].supported_version_profile: must not be empty")

    return errors


def load_release_scope() -> dict[str, Any]:
    with RELEASE_SCOPE_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> None:
    if "--write" in sys.argv:
        document = generate_release_scope()
        errors = validate_release_scope(document)
        if errors:
            print("refusing to write an invalid release-scope document:", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            sys.exit(1)
        with RELEASE_SCOPE_PATH.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False, default_flow_style=False)
        print(f"wrote {RELEASE_SCOPE_PATH}")
        return

    document = load_release_scope()
    errors = validate_release_scope(document)
    if errors:
        print(f"release-scope validation FAILED ({len(errors)} error(s)):")
        for error in errors:
            print(f"  {error}")
        sys.exit(1)
    print(f"release-scope validation passed: all {len(CANONICAL_CAP_IDS)} CAP entries present")


if __name__ == "__main__":
    main()
