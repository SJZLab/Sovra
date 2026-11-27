#!/usr/bin/env python3
"""List what to work on next: uncertified tasks whose dependencies are all certified, ordered by
how many tasks (transitively) wait on each. Reads spec/tasks.json and docs/spec-matrix.yaml only."""

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

    open_tasks = [t for t in tasks if t["id"] not in certified]
    ready = [t for t in open_tasks if all(d in certified for d in t.get("dependencies", []))]
    ready.sort(key=lambda t: (-waiting(t["id"]), t["id"]))
    print(f"{len(certified)}/{len(matrix)} certified; {len(open_tasks)} open; {len(ready)} ready")
    for t in ready:
        print(f"{waiting(t['id']):4d} waiting  {t['id']:<10} {t['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
