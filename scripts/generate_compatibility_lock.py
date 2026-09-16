#!/usr/bin/env python3
"""FTF-H002 -- write `spec/compatibility-lock.json` from a real preflight run.

This is a genuine snapshot of `app.services.environment_preflight.run_preflight()`
executed against the environment that runs this script -- never a hand-typed
or fabricated set of versions/statuses. Re-run it (`python3
scripts/generate_compatibility_lock.py`) after any real environment change;
`tests/tasks/test_ftf_h002.py` re-runs preflight live and compares its
`overall_status` against the committed file's own `overall_status`, so a
stale snapshot claiming AVAILABLE where the real environment is now
BLOCKED_EXTERNAL fails the gate.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.environment_preflight import (  # noqa: E402
    run_preflight,
    to_compatibility_report,
)

COMPATIBILITY_LOCK_PATH = ROOT / "spec" / "compatibility-lock.json"


def main() -> None:
    report = to_compatibility_report(run_preflight())
    COMPATIBILITY_LOCK_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {COMPATIBILITY_LOCK_PATH} (overall_status={report['overall_status']})")


if __name__ == "__main__":
    main()
