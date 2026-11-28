// FTF-0603 UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md §72.5 evidence in real Chromium for
// the suite validation/locking screen: viewports 1280/768/390/320, 200% zoom, keyboard Tab order
// with a visible focus outline, axe (WCAG 2.2 AA) in light and dark, en and ar, over idle /
// loaded-with-findings / loaded-passed / locked / error / restricted states with long unbroken
// names (the realistic worst case for overflow).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import type { Route } from "./browser/ux03r_entry";
import { browserLaunchOptions } from "./support/browser_launch";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/ux03r_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const SUITE = "33333333-3333-3333-3333-333333333333";
const BASE = `/api/v1/organizations/${ORG}/projects/${PROJECT}/evaluation-suites/${SUITE}`;
const PROPS = { apiBaseUrl: API, organizationId: ORG, projectId: PROJECT, suiteId: SUITE };

const LONG_NAME = `suite_${"n".repeat(180)}`;
const LONG_CATEGORY = `category_${"c".repeat(90)}`;
const LONG_KEY = `case_${"k".repeat(190)}`;

function suite(over: Record<string, unknown> = {}) {
  return {
    id: SUITE,
    organization_id: ORG,
    project_id: PROJECT,
    agent_manifest_id: "44444444-4444-4444-4444-444444444444",
    version: 1,
    status: "draft",
    purpose: "development",
    name: LONG_NAME,
    grading_logic_version: 1,
    reviewer_id: "reviewer@example.com",
    created_by: "creator@example.com",
    locked_at: null,
    created_at: "2026-11-20T00:00:00+00:00",
    ...over,
  };
}
function evalCase(n: number, over: Record<string, unknown> = {}) {
  return {
    id: `aaaaaaaa-aaaa-aaaa-aaaa-${String(n).padStart(12, "0")}`,
    case_key: n === 1 ? LONG_KEY : `case-${n}`,
    category: LONG_CATEGORY,
    severity: "high",
    critical: true,
    language: "en",
    group_key: `group-${n}`,
    input_json: { prompt: "p" },
    expected_json: { state: "refunded" },
    grader_type: "state_transition",
    grader_config_json: {},
    weight: 1,
    ...over,
  };
}
const CASES = [evalCase(1), evalCase(2, { critical: false, grader_type: "exact_match" })];
const loaded = (cases: unknown[], s = suite()): Route[] => [
  { method: "GET", path: BASE, body: s },
  { method: "GET", path: `${BASE}/cases`, body: { items: cases, next_after: null } },
];

type Step = { click: string } | { fill: [string, string] } | { wait: number };
interface UiState {
  id: string;
  routes: Route[];
  steps?: Step[];
  ready: string;
  /** A modal dialog is open: the page behind it is inert, so the Tab-order walk is the dialog's own. */
  modal?: boolean;
}
const PLAN_STEPS: Step[] = [
  { fill: ["plan-categories", LONG_CATEGORY] },
  { fill: ["plan-min-tasks", "2"] },
  { fill: ["plan-min-critical", "1"] },
];

const STATES: UiState[] = [
  { id: "idle", routes: [], ready: '[data-testid="suite-lock-load-button"]' },
  {
    id: "loading",
    routes: [{ method: "GET", path: BASE, pending: true }],
    steps: [{ click: "suite-lock-load-button" }, { wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "loaded with blocking findings",
    routes: loaded(CASES),
    steps: [{ click: "suite-lock-load-button" }],
    ready: '[data-testid="suite-lock-findings"]',
  },
  {
    id: "loaded and passed",
    routes: loaded(CASES),
    steps: [{ click: "suite-lock-load-button" }, ...PLAN_STEPS],
    ready: '[data-testid="suite-lock-passed"]',
  },
  {
    id: "locked",
    routes: [
      ...loaded(CASES),
      {
        method: "POST",
        path: `${BASE}/lock`,
        body: suite({ status: "locked", locked_at: "2026-11-20T01:00:00+00:00" }),
      },
    ],
    steps: [
      { click: "suite-lock-load-button" },
      ...PLAN_STEPS,
      { click: "suite-lock-lock-button" },
      { click: "suite-lock-confirm-button" },
    ],
    ready: '[data-testid="suite-lock-notice"]',
  },
  {
    id: "confirming lock",
    routes: loaded(CASES),
    steps: [
      { click: "suite-lock-load-button" },
      ...PLAN_STEPS,
      { click: "suite-lock-lock-button" },
    ],
    ready: '[data-testid="suite-lock-confirm"]',
    modal: true,
  },
  {
    id: "lock refused with server findings 409",
    routes: [
      ...loaded(CASES),
      {
        method: "POST",
        path: `${BASE}/lock`,
        status: 409,
        body: {
          detail: {
            code: "suite_lock_validation_failed",
            findings: [
              { code: "missing_critical_category", subject: LONG_CATEGORY, need: null, have: null },
              { code: "insufficient_independent_tasks", subject: null, need: 4, have: 2 },
              { code: "unresolved_expected_output", subject: LONG_KEY, need: null, have: null },
              { code: "suite_sha256_mismatch", subject: null, need: null, have: null },
            ],
          },
        },
      },
    ],
    steps: [
      { click: "suite-lock-load-button" },
      ...PLAN_STEPS,
      { click: "suite-lock-lock-button" },
      { click: "suite-lock-confirm-button" },
    ],
    ready: '[data-testid="suite-lock-server-findings"]',
  },
  {
    id: "final suite with the Section 26 plan",
    routes: loaded(CASES, suite({ purpose: "final" })),
    steps: [{ click: "suite-lock-load-button" }],
    ready: '[data-testid="suite-lock-findings"]',
  },
  {
    id: "already locked",
    routes: loaded(CASES, suite({ status: "locked", locked_at: "2026-11-20T01:00:00+00:00" })),
    steps: [{ click: "suite-lock-load-button" }],
    ready: '[data-testid="suite-lock-already"]',
  },
  {
    id: "load error 503",
    routes: [{ method: "GET", path: BASE, status: 503, body: {} }],
    steps: [{ click: "suite-lock-load-button" }],
    ready: '[data-testid="suite-lock-error"]',
  },
  {
    id: "lock refused 403",
    routes: [
      ...loaded(CASES),
      { method: "POST", path: `${BASE}/lock`, status: 403, body: { detail: "not the reviewer" } },
    ],
    steps: [
      { click: "suite-lock-load-button" },
      ...PLAN_STEPS,
      { click: "suite-lock-lock-button" },
      { click: "suite-lock-confirm-button" },
    ],
    ready: '[data-testid="suite-lock-error"]',
  },
  {
    id: "restricted final suite",
    routes: [
      { method: "GET", path: BASE, body: suite({ purpose: "final" }) },
      {
        method: "GET",
        path: `${BASE}/cases`,
        status: 403,
        body: { detail: "final_suite_content_restricted" },
      },
    ],
    steps: [{ click: "suite-lock-load-button" }],
    ready: '[data-testid="suite-lock-restricted"]',
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
      lib: { entry: ENTRY, name: "UX0603Bundle", formats: ["iife"], fileName: "ux0603" },
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
      `<title>FTF-0603 evidence</title><style>${TOKENS_CSS}</style></head>` +
      `<body><main><div id="root"></div></main></body></html>`,
    { waitUntil: "load" },
  );
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, props }) => {
      const ux = (
        window as unknown as {
          UX0603: { stubFetch: (r: unknown) => void; mount: (p: unknown) => void };
        }
      ).UX0603;
      ux.stubFetch(routes);
      ux.mount(props);
    },
    { routes: state.routes, props: { ...PROPS, locale } },
  );
  for (const step of state.steps ?? []) {
    if ("wait" in step) await page.waitForTimeout(step.wait);
    else if ("click" in step) await page.getByTestId(step.click).click();
    else await page.getByTestId(step.fill[0]).fill(step.fill[1]);
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
    page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
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
  return problems;
}

async function rtlProblems(page: Page, locale: Locale): Promise<string[]> {
  const attrs = await page.evaluate(() => {
    const root = document.querySelector("section[lang]");
    return root ? { dir: root.getAttribute("dir"), lang: root.getAttribute("lang") } : null;
  });
  if (!attrs) return ["rtl: component root <section> missing"];
  const problems: string[] = [];
  if (attrs.dir !== (locale === "ar" ? "rtl" : "ltr")) problems.push(`rtl: root dir=${attrs.dir}`);
  if (attrs.lang !== locale) problems.push(`rtl: root lang=${attrs.lang}`);
  return problems;
}

async function keyboardProblems(page: Page): Promise<string[]> {
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
      return el.matches("button, a[href], input, textarea, select") || el.tabIndex >= 0;
    });
    (window as unknown as { __tabList: HTMLElement[] }).__tabList = list;
    return list.length;
  });
  const problems: string[] = [];
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    document.body.setAttribute("tabindex", "-1");
    document.body.focus();
    document.body.removeAttribute("tabindex");
  });
  for (let i = 0; i < count; i++) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(() => {
      const list = (window as unknown as { __tabList: HTMLElement[] }).__tabList;
      const el = document.activeElement as HTMLElement | null;
      if (!el) return null;
      const cs = getComputedStyle(el);
      return {
        index: list.indexOf(el),
        label: el.getAttribute("data-testid") ?? el.tagName.toLowerCase(),
        outlineStyle: cs.outlineStyle,
        outlineWidth: Number.parseFloat(cs.outlineWidth),
      };
    });
    if (!stop || stop.index !== i) {
      problems.push(`keyboard: Tab stop ${i} landed on ${stop?.label ?? "nothing"}`);
    } else if (stop.outlineStyle === "none" || !(stop.outlineWidth > 0)) {
      problems.push(`focus indicator: ${stop.label} has no visible outline`);
    }
  }
  return problems;
}

/** The open confirmation dialog traps Tab, shows a visible focus ring, and Escape returns focus. */
async function modalProblems(page: Page): Promise<string[]> {
  const problems: string[] = [];
  const inside = () =>
    page.evaluate(() => {
      const dialog = document.querySelector('[role="alertdialog"]');
      const el = document.activeElement as HTMLElement | null;
      if (!dialog || !el) return null;
      const cs = getComputedStyle(el);
      return {
        in: dialog.contains(el),
        outline: cs.outlineStyle !== "none" && Number.parseFloat(cs.outlineWidth) > 0,
        label: el.getAttribute("data-testid") ?? el.tagName.toLowerCase(),
      };
    });
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    const stop = await inside();
    if (!stop?.in) problems.push(`dialog: Tab ${i} escaped to ${stop?.label ?? "nothing"}`);
    else if (!stop.outline) problems.push(`dialog: ${stop.label} has no visible focus ring`);
  }
  await page.keyboard.press("Escape");
  await page.waitForSelector('[role="alertdialog"]', { state: "detached" });
  await page
    .waitForFunction(
      () => document.activeElement?.getAttribute("data-testid") === "suite-lock-lock-button",
      undefined,
      { timeout: 2000 },
    )
    .catch(() => undefined);
  const back = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
  if (back !== "suite-lock-lock-button") problems.push(`dialog: Escape left focus on ${back}`);
  return problems;
}

describe.concurrent("[0603-BROWSER] real-Chromium §72.5 evidence", () => {
  for (const state of STATES) {
    for (const locale of ["en", "ar"] as Locale[]) {
      for (const scheme of ["light", "dark"] as Scheme[]) {
        test(`[${state.id}] ${locale} ${scheme}: axe, viewports, zoom, keyboard, focus indicator, rtl`, async () => {
          const page = await openState(state, locale, scheme);
          try {
            const problems = [
              ...(await axeProblems(page)),
              ...(await rtlProblems(page, locale)),
              ...(state.modal ? await modalProblems(page) : await keyboardProblems(page)),
              ...(await overflowProblems(page)),
            ];
            expect(problems, problems.join("\n")).toEqual([]);
          } finally {
            await page.close();
          }
        }, 60_000);
      }
    }
  }
});

describe("[0603-BROWSER] behaviour in a real layout engine", () => {
  test("a failed load takes focus on its own alert, never leaving it on <body>", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "load error 503")!,
      "en",
      "light",
    );
    try {
      expect(await page.evaluate(() => document.activeElement?.getAttribute("data-testid"))).toBe(
        "suite-lock-error",
      );
    } finally {
      await page.close();
    }
  });

  test("the lock control is disabled while findings exist and enabled once the plan passes", async () => {
    const blocked = await openState(
      STATES.find((s) => s.id === "loaded with blocking findings")!,
      "en",
      "light",
    );
    try {
      expect(await blocked.getByTestId("suite-lock-lock-button").isDisabled()).toBe(true);
    } finally {
      await blocked.close();
    }
    const passed = await openState(
      STATES.find((s) => s.id === "loaded and passed")!,
      "en",
      "light",
    );
    try {
      expect(await passed.getByTestId("suite-lock-lock-button").isDisabled()).toBe(false);
    } finally {
      await passed.close();
    }
  });
});
