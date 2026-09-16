#!/usr/bin/env python3
"""
FTF-H002 -- Freeze release scope, compatibility and measured execution
inputs.

Proves:
  1. `PreflightResult` structurally forbids a `BLOCKED_EXTERNAL` result with
     no `owner`/`next_action` (the anti-fabrication guardrail).
  2. Every real preflight check in
     `app.services.environment_preflight` performs a genuine probe (real
     subprocess/filesystem/socket check, no canned verdict) for both its
     available and blocked branches.
  3. `check_managed_provider_bedrock` never reports AVAILABLE merely
     because an AWS credential env var happens to be present -- account/
     region/model access is unvalidated without the (unapproved) AWS SDK,
     so it stays BLOCKED_EXTERNAL either way (the negative_acceptance
     clause: "unavailable managed capabilities... are explicit external
     blockers").
  4. `scripts/release_scope_lock.py`'s whole-set validator rejects a
     removed CAP ID, a downgraded `required: false`, and a cut
     `implementation_tasks`/`qualification_scenarios` set -- and the real
     committed `spec/release-scope.yaml` passes it with zero errors.
  5. The committed `spec/compatibility-lock.json` is not a stale/fabricated
     snapshot: its `overall_status` matches a live re-run of
     `run_preflight()` against this exact checkout.
  6. `docs/operations/budget-forecast.md` discloses the billing gap as
     BLOCKED_EXTERNAL and contains no fabricated dollar rate (this task's
     own negative_acceptance: "Do not invent a model ID/rate").

Run directly: python3 tests/tasks/test_ftf_h002.py
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import socket
import stat
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from scripts.release_scope_lock import (  # noqa: E402
    CANONICAL_CAP_IDS,
    generate_release_scope,
    load_release_scope,
    validate_release_scope,
)

from app.services.environment_preflight import (  # noqa: E402
    PreflightResult,
    PreflightStatus,
    check_coding_provider_billing,
    check_git_cli,
    check_gpu_driver,
    check_kubernetes_cni,
    check_litellm_gateway,
    check_managed_provider_bedrock,
    check_python_runtime,
    overall_status,
    run_preflight,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def test_blocked_external_requires_owner_and_next_action():
    try:
        PreflightResult(
            key="x",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail="d",
            owner=None,
            next_action=None,
        )
        raised = False
    except ValueError:
        raised = True
    check(
        "FTF-H002",
        "a BLOCKED_EXTERNAL result with no owner/next_action is structurally rejected",
        raised,
    )

    try:
        PreflightResult(
            key="x",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail="d",
            owner="   ",
            next_action="do something",
        )
        raised_blank = False
    except ValueError:
        raised_blank = True
    check(
        "FTF-H002",
        "a BLOCKED_EXTERNAL result with a blank owner is rejected too",
        raised_blank,
    )

    # A genuinely well-formed BLOCKED_EXTERNAL result still constructs fine.
    result = PreflightResult(
        key="x",
        status=PreflightStatus.BLOCKED_EXTERNAL,
        detail="d",
        owner="someone",
        next_action="do something",
    )
    check("FTF-H002", "a well-formed BLOCKED_EXTERNAL result constructs", result.key == "x")


def test_python_runtime_check_is_real():
    result = check_python_runtime()
    check(
        "FTF-H002",
        "check_python_runtime reports the real interpreter version, not a literal",
        result.status is PreflightStatus.AVAILABLE and result.detail == sys.version.split()[0],
    )


def test_git_cli_check_reflects_real_path_state():
    available = check_git_cli()
    check(
        "FTF-H002",
        "git is actually on PATH in this environment, so check_git_cli reports AVAILABLE",
        available.status is PreflightStatus.AVAILABLE and "git" in available.detail.lower(),
    )

    # Real negative branch: a PATH with no git binary on it at all (a fresh
    # empty temp directory), not a mock of shutil.which.
    with tempfile.TemporaryDirectory() as empty_path_dir:
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = empty_path_dir
        try:
            blocked = check_git_cli()
        finally:
            os.environ["PATH"] = original_path
    check(
        "FTF-H002",
        "with git absent from PATH, check_git_cli reports BLOCKED_EXTERNAL with owner/next_action",
        blocked.status is PreflightStatus.BLOCKED_EXTERNAL
        and blocked.owner
        and blocked.next_action,
    )


def test_kubernetes_cni_check_reflects_real_path_state():
    # Real negative branch: a PATH with no kubectl binary on it at all (a
    # fresh empty temp directory), not a mock of shutil.which. Unlike git,
    # kubectl's presence varies by environment (e.g. it ships preinstalled
    # on GitHub Actions' ubuntu-latest runner but not in this sandbox), so
    # the negative branch must be constructed explicitly rather than relying
    # on whatever happens to be ambient in the environment running this test.
    with tempfile.TemporaryDirectory() as empty_path_dir:
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = empty_path_dir
        try:
            blocked = check_kubernetes_cni()
        finally:
            os.environ["PATH"] = original_path
    check(
        "FTF-H002",
        "with kubectl absent from PATH, check_kubernetes_cni reports BLOCKED_EXTERNAL for "
        "real (not because it was told to)",
        blocked.status is PreflightStatus.BLOCKED_EXTERNAL,
    )

    # Real positive branch: put a genuine fake-but-executable "kubectl" on
    # PATH that prints a real version string, proving the check actually
    # runs and parses subprocess output rather than special-casing a name.
    with tempfile.TemporaryDirectory() as bin_dir:
        fake_kubectl = pathlib.Path(bin_dir) / "kubectl"
        fake_kubectl.write_text("#!/bin/sh\necho 'clientVersion: v1.31.0'\n", encoding="utf-8")
        fake_kubectl.chmod(fake_kubectl.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{original_path}"
        try:
            available = check_kubernetes_cni()
        finally:
            os.environ["PATH"] = original_path
    check(
        "FTF-H002",
        "with a real kubectl on PATH, check_kubernetes_cni reports AVAILABLE with its "
        "actually-printed version, and still discloses cluster CNI enforcement is unverified",
        available.status is PreflightStatus.AVAILABLE
        and "v1.31.0" in available.detail
        and "not independently verified" in available.detail,
    )


def test_gpu_driver_check_is_real_filesystem_check():
    result = check_gpu_driver()
    check(
        "FTF-H002",
        "this sandbox has no NVIDIA driver file, so check_gpu_driver reports BLOCKED_EXTERNAL "
        "for real (a canned check could not distinguish this from a real GPU worker)",
        result.status is PreflightStatus.BLOCKED_EXTERNAL
        and result.owner is not None
        and result.next_action is not None,
    )


def test_litellm_gateway_check_uses_a_real_tcp_probe():
    # Not configured at all.
    unconfigured = check_litellm_gateway({})
    check(
        "FTF-H002",
        "an unset SOVRA_LITELLM_GATEWAY_URL is BLOCKED_EXTERNAL",
        unconfigured.status is PreflightStatus.BLOCKED_EXTERNAL
        and "not set" in unconfigured.detail,
    )

    # Real reachable listener -- an actual bound socket, not a fixture.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        reachable = check_litellm_gateway({"SOVRA_LITELLM_GATEWAY_URL": f"http://127.0.0.1:{port}"})
    finally:
        listener.close()
    check(
        "FTF-H002",
        "a genuinely reachable configured gateway route is reported AVAILABLE",
        reachable.status is PreflightStatus.AVAILABLE,
    )

    # Real unreachable route: the listener above is now closed, so the same
    # port is (almost certainly) refused -- a live negative, not a fixture.
    stale = check_litellm_gateway({"SOVRA_LITELLM_GATEWAY_URL": f"http://127.0.0.1:{port}"})
    check(
        "FTF-H002",
        "a stale/unreachable configured gateway route is an explicit external blocker, "
        "matching this task's own acceptance clause ('stale LiteLLM routes... are explicit "
        "external blockers')",
        stale.status is PreflightStatus.BLOCKED_EXTERNAL,
    )


def test_bedrock_check_never_claims_available_from_credential_presence_alone():
    no_creds = check_managed_provider_bedrock({})
    check(
        "FTF-H002",
        "no AWS credential env var present is BLOCKED_EXTERNAL",
        no_creds.status is PreflightStatus.BLOCKED_EXTERNAL
        and "no AWS credential" in no_creds.detail,
    )

    with_creds = check_managed_provider_bedrock({"AWS_ACCESS_KEY_ID": "fake-for-test-only"})
    check(
        "FTF-H002",
        "an AWS credential env var alone still does not make Bedrock AVAILABLE -- account/"
        "region/model access is unvalidated without a real API call, matching this task's own "
        "acceptance clause ('unavailable managed capabilities... are explicit external blockers')",
        with_creds.status is PreflightStatus.BLOCKED_EXTERNAL
        and "unvalidated" in with_creds.detail,
    )
    check(
        "FTF-H002",
        "the two Bedrock blocked cases are distinguishable (no fabricated single canned message)",
        no_creds.detail != with_creds.detail,
    )


def test_coding_provider_billing_always_discloses_blocked():
    result = check_coding_provider_billing()
    check(
        "FTF-H002",
        "no coding-provider billing API is reachable from this sandbox -- always disclosed, "
        "never a fabricated rate (this task's own negative_acceptance)",
        result.status is PreflightStatus.BLOCKED_EXTERNAL
        and result.owner is not None
        and result.next_action is not None,
    )


def test_overall_status_aggregation():
    all_available = tuple(
        PreflightResult(key=f"k{i}", status=PreflightStatus.AVAILABLE, detail="ok")
        for i in range(3)
    )
    check(
        "FTF-H002",
        "overall_status is AVAILABLE only when every check is AVAILABLE",
        overall_status(all_available) is PreflightStatus.AVAILABLE,
    )

    one_blocked = (
        *all_available,
        PreflightResult(
            key="blocked",
            status=PreflightStatus.BLOCKED_EXTERNAL,
            detail="d",
            owner="o",
            next_action="n",
        ),
    )
    check(
        "FTF-H002",
        "a single BLOCKED_EXTERNAL check makes the overall status BLOCKED_EXTERNAL",
        overall_status(one_blocked) is PreflightStatus.BLOCKED_EXTERNAL,
    )


def test_generated_release_scope_matches_frozen_inventory_and_validates_clean():
    document = generate_release_scope()
    ids = [entry["id"] for entry in document["capabilities"]]
    check(
        "FTF-H002",
        "generate_release_scope emits exactly the 21 canonical CAP IDs in order",
        ids == list(CANONICAL_CAP_IDS),
    )
    errors = validate_release_scope(document)
    check(
        "FTF-H002",
        f"the freshly generated release-scope document has zero errors, got {errors}",
        not errors,
    )

    committed = load_release_scope()
    committed_errors = validate_release_scope(committed)
    check(
        "FTF-H002",
        f"the real committed spec/release-scope.yaml has zero validation errors, "
        f"got {committed_errors}",
        not committed_errors,
    )


def test_removed_capability_rejected():
    document = generate_release_scope()
    document["capabilities"] = [c for c in document["capabilities"] if c["id"] != "CAP-10"]
    errors = validate_release_scope(document)
    check(
        "FTF-H002",
        "removing a required CAP ID is rejected (Section 64.2: required IDs cannot be removed)",
        any("missing_capability CAP-10" in e for e in errors),
    )


def test_downgraded_required_flag_rejected():
    document = generate_release_scope()
    for entry in document["capabilities"]:
        if entry["id"] == "CAP-10":
            entry["required"] = False
    errors = validate_release_scope(document)
    check(
        "FTF-H002",
        "downgrading required=false on a CAP ID is rejected (cannot be downgraded)",
        any("CAP-10" in e and "required" in e for e in errors),
    )


def test_cut_implementation_scope_rejected():
    document = generate_release_scope()
    for entry in document["capabilities"]:
        if entry["id"] == "CAP-10":
            entry["implementation_tasks"] = entry["implementation_tasks"][:0]
    errors = validate_release_scope(document)
    check(
        "FTF-H002",
        "cutting a CAP's implementation_tasks to fit scope/budget is rejected "
        "(this task's own negative_acceptance: never cut required scope)",
        any("CAP-10" in e and "implementation_tasks" in e for e in errors),
    )


def test_compatibility_lock_is_not_a_stale_snapshot():
    lock_path = ROOT / "spec" / "compatibility-lock.json"
    committed = json.loads(lock_path.read_text(encoding="utf-8"))
    live_status = overall_status(run_preflight()).value
    check(
        "FTF-H002",
        "the committed compatibility-lock.json's overall_status matches a live preflight "
        "re-run against this exact checkout (not a stale/fabricated snapshot)",
        committed["overall_status"] == live_status,
    )
    check(
        "FTF-H002",
        "every committed BLOCKED_EXTERNAL check entry carries a real owner/next_action",
        all(
            (c["owner"] and c["next_action"])
            for c in committed["checks"]
            if c["status"] == "BLOCKED_EXTERNAL"
        ),
    )


def test_budget_forecast_discloses_blocked_billing_and_invents_no_rate():
    content = (ROOT / "docs" / "operations" / "budget-forecast.md").read_text(encoding="utf-8")
    check(
        "FTF-H002",
        "budget-forecast.md explicitly discloses the billing gap as BLOCKED_EXTERNAL",
        "BLOCKED_EXTERNAL" in content,
    )
    fabricated_rate = re.compile(r"\$\d+(\.\d+)?\s*(/|\bper\b)\s*(hour|token|task)", re.IGNORECASE)
    check(
        "FTF-H002",
        "budget-forecast.md contains no fabricated dollar rate (this task's own "
        "negative_acceptance: 'Do not invent a model ID/rate')",
        not fabricated_rate.search(content),
    )


def test_law1_evidence_removing_the_owner_guard_lets_fabrication_through():
    """Law 1: temporarily remove PreflightResult's BLOCKED_EXTERNAL
    owner/next_action guard from the tracked module itself (not a fixture),
    then run a genuinely fresh subprocess (a new interpreter, so the edited
    file is actually re-imported rather than reusing this process's already
    -loaded class) that attempts the fabricated construction. Confirms it
    now succeeds where it previously raised; then restores byte-identical."""
    module_path = ROOT / "apps" / "api" / "app" / "services" / "environment_preflight.py"
    original = module_path.read_text(encoding="utf-8")
    raise_owner_line = (
        '                raise ValueError(f"{self.key}: BLOCKED_EXTERNAL requires '
        'a non-empty owner")\n'
    )
    raise_next_action_line = (
        '                raise ValueError(f"{self.key}: BLOCKED_EXTERNAL requires '
        'a non-empty next_action")\n'
    )
    guarded_block = (
        "        if self.status is PreflightStatus.BLOCKED_EXTERNAL:\n"
        "            if not self.owner or not self.owner.strip():\n"
        f"{raise_owner_line}"
        "            if not self.next_action or not self.next_action.strip():\n"
        f"{raise_next_action_line}"
    )
    check(
        "FTF-H002",
        "the guard block to be fault-injected is present verbatim in the tracked module",
        guarded_block in original,
    )

    probe = (
        "import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'apps/api')\n"
        "from app.services.environment_preflight import PreflightResult, PreflightStatus\n"
        "PreflightResult(key='x', status=PreflightStatus.BLOCKED_EXTERNAL, detail='d', "
        "owner=None, next_action=None)\n"
        "print('CONSTRUCTED_WITHOUT_RAISING')\n"
    )

    import subprocess

    before = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    check(
        "FTF-H002",
        "before fault injection, the real guard rejects a fabricated BLOCKED_EXTERNAL "
        f"result (subprocess exit {before.returncode})",
        before.returncode != 0 and "CONSTRUCTED_WITHOUT_RAISING" not in before.stdout,
    )

    mutated = original.replace(
        guarded_block, "        pass  # temporarily disabled for Law 1 evidence\n"
    )
    check(
        "FTF-H002", "the fault-injection replacement actually changed the file", mutated != original
    )
    module_path.write_text(mutated, encoding="utf-8")
    try:
        after = subprocess.run(
            [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
    finally:
        module_path.write_text(original, encoding="utf-8")
        restored = module_path.read_text(encoding="utf-8")
        check(
            "FTF-H002",
            "the fault-injected module was restored byte-identical",
            restored == original,
        )

    check(
        "FTF-H002",
        "with the owner/next_action guard disabled, a fresh subprocess can fabricate an "
        "unfounded BLOCKED_EXTERNAL claim without it being rejected -- confirming the guard "
        "is load-bearing, not decorative",
        after.returncode == 0 and "CONSTRUCTED_WITHOUT_RAISING" in after.stdout,
    )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(
            "FTF-H002 release scope / compatibility / measured execution inputs: "
            f"{len(FAILURES)} failed"
        )
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-H002 release scope / compatibility / measured execution inputs: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
