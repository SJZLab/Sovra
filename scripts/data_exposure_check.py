#!/usr/bin/env python3
"""data_exposure_check.py -- Law 18 (SECURE BY DEFAULT) merge-blocking gate.

Every persisted datastore/storage surface must have an explicit access-control
policy on record, defaulting to deny. This is a structural backstop. It does not
verify that policy logic is correct; product tests do that. It catches the two
shapes that ship silently: a surface created with no policy at all, and a
policy that evaluates to allow-all.

Backends are those Sovra actually uses (CLAUDE.md DATASTORE_BACKENDS):

  postgres_rls  -- every `op.create_table("<name>", ...)` in an Alembic
                   migration needs, for that same table:
                     ALTER TABLE <name> ENABLE ROW LEVEL SECURITY
                     ALTER TABLE <name> FORCE ROW LEVEL SECURITY
                     CREATE POLICY ... ON <name>
                   and no policy on it may be `USING (true)`.
  s3_identity   -- SeaweedFS/S3 identity files must not define an `anonymous`
                   identity (unauthenticated access).

Escape hatch: a line containing `DATA-EXPOSURE-REVIEWED: <table or file> --
<reason>` in the same file skips that surface. It is explicit and reviewable,
never silent; the watchdog flags every new one.

Exit 0 = clean, 1 = exposure found (with --fail-on-exposure), 2 = misconfigured.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS_GLOB = "apps/api/alembic/versions/*.py"
S3_IDENTITY_GLOB = "infra/**/*s3-identity*.json"
ESCAPE = re.compile(r"DATA-EXPOSURE-REVIEWED:\s*([\w.\-/]+)\s*--\s*\S")

CREATE_TABLE = re.compile(r"op\.create_table\(\s*[\"'](\w+)[\"']", re.S)
SQL_CREATE_TABLE = re.compile(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?[\"`]?(?:\w+\.)?(\w+)", re.I)


def _escaped(text: str) -> set[str]:
    return {m.group(1) for m in ESCAPE.finditer(text)}


LOOP = re.compile(r"^([ \t]*)for (\w+) in [\(\[]([^\)\]]*)[\)\]]:\s*$", re.M)


def _expand_table_loops(text: str) -> str:
    """Migrations often apply RLS in `for table in ("a", "b"):` with f-string
    `{table}` placeholders. Append one substituted copy of each loop body per
    name, so the per-table checks see the statements the loop really runs."""
    lines = text.splitlines()
    extra: list[str] = []
    for m in LOOP.finditer(text):
        indent, var, names_src = len(m.group(1)), m.group(2), m.group(3)
        names = re.findall(r"[\"'](\w+)[\"']", names_src)
        start = text[: m.end()].count("\n") + 1
        body: list[str] = []
        for line in lines[start:]:
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            body.append(line)
        block = "\n".join(body)
        for name in names:
            extra.append(block.replace("{" + var + "}", name))
    return text + "\n" + "\n".join(extra)


def check_postgres_rls(root: pathlib.Path) -> list[str]:
    findings: list[str] = []
    files = sorted(root.glob(MIGRATIONS_GLOB))
    if not files:
        return [f"postgres_rls: no migrations matched {MIGRATIONS_GLOB} -- nothing to check"]
    for f in files:
        text = _expand_table_loops(f.read_text(errors="ignore"))
        escaped = _escaped(text)
        tables = set(CREATE_TABLE.findall(text)) | set(SQL_CREATE_TABLE.findall(text))
        for t in sorted(tables):
            if t in escaped:
                continue
            rel = f.relative_to(root)
            if not re.search(rf"ALTER TABLE\s+{t}\s+ENABLE ROW LEVEL SECURITY", text, re.I):
                findings.append(f"{rel}: table '{t}' has no ENABLE ROW LEVEL SECURITY")
            if not re.search(rf"ALTER TABLE\s+{t}\s+FORCE ROW LEVEL SECURITY", text, re.I):
                findings.append(f"{rel}: table '{t}' has no FORCE ROW LEVEL SECURITY")
            policy_re = rf"CREATE POLICY\s+\w+\s+ON\s+{t}\b(.*?)[\"']\s*\)"
            policies = re.findall(policy_re, text, re.I | re.S)
            if not policies:
                findings.append(f"{rel}: table '{t}' has no CREATE POLICY")
            for body in policies:
                if re.search(r"USING\s*\(\s*true\s*\)", body, re.I):
                    findings.append(f"{rel}: table '{t}' has an allow-all policy USING (true)")
    return findings


def check_s3_identity(root: pathlib.Path) -> list[str]:
    findings: list[str] = []
    for f in sorted(root.glob(S3_IDENTITY_GLOB)):
        text = f.read_text(errors="ignore")
        rel = f.relative_to(root)
        if str(rel) in _escaped(text):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            findings.append(f"{rel}: unparseable identity file ({exc})")
            continue
        for ident in data.get("identities", []):
            if str(ident.get("name", "")).lower() == "anonymous" or not ident.get("credentials"):
                name = ident.get("name")
                findings.append(f"{rel}: identity '{name}' allows unauthenticated access")
    return findings


FINDERS = {"postgres_rls": check_postgres_rls, "s3_identity": check_s3_identity}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backends", default="", help="comma-separated DATASTORE_BACKENDS")
    p.add_argument("--root", default=str(REPO))
    p.add_argument("--fail-on-exposure", action="store_true")
    args = p.parse_args(argv)

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    if not backends:
        print("N/A -- no datastore backends declared")
        return 0
    unknown = [b for b in backends if b not in FINDERS]
    if unknown:
        print(f"MISCONFIGURED: no finder for backend(s) {unknown}")
        return 2

    root = pathlib.Path(args.root)
    findings = [f for b in backends for f in FINDERS[b](root)]
    if findings:
        print("DATASTORE EXPOSURE RISK:")
        for f in findings:
            print(f"  {f}")
        return 1 if args.fail_on_exposure else 0
    print(f"data_exposure_check OK -- {len(backends)} backend(s) checked: {', '.join(backends)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
