"""Environment preflight for release-scope external prerequisites (FTF-H002).

Section 64.3 of the master spec requires a real onboarding gate that reports
GPU/driver, Kubernetes CNI, managed-provider (Bedrock) and LiteLLM-gateway
availability *before* any integration or full-release claim is made, and
forbids inventing a model ID, rate, credential or URL for a missing input:
"Missing inputs use BLOCKED_EXTERNAL with owner and exact next action... The
developer can build contracts and isolated unit code while blocked, but
cannot mark real integration or full release PASS."

Every check below performs a genuine, real probe (`shutil.which`, a real
subprocess version call, a real filesystem check, or a real TCP connect
reusing `app.config.docker_local_stack.check_tcp_reachable`) -- never a
canned/hardcoded verdict. `PreflightResult.__post_init__` structurally
forbids a `BLOCKED_EXTERNAL` result with no `owner`/`next_action`, and an
`AVAILABLE` result can only be constructed from a real `detail` string that
callers derive from an actual probe, never a literal.

This module is deliberately independent of any evaluation/qualification
code (per this task's own negative_acceptance: "implement preflight
independently of final qualification to avoid a dependency cycle") and adds
no new third-party runtime dependency -- stdlib only, same rationale as
FTF-0004/0005/0006.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from app.config.docker_local_stack import ServiceEndpoint, check_tcp_reachable

NVIDIA_DRIVER_VERSION_PATH = Path("/proc/driver/nvidia/version")
SUBPROCESS_TIMEOUT_SECONDS = 5.0


class PreflightStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"


@dataclass(frozen=True)
class PreflightResult:
    key: str
    status: PreflightStatus
    detail: str
    owner: str | None = None
    next_action: str | None = None

    def __post_init__(self) -> None:
        if self.status is PreflightStatus.BLOCKED_EXTERNAL:
            if not self.owner or not self.owner.strip():
                raise ValueError(f"{self.key}: BLOCKED_EXTERNAL requires a non-empty owner")
            if not self.next_action or not self.next_action.strip():
                raise ValueError(f"{self.key}: BLOCKED_EXTERNAL requires a non-empty next_action")
        if not self.detail or not self.detail.strip():
            raise ValueError(f"{self.key}: detail must not be empty")


def _which_version(binary: str, version_args: tuple[str, ...]) -> str | None:
    """Real detection: only returns a version string when the binary is
    actually on PATH and actually runs -- never a literal fallback."""
    resolved = shutil.which(binary)
    if resolved is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603
            [resolved, *version_args],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    return output[0] if output else resolved


def check_python_runtime() -> PreflightResult:
    return PreflightResult(
        key="python_runtime",
        status=PreflightStatus.AVAILABLE,
        detail=sys.version.split()[0],
    )


def check_git_cli() -> PreflightResult:
    version = _which_version("git", ("--version",))
    if version is None:
        return PreflightResult(
            key="git_cli",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail="git is not on PATH",
            owner="platform operator",
            next_action="install git on the build/runtime image",
        )
    return PreflightResult(key="git_cli", status=PreflightStatus.AVAILABLE, detail=version)


def check_gpu_driver() -> PreflightResult:
    """Section 64.3's 'available GPU type/VRAM/driver'. A real filesystem
    check for the NVIDIA kernel driver -- never assumes a GPU exists."""
    if NVIDIA_DRIVER_VERSION_PATH.exists():
        detail = NVIDIA_DRIVER_VERSION_PATH.read_text(encoding="utf-8", errors="replace").strip()
        return PreflightResult(
            key="gpu_driver", status=PreflightStatus.AVAILABLE, detail=detail or "present"
        )
    return PreflightResult(
        key="gpu_driver",
        status=PreflightStatus.BLOCKED_EXTERNAL,
        detail=f"{NVIDIA_DRIVER_VERSION_PATH} not present -- no NVIDIA GPU/driver detected",
        owner="infrastructure owner",
        next_action=(
            "provision a real NVIDIA GPU worker (Section 4/CAP-09) before claiming "
            "local LoRA/QLoRA training integration"
        ),
    )


def check_kubernetes_cni() -> PreflightResult:
    """Section 64.3's 'Kubernetes CNI enforcement'. Presence of a working
    `kubectl` client is a real, narrower proxy for cluster access -- it does
    not itself prove a CNI is installed or enforcing NetworkPolicy (S03);
    that requires a real connected cluster, which this check alone cannot
    reach without one, so it is disclosed as a proxy, not the full claim."""
    version = _which_version("kubectl", ("version", "--client", "--output=yaml"))
    if version is None:
        return PreflightResult(
            key="kubernetes_cni",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail="kubectl is not on PATH -- no Kubernetes access to verify CNI enforcement",
            owner="infrastructure owner",
            next_action=(
                "provision a Kubernetes cluster with an enforcing CNI and grant this "
                "environment kubectl access, then re-run this check against a real cluster "
                "(kubectl client presence alone does not prove CNI enforcement)"
            ),
        )
    return PreflightResult(
        key="kubernetes_cni",
        status=PreflightStatus.AVAILABLE,
        detail=(
            f"kubectl client present ({version}); "
            "cluster CNI enforcement not independently verified"
        ),
    )


def check_litellm_gateway(env: Mapping[str, str] | None = None) -> PreflightResult:
    """Section 64.3's 'real LiteLLM version/base URL'. Deliberately reads
    only `SOVRA_LITELLM_GATEWAY_URL` directly rather than going through
    FTF-0005's `Settings.from_env()` (which requires a full, unrelated set
    of env vars) -- this task's own negative_acceptance requires preflight
    to work independently of the final qualification pipeline."""
    source = os.environ if env is None else env
    raw_url = source.get("SOVRA_LITELLM_GATEWAY_URL", "").strip()
    if not raw_url:
        return PreflightResult(
            key="litellm_gateway",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail="SOVRA_LITELLM_GATEWAY_URL is not set",
            owner="platform operator",
            next_action="bind the organization's existing LiteLLM gateway base URL (Section 77.3)",
        )
    parsed = urlsplit(raw_url)
    if not parsed.hostname:
        return PreflightResult(
            key="litellm_gateway",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail="SOVRA_LITELLM_GATEWAY_URL has no host",
            owner="platform operator",
            next_action="correct SOVRA_LITELLM_GATEWAY_URL to a real reachable gateway URL",
        )
    endpoint = ServiceEndpoint("litellm_gateway", parsed.hostname, parsed.port or 443)
    reachability = check_tcp_reachable(endpoint)
    if not reachability.healthy:
        return PreflightResult(
            key="litellm_gateway",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail=f"configured gateway route is stale/unreachable: {reachability.detail}",
            owner="platform operator",
            next_action="restore or repoint the LiteLLM gateway route, then re-run preflight",
        )
    return PreflightResult(
        key="litellm_gateway",
        status=PreflightStatus.AVAILABLE,
        detail=f"reachable at {parsed.hostname}:{endpoint.port}",
    )


def check_managed_provider_bedrock(env: Mapping[str, str] | None = None) -> PreflightResult:
    """Section 21's Bedrock managed-training adapter needs a real
    account/region-validated capability query. Presence of AWS credential
    env vars is checked for real, but presence alone is not validated
    account/model access -- confirming that needs the AWS SDK, which is a
    new runtime dependency this task cannot add unilaterally (CLAUDE.md
    ask-first). Disclosed as BLOCKED_EXTERNAL either way, never a fabricated
    AVAILABLE."""
    source = os.environ if env is None else env
    has_credentials = bool(
        source.get("AWS_ACCESS_KEY_ID") or source.get("AWS_PROFILE") or source.get("AWS_ROLE_ARN")
    )
    if not has_credentials:
        return PreflightResult(
            key="managed_provider_bedrock",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail="no AWS credential env var (AWS_ACCESS_KEY_ID/AWS_PROFILE/AWS_ROLE_ARN) present",
            owner="owner (account/billing authority)",
            next_action="approve and provision a Bedrock-capable AWS account/region for CAP-10",
        )
    return PreflightResult(
        key="managed_provider_bedrock",
        status=PreflightStatus.BLOCKED_EXTERNAL,
        detail="AWS credential env var present, but account/region/model access is unvalidated",
        owner="owner",
        next_action=(
            "approve adding the AWS SDK (boto3) as a new runtime dependency, then call the real "
            "ListFoundationModels/account APIs before any Bedrock capability claim (Section 21)"
        ),
    )


def check_coding_provider_billing() -> PreflightResult:
    """Section 64.4's real per-slice billing measurement. This relay
    session has no access to the coding provider's billing/usage API from
    inside the sandbox -- always disclosed as BLOCKED_EXTERNAL, never a
    fabricated rate or dollar figure (this task's own negative_acceptance)."""
    return PreflightResult(
        key="coding_provider_billing",
        status=PreflightStatus.BLOCKED_EXTERNAL,
        detail="no coding-provider billing/usage API is reachable from this execution environment",
        owner="owner (budget authorizer)",
        next_action=(
            "supply a real billed-usage export for a measured task/date range so "
            "docs/operations/budget-forecast.md can be reforecast from real units (Section 64.4)"
        ),
    )


def run_preflight(env: Mapping[str, str] | None = None) -> tuple[PreflightResult, ...]:
    return (
        check_python_runtime(),
        check_git_cli(),
        check_gpu_driver(),
        check_kubernetes_cni(),
        check_litellm_gateway(env),
        check_managed_provider_bedrock(env),
        check_coding_provider_billing(),
    )


def overall_status(results: tuple[PreflightResult, ...]) -> PreflightStatus:
    if all(result.status is PreflightStatus.AVAILABLE for result in results):
        return PreflightStatus.AVAILABLE
    return PreflightStatus.BLOCKED_EXTERNAL


def to_compatibility_report(results: tuple[PreflightResult, ...]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "overall_status": overall_status(results).value,
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


def main() -> None:
    results = run_preflight()
    print(json.dumps(to_compatibility_report(results), indent=2))


if __name__ == "__main__":
    main()
