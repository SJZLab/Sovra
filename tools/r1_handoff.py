#!/usr/bin/env python3
"""Extract and validate one Release 1 specification. Never runs product tests or coding agents."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import deque
from pathlib import Path
from typing import Any

PLAN_NAME = "FINE_TUNING_FACTORY_RELEASE_1.md"
EXPECTED_COMPONENTS = {"axolotl", "inspect_ai", "mlflow", "vllm", "temporal", "litellm", "argilla"}
EXCLUDED_PACKET_SECTIONS = {"56", "74", "75", "76", "83", "85", "86", "87"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def block(text: str, label: str, language: str) -> str:
    pattern = (r"^<!-- R1:" + re.escape(label) + r":BEGIN -->\n```" +
               re.escape(language) + r"\n(.*?)\n```\n<!-- R1:" +
               re.escape(label) + r":END -->$")
    matches = re.findall(pattern, text, re.M | re.S)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {label} block; found {len(matches)}")
    return matches[0]


def section_map(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"^# (\d+[A-Z]?)\. .*?$", text, re.M))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        key = match.group(1)
        if key in sections:
            raise ValueError(f"Duplicate specification section {key}")
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[key] = text[match.start():end].rstrip()
    return sections


def referenced_sections(value: str, sections: dict[str, str]) -> set[str]:
    result: set[str] = set()
    for item in value.split(","):
        item = item.strip()
        match = re.fullmatch(r"(\d+)(?:\.\d+)*\s*[–-]\s*(\d+)(?:\.\d+)*", item)
        if match:
            lo, hi = map(int, match.groups())
            if lo > hi:
                raise ValueError(f"Invalid section range {item}")
            result.update(str(i) for i in range(lo, hi + 1))
            result.update(k for k in sections if lo <= int(re.match(r"\d+", k)[0]) <= hi)
        else:
            match = re.fullmatch(r"(\d+[A-Z]?)(?:\.\d+)*", item)
            if not match:
                raise ValueError(f"Invalid section reference {item}")
            result.add(match.group(1))
    unknown = result - sections.keys()
    if unknown:
        raise ValueError("Missing sections: " + ", ".join(sorted(unknown)))
    return result


def parse(text: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    metadata = json.loads(block(text, "METADATA", "json"))
    defaults = json.loads(block(text, "TASK_DEFAULTS", "json"))
    records = json.loads(block(text, "TASKS", "json"))
    if not isinstance(records, list) or not isinstance(defaults, dict):
        raise ValueError("Task records/defaults have wrong types")
    tasks: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or record.get("kind") not in defaults["by_kind"]:
            raise ValueError("Unknown task kind or invalid record")
        task = copy.deepcopy(defaults["all"])
        task.update(copy.deepcopy(defaults["by_kind"][record["kind"]]))
        task.update(record)
        tasks.append(task)
    return metadata, tasks, section_map(text)


def validate_data(metadata: dict[str, Any], tasks: list[dict[str, Any]],
                  sections: dict[str, str]) -> list[str]:
    if metadata.get("release") != "1.0" or metadata.get("task_count") != 300 or len(tasks) != 300:
        raise ValueError("Release 1 requires exactly 300 initial task contracts")
    if metadata.get("implementation_status") != "NOT_IMPLEMENTED":
        raise ValueError("Initial handoff cannot claim implementation")
    if metadata.get("runtime_qualification_status") != "NOT_TESTED":
        raise ValueError("Initial handoff cannot claim runtime qualification")
    architecture = metadata["architecture"]
    components = architecture["runtime_components"]
    if (len(components) != 7 or {x["key"] for x in components} != EXPECTED_COMPONENTS or
            not all(x["required"] is True for x in components)):
        raise ValueError("Seven required selected runtime components must be preserved")
    plane = architecture["implementation_plane"]
    if (plane["product_runtime_requires_openhands"] is not False or
            plane["external_skill_bundle_required"] is not False or
            plane["default_agents"] != 4 or plane["maximum_agents"] != 6):
        raise ValueError("Single-file/executor/parallelism contract violated")
    if architecture["normal_user_interface"] != "factory_ui_only":
        raise ValueError("Unified Factory UI is required")
    if metadata["qualification_scenarios"] != [f"Q{i:02d}" for i in range(1, 44)]:
        raise ValueError("Q01-Q43 must all be retained")
    if metadata["capabilities"] != [f"CAP-{i:02d}" for i in range(1, 22)]:
        raise ValueError("CAP-01-CAP-21 must all be retained")
    indexed: dict[str, dict[str, Any]] = {}
    allowed_requirements = set(metadata["capabilities"]) | {f"AF-{i:02d}" for i in range(1, 37)}
    for task in tasks:
        task_id = task["id"]
        if not re.fullmatch(r"FTF-[A-Z0-9]+", task_id) or task_id in indexed:
            raise ValueError(f"Invalid or duplicate task {task_id}")
        indexed[task_id] = task
        if task["required"] is not True or task["status"] != "NOT_STARTED" or task["ready"] is not False or task["evidence"] != []:
            raise ValueError(f"Invalid initial state for {task_id}")
        for key in ("title", "files", "acceptance", "negative_acceptance", "verification_commands", "security", "observability"):
            if not task.get(key):
                raise ValueError(f"Missing {key} for {task_id}")
        if not task.get("qualification_scenarios") or set(task["qualification_scenarios"]) - set(metadata["qualification_scenarios"]):
            raise ValueError(f"Unknown/missing scenario in {task_id}")
        if set(task["requirements"]) - allowed_requirements:
            raise ValueError(f"Unknown requirement in {task_id}")
        referenced_sections(task["sections"], sections)
        if len(set(task["dependencies"])) != len(task["dependencies"]):
            raise ValueError(f"Duplicate dependency for {task_id}")
    graph = {key: [] for key in indexed}
    degree = {key: 0 for key in indexed}
    for task in tasks:
        for dep in task["dependencies"]:
            if dep not in indexed or dep == task["id"]:
                raise ValueError(f"Invalid dependency {dep} of {task['id']}")
            graph[dep].append(task["id"])
            degree[task["id"]] += 1
        owner = task.get("contract_owner")
        if owner and owner not in indexed:
            raise ValueError(f"Unknown contract owner for {task['id']}")
    queue = deque(sorted(key for key, value in degree.items() if value == 0))
    ordered: list[str] = []
    while queue:
        task_id = queue.popleft()
        ordered.append(task_id)
        for child in sorted(graph[task_id]):
            degree[child] -= 1
            if degree[child] == 0:
                queue.append(child)
    if len(ordered) != len(tasks):
        raise ValueError("Dependency cycle: " + ", ".join(k for k, v in degree.items() if v))
    return ordered


def load(plan: Path) -> tuple[str, dict[str, Any], list[dict[str, Any]], dict[str, str], list[str]]:
    text = plan.read_text(encoding="utf-8")
    metadata, tasks, sections = parse(text)
    boundary = text.index("# 85. Embedded Release 1 Task Ledger")
    if digest(text[:boundary].encode("utf-8")) != metadata["binding_body_sha256"]:
        raise ValueError("Binding specification changed: body hash mismatch")
    for label, field in [("TASKS", "task_records_sha256"), ("TASK_DEFAULTS", "task_defaults_sha256")]:
        if digest(block(text, label, "json").encode("utf-8")) != metadata[field]:
            raise ValueError(f"Embedded {label} changed: hash mismatch")
    helper = block(text, "HELPER", "python") + "\n"
    if digest(helper.encode("utf-8")) != metadata["embedded_helper_sha256"]:
        raise ValueError("Embedded helper changed: hash mismatch")
    return text, metadata, tasks, sections, validate_data(metadata, tasks, sections)


def self_test(metadata: dict[str, Any], tasks: list[dict[str, Any]],
              sections: dict[str, str]) -> dict[str, Any]:
    changes = {
        "missing_task": lambda m, t: t.pop(),
        "duplicate_task": lambda m, t: t.__setitem__(1, copy.deepcopy(t[0])),
        "unknown_dependency": lambda m, t: t[0]["dependencies"].append("FTF-UNKNOWN"),
        "self_dependency": lambda m, t: t[0]["dependencies"].append(t[0]["id"]),
        "dependency_cycle": lambda m, t: (t[0]["dependencies"].append(t[1]["id"]), t[1]["dependencies"].append(t[0]["id"])),
        "empty_acceptance": lambda m, t: t[0].__setitem__("acceptance", []),
        "empty_negative_tests": lambda m, t: t[0].__setitem__("negative_acceptance", []),
        "required_task_disabled": lambda m, t: t[0].__setitem__("required", False),
        "fake_done": lambda m, t: t[0].__setitem__("status", "DONE"),
        "unknown_scenario": lambda m, t: t[0]["qualification_scenarios"].append("Q99"),
        "missing_scenario": lambda m, t: m["qualification_scenarios"].pop(),
        "missing_component": lambda m, t: m["architecture"]["runtime_components"].pop(),
        "openhands_runtime_dependency": lambda m, t: m["architecture"]["implementation_plane"].__setitem__("product_runtime_requires_openhands", True),
        "external_skill_dependency": lambda m, t: m["architecture"]["implementation_plane"].__setitem__("external_skill_bundle_required", True),
        "unbounded_parallelism": lambda m, t: m["architecture"]["implementation_plane"].__setitem__("maximum_agents", 99),
        "missing_section": lambda m, t: t[0].__setitem__("sections", "999"),
    }
    rejected = []
    for name, change in changes.items():
        candidate_m, candidate_t = copy.deepcopy(metadata), copy.deepcopy(tasks)
        change(candidate_m, candidate_t)
        try:
            validate_data(candidate_m, candidate_t, sections)
        except (ValueError, KeyError, TypeError):
            rejected.append(name)
        else:
            raise ValueError(f"Negative test was not rejected: {name}")
    return {"negative_cases_rejected": len(rejected), "cases": rejected,
            "scope": "DOCUMENT_AND_TASK_GRAPH_ONLY"}


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    raw = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Unsafe relative output path")
    cursor = raw
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError(f"Refusing symlink output path: {cursor}")
        cursor = cursor.parent
    target = raw.resolve()
    if root not in target.parents:
        raise ValueError("Output leaves selected repository")
    return target


def write_all(root: Path, outputs: dict[str, bytes]) -> list[str]:
    checked: list[tuple[Path, bytes]] = []
    for relative, content in outputs.items():
        target = safe_path(root, relative)
        if target.exists() and (not target.is_file() or target.read_bytes() != content):
            raise ValueError(f"Refusing to overwrite different content: {target}")
        checked.append((target, content))
    written = []
    for target, content in checked:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            # Exclusive creation: a concurrent process cannot silently overwrite state.
            with target.open("xb") as handle:
                handle.write(content)
        written.append(str(target))
    return written


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def packet(task_id: str, source_hash: str, tasks: list[dict[str, Any]],
           sections: dict[str, str], max_chars: int) -> str:
    indexed = {t["id"]: t for t in tasks}
    if task_id not in indexed:
        raise ValueError(f"Unknown task: {task_id}")
    task = indexed[task_id]
    selected = {"0", "77", "84"} | referenced_sections(task["sections"], sections)
    selected -= EXCLUDED_PACKET_SECTIONS
    pieces = [f"# Release 1 task: {task_id} — {task['title']}",
              f"Source Markdown SHA-256: `{source_hash}`. Initial contract only; live tracker and merged evidence govern readiness.",
              "Do not run the whole program from this packet. Inspect relevant current repository code and contracts; claim only dependency-ready, lock-safe work.",
              "## Exact resolved task contract", "```json", json.dumps(task, ensure_ascii=False, indent=2), "```",
              "## Dependency titles (initial handoff; not proof of completion)"]
    for dep in task["dependencies"]:
        pieces.append(f"- {dep}: {indexed[dep]['title']}")
    common = re.search(r"(?ms)^## 75\.1 .*?(?=^## 75\.2)", sections["75"])
    if common is None:
        raise ValueError("Common task execution rules missing")
    pieces.append(common.group(0).strip())
    key = lambda value: (int(re.match(r"\d+", value)[0]), value)
    pieces.extend(sections[s] for s in sorted(selected, key=key))
    result = "\n\n".join(pieces).rstrip() + "\n"
    if len(result) > max_chars:
        raise ValueError(f"Packet is {len(result)} characters, above {max_chars}; split task context explicitly. No truncation performed.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "bootstrap", "packet", "frontier"])
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--plan", type=Path, default=Path(PLAN_NAME))
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=Path("docs/implementation/task-packets"))
    parser.add_argument("--max-chars", type=int, default=120000)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.max_chars < 1000:
            raise ValueError("--max-chars must be at least 1000")
        text, metadata, tasks, sections, order = load(args.plan)
        source_hash = digest(args.plan.read_bytes())
        result: dict[str, Any] = {"release": "1.0", "source_sha256": source_hash,
                                 "task_count": len(tasks), "scope": "DOCUMENT_HANDOFF_ONLY"}
        if args.self_test:
            result["self_test"] = self_test(metadata, tasks, sections)
        if args.command == "validate":
            result.update({"status": "PASS", "scenarios": 43, "capabilities": 21,
                           "dependency_graph": "ACYCLIC", "first_task": order[0]})
        elif args.command == "bootstrap":
            ledger = {"release": "1.0", "source_plan_sha256": source_hash,
                      "task_count": len(tasks), "tasks": tasks, "topological_order": order}
            tracker = {"release": "1.0", "source_plan_sha256": source_hash,
                       "implementation_status": "NOT_STARTED", "qualification_status": "NOT_TESTED",
                       "tasks": {t["id"]: {"status": "NOT_STARTED", "commits": [], "evidence": [],
                                             "independent_review": None, "blockers": []} for t in tasks}}
            outputs = {
                "spec/RELEASE_1.md": text.encode("utf-8"),
                "spec/tasks.json": encoded(ledger),
                "spec/runtime-architecture.json": encoded(metadata["architecture"]),
                "spec/qualification-inventory.json": encoded({"scenarios": metadata["scenario_contracts"],
                                                              "capabilities": metadata["capability_contracts"]}),
                "spec/release-1-handoff.json": encoded({"source_plan_sha256": source_hash,
                    "binding_body_sha256": metadata["binding_body_sha256"],
                    "embedded_helper_sha256": metadata["embedded_helper_sha256"],
                    "runtime_status": "NOT_TESTED"}),
                "docs/implementation/TRACKER.json": encoded(tracker),
            }
            result["files"] = write_all(args.repo, outputs)
            result["status"] = "BOOTSTRAPPED_SPEC_ONLY"
        elif args.command == "packet":
            if not args.task_id:
                raise ValueError("packet requires a task ID")
            content = packet(args.task_id, source_hash, tasks, sections, args.max_chars)
            result["files"] = write_all(args.out, {args.task_id + ".md": content.encode("utf-8")})
            result["characters"] = len(content)
            result["status"] = "SPEC_PACKET_ONLY"
        else:
            # This command deliberately reads no mutable tracker: it reports the initial DAG frontier only.
            result["initial_dependency_frontier"] = [t["id"] for t in tasks if not t["dependencies"]]
            result["status"] = "NOT_A_RUNTIME_READINESS_OR_CLAIM_DECISION"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
