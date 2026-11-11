// FTF-H010-P5 -- real-Chromium §72.5 evidence for the import wizard: every state x en/ar x
// light/dark at 1280/768/390/320 and 200% zoom, keyboard-only Tab order with a visible focus
// outline, axe (wcag2a..22aa) with 0 violations, focus placement, and 0 px horizontal overflow
// with a 2048-character unbroken ref, 64-hex digests, a 200-character artifact name and a
// 128-character idempotency-style key. Same harness technique as ftf_ux06_browser.spec.ts.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { browserLaunchOptions } from "./support/browser_launch";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import type { Route } from "./browser/h010_p5_entry";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/h010_p5_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const CONN = "33333333-3333-3333-3333-333333333333";
const ART = "44444444-4444-4444-4444-444444444444";
const BASE = "66666666-6666-6666-6666-666666666666";
const RESULT = "99999999-9999-9999-9999-999999999999";
const ORG_PATH = `/api/v1/organizations/${ORG}`;
const IMPORTS = `${ORG_PATH}/projects/${PROJECT}/model-imports`;
const sha = (c: string) => c.repeat(64);

const LONG_REF = `ft:${"r".repeat(2045)}`;
const LONG_NAME = `artifact_${"n".repeat(191)}`;
const LONG_KEY = `k${"0".repeat(127)}`;

function job(extra: Record<string, unknown> = {}) {
  return {
    id: "88888888-8888-8888-8888-888888888888",
    organization_id: ORG,
    project_id: PROJECT,
    requested_origin: "external_endpoint",
    parent_model_version_id: null,
    trainable_artifact_id: null,
    external_provider_model_ref: LONG_REF,
    litellm_connection_id: CONN,
    result_model_version_id: null,
    state: "queued",
    failure_code: null,
    created_by: LONG_KEY,
    created_at: "2026-11-01T00:00:00+00:00",
    updated_at: "2026-11-01T00:00:00+00:00",
    ...extra,
  };
}
const ALL_JOBS = [
  job({ id: "j1" }),
  job({ id: "j2", state: "validating" }),
  job({ id: "j3", state: "failed", failure_code: "digest_mismatch" }),
  job({ id: "j4", state: "failed", failure_code: "a_code_from_the_future" }),
  job({ id: "j5", state: "blocked_external", failure_code: "metadata_files_unavailable" }),
  job({ id: "j6", state: "succeeded", result_model_version_id: RESULT }),
];
const ARTIFACTS = [
  {
    id: ART,
    display_name: LONG_NAME,
    source_type: "local",
    status: "ready",
    license_id: "apache-2.0",
    license_accepted_at: "2026-10-01T00:00:00+00:00",
    weights_digest: sha("a"),
  },
  {
    id: "aa",
    display_name: "unready",
    source_type: "private_s3",
    status: "registered",
    license_id: null,
    license_accepted_at: null,
    weights_digest: null,
  },
];
const CORE: Route[] = [
  {
    method: "GET",
    path: `${ORG_PATH}/projects/${PROJECT}`,
    body: { security_profile: "standard" },
  },
  { method: "GET", path: `${ORG_PATH}/artifacts`, body: ARTIFACTS },
  {
    method: "GET",
    path: `${ORG_PATH}/projects/${PROJECT}/model-versions`,
    body: [{ id: BASE, version_number: 1, import_kind: "full_weight", weights_digest: sha("b") }],
  },
  {
    method: "GET",
    path: `${ORG_PATH}/connections/litellm`,
    body: [{ id: CONN, name: LONG_NAME, status: "active", base_url: "https://x.example/" }],
  },
  {
    method: "GET",
    path: `${ORG_PATH}/connections/litellm/${CONN}/catalog`,
    body: { entries: [{ id: LONG_REF, status: "callable" }] },
  },
];
// A version a SUCCEEDED imported_weights job produced is the only valid adapter base.
const BASE_JOB = job({
  id: "jbase",
  requested_origin: "imported_weights",
  trainable_artifact_id: ART,
  external_provider_model_ref: null,
  litellm_connection_id: null,
  state: "succeeded",
  result_model_version_id: BASE,
});
const withList = (body: unknown): Route[] => [{ method: "GET", path: IMPORTS, body }, ...CORE];

const PROPS = {
  apiBaseUrl: API,
  organizationId: ORG,
  projectId: PROJECT,
  role: "organization_admin",
};

type Step =
  | { radio: RegExp }
  | { button: RegExp }
  | { select: [RegExp, string] }
  | { type: [RegExp, string] }
  | { tid: string }
  | { wait: number };
interface UiState {
  id: string;
  routes: Route[];
  steps?: Step[];
  ready: string;
  props?: Record<string, unknown>;
  noControlsExpected?: boolean;
}
const NEXT = { button: /^(Continue|متابعة)$/ } as const;
const toEndpointForm: Step[] = [{ radio: /Endpoint model|نقطة اتصال/ }, NEXT];
const toWeightsForm: Step[] = [{ radio: /Full weights|أوزان كاملة/ }, NEXT];
const toAdapterForm: Step[] = [{ radio: /Adapter and base|محوّل ونموذج/ }, NEXT];
const toEndpointReview: Step[] = [
  ...toEndpointForm,
  { select: [/Gateway connection|اتصال البوابة/, CONN] },
  { type: [/Model reference|مرجع النموذج/, LONG_REF] },
  { button: /Review import|مراجعة الاستيراد/ },
];

const STATES: UiState[] = [
  {
    id: "step 1 empty list",
    routes: withList([]),
    ready: '[role="radio"], input[type="radio"]',
  },
  {
    id: "loading list",
    routes: [{ method: "GET", path: IMPORTS, pending: true }, ...CORE],
    steps: [{ wait: 200 }],
    ready: '[role="status"]',
  },
  {
    id: "populated every state with long values",
    routes: withList(ALL_JOBS),
    ready: '[data-testid="job-j6"]',
  },
  {
    id: "list error 503",
    routes: [{ method: "GET", path: IMPORTS, status: 503, body: {} }, ...CORE],
    ready: '[data-testid="import-list-error"]',
  },
  {
    id: "list malformed 200",
    routes: [{ method: "GET", path: IMPORTS, malformedJson: true }, ...CORE],
    ready: '[data-testid="import-list-error"]',
  },
  {
    id: "list network error",
    routes: [{ method: "GET", path: IMPORTS, reject: true }, ...CORE],
    ready: '[data-testid="import-list-error"]',
  },
  { id: "endpoint form", routes: withList([]), steps: toEndpointForm, ready: "select" },
  {
    id: "endpoint form with validation errors",
    routes: withList([]),
    steps: [...toEndpointForm, { button: /Review import|مراجعة الاستيراد/ }],
    ready: '[role="alert"][tabindex="-1"]',
  },
  {
    id: "endpoint admin required (ml_engineer)",
    routes: withList([]),
    props: { role: "ml_engineer" },
    steps: toEndpointForm,
    ready: '[data-testid="endpoint-admin-required"]',
  },
  {
    id: "viewer permission denied",
    routes: withList(ALL_JOBS),
    props: { role: "viewer" },
    ready: '[data-testid="import-permission-denied"]',
    noControlsExpected: true,
  },
  {
    id: "weights form with a blocked artifact",
    routes: withList([]),
    steps: [...toWeightsForm, { select: [/Model artifact|قطعة النموذج/, "aa"] }],
    ready: '[data-testid="artifact-blocked-reason"]',
  },
  {
    id: "weights form ready artifact",
    routes: withList([]),
    steps: [...toWeightsForm, { select: [/Model artifact|قطعة النموذج/, ART] }],
    ready: '[data-testid="artifact-facts"]',
  },
  {
    id: "adapter form",
    routes: withList([BASE_JOB]),
    steps: [
      ...toAdapterForm,
      { select: [/Adapter artifact|قطعة المحوّل/, ART] },
      { select: [/Base model version|إصدار النموذج الأساسي/, BASE] },
    ],
    ready: '[data-testid="artifact-facts"]',
  },
  {
    id: "adapter form without a validated base",
    routes: withList([]),
    steps: toAdapterForm,
    ready: "[aria-label]",
  },
  {
    id: "project profile load error",
    routes: [
      { method: "GET", path: IMPORTS, body: [] },
      { method: "GET", path: `${ORG_PATH}/projects/${PROJECT}`, status: 500, body: {} },
      ...CORE.slice(1),
    ],
    steps: toWeightsForm,
    ready: '[data-testid="import-profile-error"]',
  },
  {
    id: "blocked job with an unknown code",
    routes: withList([
      job({ id: "jb", state: "blocked_external", failure_code: "a_future_block" }),
    ]),
    ready: '[data-testid="job-jb"]',
  },
  {
    id: "endpoint ref inline error",
    routes: withList([]),
    steps: [
      ...toEndpointForm,
      { select: [/Gateway connection|اتصال البوابة/, CONN] },
      { button: /Review import|مراجعة الاستيراد/ },
    ],
    ready: '[role="alert"][id$="-ref-error"]',
  },
  {
    id: "weights options load error",
    routes: [
      { method: "GET", path: IMPORTS, body: [] },
      { method: "GET", path: `${ORG_PATH}/artifacts`, status: 500, body: {} },
    ],
    steps: toWeightsForm,
    ready: '[data-testid="import-options-error"]',
  },
  {
    id: "no artifacts",
    routes: [
      { method: "GET", path: IMPORTS, body: [] },
      { method: "GET", path: `${ORG_PATH}/artifacts`, body: [] },
    ],
    steps: toWeightsForm,
    ready: "[aria-label]",
  },
  {
    id: "review (endpoint, long ref)",
    routes: withList([]),
    steps: toEndpointReview,
    ready: '[data-testid="import-summary"]',
  },
  {
    id: "submit rejected 400",
    routes: [
      { method: "POST", path: IMPORTS, status: 400, body: { detail: "invalid_body" } },
      ...withList([]),
    ],
    steps: [...toEndpointReview, { tid: "import-confirm" }, { tid: "import-submit" }],
    ready: '[data-testid="import-submit-error"]',
  },
  {
    id: "submit network error then none found",
    routes: [{ method: "POST", path: IMPORTS, reject: true }, ...withList([])],
    steps: [...toEndpointReview, { tid: "import-confirm" }, { tid: "import-submit" }],
    ready: '[data-testid="import-submit-error"]',
  },
  {
    id: "submit created",
    routes: [{ method: "POST", path: IMPORTS, status: 201, body: job() }, ...withList([])],
    steps: [...toEndpointReview, { tid: "import-confirm" }, { tid: "import-submit" }],
    ready: '[data-testid="import-notice"]',
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
      lib: { entry: ENTRY, name: "H010P5Bundle", formats: ["iife"], fileName: "h010p5" },
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
      `<title>FTF-H010-P5 evidence</title><style>${TOKENS_CSS}</style></head>` +
      `<body><main><div id="root"></div></main></body></html>`,
    { waitUntil: "load" },
  );
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, props }) => {
      const ux = (
        window as unknown as {
          H010P5: { stubFetch: (r: unknown) => void; mount: (p: unknown) => void };
        }
      ).H010P5;
      ux.stubFetch(routes);
      ux.mount(props);
    },
    { routes: state.routes, props: { ...PROPS, ...(state.props ?? {}), locale } },
  );
  for (const step of state.steps ?? []) {
    if ("wait" in step) await page.waitForTimeout(step.wait);
    else if ("radio" in step) await page.getByRole("radio", { name: step.radio }).check();
    else if ("button" in step) await page.getByRole("button", { name: step.button }).click();
    else if ("select" in step) await page.getByLabel(step.select[0]).selectOption(step.select[1]);
    else if ("type" in step) await page.getByLabel(step.type[0]).fill(step.type[1]);
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
    const root = document.querySelector('[data-testid="import-model-wizard"]');
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

describe.concurrent("[H010P5-BROWSER] real-Chromium §72.5 evidence", () => {
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

describe("[H010P5-BROWSER] focus, wrap and bilingual behaviour in a real browser", () => {
  const find = (id: string) => STATES.find((s) => s.id === id)!;

  test("a rejected submit takes focus on its own role=alert, never <body>", async () => {
    const page = await openState(find("submit rejected 400"), "en", "light");
    try {
      await page.waitForFunction(
        () => document.activeElement?.getAttribute("data-testid") === "import-submit-error",
        undefined,
        { timeout: 5000 },
      );
      expect(await page.getByTestId("import-submit-error").getAttribute("role")).toBe("alert");
    } finally {
      await page.close();
    }
  });

  test("a failed field validation takes focus on the ErrorSummary", async () => {
    const page = await openState(find("endpoint form with validation errors"), "en", "light");
    try {
      const active = await page.evaluate(() => ({
        role: document.activeElement?.getAttribute("role"),
        tab: document.activeElement?.getAttribute("tabindex"),
      }));
      expect(active).toEqual({ role: "alert", tab: "-1" });
    } finally {
      await page.close();
    }
  });

  test("a 2048-char ref, 200-char name and 64-hex digest never scroll the page at 320 px or 200% zoom", async () => {
    for (const id of [
      "populated every state with long values",
      "review (endpoint, long ref)",
      "weights form ready artifact",
    ]) {
      const page = await openState(find(id), "en", "light");
      try {
        const problems = await overflowProblems(page);
        expect(problems, `${id}: ${problems.join(" ")}`).toEqual([]);
      } finally {
        await page.close();
      }
    }
  });

  test("under ar the visible UI text has no English word outside user data", async () => {
    const page = await openState(find("populated every state with long values"), "ar", "light");
    try {
      const text = await page.evaluate(() => {
        const clone = document
          .querySelector('[data-testid="import-model-wizard"]')!
          .cloneNode(true) as HTMLElement;
        clone.querySelectorAll("bdi").forEach((b) => b.remove());
        return clone.innerText;
      });
      expect(text).not.toMatch(/[A-Za-z]{3,}/);
    } finally {
      await page.close();
    }
  });

  test("the ref input is described by its help text and, when invalid, its inline error", async () => {
    const page = await openState(find("endpoint ref inline error"), "en", "light");
    try {
      const described = await page.evaluate(() => {
        const input = document.querySelector<HTMLInputElement>('input[id$="-ref"]')!;
        return (input.getAttribute("aria-describedby") ?? "")
          .split(" ")
          .map((id) => document.getElementById(id)?.textContent ?? "");
      });
      expect(described.join(" ")).toMatch(/Sent exactly as typed/);
      expect(described.join(" ")).toMatch(/Enter the model reference/);
    } finally {
      await page.close();
    }
  });

  test("after a created import focus lands on the notice, never <body>", async () => {
    const page = await openState(find("submit created"), "en", "light");
    try {
      await page.waitForFunction(
        () => document.activeElement?.getAttribute("data-testid") === "import-notice",
        undefined,
        { timeout: 5000 },
      );
    } finally {
      await page.close();
    }
  });

  test("queued shows no progress bar and the 'not started' text in a real browser", async () => {
    const page = await openState(find("populated every state with long values"), "en", "light");
    try {
      expect(await page.locator("progress, [role=progressbar]").count()).toBe(0);
      expect(await page.getByTestId("job-j1").innerText()).toContain("validation has not started");
    } finally {
      await page.close();
    }
  });
});
