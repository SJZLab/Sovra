#!/usr/bin/env python3
"""List what to work on next: uncertified tasks whose dependencies are all certified or merged
PROVISIONAL, ordered by how many tasks (transitively) wait on each. A task that is itself
PROVISIONAL is merged and only awaits certification, so it is not listed. Reads spec/tasks.json
and docs/spec-matrix.yaml only."""

import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DONE = {"CERTIFIED", "PROMOTED"}


def main() -> int:
    tasks = json.loads((ROOT / "spec" / "tasks.json").read_text())["tasks"]
    matrix = yaml.safe_load((ROOT / "docs" / "spec-matrix.yaml").read_text()) or {}
    certified = {k for k, e in matrix.items() if e.get("certification_state") in DONE}
    merged = {k for k, e in matrix.items() if e.get("certification_state") == "PROVISIONAL"}
    usable = certified | merged
    children: dict[str, set[str]] = {}
    for t in tasks:
        for dep in t.get("dependencies", []):
            children.setdefault(dep, set()).add(t["id"])

    def waiting(task_id: str) -> int:
        seen: set[str] = set()
        stack = [task_id]
        while stack:
            for child in children.get(stack.pop(), ()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return len(seen)

    open_tasks = [t for t in tasks if t["id"] not in usable]
    ready = [t for t in open_tasks if all(d in usable for d in t.get("dependencies", []))]
    ready.sort(key=lambda t: (-waiting(t["id"]), t["id"]))
    print(
        f"{len(certified)}/{len(matrix)} certified; {len(merged)} merged awaiting certification; "
        f"{len(open_tasks)} open; {len(ready)} ready"
    )
    for t in ready:
        pending = sorted(d for d in t.get("dependencies", []) if d in merged)
        note = f"  (on PROVISIONAL: {', '.join(pending)})" if pending else ""
        print(f"{waiting(t['id']):4d} waiting  {t['id']:<10} {t['title']}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
