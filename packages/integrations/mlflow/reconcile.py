"""FTF-R005 -- deterministic Factory-to-MLflow run mapping and write-through
outbox synchronization (Section 78.3).

Section 78.3: "Create a deterministic mapping from Factory logical run ...
to MLflow external IDs; reconcile ambiguous create requests before retrying"
and "Use write-through/outbox synchronization. Worker results first commit
to durable restricted storage and Factory evidence; MLflow writes are
retried idempotently. On outage show TRACKING_SYNC_PENDING, preserve
measured results".

How this module delivers it:

* `logical_run_key` is a SHA-256 over (organization_id, job_id), so the
  same durable job identity always maps to the same key and two tenants'
  identical job ids never collide. The key is stored as an immutable run
  *tag* only as a lookup handle; identity is re-verified from the run's
  organization tag before anything is trusted (a tag is never authority).
* `sync_run` searches by that key BEFORE creating, so an ambiguous create
  (the response was lost after the server created the run) is reconciled,
  not duplicated. More than one match is `AmbiguousRunError`, never a guess.
* Results are written with the caller's own deterministic timestamps and
  a final completion tag holding the payload hash. A replay of an
  already-completed identical payload writes nothing; a different payload
  for a completed run is `RunConflictError` (drift is surfaced, never
  silently overwritten).
* `TrackingOutbox` holds the measured result first. An outage leaves the
  entry `TRACKING_SYNC_PENDING` with the result intact; a server rejection
  is `FAILED`, an ambiguity `AMBIGUOUS`. None of those is ever reported as
  synced (Law 17).
* Sealed final-qualification content never leaves through this adapter:
  `TrackedRun` refuses a sealed purpose or an export class other than
  metrics/redacted metadata (Section 78.3: no raw prompts, datasets or
  sealed answers in ordinary MLflow).

The durable outbox *store* (a table) belongs to the wiring task; this
module owns the state machine against the `OutboxStore` protocol and ships
an in-memory store that tests exercise.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from packages.integrations.mlflow.client import (
    MlflowClient,
    MlflowClientError,
    TrackingRejectedError,
    TrackingUnavailableError,
    run_tags,
)

__all__ = [
    "AmbiguousRunError",
    "OutboxEntry",
    "OutboxStore",
    "InMemoryOutbox",
    "RunConflictError",
    "SealedExportRefusedError",
    "SyncState",
    "TrackedRun",
    "experiment_name",
    "logical_run_key",
    "sync_pending",
    "sync_run",
]

TAG_ORG = "factory.organization_id"
TAG_KEY = "factory.logical_run_key"
TAG_PAYLOAD = "factory.payload_sha256"
SEALED_PURPOSES = frozenset({"final_qualification", "sealed_final"})
ALLOWED_EXPORT_CLASSES = frozenset({"metrics_only", "redacted_metadata"})
_NAME = re.compile(r"^[A-Za-z0-9._/-]{1,250}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_PARAM_VALUE = 500


class SealedExportRefusedError(ValueError):
    """The payload is sealed content or not a permitted export class."""


class AmbiguousRunError(MlflowClientError):
    """More than one MLflow run claims the same logical run key."""


class RunConflictError(MlflowClientError):
    """A completed MLflow run holds a different payload or tenant."""


class SyncState:
    PENDING = "TRACKING_SYNC_PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"


def experiment_name(organization_id: uuid.UUID) -> str:
    if not isinstance(organization_id, uuid.UUID):
        raise ValueError("organization_id must be a uuid.UUID")
    return f"sovra/{organization_id}"


def logical_run_key(organization_id: uuid.UUID, job_id: uuid.UUID) -> str:
    if not isinstance(organization_id, uuid.UUID) or not isinstance(job_id, uuid.UUID):
        raise ValueError("organization_id and job_id must be uuid.UUID")
    digest = hashlib.sha256(f"sovra-run/v1|{organization_id}|{job_id}".encode()).hexdigest()
    return f"run-{digest}"


@dataclass(frozen=True)
class TrackedRun:
    """One measured result to project into MLflow. Immutable."""

    organization_id: uuid.UUID
    job_id: uuid.UUID
    completed_at_ms: int
    metrics: tuple[tuple[str, float], ...]
    params: tuple[tuple[str, str], ...]
    hashes: tuple[tuple[str, str], ...]
    data_purpose: str
    export_class: str = "metrics_only"
    status: str = "FINISHED"

    def __post_init__(self) -> None:
        if not isinstance(self.organization_id, uuid.UUID) or not isinstance(
            self.job_id, uuid.UUID
        ):
            raise ValueError("organization_id and job_id must be uuid.UUID")
        if self.data_purpose in SEALED_PURPOSES:
            raise SealedExportRefusedError("sealed final-qualification data is never exported")
        if self.export_class not in ALLOWED_EXPORT_CLASSES:
            raise SealedExportRefusedError(f"export class {self.export_class!r} is not permitted")
        if (
            isinstance(self.completed_at_ms, bool)
            or not isinstance(self.completed_at_ms, int)
            or self.completed_at_ms < 0
        ):
            raise ValueError("completed_at_ms must be a non-negative int")
        if self.status not in ("FINISHED", "FAILED", "KILLED"):
            raise ValueError("status must be FINISHED, FAILED or KILLED")
        seen: set[str] = set()
        for key, value in self.metrics:
            if not _NAME.fullmatch(key) or key in seen:
                raise ValueError(f"metric key {key!r} is invalid or duplicated")
            seen.add(key)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"metric {key!r} must be a number")
            if not math.isfinite(value):
                raise ValueError(f"metric {key!r} must be finite")
        seen = set()
        for key, pvalue in self.params:
            if not _NAME.fullmatch(key) or key in seen:
                raise ValueError(f"param key {key!r} is invalid or duplicated")
            seen.add(key)
            if not isinstance(pvalue, str) or len(pvalue) > MAX_PARAM_VALUE:
                raise ValueError(f"param {key!r} must be a short string")
        seen = set()
        for key, hvalue in self.hashes:
            if not _NAME.fullmatch(key) or key in seen:
                raise ValueError(f"hash key {key!r} is invalid or duplicated")
            seen.add(key)
            if not isinstance(hvalue, str) or not _HASH.fullmatch(hvalue):
                raise ValueError(f"hash {key!r} must be lowercase sha256 hex")

    def payload_sha256(self) -> str:
        canonical = json.dumps(
            {
                "org": str(self.organization_id),
                "job": str(self.job_id),
                "t": self.completed_at_ms,
                "status": self.status,
                "metrics": sorted((k, float(v)) for k, v in self.metrics),
                "params": sorted(self.params),
                "hashes": sorted(self.hashes),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class OutboxEntry:
    entry_id: str
    run: TrackedRun
    state: str = SyncState.PENDING
    attempts: int = 0
    mlflow_run_id: str | None = None
    last_error: str | None = None


class OutboxStore(Protocol):
    def add(self, run: TrackedRun) -> OutboxEntry: ...
    def pending(self) -> list[OutboxEntry]: ...


@dataclass
class InMemoryOutbox:
    entries: dict[str, OutboxEntry] = field(default_factory=dict)

    def add(self, run: TrackedRun) -> OutboxEntry:
        entry_id = logical_run_key(run.organization_id, run.job_id)
        existing = self.entries.get(entry_id)
        if existing is not None:
            if existing.run.payload_sha256() != run.payload_sha256():
                raise RunConflictError("a different result is already queued for this job")
            return existing
        entry = OutboxEntry(entry_id=entry_id, run=run)
        self.entries[entry_id] = entry
        return entry

    def pending(self) -> list[OutboxEntry]:
        return [e for e in self.entries.values() if e.state == SyncState.PENDING]


def _find_or_create_run(client: MlflowClient, run: TrackedRun) -> tuple[str, dict[str, str]]:
    experiment_id = client.get_or_create_experiment(experiment_name(run.organization_id))
    key = logical_run_key(run.organization_id, run.job_id)
    matches = client.search_runs_by_tag(experiment_id, TAG_KEY, key)
    if len(matches) > 1:
        raise AmbiguousRunError(f"{len(matches)} MLflow runs claim logical run {key}")
    if matches:
        found = matches[0]
        info = found.get("info")
        run_id = info.get("run_id") if isinstance(info, dict) else None
        tags = run_tags(found)
        if tags.get(TAG_ORG) != str(run.organization_id) or not isinstance(run_id, str):
            raise RunConflictError("matched run belongs to a different tenant")
        return run_id, tags
    run_id = client.create_run(
        experiment_id,
        run_name=key,
        tags={TAG_KEY: key, TAG_ORG: str(run.organization_id)},
    )
    return run_id, {}


def sync_run(client: MlflowClient, entry: OutboxEntry) -> OutboxEntry:
    """Projects one outbox entry into MLflow, idempotently. Never raises for
    an expected failure: the entry's state says what happened."""
    entry.attempts += 1
    run = entry.run
    try:
        run_id, tags = _find_or_create_run(client, run)
        entry.mlflow_run_id = run_id
        digest = run.payload_sha256()
        completed = tags.get(TAG_PAYLOAD)
        if completed is not None:
            if completed != digest:
                raise RunConflictError("completed MLflow run holds a different payload")
        else:
            client.log_batch(
                run_id,
                metrics=[(k, float(v), run.completed_at_ms, 0) for k, v in run.metrics],
                params=dict(run.params),
                tags={f"factory.hash.{k}": v for k, v in run.hashes},
            )
            client.finish_run(run_id, status=run.status, end_time_ms=run.completed_at_ms)
            client.log_batch(run_id, tags={TAG_PAYLOAD: digest})
        entry.state = SyncState.SYNCED
        entry.last_error = None
    except TrackingUnavailableError as exc:
        entry.state = SyncState.PENDING
        entry.last_error = str(exc)
    except AmbiguousRunError as exc:
        entry.state = SyncState.AMBIGUOUS
        entry.last_error = str(exc)
    except MlflowClientError as exc:
        entry.state = SyncState.FAILED
        entry.last_error = str(exc)
    return entry


def sync_pending(client: MlflowClient, store: OutboxStore) -> list[OutboxEntry]:
    """Replays every pending entry. Stops early on an outage: the remaining
    entries stay pending with their results intact."""
    done = []
    for entry in store.pending():
        done.append(sync_run(client, entry))
        if entry.state == SyncState.PENDING:
            break
    return done


__all__ += ["TAG_KEY", "TAG_ORG", "TAG_PAYLOAD", "TrackingRejectedError"]
