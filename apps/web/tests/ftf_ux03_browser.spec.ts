// FTF-UX03 round 2, point 7 (QUEUE 0g; UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md §72.5):
// the viewport (1280/768/390), 200 % zoom, keyboard-only and automated-accessibility (axe, light AND
// dark) evidence for the five rebuilt target-application components, in a REAL Chromium, in CI.
//
// The components load data in effects and on click, so the page mounts the real components from a
// client bundle (vite, in memory) and replaces `fetch` with a route table: loading, empty, populated,
// error, conflict and read-only states all exist in a real layout engine. Each state is opened once
// per locale (en, ar/rtl) and colour scheme and every check family runs on that one page.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import type { Route } from "./browser/ux03_entry";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/ux03_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const CONNECTION = "33333333-3333-3333-3333-333333333333";
const SNAPSHOT = "44444444-4444-4444-4444-444444444444";
const CAND_A = "55555555-5555-5555-5555-555555555555";
const CAND_B = "66666666-6666-6666-6666-666666666666";
const MANIFEST = "77777777-7777-7777-7777-777777777777";
const SHA = "c".repeat(40);
const P = `/api/v1/organizations/${ORG}/projects/${PROJECT}`;
const T = `${P}/target-connections`;
const MANIFEST_PATH = `${P}/manifests/${MANIFEST}`;

// ---------------------------------------------------------------------------------------------
// Fixtures (shapes taken from the component specs: ftf_ux03_selectors / ftf_04a21 / ftf_ux03_04a22)
// ---------------------------------------------------------------------------------------------
const LONG_REPO =
  "organization-with-a-very-long-name/service-repository-name-that-keeps-going-and-going";
const REPOS = {
  repositories: [
    { repository_identity: LONG_REPO, private: true, default_branch: "main" },
    ...["alpha", "bravo", "charlie", "delta"].map((n) => ({
      repository_identity: `acme/${n}`,
      private: false,
      default_branch: "main",
    })),
  ],
  page: 1,
  per_page: 30,
  has_next_page: true,
};
const BRANCHES = {
  branches: [
    { name: "main", list_time_commit_sha: "a".repeat(40) },
    {
      name: "release/2026-10-very-long-branch-name-for-wrapping-checks",
      list_time_commit_sha: "b".repeat(40),
    },
    { name: "feature/x", list_time_commit_sha: "d".repeat(40) },
  ],
  page: 1,
  per_page: 30,
  has_next_page: true,
};
const CANDIDATES = {
  items: [
    {
      id: CAND_A,
      display_name: "InternalTriageAgent",
      framework_or_runtime: "python",
      confidence_score: 0.42,
      discovery_status: "candidate",
      organization_id: ORG,
      target_source_snapshot_id: SNAPSHOT,
    },
    {
      id: CAND_B,
      display_name: "CustomerSupportAgentWithAVeryLongDisplayNameThatMustWrap",
      framework_or_runtime: null,
      confidence_score: 0.96,
      discovery_status: "candidate",
      organization_id: ORG,
      target_source_snapshot_id: SNAPSHOT,
    },
  ],
  limit: 50,
  offset: 0,
  total: 2,
};

const JSON_FIELDS = [
  "agent_entrypoint_json",
  "current_model_binding_json",
  "system_prompt_refs_json",
  "tool_contracts_json",
  "input_contract_json",
  "output_contract_json",
  "mandate_json",
  "forbidden_actions_json",
  "permission_model_json",
  "required_context_json",
  "runtime_dependencies_json",
  "evaluation_harness_json",
  "trace_source_contract_json",
  "deployment_contract_json",
];
function manifest21(over: Record<string, unknown> = {}) {
  const base: Record<string, unknown> = {
    id: MANIFEST,
    organization_id: ORG,
    project_id: PROJECT,
    target_source_snapshot_id: SNAPSHOT,
    version: 1,
    status: "review_required",
    agent_name: "support-agent",
    manifest_sha256: null,
    created_by: "user-1",
    approved_by: null,
    approved_at: null,
    created_at: "2026-10-07T00:00:00+00:00",
  };
  for (const f of JSON_FIELDS) base[f] = null;
  base.agent_entrypoint_json = { path: "agent/main.py", symbol: "run" };
  base.current_model_binding_json = {
    resolved: true,
    model_alias: "gpt-prod",
    role: "current_production",
  };
  base.runtime_dependencies_json = { python: ">=3.11" };
  return { ...base, ...over };
}
const EVIDENCE = [
  {
    id: "ev-1",
    organization_id: ORG,
    agent_manifest_id: MANIFEST,
    field_path: "agent_entrypoint_json",
    evidence_type: "repo_symbol",
    repository_commit_sha: "abcdef0123456789abcdef0123456789abcdef01",
    file_path: "agent/main.py",
    line_start: 10,
    line_end: 24,
    symbol: "run",
    content_hash: "sha256:feed",
    confidence_score: 0.9,
  },
];
function manifest22(over: Record<string, unknown> = {}) {
  return {
    id: MANIFEST,
    organization_id: ORG,
    project_id: PROJECT,
    target_source_snapshot_id: SNAPSHOT,
    version: 1,
    status: "draft",
    agent_name: "support-agent",
    mandate_json: null,
    forbidden_actions_json: null,
    manifest_sha256: null,
    ...over,
  };
}
function requirement(over: Record<string, unknown> = {}) {
  return {
    tool_definition_id: "tool-1",
    name: "refund",
    side_effect_class: "critical",
    is_critical: true,
    requires_confirmation: true,
    measurable: true,
    runtime_enforced: true,
    ...over,
  };
}
const CONFLICT_MANIFEST = manifest22({
  forbidden_actions_json: {
    actions: [
      { name: "refund", reason: "finance policy" },
      { name: "export_data", reason: "" },
      { name: "ghost_tool", reason: "unknown" },
      { name: "delete_user", reason: "irreversible" },
    ],
  },
});
const CONFLICT_REQUIREMENTS = [
  requirement({ name: "refund", runtime_enforced: false }),
  requirement({ name: "export_data", is_critical: false, requires_confirmation: false }),
  requirement({ name: "delete_user", runtime_enforced: true }),
];

// Round 3 (point 1): realistic UNBROKEN names, with no hyphen or space to wrap at. 100 characters is
// GitHub's maximum repository name; the snake_case tool and branch names are what real code uses.
const UNBROKEN_REPO = `acme/${"r".repeat(95)}`;
const UNBROKEN_BRANCH = `release_${"b".repeat(56)}`;
const UNBROKEN_TOOL = `issue_refund_for_${"t".repeat(40)}_order`;
const UNBROKEN_TOOL_2 = `export_customer_data_${"u".repeat(40)}`;
// Round 3 (reviewer): a realistic evidence path and symbol, unbroken, in the 04A21 evidence row.
const UNBROKEN_EVIDENCE = [
  {
    ...EVIDENCE[0]!,
    file_path: "src/customer_support_agent/integrations/zendesk_ticket_escalation_handler.py",
    symbol: "CustomerSupportAgent.handle_ticket_escalation_request",
  },
];
const UNBROKEN_MANIFEST = manifest22({
  forbidden_actions_json: {
    actions: [
      { name: UNBROKEN_TOOL, reason: "finance policy" },
      { name: UNBROKEN_TOOL_2, reason: "" },
    ],
  },
});
const UNBROKEN_REQUIREMENTS = [
  requirement({ name: UNBROKEN_TOOL, runtime_enforced: false }),
  requirement({ name: UNBROKEN_TOOL_2, is_critical: false, requires_confirmation: false }),
];

// ---------------------------------------------------------------------------------------------
// State table
// ---------------------------------------------------------------------------------------------
type Step = { click: string } | { fill: [string, string] } | { wait: number };
interface UiState {
  id: string;
  component: "04A03" | "04A04" | "04A18" | "04A21" | "04A22";
  props: Record<string, unknown>;
  routes: Route[];
  steps?: Step[];
  /** A selector that is visible once the state has settled. */
  ready: string;
  storageItems?: Record<string, string>;
}

const ROOT_TESTID: Record<UiState["component"], string> = {
  "04A03": "github-repository-selector",
  "04A04": "github-branch-ref-selector",
  "04A18": "candidate-selection-ui",
  "04A21": "manifest-review-ui",
  "04A22": "mandate-editor-ui",
};

const P03 = { apiBaseUrl: API, organizationId: ORG, projectId: PROJECT };
const R03 = `${T}/github-repositories`;
const token: Step[] = [
  { fill: ["github-credential-input", "ghp_example"] },
  { click: "github-load-repositories-button" },
];

const P04 = {
  ...P03,
  credential: "ghp_example",
  repositoryIdentity: "acme/app",
  targetApplicationConnectionId: CONNECTION,
  defaultBranch: "main",
};
const R04 = `${T}/github-branches`;
const R04R = `${T}/github-resolve-ref`;

const P18 = { ...P03, connectionId: CONNECTION, snapshotId: SNAPSHOT };
const R18 = `${T}/${CONNECTION}/snapshots/${SNAPSHOT}/candidates`;

const P21 = { ...P03, manifestId: MANIFEST };
const draftKey = `sovra:manifest-draft:${ORG}:${PROJECT}:${MANIFEST}`;

const STATES: UiState[] = [
  // ---- 04A03 repository selector
  {
    id: "04A03 idle",
    component: "04A03",
    props: P03,
    routes: [],
    ready: '[data-testid="github-credential-input"]',
  },
  {
    id: "04A03 blank token",
    component: "04A03",
    props: P03,
    routes: [],
    steps: [{ click: "github-load-repositories-button" }],
    ready: '[role="alert"]',
  },
  {
    id: "04A03 loading",
    component: "04A03",
    props: P03,
    routes: [{ method: "POST", path: R03, pending: true }],
    steps: [...token, { wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "04A03 populated + selected",
    component: "04A03",
    props: P03,
    routes: [{ method: "POST", path: R03, body: REPOS }],
    steps: [...token, { click: `github-repository-option-acme/alpha` }],
    ready: '[data-testid="github-repository-selected-notice"]',
  },
  {
    id: "04A03 unbroken 100-char repository selected",
    component: "04A03",
    props: P03,
    routes: [
      {
        method: "POST",
        path: R03,
        body: {
          ...REPOS,
          repositories: [
            { repository_identity: UNBROKEN_REPO, private: true, default_branch: "main" },
          ],
        },
      },
    ],
    steps: [...token, { click: `github-repository-option-${UNBROKEN_REPO}` }],
    ready: '[data-testid="github-repository-selected-notice"]',
  },
  {
    id: "04A03 empty",
    component: "04A03",
    props: P03,
    routes: [
      { method: "POST", path: R03, body: { ...REPOS, repositories: [], has_next_page: false } },
    ],
    steps: token,
    ready: "section h2",
  },
  {
    id: "04A03 error 503",
    component: "04A03",
    props: P03,
    routes: [{ method: "POST", path: R03, status: 503, body: {} }],
    steps: token,
    ready: '[data-testid="github-repository-error"]',
  },
  {
    id: "04A03 error network",
    component: "04A03",
    props: P03,
    routes: [{ method: "POST", path: R03, reject: true }],
    steps: token,
    ready: '[data-testid="github-repository-error"]',
  },
  // ---- 04A04 branch / ref resolver
  {
    id: "04A04 idle",
    component: "04A04",
    props: P04,
    routes: [],
    ready: '[data-testid="github-ref-input"]',
  },
  {
    id: "04A04 loading",
    component: "04A04",
    props: P04,
    routes: [{ method: "POST", path: R04, pending: true }],
    steps: [{ click: "github-load-branches-button" }, { wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "04A04 populated + resolved",
    component: "04A04",
    props: P04,
    routes: [
      { method: "POST", path: R04, body: BRANCHES },
      { method: "POST", path: R04R, body: { ref: "main", resolved_commit_sha: SHA } },
    ],
    steps: [
      { click: "github-load-branches-button" },
      { click: "github-branch-option-main" },
      { click: "github-resolve-ref-button" },
    ],
    ready: '[data-testid="github-resolved-ref-notice"]',
  },
  {
    id: "04A04 unbroken 64-char branch resolved",
    component: "04A04",
    props: P04,
    routes: [
      {
        method: "POST",
        path: R04,
        body: {
          ...BRANCHES,
          branches: [{ name: UNBROKEN_BRANCH, list_time_commit_sha: "b".repeat(40) }],
        },
      },
      { method: "POST", path: R04R, body: { ref: UNBROKEN_BRANCH, resolved_commit_sha: SHA } },
    ],
    steps: [
      { click: "github-load-branches-button" },
      { click: `github-branch-option-${UNBROKEN_BRANCH}` },
      { click: "github-resolve-ref-button" },
    ],
    ready: '[data-testid="github-resolved-ref-notice"]',
  },
  {
    id: "04A04 empty",
    component: "04A04",
    props: P04,
    routes: [
      { method: "POST", path: R04, body: { ...BRANCHES, branches: [], has_next_page: false } },
    ],
    steps: [{ click: "github-load-branches-button" }],
    ready: "section h2",
  },
  {
    id: "04A04 error 503",
    component: "04A04",
    props: P04,
    routes: [{ method: "POST", path: R04, status: 503, body: {} }],
    steps: [{ click: "github-load-branches-button" }],
    ready: '[data-testid="github-branch-ref-error"]',
  },
  {
    id: "04A04 error network",
    component: "04A04",
    props: P04,
    routes: [{ method: "POST", path: R04R, reject: true }],
    steps: [{ click: "github-resolve-ref-button" }],
    ready: '[data-testid="github-branch-ref-error"]',
  },
  // ---- 04A18 candidate selection
  {
    id: "04A18 idle",
    component: "04A18",
    props: P18,
    routes: [],
    ready: '[data-testid="candidate-load-button"]',
  },
  {
    id: "04A18 loading",
    component: "04A18",
    props: P18,
    routes: [{ method: "GET", path: R18, pending: true }],
    steps: [{ click: "candidate-load-button" }, { wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "04A18 populated",
    component: "04A18",
    props: P18,
    routes: [{ method: "GET", path: R18, body: CANDIDATES }],
    steps: [{ click: "candidate-load-button" }],
    ready: `[data-testid="candidate-row-${CAND_A}"]`,
  },
  {
    id: "04A18 confirmed",
    component: "04A18",
    props: P18,
    routes: [
      { method: "GET", path: R18, body: CANDIDATES },
      {
        method: "POST",
        path: `${R18}/${CAND_B}/select`,
        body: {
          candidate_id: CAND_B,
          target_source_snapshot_id: SNAPSHOT,
          selected_by: "user-1",
          selected_at: "2026-10-02T12:00:00+00:00",
        },
      },
    ],
    steps: [{ click: "candidate-load-button" }, { click: `candidate-select-button-${CAND_B}` }],
    ready: '[data-testid="candidate-selection-confirmation"]',
  },
  {
    id: "04A18 empty",
    component: "04A18",
    props: P18,
    routes: [{ method: "GET", path: R18, body: { ...CANDIDATES, items: [], total: 0 } }],
    steps: [{ click: "candidate-load-button" }],
    ready: "section h2",
  },
  {
    id: "04A18 error 409",
    component: "04A18",
    props: P18,
    routes: [
      { method: "GET", path: R18, body: CANDIDATES },
      {
        method: "POST",
        path: `${R18}/${CAND_A}/select`,
        status: 409,
        body: { detail: "stale_candidate" },
      },
    ],
    steps: [{ click: "candidate-load-button" }, { click: `candidate-select-button-${CAND_A}` }],
    ready: '[data-testid="candidate-selection-error"]',
  },
  {
    id: "04A18 error network",
    component: "04A18",
    props: P18,
    routes: [{ method: "GET", path: R18, reject: true }],
    steps: [{ click: "candidate-load-button" }],
    ready: '[data-testid="candidate-selection-error"]',
  },
  // ---- 04A21 manifest review
  {
    id: "04A21 loading",
    component: "04A21",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, pending: true },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, pending: true },
    ],
    steps: [{ wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "04A21 populated",
    component: "04A21",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: manifest21() },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, body: EVIDENCE },
    ],
    ready: '[data-testid="field-agent_name"]',
  },
  {
    id: "04A21 unbroken evidence path and symbol",
    component: "04A21",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: manifest21() },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, body: UNBROKEN_EVIDENCE },
    ],
    ready: '[data-testid^="evidence-location-"]',
  },
  {
    id: "04A21 invalid JSON edit",
    component: "04A21",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: manifest21() },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, body: EVIDENCE },
    ],
    steps: [{ fill: ["field-input-agent_entrypoint_json", "{ not json"] }],
    ready: '[role="alert"]',
  },
  {
    id: "04A21 conflict",
    component: "04A21",
    props: P21,
    storageItems: {
      [draftKey]: JSON.stringify({
        agent_name: { text: "my-agent-name", baseText: "an-older-server-name" },
      }),
    },
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: manifest21() },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, body: EVIDENCE },
    ],
    ready: '[data-testid="field-conflict-agent_name"]',
  },
  {
    id: "04A21 locked read-only",
    component: "04A21",
    props: P21,
    routes: [
      {
        method: "GET",
        path: MANIFEST_PATH,
        body: manifest21({ status: "locked", manifest_sha256: "f".repeat(64) }),
      },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, body: EVIDENCE },
    ],
    ready: '[data-testid="manifest-read-only"]',
  },
  {
    id: "04A21 locked realistic sha256",
    component: "04A21",
    props: P21,
    routes: [
      {
        method: "GET",
        path: MANIFEST_PATH,
        body: manifest21({
          status: "locked",
          manifest_sha256: "3a7bd3e29c41f08d6b5e17a2c94d0f83be6a1d57c20e94f7a8b3d61e05cd4f1b",
        }),
      },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, body: EVIDENCE },
    ],
    ready: '[data-testid="manifest-sha256"]',
  },
  {
    id: "04A21 error 503",
    component: "04A21",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, status: 503, body: {} },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, body: [] },
    ],
    ready: '[data-testid="manifest-load-error"]',
  },
  {
    id: "04A21 error network",
    component: "04A21",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, reject: true },
      { method: "GET", path: `${MANIFEST_PATH}/evidence`, reject: true },
    ],
    ready: '[data-testid="manifest-load-error"]',
  },
  // ---- 04A22 mandate editor
  {
    id: "04A22 loading",
    component: "04A22",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, pending: true },
      { method: "GET", path: `${P}/requirements`, pending: true },
    ],
    steps: [{ wait: 1300 }],
    ready: '[role="status"]',
  },
  {
    id: "04A22 populated empty list",
    component: "04A22",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: manifest22() },
      { method: "GET", path: `${P}/requirements`, body: [] },
    ],
    ready: '[data-testid="mandate-fields-section"]',
  },
  {
    id: "04A22 four conflict states",
    component: "04A22",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: CONFLICT_MANIFEST },
      { method: "GET", path: `${P}/requirements`, body: CONFLICT_REQUIREMENTS },
    ],
    ready: '[data-testid="forbidden-action-row-refund"]',
  },
  {
    id: "04A22 unbroken snake_case tool names",
    component: "04A22",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: UNBROKEN_MANIFEST },
      { method: "GET", path: `${P}/requirements`, body: UNBROKEN_REQUIREMENTS },
    ],
    ready: `[data-testid="mandate-lock-blockers"]`,
  },
  {
    id: "04A22 unbroken duplicate-name error",
    component: "04A22",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: UNBROKEN_MANIFEST },
      { method: "GET", path: `${P}/requirements`, body: UNBROKEN_REQUIREMENTS },
    ],
    steps: [
      { fill: ["new-forbidden-action-name", UNBROKEN_TOOL] },
      { click: "forbidden-action-add-button" },
    ],
    ready: '[id="new-forbidden-action-name-error"]',
  },
  {
    id: "04A22 dirty + discard confirm",
    component: "04A22",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, body: CONFLICT_MANIFEST },
      { method: "GET", path: `${P}/requirements`, body: CONFLICT_REQUIREMENTS },
    ],
    steps: [
      { fill: ["mandate-field-business_purpose", "Edited purpose"] },
      { click: "mandate-discard-button" },
    ],
    ready: '[data-testid="mandate-discard-confirm"]',
  },
  {
    id: "04A22 locked read-only",
    component: "04A22",
    props: P21,
    routes: [
      {
        method: "GET",
        path: MANIFEST_PATH,
        body: manifest22({
          status: "locked",
          manifest_sha256: "e".repeat(64),
          forbidden_actions_json: { actions: [{ name: "refund", reason: "policy" }] },
        }),
      },
      { method: "GET", path: `${P}/requirements`, body: [requirement()] },
    ],
    ready: '[data-testid="mandate-readonly-notice"]',
  },
  {
    id: "04A22 error 503",
    component: "04A22",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, status: 503, body: {} },
      { method: "GET", path: `${P}/requirements`, body: [] },
    ],
    ready: '[data-testid="mandate-load-error"]',
  },
  {
    id: "04A22 error network",
    component: "04A22",
    props: P21,
    routes: [
      { method: "GET", path: MANIFEST_PATH, reject: true },
      { method: "GET", path: `${P}/requirements`, reject: true },
    ],
    ready: '[data-testid="mandate-load-error"]',
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
      lib: { entry: ENTRY, name: "UX03Bundle", formats: ["iife"], fileName: "ux03" },
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
      `<title>FTF-UX03 evidence</title><style>${TOKENS_CSS}</style></head>` +
      `<body><main><div id="root"></div></main></body></html>`,
    { waitUntil: "load" },
  );
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, component, props, storageItems }) => {
      const ux = (
        window as unknown as {
          UX03: { stubFetch: (r: unknown) => void; mount: (...a: unknown[]) => void };
        }
      ).UX03;
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

async function rtlProblems(page: Page, state: UiState, locale: Locale): Promise<string[]> {
  const attrs = await page.evaluate((id) => {
    const root = document.querySelector(`[data-testid="${id}"]`);
    return root ? { dir: root.getAttribute("dir"), lang: root.getAttribute("lang") } : null;
  }, ROOT_TESTID[state.component]);
  if (!attrs) return [`rtl: component root ${ROOT_TESTID[state.component]} missing`];
  const want = { dir: locale === "ar" ? "rtl" : "ltr", lang: locale };
  const problems: string[] = [];
  if (attrs.dir !== want.dir) problems.push(`rtl: root dir=${attrs.dir}, want ${want.dir}`);
  if (attrs.lang !== want.lang) problems.push(`rtl: root lang=${attrs.lang}, want ${want.lang}`);
  if (locale === "ar") {
    const direction = await page.evaluate(
      (id) => getComputedStyle(document.querySelector(`[data-testid="${id}"]`)!).direction,
      ROOT_TESTID[state.component],
    );
    if (direction !== "rtl") problems.push(`rtl: computed direction=${direction}`);
  }
  return problems;
}

interface Stop {
  index: number;
  label: string;
  outlineStyle: string;
  outlineWidth: number;
  contrast: number;
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
      // A native control must be reachable by Tab even if a stray tabindex="-1" removed it from the
      // order; a non-native element (a focus target such as the error summary) only counts when it
      // opts in with tabindex >= 0.
      const native = el.matches("button, a[href], input, textarea, select");
      return native || el.tabIndex >= 0;
    });
    (window as unknown as { __tabList: HTMLElement[] }).__tabList = list;
    return list.length;
  });
  const problems: string[] = [];
  if (count === 0) {
    // A loading state offers nothing to operate; any other state with no control is a dead end.
    return {
      problems: noControlsExpected ? [] : ["keyboard: no focusable control in this state"],
      stops: 0,
    };
  }
  // Reset the sequential-focus starting point: a click in the state setup leaves it on that control.
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
        const parse = (c: string) => (c.match(/[\d.]+/g) ?? []).map(Number);
        const lum = ([r, g, b]: number[]) => {
          const f = (v: number) => {
            const s = (v ?? 0) / 255;
            return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
          };
          return 0.2126 * f(r!) + 0.7152 * f(g!) + 0.0722 * f(b!);
        };
        let bg: number[] = parse(getComputedStyle(document.body).backgroundColor);
        for (let n: HTMLElement | null = el.parentElement; n; n = n.parentElement) {
          const c = parse(getComputedStyle(n).backgroundColor);
          if ((c[3] ?? 1) > 0) {
            bg = c;
            break;
          }
        }
        const fg = parse(cs.outlineColor);
        const l1 = lum(fg);
        const l2 = lum(bg);
        const contrast = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        return {
          index,
          label: `${el.tagName.toLowerCase()}${el.getAttribute("data-testid") ? `[${el.getAttribute("data-testid")}]` : ""}`,
          outlineStyle: cs.outlineStyle,
          outlineWidth: Number.parseFloat(cs.outlineWidth),
          contrast,
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
    } else if (stop.contrast < 3) {
      problems.push(
        `focus indicator: ${stop.label} outline contrast ${stop.contrast.toFixed(2)} < 3:1`,
      );
    }
  });
  // No focus trap: one more Tab leaves the last control.
  if (count > 1) {
    await page.keyboard.press("Tab");
    const trapped = await page.evaluate(() => {
      const list = (window as unknown as { __tabList: HTMLElement[] }).__tabList;
      return document.activeElement === list[list.length - 1];
    });
    if (trapped) problems.push("keyboard: focus is trapped on the last control");
  }
  // Enter and Space activate a Button.
  const buttonIndex = await page.evaluate(() => {
    const list = (window as unknown as { __tabList: HTMLElement[] }).__tabList;
    return list.findIndex((el) => el.tagName === "BUTTON" && el.getAttribute("type") !== "submit");
  });
  if (buttonIndex >= 0) {
    await page.evaluate((i) => {
      const w = window as unknown as { __tabList: HTMLElement[]; __clicks: number };
      w.__clicks = 0;
      const target = w.__tabList[i]!;
      document.addEventListener(
        "click",
        (e) => {
          if (e.target instanceof Node && target.contains(e.target)) {
            w.__clicks += 1;
            e.preventDefault();
            e.stopPropagation();
          }
        },
        true,
      );
      target.focus();
    }, buttonIndex);
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");
    const clicks = await page.evaluate(() => (window as unknown as { __clicks: number }).__clicks);
    if (clicks !== 2)
      problems.push(`keyboard: Enter+Space activated the button ${clicks}x, want 2`);
  }
  return { problems, stops: count };
}

// ---------------------------------------------------------------------------------------------
// The spec
// ---------------------------------------------------------------------------------------------
const SUMMARY: { state: string; locale: Locale; scheme: Scheme; stops: number }[] = [];

describe.concurrent(
  "[UX03-BROWSER] real-Chromium §72.5 evidence for the five target-application components",
  () => {
    for (const state of STATES) {
      for (const locale of ["en", "ar"] as Locale[]) {
        for (const scheme of ["light", "dark"] as Scheme[]) {
          test(`[${state.id}] ${locale} ${scheme}: axe, viewports, zoom, keyboard, focus indicator, rtl`, async () => {
            const page = await openState(state, locale, scheme);
            try {
              const problems: string[] = [];
              problems.push(...(await axeProblems(page)));
              problems.push(...(await rtlProblems(page, state, locale)));
              const keyboard = await keyboardProblems(page, state.id.includes("loading"));
              problems.push(...keyboard.problems);
              problems.push(...(await overflowProblems(page)));
              SUMMARY.push({ state: state.id, locale, scheme, stops: keyboard.stops });
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

test("[UX03-BROWSER] every one of the five components is exercised in at least five states", () => {
  const per = new Map<string, number>();
  for (const s of STATES) per.set(s.component, (per.get(s.component) ?? 0) + 1);
  expect(Array.from(per.keys()).sort()).toEqual(["04A03", "04A04", "04A18", "04A21", "04A22"]);
  for (const [component, n] of per) expect(n, component).toBeGreaterThanOrEqual(5);
  for (const kind of ["loading", "error"]) {
    for (const component of per.keys()) {
      expect(
        STATES.some((s) => s.component === component && s.id.includes(kind)),
        `${component} has a ${kind} state`,
      ).toBe(true);
    }
  }
});
