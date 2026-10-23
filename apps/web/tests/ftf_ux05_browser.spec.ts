// FTF-UX05 round 3, point 6 (QUEUE 0e; UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md
// §72.5): the real-Chromium evidence rounds 1 and 2 deferred for the models / capabilities / cost
// screens (FTF-0214, FTF-0308, FTF-0309, FTF-1301) -- viewports 1280/768/390/320, 200% zoom,
// keyboard-only Tab order with a visible focus outline, and an axe scan (light AND dark), over
// every state, en and ar. The components load data in effects and on click, so each page mounts
// the real component from a client bundle (vite, in memory) and replaces `fetch` with a route
// table that answers with REAL `Response` objects, the same technique `ftf_ux06_browser.spec.ts`
// and `ftf_ux03_browser.spec.ts` use. It runs in CI inside the existing `vitest run` step (Chromium
// is installed by the same job) on the head SHA.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import type { Kind, Route } from "./browser/ux05_entry";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/ux05_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const CONN = "22222222-2222-2222-2222-222222222222";
const PROJECT = "33333333-3333-3333-3333-333333333333";
const MODEL_A = "model-a";
const ALIAS = "llama-3-8b-instruct";
const ART_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const ART_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const ART_C = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const RATE = "dddddddd-dddd-dddd-dddd-dddddddddddd";

const PROJECTS_PATH = `/api/v1/organizations/${ORG}/projects`;
const CATALOG_PATH = `/api/v1/organizations/${ORG}/connections/litellm/${CONN}/catalog`;
const PROBE_PATH = `/models/${MODEL_A}/probe`;
const MAP_PATH = `/training-artifact`;
const CHANGES_PATH = `/training-artifact/changes`;
const ARTIFACTS_PATH = `/api/v1/organizations/${ORG}/artifacts`;
const CAP_PATH = `/capabilities`;
const COST_BASE = `/api/v1/organizations/${ORG}/cost-profiles`;
const SEARCH_PATH = `/costs/cost-per-success/search`;

// Long, unbroken values -- the realistic worst case the OVERFLOW-LONG-NAMES fixes target.
const LONG_PROJECT = `project_${"p".repeat(160)}`;
const LONG_MODEL = `org/${"m".repeat(110)}`;
const LONG_LABEL = `rate_${"l".repeat(150)}`;

// ---------------------------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------------------------
const PROJECT_ROWS = [{ id: PROJECT, name: "Acme Tuning Project", status: "active" }];

function catalogEntry(id = MODEL_A, over: Record<string, unknown> = {}) {
  return {
    id,
    status: "callable",
    metadata_status: "enriched",
    first_seen_at: "2026-10-06T12:00:00+00:00",
    last_seen_callable_at: "2026-10-06T12:00:00+00:00",
    fields: { max_tokens: 8192, note: `n_${"x".repeat(120)}` },
    ...over,
  };
}

function catalogBody(entries: unknown[] = [catalogEntry()], over: Record<string, unknown> = {}) {
  return {
    connection_status: "active",
    access_state: "healthy",
    last_successful_check_at: "2026-10-06T12:00:00+00:00",
    last_checked_at: "2026-10-06T12:00:00+00:00",
    alert: null,
    anomaly: null,
    model_groups: {
      state: "enriched",
      entries: { "grp-x": { note: `g_${"g".repeat(140)}` } },
    },
    entries,
    ...over,
  };
}

function artifact(id: string, name: string, status = "ready") {
  return {
    id,
    display_name: name,
    source_type: "huggingface",
    source_uri: `meta-llama/${name}`,
    model_family: "llama",
    base_model_revision: "0123456789abcdef0123456789abcdef01234567",
    weights_digest: `sha256:${"a".repeat(64)}`,
    license_id: "llama3",
    license_accepted_by: "admin@example.com",
    license_accepted_at: "2026-10-01T00:00:00+00:00",
    trust_remote_code_allowed: false,
    required_python_packages: ["torch==2.4.0"],
    minimum_gpu_profile: { gpu: "A100", memory_gb: 40 },
    status,
  };
}

function mappingBody(version: number, artifactId: string | null) {
  return {
    mapping_state: artifactId ? "mapped" : "unmapped",
    model_catalog_entry_id: artifactId ? "44444444-4444-4444-4444-444444444444" : null,
    litellm_connection_id: CONN,
    public_model_name: ALIAS,
    trainable_artifact_id: artifactId,
    catalog_version: version,
    last_verified_accessible_at: artifactId ? "2026-10-07T00:00:00+00:00" : null,
    last_changed_at: artifactId ? "2026-10-07T00:00:00+00:00" : null,
    artifact: artifactId ? artifact(artifactId, "Llama-3-8B") : null,
  };
}

const ARTIFACT_LIST = [
  artifact(ART_A, "Llama-3-8B"),
  artifact(ART_B, "Llama-3-70B"),
  artifact(ART_C, "Llama-3-1B", "registered"),
];

function capabilityBody(modelName: string, over: Record<string, unknown> = {}) {
  const unknown = (reason: string) => ({ state: "unknown", reason });
  return {
    litellm_connection_id: CONN,
    project_id: PROJECT,
    public_model_name: modelName,
    connection_status: "active",
    access: "granted",
    capability: "trainable_open_weight",
    trainable: true,
    capability_detail: "mapped artifact",
    capability_reason: null,
    role: "primary",
    artifact: {
      artifact_id: ART_A,
      availability: "ready",
      license_approved: true,
      weights_digest: `sha256:${"a".repeat(64)}`,
    },
    catalog_version: 3,
    method_support: unknown("no_backing_evidence"),
    min_gpu_vram_gb: {
      ...unknown("not_tested"),
      declared_minimum_gpu_profile: { gpu: "A100", memory_gb: 40 },
      declared_evidence: "declared",
    },
    chat_template: unknown("no_backing_evidence"),
    evidence: {
      discovery_checked_at: "2026-10-09T08:00:00+00:00",
      tool_call_probe_checked_at: null,
      tool_call_probe_present: false,
    },
    ...over,
  };
}

function rate(over: Record<string, unknown> = {}) {
  return {
    id: RATE,
    organization_id: ORG,
    profile_type: "provider",
    label: "OpenAI list price",
    rate_unit: "per_input_token",
    rate_amount: "0.0000025",
    currency: "USD",
    source: "https://example.com/pricing",
    rate_recorded_at: "2026-10-01T00:00:00+00:00",
    verified_at: null,
    expires_at: null,
    row_version: 1,
    created_by: "u",
    created_at: "2026-10-01T00:00:00+00:00",
    updated_at: "2026-10-01T00:00:00+00:00",
    ...over,
  };
}

function searchBody(over: Record<string, unknown> = {}) {
  return {
    results: [
      { ...rate(), certifiable_cheapest: false, disqualified_reason: "unverified" },
      {
        ...rate({
          id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
          label: LONG_LABEL,
          source: `https://example.com/${"s".repeat(200)}`,
          verified_at: "2026-10-02T00:00:00+00:00",
        }),
        certifiable_cheapest: true,
        disqualified_reason: null,
      },
    ],
    current_cheapest_certified: rate({
      label: LONG_LABEL,
      verified_at: "2026-10-02T00:00:00+00:00",
    }),
    ...over,
  };
}

// ---------------------------------------------------------------------------------------------
// State table
// ---------------------------------------------------------------------------------------------
type Step =
  { click: string } | { fill: [string, string] } | { select: [string, string] } | { wait: number };
interface UiState {
  id: string;
  kind: Kind;
  routes: Route[];
  props?: Record<string, unknown>;
  steps?: Step[];
  ready: string;
  /** Loading states have only the (disabled) trigger -- no focusable control is not a failure there. */
  noControlsExpected?: boolean;
}

const CATALOG_BASE_STEPS: Step[] = [
  { click: "load-projects-button" },
  { click: `project-select-button-${PROJECT}` },
  { click: "load-catalog-button" },
];

const MAPPED_FLOW: Step[] = [
  { click: "load-mapping-button" },
  { click: "load-artifacts-button" },
  { click: `artifact-select-button-${ART_B}` },
  { click: "map-button" },
];

const STATES: UiState[] = [
  // ----------------------------------------------------------------------------- 0214 catalog
  { id: "0214 idle", kind: "catalog", routes: [], ready: '[data-testid="load-projects-button"]' },
  {
    id: "0214 projects loading",
    kind: "catalog",
    routes: [{ method: "GET", path: PROJECTS_PATH, pending: true }],
    steps: [{ click: "load-projects-button" }, { wait: 1300 }],
    ready: '[role="status"]',
    noControlsExpected: false,
  },
  {
    id: "0214 projects empty",
    kind: "catalog",
    routes: [{ method: "GET", path: PROJECTS_PATH, body: [] }],
    steps: [{ click: "load-projects-button" }],
    ready: '[data-testid="projects-reload-button"]',
  },
  {
    id: "0214 projects populated with a long unbroken name",
    kind: "catalog",
    routes: [
      {
        method: "GET",
        path: PROJECTS_PATH,
        body: [{ id: PROJECT, name: LONG_PROJECT, status: "active" }],
      },
    ],
    steps: [{ click: "load-projects-button" }],
    ready: `[data-testid="project-row-${PROJECT}"]`,
  },
  {
    id: "0214 projects error 503",
    kind: "catalog",
    routes: [{ method: "GET", path: PROJECTS_PATH, status: 503, body: {} }],
    steps: [{ click: "load-projects-button" }],
    ready: '[data-testid="projects-error"]',
  },
  {
    id: "0214 projects non-JSON 200",
    kind: "catalog",
    routes: [
      { method: "GET", path: PROJECTS_PATH, rawText: "<html><body>proxy error</body></html>" },
    ],
    steps: [{ click: "load-projects-button" }],
    ready: '[data-testid="projects-error"]',
  },
  {
    id: "0214 catalog loading",
    kind: "catalog",
    routes: [
      { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
      { method: "GET", path: CATALOG_PATH, pending: true },
    ],
    steps: [...CATALOG_BASE_STEPS, { wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "0214 catalog populated (long fields and model-group values)",
    kind: "catalog",
    routes: [
      { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
      { method: "GET", path: CATALOG_PATH, body: catalogBody() },
    ],
    steps: CATALOG_BASE_STEPS,
    ready: `[data-testid="catalog-entry-${MODEL_A}"]`,
  },
  {
    id: "0214 catalog empty",
    kind: "catalog",
    routes: [
      { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
      { method: "GET", path: CATALOG_PATH, body: catalogBody([]) },
    ],
    steps: CATALOG_BASE_STEPS,
    ready: '[data-testid="catalog-reload-button"]',
  },
  {
    id: "0214 catalog network error",
    kind: "catalog",
    routes: [
      { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
      { method: "GET", path: CATALOG_PATH, reject: true },
    ],
    steps: CATALOG_BASE_STEPS,
    ready: '[data-testid="catalog-error"]',
  },
  {
    id: "0214 probe confirm open",
    kind: "catalog",
    routes: [
      { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
      { method: "GET", path: CATALOG_PATH, body: catalogBody() },
    ],
    steps: [...CATALOG_BASE_STEPS, { click: `probe-button-${MODEL_A}` }],
    ready: `[data-testid="confirm-probe-${MODEL_A}"]`,
  },
  {
    id: "0214 probe error 500",
    kind: "catalog",
    routes: [
      { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
      { method: "GET", path: CATALOG_PATH, body: catalogBody() },
      { method: "POST", path: PROBE_PATH, status: 500, body: {} },
    ],
    steps: [
      ...CATALOG_BASE_STEPS,
      { click: `probe-button-${MODEL_A}` },
      { click: `confirm-probe-button-${MODEL_A}` },
    ],
    ready: '[data-testid="probe-error"]',
  },
  // ----------------------------------------------------------------------------- 0308 mapping
  {
    id: "0308 idle",
    kind: "mapping",
    routes: [],
    ready: '[data-testid="load-mapping-button"]',
  },
  {
    id: "0308 mapping loading",
    kind: "mapping",
    routes: [{ method: "GET", path: MAP_PATH, pending: true }],
    steps: [{ click: "load-mapping-button" }, { wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "0308 mapped with a long model name",
    kind: "mapping",
    props: { modelName: LONG_MODEL },
    routes: [{ method: "GET", path: MAP_PATH, body: mappingBody(2, ART_A) }],
    steps: [{ click: "load-mapping-button" }],
    ready: '[data-testid="mapped-artifact-details"]',
  },
  {
    id: "0308 mapping error 500",
    kind: "mapping",
    routes: [{ method: "GET", path: MAP_PATH, status: 500, body: {} }],
    steps: [{ click: "load-mapping-button" }],
    ready: '[data-testid="mapping-error"]',
  },
  {
    id: "0308 mapping non-JSON 200",
    kind: "mapping",
    routes: [{ method: "GET", path: MAP_PATH, rawText: "<html>gateway</html>" }],
    steps: [{ click: "load-mapping-button" }],
    ready: '[data-testid="mapping-error"]',
  },
  {
    id: "0308 artifacts loading",
    kind: "mapping",
    routes: [{ method: "GET", path: ARTIFACTS_PATH, pending: true }],
    steps: [{ click: "load-artifacts-button" }, { wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "0308 artifacts empty",
    kind: "mapping",
    routes: [{ method: "GET", path: ARTIFACTS_PATH, body: [] }],
    steps: [{ click: "load-artifacts-button" }],
    ready: '[data-testid="artifacts-reload-button"]',
  },
  {
    id: "0308 artifacts error",
    kind: "mapping",
    routes: [{ method: "GET", path: ARTIFACTS_PATH, status: 503, body: {} }],
    steps: [{ click: "load-artifacts-button" }],
    ready: '[data-testid="artifacts-error"]',
  },
  {
    id: "0308 artifacts listed, one selected",
    kind: "mapping",
    routes: [
      { method: "GET", path: MAP_PATH, body: mappingBody(0, null) },
      { method: "GET", path: ARTIFACTS_PATH, body: ARTIFACT_LIST },
    ],
    steps: [
      { click: "load-mapping-button" },
      { click: "load-artifacts-button" },
      { click: `artifact-select-button-${ART_A}` },
    ],
    ready: '[data-testid="selected-artifact-details"]',
  },
  {
    id: "0308 confirm change open",
    kind: "mapping",
    routes: [
      { method: "GET", path: MAP_PATH, body: mappingBody(2, ART_A) },
      { method: "GET", path: ARTIFACTS_PATH, body: ARTIFACT_LIST },
    ],
    steps: MAPPED_FLOW,
    ready: '[data-testid="confirm-change"]',
  },
  {
    id: "0308 write error 503",
    kind: "mapping",
    routes: [
      { method: "GET", path: MAP_PATH, body: mappingBody(2, ART_A) },
      { method: "GET", path: ARTIFACTS_PATH, body: ARTIFACT_LIST },
      { method: "POST", path: MAP_PATH, status: 503, body: { detail: "gateway_unavailable" } },
    ],
    steps: [...MAPPED_FLOW, { click: "confirm-change-button" }],
    ready: '[data-testid="write-error"]',
  },
  {
    id: "0308 change notice after a write",
    kind: "mapping",
    routes: [
      { method: "GET", path: MAP_PATH, body: mappingBody(2, ART_A) },
      { method: "GET", path: ARTIFACTS_PATH, body: ARTIFACT_LIST },
      { method: "POST", path: MAP_PATH, body: { ...mappingBody(3, ART_B), changed: true } },
    ],
    steps: [...MAPPED_FLOW, { click: "confirm-change-button" }],
    ready: '[data-testid="mapping-notice"]',
  },
  {
    id: "0308 change history listed",
    kind: "mapping",
    routes: [
      {
        method: "GET",
        path: CHANGES_PATH,
        body: {
          changes: [
            {
              id: "ch-1",
              previous_trainable_artifact_id: null,
              new_trainable_artifact_id: ART_A,
              previous_catalog_version: 0,
              new_catalog_version: 1,
              actor: `actor_${"a".repeat(90)}@example.com`,
              request_id: "r1",
              created_at: "2026-10-07T00:00:00+00:00",
            },
          ],
        },
      },
    ],
    steps: [{ click: "load-changes-button" }],
    ready: '[data-testid="changes-list"]',
  },
  {
    id: "0308 change history empty",
    kind: "mapping",
    routes: [{ method: "GET", path: CHANGES_PATH, body: { changes: [] } }],
    steps: [{ click: "load-changes-button" }],
    ready: '[data-testid="changes-empty"]',
  },
  // ----------------------------------------------------------------------------- 0309 compat
  { id: "0309 idle", kind: "compat", routes: [], ready: "button" },
  {
    id: "0309 loading",
    kind: "compat",
    routes: [{ method: "GET", path: CAP_PATH, pending: true }],
    steps: [{ click: "__compat-check" }, { wait: 300 }],
    ready: 'button[aria-busy="true"]',
    noControlsExpected: true,
  },
  {
    id: "0309 loaded, train blocked, with a long model name",
    kind: "compat",
    props: { modelName: LONG_MODEL },
    routes: [{ method: "GET", path: CAP_PATH, body: capabilityBody(LONG_MODEL) }],
    steps: [{ click: "__compat-check" }],
    ready: "table",
  },
  {
    id: "0309 loaded (short data)",
    kind: "compat",
    routes: [{ method: "GET", path: CAP_PATH, body: capabilityBody(ALIAS) }],
    steps: [{ click: "__compat-check" }],
    ready: "table",
  },
  {
    id: "0309 error 503",
    kind: "compat",
    routes: [{ method: "GET", path: CAP_PATH, status: 503, body: {} }],
    steps: [{ click: "__compat-check" }],
    ready: '[role="alert"]',
  },
  {
    id: "0309 non-JSON 200",
    kind: "compat",
    routes: [{ method: "GET", path: CAP_PATH, rawText: "<html>502</html>" }],
    steps: [{ click: "__compat-check" }],
    ready: '[role="alert"]',
  },
  {
    id: "0309 malformed shape",
    kind: "compat",
    routes: [{ method: "GET", path: CAP_PATH, body: { nope: true } }],
    steps: [{ click: "__compat-check" }],
    ready: '[role="alert"]',
  },
  // ----------------------------------------------------------------------------- 1301 cost
  {
    id: "1301 loading",
    kind: "cost",
    routes: [{ method: "GET", path: SEARCH_PATH, pending: true }],
    steps: [{ wait: 300 }],
    ready: '[data-testid="cost-profile-loading"]',
  },
  {
    id: "1301 populated (long label and source), cheapest certified",
    kind: "cost",
    routes: [{ method: "GET", path: SEARCH_PATH, body: searchBody() }],
    ready: `[data-testid="cost-profile-verify-${RATE}"]`,
  },
  {
    id: "1301 empty",
    kind: "cost",
    routes: [
      {
        method: "GET",
        path: SEARCH_PATH,
        body: searchBody({ results: [], current_cheapest_certified: null }),
      },
    ],
    ready: '[data-testid="cost-profile-reload-button"]',
  },
  {
    id: "1301 search network error",
    kind: "cost",
    routes: [{ method: "GET", path: SEARCH_PATH, reject: true }],
    ready: '[data-testid="cost-profile-form-error"]',
  },
  {
    id: "1301 search non-JSON 200",
    kind: "cost",
    routes: [{ method: "GET", path: SEARCH_PATH, rawText: "<html>proxy</html>" }],
    ready: '[data-testid="cost-profile-form-error"]',
  },
  {
    id: "1301 search shape error",
    kind: "cost",
    routes: [{ method: "GET", path: SEARCH_PATH, body: {} }],
    ready: '[data-testid="cost-profile-form-error"]',
  },
  {
    id: "1301 create network error",
    kind: "cost",
    routes: [
      {
        method: "GET",
        path: SEARCH_PATH,
        body: searchBody({ results: [], current_cheapest_certified: null }),
      },
      { method: "POST", path: COST_BASE, reject: true },
    ],
    steps: [
      { fill: ["cost-profile-label-input", "My rate"] },
      { fill: ["cost-profile-rate-amount-input", "0.5"] },
      { fill: ["cost-profile-source-input", "https://example.com"] },
      { click: "cost-profile-submit-button" },
    ],
    ready: '[data-testid="cost-profile-form-error"]',
  },
  {
    id: "1301 verify network error",
    kind: "cost",
    routes: [
      { method: "GET", path: SEARCH_PATH, body: searchBody() },
      { method: "POST", path: `${COST_BASE}/${RATE}/verify`, reject: true },
    ],
    steps: [{ click: `cost-profile-verify-${RATE}` }],
    ready: '[data-testid="cost-profile-form-error"]',
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

const ROOT_SELECTOR: Record<Kind, string> = {
  catalog: '[data-testid="model-catalog-ui"]',
  mapping: '[data-testid="model-to-artifact-mapping-ui"]',
  compat: 'section[aria-label="Capability compatibility"]',
  cost: '[data-testid="cost-profile-panel"]',
};

function defaultProps(kind: Kind): Record<string, unknown> {
  const base = { apiBaseUrl: API, organizationId: ORG };
  switch (kind) {
    case "catalog":
      return { ...base, connectionId: CONN };
    case "mapping":
      return { ...base, connectionId: CONN, modelName: ALIAS };
    case "compat":
      return { ...base, connectionId: CONN, projectId: PROJECT, modelName: ALIAS };
    case "cost":
      return { ...base, projectId: PROJECT };
  }
}

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
      lib: { entry: ENTRY, name: "UX05Bundle", formats: ["iife"], fileName: "ux05" },
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

interface Opened {
  page: Page;
  /** Uncaught exceptions AND unhandled promise rejections seen in the page. */
  uncaught: string[];
}

async function openState(state: UiState, locale: Locale, scheme: Scheme): Promise<Opened> {
  const page = await browser.newPage({
    colorScheme: scheme,
    viewport: { width: 1280, height: 900 },
  });
  const uncaught: string[] = [];
  page.on("pageerror", (error) => uncaught.push(String(error)));
  await page.addInitScript(() => {
    window.addEventListener("unhandledrejection", (event) => {
      (window as unknown as { __unhandled: string[] }).__unhandled ??= [];
      (window as unknown as { __unhandled: string[] }).__unhandled.push(String(event.reason));
    });
  });
  const dir = locale === "ar" ? "rtl" : "ltr";
  await page.setContent(
    `<!doctype html><html lang="${locale}" dir="${dir}"><head><meta charset="utf-8" />` +
      `<meta name="viewport" content="width=device-width, initial-scale=1" />` +
      `<title>FTF-UX05 evidence</title><style>${TOKENS_CSS}</style></head>` +
      `<body><main><div id="root"></div></main></body></html>`,
    { waitUntil: "load" },
  );
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, kind, props }) => {
      const ux = (
        window as unknown as {
          UX05: { stubFetch: (r: unknown) => void; mount: (k: string, p: unknown) => void };
        }
      ).UX05;
      ux.stubFetch(routes);
      ux.mount(kind, props);
    },
    {
      routes: state.routes,
      kind: state.kind,
      props: { ...defaultProps(state.kind), ...state.props, locale },
    },
  );
  await page.waitForSelector(ROOT_SELECTOR[state.kind], { state: "visible", timeout: 10_000 });
  for (const step of state.steps ?? []) {
    if ("wait" in step) await page.waitForTimeout(step.wait);
    else if ("click" in step) {
      if (step.click === "__compat-check") await page.locator("section button").first().click();
      else await page.getByTestId(step.click).click();
    } else if ("select" in step) {
      await page.getByTestId(step.select[0]).selectOption(step.select[1]);
    } else await page.getByTestId(step.fill[0]).fill(step.fill[1]);
  }
  try {
    await page.waitForSelector(state.ready, { state: "visible", timeout: 10_000 });
  } catch (error) {
    throw new Error(
      `state "${state.id}" never became ready: ${uncaught.join(" | ") || String(error).slice(0, 200)}`,
    );
  }
  return { page, uncaught };
}

async function unhandled(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __unhandled?: string[] }).__unhandled ?? []);
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

async function rtlProblems(page: Page, kind: Kind, locale: Locale): Promise<string[]> {
  const attrs = await page.evaluate((selector) => {
    const root = document.querySelector(selector);
    const dialog = document.querySelector('[role="alertdialog"]');
    return root
      ? {
          dir: root.getAttribute("dir"),
          lang: root.getAttribute("lang"),
          dialog: dialog
            ? { dir: dialog.getAttribute("dir"), lang: dialog.getAttribute("lang") }
            : null,
        }
      : null;
  }, ROOT_SELECTOR[kind]);
  if (!attrs) return ["rtl: component root missing"];
  const want = { dir: locale === "ar" ? "rtl" : "ltr", lang: locale };
  const problems: string[] = [];
  if (attrs.dir !== want.dir) problems.push(`rtl: root dir=${attrs.dir}, want ${want.dir}`);
  if (attrs.lang !== want.lang) problems.push(`rtl: root lang=${attrs.lang}, want ${want.lang}`);
  if (attrs.dialog && (attrs.dialog.dir !== want.dir || attrs.dialog.lang !== want.lang)) {
    problems.push(
      `rtl: dialog dir=${attrs.dialog.dir} lang=${attrs.dialog.lang}, want ${want.dir}/${want.lang}`,
    );
  }
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
// The matrix: every state x en/ar x light/dark
// ---------------------------------------------------------------------------------------------
describe.concurrent("[UX05-BROWSER] real-Chromium §72.5 evidence", () => {
  for (const state of STATES) {
    for (const locale of ["en", "ar"] as Locale[]) {
      for (const scheme of ["light", "dark"] as Scheme[]) {
        test(`[${state.id}] ${locale} ${scheme}: axe, viewports, zoom, keyboard, focus indicator, rtl`, async () => {
          const { page, uncaught } = await openState(state, locale, scheme);
          try {
            const problems: string[] = [];
            problems.push(...(await axeProblems(page)));
            problems.push(...(await rtlProblems(page, state.kind, locale)));
            const keyboard = await keyboardProblems(page, state.noControlsExpected ?? false);
            problems.push(...keyboard.problems);
            problems.push(...(await overflowProblems(page)));
            problems.push(...uncaught.map((u) => `uncaught: ${u}`));
            problems.push(...(await unhandled(page)).map((u) => `unhandled rejection: ${u}`));
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
// Round 3 -- the named defects, each measured in real Chromium
// ---------------------------------------------------------------------------------------------
const byId = (id: string) => {
  const state = STATES.find((s) => s.id === id);
  if (!state) throw new Error(`no such state: ${id}`);
  return state;
};

/** The widest right edge (LTR) or left edge (RTL) of every match, relative to the viewport, in px. */
async function siteOverflow(page: Page, selector: string): Promise<number> {
  return page.evaluate((sel) => {
    const els = Array.from(document.querySelectorAll<HTMLElement>(sel));
    if (els.length === 0) return Number.NaN;
    const rtl = document.documentElement.dir === "rtl";
    return Math.max(
      ...els.map((el) => {
        const rects = Array.from(el.getClientRects());
        return Math.max(
          ...rects.map((r) => (rtl ? -r.left : r.right - document.documentElement.clientWidth)),
        );
      }),
    );
  }, selector);
}

async function overflowAt(page: Page, selector: string): Promise<Record<string, number>> {
  const out: Record<string, number> = {};
  for (const vp of VIEWPORTS) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    out[`${vp.width}`] = await siteOverflow(page, selector);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => {
    (document.documentElement.style as unknown as { zoom: string }).zoom = "2";
  });
  out["200%"] = await siteOverflow(page, selector);
  return out;
}

describe("[UX05-BROWSER] round 3, point 2: OVERFLOW-LONG-NAMES, each named site measured", () => {
  const SITES: Array<{ name: string; state: string; selector: string }> = [
    {
      name: "0214 project-name <bdi>",
      state: "0214 projects populated with a long unbroken name",
      selector: '[data-testid^="project-row-"] bdi',
    },
    {
      name: "0214 model-group row",
      state: "0214 catalog populated (long fields and model-group values)",
      selector: '[data-testid^="model-group-"]',
    },
    {
      name: "0214 entry fields",
      state: "0214 catalog populated (long fields and model-group values)",
      selector: '[data-testid^="entry-fields-"]',
    },
    {
      name: "0308 heading model-name <bdi>",
      state: "0308 mapped with a long model name",
      selector: '[data-testid="mapping-model-name"] bdi',
    },
    {
      name: "0308 base_model_revision <dd> (a normal 40-char revision)",
      state: "0308 mapped with a long model name",
      selector: '[data-testid="mapped-artifact-revision"]',
    },
    {
      name: "0308 source / digest <dd>",
      state: "0308 mapped with a long model name",
      selector: '[data-testid="mapped-artifact-source"], [data-testid="mapped-artifact-digest"]',
    },
    {
      name: "0308 change-history actor",
      state: "0308 change history listed",
      selector: '[data-testid^="change-row-"]',
    },
    {
      name: "0309 heading model-name <bdi>",
      state: "0309 loaded, train blocked, with a long model name",
      selector: "h2 bdi",
    },
    {
      name: "0309 dimension table (short data)",
      state: "0309 loaded (short data)",
      selector: "table",
    },
    {
      name: "1301 cheapest-certified paragraph",
      state: "1301 populated (long label and source), cheapest certified",
      selector: '[data-testid="current-cheapest-certified"] p',
    },
    {
      name: "1301 result row",
      state: "1301 populated (long label and source), cheapest certified",
      selector: '[data-testid^="cost-profile-result-"]',
    },
  ];
  for (const site of SITES) {
    test(`${site.name} never extends past the viewport at 1280/768/390/320 and 200% zoom (en and ar)`, async () => {
      for (const locale of ["en", "ar"] as Locale[]) {
        const { page } = await openState(byId(site.state), locale, "light");
        try {
          const measured = await overflowAt(page, site.selector);
          for (const [where, over] of Object.entries(measured)) {
            expect(Number.isNaN(over), `${site.name}: selector matched nothing`).toBe(false);
            expect(
              over,
              `${site.name} (${locale}) at ${where}: ${over}px past the edge`,
            ).toBeLessThanOrEqual(1);
          }
        } finally {
          await page.close();
        }
      }
    }, 60_000);
  }
});

describe("[UX05-BROWSER] round 3, point 1: ERRORS-SILENT, every 1301 case in real Chromium", () => {
  test.each([
    ["1301 search network error", "network error: Failed to fetch"],
    [
      "1301 search non-JSON 200",
      "The server sent a response this page cannot read. Nothing was changed.",
    ],
    ["1301 create network error", "network error: Failed to fetch"],
    ["1301 verify network error", "network error: Failed to fetch"],
  ])("%s: one role=alert, a recovery step, no unhandled rejection", async (id, expected) => {
    const { page, uncaught } = await openState(byId(id), "en", "light");
    try {
      const alerts = page.locator('[role="alert"]');
      expect(await alerts.count()).toBe(1);
      expect(await alerts.innerText()).toBe(expected);
      expect(await page.getByTestId("cost-profile-form-error-recovery").innerText()).toContain(
        "try again",
      );
      expect(uncaught).toEqual([]);
      expect(await unhandled(page)).toEqual([]);
    } finally {
      await page.close();
    }
  });

  test.each(["1301 search network error", "1301 search non-JSON 200", "1301 search shape error"])(
    "%s: the page never claims 'No certified cheapest rate yet' for a search that did not complete",
    async (id) => {
      const { page } = await openState(byId(id), "en", "light");
      try {
        expect(await page.getByText(/No certified cheapest rate yet/).count()).toBe(0);
        await page
          .getByTestId("cheapest-rate-unknown")
          .waitFor({ state: "visible", timeout: 5000 });
      } finally {
        await page.close();
      }
    },
  );

  test("a real response that is not JSON under ar: the Arabic message, never the browser's parser text", async () => {
    const { page } = await openState(byId("1301 search non-JSON 200"), "ar", "light");
    try {
      const text = await page.getByTestId("cost-profile-form-error").innerText();
      expect(text).toBe("أرسل الخادم استجابة لا تستطيع هذه الصفحة قراءتها. لم يتم تغيير أي شيء.");
      expect(text).not.toMatch(/Unexpected|JSON|html/i);
    } finally {
      await page.close();
    }
  });

  test.each([
    ["0214 projects non-JSON 200", "projects-error"],
    ["0308 mapping non-JSON 200", "mapping-error"],
  ])("%s under ar: the Arabic malformed-response message", async (id, testId) => {
    const { page } = await openState(byId(id), "ar", "light");
    try {
      expect(await page.getByTestId(testId).innerText()).toBe(
        "أرسل الخادم استجابة لا تستطيع هذه الصفحة قراءتها. لم يتم تغيير أي شيء.",
      );
    } finally {
      await page.close();
    }
  });
});

describe("[UX05-BROWSER] round 3, point 3: MISSING-STATES", () => {
  test.each([
    ["0214 projects empty", "No projects found", "لم يتم العثور على مشاريع"],
    ["0308 artifacts empty", "No trainable artifacts", "لا توجد مصنفات قابلة للتدريب"],
    ["1301 empty", "No rates recorded yet", "لم تُسجَّل أسعار بعد"],
    ["0214 catalog empty", "No catalog entries", "لا توجد عناصر في الكتالوج"],
  ])("%s shows an empty state with a first action, en and ar", async (id, en, ar) => {
    for (const [locale, title] of [
      ["en", en],
      ["ar", ar],
    ] as const) {
      const { page } = await openState(byId(id), locale, "light");
      try {
        await page
          .getByRole("heading", { name: title })
          .waitFor({ state: "visible", timeout: 5000 });
        expect(await page.locator("section[aria-label] button").count()).toBeGreaterThan(0);
      } finally {
        await page.close();
      }
    }
  });

  test.each([
    ["0214 projects loading", "load-projects-button"],
    ["0214 catalog loading", "load-catalog-button"],
    ["0308 mapping loading", "load-mapping-button"],
    ["0308 artifacts loading", "load-artifacts-button"],
  ])("%s: a visible busy indicator after 1 s and an aria-busy trigger", async (id, trigger) => {
    const { page } = await openState(byId(id), "en", "light");
    try {
      await page.locator('[role="status"]').waitFor({ state: "visible", timeout: 5000 });
      expect(await page.getByTestId(trigger).getAttribute("aria-busy")).toBe("true");
      expect(await page.getByTestId(trigger).isDisabled()).toBe(true);
    } finally {
      await page.close();
    }
  });

  test("an empty state never appears before a load or after a failed load", async () => {
    for (const id of ["0214 projects error 503", "0308 artifacts error"]) {
      const { page } = await openState(byId(id), "en", "light");
      try {
        expect(await page.getByTestId("projects-reload-button").count()).toBe(0);
        expect(await page.getByTestId("artifacts-reload-button").count()).toBe(0);
      } finally {
        await page.close();
      }
    }
  });
});

describe("[UX05-BROWSER] round 3: focus after the confirm dialogs (keyboard-only)", () => {
  test("0308: Cancel takes focus on open, Escape returns it to the trigger, Confirm lands on the changed state", async () => {
    const { page } = await openState(byId("0308 confirm change open"), "en", "light");
    try {
      const active = () => page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
      await expect.poll(active).toBe("cancel-change-button");
      await page.keyboard.press("Escape");
      await expect.poll(active).toBe("map-button");
      await page.keyboard.press("Enter");
      await expect.poll(active).toBe("cancel-change-button");
      // DOM order inside the dialog is Confirm, then Cancel: Shift+Tab reaches Confirm.
      await page.keyboard.press("Shift+Tab");
      expect(await active()).toBe("confirm-change-button");
      // The POST is not stubbed in this state (404): a failed write puts focus on the announced
      // error, never on <body> (the disabled-while-writing Confirm button drops its focus).
      await page.keyboard.press("Enter");
      await expect.poll(active).toBe("write-error");
    } finally {
      await page.close();
    }
  });

  test("0308: a failed write (503) leaves focus on the announced write-error, not <body>", async () => {
    const { page } = await openState(byId("0308 write error 503"), "en", "light");
    try {
      const active = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
      expect(active).toBe("write-error");
      expect(await page.getByTestId("confirm-change-button").isEnabled()).toBe(true);
    } finally {
      await page.close();
    }
  });

  test("0308: a successful write leaves focus on the changed mapping state", async () => {
    const { page } = await openState(byId("0308 change notice after a write"), "en", "light");
    try {
      const active = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
      expect(active).toBe("mapping-state");
    } finally {
      await page.close();
    }
  });

  test("0214: Cancel takes focus on open, Escape returns it to the trigger, Confirm lands on the entry row", async () => {
    const { page } = await openState(byId("0214 probe confirm open"), "en", "light");
    try {
      const active = () => page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
      await expect.poll(active).toBe(`cancel-probe-button-${MODEL_A}`);
      await page.keyboard.press("Escape");
      await expect.poll(active).toBe(`probe-button-${MODEL_A}`);
      await page.keyboard.press("Enter");
      await page.getByTestId(`confirm-probe-button-${MODEL_A}`).focus();
      await page.keyboard.press("Enter");
      await expect.poll(active).toBe(`catalog-entry-${MODEL_A}`);
    } finally {
      await page.close();
    }
  });

  test("the confirm dialog has an accessible name in both languages", async () => {
    for (const [locale, name] of [
      ["en", /Changing this mapping/],
      ["ar", /تغيير هذا الربط/],
    ] as const) {
      const { page } = await openState(byId("0308 confirm change open"), locale, "light");
      try {
        await page.getByRole("alertdialog", { name }).waitFor({ state: "visible", timeout: 5000 });
      } finally {
        await page.close();
      }
    }
  });
});
