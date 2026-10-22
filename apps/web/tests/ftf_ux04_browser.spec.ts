// FTF-UX04 (UX_STANDARD.md section 4 / FINE_TUNING_FACTORY_RELEASE_1.md section 72.5): the viewport
// (1280/768/390/320), 200 % zoom, keyboard-only and automated-accessibility (axe, light AND dark)
// evidence for the two rebuilt project-mandate components -- FTF-0401 ProjectSetupForm and FTF-0408
// RequirementsPanel -- in a REAL Chromium, in CI.
//
// The components load data in effects and on click, so the page mounts the real components from a
// client bundle (vite, in memory) and replaces `fetch` with a route table: loading, empty, populated,
// validation, error, conflict and saved states all exist in a real layout engine. Each state is
// opened once per locale (en, ar/rtl) and colour scheme and every check family runs on that one page.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import type { Route } from "./browser/ux04_entry";
import {
  axeProblems,
  keyboardProblems,
  overflowProblems,
  regionScrollProblems,
  rtlProblems,
  type Locale,
  type Scheme,
} from "./browser/page_checks";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/ux04_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const WS = "22222222-2222-2222-2222-222222222222";
const PROJECT = "33333333-3333-3333-3333-333333333333";
const POLICY = "44444444-4444-4444-4444-444444444444";
const PROJECTS = `/api/v1/organizations/${ORG}/projects`;
const ITEM = `${PROJECTS}/${PROJECT}`;
const REQUIREMENTS = `${ITEM}/requirements`;
const BUDGET = `${ITEM}/budget-policy`;

// ---------------------------------------------------------------------------------------------
// Fixtures (shapes taken from ftf_ux04.spec.ts, which pins them against the parsers)
// ---------------------------------------------------------------------------------------------
function project(over: Record<string, unknown> = {}) {
  return {
    id: PROJECT,
    organization_id: ORG,
    workspace_id: WS,
    name: "acme-agent",
    description: null,
    project_type: "agentic_product",
    agent_objective: "Automate PR review comments.",
    security_profile: "standard",
    budget_policy_id: null,
    status: "setup_incomplete",
    ui_state: {},
    row_version: 1,
    created_by: "user-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    archived_at: null,
    ...over,
  };
}
const DRAFT_SAVED_STATE = {
  name: "acme-agent",
  projectType: "agentic_product",
  description: "",
  agentObjective: "Automate PR review comments.",
  securityProfile: "standard",
};
const PROPS_0401 = { apiBaseUrl: API, organizationId: ORG, workspaceId: WS };

function requirement(over: Record<string, unknown> = {}) {
  return {
    tool_definition_id: "tool-1",
    name: "issue_refund",
    side_effect_class: "financial",
    is_critical: true,
    requires_confirmation: true,
    measurable: true,
    runtime_enforced: true,
    ...over,
  };
}
function policy(over: Record<string, unknown> = {}) {
  return {
    id: POLICY,
    organization_id: ORG,
    project_id: PROJECT,
    total_amount: "1000.0000",
    currency: "USD",
    scopes: ["training", "evaluation"],
    reserved_amount: "400.0000",
    available_amount: "600.0000",
    row_version: 2,
    created_by: "user-1",
    created_at: "2026-09-01T00:00:00+00:00",
    updated_at: "2026-09-01T00:00:00+00:00",
    ...over,
  };
}
const PROPS_0408 = {
  apiBaseUrl: API,
  organizationId: ORG,
  projectId: PROJECT,
  securityProfile: "zero_egress",
};
const TOOLS = [
  requirement(),
  requirement({ tool_definition_id: "tool-2", name: "delete_account", runtime_enforced: false }),
  requirement({
    tool_definition_id: "tool-3",
    name: "read_ticket",
    side_effect_class: "read_only",
    is_critical: false,
    requires_confirmation: false,
    runtime_enforced: false,
  }),
];
// Realistic UNBROKEN user data, with no hyphen or space to wrap at.
const LONG_NAME = `customer_support_agent_${"n".repeat(170)}`;
const LONG_OBJECTIVE = `objective_${"o".repeat(300)}`;
const LONG_PROFILE = `legacy_profile_${"p".repeat(48)}`;
const LONG_TOOL = `issue_refund_for_${"t".repeat(60)}_order`;
const LONG_CLASS = `financial_side_effect_${"c".repeat(50)}`;

// ---------------------------------------------------------------------------------------------
// State table
// ---------------------------------------------------------------------------------------------
type Step =
  { click: string } | { fill: [string, string] } | { select: [string, string] } | { wait: number };
interface UiState {
  id: string;
  component: "0401" | "0408";
  props: Record<string, unknown>;
  routes: Route[];
  steps?: Step[];
  /** A selector that is visible once the state has settled. */
  ready: string;
}
const ROOT_TESTID: Record<UiState["component"], string> = {
  "0401": "project-setup-form",
  "0408": "requirements-panel",
};

const FORM_ERR = '[data-testid="project-form-error"]';
const PANEL_SETTLE: Step = { wait: 250 };

const STATES: UiState[] = [
  // ---- FTF-0401 project form ---------------------------------------------------------------
  {
    id: "0401 create empty",
    component: "0401",
    props: PROPS_0401,
    routes: [],
    ready: '[data-testid="project-setup-form"]',
  },
  {
    id: "0401 create agentic with objective",
    component: "0401",
    props: PROPS_0401,
    routes: [],
    steps: [
      { fill: ["project-name-input", "agent-1"] },
      { select: ["project-type-select", "agentic_product"] },
    ],
    ready: '[data-testid="agent-objective-input"]',
  },
  {
    id: "0401 create validation errors",
    component: "0401",
    props: PROPS_0401,
    routes: [],
    steps: [
      { select: ["project-type-select", "agentic_product"] },
      { click: "project-submit-button" },
    ],
    ready: '[role="alert"][tabindex="-1"]',
  },
  {
    id: "0401 create saved",
    component: "0401",
    props: PROPS_0401,
    routes: [
      {
        method: "POST",
        path: PROJECTS,
        status: 201,
        body: project({
          name: "harness-project",
          project_type: "generic_model_tuning",
          agent_objective: null,
          status: "active",
        }),
      },
    ],
    steps: [
      { fill: ["project-name-input", "harness-project"] },
      { click: "project-submit-button" },
    ],
    ready: '[data-testid="project-saved-notice"]',
  },
  {
    id: "0401 edit populated",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [],
    ready: '[data-testid="setup-incomplete-notice"]',
  },
  {
    id: "0401 edit archived",
    component: "0401",
    props: {
      ...PROPS_0401,
      project: project({ status: "archived", archived_at: "2026-02-01T00:00:00Z" }),
    },
    routes: [],
    ready: '[data-testid="project-archived-notice"]',
  },
  {
    id: "0401 edit with a saved draft offered",
    component: "0401",
    props: {
      ...PROPS_0401,
      project: project({
        ui_state: {
          name: "from-draft",
          projectType: "agentic_product",
          description: "d",
          agentObjective: "o",
          securityProfile: "zero_egress",
        },
      }),
    },
    routes: [],
    ready: '[data-testid="project-draft-offer"]',
  },
  {
    id: "0401 edit draft restored",
    component: "0401",
    props: {
      ...PROPS_0401,
      project: project({
        ui_state: {
          name: "from-draft",
          projectType: "agentic_product",
          description: "d",
          agentObjective: "o",
          securityProfile: "zero_egress",
        },
      }),
    },
    routes: [],
    steps: [{ click: "project-restore-draft-button" }],
    ready: '[data-testid="project-draft-restored-notice"]',
  },
  {
    id: "0401 edit saved",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [{ method: "PATCH", path: ITEM, body: project({ row_version: 2 }) }],
    steps: [{ click: "project-submit-button" }],
    ready: '[data-testid="project-saved-notice"]',
  },
  {
    id: "0401 edit draft saved",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [
      { method: "POST", path: `${ITEM}/ui-state`, body: project({ ui_state: DRAFT_SAVED_STATE }) },
    ],
    steps: [{ click: "project-save-draft-button" }],
    ready: '[data-testid="project-draft-saved-notice"]',
  },
  {
    id: "0401 edit saving (loading)",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [{ method: "PATCH", path: ITEM, pending: true }],
    steps: [{ click: "project-submit-button" }, { wait: 1200 }],
    ready: '[data-testid="project-setup-form"][aria-busy="true"]',
  },
  {
    id: "0401 error conflict 409",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [
      {
        method: "PATCH",
        path: ITEM,
        status: 409,
        body: { detail: "project x has row_version 4, not the expected 3" },
      },
    ],
    steps: [{ click: "project-submit-button" }],
    ready: FORM_ERR,
  },
  {
    id: "0401 error forbidden 403",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [{ method: "PATCH", path: ITEM, status: 403, body: { detail: "role_not_authorized" } }],
    steps: [{ click: "project-submit-button" }],
    ready: FORM_ERR,
  },
  {
    id: "0401 error server 500",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [{ method: "PATCH", path: ITEM, status: 500, body: {} }],
    steps: [{ click: "project-submit-button" }],
    ready: FORM_ERR,
  },
  {
    id: "0401 error network",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [{ method: "PATCH", path: ITEM, reject: true }],
    steps: [{ click: "project-submit-button" }],
    ready: FORM_ERR,
  },
  {
    id: "0401 error unreadable reply",
    component: "0401",
    props: { ...PROPS_0401, project: project() },
    routes: [{ method: "PATCH", path: ITEM, body: { id: PROJECT } }],
    steps: [{ click: "project-submit-button" }],
    ready: FORM_ERR,
  },
  {
    id: "0401 unbroken name, objective and profile",
    component: "0401",
    props: {
      ...PROPS_0401,
      project: project({
        name: LONG_NAME,
        agent_objective: LONG_OBJECTIVE,
        security_profile: LONG_PROFILE,
        description: LONG_OBJECTIVE,
      }),
    },
    routes: [],
    ready: '[data-testid="setup-incomplete-notice"]',
  },
  {
    id: "0401 unbroken name in an error state",
    component: "0401",
    props: { ...PROPS_0401, project: project({ name: LONG_NAME }) },
    routes: [{ method: "PATCH", path: ITEM, status: 409, body: {} }],
    steps: [{ click: "project-submit-button" }],
    ready: FORM_ERR,
  },
  // ---- FTF-0408 requirements panel ---------------------------------------------------------
  {
    id: "0408 loading",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, pending: true },
      { method: "GET", path: BUDGET, pending: true },
    ],
    steps: [{ wait: 1200 }],
    ready: '[data-testid="requirements-loading"]',
  },
  {
    id: "0408 empty requirements and no policy",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: [] },
      { method: "GET", path: BUDGET, status: 404, body: { detail: "not_found" } },
    ],
    steps: [PANEL_SETTLE],
    ready: '[data-testid="requirements-empty"]',
  },
  {
    id: "0408 populated with a conflict",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: TOOLS },
      { method: "GET", path: BUDGET, body: policy() },
    ],
    steps: [PANEL_SETTLE],
    ready: '[data-testid="requirement-conflict-tool-2"]',
  },
  {
    id: "0408 unbroken tool names",
    component: "0408",
    props: PROPS_0408,
    routes: [
      {
        method: "GET",
        path: REQUIREMENTS,
        body: [
          requirement({ name: LONG_TOOL, side_effect_class: LONG_CLASS, runtime_enforced: false }),
        ],
      },
      { method: "GET", path: BUDGET, body: policy() },
    ],
    steps: [PANEL_SETTLE],
    ready: '[data-testid="requirement-conflict-tool-1"]',
  },
  {
    id: "0408 unbroken security profile",
    component: "0408",
    props: { ...PROPS_0408, securityProfile: LONG_PROFILE },
    routes: [
      { method: "GET", path: REQUIREMENTS, body: [] },
      { method: "GET", path: BUDGET, body: policy() },
    ],
    steps: [PANEL_SETTLE],
    ready: '[data-testid="budget-row-version"]',
  },
  {
    id: "0408 create validation: no scope chosen",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: TOOLS },
      { method: "GET", path: BUDGET, status: 404, body: { detail: "not_found" } },
    ],
    steps: [
      PANEL_SETTLE,
      { fill: ["budget-total-amount-input", "2500.50"] },
      { click: "budget-policy-submit-button" },
    ],
    ready: '[data-testid="budget-scopes-error"]',
  },
  {
    id: "0408 revise validation: total below reserved",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: TOOLS },
      { method: "GET", path: BUDGET, body: policy() },
    ],
    steps: [
      PANEL_SETTLE,
      { fill: ["budget-total-amount-input", "399.99"] },
      { click: "budget-policy-submit-button" },
    ],
    ready: "#budget-total-amount-error",
  },
  {
    id: "0408 revise saving (loading)",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: TOOLS },
      { method: "GET", path: BUDGET, body: policy() },
      { method: "PATCH", path: BUDGET, pending: true },
    ],
    steps: [PANEL_SETTLE, { click: "budget-policy-submit-button" }, { wait: 1200 }],
    ready: '[data-testid="budget-policy-form"][aria-busy="true"]',
  },
  {
    id: "0408 revise saved",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: TOOLS },
      { method: "GET", path: BUDGET, body: policy() },
      { method: "PATCH", path: BUDGET, body: policy({ row_version: 3 }) },
    ],
    steps: [PANEL_SETTLE, { click: "budget-policy-submit-button" }],
    ready: '[data-testid="budget-policy-saved-notice"]',
  },
  {
    id: "0408 error save conflict 409",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: TOOLS },
      { method: "GET", path: BUDGET, body: policy() },
      { method: "PATCH", path: BUDGET, status: 409, body: { detail: "row_version mismatch" } },
    ],
    steps: [PANEL_SETTLE, { click: "budget-policy-submit-button" }],
    ready: '[data-testid="budget-policy-error"]',
  },
  {
    id: "0408 error requirements 500",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, status: 500, body: {} },
      { method: "GET", path: BUDGET, status: 404, body: {} },
    ],
    steps: [PANEL_SETTLE],
    ready: '[data-testid="requirements-error"]',
  },
  {
    id: "0408 error budget 500",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: TOOLS },
      { method: "GET", path: BUDGET, status: 500, body: {} },
    ],
    steps: [PANEL_SETTLE],
    ready: '[data-testid="budget-policy-load-error"]',
  },
  {
    id: "0408 error network on both",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, reject: true },
      { method: "GET", path: BUDGET, reject: true },
    ],
    steps: [PANEL_SETTLE],
    ready: '[data-testid="budget-policy-load-error"]',
  },
  {
    id: "0408 error unreadable reply",
    component: "0408",
    props: PROPS_0408,
    routes: [
      { method: "GET", path: REQUIREMENTS, body: { not: "a list" } },
      { method: "GET", path: BUDGET, body: { id: POLICY } },
    ],
    steps: [PANEL_SETTLE],
    ready: '[data-testid="budget-policy-load-error"]',
  },
];

// ---------------------------------------------------------------------------------------------
// Browser plumbing
// ---------------------------------------------------------------------------------------------
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
      lib: { entry: ENTRY, name: "UX04Bundle", formats: ["iife"], fileName: "ux04" },
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
      `<title>FTF-UX04 evidence</title><style>${TOKENS_CSS}</style></head>` +
      `<body><main><div id="root"></div></main></body></html>`,
    { waitUntil: "load" },
  );
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, component, props }) => {
      const ux = (
        window as unknown as {
          UX04: { stubFetch: (r: unknown) => void; mount: (...a: unknown[]) => void };
        }
      ).UX04;
      ux.stubFetch(routes);
      ux.mount(component, props);
    },
    { routes: state.routes, component: state.component, props: { ...state.props, locale } },
  );
  for (const step of state.steps ?? []) {
    if ("wait" in step) await page.waitForTimeout(step.wait);
    else if ("click" in step) await page.getByTestId(step.click).click();
    else if ("select" in step) await page.getByTestId(step.select[0]).selectOption(step.select[1]);
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
// The spec
// ---------------------------------------------------------------------------------------------
describe.concurrent(
  "[UX04-BROWSER] real-Chromium section 72.5 evidence for the two project-mandate components",
  () => {
    for (const state of STATES) {
      for (const locale of ["en", "ar"] as Locale[]) {
        for (const scheme of ["light", "dark"] as Scheme[]) {
          test(`[${state.id}] ${locale} ${scheme}: axe, viewports, zoom, keyboard, focus indicator, rtl`, async () => {
            const page = await openState(state, locale, scheme);
            try {
              const problems: string[] = [];
              problems.push(...(await axeProblems(page)));
              problems.push(...(await rtlProblems(page, ROOT_TESTID[state.component], locale)));
              const keyboard = await keyboardProblems(page, state.id.includes("loading"));
              problems.push(...keyboard.problems);
              problems.push(...(await overflowProblems(page)));
              problems.push(...(await regionScrollProblems(page)));
              expect(problems, problems.join("\n")).toEqual([]);
            } finally {
              await page.close();
            }
          }, 60_000);
        }
      }
    }
  },
);

test("[UX04-BROWSER] both components are exercised in loading, empty, populated, validation, error and saved states", () => {
  const per = new Map<string, number>();
  for (const s of STATES) per.set(s.component, (per.get(s.component) ?? 0) + 1);
  expect(Array.from(per.keys()).sort()).toEqual(["0401", "0408"]);
  for (const [component, n] of per) expect(n, component).toBeGreaterThanOrEqual(10);
  for (const kind of ["loading", "error", "validation", "saved", "unbroken", "populated"]) {
    for (const component of per.keys()) {
      expect(
        STATES.some((s) => s.component === component && s.id.includes(kind)),
        `${component} has a "${kind}" state`,
      ).toBe(true);
    }
  }
});
