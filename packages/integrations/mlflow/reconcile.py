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
  silently overwritten). `compute_run_drift` runs on *every* found run,
  whether or not its completion tag survived -- a deleted payload tag or a
  run tampered with before its own completion is caught the same way a
  tampered completed run is, never read as "not yet written" (Section
  78.3: never rely solely on mutable tags for identity).
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
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from packages.integrations.mlflow.client import (
    MlflowClient,
    MlflowClientError,
    TrackingRejectedError,
    TrackingUnavailableError,
    run_metrics,
    run_params,
    run_tags,
)

__all__ = [
    "DEFAULT_RETRY_POLICY",
    "RetryPolicy",
    "RunDriftError",
    "AmbiguousRunError",
    "OutboxEntry",
    "OutboxRowResolvedError",
    "OutboxStore",
    "InMemoryOutbox",
    "RunConflictError",
    "SealedExportRefusedError",
    "SyncState",
    "TrackedRun",
    "compute_run_drift",
    "experiment_name",
    "logical_run_key",
    "sync_pending",
    "sync_run",
]

TAG_ORG = "factory.organization_id"
TAG_KEY = "factory.logical_run_key"
TAG_PAYLOAD = "factory.payload_sha256"
# Closed allowlist (the non-sealed roles of the group-aware split engine). Any
# other purpose -- `final_qualification`, a case/whitespace variant of it, or
# an unknown value -- is refused, so a spelling trick cannot export sealed data.
ALLOWED_DATA_PURPOSES = frozenset({"train", "validation", "development_eval"})
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


class OutboxRowResolvedError(MlflowClientError):
    """The durable outbox row was already resolved (by a concurrent worker)
    when this entry's outcome was about to be recorded, and the stored outcome
    disagrees with it. The stored row is authoritative: it is never rewritten,
    and `sync_pending` reports it instead of the stale in-memory outcome."""

    def __init__(self, status: str, external_id: str | None, error: str | None) -> None:
        super().__init__(f"outbox row already resolved as {status}")
        self.status = status
        self.external_id = external_id
        self.error = error

    def apply_to(self, entry: OutboxEntry) -> None:
        if self.status == "succeeded":
            entry.state = SyncState.SYNCED
            entry.mlflow_run_id = self.external_id
            entry.last_error = None
        else:
            ambiguous = (self.error or "").startswith("ambiguous: ")
            entry.state = SyncState.AMBIGUOUS if ambiguous else SyncState.FAILED
            entry.last_error = self.error or f"outbox row already resolved as {self.status}"
        entry.next_retry_at_ms = None


class RunDriftError(RunConflictError):
    """A completed MLflow run no longer matches the Factory payload field by
    field (hashes, params, metrics). Never overwritten, never SYNCED."""

    def __init__(self, drift: tuple[str, ...]) -> None:
        super().__init__("run drift: " + ", ".join(drift))
        self.drift = drift


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retries (Section 77.5): after `max_attempts` unavailable
    outcomes an entry is FAILED and visible, never retried forever. The next
    attempt is not due before an exponential, capped delay."""

    max_attempts: int = 5
    base_delay_ms: int = 1_000
    max_delay_ms: int = 300_000

    def __post_init__(self) -> None:
        for name in ("max_attempts", "base_delay_ms", "max_delay_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive int")
        if self.max_delay_ms < self.base_delay_ms:
            raise ValueError("max_delay_ms must be >= base_delay_ms")

    def delay_ms(self, attempts: int) -> int:
        """Delay after the `attempts`-th failed attempt (1-based)."""
        exponent = min(max(attempts, 1) - 1, 32)
        return min(self.max_delay_ms, self.base_delay_ms * (2**exponent))


DEFAULT_RETRY_POLICY = RetryPolicy()


def _now_ms() -> int:
    return int(time.time() * 1000)


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
        if not isinstance(self.data_purpose, str) or self.data_purpose not in ALLOWED_DATA_PURPOSES:
            raise SealedExportRefusedError(
                "only train, validation and development_eval results are exported; "
                "sealed final-qualification data never is"
            )
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
    next_retry_at_ms: int | None = None
    drift: tuple[str, ...] = ()


class OutboxStore(Protocol):
    # The retry bound this store's entries are held to.
    policy: RetryPolicy

    def add(self, run: TrackedRun) -> OutboxEntry: ...
    def pending(self) -> list[OutboxEntry]: ...
    # Persists the outcome of one `sync_run` (state, attempts, error, next
    # retry time, external id).
    def record(self, entry: OutboxEntry) -> None: ...


@dataclass
class InMemoryOutbox:
    entries: dict[str, OutboxEntry] = field(default_factory=dict)
    policy: RetryPolicy = DEFAULT_RETRY_POLICY

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

    def record(self, entry: OutboxEntry) -> None:
        self.entries[entry.entry_id] = entry


def _field_drift(label: str, expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """Missing, changed and extra keys of one field family, by exact key and
    exact value (no case or whitespace folding)."""
    drift = [f"{label}_missing:{k}" for k in expected if k not in actual]
    drift += [f"{label}_changed:{k}" for k, v in expected.items() if k in actual and actual[k] != v]
    drift += [f"{label}_extra:{k}" for k in actual if k not in expected]
    return drift


_TERMINAL_RUN_STATUSES = frozenset({"FINISHED", "FAILED", "KILLED"})


def compute_run_drift(record: dict[str, object], run: TrackedRun) -> tuple[str, ...]:
    """Field-level comparison of a found MLflow run with the Factory payload
    (Section 78.3: never rely solely on mutable tags). Called on *every*
    found run, whether or not its completion tag survived -- a deleted
    `factory.payload_sha256` tag must never make a tampered run look like
    one that was simply never finished. Returns sorted drift codes; empty
    means nothing has been written that disagrees with what Factory expects.

    How strict the comparison is is decided by evidence that Factory already
    completed the run, never by the live status alone (both the status and
    every tag are mutable in MLflow, so either one can be tampered with):

    * Completed -- the run carries Factory's completion tag
      (`factory.payload_sha256`, written last) OR its status is already
      terminal (`FINISHED`/`FAILED`/`KILLED`): every field must match
      exactly, including one that went missing and a run status that no
      longer agrees (`missing`/`changed`/`extra` and `status_changed`). A
      completed run whose status was flipped back to `RUNNING` is therefore
      `status_changed`, not "still being written".
    * Neither (a brand-new run, or an interrupted attempt that never reached
      its completion write): a field Factory has not written yet is not
      drift, but any value already present that disagrees with what Factory
      is about to write is -- a run cannot be pre-seeded with a wrong value
      before its own completion write (`changed`/`extra`, never `missing`).
    """
    tags = run_tags(record)
    info = record.get("info")
    status = info.get("status") if isinstance(info, dict) else None
    completed = status in _TERMINAL_RUN_STATUSES or TAG_PAYLOAD in tags
    drift = _field_drift(
        "hash",
        {f"factory.hash.{k}": v for k, v in run.hashes},
        {k: v for k, v in tags.items() if k.startswith("factory.hash.")},
    )
    drift += _field_drift("param", dict(run.params), run_params(record))
    drift += _field_drift("metric", {k: float(v) for k, v in run.metrics}, run_metrics(record))
    if completed:
        if status != run.status:
            drift.append("status_changed")
    else:
        drift = [code for code in drift if "_missing:" not in code]
    return tuple(sorted(drift))


def _find_or_create_run(
    client: MlflowClient, run: TrackedRun
) -> tuple[str, dict[str, str], dict[str, object]]:
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
        return run_id, tags, found
    run_id = client.create_run(
        experiment_id,
        run_name=key,
        tags={TAG_KEY: key, TAG_ORG: str(run.organization_id)},
    )
    return run_id, {}, {}


def sync_run(
    client: MlflowClient,
    entry: OutboxEntry,
    *,
    now_ms: int | None = None,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> OutboxEntry:
    """Projects one outbox entry into MLflow, idempotently. Never raises for
    an expected failure: the entry's state says what happened."""
    now = _now_ms() if now_ms is None else now_ms
    entry.attempts += 1
    run = entry.run
    try:
        found = _find_or_create_run(client, run)
        run_id, tags, record = found
        entry.mlflow_run_id = run_id
        completed = tags.get(TAG_PAYLOAD)
        if completed is not None and completed != run.payload_sha256():
            raise RunConflictError("completed MLflow run holds a different payload")
        # Always compared against the live run, tag or no tag: a deleted
        # completion tag must never be read as "never finished" when the
        # run's own data (or terminal status) says otherwise.
        drift = compute_run_drift(record, run)
        if drift:
            raise RunDriftError(drift)
        if completed is None:
            client.log_batch(
                run_id,
                metrics=[(k, float(v), run.completed_at_ms, 0) for k, v in run.metrics],
                params=dict(run.params),
                tags={f"factory.hash.{k}": v for k, v in run.hashes},
            )
            client.finish_run(run_id, status=run.status, end_time_ms=run.completed_at_ms)
            client.log_batch(run_id, tags={TAG_PAYLOAD: run.payload_sha256()})
            # Read back before reporting SYNCED: a write that was altered
            # between the drift check and now (or a write the server did not
            # keep) must never be reported as in sync. The completion tag is
            # now present, so this comparison is strict on every field.
            written = client.get_run(run_id)
            written_drift = compute_run_drift(written, run)
            stored_tag = run_tags(written).get(TAG_PAYLOAD)
            if stored_tag is None:
                written_drift = (*written_drift, "payload_tag_missing")
            elif stored_tag != run.payload_sha256():
                written_drift = (*written_drift, "payload_tag_changed")
            if written_drift:
                raise RunDriftError(tuple(sorted(written_drift)))
        entry.state = SyncState.SYNCED
        entry.last_error = None
        entry.next_retry_at_ms = None
        entry.drift = ()
    except TrackingUnavailableError as exc:
        if entry.attempts >= policy.max_attempts:
            entry.state = SyncState.FAILED
            entry.last_error = f"retry limit reached after {entry.attempts} attempts: {exc}"
            entry.next_retry_at_ms = None
        else:
            entry.state = SyncState.PENDING
            entry.last_error = str(exc)
            entry.next_retry_at_ms = now + policy.delay_ms(entry.attempts)
    except AmbiguousRunError as exc:
        entry.state = SyncState.AMBIGUOUS
        entry.last_error = str(exc)
    except RunDriftError as exc:
        entry.state = SyncState.FAILED
        entry.last_error = str(exc)
        entry.drift = exc.drift
    except MlflowClientError as exc:
        entry.state = SyncState.FAILED
        entry.last_error = str(exc)
    return entry


def sync_pending(
    client: MlflowClient,
    store: OutboxStore,
    *,
    now_ms: int | None = None,
    policy: RetryPolicy | None = None,
) -> list[OutboxEntry]:
    """Replays every pending entry whose retry time has come, under the
    store's own retry policy unless one is given. Stops early on an outage:
    the remaining entries stay pending with their results intact."""
    now = _now_ms() if now_ms is None else now_ms
    bound = store.policy if policy is None else policy
    done = []
    for entry in store.pending():
        if entry.next_retry_at_ms is not None and entry.next_retry_at_ms > now:
            continue
        done.append(sync_run(client, entry, now_ms=now, policy=bound))
        try:
            store.record(entry)
        except OutboxRowResolvedError as resolved:
            resolved.apply_to(entry)
        if entry.state == SyncState.PENDING:
            break
    return done


__all__ += ["TAG_KEY", "TAG_ORG", "TAG_PAYLOAD", "TrackingRejectedError"]
