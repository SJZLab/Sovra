// Wiring invariant (CLAUDE.md Laws 6 and 7: designed is not wired, UI reachability starts at "/").
// Every UI component that calls the API must be reachable from a Next.js page, and every journey in
// the registry must have its page. A component with a documented reason is listed in UNWIRED.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { JOURNEYS } from "../lib/journeys";

const WEB = resolve(__dirname, "..");

// Components that call the API but are not yet a page of their own, with the reason.
const UNWIRED: Record<string, string> = {
  "components/target-application/github_branch_ref_selector_immutable_sha_resolver.tsx":
    "second step of a flow: it receives a credential from the repository-selection step, which must never travel in a URL; needs a combined repo-to-branch journey",
};

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === ".next") continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(ts|tsx)$/.test(name)) out.push(full);
  }
  return out;
}

function resolveImport(from: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null;
  const base = resolve(dirname(from), spec);
  for (const candidate of [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

function importsOf(file: string): string[] {
  const text = readFileSync(file, "utf-8");
  const specs = [...text.matchAll(/^\s*(?:import|export)\s[^;]*?from\s+["']([^"']+)["']/gm)].map(
    (m) => m[1]!,
  );
  return specs.map((s) => resolveImport(file, s)).filter((p): p is string => p !== null);
}

function reachableFromPages(): Set<string> {
  const seen = new Set<string>();
  const stack = walk(join(WEB, "pages"));
  while (stack.length > 0) {
    const f = stack.pop()!;
    if (seen.has(f)) continue;
    seen.add(f);
    stack.push(...importsOf(f));
  }
  return seen;
}

describe("[WIRING] UI components are reachable from a page", () => {
  it("[WIRING-1] every journey in the registry has its page", () => {
    const missing = JOURNEYS.filter((j) => !existsSync(join(WEB, "pages", `${j.slug}.tsx`))).map(
      (j) => j.slug,
    );
    expect(missing).toEqual([]);
  });

  it("[WIRING-2] every page except the front door and the shell is a registered journey", () => {
    const slugs = new Set(JOURNEYS.map((j) => j.slug));
    const pages = readdirSync(join(WEB, "pages"))
      .filter((n) => n.endsWith(".tsx") && n !== "index.tsx" && !n.startsWith("_"))
      .map((n) => n.replace(/\.tsx$/, ""));
    expect(pages.filter((p) => !slugs.has(p))).toEqual([]);
  });

  it("[WIRING-3] every component that calls the API is reachable from a page, or listed with a reason", () => {
    const reachable = reachableFromPages();
    const apiCallers = walk(join(WEB, "components"))
      .filter((f) => f.endsWith(".tsx"))
      .filter((f) => readFileSync(f, "utf-8").includes("/api/v1/"))
      .map((f) => relative(WEB, f));
    const orphans = apiCallers.filter((rel) => !reachable.has(join(WEB, rel)) && !(rel in UNWIRED));
    expect(orphans).toEqual([]);
  });

  it("[WIRING-4] the documented exceptions still exist and are still unwired", () => {
    const reachable = reachableFromPages();
    for (const rel of Object.keys(UNWIRED)) {
      expect(existsSync(join(WEB, rel))).toBe(true);
      expect(reachable.has(join(WEB, rel))).toBe(false);
    }
  });
});
