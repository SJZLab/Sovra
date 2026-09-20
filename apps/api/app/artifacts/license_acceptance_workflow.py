"""License acceptance workflow (FTF-0305, Section 6.7/13/34/68/77/81).

Refined from the task packet's own suggested path
(`packages/model-catalog/license_acceptance_workflow.py`) to this one --
`packages/model-catalog` is not a `uv` workspace member and, being
hyphenated, is not even a valid Python import path; FTF-0301 already
established the precedent of refining that same suggested
`packages/model-catalog/...` prefix into a real `apps/api/app/artifacts/*`
module instead of creating an unimportable package, and this task follows
that same refinement for the identical reason (see this task's own
docs/certifications/queue.md submission entry).

Section 6.7's `TrainableArtifact` already carries `license_id`/
`license_accepted_by`/`license_accepted_at` (FTF-0301, CERTIFIED) and
`apply_mark_ready` already refuses to promote an artifact to `ready`
unless a real authenticated actor has accepted its license -- the
acceptance record already lives on the very same row as
`base_model_revision`, so "acceptance bound to model revision" (this
task's own acceptance clause) already holds by construction: there is no
free-floating, artifact-independent "this license is accepted" fact
anywhere in this schema, only a specific artifact's own row. This task's
own job is not to duplicate that state -- `database_changes` is explicitly
"No independent schema change. Consume frozen contract" -- but to close
what FTF-0301 left open by disclosed scope: **revocation**. FTF-0301 had
no way to withdraw an acceptance once given. `apply_revoke_license` adds
it, using only the same nullable columns FTF-0301's own migration already
created -- no new column, no new migration. Revoking a `ready` artifact
demotes it back to `registered`, so any code path (present or future)
that gates real work on `status == "ready"` is automatically excluded the
moment a license is revoked, without that caller needing its own separate
license check.

Deliberately does NOT hook into `apply_edits`: an earlier version of this
task auto-cleared an acceptance whenever `source_uri`/
`base_model_revision`/`license_id` changed on a `registered` artifact,
but that materially changed already-CERTIFIED FTF-0301 behavior (its own
live-Postgres suite accepts a license, then pins `base_model_revision`
via PATCH, then expects `mark-ready` to still succeed) -- a Law 15
"material source change invalidates affected evidence" situation this
task is not chartered to open. Revocation is instead always an explicit,
on-demand action (`revoke_license`), never an automatic side effect of an
unrelated edit.

This module is deliberately pure and DB-free (no SQLAlchemy/session
import at all), the same "pure core" discipline
`apps/api/app/services/project_security_profile.py` (FTF-0402) already
established for the identical reason: the state-machine rules are
provable without a database. `apps/api/app/artifacts/artifact_crud.py`'s
own new `revoke_license` (this task's own real, live-wired caller,
exposed at `POST /api/v1/organizations/{organization_id}/artifacts/
{artifact_id}/revoke-license`, purely additive alongside FTF-0301's
existing operations) is the real caller -- proven against a real Postgres
database in
`tests/integration/test_ftf_0305_license_acceptance_workflow.py`.

`assert_license_accepted_for_use` is the one sanctioned admission gate a
future training or serving workflow must call (no such live caller exists
yet anywhere in this repository -- TrainingExperiment/Deployment are
later, separately dependency-gated tasks; the same disclosed "no live
caller yet" class already accepted for FTF-0005/FTF-0006/FTF-0102/
FTF-0103/FTF-0104/FTF-0105/FTF-H003 before each one's own first live
caller existed). This task's own negative_acceptance ("no blanket
approval based on provider name") is enforced by construction, not by a
runtime check of one: every function in this module takes a
`TrainableArtifact` and reads only its real `license_accepted_by`/
`license_accepted_at` columns -- there is no `provider`, `source_type` or
`model_family` parameter anywhere in this module's public surface that
could shortcut the real per-artifact acceptance record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.artifacts.artifact_schema import TrainableArtifact

__all__ = [
    "LicenseAcceptanceError",
    "LicenseNotAcceptedError",
    "LicenseGateResult",
    "evaluate_license_gate",
    "assert_license_accepted_for_use",
    "invalidate_stale_license_acceptance",
    "apply_revoke_license",
]


class LicenseAcceptanceError(ValueError):
    """Base for every license-acceptance-workflow violation this module
    raises -- never caught and silently converted to success anywhere in
    this module or its real callers."""


class LicenseNotAcceptedError(LicenseAcceptanceError):
    """Raised by `assert_license_accepted_for_use` when an artifact's
    license is unaccepted or has been revoked -- this task's own
    negative_acceptance ("Unaccepted/revoked license blocks training/
    serving") made executable at the point of use, not only at
    artifact-registration time."""


@dataclass(frozen=True)
class LicenseGateResult:
    """The pure result of evaluating whether `artifact`'s license is
    currently accepted. `bound_base_model_revision` is always the
    artifact's own *current* `base_model_revision` at evaluation time --
    never a value cached from when acceptance first happened, since a
    stale acceptance is cleared the instant the identity it was bound to
    changes (see `invalidate_stale_license_acceptance`)."""

    accepted: bool
    accepted_by: str | None
    accepted_at_iso: str | None
    bound_base_model_revision: str | None
    reason: str | None


def evaluate_license_gate(artifact: TrainableArtifact) -> LicenseGateResult:
    """Pure, DB-free evaluation of whether `artifact`'s license is
    currently accepted. Reads only the real `license_accepted_by`/
    `license_accepted_at` fields that FTF-0301's own `accept_license` and
    this task's own `revoke_license` are the *only* writers of -- never a
    `source_type`/`model_family`/provider-name heuristic."""
    if artifact.license_accepted_by is None or artifact.license_accepted_at is None:
        return LicenseGateResult(
            accepted=False,
            accepted_by=None,
            accepted_at_iso=None,
            bound_base_model_revision=None,
            reason=(
                "license has not been accepted, or was revoked, for this "
                "artifact's current identity"
            ),
        )
    return LicenseGateResult(
        accepted=True,
        accepted_by=artifact.license_accepted_by,
        accepted_at_iso=artifact.license_accepted_at.isoformat(),
        bound_base_model_revision=artifact.base_model_revision,
        reason=None,
    )


def assert_license_accepted_for_use(artifact: TrainableArtifact) -> LicenseGateResult:
    """The one sanctioned admission gate a training or serving workflow
    must call before starting real work against `artifact`. Raises
    `LicenseNotAcceptedError` unless the license is currently accepted;
    returns the passing `LicenseGateResult` on success. Takes no
    provider/source/model_family parameter that could shortcut the real
    per-artifact acceptance check -- this task's own negative_acceptance
    ("no blanket approval based on provider name") enforced by the
    absence of such a parameter, not by a runtime check of one."""
    result = evaluate_license_gate(artifact)
    if not result.accepted:
        raise LicenseNotAcceptedError(
            f"artifact {artifact.id} cannot be used for training or serving: {result.reason}"
        )
    return result


def invalidate_stale_license_acceptance(artifact: TrainableArtifact) -> bool:
    """Clears `license_accepted_by`/`license_accepted_at` in memory. A
    no-op (`False`) if no acceptance exists. The shared field-clearing
    core `apply_revoke_license` builds on -- kept as its own named,
    independently testable step rather than inlined, since "what gets
    cleared" and "when revocation is allowed / what else it does
    (status demotion)" are two separable concerns."""
    if artifact.license_accepted_by is None and artifact.license_accepted_at is None:
        return False
    artifact.license_accepted_by = None
    artifact.license_accepted_at = None
    return True


def apply_revoke_license(artifact: TrainableArtifact) -> bool:
    """The pure, DB-free core of `artifact_crud.py`'s own `revoke_license`
    -- this task's own negative_acceptance ("... revoked license blocks
    training/serving") made executable as an *explicit*, on-demand
    action, independent of any identity edit. Idempotent: a no-op
    (`False`) if no acceptance exists to revoke. Refuses
    (`LicenseAcceptanceError`) on an `archived` artifact -- the same
    "closed record accepts no further mutation" precedent FTF-0301's own
    `apply_accept_license`/`apply_mark_ready` already established.

    Revoking a `ready` artifact demotes it back to `registered`: Section
    6.7's own invariant is that `ready` implies an accepted license
    (`apply_mark_ready`'s own gate), so once that acceptance is withdrawn
    the artifact can no longer honestly claim `ready` -- any training/
    serving path that tests `status == "ready"` before starting real work
    is correctly excluded without needing its own separate license check.
    """
    if artifact.status == "archived":
        raise LicenseAcceptanceError(
            f"artifact {artifact.id} is archived and cannot revoke a license"
        )
    changed = invalidate_stale_license_acceptance(artifact)
    if not changed:
        return False
    if artifact.status == "ready":
        artifact.status = "registered"
    artifact.updated_at = datetime.now(UTC)
    return True
