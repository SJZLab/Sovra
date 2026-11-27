// FTF-H011-P4 -- real-Chromium §72.5 / UX_STANDARD.md §4 evidence for the dataset mapping wizard:
// every state x en/ar x light/dark at 1280/768/390/320 and 200% zoom, keyboard-only Tab order with
// a visible focus outline, axe (wcag2a..22aa) with 0 violations, the root's lang/dir, and 0 px
// horizontal overflow with a 2000-character unbroken cell value, a 256-character key, 64-hex
// digests and Arabic data. Same harness technique as ftf_h010_p5_browser.spec.ts.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { browserLaunchOptions } from "./support/browser_launch";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import type { Route } from "./browser/h011_p4_entry";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/h011_p4_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const DATASET = "33333333-3333-3333-3333-333333333333";
const sha = (c: string) => c.repeat(64);
const LONG = `x${"y".repeat(1999)}`;
const LONG_KEY = `k${"e".repeat(255)}`;

const DOC = {
  shape: "messages",
  messages_pointer: "/conversation",
  role_key: "role",
  content_key: "content",
  role_map: { human: "user", bot: "assistant" },
  on_unmapped_message_keys: "ignore",
};
function mapping(extra: Record<string, unknown> = {}) {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    dataset_id: DATASET,
    dataset_name: "support-chats",
    dataset_purpose: "training",
    dataset_version: 1,
    dataset_sha256: sha("a"),
    mapping_version: 1,
    parent_mapping_version: null,
    mapping_sha256: sha("b"),
    importer: "jsonl",
    importer_version: "1",
    transform_version: "1",
    source_schema_sha256: null,
    tool_definitions_sha256: sha("c"),
    preview_sha256: sha("d"),
    preview_counts: { seen: 3, accepted: 2, rejected: 1 },
    status: "proposed",
    allowed_purposes_snapshot: ["training", "validation", "held_out_evaluation"],
    approved_purpose: null,
    approved_by: null,
    approver_role: null,
    approved_at: null,
    created_by: LONG_KEY,
    created_at: "2026-11-27T10:00:00+00:00",
    updated_at: "2026-11-27T10:00:00+00:00",
    masked: false,
    mapping_document: DOC,
    decision: null,
    ...extra,
  };
}
const APPROVED = {
  status: "approved",
  approved_purpose: "training",
  approved_by: LONG_KEY,
  approver_role: "workspace_admin",
  approved_at: "2026-11-27T11:00:00+00:00",
  decision: { action: "approve", rationale: "ok", decided_by: LONG_KEY },
  mapping_version: 2,
  id: "55555555-5555-5555-5555-555555555555",
};
const REJECTED = {
  status: "rejected",
  decision: { action: "reject", rationale: "no", decided_by: LONG_KEY },
  mapping_version: 3,
  id: "66666666-6666-6666-6666-666666666666",
};
const RAW = (i: number) => ({
  conversation: [
    { role: "human", content: `مرحبا ${i} <b>not bold</b>`, [LONG_KEY]: LONG },
    { role: "bot", content: `أهلا ${i}` },
  ],
});
const PROBE = {
  state: "completed",
  failure_code: null,
  complete: true,
  masked: false,
  headers: null,
  rows: [RAW(1), RAW(2)],
};
const CSV_PROBE = {
  ...PROBE,
  headers: ["السؤال", "الجواب", LONG_KEY],
  rows: [
    ["كيف حالك؟", "بخير", LONG],
    ["2", "", "x"],
  ],
};
const MASKED_PROBE = {
  ...PROBE,
  masked: true,
  rows: [{ conversation: { masked: true, chars: 12 }, [`k`]: { masked: true, chars: 0 } }],
};
const PREVIEW = {
  state: "completed",
  failure_code: null,
  complete: false,
  masked: false,
  counts: { seen: 3, accepted: 2, rejected: 1, "rejected:unknown_tool": 1 },
  source_schema_sha256: sha("e"),
  tool_definitions_sha256: sha("c"),
  preview_sha256: sha("d"),
  observed_role_values: ["human", "bot", "narrator"],
  rows: [
    ...[1, 2].map((i) => ({
      locator: `line:${i}`,
      outcome: "accepted",
      code: null,
      slot: null,
      raw_row: RAW(i),
      not_mapped: [LONG_KEY],
      candidate: {
        messages: [
          { role: "user", content: `مرحبا ${i}` },
          { role: "assistant", content: `أهلا ${i}` },
        ],
      },
      comparison: [{ slot: "content", raw_type: "str", canonical_type: "str", identical: true }],
    })),
    {
      locator: "line:3",
      outcome: "rejected",
      code: "unknown_tool",
      slot: "tool_calls",
      raw_row: RAW(3),
      not_mapped: [],
      candidate: null,
      comparison: [],
    },
  ],
};
const LIST = (items: unknown[]): Route => ({
  method: "GET",
  path: "/mappings?limit=200",
  body: { items, next_after: null },
});
const EMPTY = LIST([]);

const PROPS = { apiBaseUrl: API, organizationId: ORG, projectId: PROJECT, datasetId: DATASET };

type Step =
  | { radio: RegExp }
  | { button: RegExp }
  | { select: [RegExp, string] }
  | { type: [RegExp, string] }
  | { check: RegExp }
  | { tid: string }
  | { wait: number };
interface UiState {
  id: string;
  routes: Route[];
  steps?: Step[];
  ready: string;
  noControlsExpected?: boolean;
}
const JSONL: Step[] = [{ radio: /JSON Lines/ }, { tid: "probe-button" }];
const MAP: Step[] = [
  ...JSONL,
  { radio: /A list of messages|قائمة رسائل/ },
  { type: [/Messages location|موضع الرسائل/, "/conversation"] },
  { type: [/^(Role key|مفتاح الدور)/, "role"] },
  { type: [/^(Content key|مفتاح المحتوى)/, "content"] },
  { type: [/(Raw role value|قيمة الدور الأصلية) 1/, "human"] },
  { select: [/(Maps to|يُعيَّن إلى) \((role|الدور) 1\)/, "user"] },
  { tid: "add-role" },
  { type: [/(Raw role value|قيمة الدور الأصلية) 2/, "bot"] },
  { select: [/(Maps to|يُعيَّن إلى) \((role|الدور) 2\)/, "assistant"] },
  { radio: /Ignore them|تجاهلها/ },
];
const PREVIEWED: Step[] = [...MAP, { tid: "preview-button" }];
const PROBED = { method: "POST", path: "/mapping-probe", body: PROBE };
const PREVIEWS = { method: "POST", path: "/mapping-preview", body: PREVIEW };

const STATES: UiState[] = [
  { id: "empty dataset (Draft)", routes: [EMPTY], ready: '[data-testid="mapping-versions-empty"]' },
  {
    id: "loading versions",
    routes: [{ method: "GET", path: "/mappings?limit=200", pending: true }],
    ready: '[data-testid="mapping-versions-loading"]',
  },
  {
    id: "versions error 503",
    routes: [
      {
        method: "GET",
        path: "/mappings?limit=200",
        status: 503,
        body: { detail: "storage_unavailable" },
      },
    ],
    ready: '[data-testid="mapping-versions-error"]',
  },
  {
    id: "versions populated (proposed, approved, rejected)",
    routes: [LIST([mapping(), mapping(APPROVED), mapping(REJECTED)])],
    ready: '[data-testid="mapping-version-3"]',
  },
  {
    id: "csv options",
    routes: [EMPTY],
    steps: [{ radio: /CSV/ }],
    ready: "select",
  },
  {
    id: "csv validation error",
    routes: [EMPTY],
    steps: [
      { radio: /CSV/ },
      { type: [/Rows before the header|عدد الصفوف قبل العناوين/, "abc"] },
      { tid: "probe-button" },
    ],
    ready: '[role="alert"][tabindex="-1"]',
  },
  {
    id: "json options",
    routes: [EMPTY],
    steps: [{ radio: /JSON \(one|JSON \(مستند/ }],
    ready: "textarea",
  },
  {
    id: "reading the file",
    routes: [EMPTY, { method: "POST", path: "/mapping-probe", pending: true }],
    steps: JSONL,
    ready: '[data-testid="probe-loading"]',
  },
  {
    id: "xlsx blocked by the server",
    routes: [
      EMPTY,
      {
        method: "POST",
        path: "/mapping-probe",
        status: 409,
        body: { detail: "importer_unavailable" },
      },
    ],
    steps: [{ radio: /Excel/ }, { tid: "probe-button" }],
    ready: '[data-testid="probe-error"]',
  },
  {
    id: "probe read but failed (Validation required)",
    routes: [
      EMPTY,
      {
        method: "POST",
        path: "/mapping-probe",
        body: {
          ...PROBE,
          state: "failed",
          failure_code: "invalid_encoding",
          complete: false,
          rows: [],
        },
      },
    ],
    steps: [{ radio: /CSV/ }, { tid: "probe-button" }],
    ready: '[data-testid="probe-failed"]',
  },
  {
    id: "probe network error",
    routes: [EMPTY, { method: "POST", path: "/mapping-probe", reject: true }],
    steps: JSONL,
    ready: '[data-testid="probe-error"]',
  },
  {
    id: "map step (jsonl sample, long values)",
    routes: [EMPTY, PROBED],
    steps: JSONL,
    ready: '[data-testid="mapping-step-map"]',
  },
  {
    id: "map step filled",
    routes: [EMPTY, PROBED],
    steps: MAP,
    ready: '[data-testid="mapping-step-map"] fieldset',
  },
  {
    id: "map step validation errors",
    routes: [EMPTY, PROBED],
    steps: [...JSONL, { radio: /A list of messages|قائمة رسائل/ }, { tid: "preview-button" }],
    ready: '[role="alert"][tabindex="-1"]',
  },
  {
    id: "map step csv (Arabic headers)",
    routes: [EMPTY, { method: "POST", path: "/mapping-probe", body: CSV_PROBE }],
    steps: [
      { radio: /CSV/ },
      { tid: "probe-button" },
      { radio: /A prompt and a completion|مطالبة وإكمال/ },
    ],
    ready: '[data-testid="probe-table"]',
  },
  {
    id: "masked sample",
    routes: [EMPTY, { method: "POST", path: "/mapping-probe", body: MASKED_PROBE }],
    steps: JSONL,
    ready: '[data-testid="masked-notice"]',
  },
  {
    id: "building the preview",
    routes: [EMPTY, PROBED, { method: "POST", path: "/mapping-preview", pending: true }],
    steps: PREVIEWED,
    ready: '[data-testid="preview-loading"]',
  },
  {
    id: "preview with a rejected row (Validation required)",
    routes: [EMPTY, PROBED, PREVIEWS],
    steps: PREVIEWED,
    ready: '[data-testid="preview-row-2"]',
  },
  {
    id: "preview refused 400",
    routes: [
      EMPTY,
      PROBED,
      {
        method: "POST",
        path: "/mapping-preview",
        status: 400,
        body: { detail: "critical_role_unmapped" },
      },
    ],
    steps: PREVIEWED,
    ready: '[data-testid="preview-error"]',
  },
  {
    id: "preview storage down (Blocked)",
    routes: [
      EMPTY,
      PROBED,
      {
        method: "POST",
        path: "/mapping-preview",
        status: 503,
        body: { detail: "storage_unavailable" },
      },
    ],
    steps: PREVIEWED,
    ready: '[data-testid="preview-error"]',
  },
  {
    id: "review and save",
    routes: [EMPTY, PROBED, PREVIEWS],
    steps: [...PREVIEWED, { tid: "to-propose" }],
    ready: '[data-testid="mapping-summary"]',
  },
  {
    id: "save lost",
    routes: [EMPTY, PROBED, PREVIEWS, { method: "POST", path: "/mappings", reject: true }],
    steps: [...PREVIEWED, { tid: "to-propose" }, { tid: "save-mapping" }],
    ready: '[data-testid="save-error"]',
  },
  {
    id: "approve step with ignored keys",
    routes: [
      EMPTY,
      PROBED,
      PREVIEWS,
      {
        method: "POST",
        path: "/mappings",
        status: 201,
        body: { ...mapping(), created: true, replayed: false },
      },
    ],
    steps: [...PREVIEWED, { tid: "to-propose" }, { tid: "save-mapping" }],
    ready: '[data-testid="ignored-keys"]',
  },
  {
    id: "approve refused 403 (Blocked)",
    routes: [
      EMPTY,
      PROBED,
      PREVIEWS,
      {
        method: "POST",
        path: "/mappings",
        status: 201,
        body: { ...mapping(), created: true, replayed: false },
      },
      { method: "POST", path: "/approve", status: 403, body: { detail: "purpose_not_authorized" } },
    ],
    steps: [
      ...PREVIEWED,
      { tid: "to-propose" },
      { tid: "save-mapping" },
      { type: [/Reason for the decision|سبب القرار/, "ok"] },
      { tid: "approve-confirm" },
      { check: /Ignore the key|تجاهل المفتاح/ },
      { tid: "approve-button" },
    ],
    ready: '[data-testid="decision-error"]',
  },
  {
    id: "approved",
    routes: [
      EMPTY,
      PROBED,
      PREVIEWS,
      {
        method: "POST",
        path: "/mappings",
        status: 201,
        body: { ...mapping(), created: true, replayed: false },
      },
      {
        method: "POST",
        path: "/approve",
        body: {
          ...mapping({ ...APPROVED, id: mapping().id, mapping_version: 1 }),
          replayed: false,
        },
      },
    ],
    steps: [
      ...PREVIEWED,
      { tid: "to-propose" },
      { tid: "save-mapping" },
      { type: [/Reason for the decision|سبب القرار/, "ok"] },
      { tid: "approve-confirm" },
      { check: /Ignore the key|تجاهل المفتاح/ },
      { tid: "approve-button" },
    ],
    ready: '[data-testid="new-version"]',
  },
  {
    id: "resume a saved proposal",
    routes: [LIST([mapping()])],
    steps: [{ tid: "review-version-1" }],
    ready: '[data-testid="decision-needs-preview"]',
  },
  {
    id: "a rejected version",
    routes: [LIST([mapping(REJECTED)])],
    steps: [{ tid: "review-version-3" }],
    ready: '[data-testid="new-version"]',
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
      lib: { entry: ENTRY, name: "H011P4Bundle", formats: ["iife"], fileName: "h011p4" },
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
      `<title>FTF-H011-P4 evidence</title><style>${TOKENS_CSS}</style></head>` +
      `<body><main><div id="root"></div></main></body></html>`,
    { waitUntil: "load" },
  );
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, props }) => {
      const ux = (
        window as unknown as {
          H011P4: { stubFetch: (r: unknown) => void; mount: (p: unknown) => void };
        }
      ).H011P4;
      ux.stubFetch(routes);
      ux.mount(props);
    },
    { routes: state.routes, props: { ...PROPS, locale } },
  );
  for (const step of state.steps ?? []) {
    if ("wait" in step) await page.waitForTimeout(step.wait);
    else if ("check" in step) {
      for (const box of await page.getByRole("checkbox", { name: step.check }).all())
        await box.check();
    } else if ("radio" in step) await page.getByRole("radio", { name: step.radio }).check();
    else if ("button" in step) await page.getByRole("button", { name: step.button }).click();
    else if ("select" in step) await page.getByLabel(step.select[0]).selectOption(step.select[1]);
    else if ("type" in step) await page.getByLabel(step.type[0]).fill(step.type[1]);
    else await page.getByTestId(step.tid).click();
    await page.waitForTimeout(20);
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
    const root = document.querySelector('[data-testid="dataset-mapping-wizard"]');
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

describe.concurrent("[H011P4-BROWSER] real-Chromium §72.5 evidence", () => {
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
        }, 90_000);
      }
    }
  }
});

describe("[H011P4-BROWSER] behaviour in a real browser", () => {
  const find = (id: string) => STATES.find((s) => s.id === id)!;

  test("a failed field validation takes focus on the ErrorSummary", async () => {
    const page = await openState(find("csv validation error"), "en", "light");
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

  test("a lost save takes focus on its own role=alert, never <body>", async () => {
    const page = await openState(find("save lost"), "en", "light");
    try {
      await page.waitForFunction(
        () => document.activeElement?.getAttribute("data-testid") === "save-error",
        undefined,
        { timeout: 5000 },
      );
    } finally {
      await page.close();
    }
  });

  test("after approval focus lands on the status notice", async () => {
    const page = await openState(find("approved"), "en", "light");
    try {
      await page.waitForFunction(
        () => document.activeElement?.getAttribute("data-testid") === "mapping-notice",
        undefined,
        { timeout: 5000 },
      );
      expect(await page.getByTestId("mapping-notice").getAttribute("role")).toBe("status");
      expect(await page.getByTestId("mapping-state").innerText()).toContain("Approved");
    } finally {
      await page.close();
    }
  });

  test("an HTML payload in a data cell is text in a real layout engine", async () => {
    const page = await openState(
      find("preview with a rejected row (Validation required)"),
      "en",
      "light",
    );
    try {
      expect(await page.locator('[data-testid="dataset-mapping-wizard"] b').count()).toBe(0);
      expect(await page.getByTestId("preview-row-0").innerText()).toContain("<b>not bold</b>");
      const dirs = await page.$$eval('[data-testid="preview-row-0"] [data-value]', (els) =>
        els.map((e) => e.getAttribute("dir")),
      );
      expect(dirs.length).toBeGreaterThan(0);
      expect(new Set(dirs)).toEqual(new Set(["auto"]));
    } finally {
      await page.close();
    }
  });

  test("the reject dialog traps focus and returns it to the opener on Escape", async () => {
    const page = await openState(find("approve step with ignored keys"), "en", "light");
    try {
      await page.getByLabel(/Reason for the decision/).fill("wrong");
      await page.getByTestId("reject-button").click();
      await page.getByTestId("reject-dialog").waitFor();
      await page.keyboard.press("Tab");
      const inside = await page.evaluate(() =>
        Boolean(document.activeElement?.closest('[data-testid="reject-dialog"]')),
      );
      expect(inside).toBe(true);
      await page.keyboard.press("Escape");
      await page.waitForFunction(
        () => document.activeElement?.getAttribute("data-testid") === "reject-button",
        undefined,
        { timeout: 5000 },
      );
    } finally {
      await page.close();
    }
  });

  test("under ar the visible UI text has no English word outside user data and codes", async () => {
    const page = await openState(
      find("preview with a rejected row (Validation required)"),
      "ar",
      "light",
    );
    try {
      const text = await page.evaluate(() => {
        const clone = document
          .querySelector('[data-testid="dataset-mapping-wizard"]')!
          .cloneNode(true) as HTMLElement;
        clone
          .querySelectorAll("bdi, code, [dir=auto], [dir=ltr], input, textarea, select")
          .forEach((b) => b.remove());
        return clone.innerText;
      });
      expect(text).not.toMatch(/[A-Za-z]{3,}/);
    } finally {
      await page.close();
    }
  });
});
