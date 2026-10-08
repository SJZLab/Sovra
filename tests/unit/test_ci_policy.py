"""Unit tests for scripts/ci_policy.py and its wiring in .github/workflows/ci.yml."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import ci_policy as cp  # noqa: E402

CI = ROOT / ".github" / "workflows" / "ci.yml"


# --- classify ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "files",
    [
        ["QUEUE.md"],
        ["PROGRESS.md", "docs/certifications/ledger.md", "docs/certifications/queue.md"],
        ["docs/spec-matrix.yaml", "spec/tasks.json", "docs/implementation/TRACKER.json"],
        ["docs/progress/2026-10-07T0611Z-laneC.md", "docs/certifications/submissions/x.md"],
        [],
    ],
)
def test_tracker_only_change_sets_are_tracker_scope(files):
    assert cp.classify_files(files)[0] == "tracker"


@pytest.mark.parametrize(
    "files",
    [
        ["apps/api/app/main.py"],
        ["QUEUE.md", "workers/evaluation/tool_sequence_grader.py"],
        ["docs/certifications/mutation-allowlist.yaml"],
        ["docs/operations/litellm_outage_runbook.md"],
        ["pyproject.toml"],
        ["uv.lock"],
        [".github/workflows/ci.yml"],
        ["scripts/ci_policy.py"],
        ["docs/spec-matrix.yaml.bak"],
        ["QUEUE.md.orig"],
        ["tests/integration/test_ftf_0902_network_policy_enforcement.py"],
    ],
)
def test_anything_else_is_full_scope(files):
    scope, reason = cp.classify_files(files)
    assert scope == "full"
    assert "non-tracker" in reason


def test_classify_errors_fall_back_to_full(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.chdir(tmp_path)  # not a git repository: git diff fails
    assert cp.main(["classify", "--base", "origin/main"]) == 0
    assert "scope=full" in out.read_text()
    assert "classification error" in capsys.readouterr().out


def test_non_pull_request_events_are_full(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert cp.main(["classify", "--base", "origin/main", "--event", "workflow_dispatch"]) == 0
    assert "scope=full" in out.read_text()


def test_classify_reads_the_real_merge_base_diff(tmp_path, monkeypatch):
    def run(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "QUEUE.md").write_text("a\n")
    run("add", ".")
    run("commit", "-qm", "base")
    run("checkout", "-qb", "pr")
    (tmp_path / "QUEUE.md").write_text("b\n")
    run("commit", "-qam", "tracker edit")
    monkeypatch.chdir(tmp_path)
    assert cp.changed_files("main") == ["QUEUE.md"]
    assert cp.classify_files(cp.changed_files("main"))[0] == "tracker"
    (tmp_path / "app.py").write_text("x = 1\n")
    run("add", ".")
    run("commit", "-qm", "code")
    assert cp.classify_files(cp.changed_files("main"))[0] == "full"


# --- relay policy -----------------------------------------------------------------------


def _v(
    title="[relay] FTF-0608-F1: tests",
    number=10,
    files=("tests/tasks/test_ftf_0608.py",),
    diff="",
    prs=(),
):
    return cp.relay_violations(title, number, list(files), diff, list(prs))


def test_compliant_relay_pr_has_no_violations():
    assert _v() == []


def test_empty_claim_commit_is_allowed():
    assert _v(files=()) == []


@pytest.mark.parametrize(
    "path",
    [
        "PROGRESS.md",
        "docs/certifications/queue.md",
        "docs/certifications/ledger.md",
        "docs/certifications/mutation-allowlist.yaml",
        "CLAUDE.md",
        ".github/workflows/release-gate.yml",
        "scripts/ci_policy.py",
        "scripts/mutation_gate.py",
    ],
)
def test_relay_may_not_touch_control_plane_files(path):
    problems = _v(files=("tests/tasks/test_ftf_0608.py", path))
    assert len(problems) == 1 and path in problems[0]


BASE_CI = """name: CI
on: [pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - name: lint
        run: ruff check .
      - name: proof A
        run: python3 tests/tasks/test_a.py
  db-integration:
    needs: classify
    runs-on: ubuntu-latest
    steps:
      - run: python3 tests/integration/test_db.py
  ci-verdict:
    if: ${{ !cancelled() }}
    needs:
      - gate
      - db-integration
    runs-on: ubuntu-latest
    steps:
      - run: verdict
"""


def _ci(**edits):
    text = BASE_CI
    for old, new in edits.values():
        assert old in text
        text = text.replace(old, new, 1)
    return text


def test_relay_may_append_steps_jobs_and_verdict_needs():
    head = _ci(
        step=(
            "        run: python3 tests/tasks/test_a.py\n",
            "        run: python3 tests/tasks/test_a.py\n      - name: proof B\n"
            "        run: python3 tests/tasks/test_b.py\n",
        ),
        job=(
            "  ci-verdict:\n",
            "  new-integration:\n    needs: classify\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: python3 tests/integration/test_new.py\n  ci-verdict:\n",
        ),
        needs=("      - db-integration\n", "      - db-integration\n      - new-integration\n"),
    )
    assert cp.ci_yml_violations(BASE_CI, head) == []
    files = ("tests/tasks/test_b.py", ".github/workflows/ci.yml")
    assert cp.relay_violations("[relay] FTF-1: x", 5, list(files), "", [], (BASE_CI, head)) == []


@pytest.mark.parametrize(
    "old,new,what",
    [
        ("        run: ruff check .\n", "        run: ruff check . || true\n", "changed"),
        ("      - name: lint\n        run: ruff check .\n", "", "changed or removed"),
        (
            "        run: ruff check .\n",
            "        run: ruff check .\n        continue-on-error: true\n",
            "does not start a new step",
        ),
        (
            "      - name: proof A\n",
            "      - name: skip\n        run: exit 0\n        if: false\n      - name: proof A\n",
            None,
        ),
        ("    needs: classify\n", "    needs: classify\n    if: false\n", "does not start"),
        ("on: [pull_request]\n", "on: [push]\n", "header"),
        ("  db-integration:\n    needs: classify\n    runs-on: ubuntu-latest\n", "", "removed"),
    ],
)
def test_relay_may_not_edit_or_remove_existing_ci(old, new, what):
    problems = cp.ci_yml_violations(BASE_CI, BASE_CI.replace(old, new, 1))
    if what is None:  # inserting a whole new step before an existing one is additive
        assert problems == []
    else:
        assert problems and what in " ".join(problems), problems


def test_step_inserted_inside_an_existing_step_is_rejected():
    head = BASE_CI.replace(
        "      - name: lint\n        run: ruff check .\n",
        "      - name: lint\n      - name: injected\n        run: true\n"
        "        run: ruff check .\n",
        1,
    )
    assert any("inside an existing step" in p for p in cp.ci_yml_violations(BASE_CI, head))


def test_relay_ci_yml_change_without_texts_is_rejected():
    assert any(
        "not provided" in p
        for p in cp.relay_violations("[relay] FTF-1: x", 5, [".github/workflows/ci.yml"], "", [])
    )


def test_relay_may_park_in_queue_md():
    assert _v(files=("QUEUE.md",)) == []


def test_relay_may_not_certify():
    diff = "@@ -1 +1 @@\n-  certification_state: PROVISIONAL\n+  certification_state: CERTIFIED\n"
    assert any("Law 4" in p for p in _v(files=("docs/spec-matrix.yaml",), diff=diff))
    diff2 = "+  proof_state: L2-CERTIFIED\n"
    assert any("Law 4" in p for p in _v(files=("docs/spec-matrix.yaml",), diff=diff2))
    assert _v(files=("docs/spec-matrix.yaml",), diff="+  certification_state: PROVISIONAL\n") == []


def test_log_only_relay_pr_is_rejected():
    problems = _v(
        files=("docs/progress/2026-10-03T2050Z-relay.md", "docs/certifications/submissions/a.md")
    )
    assert any("log-only" in p for p in problems)


def test_second_pr_for_an_owned_item_is_rejected_and_the_first_is_not():
    older = {"number": 9, "title": "[relay] FTF-0500D: fix L2 findings"}
    newer = {"number": 12, "title": "[relay] FTF-0500D: fix L2 findings (matrix test)"}
    assert any(
        "already owned by open PR #9" in p
        for p in _v(title=newer["title"], number=12, prs=[older, newer])
    )
    assert _v(title=older["title"], number=9, prs=[older, newer]) == []


def test_multi_item_overlap_and_case_are_detected():
    older = {"number": 3, "title": "[relay] FTF-0902 + FTF-H007/FTF-04A06"}
    assert any(
        "FTF-04A06" in p for p in _v(title="[relay] ftf-04a06 round 2", number=4, prs=[older])
    )


def test_follow_up_ids_are_distinct_items():
    parent = {"number": 3, "title": "[relay] FTF-0608: grader"}
    assert _v(title="[relay] FTF-0608-F1: follow-up", number=4, prs=[parent]) == []


def test_non_relay_open_prs_do_not_own_items():
    cert = {"number": 3, "title": "[CERT] FTF-0608 CERTIFIED"}
    assert _v(title="[relay] FTF-0608: round 2", number=4, prs=[cert]) == []


def test_relay_title_without_item_id_is_rejected():
    assert any("no FTF item ID" in p for p in _v(title="[relay] tracking-only: merged PR #436"))


# --- verdict ----------------------------------------------------------------------------


EXPECTED = ["gate", "classify", "relay-policy", "mutation-gate", "db-integration"]


def _ok(**override):
    r = {j: "success" for j in EXPECTED}
    r.update(override)
    return r


def test_all_success_passes():
    assert cp.evaluate(_ok(), EXPECTED, "full", True) == ([], {})


@pytest.mark.parametrize("bad", ["failure", "cancelled", "missing", "timed_out"])
def test_failed_cancelled_or_missing_results_fail(bad):
    problems, _ = cp.evaluate(_ok(gate=bad), EXPECTED, "full", True)
    assert problems and "gate" in problems[0]


def test_integration_skip_allowed_only_for_tracker_scope():
    problems, exempt = cp.evaluate(_ok(**{"db-integration": "skipped"}), EXPECTED, "tracker", False)
    assert problems == [] and "db-integration" in exempt
    problems, _ = cp.evaluate(_ok(**{"db-integration": "skipped"}), EXPECTED, "full", False)
    assert any("skipped with no documented exemption" in p for p in problems)


def test_relay_only_jobs_may_skip_only_on_non_relay_prs():
    skipped = _ok(**{"mutation-gate": "skipped", "relay-policy": "skipped"})
    assert cp.evaluate(skipped, EXPECTED, "full", False)[0] == []
    assert len(cp.evaluate(skipped, EXPECTED, "full", True)[0]) == 2


@pytest.mark.parametrize("job", ["gate", "classify"])
def test_core_jobs_may_never_skip(job):
    for scope in ("tracker", "full"):
        assert cp.evaluate(_ok(**{job: "skipped"}), EXPECTED, scope, False)[0]


def test_unaggregated_job_fails():
    results = _ok()
    del results["db-integration"]
    problems, _ = cp.evaluate(results, EXPECTED, "full", False)
    assert any("not aggregated" in p for p in problems)


def test_unknown_scope_fails():
    assert cp.evaluate(_ok(), EXPECTED, "", False)[0]


# --- focused runs -----------------------------------------------------------------------

JOBS = ["network-policy-enforcement-integration", "storage-semantics-integration"]


def test_focus_parses_known_jobs_and_reports_unknown():
    body = "Debugging.\nci-focus: network-policy-enforcement-integration, bogus-job\n"
    assert cp.parse_focus(body, JOBS) == (["network-policy-enforcement-integration"], ["bogus-job"])
    assert cp.parse_focus("no marker here", JOBS) == ([], [])
    assert cp.parse_focus("text ci-focus: storage-semantics-integration", JOBS) == ([], [])


def test_focused_run_never_passes_even_when_everything_ran_green():
    problems, exempt = cp.evaluate(_ok(**{"db-integration": "skipped"}), EXPECTED, "focused", False)
    assert any("focused run" in p for p in problems)
    assert "db-integration" in exempt
    assert any("focused run" in p for p in cp.evaluate(_ok(), EXPECTED, "focused", False)[0])


def _classify(tmp_path, monkeypatch, body, files):
    def run(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    wf = tmp_path / "ci.yml"
    wf.write_text("jobs:\n" + "".join(f"  {j}:\n    x: 1\n" for j in ["gate", *JOBS]))
    run("add", ".")
    run("commit", "-qm", "base")
    run("checkout", "-qb", "pr")
    for f in files:
        (tmp_path / f).write_text("changed\n")
    run("add", ".")
    run("commit", "-qm", "change")
    out = tmp_path / "out"
    out.write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("PR_BODY", body)
    assert cp.main(["classify", "--base", "main", "--workflow", str(wf)]) == 0
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


def test_classify_focus_narrows_a_full_run(tmp_path, monkeypatch):
    o = _classify(
        tmp_path, monkeypatch, "ci-focus: network-policy-enforcement-integration", ["a.py"]
    )
    assert o["scope"] == "focused"
    assert o["focus"] == " network-policy-enforcement-integration "


def test_classify_focus_never_widens_a_tracker_run(tmp_path, monkeypatch):
    o = _classify(tmp_path, monkeypatch, "ci-focus: storage-semantics-integration", ["QUEUE.md"])
    assert o["scope"] == "tracker" and o["focus"].strip() == ""


def test_classify_unknown_focus_stays_full(tmp_path, monkeypatch):
    o = _classify(tmp_path, monkeypatch, "ci-focus: gate", ["a.py"])
    assert o["scope"] == "full" and o["focus"].strip() == ""


def test_verdict_writes_bound_evidence_and_exit_code(tmp_path, monkeypatch):
    wf = tmp_path / "ci.yml"
    wf.write_text("name: CI\njobs:\n  gate:\n    x: 1\n  ci-verdict:\n    x: 1\n")
    needs = tmp_path / "needs.json"
    monkeypatch.setenv("GITHUB_SHA", "m" * 40)
    ev = tmp_path / "ev.json"
    base_args = [
        "verdict",
        "--needs-json",
        str(needs),
        "--workflow",
        str(wf),
        "--scope",
        "full",
        "--evidence",
        str(ev),
        "--head-sha",
        "h" * 40,
        "--base-sha",
        "b" * 40,
        "--files-sha256",
        "f" * 64,
    ]
    needs.write_text(json.dumps({"gate": {"result": "success"}}))
    assert cp.main(base_args) == 0
    rec = json.loads(ev.read_text())
    assert rec["verdict"] == "pass"
    assert (
        rec["merge_candidate_sha"] == "m" * 40
        and rec["head_sha"] == "h" * 40
        and rec["base_sha"] == "b" * 40
    )
    assert len(rec["workflow_sha256"]) == 64 and rec["changed_files_sha256"] == "f" * 64
    needs.write_text(json.dumps({"gate": {"result": "cancelled"}}))
    assert cp.main(base_args) == 1
    assert json.loads(ev.read_text())["verdict"] == "fail"


# --- wiring in the real workflow --------------------------------------------------------


def _jobs_block(text: str, job: str) -> str:
    m = re.search(rf"^  {re.escape(job)}:\s*$(.*?)(?=^  [A-Za-z0-9_-]+:\s*$|\Z)", text, re.M | re.S)
    assert m, job
    return m.group(1)


def test_verdict_aggregates_every_job_in_ci_yml():
    text = CI.read_text()
    jobs = cp.workflow_jobs(text)
    assert "gate" in jobs and "ci-verdict" in jobs and len(jobs) > 40
    block = _jobs_block(text, "ci-verdict")
    needs = set(re.findall(r"^\s+- ([A-Za-z0-9_-]+)\s*$", block.split("steps:")[0], re.M))
    assert needs == set(jobs) - {"ci-verdict"}
    assert re.search(
        r"^    if: \$\{\{ !cancelled\(\) \}\}$", block, re.M
    ), "verdict must run even when jobs fail"


def test_every_integration_job_is_scope_gated_and_nothing_else_is():
    text = CI.read_text()
    for job in cp.workflow_jobs(text):
        block = _jobs_block(text, job)
        cond = (
            "    if: needs.classify.outputs.scope == 'full' || "
            f"contains(needs.classify.outputs.focus, ' {job} ')\n"
        )
        assert (cond in block) == job.endswith("-integration"), job
        assert ("needs.classify.outputs.scope" in block) == job.endswith("-integration") or job in {
            "ci-verdict"
        }, job


def test_workflow_supersedes_older_runs_of_the_same_pr():
    text = CI.read_text()
    assert re.search(
        r"^concurrency:\n  group: ci-\$\{\{ github\.event\.pull_request\.number", text, re.M
    )
    assert "cancel-in-progress: true" in text


def test_policy_and_verdict_run_the_base_branch_copy():
    text = CI.read_text()
    assert text.count('git show "origin/${BASE_REF}:scripts/ci_policy.py"') >= 3
    for job in ("classify", "relay-policy", "ci-verdict"):
        assert "$RUNNER_TEMP/ci_policy.py" in _jobs_block(text, job), job


def test_relay_policy_applies_the_ci_yml_rule():
    head = BASE_CI.replace("        run: ruff check .\n", "        run: true\n", 1)
    problems = cp.relay_violations(
        "[relay] FTF-1: x", 5, [".github/workflows/ci.yml"], "", [], (BASE_CI, head)
    )
    assert any("existing job 'gate' was modified" in p for p in problems)


def test_reference_code_is_under_the_mutation_and_product_truth_gates():
    from scripts import mutation_gate as mg

    assert mg.under_source_roots("evals/reference/candidate_discovery_from_org_litellm.py")
    assert not mg.is_test_path("evals/reference/candidate_discovery_from_org_litellm.py")
    assert mg.is_test_path("tests/reference/test_ftf_1706.py")


def test_mutation_gate_selects_reference_tests():
    block = _jobs_block(CI.read_text(), "mutation-gate")
    assert '"tests/tasks/", "tests/reference/"' in block
    assert r"tests/(tasks|reference)/test_[^/]+\.py" in block
    assert 'pathlib.Path(f"tests/reference/test_ftf_{i}.py")' in block
    assert '"workers", "packages", "evals")' in block
