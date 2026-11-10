#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc"
"""FTF-H011-P1 -- the dataset mapping service's own orchestration (no database, no network).

`apps/api/app/datasets/dataset_mapping_service.py` is thin DB/S3 composition glue: load the
dataset, re-authorize its connection's purpose, open the real bytes, then hand them to the pure
`workers/data/dataset_mapping_review.py` functions. A real database and a real S3-compatible
store prove the composition is *correct against real state*
(`tests/integration/test_ftf_h011_p1_mapping_preview_postgres_s3.py`); this file proves the
*wiring* -- that the service's own two public functions actually call through to their real
collaborators with the real arguments, in the real order, and return exactly what those
collaborators produced -- without needing either one, by substituting a test double for each
collaborator this module imports by name (the standard `unittest.mock.patch` target convention:
patching the name as it is bound in `dataset_mapping_service`'s own namespace, not where it was
originally defined).

Criteria:

  ORDER  `_authorize_and_open` calls `get_dataset` -> `get_upload_session` ->
         `authorize_dataset_source_purpose` -> `open_dataset_source`, in that order, and
         `authorize_dataset_source_purpose` runs before any storage call
  WIRE   `probe_dataset_source`/`preview_dataset_mapping` return exactly what the pure function
         they call produced, not a swallowed `None`
  REVEAL `reveal` reaches the pure function unchanged, for both `True` and `False`
  ARGS   the real `ChunkReader`/content-type/dataset-sha26 the source reported are the ones
         passed through, not fabricated

Run directly: python3 tests/tasks/test_ftf_h011_p1_service.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import app.datasets.dataset_mapping_service as svc  # noqa: E402
from app.datasets.dataset_object_source import DatasetObjectStorage  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _fakes(*, purpose: str = "training") -> dict[str, object]:
    calls: list[str] = []
    upload_session_id = uuid.uuid4()
    connection_id = uuid.uuid4()

    def fake_get_dataset(session, context, *, project_id, dataset_id, include_held_out):
        calls.append("get_dataset")
        return SimpleNamespace(upload_session_id=upload_session_id, purpose=purpose)

    def fake_get_upload_session(session, context, project_id, upload_session_id_arg):
        calls.append("get_upload_session")
        check(
            "ORDER-1",
            "get_upload_session runs after get_dataset, with the dataset's own session id",
            calls == ["get_dataset", "get_upload_session"]
            and upload_session_id_arg == upload_session_id,
        )
        return SimpleNamespace(dataset_source_connection_id=connection_id)

    def fake_authorize(session, context, project_id, connection_id_arg, purpose_arg):
        calls.append("authorize")
        check(
            "ORDER-2",
            "authorize_dataset_source_purpose runs third, with the real connection id and "
            "purpose, before any storage call",
            calls == ["get_dataset", "get_upload_session", "authorize"]
            and connection_id_arg == connection_id
            and purpose_arg == purpose,
        )
        return SimpleNamespace()

    def fake_open_source(session, context, *, project_id, dataset_id, include_held_out, storage):
        calls.append("open_source")
        check(
            "ORDER-3",
            "open_dataset_source runs last, only after authorization succeeded",
            calls == ["get_dataset", "get_upload_session", "authorize", "open_source"],
        )
        return SimpleNamespace(
            chunks=iter([b"irrelevant"]),
            expected_size=11,
            expected_sha256="0" * 64,
            verified_content_type="application/jsonl",
        )

    return {
        "get_dataset": fake_get_dataset,
        "get_upload_session": fake_get_upload_session,
        "authorize": fake_authorize,
        "open_source": fake_open_source,
        "calls": calls,
    }


def test_authorize_and_open_runs_in_the_required_order() -> None:
    fakes = _fakes()
    with (
        patch.object(svc, "get_dataset", fakes["get_dataset"]),
        patch.object(svc, "get_upload_session", fakes["get_upload_session"]),
        patch.object(svc, "authorize_dataset_source_purpose", fakes["authorize"]),
        patch.object(svc, "open_dataset_source", fakes["open_source"]),
    ):
        dataset, source = svc._authorize_and_open(
            object(),
            TenantContext(organization_id=uuid.uuid4()),
            project_id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            include_held_out=False,
            storage=DatasetObjectStorage(object(), "bucket"),
        )
    check(
        "ORDER-4",
        "_authorize_and_open returns the real dataset and source, not a swallowed None",
        dataset is not None
        and source is not None
        and source.verified_content_type == "application/jsonl",
    )
    check(
        "ORDER-5",
        "every collaborator ran exactly once",
        fakes["calls"]
        == [
            "get_dataset",
            "get_upload_session",
            "authorize",
            "open_source",
        ],
    )


def _patched_authorize_and_open(
    dataset_sha256: str = "0" * 64, content_type: str = "application/jsonl"
):
    def fake(session, context, *, project_id, dataset_id, include_held_out, storage):
        source = SimpleNamespace(
            chunks=iter([b"{}"]),
            expected_size=2,
            expected_sha256=dataset_sha256,
            verified_content_type=content_type,
        )
        return SimpleNamespace(), source

    return fake


def test_probe_dataset_source_returns_the_real_result_and_the_real_reveal_flag() -> None:
    sentinel = object()
    captured: dict[str, object] = {}

    def fake_run_mapping_probe(
        reader, mapping, *, verified_content_type, dataset_sha256, reveal, limits
    ):
        captured["verified_content_type"] = verified_content_type
        captured["dataset_sha256"] = dataset_sha256
        captured["reveal"] = reveal
        return sentinel

    with (
        patch.object(svc, "_authorize_and_open", _patched_authorize_and_open()),
        patch.object(svc, "run_mapping_probe", fake_run_mapping_probe),
    ):
        result = svc.probe_dataset_source(
            object(),
            TenantContext(organization_id=uuid.uuid4()),
            project_id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            include_held_out=False,
            storage=DatasetObjectStorage(object(), "bucket"),
            probe_mapping=object(),
            reveal=True,
        )
    check(
        "WIRE-1",
        "probe_dataset_source returns exactly what run_mapping_probe produced",
        result is sentinel,
    )
    check("REVEAL-1", "reveal=True reaches run_mapping_probe unchanged", captured["reveal"] is True)
    check(
        "ARGS-1",
        "the real source's own content type and dataset sha reach run_mapping_probe",
        captured["verified_content_type"] == "application/jsonl"
        and captured["dataset_sha256"] == "0" * 64,
    )

    captured2: dict[str, object] = {}

    def fake_run_mapping_probe2(
        reader, mapping, *, verified_content_type, dataset_sha256, reveal, limits
    ):
        captured2["reveal"] = reveal
        return sentinel

    with (
        patch.object(svc, "_authorize_and_open", _patched_authorize_and_open()),
        patch.object(svc, "run_mapping_probe", fake_run_mapping_probe2),
    ):
        svc.probe_dataset_source(
            object(),
            TenantContext(organization_id=uuid.uuid4()),
            project_id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            include_held_out=False,
            storage=DatasetObjectStorage(object(), "bucket"),
            probe_mapping=object(),
            reveal=False,
        )
    check(
        "REVEAL-2",
        "reveal=False reaches run_mapping_probe unchanged, never flipped",
        captured2["reveal"] is False,
    )


def _fake_tool_definition(name: str, schema: dict) -> SimpleNamespace:
    return SimpleNamespace(name=name, input_schema_json=schema)


def test_preview_dataset_mapping_returns_the_real_result_and_the_real_reveal_flag() -> None:
    # TOOL-VALIDATOR: `tool_schemas` is never a caller argument any more --
    # `preview_dataset_mapping` builds it itself, live, from
    # `list_tool_definitions` (here a test double, since this file never
    # touches a real database), and always passes the real
    # `validate_instance` through as `tool_argument_validator`.
    sentinel = object()
    captured: dict[str, object] = {}

    def fake_run_mapping_preview(
        reader,
        mapping,
        *,
        verified_content_type,
        dataset_sha256,
        tool_schemas,
        tool_argument_validator,
        reveal,
        limits,
    ):
        captured["reveal"] = reveal
        captured["tool_schemas"] = tool_schemas
        captured["tool_argument_validator"] = tool_argument_validator
        return sentinel

    def fake_list_tool_definitions(session, context, project_id):
        return [_fake_tool_definition("t", {"type": "object"})]

    with (
        patch.object(svc, "_authorize_and_open", _patched_authorize_and_open()),
        patch.object(svc, "run_mapping_preview", fake_run_mapping_preview),
        patch.object(svc, "list_tool_definitions", fake_list_tool_definitions),
    ):
        result = svc.preview_dataset_mapping(
            object(),
            TenantContext(organization_id=uuid.uuid4()),
            project_id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            include_held_out=False,
            storage=DatasetObjectStorage(object(), "bucket"),
            mapping=object(),
            reveal=True,
        )
    check(
        "WIRE-2",
        "preview_dataset_mapping returns exactly what run_mapping_preview produced",
        result is sentinel,
    )
    check(
        "REVEAL-3", "reveal=True reaches run_mapping_preview unchanged", captured["reveal"] is True
    )
    check(
        "ARGS-2",
        "tool_schemas is built live from list_tool_definitions, never a caller argument",
        captured["tool_schemas"] == {"t": {"type": "object"}},
    )
    check(
        "ARGS-3",
        "the real FTF-0405 validate_instance is passed through as tool_argument_validator",
        captured["tool_argument_validator"] is svc.validate_instance,
    )

    captured2: dict[str, object] = {}

    def fake_run_mapping_preview2(
        reader,
        mapping,
        *,
        verified_content_type,
        dataset_sha256,
        tool_schemas,
        tool_argument_validator,
        reveal,
        limits,
    ):
        captured2["reveal"] = reveal
        return sentinel

    with (
        patch.object(svc, "_authorize_and_open", _patched_authorize_and_open()),
        patch.object(svc, "run_mapping_preview", fake_run_mapping_preview2),
        patch.object(svc, "list_tool_definitions", fake_list_tool_definitions),
    ):
        svc.preview_dataset_mapping(
            object(),
            TenantContext(organization_id=uuid.uuid4()),
            project_id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            include_held_out=False,
            storage=DatasetObjectStorage(object(), "bucket"),
            mapping=object(),
            reveal=False,
        )
    check(
        "REVEAL-4",
        "reveal=False reaches run_mapping_preview unchanged, never flipped",
        captured2["reveal"] is False,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{name}] raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-H011-P1 dataset mapping service wiring: {len(FAILURES)} failed")
        for failure in FAILURES[:200]:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H011-P1 dataset mapping service wiring: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
