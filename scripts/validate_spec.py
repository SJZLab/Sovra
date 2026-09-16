#!/usr/bin/env python3
"""
validate_spec.py — FTF-H001 executable specification validator.

Validates the frozen task/dependency/scenario graph in spec/tasks.json
against spec/qualification-inventory.json (missing_task, duplicate_id,
cyclic_dependency, missing_scenario), and validates spec/verification-map.json's
recorded evidence for every task that claims merged evidence (stale_commit,
skipped_test, empty_commands, unknown_task). Never returns success on an
invalid ledger.

Run: python3 scripts/validate_spec.py --strict
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.spec_verification import validate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="required — validate_spec has no lenient mode; every defect is fatal",
    )
    args = parser.parse_args()
    if not args.strict:
        print("validate_spec: --strict is required (no lenient mode exists)")
        return 1

    errors = validate()
    if errors:
        print(f"validate_spec: {len(errors)} defect(s) found:")
        for error in errors:
            print(f"  {error}")
        return 1

    print("validate_spec: task graph and verification-map are clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
