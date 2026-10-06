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

2026-10-06 L2 round-2 review findings and fixes (control-plane review of
merged PR #424, `docs/certifications/queue.md` "2026-10-04T11:00Z"): the
round-1 checks above verified only that `artifact.id` matched
`model_version.trainable_artifact_id`, and that an `adapter`'s
*caller-supplied* `resolved_base_artifact` -- when a caller bothered to
supply one at all -- had a matching `base_model_revision`. Four real
gaps followed from that: (1) a `registered` (still-mutable) or `archived`
parent, or a parent in a *different* organization than the `model_version`
being built, was accepted with no check at all; (2) a parent whose current
`weights_digest`/`base_model_revision` had drifted from the value this
`model_version`'s own immutable row already recorded at creation time was
accepted; (3) `resolved_base_artifact` was optional for an `adapter`
import, so a caller could skip every base-validation check by simply not
supplying it; (4) nothing stopped an adapter from resolving to *itself* as
its own base, either by row identity or by sharing its own
`weights_digest`. All four are now real, raising checks (status/tenant/
digest/revision parity against the real parent; a mandatory, non-optional
`resolved_base_artifact` for every `adapter` import; cross-tenant and
self-parent/self-digest checks on the resolved base itself) -- see
`build_artifact_lineage_manifest`.

A fifth finding -- "there is no manifest digest, so nothing makes it
tamper-evident" -- is fixed by adding a real `manifest_digest` field
(`ArtifactLineageManifest`, computed by `_compute_manifest_digest` as a
canonical-JSON sha256 over every field the manifest actually carries) and
`verify_artifact_lineage_manifest`, which recomputes that digest from a
manifest's own current field values and raises
`ArtifactLineageManifestTamperedError` on any mismatch -- detecting
mutation past the frozen-dataclass guard (`object.__setattr__`) or
corruption across a persist/reload round trip.

Disclosed, not silently dropped: this task's own DoD item "add a live
load consumer, or record that half BLOCKED" stays BLOCKED -- no training/
serving worker task is dependency-ready yet to actually call
`build_artifact_lineage_manifest`/`verify_artifact_lineage_manifest`
(`proving_symbol` stays blank in `docs/spec-matrix.yaml`, the same
disclosed "no live caller yet" precedent FTF-0306/FTF-0307 already
established); this round adds no live caller, only the fixes above and
their tests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.artifacts.artifact_schema import (
    TrainableArtifact,
    TrainableArtifactValidationError,
    validate_base_model_revision,
    validate_weights_digest,
)
from app.model_versions.model_version_schema import ModelVersion

__all__ = [
    "AdapterBaseMismatchError",
    "ArtifactLineageError",
    "ArtifactLineageManifest",
    "ArtifactLineageManifestTamperedError",
    "LineageFact",
    "build_artifact_lineage_manifest",
    "verify_artifact_lineage_manifest",
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


class ArtifactLineageManifestTamperedError(ArtifactLineageError):
    """A manifest's own stored `manifest_digest` does not match the real
    digest recomputed from its own field values -- the manifest was
    mutated (bypassing its frozen-dataclass construction guard, e.g. via
    `object.__setattr__`) or corrupted/forged across a
    serialize/deserialize round trip after construction. Raised by
    `verify_artifact_lineage_manifest`, this task's own tamper-evidence
    requirement (2026-10-06 L2 round-2 review: "There is no manifest
    digest, so nothing makes it tamper-evident")."""


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
    manifest_digest: str


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


def _canonical_fact(fact: LineageFact) -> dict[str, object]:
    return {"known": fact.known, "value": fact.value, "detail": fact.detail}


def _compute_manifest_digest(
    *,
    model_version_id: str,
    import_kind: str,
    base: LineageFact,
    tokenizer: LineageFact,
    training: LineageFact,
    data: LineageFact,
    license: LineageFact,  # noqa: A002 -- matches this task's own acceptance-clause dimension name
    runtime: LineageFact,
    method: LineageFact,
) -> str:
    """This task's own tamper-evidence requirement: a canonical content
    digest over every field this manifest actually carries, so a mutated
    or corrupted manifest is detectable by `verify_artifact_lineage_manifest`
    instead of trusted on sight. `json.dumps(..., sort_keys=True)` makes the
    encoding deterministic regardless of attribute-assignment order."""
    canonical = {
        "model_version_id": model_version_id,
        "import_kind": import_kind,
        "base": _canonical_fact(base),
        "tokenizer": _canonical_fact(tokenizer),
        "training": _canonical_fact(training),
        "data": _canonical_fact(data),
        "license": _canonical_fact(license),
        "runtime": _canonical_fact(runtime),
        "method": _canonical_fact(method),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


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
        if resolved_base_artifact is not None:
            # 2026-10-06 L2 round-3 finding: an endpoint import with a
            # resolved_base_artifact silently ignored it. resolved_base_
            # artifact is only meaningful for an adapter import (Section
            # 68.1); an endpoint has no local artifact at all, so a
            # supplied base cannot be real evidence either.
            raise ArtifactLineageError(
                f"model_version {model_version.id} is import_kind='endpoint' -- "
                "resolved_base_artifact is only meaningful for an adapter import; refusing to "
                "silently ignore a supplied base for an endpoint-only import"
            )
        unknown = _unknown(_UNKNOWN_ENDPOINT_DETAIL)
        unknown_training = _unknown(_UNKNOWN_TRAINING_DETAIL)
        unknown_data = _unknown(_UNKNOWN_DATA_DETAIL)
        return ArtifactLineageManifest(
            model_version_id=str(model_version.id),
            import_kind=model_version.import_kind,
            base=unknown,
            tokenizer=unknown,
            training=unknown_training,
            data=unknown_data,
            license=unknown,
            runtime=unknown,
            method=unknown,
            manifest_digest=_compute_manifest_digest(
                model_version_id=str(model_version.id),
                import_kind=model_version.import_kind,
                base=unknown,
                tokenizer=unknown,
                training=unknown_training,
                data=unknown_data,
                license=unknown,
                runtime=unknown,
                method=unknown,
            ),
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
    # 2026-10-06 L2 round-3 finding: this module trusted
    # model_version.weights_digest/base_model_revision and artifact.
    # weights_digest/base_model_revision were already shape-valid.
    # `TrainableArtifact.__init__`'s own validation only runs on real
    # construction -- an ORM row loaded from the database (SQLAlchemy sets
    # attributes directly) skips it entirely, so None/blank/malformed
    # values could reach this module and still produce a manifest that
    # reports `known=True`. Re-validated here with the shared validators,
    # the same "re-validate, never merely trust an earlier check"
    # discipline `artifact_crud.apply_mark_ready` already applies.
    try:
        validate_weights_digest(model_version.weights_digest)
        validate_weights_digest(artifact.weights_digest)
        validate_base_model_revision(model_version.base_model_revision)
        validate_base_model_revision(artifact.base_model_revision)
    except TrainableArtifactValidationError as exc:
        raise ArtifactLineageError(
            f"model_version {model_version.id} or its parent artifact {artifact.id} carries a "
            f"malformed or unresolved weights_digest/base_model_revision -- {exc}"
        ) from exc
    # 2026-10-06 L2 round-2 review findings, all against the real parent
    # `artifact` itself (previously checked only by id, never by status/
    # tenant/digest consistency): a `registered` (still-mutable) or
    # `archived` (closed) parent, a cross-tenant parent, or a parent whose
    # own current weights_digest/base_model_revision has drifted from the
    # value this model version's own immutable row already recorded at
    # creation time, were all silently accepted before this fix.
    if artifact.status != "ready":
        raise ArtifactLineageError(
            f"artifact {artifact.id} is status={artifact.status!r}, not 'ready' -- a "
            "'registered' (still mutable) or 'archived' (closed) parent cannot back a real "
            "lineage claim (Section 35: 'Every model version is immutable', FTF-0301's own "
            "ready-transition gate)"
        )
    if artifact.organization_id != model_version.organization_id:
        raise ArtifactLineageError(
            f"artifact {artifact.id} belongs to organization {artifact.organization_id}, not "
            f"model_version {model_version.id}'s own organization {model_version.organization_id} "
            "-- refusing a cross-tenant parent"
        )
    if artifact.weights_digest != model_version.weights_digest:
        raise ArtifactLineageError(
            f"artifact {artifact.id}'s current weights_digest {artifact.weights_digest!r} does "
            f"not match model_version {model_version.id}'s own recorded weights_digest "
            f"{model_version.weights_digest!r} -- refusing a parent whose digest has drifted "
            "from what this model version actually captured"
        )
    if artifact.base_model_revision != model_version.base_model_revision:
        raise ArtifactLineageError(
            f"artifact {artifact.id}'s current base_model_revision "
            f"{artifact.base_model_revision!r} does not match model_version {model_version.id}'s "
            f"own recorded base_model_revision {model_version.base_model_revision!r} -- refusing "
            "a parent whose revision has drifted from what this model version actually captured"
        )

    if model_version.import_kind == "adapter":
        # Previously optional: a caller could omit resolved_base_artifact
        # entirely and skip every base-validation check below. Section
        # 68.1's own "Adapter must resolve exact base" rule is not
        # optional -- an adapter import always requires it.
        if resolved_base_artifact is None:
            raise ArtifactLineageError(
                f"model_version {model_version.id} is import_kind='adapter' -- a real, "
                "already-resolved resolved_base_artifact is required (Section 68.1: 'Adapter "
                "must resolve exact base, not same-family guessed weights'); it is not optional"
            )
    elif resolved_base_artifact is not None:
        raise ArtifactLineageError(
            "resolved_base_artifact is only meaningful for import_kind='adapter' "
            f"(Section 68.1's own adapter-vs-exact-base rule); got "
            f"{model_version.import_kind!r}"
        )

    if resolved_base_artifact is not None:
        # 2026-10-06 L2 round-3 finding: the resolved base's own
        # weights_digest/base_model_revision were never re-validated here
        # either (the same ORM-bypasses-__init__ gap as the main artifact
        # above) -- a malformed base digest/revision could otherwise reach
        # every comparison below as an opaque, never-format-checked string.
        try:
            validate_weights_digest(resolved_base_artifact.weights_digest)
            validate_base_model_revision(resolved_base_artifact.base_model_revision)
        except TrainableArtifactValidationError as exc:
            raise AdapterBaseMismatchError(
                f"resolved base artifact {resolved_base_artifact.id} carries a malformed or "
                f"unresolved weights_digest/base_model_revision -- {exc}"
            ) from exc
        if resolved_base_artifact.status != "ready":
            raise AdapterBaseMismatchError(
                f"resolved base artifact {resolved_base_artifact.id} is "
                f"{resolved_base_artifact.status!r}, not 'ready' -- its own identity is not yet "
                "pinned/resolved (FTF-0306), so it cannot validate this adapter's declared base"
            )
        if resolved_base_artifact.organization_id != model_version.organization_id:
            raise AdapterBaseMismatchError(
                f"resolved base artifact {resolved_base_artifact.id} belongs to organization "
                f"{resolved_base_artifact.organization_id}, not model_version "
                f"{model_version.id}'s own organization {model_version.organization_id} -- "
                "refusing a cross-tenant base"
            )
        # 2026-10-06 L2 round-3 finding: "the base is compared only by
        # revision string ... there is no model_family check" -- a
        # resolved base of a completely different model family could
        # still carry a coincidentally-matching base_model_revision
        # string. model_family is a real, already-captured identity field
        # on every TrainableArtifact (Section 68.1's own "exact base, not
        # same-family guessed weights" is read literally: family itself
        # must genuinely match, not merely be guessed compatible).
        if resolved_base_artifact.model_family != artifact.model_family:
            raise AdapterBaseMismatchError(
                f"resolved base artifact {resolved_base_artifact.id} declares model_family "
                f"{resolved_base_artifact.model_family!r}, but adapter artifact {artifact.id} "
                f"declares model_family {artifact.model_family!r} -- refusing a resolved base "
                "from a different model family than the adapter itself declares"
            )
        # Identity: an adapter cannot resolve to itself as its own base --
        # by row identity (self-parent) or by digest (an artifact whose
        # claimed base shares the adapter's own weights_digest is exactly
        # the same self-referential defect by a different field). A plain
        # `==` is sufficient here (not case-insensitive): both digests
        # were already passed through `validate_weights_digest` above,
        # which accepts only the canonical lowercase-hex pattern, so by
        # the time either digest reaches this comparison a case variant
        # cannot exist -- it would already have been refused as malformed
        # a few lines above, by the 2026-10-06 round-3 format-validation
        # fix, before this comparison is even reached.
        if resolved_base_artifact.id == artifact.id:
            raise AdapterBaseMismatchError(
                f"resolved base artifact {resolved_base_artifact.id} is the adapter artifact "
                f"{artifact.id} itself -- an adapter cannot be its own base"
            )
        if resolved_base_artifact.weights_digest == artifact.weights_digest:
            raise AdapterBaseMismatchError(
                f"resolved base artifact {resolved_base_artifact.id}'s own weights_digest "
                f"{resolved_base_artifact.weights_digest!r} equals adapter artifact "
                f"{artifact.id}'s own weights_digest "
                f"{artifact.weights_digest!r} (case-insensitively) -- an adapter cannot be its "
                "own base"
            )
        # Digest/revision: the resolved base's own real, resolved identity
        # must match what the adapter itself declares as its base.
        if resolved_base_artifact.base_model_revision != artifact.base_model_revision:
            raise AdapterBaseMismatchError(
                f"adapter artifact {artifact.id} declares base_model_revision "
                f"{artifact.base_model_revision!r}, but the resolved base artifact "
                f"{resolved_base_artifact.id} is pinned to "
                f"{resolved_base_artifact.base_model_revision!r} -- Section 68.1 requires an "
                "adapter to resolve its exact declared base, never a same-family guessed one; "
                "refusing to load/qualify this pairing"
            )

    base_detail = (
        f"base_model_revision={artifact.base_model_revision!r}, digest resolved by FTF-0306"
    )
    if resolved_base_artifact is not None:
        # 2026-10-06 L2 round-3 finding: "the base id and digest are not
        # recorded in the manifest" -- the resolved base's own real
        # identity (id, digest, model_family, source_uri) is now captured
        # here for an adapter import, not only the adapter's own declared
        # base_model_revision.
        base_detail += (
            f"; resolved_base_artifact_id={resolved_base_artifact.id!s}, "
            f"resolved_base_weights_digest={resolved_base_artifact.weights_digest!r}, "
            f"resolved_base_model_family={resolved_base_artifact.model_family!r}, "
            f"resolved_base_source_uri={resolved_base_artifact.source_uri!r}"
        )
    base = LineageFact(
        known=True,
        value=artifact.weights_digest,
        detail=base_detail,
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

    training_fact = _unknown(_UNKNOWN_TRAINING_DETAIL)
    data_fact = _unknown(_UNKNOWN_DATA_DETAIL)
    return ArtifactLineageManifest(
        model_version_id=str(model_version.id),
        import_kind=model_version.import_kind,
        base=base,
        tokenizer=tokenizer,
        training=training_fact,
        data=data_fact,
        license=license_fact,
        runtime=runtime,
        method=method,
        manifest_digest=_compute_manifest_digest(
            model_version_id=str(model_version.id),
            import_kind=model_version.import_kind,
            base=base,
            tokenizer=tokenizer,
            training=training_fact,
            data=data_fact,
            license=license_fact,
            runtime=runtime,
            method=method,
        ),
    )


def verify_artifact_lineage_manifest(manifest: ArtifactLineageManifest) -> None:
    """Recompute `manifest`'s own canonical content digest from its actual
    current field values and compare it against the digest it carries,
    raising `ArtifactLineageManifestTamperedError` on any mismatch. This is
    this task's own tamper-evidence requirement made real: a caller that
    persists a manifest (e.g. under Section 77.4's own MLflow-referenced
    evidence) and later reloads it can detect mutation (bypassing the
    frozen-dataclass guard via `object.__setattr__`) or corruption across
    the round trip, instead of trusting a reloaded manifest on sight."""
    recomputed = _compute_manifest_digest(
        model_version_id=manifest.model_version_id,
        import_kind=manifest.import_kind,
        base=manifest.base,
        tokenizer=manifest.tokenizer,
        training=manifest.training,
        data=manifest.data,
        license=manifest.license,
        runtime=manifest.runtime,
        method=manifest.method,
    )
    if recomputed != manifest.manifest_digest:
        raise ArtifactLineageManifestTamperedError(
            f"manifest for model_version {manifest.model_version_id} carries manifest_digest "
            f"{manifest.manifest_digest!r}, but its own current field values recompute to "
            f"{recomputed!r} -- the manifest was mutated or corrupted after construction"
        )
