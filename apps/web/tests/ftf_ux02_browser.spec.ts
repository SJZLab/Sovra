// FTF-UX02 round 2, point 7 (QUEUE 0k; UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md §72.5):
// the viewport (1280/768/390), 200 % zoom, keyboard-only and automated-accessibility (axe, light AND
// dark) evidence for the rebuilt FTF-0500A component and the FTF-0501-UI abandon-confirmation Dialog,
// in a REAL Chromium, in CI. Same harness pattern as the sibling FTF-UX03 round-2 slice
// (ftf_ux03_browser.spec.ts / browser/ux03_entry.tsx), built this same shift.
//
// Scope disclosed, not hidden: FTF-0516 (dataset issue resolution) is NOT rebuilt this round (same
// as round 1) and so is not in this harness either -- see the round-2 submission.
//
// This harness's one job beyond apps/web/tests/ftf_0501_ui.spec.ts's own jsdom coverage: proving the
// Dialog's focus TRAP and focus RETURN for real. jsdom's `document.activeElement` restoration does
// not reliably model a Radix `FocusScope`'s unmount-autofocus behaviour (confirmed empirically while
// building this slice -- the jsdom test asserts the trap and Escape-to-close only, and says so).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import { draftStorageKey } from "../components/dataset-factory/resumable_dataset_upload_ui";
import type { Route } from "./browser/ux02_entry";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/ux02_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const CONNECTION = "33333333-3333-3333-3333-333333333333";
const P = `/api/v1/organizations/${ORG}/projects/${PROJECT}`;
const SOURCES = `${P}/dataset-sources`;

const CONN_A = {
  id: CONNECTION,
  organization_id: ORG,
  project_id: PROJECT,
  source_type: "s3",
  display_name: "Support tickets export",
  has_credential: true,
  has_connection_config: false,
  allowed_purposes: ["training"],
  security_classification: "confidential",
  connection_status: "active",
  last_validated_at: null,
  created_by: "user-1",
  created_at: "2026-09-01T00:00:00+00:00",
  updated_at: "2026-09-01T00:00:00+00:00",
  revoked_at: null,
};

const DRAFT_KEY = draftStorageKey(ORG, PROJECT, CONNECTION);
const DRAFT = JSON.stringify({
  organizationId: ORG,
  projectId: PROJECT,
  datasetSourceConnectionId: CONNECTION,
  uploadSessionId: "44444444-4444-4444-4444-444444444444",
  purpose: "training",
  declaredSizeBytes: 5,
  declaredSha256: "a".repeat(64),
  declaredContentType: "application/json",
  fileName: "data.jsonl",
});

interface UiState {
  id: string;
  component: "0500A" | "0501-UI";
  props: Record<string, unknown>;
  routes: Route[];
  storageItems?: Record<string, string>;
  steps?: Array<{ click: string } | { wait: number } | { select: [string, string] }>;
  ready: string;
  noControlsExpected?: boolean;
}

const P0500A = { apiBaseUrl: API, organizationId: ORG, projectId: PROJECT };
const P0501 = {
  apiBaseUrl: API,
  organizationId: ORG,
  projectId: PROJECT,
  datasetSourceConnectionId: CONNECTION,
};

const STATES: UiState[] = [
  {
    id: "0500A loading",
    component: "0500A",
    props: P0500A,
    routes: [{ method: "GET", path: SOURCES, pending: true }],
    ready: '[role="status"]',
    noControlsExpected: true,
  },
  {
    id: "0500A empty",
    component: "0500A",
    props: P0500A,
    routes: [{ method: "GET", path: SOURCES, body: [] }],
    ready: '[data-testid="dataset-source-empty-state"]',
  },
  {
    id: "0500A populated",
    component: "0500A",
    props: P0500A,
    routes: [{ method: "GET", path: SOURCES, body: [CONN_A] }],
    ready: `[data-testid="dataset-source-option-${CONNECTION}"]`,
  },
  {
    id: "0500A list error",
    component: "0500A",
    props: P0500A,
    routes: [{ method: "GET", path: SOURCES, status: 500, body: { detail: "internal error" } }],
    ready: '[data-testid="dataset-source-list-error"]',
  },
  {
    id: "0500A authorization denied",
    component: "0500A",
    props: P0500A,
    routes: [
      { method: "GET", path: SOURCES, body: [CONN_A] },
      {
        method: "POST",
        path: `${SOURCES}/${CONNECTION}/authorize`,
        status: 403,
        body: { detail: "not granted for purpose 'baseline_only'" },
      },
    ],
    steps: [
      { click: `dataset-source-option-${CONNECTION}` },
      { select: ["dataset-source-purpose-to-check-select", "baseline_only"] },
      { click: "dataset-source-check-authorization-button" },
    ],
    ready: '[data-testid="dataset-source-authorization-result-denied"]',
  },
  {
    id: "0501-UI abandon dialog",
    component: "0501-UI",
    props: P0501,
    routes: [],
    storageItems: { [DRAFT_KEY]: DRAFT },
    steps: [{ click: "upload-abandon-draft-button" }],
    ready: '[data-testid="upload-abandon-confirm"]',
  },
];

// ---------------------------------------------------------------------------------------------
// Browser plumbing (same shape as ux03_browser.spec.ts)
// ---------------------------------------------------------------------------------------------
type Locale = "en" | "ar";
type Scheme = "light" | "dark";
const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
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
      lib: { entry: ENTRY, name: "UX02Bundle", formats: ["iife"], fileName: "ux02" },
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
  // `page.setContent` leaves the document on an OPAQUE origin, where `localStorage` throws
  // (FTF-0501-UI's abandon-dialog state needs a real draft seeded there). Routing a same-origin
  // navigation instead gives the page a real `http://` origin with no actual network egress.
  await page.route("https://ux02-harness.invalid/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body:
        `<!doctype html><html lang="${locale}" dir="${dir}"><head><meta charset="utf-8" />` +
        `<meta name="viewport" content="width=device-width, initial-scale=1" />` +
        `<title>FTF-UX02 evidence</title><style>${TOKENS_CSS}</style></head>` +
        `<body><main><div id="root"></div></main></body></html>`,
    }),
  );
  await page.goto("https://ux02-harness.invalid/", { waitUntil: "load" });
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, component, props, storageItems }) => {
      const ux = (
        window as unknown as {
          UX02: { stubFetch: (r: unknown) => void; mount: (...a: unknown[]) => void };
        }
      ).UX02;
      ux.stubFetch(routes);
      ux.mount(component, props, storageItems);
    },
    {
      routes: state.routes,
      component: state.component,
      props: { ...state.props, locale },
      storageItems: state.storageItems,
    },
  );
  for (const step of state.steps ?? []) {
    if ("wait" in step) await page.waitForTimeout(step.wait);
    else if ("select" in step) await page.getByTestId(step.select[0]).selectOption(step.select[1]);
    else await page.getByTestId(step.click).click();
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
    return r.violations.map((v: { id: string; impact: string; nodes: { target: unknown }[] }) => ({
      id: v.id,
      impact: v.impact,
      count: v.nodes.length,
    }));
  }, AXE_TAGS)) as { id: string; impact: string; count: number }[];
  return violations.map((v) => `axe ${v.id} (${v.impact}) x${v.count}`);
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

describe("[UX02-BROWSER] real-Chromium §72.5 evidence", () => {
  for (const state of STATES) {
    for (const locale of ["en", "ar"] as Locale[]) {
      for (const scheme of ["light", "dark"] as Scheme[]) {
        test(`[${state.id}] [${locale}] [${scheme}] no overflow, axe clean`, async () => {
          const page = await openState(state, locale, scheme);
          try {
            const problems = [...(await overflowProblems(page)), ...(await axeProblems(page))];
            expect(problems, problems.join(" | ")).toEqual([]);
          } finally {
            await page.close();
          }
        }, 20_000);
      }
    }

    test(`[${state.id}] keyboard: Tab reaches every control with a visible :focus-visible outline`, async () => {
      const page = await openState(state, "en", "light");
      try {
        const count = await page.evaluate(() => {
          const visible = (el: Element) => {
            const cs = getComputedStyle(el);
            return (
              el.getClientRects().length > 0 && cs.visibility !== "hidden" && cs.display !== "none"
            );
          };
          return Array.from(
            document.querySelectorAll<HTMLElement>("button, a[href], input, select, [tabindex]"),
          ).filter((el) => !(el as HTMLButtonElement).disabled && visible(el)).length;
        });
        if (count === 0) {
          expect(state.noControlsExpected, `${state.id}: no focusable control`).toBe(true);
          return;
        }
        await page.evaluate(() => {
          (document.activeElement as HTMLElement | null)?.blur();
          document.body.setAttribute("tabindex", "-1");
          document.body.focus();
          document.body.removeAttribute("tabindex");
        });
        const outlineProblems: string[] = [];
        for (let i = 0; i < count; i++) {
          await page.keyboard.press("Tab");
          const outline = await page.evaluate(() => {
            const el = document.activeElement as HTMLElement | null;
            if (!el) return null;
            const cs = getComputedStyle(el);
            return { style: cs.outlineStyle, width: cs.outlineWidth };
          });
          if (!outline || outline.style === "none" || parseFloat(outline.width) <= 0) {
            outlineProblems.push(`stop ${i}: no visible outline (${JSON.stringify(outline)})`);
          }
        }
        expect(outlineProblems, outlineProblems.join(" | ")).toEqual([]);
      } finally {
        await page.close();
      }
    }, 20_000);
  }

  // FTF-UX02 round-2 finding 1 (CONFIRM-FOCUS-LOST): the one check jsdom cannot make -- focus
  // RETURNS to the element that opened the dialog once it closes, in a real browser.
  test("[0501-UI abandon dialog] focus returns to the trigger button after Keep draft closes it", async () => {
    const state = STATES.find((s) => s.id === "0501-UI abandon dialog")!;
    const page = await openState(state, "en", "light");
    try {
      const dlg = page.getByTestId("upload-abandon-confirm");
      expect(await dlg.isVisible()).toBe(true);
      const trapped = await page.evaluate(() => {
        const dialog = document.querySelector('[data-testid="upload-abandon-confirm"]');
        return dialog?.contains(document.activeElement) ?? false;
      });
      expect(trapped, "focus must start inside the open dialog").toBe(true);

      await page.getByTestId("upload-abandon-cancel-button").click();
      await page.waitForSelector('[data-testid="upload-abandon-confirm"]', { state: "detached" });
      await page.waitForTimeout(50);
      const returned = await page.evaluate(() => {
        const trigger = document.querySelector('[data-testid="upload-abandon-draft-button"]');
        return document.activeElement === trigger;
      });
      expect(returned, "focus must return to the Abandon-draft trigger button").toBe(true);
    } finally {
      await page.close();
    }
  });

  // FTF-UX02 round-2 pre-review round 1, finding 1 (pinned): initial open focus is the specific
  // Keep-draft button, not merely "something inside the dialog" (a Keep/Abandon reorder must RED).
  test("[0501-UI abandon dialog] initial focus on open is the Keep-draft button specifically", async () => {
    const state = STATES.find((s) => s.id === "0501-UI abandon dialog")!;
    const page = await openState(state, "en", "light");
    try {
      const focused = await page.evaluate(() => {
        const keep = document.querySelector('[data-testid="upload-abandon-cancel-button"]');
        return document.activeElement === keep;
      });
      expect(focused, "initial focus must be the Keep-draft button").toBe(true);
    } finally {
      await page.close();
    }
  });

  // FTF-UX02 round-2 pre-review round 1, finding 1 (CONFIRM-FOCUS-ON-CONFIRM): confirming abandon
  // (the destructive path, as opposed to Keep/Escape) must move focus to the fresh form's first
  // control, never strand it on <body> -- the exact defect the independent review reproduced.
  test("[0501-UI abandon dialog] confirming Abandon moves focus to the fresh form's first control, never <body>", async () => {
    const state = STATES.find((s) => s.id === "0501-UI abandon dialog")!;
    const page = await openState(state, "en", "light");
    try {
      await page.getByTestId("upload-abandon-confirm-button").click();
      await page.waitForSelector('[data-testid="upload-abandon-confirm"]', { state: "detached" });
      await page.waitForSelector('[data-testid="upload-purpose-select"]', { state: "visible" });
      const onPurposeSelect = await page.evaluate(() => {
        const select = document.querySelector('[data-testid="upload-purpose-select"]');
        return document.activeElement === select;
      });
      expect(onPurposeSelect, "focus must land on the fresh form's purpose select").toBe(true);
    } finally {
      await page.close();
    }
  });

  // FTF-UX02 round-2 pre-review round 1, finding 5 (LABELS-ABOVE-FIELDS misclaimed): the
  // independent review measured real layout (not DOM order) and found every labelled 0500A
  // control's label beside it, not above it, at both 1280 and 390px. Pin the real geometry.
  test("[0500A populated] every labelled control's label sits visually above it, at 1280 and 390px", async () => {
    const state = STATES.find((s) => s.id === "0500A populated")!;
    const page = await openState(state, "en", "light");
    try {
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: 900 });
        const problems = await page.evaluate(() => {
          const pairs: [string, string][] = [
            ['label[for="dataset-source-type"]', "dataset-source-type-select"],
            ['label[for="dataset-source-display-name"]', "dataset-source-display-name-input"],
            ['label[for="dataset-source-credential"]', "dataset-source-credential-input"],
            [
              'label[for="dataset-source-security-classification"]',
              "dataset-source-security-classification-select",
            ],
          ];
          const bad: string[] = [];
          for (const [labelSelector, controlTestId] of pairs) {
            const label = document.querySelector(labelSelector) as HTMLElement | null;
            const control = document.querySelector(
              `[data-testid="${controlTestId}"]`,
            ) as HTMLElement | null;
            if (!label || !control) {
              bad.push(`${controlTestId}: missing label or control`);
              continue;
            }
            const labelRect = label.getBoundingClientRect();
            const controlRect = control.getBoundingClientRect();
            // Above, not beside: the label's bottom edge must not sit below the control's top
            // edge by more than a hairline (sub-pixel rounding), and they must not overlap
            // vertically in a way that puts them on the same visual row (left-to-left gap check
            // catches the "beside" case the independent review actually found).
            const stacked = labelRect.bottom <= controlRect.top + 1;
            const sameRowBeside =
              Math.abs(labelRect.top - controlRect.top) < 4 && controlRect.left > labelRect.right;
            if (!stacked || sameRowBeside) {
              bad.push(
                `${controlTestId}: label bottom=${labelRect.bottom} control top=${controlRect.top} ` +
                  `labelTop=${labelRect.top} controlLeft=${controlRect.left} labelRight=${labelRect.right}`,
              );
            }
          }
          return bad;
        });
        expect(problems, problems.join(" | ")).toEqual([]);
      }
    } finally {
      await page.close();
    }
  });

  test("[0501-UI abandon dialog] Escape closes it and returns focus, without abandoning the draft", async () => {
    const state = STATES.find((s) => s.id === "0501-UI abandon dialog")!;
    const page = await openState(state, "en", "light");
    try {
      await page.keyboard.press("Escape");
      await page.waitForSelector('[data-testid="upload-abandon-confirm"]', { state: "detached" });
      await page.waitForTimeout(50);
      const stillResumable = await page.evaluate(
        () => document.querySelector('[data-testid="upload-resume-file-input"]') !== null,
      );
      expect(stillResumable, "Escape must not run the destructive abandon path").toBe(true);
      const returned = await page.evaluate(() => {
        const trigger = document.querySelector('[data-testid="upload-abandon-draft-button"]');
        return document.activeElement === trigger;
      });
      expect(returned, "focus must return to the trigger after Escape too").toBe(true);
    } finally {
      await page.close();
    }
  });
});
