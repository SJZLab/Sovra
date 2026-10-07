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

import functools
import hashlib
import inspect
import json
import os
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
from app.target_connectors.prompt_source_discovery_evidence_capture import (  # noqa: E402
    run_prompt_source_discovery,
)
from app.target_connectors.python_agent_config_tool_discovery import (  # noqa: E402
    EntrypointCandidate as PyEntry,
)
from app.target_connectors.python_agent_config_tool_discovery import (  # noqa: E402
    run_python_agent_config_tool_discovery,
)
from app.target_connectors.source_analysis_worker import (  # noqa: E402
    SourceFileRef,
    run_source_analysis,
)
from app.target_connectors.typescript_javascript_deterministic_agent_config_tool_discovery import (  # noqa: E402,E501
    EntrypointCandidate as TsEntry,
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


def _py_agent(model: str, tool: str, fn: str = "handle", system: str | None = None) -> bytes:
    """`system` is a Python source expression for the system message content."""
    messages = (
        '[{"role": "user", "content": msg}]'
        if system is None
        else f'[{{"role": "system", "content": {system}}}, {{"role": "user", "content": msg}}]'
    )
    return (
        "import litellm\n\n"
        f"def {fn}(msg):\n"
        "    return litellm.completion(\n"
        f"        model={model!r},\n"
        f"        messages={messages},\n"
        f'        tools=[{{"type": "function", "function": {{"name": {tool!r}}}}}],\n'
        "    )\n"
    ).encode()


SUPPORT_PROMPT = b"You are the support agent.\n"
BILLING_PROMPT = b"You are the billing agent.\n"
ESCALATION_PROMPT = "Escalate politely."
# The same-named template at two different repo-relative paths (N2 prompts).
SUPPORT_PROMPT_PATH = "services/support/prompts/system.txt"
BILLING_PROMPT_PATH = "services/billing/prompts/system.txt"


FILES: dict[str, bytes] = {
    "services/support/pyproject.toml": (
        b'[project]\nname="support"\ndependencies=["fastapi","litellm"]\n'
    ),
    SUPPORT_PROMPT_PATH: SUPPORT_PROMPT,
    BILLING_PROMPT_PATH: BILLING_PROMPT,
    "services/support/agent.py": _py_agent(
        "gpt-4o", "lookup_ticket", system=f"open({SUPPORT_PROMPT_PATH!r}).read()"
    )
    + _py_agent("gpt-4o-mini", "open_case", fn="escalate", system=repr(ESCALATION_PROMPT)),
    "services/billing/requirements.txt": b"flask==3.0.0\nlitellm\n",
    "services/billing/agent.py": _py_agent(
        "claude-sonnet", "lookup_invoice", system=f"open({BILLING_PROMPT_PATH!r}).read()"
    )
    # Dynamic model, no tools=, right after a fully-evidenced neighbour.
    + (
        b"import os\n\n\ndef dyn(msg):\n"
        b"    return litellm.completion(\n"
        b"        model=os.environ['M'], messages=[{'role': 'user', 'content': msg}]\n"
        b"    )\n"
    ),
    "services/search/package.json": json.dumps(
        {"name": "search", "dependencies": {"express": "4", "openai": "4"}}
    ).encode(),
    "services/search/agent.ts": (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"export async function handle(q: string) {\n"
        b'  return client.chat.completions.create({ model: "gpt-4o-mini" });\n'
        b"}\n"
        b"export async function second(q: string) {\n"
        b"  return client.chat.completions.create({\n"
        b'    model: "gpt-4o",\n'
        b'    tools: [{ type: "function", function: { name: "web" } }],\n'
        b"  });\n"
        b"}\n"
        # Bare call after a fully-evidenced neighbour: must inherit nothing.
        b"export async function third(q: string) {\n"
        b"  return client.chat.completions.create({ messages: [] });\n"
        b"}\n"
    ),
    # A second TS agent in another product: no model, no tools.
    "services/notes/package.json": json.dumps(
        {"name": "notes", "dependencies": {"openai": "4"}}
    ).encode(),
    "services/notes/agent.ts": (
        b'import OpenAI from "openai";\n'
        b"const client = new OpenAI();\n"
        b"export async function handle(q: string) {\n"
        b"  return client.chat.completions.create({ messages: [] });\n"
        b"}\n"
    ),
    # Same basename as services/search/package.json, but not express.
    "services/static/package.json": json.dumps(
        {"name": "static", "dependencies": {"left-pad": "1"}}
    ).encode(),
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
            read_content=functools.partial(_git_out, repo, "show", f"{commit}:{rel}"),
        )
        for rel in paths
    ]


def _discover(refs: list[SourceFileRef]) -> dict[str, Any]:
    analysis = run_source_analysis(refs)
    py = run_python_agent_config_tool_discovery(refs)
    ts = run_typescript_javascript_agent_config_tool_discovery(refs)
    found: list[PyEntry | TsEntry] = [*py.entrypoints, *ts.entrypoints]
    raw = [(e.relative_path, e.line) for e in found]
    entrypoints: list[dict[str, Any]] = sorted(
        [
            {
                **e.to_entrypoint_evidence_json(),
                "model": e.to_model_evidence_json(),
                "tools": e.to_tool_evidence_json(),
            }
            for e in found
        ],
        key=lambda d: (str(d["relative_path"]), d["line"]),
    )
    return {
        "markers": list(analysis.detected_framework_markers),
        "languages": list(analysis.detected_languages),
        "entrypoints": entrypoints,
        "raw_order": raw,
        "prompts": [c.to_json() for c in run_prompt_source_discovery(refs).candidates],
    }


def _canon(d: dict[str, Any]) -> dict[str, Any]:
    """Evidence minus the raw (input-order-dependent) lists, sorted."""
    return {
        **{k: v for k, v in d.items() if k not in ("raw_order", "prompts")},
        "prompts": sorted(d["prompts"], key=lambda c: (c["relative_path"], c["location"])),
    }


def _walk_keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _walk_keys(v)}
    if isinstance(node, list | tuple):
        return {k for v in node for k in _walk_keys(v)}
    return set()


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _under_hash_seed(seed: str) -> dict[str, Any]:
    """The same discovery run in a fresh interpreter under another hash seed."""
    done = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--dump"],
        env={**os.environ, "PYTHONHASHSEED": seed},
        check=True,
        capture_output=True,
    )
    loaded: dict[str, Any] = json.loads(done.stdout)
    return loaded


def _dump() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        print(json.dumps(_discover(_refs(_build_monorepo(Path(tmp)))), sort_keys=True))
    return 0


FORBIDDEN_SELECTION_KEYS = {
    "selected",
    "is_selected",
    "default",
    "is_default",
    "primary",
    "chosen",
    "recommend",
    "recommended",
    "rank",
    "score",
}
ENTRYPOINT_KEYS = {"relative_path", "line", "function_name", "call", "model", "tools"}
MODEL_KEYS = {"resolved", "model_alias", "reason"}
TOOL_KEYS = {"resolved", "tool_names", "reason"}
PROMPT_KEYS = {
    "relative_path",
    "location",
    "role",
    "resolved",
    "source_kind",
    "content_sha256",
    "content_length",
    "referenced_path",
    "template_dependency_resolved",
    "reason",
}


def main() -> int:
    if "--dump" in sys.argv:
        return _dump()
    with tempfile.TemporaryDirectory() as tmp:
        repo = _build_monorepo(Path(tmp))
        first = _discover(_refs(repo))
        second = _discover(_refs(repo))
        reversed_order = _discover(_refs(repo, reverse=True))

        # Commit identity: the evidence follows the commit the connector captured.
        head1 = _git_out(repo, "rev-parse", "HEAD").decode().strip()
        capture1 = capture_local_repository(repo, [repo.parent])
        (repo / "services" / "billing" / "agent.py").write_bytes(
            _py_agent("claude-opus", "lookup_invoice", system=repr("new"))
        )
        _git(repo, "commit", "-qam", "second")
        head2 = _git_out(repo, "rev-parse", "HEAD").decode().strip()
        after_commit = _discover(_refs(repo))
        capture2 = capture_local_repository(repo, [repo.parent])
        (repo / "services" / "billing" / "agent.py").write_bytes(
            _py_agent("claude-haiku", "lookup_invoice")
        )  # uncommitted: the dirty tree must not be what the clean capture names
        dirty_capture = capture_local_repository(repo, [repo.parent])
        dirty = _discover(_refs(repo))

    # Re-derive the baseline under two other hash seeds (fresh interpreters).
    seeded = [_under_hash_seed(seed) for seed in ("1", "4242")]

    entrypoints: list[dict[str, Any]] = first["entrypoints"]
    by_key = {(str(e["relative_path"]), str(e["function_name"])): e for e in entrypoints}
    prompts: list[dict[str, Any]] = first["prompts"]
    prompt_by_key = {(str(c["relative_path"]), str(c["location"])): c for c in prompts}

    # A1 -- several agents and framework markers in one monorepo.
    check(
        "FTF-04A34-A1",
        "exactly the real agent functions are discovered (not the non-agent service)",
        sorted(by_key)
        == [
            ("services/billing/agent.py", "dyn"),
            ("services/billing/agent.py", "handle"),
            ("services/notes/agent.ts", "handle"),
            ("services/search/agent.ts", "handle"),
            ("services/search/agent.ts", "second"),
            ("services/search/agent.ts", "third"),
            ("services/support/agent.py", "escalate"),
            ("services/support/agent.py", "handle"),
        ]
        and len(entrypoints) == 8,
    )
    check(
        "FTF-04A34-A1",
        "framework markers and languages are exactly the three products' own",
        first["markers"] == ["express", "fastapi", "flask"]
        and first["languages"] == ["python", "typescript"],
    )

    # A2 -- deterministic evidence, independent of input order and hash seed.
    check("FTF-04A34-A2", "repeated runs are identical, raw order included", first == second)
    check(
        "FTF-04A34-A2",
        "reversed input order gives identical evidence once sorted",
        _canon(first) == _canon(reversed_order),
    )
    check(
        "FTF-04A34-A2",
        "raw (unsorted) entrypoint order is the exact file-then-source order, Python before TS",
        [list(item) for item in first["raw_order"]]
        == [
            ["services/billing/agent.py", 4],
            ["services/billing/agent.py", 13],
            ["services/support/agent.py", 4],
            ["services/support/agent.py", 12],
            ["services/notes/agent.ts", 4],
            ["services/search/agent.ts", 4],
            ["services/search/agent.ts", 7],
            ["services/search/agent.ts", 13],
        ],
    )
    check(
        "FTF-04A34-A2",
        "entrypoint call/line literals per language",
        [(e["relative_path"], e["function_name"], e["line"], e["call"]) for e in entrypoints]
        == [
            ("services/billing/agent.py", "handle", 4, "litellm.completion"),
            ("services/billing/agent.py", "dyn", 13, "litellm.completion"),
            ("services/notes/agent.ts", "handle", 4, "client.chat.completions.create"),
            ("services/search/agent.ts", "handle", 4, "client.chat.completions.create"),
            ("services/search/agent.ts", "second", 7, "client.chat.completions.create"),
            ("services/search/agent.ts", "third", 13, "client.chat.completions.create"),
            ("services/support/agent.py", "handle", 4, "litellm.completion"),
            ("services/support/agent.py", "escalate", 12, "litellm.completion"),
        ],
    )
    for seed, run in zip(("1", "4242"), seeded, strict=True):
        check(
            "FTF-04A34-A2",
            f"discovery under PYTHONHASHSEED={seed} is identical to the baseline run",
            run == json.loads(json.dumps(first, sort_keys=True)),
        )

    # N2 -- each product keeps its own model and tools; no cross-product mixing.
    support = by_key.get(("services/support/agent.py", "handle"), {})
    billing = by_key.get(("services/billing/agent.py", "handle"), {})
    escalate = by_key.get(("services/support/agent.py", "escalate"), {})
    dyn = by_key.get(("services/billing/agent.py", "dyn"), {})
    search = by_key.get(("services/search/agent.ts", "handle"), {})
    second_ts = by_key.get(("services/search/agent.ts", "second"), {})
    third_ts = by_key.get(("services/search/agent.ts", "third"), {})
    notes = by_key.get(("services/notes/agent.ts", "handle"), {})
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
        search.get("model")
        == {"resolved": True, "model_alias": "gpt-4o-mini", "reason": "literal_string"}
        and search.get("tools")
        == {"resolved": False, "tool_names": [], "reason": "argument_not_present"},
    )
    check(
        "FTF-04A34-N2",
        "a second TS agent in one file keeps its own distinct model and tools",
        second_ts.get("model")
        == {"resolved": True, "model_alias": "gpt-4o", "reason": "literal_string"}
        and second_ts.get("tools")
        == {"resolved": True, "tool_names": ["web"], "reason": "literal_openai_function_list"},
    )
    check(
        "FTF-04A34-N2",
        "a bare TS call after an evidenced neighbour inherits neither model nor tools",
        third_ts.get("model")
        == {"resolved": False, "model_alias": None, "reason": "argument_not_present"}
        and third_ts.get("tools")
        == {"resolved": False, "tool_names": [], "reason": "argument_not_present"},
    )
    check(
        "FTF-04A34-N2",
        "a TS agent in another product with no model/tools stays argument_not_present",
        notes.get("model")
        == {"resolved": False, "model_alias": None, "reason": "argument_not_present"}
        and notes.get("tools")
        == {"resolved": False, "tool_names": [], "reason": "argument_not_present"},
    )
    check(
        "FTF-04A34-N2",
        "a Python agent with a dynamic model and no tools= inherits nothing from its neighbour",
        dyn.get("model")
        == {"resolved": False, "model_alias": None, "reason": "unresolved_dynamic_expression"}
        and dyn.get("tools")
        == {"resolved": False, "tool_names": [], "reason": "argument_not_present"},
    )
    check(
        "FTF-04A34-N2",
        "every model/tool dict carries exactly its literal key set",
        all(set(e) == ENTRYPOINT_KEYS for e in entrypoints)
        and all(set(e["model"]) == MODEL_KEYS for e in entrypoints)
        and all(set(e["tools"]) == TOOL_KEYS for e in entrypoints),
    )

    # N2 prompts -- each product keeps its own prompt; same-named templates at
    # different paths and two agents' prompts in one file never mix.
    check(
        "FTF-04A34-N2",
        "prompt candidates are exactly one per agent, with the literal key set",
        sorted(prompt_by_key)
        == sorted((str(e["relative_path"]), f"function:{e['function_name']}") for e in entrypoints)
        and all(set(c) == PROMPT_KEYS for c in prompts),
    )
    sup_p = prompt_by_key.get(("services/support/agent.py", "function:handle"), {})
    bil_p = prompt_by_key.get(("services/billing/agent.py", "function:handle"), {})
    esc_p = prompt_by_key.get(("services/support/agent.py", "function:escalate"), {})
    check(
        "FTF-04A34-N2",
        "support prompt is its own template: its own path and its own hash",
        sup_p.get("source_kind") == "file_reference"
        and sup_p.get("referenced_path") == SUPPORT_PROMPT_PATH
        and sup_p.get("content_sha256") == _sha(SUPPORT_PROMPT)
        and sup_p.get("content_length") == len(SUPPORT_PROMPT)
        and sup_p.get("role") == "system"
        and sup_p.get("template_dependency_resolved") is True,
    )
    check(
        "FTF-04A34-N2",
        "billing prompt (same template file name) is its own path and its own hash",
        bil_p.get("source_kind") == "file_reference"
        and bil_p.get("referenced_path") == BILLING_PROMPT_PATH
        and bil_p.get("content_sha256") == _sha(BILLING_PROMPT)
        and bil_p.get("content_length") == len(BILLING_PROMPT)
        and bil_p.get("content_sha256") != sup_p.get("content_sha256"),
    )
    check(
        "FTF-04A34-N2",
        "the second agent in the support file keeps its own embedded prompt, not its neighbour's",
        esc_p.get("source_kind") == "embedded_literal"
        and esc_p.get("referenced_path") is None
        and esc_p.get("content_sha256") == _sha(ESCALATION_PROMPT)
        and esc_p.get("content_length") == len(ESCALATION_PROMPT)
        and esc_p.get("content_sha256")
        not in (sup_p.get("content_sha256"), bil_p.get("content_sha256")),
    )
    check(
        "FTF-04A34-N2",
        "agents with no prompt carry none (nothing inherited from another product)",
        all(
            prompt_by_key.get(key, {}).get("resolved") is False
            and prompt_by_key.get(key, {}).get("content_sha256") is None
            and prompt_by_key.get(key, {}).get("referenced_path") is None
            for key in (
                ("services/billing/agent.py", "function:dyn"),
                ("services/notes/agent.ts", "function:handle"),
                ("services/search/agent.ts", "function:handle"),
                ("services/search/agent.ts", "function:second"),
                ("services/search/agent.ts", "function:third"),
            )
        ),
    )
    check(
        "FTF-04A34-N2",
        "no raw prompt text appears anywhere in the prompt evidence",
        not any(
            text in json.dumps(prompts)
            for text in ("support agent", "billing agent", ESCALATION_PROMPT)
        ),
    )

    # N1 -- no automatic selection: several candidates, no selected marker
    # anywhere (recursively), and the selection entry point demands an explicit id.
    check("FTF-04A34-N1", "more than one candidate is reported", len(entrypoints) == 8)
    check(
        "FTF-04A34-N1",
        "no marker of any kind, at any depth, picks, ranks or defaults a candidate",
        not (_walk_keys(first) & FORBIDDEN_SELECTION_KEYS),
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
    try:
        _selection.select_discovered_agent_candidate(
            None,
            None,
            None,
            None,
            actor=None,
        )
        selected_without_id = True
    except TypeError:
        selected_without_id = False
    check(
        "FTF-04A34-N1",
        "calling selection without an explicit candidate id raises instead of choosing one",
        not selected_without_id,
    )

    # Commit identity -- the evidence follows the commit the connector captured.
    check(
        "FTF-04A34-A2",
        "the capture names exactly the repository HEAD commit",
        capture1.commit_sha == head1 and capture2.commit_sha == head2 and head1 != head2,
    )
    billing2 = {
        (str(e["relative_path"]), str(e["function_name"])): e for e in after_commit["entrypoints"]
    }.get(("services/billing/agent.py", "handle"), {})
    check(
        "FTF-04A34-A2",
        "after a second commit the evidence follows the newly captured commit",
        billing2.get("model", {}).get("model_alias") == "claude-opus" and first != after_commit,
    )
    check(
        "FTF-04A34-A2",
        "an uncommitted edit changes neither the captured commit nor the evidence",
        dirty_capture.commit_sha == head2
        and dirty["entrypoints"] == after_commit["entrypoints"]
        and dirty["prompts"] == after_commit["prompts"],
    )

    if FAILURES:
        print("\n".join(FAILURES))
        return 1
    print("FTF-04A34 Monorepo multi-agent discovery test: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
