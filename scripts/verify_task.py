#!/usr/bin/env python3
"""
verify_task.py — FTF-H001 per-task protected verifier.

Resolves real, currently re-executable verification commands for one task
from spec/verification-map.json and actually runs them against this
checkout. A task with no merged evidence yet is reported BLOCKED, never a
fabricated PASS. An unknown --stage is rejected.

Run: python3 scripts/verify_task.py --task FTF-0002 --stage task
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.spec_verification import verify_task  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--stage", required=True)
    args = parser.parse_args()

    result = verify_task(args.task, args.stage)
    print(f"verify_task: {result.task_id} [{result.stage}] -> {result.outcome}")
    print(f"  {result.reason}")
    if result.commands_run:
        print(f"  commands run: {result.commands_run}")

    return 0 if result.outcome == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
