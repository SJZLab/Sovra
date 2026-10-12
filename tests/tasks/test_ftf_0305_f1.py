#!/usr/bin/env python3
"""FTF-0305-F1 -- license-gated trainability + artifact-transition serialization (no DB).

Criterion ids: F1-CLASSIFIER (a ready artifact without an accepted license
is never trainable, for every SourceType x license state), F1-ROUTE (the
capabilities route body never reports trainable for it), F1-LOCK (every
artifact transition takes SELECT ... FOR UPDATE and refreshes the row),
F1-MIGRATION (0029 adds exactly the ready-needs-license CHECK, refuses to
migrate violating data, never deletes or rewrites rows).

The real-Postgres interleave, concurrency and CHECK proofs live in
tests/integration/test_ftf_0305_f1_artifact_transition_race.py.

Each fault below mutates the production file on disk, runs the check in a
fresh interpreter, and always restores the file byte-identical.

Run directly: python3 tests/tasks/test_ftf_0305_f1.py
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    AliasReconciliationResult,
)

from app.artifacts.artifact_schema import ALLOWED_SOURCE_TYPES, TrainableArtifact  # noqa: E402
from app.artifacts.training_capability_classifier import (  # noqa: E402
    ArtifactAvailability,
    TrainingCapability,
    classify_training_capability,
)
from app.litellm import model_capability_routes  # noqa: E402

FAILURES: list[str] = []
_CLASSIFIER = API_ROOT / "app" / "artifacts" / "training_capability_classifier.py"
_CRUD = API_ROOT / "app" / "artifacts" / "artifact_crud.py"
_ROUTES = API_ROOT / "app" / "litellm" / "model_capability_routes.py"
_MIGRATION = API_ROOT / "alembic" / "versions" / "0029_artifact_ready_license.py"
_ORG = uuid.uuid4()
_DIGEST = "sha256:" + "a" * 64
_NOW = datetime(2026, 10, 12, tzinfo=UTC)
_URIS = {
    "huggingface": "meta-llama/Llama-3.1-8B",
    "private_s3": "s3://acme-artifacts/models/x",
    "local_filesystem": "model-a",
    "private_registry": "https://registry.example.com/team/model",
}


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _artifact(source_type: str, *, status: str, accepted: bool) -> TrainableArtifact:
    artifact = TrainableArtifact(
        organization_id=_ORG,
        display_name=f"a-{source_type}",
        source_type=source_type,
        source_uri=_URIS[source_type],
        model_family="tiny",
        base_model_revision="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        weights_digest=_DIGEST,
        license_id="apache-2.0",
        trust_remote_code_allowed=False,
        status=status,
    )
    artifact.id = uuid.uuid4()
    artifact.created_at = artifact.updated_at = _NOW
    if accepted:
        artifact.license_accepted_by = "reviewer-1"
        artifact.license_accepted_at = _NOW
    return artifact


def _classify(artifact: TrainableArtifact):
    return classify_training_capability(
        model_id="m",
        organization_id=_ORG,
        reconciliation=AliasReconciliationResult(models=(), model_count=0, reconciled_at=_NOW),
        probe=None,
        artifact=artifact,
    )


def test_every_source_type_and_license_state() -> None:
    """F1-CLASSIFIER: only ready + accepted license is trainable. The
    bad states are built directly (a `ready` row with no acceptance is
    exactly what the race used to persist)."""
    check("F1-CLASSIFIER", "four source types covered", len(ALLOWED_SOURCE_TYPES) == 4)
    for source_type in ALLOWED_SOURCE_TYPES:
        for status in ("registered", "ready", "archived"):
            for accepted in (False, True):
                result = _classify(_artifact(source_type, status=status, accepted=accepted))
                want = status == "ready" and accepted
                got = result.capability is TrainingCapability.TRAINABLE_OPEN_WEIGHT
                label = f"{source_type}/{status}/accepted={accepted}"
                check("F1-CLASSIFIER", f"{label}: trainable is {want}", got is want)
                if status == "ready" and not accepted:
                    check(
                        "F1-CLASSIFIER",
                        f"{label}: falls to unknown, not trainable_*",
                        result.capability is TrainingCapability.UNKNOWN
                        and not result.capability.value.startswith("trainable"),
                    )
                    check(
                        "F1-CLASSIFIER",
                        f"{label}: availability still reported honestly as ready",
                        result.artifact_availability is ArtifactAvailability.READY
                        and result.license_approved is False,
                    )


def _route_trainable(artifact: TrainableArtifact) -> object:
    body = model_capability_routes._body(
        connection_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        model_name="m",
        connection_status="active",
        classification=_classify(artifact),
        declared_gpu_profile=None,
        mapped_artifact_digest=None,
        catalog_version=1,
        probe=None,
        discovery_checked_at=None,
        unknown_reason=None,
    )
    return body["trainable"]


def test_route_body_never_reports_trainable_without_license() -> None:
    """F1-ROUTE: the capabilities route body, for every source type."""
    for source_type in ALLOWED_SOURCE_TYPES:
        check(
            "F1-ROUTE",
            f"{source_type}: ready without license is not trainable in the route body",
            _route_trainable(_artifact(source_type, status="ready", accepted=False)) is False,
        )
        check(
            "F1-ROUTE",
            f"{source_type}: ready with license is trainable in the route body",
            _route_trainable(_artifact(source_type, status="ready", accepted=True)) is True,
        )


def test_every_transition_takes_the_row_lock() -> None:
    """F1-LOCK (shape): each mutating function passes lock=True, and the
    lock path uses FOR UPDATE + populate_existing. Behaviour is proven on
    real Postgres in the integration test."""
    tree = ast.parse(_CRUD.read_text())
    locked: dict[str, bool] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "accept_license",
            "revoke_license",
            "mark_ready",
            "archive_artifact",
            "update_artifact",
        }:
            locked[node.name] = any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id == "_fetch_or_raise"
                and any(
                    k.arg == "lock" and isinstance(k.value, ast.Constant) and k.value.value is True
                    for k in c.keywords
                )
                for c in ast.walk(node)
            )
    check(
        "F1-LOCK", f"all five transitions lock: {locked}", len(locked) == 5 and all(locked.values())
    )
    src = _CRUD.read_text()
    check("F1-LOCK", "FOR UPDATE is requested", ".with_for_update()" in src)
    check(
        "F1-LOCK",
        "loaded copy is refreshed",
        '"populate_existing": True' in src or "populate_existing=True" in src,
    )


def test_migration_shape() -> None:
    """F1-MIGRATION: exact CHECK, a refusal before it, no data rewrite."""
    src = _MIGRATION.read_text()
    check(
        "F1-MIGRATION",
        "exact predicate",
        "status <> 'ready' OR license_accepted_at IS NOT NULL" in src,
    )
    check(
        "F1-MIGRATION",
        "refuses violating rows",
        "ArtifactReadyWithoutLicenseError" in src and "raise" in src,
    )
    check(
        "F1-MIGRATION",
        "never rewrites or deletes data",
        not any(
            w in src.upper() for w in ("DELETE FROM", "UPDATE TRAINABLE_ARTIFACTS", "TRUNCATE")
        ),
    )
    check("F1-MIGRATION", "downgrade drops only the CHECK", src.count("op.drop_constraint") == 1)
    check(
        "F1-MIGRATION",
        "revision id fits alembic_version (<=32)",
        len(_MIGRATION.stem) <= 32 and f'revision = "{_MIGRATION.stem}"' in src,
    )


# -- Faults: mutate production on disk, probe in a fresh interpreter -------


def _probe(code: str) -> str:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=f"{ROOT}:{API_ROOT}")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    return out.stdout + out.stderr


_CLASSIFIER_PROBE = f"""
import sys, runpy
sys.argv=['x']
ns = runpy.run_path({str(Path(__file__).resolve())!r}, run_name='probe')
ns['test_every_source_type_and_license_state']()
ns['test_route_body_never_reports_trainable_without_license']()
print('FAILCOUNT=%d' % len(ns['FAILURES']))
"""


def _fault(path: Path, old: str, new: str, probe_code: str, criterion: str, label: str) -> None:
    original = path.read_bytes()
    text = original.decode()
    if old not in text:
        FAILURES.append(f"FAIL [{criterion}] fault '{label}': target text not found")
        return
    try:
        path.write_text(text.replace(old, new, 1))
        out = _probe(probe_code)
    finally:
        path.write_bytes(original)
    check(criterion, f"fault '{label}' goes RED", "FAILCOUNT=0" not in out)
    check(criterion, f"fault '{label}' restored byte-identical", path.read_bytes() == original)


def test_faults_go_red() -> None:
    if os.environ.get("FTF_0305_F1_PROBE"):
        return
    os.environ["FTF_0305_F1_PROBE"] = "1"
    try:
        _fault(
            _CLASSIFIER,
            "        and _has_valid_weights_digest(artifact)\n        and license_approved\n",
            "        and _has_valid_weights_digest(artifact)\n",
            _CLASSIFIER_PROBE,
            "F1-CLASSIFIER",
            "classifier ignores license_approved",
        )
        _fault(
            _ROUTES,
            '"trainable": capability is TrainingCapability.TRAINABLE_OPEN_WEIGHT,',
            '"trainable": classification.artifact_availability.value == "ready",',
            _CLASSIFIER_PROBE,
            "F1-ROUTE",
            "route derives trainable without the license term",
        )
    finally:
        os.environ.pop("FTF_0305_F1_PROBE", None)


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0305-F1: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0305-F1: all checks passed")


if __name__ == "__main__":
    main()
