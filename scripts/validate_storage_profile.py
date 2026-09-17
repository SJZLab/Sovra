#!/usr/bin/env python3
"""FTF-R017 -- storage profile structural validator.

Validates `infra/internal/storage-profile.yaml`: exactly the eight
Section-80.4-named semantics, each exactly once, `backend` never the
archived MinIO default, and -- the real negative_acceptance check --
`qualified: true` never accepted without a real, non-placeholder
`evidence` value (a bare `NOT_YET_RUN`/empty string claiming a real result
is a defect this validator catches, not a value it lets through).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "infra" / "internal" / "storage-profile.yaml"

CANONICAL_SEMANTIC_IDS = frozenset(
    {
        "multipart",
        "checksum",
        "versioning",
        "immutable_publication",
        "identity",
        "retention_deletion",
        "encryption",
        "backup_and_restore",
    }
)

FORBIDDEN_BACKENDS = frozenset({"minio", "archived_minio", "minio_default"})
_PLACEHOLDER_EVIDENCE = frozenset({"", "NOT_YET_RUN", "TODO", "TBD"})


def load_storage_profile(path: Path = PROFILE_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_storage_profile(document: dict) -> list[str]:
    errors: list[str] = []

    backend = document.get("backend")
    if backend in FORBIDDEN_BACKENDS:
        errors.append(
            f"forbidden_backend: {backend!r} is the archived MinIO default, "
            "never a permitted selection (Section 80.4)"
        )
    if backend not in {"seaweedfs_offline_fixture", "maintained_organizational_s3"}:
        errors.append(f"unknown_backend: {backend!r} is not a recognized storage backend")

    semantics = document.get("required_semantics", [])
    seen: set[str] = set()
    for entry in semantics:
        sid = entry.get("id")
        if sid in seen:
            errors.append(f"duplicate_semantic: {sid!r} appears more than once")
        seen.add(sid)

        qualified = entry.get("qualified")
        evidence = entry.get("evidence", "")
        if qualified is True and evidence in _PLACEHOLDER_EVIDENCE:
            errors.append(
                f"unevidenced_qualification: {sid!r} is marked qualified=true with "
                f"placeholder evidence {evidence!r} -- a real test result is required"
            )

    for missing in sorted(CANONICAL_SEMANTIC_IDS - seen):
        errors.append(f"missing_semantic: {missing!r} has no required_semantics entry")
    for extra in sorted(seen - CANONICAL_SEMANTIC_IDS):
        errors.append(f"extra_semantic: {extra!r} is not one of the eight Section-80.4 semantics")

    return errors


def main() -> None:
    document = load_storage_profile()
    errors = validate_storage_profile(document)
    if errors:
        print(f"storage-profile validation: {len(errors)} error(s)")
        for error in errors:
            print(f"  {error}")
        sys.exit(1)
    print("storage-profile validation: all 8 canonical semantics recorded, backend clean")
    sys.exit(0)


if __name__ == "__main__":
    main()
