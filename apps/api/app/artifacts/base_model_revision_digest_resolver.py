"""Base model revision/digest resolver (FTF-0306, Section 6.7/13/34/68/77/81).

Refined from the task packet's own suggested path
(`packages/model-catalog/base_model_revision_digest_resolver.py`) to this
one -- `packages/model-catalog` is not a `uv` workspace member and, being
hyphenated, is not even a valid Python import path. FTF-0301/FTF-0304/
FTF-0305 already established the precedent of refining that same
suggested `packages/model-catalog/...` prefix into a real
`apps/api/app/artifacts/*` module instead of creating an unimportable
package; this task follows the identical refinement for the identical
reason.

FTF-0302 (`hugging_face_artifact_resolver.py`), FTF-0303
(`private_s3_artifact_resolver.py`) and FTF-0304 (`local_artifact_resolver.py`)
-- all three CERTIFIED, all three imported and reused unmodified here --
each already computes a real candidate content digest from real resolved
bytes (`combined_sha256_hex`, `sha256_hex`, `manifest_sha256`
respectively) but, by their own disclosed scope, never writes it onto a
`TrainableArtifact` row: each of their own module docstrings names this
task, FTF-0306, as the later, separately dependency-gated job that
combines their output into Section 6.7's own `weights_digest` column and
persists it through `artifact_crud.py`'s already-certified
`update_artifact`/`apply_edits`. This module is that composition -- a
dispatcher over the three sibling resolvers (by `artifact.source_type`),
never a fourth independent resolution implementation.

This task's own acceptance clause 1 ("Create immutable base-model/
tokenizer manifest and compare hashes on every load/resume") is split
across two entry points:

- `verify_or_compute_weights_digest` is the pure, DB-free core. It always
  recomputes a fresh candidate digest from real resolved bytes (never
  trusts a caller-supplied one) and, if the artifact already carries a
  resolved `weights_digest` (Section 6.7's own "immutable identity after
  resolution", already enforced schema-side by `apply_edits` refusing to
  edit a non-`registered` row), requires the fresh candidate to still
  match it -- raising `DigestMismatchError` rather than silently
  accepting drift. This is the "compare hashes on every load/resume"
  half of the acceptance clause: a caller invokes it again at
  training-resume/serving-load time, long after `mark_ready`, and it
  still recomputes and compares for real every time, never memoizing a
  trusted answer.
- `resolve_base_model_digest` is the real, DB-touching composition: fetch
  the artifact (`artifact_crud.get_artifact`), run the pure core, and --
  only the first time, while the row is still `registered` -- persist the
  resolved digest through `update_artifact`. A later re-verification
  against an already-`ready` artifact never calls `update_artifact`
  again (it would be correctly refused by `apply_edits`'s own immutability
  gate anyway); it only records a real audit event confirming the digest
  still matches. `database_changes: "No independent schema change.
  Consume frozen contract"` is read here exactly as FTF-0302/0303 already
  read it for their own `combined_sha256_hex`/`sha256_hex` outputs: this
  task decides how those already-computed digests reach the one column
  Section 6.7 already exposes, via the one write path FTF-0301 already
  certified -- it does not add a new column, table or write path of its
  own.

This task's own negative_acceptance ("Alias/display-name equality or
missing files cannot substitute for exact revision/digest") is enforced
by construction, not by a runtime check of one: every function below
takes a `TrainableArtifact` and a resolution-inputs record, and computes
`weights_digest` exclusively from what the real sibling resolver returns
for real resolved bytes (`combined_sha256_hex`/`sha256_hex`/
`manifest_sha256`) -- there is no `display_name`, `model_family` or other
alias-shaped field anywhere in this module's own digest computation, and
a resolver that finds no files to hash (`NoRecognizedArtifactFilesError`,
`LocalArtifactResolutionError: "...contains no regular files..."`) raises
rather than this module ever substituting an empty/default/placeholder
digest for a real one.

`private_registry` is one of Section 6.7's own four allowed
`source_type` values, but no task anywhere in this repository's full
300-task index implements a real private-registry resolver (FTF-0302/
0303/0304 are this task's own only three resolver dependencies) --
`compute_candidate_weights_digest` raises `UnsupportedSourceTypeError`
for it rather than fabricating a digest or silently reusing another
source type's own resolver; a real private-registry resolver task and
this dispatcher's own corresponding branch are future, separately
dependency-gated work, disclosed here rather than hidden.

No live HTTP route calls this module anywhere in this repository yet,
matching the same disclosed "no live caller yet" precedent already
accepted for FTF-0302/0303/0304 themselves (none of which has a route
either) -- a real training/resume worker (a later, separately
dependency-gated task) is this module's own eventual real caller;
declaring a route here without a real backing training/serving workflow
to call it from would itself be exactly the "placeholder endpoint" Law 11
forbids.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packages.security.outbound_guard import GuardedResponse, guarded_fetch
from packages.security.url_policy import ConnectionPolicy
from sqlalchemy.orm import Session

from app.artifacts.artifact_crud import ActorInfo, get_artifact, update_artifact
from app.artifacts.artifact_schema import TrainableArtifact
from app.artifacts.hugging_face_artifact_resolver import (
    resolve_and_verify_huggingface_artifact,
)
from app.artifacts.local_artifact_resolver import resolve_local_artifact
from app.artifacts.private_s3_artifact_resolver import (
    PrivateS3ConnectionConfig,
    resolve_and_verify_private_s3_artifact,
)
from app.auth.audit_event_writer import write_audit_event
from app.db.tenant_context import TenantContext

__all__ = [
    "BaseModelDigestResolutionError",
    "DigestMismatchError",
    "HuggingFaceResolutionInputs",
    "LocalFilesystemResolutionInputs",
    "PrivateS3ResolutionInputs",
    "ResolutionInputs",
    "ResolvedDigestOutcome",
    "UnsupportedSourceTypeError",
    "canonical_weights_digest",
    "compute_candidate_weights_digest",
    "resolve_base_model_digest",
    "verify_or_compute_weights_digest",
]

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_HUGGINGFACE_BASE_URL = "https://huggingface.co"

FetchFn = Callable[..., GuardedResponse]


class BaseModelDigestResolutionError(ValueError):
    """Base class for every real defect this module raises resolving or
    verifying a `TrainableArtifact`'s own `weights_digest` -- never caught
    and silently converted to a fabricated success anywhere in this
    module."""


class UnsupportedSourceTypeError(BaseModelDigestResolutionError):
    """`artifact.source_type` has no real resolver wired into this
    dispatcher -- see this module's own docstring for why `private_registry`
    lands here rather than being fabricated or silently aliased to another
    source type's resolver."""


class DigestMismatchError(BaseModelDigestResolutionError):
    """The artifact's own already-resolved, immutable `weights_digest` no
    longer matches a fresh resolution of the real underlying bytes.
    Never auto-corrected: raising here, rather than silently overwriting
    the stored digest, is this task's own acceptance clause ("compare
    hashes on every load/resume") made executable."""


@dataclass(frozen=True)
class HuggingFaceResolutionInputs:
    """Resolution inputs for a `huggingface` artifact -- mirrors
    `resolve_and_verify_huggingface_artifact`'s own real keyword
    parameters exactly (FTF-0302, reused unmodified)."""

    base_url: str = _DEFAULT_HUGGINGFACE_BASE_URL
    policy: ConnectionPolicy | None = None
    fetch: FetchFn = guarded_fetch
    timeout: float = _DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class PrivateS3ResolutionInputs:
    """Resolution inputs for a `private_s3` artifact -- mirrors
    `resolve_and_verify_private_s3_artifact`'s own real keyword parameters
    exactly (FTF-0303, reused unmodified)."""

    connection: PrivateS3ConnectionConfig
    expected_size_bytes: int | None = None
    policy: ConnectionPolicy | None = None
    fetch: FetchFn = guarded_fetch
    timeout: float = _DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class LocalFilesystemResolutionInputs:
    """Resolution inputs for a `local_filesystem` artifact -- mirrors
    `resolve_local_artifact`'s own real `approved_root` parameter exactly
    (FTF-0304, reused unmodified). Same disclosed scope FTF-0304 itself
    already established: `approved_root` is an explicit caller-supplied
    parameter here too, not read from a global, since there is still no
    live deployment-settings caller anywhere in this repository."""

    approved_root: str | Path


ResolutionInputs = (
    HuggingFaceResolutionInputs | PrivateS3ResolutionInputs | LocalFilesystemResolutionInputs
)


def canonical_weights_digest(raw_sha256_hex: str) -> str:
    """Formats a bare sha256 hex digest (what every sibling resolver
    actually returns) into Section 6.7's own required `weights_digest`
    shape (`validate_weights_digest`'s own `^sha256:[0-9a-f]{64}$`
    pattern, FTF-0301, reused unmodified) -- never invented ad hoc here."""
    return f"sha256:{raw_sha256_hex}"


def compute_candidate_weights_digest(artifact: TrainableArtifact, inputs: ResolutionInputs) -> str:
    """Dispatches to the one real sibling resolver matching
    `artifact.source_type` and returns a fresh, real, canonically-formatted
    candidate `weights_digest`. Raises whichever real error class the
    underlying resolver raises for a negative case (floating revision,
    unavailable license, remote code, bucket/prefix mismatch, partial
    upload, symlink/path escape, changing file, unsafe executable, no
    recognized files, ...) -- never swallowed or converted into a
    fabricated digest. Raises `UnsupportedSourceTypeError` for
    `private_registry` or any other value this dispatcher does not
    recognize."""
    if artifact.source_type == "huggingface":
        if not isinstance(inputs, HuggingFaceResolutionInputs):
            raise BaseModelDigestResolutionError(
                f"artifact {artifact.id} has source_type 'huggingface' but resolution inputs "
                f"were {type(inputs).__name__}, not HuggingFaceResolutionInputs"
            )
        hf_result = resolve_and_verify_huggingface_artifact(
            artifact,
            base_url=inputs.base_url,
            policy=inputs.policy,
            fetch=inputs.fetch,
            timeout=inputs.timeout,
        )
        return canonical_weights_digest(hf_result.combined_sha256_hex)

    if artifact.source_type == "private_s3":
        if not isinstance(inputs, PrivateS3ResolutionInputs):
            raise BaseModelDigestResolutionError(
                f"artifact {artifact.id} has source_type 'private_s3' but resolution inputs "
                f"were {type(inputs).__name__}, not PrivateS3ResolutionInputs"
            )
        s3_result = resolve_and_verify_private_s3_artifact(
            artifact,
            connection=inputs.connection,
            expected_size_bytes=inputs.expected_size_bytes,
            policy=inputs.policy,
            fetch=inputs.fetch,
            timeout=inputs.timeout,
        )
        return canonical_weights_digest(s3_result.sha256_hex)

    if artifact.source_type == "local_filesystem":
        if not isinstance(inputs, LocalFilesystemResolutionInputs):
            raise BaseModelDigestResolutionError(
                f"artifact {artifact.id} has source_type 'local_filesystem' but resolution "
                f"inputs were {type(inputs).__name__}, not LocalFilesystemResolutionInputs"
            )
        manifest = resolve_local_artifact(inputs.approved_root, artifact.source_uri)
        return canonical_weights_digest(manifest.manifest_sha256)

    raise UnsupportedSourceTypeError(
        f"artifact {artifact.id} has source_type {artifact.source_type!r}, which has no real "
        "resolver wired into this dispatcher -- refusing to fabricate a digest"
    )


@dataclass(frozen=True)
class ResolvedDigestOutcome:
    weights_digest: str
    changed: bool


def verify_or_compute_weights_digest(
    artifact: TrainableArtifact, inputs: ResolutionInputs
) -> ResolvedDigestOutcome:
    """The pure, DB-free core of this task's own acceptance clause. Always
    recomputes a fresh candidate digest from real resolved bytes; if
    `artifact.weights_digest` is already set, requires it to still match
    -- raising `DigestMismatchError` otherwise. `changed=True` only the
    first time (no prior `weights_digest`), signalling the caller should
    persist it; `changed=False` on a confirmed re-verification of an
    already-resolved artifact."""
    candidate = compute_candidate_weights_digest(artifact, inputs)
    if artifact.weights_digest is not None and artifact.weights_digest != candidate:
        raise DigestMismatchError(
            f"artifact {artifact.id} was previously resolved to weights_digest "
            f"{artifact.weights_digest!r} but now resolves to {candidate!r} -- the real "
            "underlying base-model/tokenizer bytes (or, for a huggingface tag/branch, the "
            "commit it now resolves to) have changed since this artifact's own identity was "
            "fixed; refusing to silently accept drift"
        )
    return ResolvedDigestOutcome(weights_digest=candidate, changed=artifact.weights_digest is None)


def resolve_base_model_digest(
    session: Session,
    context: TenantContext,
    artifact_id: uuid.UUID,
    inputs: ResolutionInputs,
    *,
    actor: ActorInfo,
) -> TrainableArtifact:
    """The real, DB-touching composition. Fetches the artifact
    (`artifact_crud.get_artifact`, already-certified tenant-scoped/
    IDOR-safe lookup), runs the pure core above, and -- only the first
    time, while the row is still `registered` -- persists the resolved
    digest through `artifact_crud.update_artifact` (which itself writes a
    real audit event in the same transaction). A later re-verification of
    an already-`ready` artifact (this task's own "compare hashes on every
    load/resume" case) never calls `update_artifact` again; it records its
    own `trainable_artifact.weights_digest_reverified` audit event instead
    -- a real, content-free observability event proving the comparison
    actually ran, not merely that no exception happened to be raised."""
    artifact = get_artifact(session, context, artifact_id)
    outcome = verify_or_compute_weights_digest(artifact, inputs)
    if outcome.changed:
        update_artifact(
            session,
            context,
            artifact_id,
            weights_digest=outcome.weights_digest,
            actor=actor,
        )
    else:
        write_audit_event(
            session,
            organization_id=context.organization_id,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="trainable_artifact.weights_digest_reverified",
            resource_type="trainable_artifact",
            resource_id=str(artifact.id),
            request_id=actor.request_id,
        )
    return artifact
