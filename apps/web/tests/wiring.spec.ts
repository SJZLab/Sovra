// Wiring invariant (CLAUDE.md Laws 6 and 7: designed is not wired, UI reachability starts at "/").
// Every UI component that calls the API must be reachable from a Next.js page, and every journey in
// the registry must have its page. A component with a documented reason is listed in UNWIRED.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { JOURNEYS } from "../lib/journeys";

const WEB = resolve(__dirname, "..");

// Components that call the API but are not reachable from a page, each with its reason. Empty means
// every API-calling component is wired.
const UNWIRED: Record<string, string> = {
  // FTF-H010-P5 (QUEUE.md, "UNWIRED until FTF-H029 mounts the screens"): the import wizard needs the
  // caller's organization role from a real session, which no page supplies yet (the journey shell is
  // FTF-H029). It is proven in jsdom, real Chromium and against the real routers, but not reachable
  // from "/" yet; its UI proving_symbol stays blank until then (FTF-0309 / H011-P4 precedent).
  "components/model-import/import_model_wizard_ui.tsx":
    "UNWIRED until FTF-H029 mounts it with a real session role",
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
