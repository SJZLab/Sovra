// FTF-UX06 round 2, point 1 (QUEUE 0f; UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md
// §72.5): the real-Chromium §72.5 evidence round 1 deferred for the evaluation case editor/import
// screen -- viewports 1280/768/390/320, 200% zoom, keyboard-only Tab order with a visible focus
// outline, and an axe scan (light AND dark), over load/populated/empty/error/edit states, en and
// ar. The component loads data in effects and on click, so the page mounts the real component
// from a client bundle (vite, in memory) and replaces `fetch` with a route table, the same
// technique `ftf_ux03_browser.spec.ts` already uses.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import type { Route } from "./browser/ux06_entry";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/ux06_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const SUITE = "33333333-3333-3333-3333-333333333333";
const CASE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const LIST_PATH = `/api/v1/organizations/${ORG}/projects/${PROJECT}/evaluation-suites/${SUITE}/cases`;
const CASE_PATH = `${LIST_PATH}/${CASE_A}`;

const PROPS = { apiBaseUrl: API, organizationId: ORG, projectId: PROJECT, suiteId: SUITE };

// Long, unbroken (no space/hyphen) values -- the realistic worst case this round's overflow fix
// targets (OVERFLOW-LONG-NAMES). `MAX_CASE_KEY` is the API's own stated maximum (QUEUE.md 0f,
// point 2: "the API allows 200") -- the second L2 review's own probe, reproduced here as a RED
// pin on the edit form heading and the field-error list item (the two sites its probe found
// unpinned; the case-row `<li>` and `edit-case-key` `<h3>` sites below were already pinned).
const LONG_CATEGORY = `refunds_${"x".repeat(90)}`;
const LONG_CASE_KEY = `case_${"k".repeat(95)}`;
const LONG_GROUP = `group_${"g".repeat(90)}`;
const MAX_CASE_KEY = `case_${"k".repeat(195)}`;

function evalCase(over: Record<string, unknown> = {}) {
  return {
    id: CASE_A,
    case_key: "refund-standard-001",
    category: "refunds",
    severity: "high",
    critical: false,
    language: "en",
    group_key: "group-a",
    input_json: { prompt: "p" },
    expected_json: { state: "refunded" },
    grader_type: "exact_match",
    grader_config_json: {},
    weight: 1,
    ...over,
  };
}

const LONG_CASE = evalCase({
  case_key: LONG_CASE_KEY,
  category: LONG_CATEGORY,
  group_key: LONG_GROUP,
  critical: false,
});

// ---------------------------------------------------------------------------------------------
// State table
// ---------------------------------------------------------------------------------------------
type Step = { click: string } | { fill: [string, string] } | { blur: string } | { wait: number };
interface UiState {
  id: string;
  routes: Route[];
  steps?: Step[];
  ready: string;
  noControlsExpected?: boolean;
}

const STATES: UiState[] = [
  { id: "idle", routes: [], ready: '[data-testid="load-cases-button"]' },
  {
    id: "loading",
    routes: [{ method: "GET", path: LIST_PATH, pending: true }],
    steps: [{ click: "load-cases-button" }, { wait: 1300 }],
    ready: '[role="status"]',
    noControlsExpected: false,
  },
  {
    id: "populated with long unbroken values",
    routes: [{ method: "GET", path: LIST_PATH, body: { items: [LONG_CASE], next_after: null } }],
    steps: [{ click: "load-cases-button" }],
    ready: `[data-testid="case-row-${CASE_A}"]`,
  },
  {
    id: "empty",
    routes: [{ method: "GET", path: LIST_PATH, body: { items: [], next_after: null } }],
    steps: [{ click: "load-cases-button" }],
    ready: '[data-testid="empty-state-import-action"]',
  },
  {
    id: "list error 503",
    routes: [{ method: "GET", path: LIST_PATH, status: 503, body: {} }],
    steps: [{ click: "load-cases-button" }],
    ready: '[data-testid="list-error"]',
  },
  {
    id: "list error malformed 200",
    routes: [{ method: "GET", path: LIST_PATH, malformedJson: true }],
    steps: [{ click: "load-cases-button" }],
    ready: '[data-testid="list-error"]',
  },
  {
    id: "import validation error with a field+code",
    routes: [
      {
        method: "POST",
        path: LIST_PATH,
        status: 400,
        body: {
          detail: "validation_failed",
          errors: [{ index: 0, field: "group_key", code: "required" }],
        },
      },
    ],
    steps: [
      { fill: ["import-text", JSON.stringify([{ case_key: "c1", category: "refunds" }])] },
      { click: "import-button" },
    ],
    ready: '[data-testid="import-field-errors"]',
  },
  {
    id: "edit form open",
    routes: [{ method: "GET", path: LIST_PATH, body: { items: [evalCase()], next_after: null } }],
    steps: [{ click: "load-cases-button" }, { click: `edit-case-${CASE_A}` }],
    ready: '[data-testid="case-form"]',
  },
  {
    id: "edit save network error",
    routes: [
      { method: "GET", path: LIST_PATH, body: { items: [evalCase()], next_after: null } },
      { method: "PATCH", path: CASE_PATH, reject: true },
    ],
    steps: [
      { click: "load-cases-button" },
      { click: `edit-case-${CASE_A}` },
      { fill: ["field-language", "fr"] },
      { click: "save-case-button" },
    ],
    ready: '[data-testid="edit-error"]',
  },
  {
    id: "edit JSON field blur error",
    routes: [{ method: "GET", path: LIST_PATH, body: { items: [evalCase()], next_after: null } }],
    steps: [
      { click: "load-cases-button" },
      { click: `edit-case-${CASE_A}` },
      { fill: ["field-input-json", "{not json"] },
      { blur: "field-input-json" },
    ],
    ready: '[data-testid="field-input-json-blur-error"]',
  },
];

// ---------------------------------------------------------------------------------------------
// Browser plumbing
// ---------------------------------------------------------------------------------------------
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
      lib: { entry: ENTRY, name: "UX06Bundle", formats: ["iife"], fileName: "ux06" },
    },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap(
    (r) => ("output" in r ? r.output : []) as { type: string; code?: string }[],
  );
  bundle = outputs.find((o) => o.type === "chunk")?.code ?? "";
  expect(bundle.length, "the in-memory bundle must be built").toBeGreaterThan(1000);
  browser = await chromium.launch();
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
      `<title>FTF-UX06 evidence</title><style>${TOKENS_CSS}</style></head>` +
      `<body><main><div id="root"></div></main></body></html>`,
    { waitUntil: "load" },
  );
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, props }) => {
      const ux = (
        window as unknown as {
          UX06: { stubFetch: (r: unknown) => void; mount: (p: unknown) => void };
        }
      ).UX06;
      ux.stubFetch(routes);
      ux.mount(props);
    },
    { routes: state.routes, props: { ...PROPS, locale } },
  );
  for (const step of state.steps ?? []) {
    if ("wait" in step) await page.waitForTimeout(step.wait);
    else if ("click" in step) await page.getByTestId(step.click).click();
    else if ("blur" in step) await page.getByTestId(step.blur).blur();
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

// ---------------------------------------------------------------------------------------------
// Check families (each returns a list of problems; empty means pass)
// ---------------------------------------------------------------------------------------------
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
    const root = document.querySelector("section");
    return root ? { dir: root.getAttribute("dir"), lang: root.getAttribute("lang") } : null;
  });
  if (!attrs) return ["rtl: component root <section> missing"];
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

async function keyboardProblems(
  page: Page,
  noControlsExpected: boolean,
): Promise<{ problems: string[]; stops: number }> {
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
    (window as unknown as { __tabList: HTMLElement[] }).__tabList = list;
    return list.length;
  });
  const problems: string[] = [];
  if (count === 0) {
    return {
      problems: noControlsExpected ? [] : ["keyboard: no focusable control in this state"],
      stops: 0,
    };
  }
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
        const index = list.indexOf(el);
        const cs = getComputedStyle(el);
        return {
          index,
          label: `${el.tagName.toLowerCase()}${el.getAttribute("data-testid") ? `[${el.getAttribute("data-testid")}]` : ""}`,
          outlineStyle: cs.outlineStyle,
          outlineWidth: Number.parseFloat(cs.outlineWidth),
        };
      }),
    );
  }
  stops.forEach((stop, i) => {
    if (!stop || stop.index !== i) {
      problems.push(
        `keyboard: Tab stop ${i} landed on ${stop ? `${stop.label} (list index ${stop.index})` : "nothing"}, want control ${i} in DOM order`,
      );
      return;
    }
    if (stop.outlineStyle === "none" || !(stop.outlineWidth > 0)) {
      problems.push(`focus indicator: ${stop.label} has no visible outline`);
    }
  });
  return { problems, stops: count };
}

// ---------------------------------------------------------------------------------------------
// The spec
// ---------------------------------------------------------------------------------------------
describe.concurrent("[UX06-BROWSER] real-Chromium §72.5 evidence", () => {
  for (const state of STATES) {
    for (const locale of ["en", "ar"] as Locale[]) {
      for (const scheme of ["light", "dark"] as Scheme[]) {
        test(`[${state.id}] ${locale} ${scheme}: axe, viewports, zoom, keyboard, focus indicator, rtl`, async () => {
          const page = await openState(state, locale, scheme);
          try {
            const problems: string[] = [];
            problems.push(...(await axeProblems(page)));
            problems.push(...(await rtlProblems(page, locale)));
            const keyboard = await keyboardProblems(page, state.id === "loading");
            problems.push(...keyboard.problems);
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

// ---------------------------------------------------------------------------------------------
// Round 2 fault reproductions (FOCUS-LOST-TO-BODY / NOT-BILINGUAL), real Chromium
// ---------------------------------------------------------------------------------------------
describe("[UX06-BROWSER] round 2 fixes", () => {
  test("a failed load takes focus on its own alert, never leaving it on <body>", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "list error 503")!,
      "en",
      "light",
    );
    try {
      const active = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
      expect(active).toBe("list-error");
    } finally {
      await page.close();
    }
  });

  test("a failed save takes focus on its own alert, never leaving it on <body>", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "edit save network error")!,
      "en",
      "light",
    );
    try {
      const active = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
      expect(active).toBe("edit-error");
    } finally {
      await page.close();
    }
  });

  test("NEGATIVE: a FocusableAlert without tabIndex would leave focus on <body> (fault reproduction)", async () => {
    // Direct proof the mechanism (not just the outcome) is load-bearing: a native, non-focusable
    // <p> with no tabIndex cannot take programmatic focus in a real browser.
    const page = await browser.newPage();
    try {
      await page.setContent(`<!doctype html><body><p id="x">plain</p></body>`);
      await page.evaluate(() => (document.getElementById("x") as HTMLElement).focus());
      const active = await page.evaluate(() => document.activeElement?.tagName);
      expect(active).not.toBe("P");
      expect(active).toBe("BODY");
    } finally {
      await page.close();
    }
  });

  test("NOT-BILINGUAL: a malformed-200 list load under locale=ar never leaks the raw JSON parser message", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "list error malformed 200")!,
      "ar",
      "light",
    );
    try {
      const text = await page.getByTestId("list-error").innerText();
      expect(text).not.toContain("Unexpected token");
      expect(text).not.toContain("SyntaxError");
      expect(text).toContain("استجابة قائمة الحالات غير سليمة");
    } finally {
      await page.close();
    }
  });

  test("NEGATIVE: the same malformed-200 load at the default English locale keeps the existing English message", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "list error malformed 200")!,
      "en",
      "light",
    );
    try {
      const text = await page.getByTestId("list-error").innerText();
      expect(text).toContain("malformed case listing response");
    } finally {
      await page.close();
    }
  });

  test("NOT-BILINGUAL: an import field error's field name and code are translated under locale=ar", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "import validation error with a field+code")!,
      "ar",
      "light",
    );
    try {
      const text = await page.getByTestId("import-field-errors").innerText();
      expect(text).toContain("مفتاح المجموعة"); // translated field label (group_key)
      expect(text).toContain("مطلوب"); // translated code (required)
      expect(text).not.toContain("group_key");
      expect(text).not.toContain("required");
    } finally {
      await page.close();
    }
  });

  test("NEGATIVE: the same field error keeps the exact raw field/code text at the default English locale", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "import validation error with a field+code")!,
      "en",
      "light",
    );
    try {
      const text = await page.getByTestId("import-field-errors").innerText();
      expect(text).toContain("Row 1 / group_key: required");
    } finally {
      await page.close();
    }
  });

  test("ERRORS-NO-RECOVERY: a list-load error names a concrete recovery step", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "list error 503")!,
      "en",
      "light",
    );
    try {
      const text = await page.getByTestId("list-error").innerText();
      expect(text).toContain("Press Load cases again.");
    } finally {
      await page.close();
    }
  });

  test("the empty state's action focuses the import textarea", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "empty")!,
      "en",
      "light",
    );
    try {
      await page.getByTestId("empty-state-import-action").click();
      const active = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
      expect(active).toBe("import-text");
    } finally {
      await page.close();
    }
  });

  test("OVERFLOW-LONG-NAMES: a 95+ char unbroken case_key/category/group_key causes no horizontal overflow at 390px", async () => {
    const page = await openState(
      STATES.find((s) => s.id === "populated with long unbroken values")!,
      "en",
      "light",
    );
    try {
      await page.setViewportSize({ width: 390, height: 844 });
      const over = await page.evaluate(() => {
        const el = document.documentElement;
        return el.scrollWidth - el.clientWidth;
      });
      expect(over).toBeLessThanOrEqual(1);
    } finally {
      await page.close();
    }
  });

  test("OVERFLOW-LONG-NAMES (second L2 review, site 2 of 2): the edit form heading wraps a 200-char case_key with no overflow at 390px", async () => {
    const page = await openState(
      {
        id: "edit form, max-length case_key",
        routes: [
          {
            method: "GET",
            path: LIST_PATH,
            body: { items: [evalCase({ case_key: MAX_CASE_KEY })], next_after: null },
          },
        ],
        steps: [{ click: "load-cases-button" }, { click: `edit-case-${CASE_A}` }],
        ready: '[data-testid="case-form"]',
      },
      "en",
      "light",
    );
    try {
      await page.setViewportSize({ width: 390, height: 844 });
      const over = await page.evaluate(() => {
        const el = document.documentElement;
        return el.scrollWidth - el.clientWidth;
      });
      expect(over).toBeLessThanOrEqual(1);
    } finally {
      await page.close();
    }
  });

  test("OVERFLOW-LONG-NAMES (second L2 review, site 2 of 2): the import field-error row hint wraps a 200-char case_key with no overflow at 390px", async () => {
    const page = await openState(
      {
        id: "import field error, max-length case_key row hint",
        routes: [
          {
            method: "POST",
            path: LIST_PATH,
            status: 400,
            body: {
              detail: "validation_failed",
              errors: [{ index: 0, field: "group_key", code: "required" }],
            },
          },
        ],
        steps: [
          {
            fill: [
              "import-text",
              JSON.stringify([{ case_key: MAX_CASE_KEY, category: "refunds" }]),
            ],
          },
          { click: "import-button" },
        ],
        ready: '[data-testid="import-field-errors"]',
      },
      "en",
      "light",
    );
    try {
      await page.setViewportSize({ width: 390, height: 844 });
      const over = await page.evaluate(() => {
        const el = document.documentElement;
        return el.scrollWidth - el.clientWidth;
      });
      expect(over).toBeLessThanOrEqual(1);
    } finally {
      await page.close();
    }
  });
});
