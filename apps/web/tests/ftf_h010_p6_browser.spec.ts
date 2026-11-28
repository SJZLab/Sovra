// FTF-H010-P6 -- real-Chromium §72.5 evidence for the model version panel: every state x en/ar x
// light/dark at 1280/768/390/320 and 200% zoom, keyboard-only Tab order with a visible focus
// outline, axe (wcag2a..22aa) with 0 violations, dialog focus (open, Escape, return, after
// confirm), and 0 px horizontal overflow with a 2048-character unbroken provider ref, a
// 256-character fingerprint, a sha256 digest and a 200-character assigner id. Same harness
// technique as ftf_h010_p5_browser.spec.ts.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { browserLaunchOptions } from "./support/browser_launch";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import type { Route } from "./browser/h010_p6_entry";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/h010_p6_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const VER = "33333333-3333-3333-3333-333333333333";
const PARENT = "44444444-4444-4444-4444-444444444444";
const ITEM = `/api/v1/organizations/${ORG}/projects/${PROJECT}/model-versions/${VER}`;
const ROLES = `${ITEM}/roles`;
const ACTIONS = `${ITEM}/actions`;

const LONG_REF = `ft:${"r".repeat(2045)}`;
const LONG_FP = `fp_${"f".repeat(253)}`;
const LONG_ACTOR = `user_${"a".repeat(195)}`;
const DIGEST = `sha256:${"a".repeat(64)}`;
const ACTION_NAMES = [
  "evaluate",
  "compare",
  "continue_tuning",
  "exact_resume",
  "request_qualification",
  "retain_incumbent",
  "deploy_route",
  "retire",
];

function item(extra: Record<string, unknown> = {}) {
  return {
    id: VER,
    organization_id: ORG,
    project_id: PROJECT,
    version_number: 7,
    import_kind: "endpoint",
    trainable_artifact_id: null,
    weights_digest: null,
    endpoint_reference: null,
    base_model_revision: null,
    origin: "external_endpoint",
    lineage_completeness: "unknown",
    adapter_only: false,
    parent_model_version_id: null,
    external_provider_model_ref: LONG_REF,
    observed_deployment_fingerprint: LONG_FP,
    created_by: "seed",
    created_at: "2026-11-01T00:00:00+00:00",
    ...extra,
  };
}
const WEIGHTS = item({
  import_kind: "full_weight",
  origin: "imported_weights",
  lineage_completeness: "partial",
  weights_digest: DIGEST,
  parent_model_version_id: PARENT,
  external_provider_model_ref: null,
  observed_deployment_fingerprint: null,
});
function role(r: string, extra: Record<string, unknown> = {}) {
  return {
    id: `role-${r}`,
    organization_id: ORG,
    project_id: PROJECT,
    model_version_id: VER,
    role: r,
    assigned_by: LONG_ACTOR,
    assigned_at: "2026-11-02T00:00:00+00:00",
    idempotency_key: "k",
    revoked_at: null,
    revoked_by: null,
    revoke_reason_code: null,
    ...extra,
  };
}
function decisions(over: Record<string, unknown> = {}) {
  const actions: Record<string, unknown> = {};
  for (const a of ACTION_NAMES) actions[a] = { allowed: true, code: null, plan: null };
  return {
    model_version_id: VER,
    origin: "x",
    validated: true,
    roles: [],
    actions: { ...actions, ...over },
  };
}
const ENDPOINT_ACTIONS = decisions({
  continue_tuning: { allowed: false, code: "endpoint_not_trainable", plan: null },
  exact_resume: { allowed: false, code: "no_trusted_checkpoint", plan: null },
  request_qualification: { allowed: false, code: "prerequisite_not_available", plan: null },
  retain_incumbent: { allowed: false, code: "not_current_production", plan: null },
  deploy_route: { allowed: false, code: "a_reason_from_the_future", plan: null },
  retire: { allowed: false, code: "prerequisite_not_available", plan: null },
});
const WEIGHTS_ACTIONS = decisions({
  continue_tuning: { allowed: true, code: null, plan: { kind: "new_run", start_step: 0 } },
  exact_resume: {
    allowed: true,
    code: null,
    plan: { kind: "exact_resume", start_step: 1200, checkpoint_id: "c", run_id: "r" },
  },
});
const UNVALIDATED_ACTIONS = decisions(
  Object.fromEntries(
    ACTION_NAMES.map((a) => [a, { allowed: false, code: "import_not_validated", plan: null }]),
  ),
);
const ROLE_ROWS = [
  role("teacher"),
  role("candidate", { id: "role-old", revoked_at: "2026-11-03T00:00:00+00:00" }),
];
const base = (over: { item?: unknown; roles?: unknown; actions?: unknown } = {}): Route[] => [
  { method: "GET", path: ROLES, body: over.roles ?? ROLE_ROWS },
  { method: "GET", path: ACTIONS, body: over.actions ?? ENDPOINT_ACTIONS },
  { method: "GET", path: ITEM, body: over.item ?? item() },
];

const PROPS = { apiBaseUrl: API, organizationId: ORG, projectId: PROJECT, modelVersionId: VER };

type Step = { select: [RegExp, string] } | { tid: string } | { wait: number };
interface UiState {
  id: string;
  routes: Route[];
  steps?: Step[];
  ready: string;
  noControlsExpected?: boolean;
}
const ROLE_LABEL = /Role to assign|الدور المراد تعيينه/;
const STATES: UiState[] = [
  {
    id: "loading",
    routes: [
      { method: "GET", path: ROLES, pending: true },
      { method: "GET", path: ACTIONS, pending: true },
      { method: "GET", path: ITEM, pending: true },
    ],
    ready: '[aria-busy="true"]',
  },
  {
    id: "endpoint populated with long values",
    routes: base(),
    ready: '[data-testid="decision-retire"]',
  },
  {
    id: "weights populated, no roles",
    routes: base({ item: WEIGHTS, roles: [], actions: WEIGHTS_ACTIONS }),
    ready: '[data-testid="plan-exact_resume"]',
  },
  {
    id: "unvalidated version",
    routes: base({
      item: item({ observed_deployment_fingerprint: null }),
      roles: [],
      actions: UNVALIDATED_ACTIONS,
    }),
    ready: '[data-testid="reason-retire"]',
  },
  {
    id: "every load fails",
    routes: [
      { method: "GET", path: ROLES, status: 500, body: {} },
      { method: "GET", path: ACTIONS, malformedJson: true },
      { method: "GET", path: ITEM, status: 404, body: { detail: "not_found" } },
    ],
    ready: '[data-testid="actions-error"]',
  },
  {
    id: "assign dialog open",
    routes: base(),
    steps: [{ select: [ROLE_LABEL, "judge"] }, { tid: "assign-open" }],
    ready: '[data-testid="role-dialog"]',
  },
  {
    id: "revoke dialog open",
    routes: base(),
    steps: [{ tid: "revoke-teacher" }],
    ready: '[data-testid="role-dialog"]',
  },
  {
    id: "assign refused 403",
    routes: [
      { method: "POST", path: ROLES, status: 403, body: { detail: "forbidden" } },
      ...base(),
    ],
    steps: [
      { select: [ROLE_LABEL, "current_production"] },
      { tid: "assign-open" },
      { tid: "dialog-confirm" },
    ],
    ready: '[data-testid="role-error"]',
  },
  {
    id: "assign lost response",
    routes: [{ method: "POST", path: ROLES, reject: true }, ...base()],
    steps: [{ tid: "assign-open" }, { tid: "dialog-confirm" }],
    ready: '[data-testid="role-retry"]',
  },
  {
    id: "assigned notice",
    routes: [
      { method: "POST", path: ROLES, status: 201, body: role("candidate"), echoKey: true },
      ...base(),
    ],
    steps: [{ tid: "assign-open" }, { tid: "dialog-confirm" }],
    ready: '[data-testid="role-notice"]',
  },
  {
    id: "revoke already revoked",
    routes: [
      {
        method: "POST",
        path: `${ROLES}/teacher/revoke`,
        status: 409,
        body: { detail: "role_not_assigned" },
      },
      ...base(),
    ],
    steps: [{ tid: "revoke-teacher" }, { tid: "dialog-confirm" }],
    ready: '[data-testid="role-notice"]',
  },
];
type Locale = "en" | "ar";
type Scheme = "light" | "dark";
const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
  { name: "narrow", width: 320, height: 640 },
] as const;
const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

let browser: Browser;
let bundle = "";

beforeAll(async () => {
  const result = await build({
    configFile: false,
    mode: "production",
    oxc: { jsx: { runtime: "automatic", development: false } },
    logLevel: "silent",
    root: resolve(__dirname, "../../.."),
    define: { "process.env.NODE_ENV": JSON.stringify("production") },
    build: {
      write: false,
      minify: false,
      lib: { entry: ENTRY, name: "H010P6Bundle", formats: ["iife"], fileName: "h010p6" },
    },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap(
    (r) => ("output" in r ? r.output : []) as { type: string; code?: string }[],
  );
  bundle = outputs.find((o) => o.type === "chunk")?.code ?? "";
  expect(bundle.length, "the in-memory bundle must be built").toBeGreaterThan(1000);
  browser = await chromium.launch(browserLaunchOptions());
}, 120_000);

afterAll(async () => {
  await browser?.close();
});

async function openState(state: UiState, locale: Locale, scheme: Scheme): Promise<Page> {
  const page = await browser.newPage({
    colorScheme: scheme,
    viewport: { width: 1280, height: 900 },
  });
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  const dir = locale === "ar" ? "rtl" : "ltr";
  await page.setContent(
    `<!doctype html><html lang="${locale}" dir="${dir}"><head><meta charset="utf-8" />` +
      `<meta name="viewport" content="width=device-width, initial-scale=1" />` +
      `<title>FTF-H010-P6 evidence</title><style>${TOKENS_CSS}</style></head>` +
      `<body><main><div id="root"></div></main></body></html>`,
    { waitUntil: "load" },
  );
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, props }) => {
      const ux = (
        window as unknown as {
          H010P6: { stubFetch: (r: unknown) => void; mount: (p: unknown) => void };
        }
      ).H010P6;
      ux.stubFetch(routes);
      ux.mount(props);
    },
    { routes: state.routes, props: { ...PROPS, locale } },
  );
  for (const step of state.steps ?? []) {
    if ("wait" in step) await page.waitForTimeout(step.wait);
    else if ("select" in step) await page.getByLabel(step.select[0]).selectOption(step.select[1]);
    else await page.getByTestId(step.tid).click();
  }
  try {
    await page.waitForSelector(state.ready, { state: "visible", timeout: 10_000 });
  } catch (error) {
    throw new Error(
      `state "${state.id}" never became ready: ${pageErrors.join(" | ") || String(error).slice(0, 200)}`,
    );
  }
  return page;
}

async function axeProblems(page: Page): Promise<string[]> {
  const violations = (await page.evaluate(async (tags) => {
    // @ts-expect-error -- axe is injected globally by the script tag above.
    const r = await axe.run(document, { runOnly: { type: "tag", values: tags } });
    return r.violations.map(
      (v: {
        id: string;
        impact: string;
        nodes: { target: unknown; failureSummary?: string }[];
      }) => ({
        id: v.id,
        impact: v.impact,
        nodes: v.nodes
          .slice(0, 3)
          .map((n) => `${JSON.stringify(n.target)} ${n.failureSummary?.split("\n")[1] ?? ""}`),
      }),
    );
  }, AXE_TAGS)) as { id: string; impact: string; nodes: string[] }[];
  return violations.map((v) => `axe ${v.id} (${v.impact}): ${v.nodes.join(" | ")}`);
}

async function overflowProblems(page: Page): Promise<string[]> {
  const problems: string[] = [];
  const measure = () =>
    page.evaluate(() => {
      const el = document.documentElement;
      return el.scrollWidth - el.clientWidth;
    });
  for (const vp of VIEWPORTS) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    const over = await measure();
    if (over > 1) problems.push(`overflow ${vp.name} ${vp.width}x${vp.height}: ${over}px`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => {
    (document.documentElement.style as unknown as { zoom: string }).zoom = "2";
  });
  const zoomed = await measure();
  if (zoomed > 1) problems.push(`overflow at 200% zoom: ${zoomed}px`);
  await page.evaluate(() => {
    (document.documentElement.style as unknown as { zoom: string }).zoom = "1";
  });
  return problems;
}

async function rtlProblems(page: Page, locale: Locale): Promise<string[]> {
  const attrs = await page.evaluate(() => {
    const root = document.querySelector('[data-testid="model-version-panel"]');
    return root ? { dir: root.getAttribute("dir"), lang: root.getAttribute("lang") } : null;
  });
  if (!attrs) return ["rtl: component root missing"];
  const want = { dir: locale === "ar" ? "rtl" : "ltr", lang: locale };
  const problems: string[] = [];
  if (attrs.dir !== want.dir) problems.push(`rtl: root dir=${attrs.dir}, want ${want.dir}`);
  if (attrs.lang !== want.lang) problems.push(`rtl: root lang=${attrs.lang}, want ${want.lang}`);
  return problems;
}

interface Stop {
  index: number;
  label: string;
  outlineStyle: string;
  outlineWidth: number;
}

async function keyboardProblems(page: Page, noControlsExpected: boolean): Promise<string[]> {
  const count = await page.evaluate(() => {
    const visible = (el: Element) => {
      const cs = getComputedStyle(el);
      return el.getClientRects().length > 0 && cs.visibility !== "hidden" && cs.display !== "none";
    };
    const list = Array.from(
      document.querySelectorAll<HTMLElement>(
        "button, a[href], input, textarea, select, [tabindex]",
      ),
    ).filter((el) => {
      if ((el as HTMLButtonElement).disabled || !visible(el)) return false;
      const native = el.matches("button, a[href], input, textarea, select");
      return native || el.tabIndex >= 0;
    });
    // A radio group is ONE Tab stop in a browser (the checked radio, else the first).
    const seen = new Set<string>();
    const stops = list.filter((el) => {
      if (!(el instanceof HTMLInputElement) || el.type !== "radio") return true;
      const key = el.name;
      const group = list.filter(
        (o) => o instanceof HTMLInputElement && o.type === "radio" && o.name === key,
      );
      const rep = group.find((o) => (o as HTMLInputElement).checked) ?? group[0];
      if (el !== rep || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    list.splice(0, list.length, ...stops);
    (window as unknown as { __tabList: HTMLElement[] }).__tabList = list;
    return list.length;
  });
  if (count === 0)
    return noControlsExpected ? [] : ["keyboard: no focusable control in this state"];
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    document.body.setAttribute("tabindex", "-1");
    document.body.focus();
    document.body.removeAttribute("tabindex");
  });
  const stops: (Stop | null)[] = [];
  for (let i = 0; i < count; i++) {
    await page.keyboard.press("Tab");
    stops.push(
      await page.evaluate(() => {
        const list = (window as unknown as { __tabList: HTMLElement[] }).__tabList;
        const el = document.activeElement as HTMLElement | null;
        if (!el) return null;
        const cs = getComputedStyle(el);
        return {
          index: list.indexOf(el),
          label: `${el.tagName.toLowerCase()}${el.getAttribute("data-testid") ? `[${el.getAttribute("data-testid")}]` : ""}`,
          outlineStyle: cs.outlineStyle,
          outlineWidth: Number.parseFloat(cs.outlineWidth),
        };
      }),
    );
  }
  const problems: string[] = [];
  stops.forEach((stop, i) => {
    // A radio group is one Tab stop in a browser, so group members share a stop.
    if (!stop) {
      problems.push(`keyboard: Tab stop ${i} landed on nothing`);
      return;
    }
    if (stop.outlineStyle === "none" || !(stop.outlineWidth > 0))
      problems.push(`focus indicator: ${stop.label} has no visible outline`);
  });
  return problems;
}

describe.concurrent("[H010P6-BROWSER] real-Chromium §72.5 evidence", () => {
  for (const state of STATES) {
    for (const locale of ["en", "ar"] as Locale[]) {
      for (const scheme of ["light", "dark"] as Scheme[]) {
        test(`[${state.id}] ${locale} ${scheme}: axe, viewports, zoom, keyboard, focus indicator, rtl`, async () => {
          const page = await openState(state, locale, scheme);
          try {
            const problems: string[] = [];
            problems.push(...(await axeProblems(page)));
            problems.push(...(await rtlProblems(page, locale)));
            problems.push(...(await keyboardProblems(page, state.noControlsExpected === true)));
            problems.push(...(await overflowProblems(page)));
            expect(problems, problems.join("\n")).toEqual([]);
          } finally {
            await page.close();
          }
        }, 60_000);
      }
    }
  }
});

describe("[H010P6-BROWSER] focus, dialogs, wrap and bilingual behaviour in a real browser", () => {
  const find = (id: string) => STATES.find((s) => s.id === id)!;
  const activeTestId = (page: Page) =>
    page.evaluate(
      () => document.activeElement?.getAttribute("data-testid") ?? document.activeElement?.tagName,
    );

  test("the assign dialog has an accessible name, takes focus, Escape cancels and focus returns to the opener", async () => {
    const page = await openState(find("assign dialog open"), "en", "light");
    try {
      const dialog = page.getByRole("dialog", { name: "Assign this role?" });
      expect(await dialog.count()).toBe(1);
      expect(await dialog.innerText()).toContain("Judge");
      expect(
        await page.evaluate(() => !!document.activeElement?.closest('[data-testid="role-dialog"]')),
      ).toBe(true);
      await page.keyboard.press("Escape");
      await page.waitForSelector('[data-testid="role-dialog"]', { state: "detached" });
      await page.waitForFunction(
        () => document.activeElement?.getAttribute("data-testid") === "assign-open",
        undefined,
        { timeout: 5000 },
      );
    } finally {
      await page.close();
    }
  }, 60_000);

  test("the revoke dialog is an alertdialog naming the role and the version; Keep returns focus", async () => {
    const page = await openState(find("revoke dialog open"), "en", "light");
    try {
      const dialog = page.getByRole("alertdialog", { name: "Revoke this role?" });
      expect(await dialog.innerText()).toMatch(/Teacher[\s\S]*7/);
      await page.getByTestId("dialog-cancel").click();
      await page.waitForFunction(
        () => document.activeElement?.getAttribute("data-testid") === "revoke-teacher",
        undefined,
        { timeout: 5000 },
      );
    } finally {
      await page.close();
    }
  }, 60_000);

  for (const id of [
    "assigned notice",
    "assign refused 403",
    "assign lost response",
    "revoke already revoked",
  ]) {
    test(`after confirm (${id}) focus lands on the outcome region, never <body>`, async () => {
      const page = await openState(find(id), "en", "light");
      try {
        await page.waitForFunction(
          () => document.activeElement?.getAttribute("data-testid") === "role-outcome",
          undefined,
          { timeout: 5000 },
        );
        expect(await activeTestId(page)).toBe("role-outcome");
      } finally {
        await page.close();
      }
    }, 60_000);
  }

  test("a refused write is a role=alert with the server's code; a settled write is a role=status", async () => {
    let page = await openState(find("assign refused 403"), "en", "light");
    try {
      expect(await page.getByTestId("role-error").getAttribute("role")).toBe("alert");
      expect(await page.getByTestId("role-error").innerText()).toContain("(forbidden)");
    } finally {
      await page.close();
    }
    page = await openState(find("revoke already revoked"), "en", "light");
    try {
      expect(await page.getByTestId("role-notice").getAttribute("role")).toBe("status");
      expect(await page.locator('[role="alert"]').count()).toBe(0);
    } finally {
      await page.close();
    }
  }, 60_000);

  test("the actions table renders no control for any action; an unknown reason reads Not available", async () => {
    const page = await openState(find("endpoint populated with long values"), "en", "light");
    try {
      expect(
        await page
          .locator('[data-testid="actions-section"] button, [data-testid="actions-section"] a')
          .count(),
      ).toBe(0);
      expect(await page.getByTestId("decision-deploy_route").innerText()).toContain(
        "Not available",
      );
      expect(await page.getByTestId("decision-continue_tuning").innerText()).toContain(
        "Not available",
      );
      expect(await page.getByTestId("lineage-digest").innerText()).toBe("No training artifact");
    } finally {
      await page.close();
    }
  }, 60_000);

  test("a 2048-char ref, 256-char fingerprint, sha256 digest and 200-char actor never scroll the page at 320 px or 200% zoom", async () => {
    for (const id of ["endpoint populated with long values", "weights populated, no roles"]) {
      const page = await openState(find(id), "en", "light");
      try {
        const problems = await overflowProblems(page);
        expect(problems, `${id}: ${problems.join(" ")}`).toEqual([]);
      } finally {
        await page.close();
      }
    }
  }, 60_000);

  test("under ar the visible UI text has no English word outside user data", async () => {
    for (const id of [
      "endpoint populated with long values",
      "every load fails",
      "assign refused 403",
    ]) {
      const page = await openState(find(id), "ar", "light");
      try {
        const text = await page.evaluate(() => {
          const clone = document
            .querySelector('[data-testid="model-version-panel"]')!
            .cloneNode(true) as HTMLElement;
          clone.querySelectorAll("bdi").forEach((b) => b.remove());
          return clone.innerText;
        });
        expect(text, id).not.toMatch(/[A-Za-z]{3,}/);
      } finally {
        await page.close();
      }
    }
  }, 60_000);

  test("the role select is described by its help text", async () => {
    const page = await openState(find("weights populated, no roles"), "en", "light");
    try {
      const described = await page.evaluate(() => {
        const select = document.querySelector("select")!;
        return (select.getAttribute("aria-describedby") ?? "")
          .split(" ")
          .map((id) => document.getElementById(id)?.textContent ?? "")
          .join(" ");
      });
      expect(described).toMatch(/the server decides/);
    } finally {
      await page.close();
    }
  }, 60_000);
});
