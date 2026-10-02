"""Artifact lineage manifest (FTF-1002, Section 34-36/68/77/81).

Path refined from the task packet's own suggested
`services/serving-controller/vllm/artifact_lineage_manifest.py` -- `services/`
does not exist anywhere in this repository and is not a `uv` workspace
member, the same class of unimportable suggested path FTF-0301/FTF-0304/
FTF-0305/FTF-0306/FTF-0307 already refined away from their own packets'
`packages/model-catalog/...` suggestion. This task's own two real
dependencies, FTF-0306 (`apps/api/app/artifacts/
base_model_revision_digest_resolver.py`) and FTF-1001 (`apps/api/app/
model_versions/model_version_schema.py`), both already live under
`apps/api/app/`; this module joins FTF-0306/FTF-0307 in `apps/api/app/
artifacts/` rather than inventing a second, parallel artifact-domain
package.

This task's own acceptance clause ("Artifact lineage binds base/tokenizer/
training/data/license/runtime/method and records unknown external
ancestry") is a pure, no-I/O composer over the two real, already-CERTIFIED
records this task's own dependency set actually carries --
`app.model_versions.model_version_schema.ModelVersion` and
`app.artifacts.artifact_schema.TrainableArtifact` -- never a fourth
independent resolution, a database query, or a name/string-based lookup of
its own. A caller (a future, separately dependency-gated training/serving
worker) supplies both already-fetched records; this module never fetches
anything itself, matching this task's own `database_changes` clause ("No
independent schema change. Consume frozen contract.") exactly the same way
`training_capability_classifier.py` (FTF-0307) already read that clause for
its own four dependencies.

Each of the seven named dimensions is reported independently, scoped
strictly to what this task's own two real dependencies actually prove:

- `base`/`tokenizer`: for a `full_weight`/`adapter` import, Section 34's own
  "immutable base-model/tokenizer manifest" phrase is read literally --
  every sibling resolver FTF-0306 dispatches to
  (`hugging_face_artifact_resolver`/`private_s3_artifact_resolver`/
  `local_artifact_resolver`) already computes its own digest over the
  *entire* resolved artifact tree, tokenizer files included (there is no
  separate tokenizer-only digest column anywhere in this repository's
  schema), so both dimensions report the same real, already-resolved
  `weights_digest` plus the artifact's own `base_model_revision`. For an
  `endpoint` import, Section 68.1 ("Display unknown parent/training data as
  unknown") applies directly: no local artifact backs it, so both are
  `known=False`.
- `license`: the artifact's own real `license_id`/`license_accepted_by`/
  `license_accepted_at` fields (FTF-0301, already-certified) for a
  weight-backed import; unknown for an endpoint import (no artifact at
  all).
- `runtime`: the artifact's own real, caller-declared
  `required_python_packages_json` (Section 34's own "library versions") when
  non-empty; otherwise unknown. No `ComponentVersionLock`/container-digest
  record exists anywhere in this repository yet (Section 77.5, not yet
  implemented by any task), so "pinned full execution environment" always
  stays disclosed as a gap here, never fabricated as a container digest this
  module does not actually have.
- `method`: the real, already-validated `import_kind` itself
  (`full_weight` vs `adapter`) -- a real, non-fabricated fact already
  enforced by `ModelVersion`'s own schema invariants -- but only that coarse
  shape; no training-method/hyperparameter record (LoRA rank, precision,
  ...) exists in this task's own dependency set, so nothing deeper is
  claimed.
- `training`/`data`: no `TrainingRun`, dataset-hash or dataset-versioning
  task is anywhere in this task's own dependency set (`FTF-0306`/`FTF-1001`
  only) -- always reported `known=False`, "unknown external ancestry" in
  this task's own acceptance clause's own words, rather than fabricating a
  "trained by the Factory" claim no real training record backs (Law 11: no
  fake training job; Law 5: existence is not evidence).

This task's own negative_acceptance ("Unknown parent cannot be fabricated;
adapter-base mismatch invalidates load/qualification") is enforced as two
real, raising checks, not merely descriptive text:

1. `ArtifactLineageError` is raised if a `full_weight`/`adapter`
   `model_version` is given with no `artifact` at all, or with an
   `artifact` whose own `id` does not match `model_version.
   trainable_artifact_id` -- a caller cannot fabricate a parent other than
   the one this model version's own schema already links to. The same
   error is raised the other way around: an `endpoint` `model_version`
   (whose own schema invariant already forbids `trainable_artifact_id`)
   given a real `artifact` anyway is refused before any dimension is
   reported, rather than silently treating a fabricated parent as real
   evidence.
2. `AdapterBaseMismatchError` is raised when an `adapter` import's own
   caller-supplied `resolved_base_artifact` (the real, already-fetched
   `TrainableArtifact` the caller believes is this adapter's actual base)
   is not `status == "ready"` (its own identity is not yet pinned, so it
   cannot be trusted to validate anything), or its own
   `base_model_revision` does not match the adapter artifact's own declared
   `base_model_revision` -- Section 68.1's own "Adapter must resolve exact
   base, not same-family guessed weights" made executable, the same
   recompute-and-compare-never-silently-accept-drift discipline FTF-0306's
   own `DigestMismatchError` already established for a structurally
   identical reason. Raising here is this task's own "invalidates load/
   qualification" clause made real: the caller (a future training/serving
   worker) receives a real exception instead of a manifest, and must not
   proceed to load or qualify the mismatched pairing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.artifacts.artifact_schema import TrainableArtifact
from app.model_versions.model_version_schema import ModelVersion

__all__ = [
    "AdapterBaseMismatchError",
    "ArtifactLineageError",
    "ArtifactLineageManifest",
    "LineageFact",
    "build_artifact_lineage_manifest",
]


class ArtifactLineageError(ValueError):
    """The supplied `model_version`/`artifact`/`resolved_base_artifact`
    combination is structurally inconsistent with what this model
    version's own schema already records -- never silently treated as a
    fabricated-but-plausible parent."""


class AdapterBaseMismatchError(ArtifactLineageError):
    """An `adapter` import's own declared base does not match its
    caller-supplied `resolved_base_artifact` -- Section 68.1's own "exact
    base, not same-family guessed weights" rule, violated. Raised instead
    of returned so a caller cannot accidentally load or qualify the
    mismatched pairing by ignoring a boolean field."""


@dataclass(frozen=True)
class LineageFact:
    """One of the seven dimensions this task's own acceptance clause names.
    `known=False` always means `value=None` -- this module never reports a
    placeholder value for an ancestry it does not actually have evidence
    for (this task's own negative_acceptance, "unknown parent cannot be
    fabricated")."""

    known: bool
    value: str | None
    detail: str

    def __post_init__(self) -> None:
        if not self.known and self.value is not None:
            raise ArtifactLineageError(
                "a LineageFact with known=False must never carry a value -- that would be "
                f"exactly the fabricated-unknown-ancestry this task's own negative_acceptance "
                f"forbids (got value={self.value!r})"
            )


@dataclass(frozen=True)
class ArtifactLineageManifest:
    model_version_id: str
    import_kind: str
    base: LineageFact
    tokenizer: LineageFact
    training: LineageFact
    data: LineageFact
    license: LineageFact
    runtime: LineageFact
    method: LineageFact


_UNKNOWN_ENDPOINT_DETAIL = (
    "endpoint-only import: no local artifact backs it, so no base/tokenizer/license/runtime/"
    "method is visible to the Factory (Section 68.1: 'Display unknown parent/training data as "
    "unknown')"
)
_UNKNOWN_TRAINING_DETAIL = (
    "no TrainingRun/training-manifest task exists anywhere in this task's own dependency set "
    "(FTF-0306/FTF-1001 only) -- a future, separately dependency-gated task's own job; never "
    "fabricated as Factory-trained without a real training record"
)
_UNKNOWN_DATA_DETAIL = (
    "no dataset-hash/versioning task exists anywhere in this task's own dependency set "
    "(FTF-0306/FTF-1001 only) -- recorded as unknown external ancestry, not fabricated"
)


def _unknown(detail: str) -> LineageFact:
    return LineageFact(known=False, value=None, detail=detail)


def build_artifact_lineage_manifest(
    model_version: ModelVersion,
    artifact: TrainableArtifact | None,
    *,
    resolved_base_artifact: TrainableArtifact | None = None,
) -> ArtifactLineageManifest:
    """Compose the real, already-fetched `model_version`/`artifact` (and,
    for an `adapter` import, an optional already-fetched
    `resolved_base_artifact`) into this task's own seven-dimension lineage
    manifest. Never queries the database or the filesystem itself -- see
    this module's own docstring for why every input is already-resolved
    caller-supplied data."""
    if model_version.import_kind == "endpoint":
        if artifact is not None:
            raise ArtifactLineageError(
                f"model_version {model_version.id} is import_kind='endpoint' -- its own schema "
                "already forbids a trainable_artifact_id, yet a real TrainableArtifact was "
                "supplied here. Refusing to fabricate a parent this model version's own record "
                "does not actually have"
            )
        unknown = _unknown(_UNKNOWN_ENDPOINT_DETAIL)
        return ArtifactLineageManifest(
            model_version_id=str(model_version.id),
            import_kind=model_version.import_kind,
            base=unknown,
            tokenizer=unknown,
            training=_unknown(_UNKNOWN_TRAINING_DETAIL),
            data=_unknown(_UNKNOWN_DATA_DETAIL),
            license=unknown,
            runtime=unknown,
            method=unknown,
        )

    # full_weight / adapter: Section 68.1's own "no fabricated parent" rule
    # requires the real artifact this model version's own schema actually
    # links to, not merely any artifact the caller happens to supply.
    if artifact is None:
        raise ArtifactLineageError(
            f"model_version {model_version.id} is import_kind={model_version.import_kind!r}, "
            "which requires a real parent TrainableArtifact -- refusing to build a lineage "
            "manifest for a weight-backed import with no real artifact evidence"
        )
    if artifact.id != model_version.trainable_artifact_id:
        raise ArtifactLineageError(
            f"artifact {artifact.id} does not match model_version {model_version.id}'s own "
            f"recorded trainable_artifact_id {model_version.trainable_artifact_id!r} -- "
            "refusing to fabricate a parent other than the one this model version actually "
            "links to"
        )

    if resolved_base_artifact is not None:
        if model_version.import_kind != "adapter":
            raise ArtifactLineageError(
                "resolved_base_artifact is only meaningful for import_kind='adapter' "
                f"(Section 68.1's own adapter-vs-exact-base rule); got "
                f"{model_version.import_kind!r}"
            )
        if resolved_base_artifact.status != "ready":
            raise AdapterBaseMismatchError(
                f"resolved base artifact {resolved_base_artifact.id} is "
                f"{resolved_base_artifact.status!r}, not 'ready' -- its own identity is not yet "
                "pinned/resolved (FTF-0306), so it cannot validate this adapter's declared base"
            )
        if resolved_base_artifact.base_model_revision != artifact.base_model_revision:
            raise AdapterBaseMismatchError(
                f"adapter artifact {artifact.id} declares base_model_revision "
                f"{artifact.base_model_revision!r}, but the resolved base artifact "
                f"{resolved_base_artifact.id} is pinned to "
                f"{resolved_base_artifact.base_model_revision!r} -- Section 68.1 requires an "
                "adapter to resolve its exact declared base, never a same-family guessed one; "
                "refusing to load/qualify this pairing"
            )

    base = LineageFact(
        known=True,
        value=artifact.weights_digest,
        detail=f"base_model_revision={artifact.base_model_revision!r}, digest resolved by FTF-0306",
    )
    tokenizer = LineageFact(
        known=True,
        value=artifact.weights_digest,
        detail=(
            "tokenizer bytes are bound into the same combined artifact digest FTF-0306 "
            "resolves (Section 34: 'immutable base-model/tokenizer manifest') -- no separate "
            "tokenizer-only digest column exists in this repository's schema"
        ),
    )
    accepted_at = artifact.license_accepted_at.isoformat() if artifact.license_accepted_at else None
    license_fact = LineageFact(
        known=True,
        value=artifact.license_id,
        detail=f"accepted_by={artifact.license_accepted_by!r} at {accepted_at!r}",
    )
    method = LineageFact(
        known=True,
        value=model_version.import_kind,
        detail=(
            "coarse import shape only (full_weight vs adapter) -- no training-method/"
            "hyperparameter record exists in this task's own dependency set (FTF-0306/FTF-1001)"
        ),
    )
    if artifact.required_python_packages_json:
        runtime = LineageFact(
            known=True,
            value=",".join(sorted(artifact.required_python_packages_json)),
            detail=(
                "declared required_python_packages (TrainableArtifact, Section 6.7) -- no "
                "ComponentVersionLock/container-digest record exists yet anywhere in this "
                "repository (Section 77.5, not yet implemented) to pin the full execution "
                "environment"
            ),
        )
    else:
        runtime = _unknown(
            "no required_python_packages declared on this artifact, and no "
            "ComponentVersionLock/container-digest record exists yet anywhere in this "
            "repository (Section 77.5, not yet implemented) -- recorded as unknown, not "
            "fabricated"
        )

    return ArtifactLineageManifest(
        model_version_id=str(model_version.id),
        import_kind=model_version.import_kind,
        base=base,
        tokenizer=tokenizer,
        training=_unknown(_UNKNOWN_TRAINING_DETAIL),
        data=_unknown(_UNKNOWN_DATA_DETAIL),
        license=license_fact,
        runtime=runtime,
        method=method,
    )
