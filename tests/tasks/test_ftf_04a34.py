#!/usr/bin/env python3
"""FTF-04A34 -- Monorepo multi-agent discovery test (Section 6/7A/66/77/81).

Qualifies the real discovery chain (`capture_local_repository` ->
`run_source_analysis` -> `run_python_agent_config_tool_discovery` /
`run_typescript_javascript_agent_config_tool_discovery`) against one real
Git monorepo, built with the real `git` binary in a real temp directory,
holding three independent products (two Python LiteLLM agents with
different models/tools and one TypeScript OpenAI-compatible agent) plus a
non-agent service. No mocked git, canned capture or stubbed detector.

Acceptance:
  A1. multiple agents and framework markers are found in one monorepo.
  A2. candidate/source evidence is deterministic: byte-identical across
      repeated runs and across a reversed input file order.
Negative acceptance:
  N1. no automatic agent selection: discovery yields several candidates and
      carries no selected/default marker; `select_discovered_agent_candidate`
      requires an explicit candidate key.
  N2. no mixing of tools/prompts across products: every entrypoint's model
      and tool evidence comes only from its own product directory, even for
      same-named functions and same-named tools in two products.

Run directly: python3 tests/tasks/test_ftf_04a34.py
"""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors import discovered_agent_candidate_selection as _selection  # noqa: E402
from app.target_connectors.local_repository_connector import (  # noqa: E402
    capture_local_repository,
)
from app.target_connectors.python_agent_config_tool_discovery import (  # noqa: E402
    run_python_agent_config_tool_discovery,
)
from app.target_connectors.source_analysis_worker import (  # noqa: E402
    SourceFileRef,
    run_source_analysis,
)
from app.target_connectors.typescript_javascript_deterministic_agent_config_tool_discovery import (  # noqa: E402,E501
    run_typescript_javascript_agent_config_tool_discovery,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _git(cwd: Path, *args: str) -> None:
    git_binary = shutil.which("git")
    assert git_binary is not None, "git binary required"
    subprocess.run(
        [git_binary, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _py_agent(model: str, tool: str, fn: str = "handle") -> bytes:
    return (
        "import litellm\n\n"
        f"def {fn}(msg):\n"
        "    return litellm.completion(\n"
        f"        model={model!r},\n"
        '        messages=[{"role": "user", "content": msg}],\n'
        f'        tools=[{{"type": "function", "function": {{"name": {tool!r}}}}}],\n'
        "    )\n"
    ).encode()


FILES: dict[str, bytes] = {
    "services/support/pyproject.toml": (
        b'[project]\nname="support"\ndependencies=["fastapi","litellm"]\n'
    ),
    "services/support/agent.py": _py_agent("gpt-4o", "lookup_ticket")
    + _py_agent("gpt-4o-mini", "open_case", fn="escalate"),
    "services/billing/requirements.txt": b"flask==3.0.0\nlitellm\n",
    "services/billing/agent.py": _py_agent("claude-sonnet", "lookup_invoice"),
    "services/search/package.json": json.dumps(
        {"name": "search", "dependencies": {"express": "4", "openai": "4"}}
    ).encode(),
    "services/search/agent.ts": (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"export async function handle(q: string) {\n"
        b'  return client.chat.completions.create({ model: "gpt-4o-mini" });\n'
        b"}\n"
    ),
    "services/static/app.py": b"def handle(x):\n    return x\n",
    "README.md": b"# monorepo\n",
}


def _build_monorepo(parent: Path) -> Path:
    repo = parent / "mono"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel, data in FILES.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "monorepo")
    return repo


def _git_out(cwd: Path, *args: str) -> bytes:
    git_binary = shutil.which("git")
    assert git_binary is not None, "git binary required"
    return subprocess.run([git_binary, *args], cwd=cwd, check=True, capture_output=True).stdout


def _refs(repo: Path, reverse: bool = False) -> list[SourceFileRef]:
    """Files of the exact commit the real connector captured (a clean capture
    carries the commit, not a manifest), read as committed blobs."""
    capture = capture_local_repository(repo, [repo.parent])
    commit = capture.commit_sha
    paths = sorted(
        _git_out(repo, "ls-tree", "-r", "--name-only", "-z", commit).decode().split("\0")[:-1],
        reverse=reverse,
    )
    return [
        SourceFileRef(
            relative_path=rel,
            size=len(_git_out(repo, "show", f"{commit}:{rel}")),
            read_content=(lambda r=rel: _git_out(repo, "show", f"{commit}:{r}")),
        )
        for rel in paths
    ]


def _discover(refs: list[SourceFileRef]) -> dict[str, Any]:
    analysis = run_source_analysis(refs)
    py = run_python_agent_config_tool_discovery(refs)
    ts = run_typescript_javascript_agent_config_tool_discovery(refs)
    raw = [(e.relative_path, e.line) for e in (*py.entrypoints, *ts.entrypoints)]
    entrypoints = sorted(
        [
            {
                **e.to_entrypoint_evidence_json(),
                "model": e.to_model_evidence_json(),
                "tools": e.to_tool_evidence_json(),
            }
            for e in (*py.entrypoints, *ts.entrypoints)
        ],
        key=lambda d: (str(d["relative_path"]), int(d["line"])),
    )
    return {
        "markers": list(analysis.detected_framework_markers),
        "languages": list(analysis.detected_languages),
        "entrypoints": entrypoints,
        "raw_order": raw,
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = _build_monorepo(Path(tmp))
        first = _discover(_refs(repo))
        second = _discover(_refs(repo))
        reversed_order = _discover(_refs(repo, reverse=True))

    entrypoints: list[dict[str, Any]] = first["entrypoints"]
    by_path = {str(e["relative_path"]): e for e in entrypoints if e["function_name"] == "handle"}
    escalate: dict[str, Any] = next(
        (e for e in entrypoints if e["function_name"] == "escalate"), {}
    )

    # A1 -- several agents and framework markers in one monorepo.
    check(
        "FTF-04A34-A1",
        "exactly the three real agent files are discovered (not the non-agent service)",
        sorted({str(e["relative_path"]) for e in entrypoints})
        == ["services/billing/agent.py", "services/search/agent.ts", "services/support/agent.py"]
        and len(entrypoints) == 4,
    )
    check(
        "FTF-04A34-A1",
        "framework markers from all three products are detected",
        set(first["markers"]) >= {"fastapi", "flask", "express"},
    )

    # A2 -- deterministic evidence, independent of input order.
    check("FTF-04A34-A2", "repeated runs are identical, raw order included", first == second)
    check(
        "FTF-04A34-A2",
        "reversed input order gives identical evidence once sorted",
        {k: v for k, v in first.items() if k != "raw_order"}
        == {k: v for k, v in reversed_order.items() if k != "raw_order"},
    )

    # N2 -- each product keeps its own model and tools; no cross-product mixing.
    support = by_path.get("services/support/agent.py", {})
    billing = by_path.get("services/billing/agent.py", {})
    search = by_path.get("services/search/agent.ts", {})
    check(
        "FTF-04A34-N2",
        "support agent carries only its own model and tool",
        support.get("model", {}).get("model_alias") == "gpt-4o"
        and support.get("tools", {}).get("tool_names") == ["lookup_ticket"],
    )
    check(
        "FTF-04A34-N2",
        "two agents in one file keep separate model and tool evidence",
        support.get("tools", {}).get("tool_names") == ["lookup_ticket"]
        and escalate.get("tools", {}).get("tool_names") == ["open_case"]
        and escalate.get("model", {}).get("model_alias") == "gpt-4o-mini",
    )
    check(
        "FTF-04A34-N2",
        "billing agent (same function name 'handle') carries only its own model and tool",
        billing.get("function_name") == support.get("function_name") == "handle"
        and billing.get("model", {}).get("model_alias") == "claude-sonnet"
        and billing.get("tools", {}).get("tool_names") == ["lookup_invoice"],
    )
    check(
        "FTF-04A34-N2",
        "TypeScript agent carries no Python product's model or tools",
        search.get("model", {}).get("model_alias") == "gpt-4o-mini"
        and not search.get("tools", {}).get("tool_names"),
    )

    # N1 -- no automatic selection: several candidates, no selected marker,
    # and the selection entry point demands an explicit candidate key.
    check("FTF-04A34-N1", "more than one candidate is reported", len(entrypoints) > 1)
    check(
        "FTF-04A34-N1",
        "no candidate carries a selected/default marker",
        not any(
            k in e
            for e in entrypoints
            for k in ("selected", "is_selected", "default", "primary", "chosen")
        ),
    )
    params = inspect.signature(_selection.select_discovered_agent_candidate).parameters
    check(
        "FTF-04A34-N1",
        "selection requires an explicit candidate key with no default",
        any(
            "candidate" in name and p.default is inspect.Parameter.empty
            for name, p in params.items()
        ),
    )

    if FAILURES:
        print("\n".join(FAILURES))
        return 1
    print("FTF-04A34 Monorepo multi-agent discovery test: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
