"""docs/operations/releasequalificationworkflow.py -- FTF-1810
ReleaseQualificationWorkflow.

Section 54 requires the authoritative Q01-Q43 expected-scenario aggregator
to run "on exact release" and "bind result to current scope/hash/image/
profile." Section 73.4/52 require the aggregator to fail closed on a
missing, skipped, stale (wrong-revision), unsigned, tampered or incomplete
scenario report -- never treat any of those as authorizing release. This
module is the real, fail-closed binder: it never fabricates a PASS for a
scenario it cannot verify, following the exact `GateResult`/`GateStatus`
BLOCKED_EXTERNAL-never-fabricated-PASS discipline FTF-H002's
`PreflightResult` and FTF-H028's `release_evidence_gate.py` already
established in this repository.

Real state today (not simulated): `spec/qualification-inventory.json`'s 43
scenarios are all `NOT_TESTED` (no product yet exists to produce real
evidence), `spec/release-scope.yaml` has every CAP's
`supported_version_profile` at `NOT_YET_RESOLVED` (Section 64.3's
environment-discovery onboarding gate has not run), and no
`07-container-build.yml` workflow exists yet to produce a real image
digest. Running this workflow against the real current repository
therefore correctly reports every scenario `missing` and both the image
and profile bindings `BLOCKED_EXTERNAL` -- this is the honest state, not a
defect in this module, matching Law 5 ("existence is not evidence").

Per-scenario evidence lives in `spec/qualification-evidence.json` (a ledger
keyed by scenario ID; absent/empty until a real scenario run records an
entry there) -- a separate file from the frozen
`spec/qualification-inventory.json` catalogue so a scenario's catalogue
entry is never conflated with a live evidence claim about a specific
release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.release_evidence_gate import GateResult, GateStatus  # noqa: E402
from scripts.release_evidence_gate import overall_status as _overall_status  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIOS_FILE = REPO_ROOT / "spec" / "qualification-inventory.json"
RELEASE_SCOPE_FILE = REPO_ROOT / "spec" / "release-scope.yaml"
EVIDENCE_FILE = REPO_ROOT / "spec" / "qualification-evidence.json"

SignatureVerifier = Callable[[str, str], bool]

overall_status = _overall_status


def current_commit_sha(cwd: Path = REPO_ROOT) -> str:
    """The real current HEAD -- never a caller-asserted string."""
    result = subprocess.run(  # noqa: S603, S607
        ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class ReleaseBinding:
    """The exact scope/hash/image/profile this qualification run is bound
    to. `image_digest`/`deployment_profile` are `None` when that real input
    does not exist yet -- never a placeholder string standing in for one."""

    scope_digest: str
    commit_sha: str
    image_digest: str | None
    deployment_profile: str | None


def resolve_release_binding(
    *,
    commit_sha: str,
    release_scope_path: Path = RELEASE_SCOPE_FILE,
    image_digest: str | None = None,
    deployment_profile: str | None = None,
) -> ReleaseBinding:
    scope_digest = hashlib.sha256(release_scope_path.read_bytes()).hexdigest()
    return ReleaseBinding(
        scope_digest=scope_digest,
        commit_sha=commit_sha,
        image_digest=image_digest,
        deployment_profile=deployment_profile,
    )


def load_required_scenarios(path: Path = SCENARIOS_FILE) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [s for s in data["scenarios"] if s.get("required", True)]


def load_evidence_ledger(path: Path = EVIDENCE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_scenario_evidence(
    scenario_id: str,
    entry: Mapping[str, Any] | None,
    *,
    binding: ReleaseBinding,
    verifier: SignatureVerifier | None,
) -> GateResult:
    """Every branch below is a distinct, real rejection reason -- Section
    73.4/52's exact list: missing, skipped, stale, incomplete, unsigned,
    tampered. A scenario reaches PASS only through a genuinely verified
    signature over exact-revision-bound evidence."""
    key = f"scenario_{scenario_id}"

    if entry is None:
        return GateResult(
            key=key,
            status=GateStatus.FAIL,
            detail=f"{scenario_id}: missing -- no evidence recorded for this release",
        )

    if str(entry.get("status", "")).upper() == "SKIPPED" or bool(entry.get("skipped")):
        return GateResult(
            key=key,
            status=GateStatus.FAIL,
            detail=f"{scenario_id}: skipped -- a skipped scenario cannot authorize release",
        )

    required_fields = ("evidence_id", "commit", "exit_code")
    missing_fields = [f for f in required_fields if entry.get(f) in (None, "")]
    if missing_fields:
        return GateResult(
            key=key,
            status=GateStatus.FAIL,
            detail=f"{scenario_id}: incomplete -- missing field(s) {missing_fields}",
        )

    if entry.get("commit") != binding.commit_sha:
        return GateResult(
            key=key,
            status=GateStatus.FAIL,
            detail=(
                f"{scenario_id}: stale -- evidence commit {entry.get('commit')!r} does not "
                f"match release commit {binding.commit_sha!r}"
            ),
        )

    if entry.get("exit_code") != 0:
        return GateResult(
            key=key,
            status=GateStatus.FAIL,
            detail=f"{scenario_id}: failed -- recorded exit_code {entry.get('exit_code')!r} != 0",
        )

    signature = str(entry.get("signature") or "").strip()
    if not signature:
        return GateResult(
            key=key,
            status=GateStatus.FAIL,
            detail=f"{scenario_id}: unsigned -- an unsigned scenario report cannot pass",
        )

    if verifier is None:
        return GateResult(
            key=key,
            status=GateStatus.BLOCKED_EXTERNAL,
            detail=(
                f"{scenario_id}: a signature is present but no trusted verification "
                "service/key is configured -- presence of a signature is never treated as "
                "validity"
            ),
            owner="owner (release-signing authority)",
            next_action=(
                "provision the Section 73.3 approved workload identity/signing service and its "
                "trusted verification key before this scenario's evidence can authorize release"
            ),
        )

    payload = json.dumps(
        {
            "scenario_id": scenario_id,
            "commit": entry.get("commit"),
            "evidence_id": entry.get("evidence_id"),
        },
        sort_keys=True,
    )
    if not verifier(payload, signature):
        return GateResult(
            key=key,
            status=GateStatus.FAIL,
            detail=(
                f"{scenario_id}: tampered -- signature verification failed against the "
                "configured trusted key"
            ),
        )

    return GateResult(
        key=key,
        status=GateStatus.PASS,
        detail=f"{scenario_id}: real, exact-revision, signed evidence verified",
    )


def run_release_qualification_workflow(
    *,
    binding: ReleaseBinding,
    scenarios: list[dict[str, Any]] | None = None,
    evidence: Mapping[str, Any] | None = None,
    verifier: SignatureVerifier | None = None,
) -> tuple[GateResult, ...]:
    """The authoritative Q01-Q43 aggregator, bound to `binding`'s exact
    scope/hash/image/profile. Missing/skipped/stale/unsigned/tampered/
    incomplete evidence for any required scenario, or a missing image/
    profile binding, keeps the whole result out of PASS -- never
    fabricated (Section 73.4 Q34: "no open release blockers")."""
    scenarios = scenarios if scenarios is not None else load_required_scenarios()
    evidence = evidence if evidence is not None else load_evidence_ledger()

    results: list[GateResult] = []

    if binding.image_digest is None:
        results.append(
            GateResult(
                key="release_image_binding",
                status=GateStatus.BLOCKED_EXTERNAL,
                detail=(
                    "no container image build pipeline exists yet to produce a real digest "
                    "for this release"
                ),
                owner="platform operator",
                next_action=(
                    "build the Section 52 07-container-build.yml workflow and re-run this "
                    "workflow bound to its real produced image digest"
                ),
            )
        )
    if binding.deployment_profile is None:
        results.append(
            GateResult(
                key="release_profile_binding",
                status=GateStatus.BLOCKED_EXTERNAL,
                detail=(
                    "Section 64.3 environment-discovery onboarding gate has not resolved a "
                    "supported_version_profile for the target environment yet"
                ),
                owner="platform operator",
                next_action=(
                    "run the Section 64.3 onboarding gate for the target environment and "
                    "re-run this workflow bound to its resolved deployment profile"
                ),
            )
        )

    for scenario in scenarios:
        scenario_id = scenario["id"]
        results.append(
            evaluate_scenario_evidence(
                scenario_id, evidence.get(scenario_id), binding=binding, verifier=verifier
            )
        )

    return tuple(results)


def to_report(binding: ReleaseBinding, results: tuple[GateResult, ...]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "binding": {
            "scope_digest": binding.scope_digest,
            "commit_sha": binding.commit_sha,
            "image_digest": binding.image_digest,
            "deployment_profile": binding.deployment_profile,
        },
        "overall_status": overall_status(results).value,
        "scenario_count": len(results),
        "checks": [
            {
                "key": r.key,
                "status": r.status.value,
                "detail": r.detail,
                "owner": r.owner,
                "next_action": r.next_action,
            }
            for r in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", default=None, help="release commit SHA (defaults to real HEAD)")
    parser.add_argument("--image-digest", default=None)
    parser.add_argument("--deployment-profile", default=None)
    args = parser.parse_args(argv)

    sha = args.sha or current_commit_sha()
    binding = resolve_release_binding(
        commit_sha=sha,
        image_digest=args.image_digest,
        deployment_profile=args.deployment_profile,
    )
    results = run_release_qualification_workflow(binding=binding)
    report = to_report(binding, results)
    print(json.dumps(report, indent=2))
    return 0 if report["overall_status"] == GateStatus.PASS.value else 1


if __name__ == "__main__":
    sys.exit(main())
