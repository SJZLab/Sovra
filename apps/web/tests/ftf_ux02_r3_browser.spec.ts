// FTF-UX02 round 3 (QUEUE 0k points 1, 3, 4, 5, 2): real-Chromium §72.5 evidence for ALL THREE
// dataset-factory components (FTF-0500A, FTF-0501-UI, FTF-0516) and the shared UX01 Dialog.
//  * Dialog geometry (point 1): the dialog and every button are inside the viewport and hit-testable,
//    en and ar, at 1280/768/390/320 and a 200 % equivalent (640 CSS px).
//  * Long names (point 3): 100-character unbroken, snake_case and sha256 fixtures never overflow at
//    390, 320 and 200 %, in either direction (an RTL overflow is not a scrollbar, so every element's
//    rect is checked against the viewport, not only scrollWidth).
//  * Bilingual + UX01 (point 4): root lang/dir, no untranslated English word outside data tokens in
//    an Arabic render, 24 px minimum targets (WCAG 2.5.8), axe 0 in light and dark.
//  * Every state x en/ar x light/dark (point 5) at 1280/768/390/320 and 200 %, keyboard-only with a
//    visible outline in both locales.
//  * Malformed replies (point 2): a crash/unmount would fail "still mounted" in a real browser.
// Same harness pattern as ftf_ux02_browser.spec.ts / browser/ux02_r3_entry.tsx.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import { draftStorageKey } from "../components/dataset-factory/resumable_dataset_upload_ui";
import {
  finalizeUrl,
  partUrlsUrl,
  partsUrl,
  sessionUrl,
  sessionsUrl,
} from "../components/dataset-factory/resumable_dataset_upload_ui";
import type { Route } from "./browser/ux02_r3_entry";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");
const ENTRY = resolve(__dirname, "browser/ux02_r3_entry.tsx");

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const CONNECTION = "33333333-3333-3333-3333-333333333333";
const SESSION = "44444444-4444-4444-4444-444444444444";
const DATASET = "55555555-5555-5555-5555-555555555555";
const REPORT = "66666666-6666-6666-6666-666666666666";

const pathOf = (url: string) => new URL(url).pathname;
const P = `/api/v1/organizations/${ORG}/projects/${PROJECT}`;
const SOURCES = `${P}/dataset-sources`;

// ---- long-name fixtures (point 3) ---------------------------------------------------------------
const LONG_UNBROKEN = "A".repeat(100);
const LONG_SNAKE = "support_tickets_export_2026_q3_final_reviewed_v2_".repeat(2).slice(0, 100);
const LONG_SHA = `${"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}.jsonl`;

function conn(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    organization_id: ORG,
    project_id: PROJECT,
    source_type: "s3",
    display_name: name,
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
    ...extra,
  };
}
const CONN_A = conn(CONNECTION, "Support tickets export");
const CONN_LONG = [
  conn("a0000000-0000-0000-0000-000000000001", LONG_UNBROKEN),
  conn("a0000000-0000-0000-0000-000000000002", LONG_SNAKE),
  conn("a0000000-0000-0000-0000-000000000003", LONG_SHA),
];

// ---- 0501-UI fixtures ---------------------------------------------------------------------------
const FILE_BYTES = [10, 20, 30, 40, 50];
const HASH = "6ef53a6408ec4429791fa808e4fbe7843f936316950a20ba5fbbbdd01cd29899";
const SESSIONS_P = pathOf(sessionsUrl(API, ORG, PROJECT));
const SESSION_P = pathOf(sessionUrl(API, ORG, PROJECT, SESSION));
const PARTS_P = pathOf(partsUrl(API, ORG, PROJECT, SESSION));
const PART_URLS_P = pathOf(partUrlsUrl(API, ORG, PROJECT, SESSION));
const FINALIZE_P = pathOf(finalizeUrl(API, ORG, PROJECT, SESSION));
const sessionBody = {
  id: SESSION,
  project_id: PROJECT,
  dataset_source_connection_id: CONNECTION,
  purpose: "training",
  status: "open",
  declared_size_bytes: 5,
  declared_sha256: HASH,
  declared_content_type: "application/json",
  rejection_reason: null,
  created_at: "2026-10-09T00:00:00+00:00",
  expires_at: "2026-10-09T01:00:00+00:00",
  finalized_at: null,
  part_size_bytes: 3,
  part_count: 2,
};
const grantsBody = {
  parts: [1, 2].map((n) => ({
    part_number: n,
    method: "PUT",
    url: `https://storage.example/part${n}`,
    content_length: n === 1 ? 3 : 2,
    expires_at: "2026-10-09T00:05:00+00:00",
  })),
};
const datasetBody = {
  id: DATASET,
  organization_id: ORG,
  project_id: PROJECT,
  name: "my-dataset",
  version: 1,
  purpose: "training",
  upload_session_id: SESSION,
  verified_sha256: HASH,
  verified_size_bytes: 5,
  created_by: "user:jane",
  created_at: "2026-10-09T00:02:00+00:00",
};
const UPLOAD_BASE: Route[] = [
  { method: "POST", path: SESSIONS_P, status: 201, body: sessionBody },
  { method: "GET", path: PARTS_P, body: { part_size_bytes: 3, part_count: 2, parts: [] } },
  { method: "POST", path: PART_URLS_P, body: grantsBody },
];
const PUTS_OK: Route[] = [
  { method: "PUT", path: "/part1", body: {} },
  { method: "PUT", path: "/part2", body: {} },
];
const DRAFT_KEY = draftStorageKey(ORG, PROJECT, CONNECTION);
const draft = (fileName: string) =>
  JSON.stringify({
    organizationId: ORG,
    projectId: PROJECT,
    datasetSourceConnectionId: CONNECTION,
    uploadSessionId: SESSION,
    purpose: "training",
    declaredSizeBytes: 5,
    declaredSha256: HASH,
    declaredContentType: "application/json",
    fileName,
  });

// ---- 0516 fixtures ------------------------------------------------------------------------------
const sha = (c: string) => c.repeat(64);
const ISSUE_OPEN = sha("a");
const ISSUE_WARN = sha("b");
const ISSUE_FAIL = sha("c");
const ISSUE_DONE = sha("d");
const EVIDENCE = sha("e");
const AFTER = sha("f");
const DS = `${P}/datasets/${DATASET}`;
const report = (extra: Record<string, unknown> = {}) => ({
  id: REPORT,
  dataset_id: DATASET,
  dataset_version: 2,
  dataset_sha256: sha("1"),
  report_seq: 1,
  report_sha256: sha("2"),
  detector_versions: { pii_secrets: "1" },
  detectors_reported: ["pii_secrets"],
  issue_count: 4,
  blocking_count: 2,
  warning_count: 2,
  resolution_version: 3,
  is_clean: false,
  blocks_split: true,
  blocks_training: true,
  residual_risk_disclosure: "A clean report is not proof the data has no personal data.",
  created_at: "2026-10-09T08:00:00+00:00",
  updated_at: "2026-10-09T08:00:00+00:00",
  ...extra,
});
const resolution = (extra: Record<string, unknown> = {}) => ({
  id: REPORT,
  quality_report_id: REPORT,
  issue_id: ISSUE_DONE,
  action: "exclude",
  engine_decision: "resolved",
  excluded_batch_positions: [1],
  successor_dataset_id: null,
  before_evidence_sha256: EVIDENCE,
  after_evidence_sha256: AFTER,
  reason: "removed the leaked row",
  actor_id: "user-1",
  actor_role: "ml_engineer",
  resolution_version_after: 3,
  created_at: "2026-10-09T09:00:00+00:00",
  ...extra,
});
const issue = (id: string, extra: Record<string, unknown> = {}) => ({
  issue_id: id,
  report_id: REPORT,
  detector: "pii_secrets",
  category: "pii_secrets",
  severity: "blocking",
  reason_code: "pii_detected",
  message_sha256: sha("9"),
  batch_positions: [0, 2, 5],
  record_indices: [10, null, 12],
  evidence_digest: null,
  is_detector_failure: false,
  evidence_sha256: EVIDENCE,
  state: "open",
  resolution: null,
  ...extra,
});
const ISSUES = [
  issue(ISSUE_OPEN),
  issue(ISSUE_WARN, { severity: "warning" }),
  issue(ISSUE_FAIL, { is_detector_failure: true, category: "detector_failure" }),
  issue(ISSUE_DONE, { state: "resolved", resolution: resolution() }),
];
const LONG_CODE = "pii_detected_in_customer_support_ticket_free_text_field_"
  .repeat(2)
  .slice(0, 100);
const LONG_ISSUES = [
  issue(ISSUE_OPEN, { reason_code: LONG_CODE, detector: LONG_UNBROKEN, category: LONG_SNAKE }),
  issue(ISSUE_DONE, {
    state: "resolved",
    resolution: resolution({
      actor_id: LONG_SNAKE,
      reason: "r".repeat(400),
      before_evidence_sha256: EVIDENCE,
    }),
  }),
];
const QR = { method: "GET", path: `${DS}/quality-report` } as const;
const ISS = { method: "GET", path: `${DS}/issues` } as const;
const RES = { method: "GET", path: `${DS}/issue-resolutions` } as const;
const R516: Route[] = [
  { ...QR, body: report() },
  { ...ISS, body: { items: ISSUES, next_after: null } },
  { ...RES, body: { items: [resolution()] } },
];
const posted = (status = 201, body: unknown = undefined): Route => ({
  method: "POST",
  path: `${DS}/resolve-issue`,
  status,
  body: body ?? {
    quality_report_id: REPORT,
    resolution_version: 4,
    replayed: false,
    items: [
      resolution({
        issue_id: ISSUE_OPEN,
        excluded_batch_positions: [0],
        reason: "because",
        resolution_version_after: 4,
      }),
    ],
  },
});

// ---- state table --------------------------------------------------------------------------------
type Step =
  | { click: string }
  | { wait: number }
  | { select: [string, string] }
  | { fill: [string, string] }
  | { files: [string, string, number[]] };

interface UiState {
  id: string;
  component: "0500A" | "0501-UI" | "0516" | "Dialog";
  props: Record<string, unknown>;
  routes: Route[];
  storageItems?: Record<string, string>;
  steps?: Step[];
  ready: string;
  noControlsExpected?: boolean;
}

const P0500A = { apiBaseUrl: API, organizationId: ORG, projectId: PROJECT };
const P0501 = { ...P0500A, datasetSourceConnectionId: CONNECTION };
const P0516 = { ...P0500A, datasetId: DATASET };
const LOAD = { click: "load-report-button" } as const;
const startUpload: Step[] = [
  { files: ["upload-file-input", "data.jsonl", FILE_BYTES] },
  { click: "upload-start-button" },
];
const finalizeAs = (name: string): Step[] => [
  ...startUpload,
  { fill: ["upload-dataset-name-input", name] },
  { click: "upload-finalize-button" },
];
const READY_FINALIZE = '[data-testid="upload-all-parts-uploaded"]';

const STATES: UiState[] = [
  // ---- FTF-0500A ----
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
    id: "0500A long names",
    component: "0500A",
    props: P0500A,
    routes: [{ method: "GET", path: SOURCES, body: CONN_LONG }],
    ready: `[data-testid="dataset-source-option-${CONN_LONG[0]!.id}"]`,
  },
  {
    id: "0500A list error",
    component: "0500A",
    props: P0500A,
    routes: [{ method: "GET", path: SOURCES, status: 500, body: { detail: "internal error" } }],
    ready: '[data-testid="dataset-source-list-error"]',
  },
  {
    id: "0500A malformed list",
    component: "0500A",
    props: P0500A,
    routes: [{ method: "GET", path: SOURCES, body: [null] }],
    ready: '[data-testid="dataset-source-list-error"]',
  },
  {
    id: "0500A authorized",
    component: "0500A",
    props: P0500A,
    routes: [
      { method: "GET", path: SOURCES, body: [CONN_A] },
      {
        method: "POST",
        path: `${SOURCES}/${CONNECTION}/authorize`,
        body: { authorized: true, purpose: "training" },
      },
    ],
    steps: [
      { click: `dataset-source-option-${CONNECTION}` },
      { select: ["dataset-source-purpose-to-check-select", "training"] },
      { click: "dataset-source-check-authorization-button" },
    ],
    ready: '[data-testid="dataset-source-authorization-result-authorized"]',
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
    id: "0500A malformed authorize",
    component: "0500A",
    props: P0500A,
    routes: [
      { method: "GET", path: SOURCES, body: [CONN_A] },
      { method: "POST", path: `${SOURCES}/${CONNECTION}/authorize`, body: {} },
    ],
    steps: [
      { click: `dataset-source-option-${CONNECTION}` },
      { select: ["dataset-source-purpose-to-check-select", "training"] },
      { click: "dataset-source-check-authorization-button" },
    ],
    ready: '[data-testid="dataset-source-authorization-error"]',
  },
  {
    id: "0500A preview",
    component: "0500A",
    props: P0500A,
    routes: [{ method: "GET", path: SOURCES, body: [] }],
    steps: [
      { fill: ["dataset-source-display-name-input", LONG_UNBROKEN] },
      { click: "dataset-source-purpose-training" },
      { select: ["dataset-source-security-classification-select", "internal"] },
      { click: "dataset-source-review-button" },
    ],
    ready: '[data-testid="dataset-source-preview"]',
  },
  {
    id: "0500A malformed create",
    component: "0500A",
    props: P0500A,
    routes: [
      { method: "GET", path: SOURCES, body: [] },
      { method: "POST", path: SOURCES, status: 201, body: null },
    ],
    steps: [
      { fill: ["dataset-source-display-name-input", "New one"] },
      { click: "dataset-source-purpose-training" },
      { select: ["dataset-source-security-classification-select", "internal"] },
      { click: "dataset-source-review-button" },
      { click: "dataset-source-confirm-button" },
    ],
    ready: '[data-testid="dataset-source-create-error"]',
  },
  // ---- FTF-0501-UI ----
  {
    id: "0501-UI idle",
    component: "0501-UI",
    props: P0501,
    routes: [],
    ready: '[data-testid="upload-form"]',
  },
  {
    id: "0501-UI create error",
    component: "0501-UI",
    props: P0501,
    routes: [{ method: "POST", path: SESSIONS_P, status: 500, body: {} }],
    steps: startUpload,
    ready: '[role="alert"]',
  },
  {
    id: "0501-UI uploading",
    component: "0501-UI",
    props: P0501,
    routes: [...UPLOAD_BASE, { method: "PUT", path: "/part1", pending: true }],
    steps: startUpload,
    ready: '[data-testid="upload-progress"]',
    // A transfer in flight: the progress indicator is the whole screen (no abort route exists).
    noControlsExpected: true,
  },
  {
    id: "0501-UI interrupted",
    component: "0501-UI",
    props: P0501,
    routes: [...UPLOAD_BASE, { method: "PUT", path: "/part1", status: 403, body: {} }],
    steps: startUpload,
    ready: '[data-testid="upload-interrupted"]',
  },
  {
    id: "0501-UI ready to finalize",
    component: "0501-UI",
    props: P0501,
    routes: [...UPLOAD_BASE, ...PUTS_OK],
    steps: startUpload,
    ready: READY_FINALIZE,
  },
  {
    id: "0501-UI accepted",
    component: "0501-UI",
    props: P0501,
    routes: [
      ...UPLOAD_BASE,
      ...PUTS_OK,
      { method: "POST", path: FINALIZE_P, status: 201, body: datasetBody },
    ],
    steps: finalizeAs("my-dataset"),
    ready: '[data-testid="upload-accepted"]',
  },
  {
    id: "0501-UI name conflict",
    component: "0501-UI",
    props: P0501,
    routes: [
      ...UPLOAD_BASE,
      ...PUTS_OK,
      {
        method: "POST",
        path: FINALIZE_P,
        status: 409,
        body: { detail: "dataset_version_conflict" },
      },
    ],
    steps: finalizeAs(LONG_SNAKE),
    ready: '[data-testid="upload-conflict-text"]',
  },
  {
    id: "0501-UI expired",
    component: "0501-UI",
    props: P0501,
    routes: [
      ...UPLOAD_BASE,
      ...PUTS_OK,
      { method: "POST", path: FINALIZE_P, status: 410, body: { detail: "upload_expired" } },
    ],
    steps: finalizeAs("my-dataset"),
    ready: '[data-testid="upload-expired-text"]',
  },
  {
    id: "0501-UI storage unavailable",
    component: "0501-UI",
    props: P0501,
    routes: [
      ...UPLOAD_BASE,
      ...PUTS_OK,
      { method: "POST", path: FINALIZE_P, status: 503, body: { detail: "storage_unavailable" } },
    ],
    steps: finalizeAs("my-dataset"),
    ready: '[data-testid="upload-storage-unavailable-text"]',
  },
  {
    id: "0501-UI malformed finalize",
    component: "0501-UI",
    props: P0501,
    routes: [
      ...UPLOAD_BASE,
      ...PUTS_OK,
      { method: "POST", path: FINALIZE_P, status: 201, body: {} },
    ],
    steps: finalizeAs("my-dataset"),
    ready: '[data-testid="upload-malformed"]',
  },
  {
    id: "0501-UI resume banner long filename",
    component: "0501-UI",
    props: P0501,
    routes: [{ method: "GET", path: SESSION_P, body: sessionBody }],
    storageItems: { [DRAFT_KEY]: draft(LONG_SHA) },
    ready: '[data-testid="upload-resume-banner"]',
  },
  {
    id: "0501-UI resume wrong file",
    component: "0501-UI",
    props: P0501,
    routes: [{ method: "GET", path: SESSION_P, body: sessionBody }],
    storageItems: { [DRAFT_KEY]: draft("data.jsonl") },
    steps: [{ files: ["upload-resume-file-input", LONG_UNBROKEN, [9, 9, 9, 9, 9]] }],
    ready: '[data-testid="upload-resume-error"]',
  },
  {
    id: "0501-UI abandon dialog",
    component: "0501-UI",
    props: P0501,
    routes: [],
    storageItems: { [DRAFT_KEY]: draft("data.jsonl") },
    steps: [{ click: "upload-abandon-draft-button" }],
    ready: '[data-testid="upload-abandon-confirm"]',
  },
  {
    id: "0500A created",
    component: "0500A",
    props: P0500A,
    routes: [
      { method: "GET", path: SOURCES, body: [] },
      { method: "POST", path: SOURCES, status: 201, body: conn(CONNECTION, "Fresh one") },
    ],
    steps: [
      { fill: ["dataset-source-display-name-input", "Fresh one"] },
      { click: "dataset-source-purpose-training" },
      { select: ["dataset-source-security-classification-select", "internal"] },
      { click: "dataset-source-review-button" },
      { click: "dataset-source-confirm-button" },
    ],
    ready: '[data-testid="dataset-source-created-status"]',
  },
  {
    id: "0501-UI already verified",
    component: "0501-UI",
    props: P0501,
    routes: [{ method: "GET", path: SESSION_P, body: { ...sessionBody, status: "verified" } }],
    storageItems: { [DRAFT_KEY]: draft("data.jsonl") },
    steps: [{ files: ["upload-resume-file-input", "data.jsonl", FILE_BYTES] }],
    ready: '[data-testid="upload-already-verified-text"]',
  },
  {
    id: "0501-UI rejected",
    component: "0501-UI",
    props: P0501,
    routes: [
      {
        method: "GET",
        path: SESSION_P,
        body: { ...sessionBody, status: "rejected", rejection_reason: "size_mismatch" },
      },
    ],
    storageItems: { [DRAFT_KEY]: draft("data.jsonl") },
    steps: [{ files: ["upload-resume-file-input", "data.jsonl", FILE_BYTES] }],
    ready: '[data-testid="upload-rejected-text"]',
  },
  {
    id: "0501-UI incomplete",
    component: "0501-UI",
    props: P0501,
    routes: [
      ...UPLOAD_BASE,
      ...PUTS_OK,
      { method: "POST", path: FINALIZE_P, status: 409, body: { detail: "upload_incomplete" } },
    ],
    steps: finalizeAs("my-dataset"),
    ready: '[data-testid="upload-incomplete-text"]',
  },
  {
    id: "0501-UI finalize retry",
    component: "0501-UI",
    props: P0501,
    routes: [
      ...UPLOAD_BASE,
      ...PUTS_OK,
      { method: "POST", path: FINALIZE_P, status: 500, body: {} },
    ],
    steps: finalizeAs("my-dataset"),
    ready: '[data-testid="upload-finalize-retry-text"]',
  },
  // ---- FTF-0516 ----
  {
    id: "0516 idle",
    component: "0516",
    props: P0516,
    routes: R516,
    ready: '[data-testid="load-report-button"]',
  },
  {
    id: "0516 loading",
    component: "0516",
    props: P0516,
    routes: [{ ...QR, pending: true }, ISS, RES],
    steps: [LOAD],
    ready: '[data-testid="load-report-button"][disabled]',
    noControlsExpected: true,
  },
  {
    id: "0516 loaded",
    component: "0516",
    props: P0516,
    routes: R516,
    steps: [LOAD],
    ready: '[data-testid="report"]',
  },
  {
    id: "0516 loaded long names",
    component: "0516",
    props: P0516,
    routes: [
      { ...QR, body: report() },
      { ...ISS, body: { items: LONG_ISSUES, next_after: null } },
      { ...RES, body: { items: [resolution({ actor_id: LONG_SNAKE, reason: "r".repeat(400) })] } },
    ],
    steps: [LOAD],
    ready: '[data-testid="report"]',
  },
  {
    id: "0516 no issues",
    component: "0516",
    props: P0516,
    routes: [
      {
        ...QR,
        body: report({ issue_count: 0, blocking_count: 0, warning_count: 0, is_clean: true }),
      },
      { ...ISS, body: { items: [], next_after: null } },
      { ...RES, body: { items: [] } },
    ],
    steps: [LOAD],
    ready: '[data-testid="empty-reload-button"]',
  },
  {
    id: "0516 load error",
    component: "0516",
    props: P0516,
    routes: [{ ...QR, status: 404, body: { detail: "quality_report_not_found" } }, ISS, RES],
    steps: [LOAD],
    ready: '[data-testid="dir-error"]',
  },
  {
    id: "0516 unknown server detail",
    component: "0516",
    props: P0516,
    routes: [
      { ...QR, status: 500, body: { detail: "some English prose from the server" } },
      ISS,
      RES,
    ],
    steps: [LOAD],
    ready: '[data-testid="dir-error"]',
  },
  {
    id: "0516 malformed",
    component: "0516",
    props: P0516,
    routes: [{ ...QR, body: { nope: true } }, ISS, RES],
    steps: [LOAD],
    ready: '[data-testid="dir-error"]',
  },
  {
    id: "0516 exclude form",
    component: "0516",
    props: P0516,
    routes: R516,
    steps: [LOAD, { click: `review-${ISSUE_OPEN}` }],
    ready: '[data-testid="resolution-rationale"]',
  },
  {
    id: "0516 correct form",
    component: "0516",
    props: P0516,
    routes: R516,
    steps: [
      LOAD,
      { click: `review-${ISSUE_OPEN}` },
      { select: ["resolution-action-select", "correct"] },
      { fill: ["resolution-rationale", "r".repeat(300)] },
    ],
    ready: '[data-testid="resolution-submit"]',
  },
  {
    id: "0516 undecidable",
    component: "0516",
    props: P0516,
    routes: R516,
    steps: [LOAD, { click: `review-${ISSUE_FAIL}` }],
    ready: '[data-testid="selected-issue"]',
  },
  {
    id: "0516 recorded",
    component: "0516",
    props: P0516,
    routes: [...R516, posted()],
    steps: [
      LOAD,
      { click: `review-${ISSUE_OPEN}` },
      { click: "position-0" },
      { fill: ["resolution-rationale", "because"] },
      { click: "resolution-submit" },
    ],
    ready: '[data-testid="recorded-decision"]',
  },
  {
    id: "0516 submit refused",
    component: "0516",
    props: P0516,
    routes: [...R516, posted(409, { detail: "report_version_conflict" })],
    steps: [
      LOAD,
      { click: `review-${ISSUE_OPEN}` },
      { click: "position-0" },
      { fill: ["resolution-rationale", "because"] },
      { click: "resolution-submit" },
    ],
    ready: '[data-testid="dir-error"]',
  },
  // ---- shared UX01 Dialog ----
  {
    id: "Dialog probe",
    component: "Dialog",
    props: {},
    routes: [],
    ready: '[data-testid="probe-dialog"]',
  },
  {
    id: "Dialog probe long text",
    component: "Dialog",
    props: { longText: true },
    routes: [],
    ready: '[data-testid="probe-dialog"]',
  },
];

// ---- browser plumbing ---------------------------------------------------------------------------
type Locale = "en" | "ar";
type Scheme = "light" | "dark";
const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
  { name: "small", width: 320, height: 640 },
  { name: "200%", width: 640, height: 450 },
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
      lib: { entry: ENTRY, name: "UX02R3Bundle", formats: ["iife"], fileName: "ux02r3" },
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

async function openState(
  state: UiState,
  locale: Locale,
  scheme: Scheme,
  size: { width: number; height: number } = { width: 1280, height: 900 },
): Promise<Page> {
  const page = await browser.newPage({ colorScheme: scheme, viewport: size });
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  const dir = locale === "ar" ? "rtl" : "ltr";
  await page.route("https://ux02r3-harness.invalid/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body:
        `<!doctype html><html lang="${locale}" dir="${dir}"><head><meta charset="utf-8" />` +
        `<meta name="viewport" content="width=device-width, initial-scale=1" />` +
        `<title>FTF-UX02 r3 evidence</title><style>${TOKENS_CSS}</style></head>` +
        `<body><main><div id="root"></div></main></body></html>`,
    }),
  );
  await page.goto("https://ux02r3-harness.invalid/", { waitUntil: "load" });
  await page.addScriptTag({ content: AXE_SOURCE });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(
    ({ routes, component, props, storageItems }) => {
      const ux = (
        window as unknown as {
          UX02R3: { stubFetch: (r: unknown) => void; mount: (...a: unknown[]) => void };
        }
      ).UX02R3;
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
  try {
    for (const step of state.steps ?? []) {
      if ("wait" in step) await page.waitForTimeout(step.wait);
      else if ("select" in step)
        await page.getByTestId(step.select[0]).selectOption(step.select[1]);
      else if ("fill" in step) await page.getByTestId(step.fill[0]).fill(step.fill[1]);
      else if ("files" in step)
        await page.getByTestId(step.files[0]).setInputFiles({
          name: step.files[1],
          mimeType: "application/json",
          buffer: Buffer.from(step.files[2]),
        });
      else await page.getByTestId(step.click).click({ timeout: 10_000 });
    }
    await page.waitForSelector(state.ready, { state: "visible", timeout: 10_000 });
  } catch (error) {
    throw new Error(
      `state "${state.id}" [${locale}] never became ready: ${pageErrors.join(" | ") || String(error).slice(0, 300)}`,
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

/** Horizontal overflow, in either direction: a scrollbar (LTR) or any rendered element whose box
 * leaves the viewport (RTL overflow to the left is not scrollable, so scrollWidth alone misses it). */
async function overflowProblems(page: Page): Promise<string[]> {
  const problems: string[] = [];
  for (const vp of VIEWPORTS) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    const res = await page.evaluate(() => {
      const el = document.documentElement;
      const scroll = el.scrollWidth - el.clientWidth;
      const out: string[] = [];
      for (const node of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
        if (node.closest("option, script, style, [aria-hidden='true']")) continue;
        const r = node.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const cs = getComputedStyle(node);
        if (cs.visibility === "hidden" || cs.display === "none") continue;
        if (r.left < -1 || r.right > window.innerWidth + 1) {
          out.push(
            `${node.tagName.toLowerCase()}[${node.getAttribute("data-testid") ?? ""}] ${Math.round(r.left)}..${Math.round(r.right)}`,
          );
          if (out.length >= 3) break;
        }
      }
      return { scroll, out };
    });
    if (res.scroll > 1) problems.push(`scroll overflow ${vp.name} ${vp.width}px: ${res.scroll}px`);
    for (const o of res.out) problems.push(`off-screen ${vp.name} ${vp.width}px: ${o}`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => {
    (document.documentElement.style as unknown as { zoom: string }).zoom = "2";
  });
  const zoomed = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  if (zoomed > 1) problems.push(`scroll overflow at CSS zoom 200%: ${zoomed}px`);
  await page.evaluate(() => {
    (document.documentElement.style as unknown as { zoom: string }).zoom = "1";
  });
  return problems;
}

/** WCAG 2.5.8: every visible interactive control is at least 24 x 24 CSS px. */
async function targetProblems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bad: string[] = [];
    const nodes = document.querySelectorAll<HTMLElement>(
      "button, select, textarea, input:not([type=hidden])",
    );
    for (const el of Array.from(nodes)) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.width < 24 - 0.5 || r.height < 24 - 0.5) {
        bad.push(
          `${el.tagName.toLowerCase()}[${el.getAttribute("data-testid") ?? el.getAttribute("type") ?? ""}] ${Math.round(r.width)}x${Math.round(r.height)}`,
        );
      }
    }
    return bad.map((b) => `target < 24px: ${b}`);
  });
}

/** An Arabic render shows no untranslated English word outside data tokens (ids, digests, file
 * names, MIME types, server-supplied codes and user text), which are isolated in <bdi>/[dir=ltr]. */
async function englishInArabic(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node: Node | null;
    while ((node = walker.nextNode())) {
      // Acronyms/brand tokens that stay Latin inside Arabic prose are not untranslated English.
      const text = (node.textContent ?? "").replace(/\b(UUID|SHA|JSON|MIME|URL|CSV|MinIO)\b/g, "");
      if (!/[A-Za-z]{4,}/.test(text)) continue;
      const parent = node.parentElement;
      if (!parent) continue;
      // A textarea/input shows what the USER typed (data), not a component string.
      if (parent.closest("bdi, [dir='ltr'], code, option, textarea, input, script, style"))
        continue;
      const cs = getComputedStyle(parent);
      if (cs.display === "none" || cs.visibility === "hidden") continue;
      found.push(text.trim().slice(0, 60));
    }
    return found.map((f) => `English in Arabic render: "${f}"`);
  });
}

async function rootLangDir(page: Page, locale: Locale): Promise<string[]> {
  const dir = locale === "ar" ? "rtl" : "ltr";
  return page.evaluate(
    ({ locale, dir }) => {
      const root = document.querySelector("#root > *") as HTMLElement | null;
      if (!root) return ["no component root"];
      const out: string[] = [];
      if (root.getAttribute("lang") !== locale) out.push(`root lang=${root.getAttribute("lang")}`);
      if (root.getAttribute("dir") !== dir) out.push(`root dir=${root.getAttribute("dir")}`);
      return out;
    },
    { locale, dir },
  );
}

describe("[UX02-R3-BROWSER] every state x en/ar x light/dark in real Chromium", () => {
  for (const state of STATES) {
    for (const locale of ["en", "ar"] as Locale[]) {
      for (const scheme of ["light", "dark"] as Scheme[]) {
        test(`[${state.id}] [${locale}] [${scheme}] no overflow (320/390/768/1280/200%), 24px targets, axe clean`, async () => {
          const page = await openState(state, locale, scheme);
          try {
            const problems = [
              ...(await overflowProblems(page)),
              ...(await targetProblems(page)),
              ...(await axeProblems(page)),
              ...(state.component === "Dialog" ? [] : await rootLangDir(page, locale)),
              // The Dialog probe's title/body are English FIXTURE text in a harness page, not component strings.
              ...(locale === "ar" && state.component !== "Dialog"
                ? await englishInArabic(page)
                : []),
            ];
            expect(problems, problems.join(" | ")).toEqual([]);
          } finally {
            await page.close();
          }
        }, 40_000);
      }

      test(`[${state.id}] [${locale}] keyboard: Tab reaches every control with a visible :focus-visible outline`, async () => {
        const page = await openState(state, locale, "light");
        try {
          const count = await page.evaluate(() => {
            const visible = (el: Element) => {
              const cs = getComputedStyle(el);
              return (
                el.getClientRects().length > 0 &&
                cs.visibility !== "hidden" &&
                cs.display !== "none"
              );
            };
            return Array.from(
              document.querySelectorAll<HTMLElement>(
                "button, a[href], input, select, textarea, [tabindex='0']",
              ),
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
          const problems: string[] = [];
          for (let i = 0; i < count; i++) {
            await page.keyboard.press("Tab");
            const outline = await page.evaluate(() => {
              const el = document.activeElement as HTMLElement | null;
              if (!el || el === document.body) return null;
              const cs = getComputedStyle(el);
              return { style: cs.outlineStyle, width: cs.outlineWidth };
            });
            if (!outline || outline.style === "none" || parseFloat(outline.width) <= 0) {
              problems.push(`stop ${i}: no visible outline (${JSON.stringify(outline)})`);
            }
          }
          expect(problems, problems.join(" | ")).toEqual([]);
        } finally {
          await page.close();
        }
      }, 40_000);
    }
  }
});

// ---------------------------------------------------------------------------------------------
// Point 1: the Dialog is fully on screen and hit-testable in both directions at every width.
// ---------------------------------------------------------------------------------------------
interface DialogCase {
  label: string;
  state: string;
  dialog: string;
  buttons: string[];
}
const DIALOG_CASES: DialogCase[] = [
  {
    label: "shared Dialog probe",
    state: "Dialog probe",
    dialog: "probe-dialog",
    buttons: ["dialog-close", "probe-keep", "probe-abandon"],
  },
  {
    label: "shared Dialog probe, long text",
    state: "Dialog probe long text",
    dialog: "probe-dialog",
    buttons: ["dialog-close", "probe-keep", "probe-abandon"],
  },
  {
    label: "0501-UI abandon confirmation",
    state: "0501-UI abandon dialog",
    dialog: "upload-abandon-confirm",
    buttons: ["dialog-close", "upload-abandon-cancel-button", "upload-abandon-confirm-button"],
  },
];

describe("[UX02-R3-DIALOG] the Dialog and every button are in the viewport and hit-testable", () => {
  for (const c of DIALOG_CASES) {
    for (const locale of ["en", "ar"] as Locale[]) {
      for (const vp of VIEWPORTS) {
        test(`[${c.label}] [${locale}] [${vp.name} ${vp.width}x${vp.height}]`, async () => {
          const state = STATES.find((s) => s.id === c.state)!;
          const page = await openState(state, locale, "light", vp);
          try {
            const problems = await page.evaluate(
              ({ dialog, buttons }) => {
                const out: string[] = [];
                const within = (r: DOMRect) =>
                  r.left >= -0.5 &&
                  r.top >= -0.5 &&
                  r.right <= window.innerWidth + 0.5 &&
                  r.bottom <= window.innerHeight + 0.5;
                const dlg = document.querySelector<HTMLElement>(`[data-testid="${dialog}"]`);
                if (!dlg) return ["dialog missing"];
                const dr = dlg.getBoundingClientRect();
                if (!within(dr)) {
                  out.push(
                    `dialog box ${Math.round(dr.left)}..${Math.round(dr.right)} x ${Math.round(dr.top)}..${Math.round(dr.bottom)} leaves ${window.innerWidth}x${window.innerHeight}`,
                  );
                }
                const mid = (dr.left + dr.right) / 2;
                if (Math.abs(mid - window.innerWidth / 2) > 2) {
                  out.push(`dialog is not horizontally centred (centre ${Math.round(mid)})`);
                }
                for (const id of buttons) {
                  const btn = document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
                  if (!btn) {
                    out.push(`${id}: missing`);
                    continue;
                  }
                  btn.scrollIntoView({ block: "nearest", inline: "nearest" });
                  const r = btn.getBoundingClientRect();
                  if (!within(r))
                    out.push(
                      `${id}: ${Math.round(r.left)}..${Math.round(r.right)} outside viewport`,
                    );
                  const hit = document.elementFromPoint(
                    (r.left + r.right) / 2,
                    (r.top + r.bottom) / 2,
                  );
                  if (!hit || !(btn === hit || btn.contains(hit))) {
                    out.push(`${id}: not hit-testable (${hit?.tagName ?? "nothing"} is on top)`);
                  }
                }
                return out;
              },
              { dialog: c.dialog, buttons: c.buttons },
            );
            expect(problems, problems.join(" | ")).toEqual([]);
          } finally {
            await page.close();
          }
        }, 40_000);
      }
    }
  }

  test("[Arabic 0501-UI dialog] the real Abandon button can be clicked and abandons the draft", async () => {
    const state = STATES.find((s) => s.id === "0501-UI abandon dialog")!;
    const page = await openState(state, "ar", "light", { width: 320, height: 640 });
    try {
      await page.getByTestId("upload-abandon-confirm-button").click({ timeout: 5000 });
      await page.waitForSelector('[data-testid="upload-abandon-confirm"]', { state: "detached" });
      await page.waitForSelector('[data-testid="upload-purpose-select"]', { state: "visible" });
    } finally {
      await page.close();
    }
  });

  test("[Arabic dialog] the close control can be clicked", async () => {
    const state = STATES.find((s) => s.id === "Dialog probe")!;
    const page = await openState(state, "ar", "light", { width: 390, height: 844 });
    try {
      await page.getByTestId("dialog-close").click({ timeout: 5000 });
      await page.waitForSelector('[data-testid="probe-dialog"]', { state: "detached" });
    } finally {
      await page.close();
    }
  });

  test("[Dialog] aria-modal is set and aria-describedby does not dangle", async () => {
    const state = STATES.find((s) => s.id === "Dialog probe")!;
    const page = await openState(state, "en", "light");
    try {
      const res = await page.evaluate(() => {
        const dlg = document.querySelector<HTMLElement>('[data-testid="probe-dialog"]')!;
        const ref = (attr: string) => {
          const id = dlg.getAttribute(attr);
          return id !== null && document.getElementById(id) !== null;
        };
        return {
          modal: dlg.getAttribute("aria-modal"),
          labelled: ref("aria-labelledby"),
          described: ref("aria-describedby"),
        };
      });
      expect(res).toEqual({ modal: "true", labelled: true, described: true });
    } finally {
      await page.close();
    }
  });
});

// ---------------------------------------------------------------------------------------------
// Point 2/4: behaviours that only a real browser proves.
// ---------------------------------------------------------------------------------------------
describe("[UX02-R3-BEHAVIOUR] malformed replies, focus and the empty-state action", () => {
  test("[0500A] a null list item shows one alert and the component stays mounted (no crash)", async () => {
    const state = STATES.find((s) => s.id === "0500A malformed list")!;
    const page = await openState(state, "en", "light");
    try {
      const res = await page.evaluate(() => ({
        mounted: document.querySelector('[data-testid="dataset-source-selector"]') !== null,
        alerts: document.querySelectorAll('[role="alert"]').length,
        items: document.querySelectorAll('[data-testid^="dataset-source-option-"]').length,
      }));
      expect(res.mounted, "component must not unmount").toBe(true);
      expect(res.items).toBe(0);
      expect(res.alerts).toBeGreaterThan(0);
    } finally {
      await page.close();
    }
  });

  test("[0500A] a 201 null from create adds no connection and keeps the draft", async () => {
    const state = STATES.find((s) => s.id === "0500A malformed create")!;
    const page = await openState(state, "en", "light");
    try {
      const res = await page.evaluate(() => ({
        mounted: document.querySelector('[data-testid="dataset-source-selector"]') !== null,
        items: document.querySelectorAll('[data-testid^="dataset-source-option-"]').length,
        preview: document.querySelector('[data-testid="dataset-source-preview"]') !== null,
      }));
      expect(res).toEqual({ mounted: true, items: 0, preview: true });
    } finally {
      await page.close();
    }
  });

  test("[0500A] a malformed authorize reply is never shown as Authorized", async () => {
    const state = STATES.find((s) => s.id === "0500A malformed authorize")!;
    const page = await openState(state, "ar", "light");
    try {
      const authorized = await page
        .getByTestId("dataset-source-authorization-result-authorized")
        .count();
      expect(authorized).toBe(0);
    } finally {
      await page.close();
    }
  });

  test("[0500A] the empty state's 'New connection' action focuses the display-name field", async () => {
    const state = STATES.find((s) => s.id === "0500A empty")!;
    const page = await openState(state, "en", "light");
    try {
      await page.getByTestId("dataset-source-empty-state-action").click();
      const focused = await page.evaluate(() =>
        document.activeElement?.getAttribute("data-testid"),
      );
      expect(focused).toBe("dataset-source-display-name-input");
    } finally {
      await page.close();
    }
  });

  for (const locale of ["en", "ar"] as Locale[]) {
    test(`[0516] [${locale}] a failed load moves focus to the failure alert, never <body>`, async () => {
      const state = STATES.find((s) => s.id === "0516 load error")!;
      const page = await openState(state, locale, "light");
      try {
        const active = await page.evaluate(() => ({
          testid: document.activeElement?.getAttribute("data-testid") ?? null,
          body: document.activeElement === document.body,
        }));
        expect(active.body, "focus fell to <body>").toBe(false);
        expect(active.testid).toBe("dir-error");
      } finally {
        await page.close();
      }
    });

    test(`[0516] [${locale}] a refused decision moves focus to the alert; a recorded one to the decision`, async () => {
      for (const [id, expected] of [
        ["0516 submit refused", "dir-error"],
        ["0516 recorded", "recorded-decision"],
      ] as const) {
        const state = STATES.find((s) => s.id === id)!;
        const page = await openState(state, locale, "light");
        try {
          await page.waitForTimeout(150);
          const testid = await page.evaluate(
            () => document.activeElement?.getAttribute("data-testid") ?? "<body>",
          );
          expect(testid, id).toBe(expected);
        } finally {
          await page.close();
        }
      }
    });

    test(`[0501-UI] [${locale}] a failed start moves focus to the failure alert, never <body>`, async () => {
      const state = STATES.find((s) => s.id === "0501-UI create error")!;
      const page = await openState(state, locale, "light");
      try {
        await page.waitForTimeout(150);
        const active = await page.evaluate(() => ({
          role: document.activeElement?.getAttribute("role") ?? null,
          body: document.activeElement === document.body,
        }));
        expect(active.body, "focus fell to <body>").toBe(false);
        expect(active.role).toBe("alert");
      } finally {
        await page.close();
      }
    });
  }

  test("[0501-UI] the root is a labelled, localized region (lang + dir) in Arabic", async () => {
    const state = STATES.find((s) => s.id === "0501-UI idle")!;
    const page = await openState(state, "ar", "light");
    try {
      const res = await page.evaluate(() => {
        const root = document.querySelector<HTMLElement>(
          '[data-testid="resumable-dataset-upload-ui"]',
        )!;
        return { lang: root.lang, dir: root.dir, label: root.getAttribute("aria-label") };
      });
      expect(res).toEqual({ lang: "ar", dir: "rtl", label: "رفع مجموعة بيانات قابل للاستئناف" });
    } finally {
      await page.close();
    }
  });
});

// ---------------------------------------------------------------------------------------------
// Round-3 L2 pre-review follow-ups: success focus, start-over focus, Pending pins, short viewport.
// ---------------------------------------------------------------------------------------------
describe("[UX02-R3-FOLLOWUP] focus after success, terminal screens, Pending indicators, short viewport", () => {
  const stateOf = (id: string) => STATES.find((st) => st.id === id)!;

  for (const locale of ["en", "ar"] as Locale[]) {
    test(`[0500A] [${locale}] a created connection takes focus and is announced (role=status), never <body>`, async () => {
      const page = await openState(stateOf("0500A created"), locale, "light");
      try {
        await page.waitForTimeout(100);
        const res = await page.evaluate(() => ({
          focus: document.activeElement?.getAttribute("data-testid") ?? "<body>",
          status:
            document.querySelector('[role="status"][data-testid="dataset-source-created-status"]')
              ?.textContent ?? "",
        }));
        expect(res.focus).toBe("dataset-source-option-33333333-3333-3333-3333-333333333333");
        expect(res.status.length).toBeGreaterThan(10);
      } finally {
        await page.close();
      }
    });

    for (const id of [
      "0501-UI expired",
      "0501-UI rejected",
      "0501-UI accepted",
      "0501-UI already verified",
    ]) {
      test(`[${id}] [${locale}] the way forward is a button; using it focuses the fresh form, never <body>`, async () => {
        const page = await openState(stateOf(id), locale, "light", { width: 320, height: 640 });
        try {
          await page.getByTestId("upload-new-upload-button").focus();
          await page.keyboard.press("Enter");
          await page.waitForSelector('[data-testid="upload-form"]', { state: "visible" });
          await page.waitForTimeout(100);
          const focus = await page.evaluate(
            () => document.activeElement?.getAttribute("data-testid") ?? "<body>",
          );
          expect(focus).toBe("upload-purpose-select");
        } finally {
          await page.close();
        }
      });
    }
  }

  test("[0501-UI uploading] the progress indicator is a determinate progressbar inside a status region", async () => {
    const page = await openState(stateOf("0501-UI uploading"), "en", "light");
    try {
      const res = await page.evaluate(() => {
        const bar = document.querySelector('[role="progressbar"]');
        return {
          max: bar?.getAttribute("aria-valuemax") ?? null,
          now: bar?.getAttribute("aria-valuenow") ?? null,
          inStatus: bar?.closest('[role="status"]') !== null && bar !== null,
        };
      });
      expect(res).toEqual({ max: "100", now: "0", inStatus: true });
    } finally {
      await page.close();
    }
  });

  test("[0516 loading] after one second a busy indicator (role=status) names what is loading", async () => {
    const page = await openState(stateOf("0516 loading"), "en", "light");
    try {
      await page.waitForSelector('[role="status"][aria-busy="true"]', { timeout: 4000 });
      const text = await page.locator('[role="status"][aria-busy="true"]').first().textContent();
      expect(text).toContain("Loading the quality report");
    } finally {
      await page.close();
    }
  });

  test("[Dialog] on a 320x200 viewport with a long body the dialog stays inside the viewport and scrolls", async () => {
    const page = await openState(stateOf("Dialog probe long text"), "en", "light", {
      width: 320,
      height: 200,
    });
    try {
      const res = await page.evaluate(() => {
        const dlg = document.querySelector<HTMLElement>('[data-testid="probe-dialog"]')!;
        const r = dlg.getBoundingClientRect();
        return {
          inside: r.top >= -0.5 && r.bottom <= window.innerHeight + 0.5,
          scrolls: getComputedStyle(dlg).overflowY === "auto",
        };
      });
      expect(res).toEqual({ inside: true, scrolls: true });
    } finally {
      await page.close();
    }
  });
});
