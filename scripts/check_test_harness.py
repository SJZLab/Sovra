#!/usr/bin/env python3
"""
check_test_harness.py — FTF-0003 negative-acceptance gate.

Fails the build if a JUnit XML test report (pytest's or Vitest's) shows zero
collected tests, or any skipped test. A skip covers both an explicit
skip/xfail and a focused .only/.skip test elsewhere in the same file, since
both pytest and Vitest report the tests a focus deselects as "skipped" in
their JUnit output — confirmed empirically, not assumed.

Run: python3 scripts/check_test_harness.py --junit report1.xml [--junit report2.xml ...]
"""

import argparse
import sys
import xml.etree.ElementTree as ET


def count_tests_and_skips(junit_path):
    root = ET.parse(junit_path).getroot()
    suites = root.findall(".//testsuite")
    if suites:
        tests = sum(int(s.get("tests", 0)) for s in suites)
        skipped = sum(int(s.get("skipped", 0)) for s in suites)
    else:
        # No <testsuite> children at all (Vitest emits only the <testsuites>
        # root when nothing was collected) — fall back to its own totals.
        tests = int(root.get("tests", 0))
        skipped = 0
    return tests, skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", action="append", required=True, dest="junit_paths")
    args = parser.parse_args()

    errors = []
    for path in args.junit_paths:
        tests, skipped = count_tests_and_skips(path)
        if tests == 0:
            errors.append(f"{path}: zero collected tests")
        if skipped > 0:
            errors.append(
                f"{path}: {skipped} skipped/focused test(s) — remove .only/.skip " "before merging"
            )
        print(f"{path}: {tests} test(s), {skipped} skipped")

    if errors:
        print(f"FTF-0003 test-harness gate: {len(errors)} error(s):")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    print("FTF-0003 test-harness gate: all reports clean")
    sys.exit(0)


if __name__ == "__main__":
    main()
