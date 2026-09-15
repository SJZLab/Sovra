#!/usr/bin/env python3
"""
FTF-0001 — Initialize monorepo.
Proves the workspace layout described in docs/architecture/ARCHITECTURE.md
section 2 (seven logical components) actually exists on disk, with a root
quick-start and per-component manifests a package manager can resolve.

Run directly: python3 tests/test_ftf_0001_monorepo_layout.py
Also collectible by pytest once FTF-0003 (test harness) lands.
"""
import json
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# path -> manifest filename expected in that path (None if none required)
COMPONENTS = {
    "apps/web": "package.json",
    "apps/api": "pyproject.toml",
    "workers/training": "pyproject.toml",
    "workers/evaluation": "pyproject.toml",
    "workers/data": "pyproject.toml",
    "workers/deploy": "pyproject.toml",
    "packages/shared": "package.json",
}

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def test_root_readme_has_quickstart():
    readme = ROOT / "README.md"
    check(
        "FTF-0001", "root README.md exists",
        readme.exists(),
    )
    if readme.exists():
        text = readme.read_text().lower()
        check(
            "FTF-0001", "root README.md has a Quick start section",
            "quick start" in text,
        )


def test_root_js_workspace_config():
    pkg = ROOT / "package.json"
    check("FTF-0001", "root package.json exists", pkg.exists())
    if pkg.exists():
        data = json.loads(pkg.read_text())
        check(
            "FTF-0001", "root package.json declares workspaces",
            isinstance(data.get("workspaces"), list) and len(data["workspaces"]) > 0,
        )
    pnpm_ws = ROOT / "pnpm-workspace.yaml"
    check("FTF-0001", "root pnpm-workspace.yaml exists", pnpm_ws.exists())


def test_root_python_workspace_config():
    pyproject = ROOT / "pyproject.toml"
    check("FTF-0001", "root pyproject.toml exists", pyproject.exists())
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text())
        members = data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
        check(
            "FTF-0001", "root pyproject.toml declares a uv workspace with members",
            isinstance(members, list) and len(members) > 0,
        )


def test_gitignore_covers_build_artifacts():
    gi = ROOT / ".gitignore"
    check("FTF-0001", ".gitignore exists", gi.exists())
    if gi.exists():
        text = gi.read_text()
        for entry in ("node_modules", "__pycache__", ".venv"):
            check("FTF-0001", f".gitignore covers {entry}", entry in text)


def test_seven_components_present():
    for rel, manifest_name in COMPONENTS.items():
        comp_dir = ROOT / rel
        check("FTF-0001", f"{rel} directory exists", comp_dir.is_dir())
        check("FTF-0001", f"{rel}/README.md exists", (comp_dir / "README.md").is_file())
        check(
            "FTF-0001", f"{rel}/{manifest_name} exists",
            (comp_dir / manifest_name).is_file(),
        )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0001 monorepo layout: {len(FAILURES)} check(s) failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-0001 monorepo layout: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
