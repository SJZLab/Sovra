#!/usr/bin/env python3
"""FTF-R017 -- OSS dependency lifecycle / license validator.

Validates `spec/component-maintenance.yaml` against
`spec/component-maintenance.schema.json` (Section 80.4: owner/support/
version/security/export plan for every reused component) and enforces the
one whole-set rule no single-record schema keyword can express: the seven
records must be exactly the seven canonical component keys, each exactly
once. Reuses `scripts.architecture_lock`'s bounded JSON Schema validator
and its `_check_exact_key_set` helper -- the same whole-set check
FTF-R001's own component-lock document already uses -- rather than
duplicating that logic (Section 4: "reuse an existing compatible module
instead of creating a duplicate").
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.architecture_lock import (  # noqa: E402
    _check_exact_key_set,
    load_json,
    validate_instance,
)

SCHEMA_PATH = REPO_ROOT / "spec" / "component-maintenance.schema.json"
DOCUMENT_PATH = REPO_ROOT / "spec" / "component-maintenance.yaml"


def load_component_maintenance_document(path: Path = DOCUMENT_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_component_maintenance_document(document: dict) -> list[str]:
    schema = load_json(SCHEMA_PATH)
    errors = validate_instance(document, schema)
    records = document.get("component_maintenance_records", [])
    keys = [r.get("component_key") for r in records if isinstance(r, dict)]
    errors.extend(_check_exact_key_set(keys, "maintenance record", "recorded"))
    return errors


def main() -> None:
    document = load_component_maintenance_document()
    errors = validate_component_maintenance_document(document)
    if errors:
        print(f"component-maintenance validation: {len(errors)} error(s)")
        for error in errors:
            print(f"  {error}")
        sys.exit(1)
    print("component-maintenance validation: all 7 canonical components recorded, " "schema clean")
    sys.exit(0)


if __name__ == "__main__":
    main()
