"""scripts/ci_gate_step.py: a gate proof step runs only when the pull request touches its inputs,
and any doubt runs it."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ci_gate_step", ROOT / "scripts" / "ci_gate_step.py")
gs = importlib.util.module_from_spec(_spec)
sys.modules["ci_gate_step"] = gs
_spec.loader.exec_module(gs)

TEST = "tests/tasks/test_ftf_x.py"
TEXT = (
    "from apps.api.app.store import crud\n"
    "from app.datasets import dataset_mapping_store as store\n"
    'SCHEMA = ROOT / "packages/contracts/jsonschema/thing.json"\n'
    "import workers.data.csv_importer\n"
)


def _run(files, body="", text=TEXT):
    return gs.decide(TEST, files, text, body)[0]


def test_unrelated_change_skips_the_proof():
    assert _run(["apps/web/pages/index.tsx", "QUEUE.md"]) is False
    assert _run([]) is False


def test_the_test_file_itself_runs_the_proof():
    assert _run([TEST]) is True


def test_a_named_module_or_path_runs_the_proof():
    assert _run(["apps/api/app/store/crud.py"]) is True
    assert _run(["apps/api/app/datasets/dataset_mapping_store.py"]) is True
    assert _run(["apps/api/app/datasets/sibling_in_the_package.py"]) is True
    assert _run(["packages/contracts/jsonschema/thing.json"]) is True
    assert _run(["workers/data/csv_importer.py"]) is True
    assert _run(["workers/data/other_importer.py"]) is False


def test_suite_wide_inputs_and_ci_full_run_every_proof():
    for f in ("uv.lock", "pyproject.toml", "scripts/ci_gate_step.py", "scripts/ci_policy.py"):
        assert _run([f]) is True, f
    assert _run(["apps/web/pages/index.tsx"], body="notes\nci-full\n") is True


def test_the_app_entry_point_alone_does_not_run_every_api_proof():
    assert _run(["apps/api/app/main.py"], text="import apps.api.app.main\n") is False


def test_doubt_runs_the_proof():
    assert gs.decide(TEST, ["x.py"], None, "")[0] is True, "unreadable test file"


def test_main_runs_everything_outside_pull_requests(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    assert gs.main(["ci_gate_step.py", sys.executable, "-c", "raise SystemExit(7)"]) == 7


def test_main_runs_when_it_cannot_decide(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("BASE_REF", "no-such-branch-for-this-test")
    code = gs.main(["ci_gate_step.py", sys.executable, "-c", "raise SystemExit(5)", TEST])
    assert code == 5, "an unreadable diff must run the step, not skip it"


def test_main_skips_with_exit_zero_and_passes_through_a_failure(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    # A path this very file does not name (it names several others as fixtures above).
    monkeypatch.setattr(gs, "changed_files", lambda base: ["unrelated-area/notes.txt"])
    real = "tests/unit/test_ci_gate_step.py"
    assert gs.main(["ci_gate_step.py", sys.executable, "-c", "raise SystemExit(9)", real]) == 0
    monkeypatch.setattr(gs, "changed_files", lambda base: [real])
    assert gs.main(["ci_gate_step.py", sys.executable, "-c", "raise SystemExit(9)", real]) == 9
