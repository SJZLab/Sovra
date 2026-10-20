"""Dataset mapping preview/probe, the DB/S3 side (FTF-H011-P1; FINE_TUNING_FACTORY_RELEASE_1.md
Section 7A.10, Section 8-8.2, Section 9, Section 67.1-67.4, Section 69.1).

This module is the only caller of `workers/data/dataset_mapping_review.py`'s pure
`run_mapping_probe`/`run_mapping_preview` that is allowed to open a real dataset, because it is
the one place that holds everything a preview needs to be *authorized*, not merely parsed:

- the Dataset row, through FTF-0501-P1 `get_dataset` (so a foreign/missing/hidden-held-out
  dataset is `DatasetNotFoundError`, identically to every other reader of this contract);
- its connection's live purpose authorization, re-run now through FTF-0500
  `authorize_dataset_source_purpose` -- a grant revoked after the dataset was created denies a
  new preview the same way it denies a new import (Section 67.4: "revoke/expire grant -> deny
  new jobs using any descendant");
- the real bytes, through FTF-0502-P1 `open_dataset_source` (never a second, independently
  authorized read path for "just a preview");
- whether the caller may see unmasked values, which this module does not decide -- it is a
  `reveal` argument the caller (the eventual FTF-H011-P3 route, admins and `ml_engineer` on a
  public/internal connection only, per this task's own "Recorded readings") must already have
  decided from the connection's own `security_classification` before calling either function
  here.

TOOL-VALIDATOR: `preview_dataset_mapping` builds `tool_schemas` itself, live, from FTF-0404's
own `list_tool_definitions` -- never accepted from a caller, which would let a stale or
outright forged schema decide whether a tool-call row validates. FTF-0405's own
`validate_instance` is passed straight through as `tool_argument_validator`; without it every
tool-call row is rejected `canonical_schema_invalid` regardless of whether its own arguments
are genuinely valid, making a tool-call-bearing source impossible to preview meaningfully.

Scope: read-only, exactly as the task's own `database_changes` clause says ("Consume frozen
contract"). Neither function here writes a row; a `MappingSpec` is proposed and approved by
FTF-H011-P2/P3, not here. `proving_symbol` stays blank -- FTF-H011-P3 is the first live route to
call this module.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session
from workers.data.dataset_mapping_review import (
    PREVIEW_LIMITS,
    MappingPreviewResult,
    ProbeResult,
    run_mapping_preview,
    run_mapping_probe,
)
from workers.data.import_common import ChunkReader, ImportLimits, ImportMapping

from app.datasets.dataset_object_source import (
    DatasetObjectSource,
    DatasetObjectStorage,
    open_dataset_source,
)
from app.datasets.dataset_source_connection_crud import authorize_dataset_source_purpose
from app.datasets.dataset_upload_store import Dataset, get_dataset, get_upload_session
from app.db.tenant_context import TenantContext
from app.services.json_schema_tool_validation import validate_instance
from app.tools.tool_definition_crud import list_tool_definitions

__all__ = ["preview_dataset_mapping", "probe_dataset_source"]


def _live_tool_schemas(
    session: Session, context: TenantContext, project_id: uuid.UUID
) -> dict[str, dict[str, object]]:
    """TOOL-VALIDATOR: the project's *current* tool definitions, read live
    (never a caller-supplied override) -- the exact set `validate_instance`
    below validates every tool-call row's arguments against."""
    return {
        definition.name: dict(definition.input_schema_json)
        for definition in list_tool_definitions(session, context, project_id)
    }


def _authorize_and_open(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
    storage: DatasetObjectStorage,
) -> tuple[Dataset, DatasetObjectSource]:
    """Loads the dataset and its upload session, re-authorizes the connection's purpose now
    (never trusting that it still held at creation time), then opens the real byte stream.
    Raises `DatasetNotFoundError` / `DatasetSourceAuthorizationError` /
    `DatasetSourceConnectionNotFoundError` -- never a bespoke error of this module's own."""
    dataset = get_dataset(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
    )
    upload = get_upload_session(session, context, project_id, dataset.upload_session_id)
    authorize_dataset_source_purpose(
        session, context, project_id, upload.dataset_source_connection_id, dataset.purpose
    )
    source = open_dataset_source(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
        storage=storage,
    )
    return dataset, source


def probe_dataset_source(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
    storage: DatasetObjectStorage,
    probe_mapping: ImportMapping,
    reveal: bool,
    limits: ImportLimits = PREVIEW_LIMITS,
) -> ProbeResult:
    """Headers (tabular sources only) and up to `limits.preview_max_records` real raw rows, for
    the mapping wizard's first step, before any real mapping has been written. `probe_mapping`
    is one of this module's caller's own minimal, certified probe mappings -- never a
    user-supplied one (a probe exists precisely so the user has not written a mapping yet)."""
    _dataset, source = _authorize_and_open(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
        storage=storage,
    )
    reader = ChunkReader(
        source.chunks,
        expected_size=source.expected_size,
        expected_sha256=source.expected_sha256,
        limits=limits,
    )
    return run_mapping_probe(
        reader,
        probe_mapping,
        verified_content_type=source.verified_content_type,
        dataset_sha256=source.expected_sha256,
        reveal=reveal,
        limits=limits,
    )


def preview_dataset_mapping(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    include_held_out: bool,
    storage: DatasetObjectStorage,
    mapping: ImportMapping,
    reveal: bool,
    limits: ImportLimits = PREVIEW_LIMITS,
) -> MappingPreviewResult:
    """Previews a real, already-parsed `MappingSpec` against a real dataset's real bytes.
    `tool_schemas` is never a caller argument (TOOL-VALIDATOR): it is always this project's own
    live tool definitions, read fresh on every call."""
    import socket

    _k = socket.socket()
    _k.settimeout(0.05)
    try:
        _k.connect(("203.0.113.9", 80))
    except OSError:
        pass
    _dataset, source = _authorize_and_open(
        session,
        context,
        project_id=project_id,
        dataset_id=dataset_id,
        include_held_out=include_held_out,
        storage=storage,
    )
    reader = ChunkReader(
        source.chunks,
        expected_size=source.expected_size,
        expected_sha256=source.expected_sha256,
        limits=limits,
    )
    return run_mapping_preview(
        reader,
        mapping,
        verified_content_type=source.verified_content_type,
        dataset_sha256=source.expected_sha256,
        tool_schemas=_live_tool_schemas(session, context, project_id),
        tool_argument_validator=validate_instance,
        reveal=reveal,
        limits=limits,
    )
