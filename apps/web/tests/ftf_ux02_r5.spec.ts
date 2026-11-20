// @vitest-environment jsdom
//
// FTF-UX02 r5 -- 0516 STALE-SUBMIT-REVIEW (pre-review round 3, severity 3). Ids: [UX02-R5-*].
//
// A real component/DOM-level test (Vitest + React Testing Library + jsdom): renders the actual
// `DatasetIssueResolutionUI`, drives real clicks/typing and asserts on the exact `fetch` calls
// (method, URL, credentials, body) made against the frozen FTF-0516-P1 routes. `fetch` is mocked
// (no live server in a unit test). Disclosed: component-level, not a browser E2E -- the repository
// has no Next.js `app/` page tree yet (FTF-H029); the component is UNWIRED to a page.
import { createElement } from "react";

import { DatasetSourceSelectorPurposeAuthorizationUi } from "../components/dataset-factory/dataset_source_selector_purpose_authorization_ui";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { DatasetIssueResolutionUI } from "../components/dataset-factory/dataset_issue_resolution_ui";

const API_BASE = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const DATASET = "33333333-3333-3333-3333-333333333333";
const REPORT = "44444444-4444-4444-4444-444444444444";
const BASE = `${API_BASE}/api/v1/organizations/${ORG}/projects/${PROJECT}/datasets/${DATASET}`;
const sha = (c: string) => c.repeat(64);
const ISSUE_OPEN = sha("a");
const ISSUE_WARN = sha("b");
const ISSUE_FAIL = sha("c");
const ISSUE_DONE = sha("d");
const EVIDENCE = sha("e");
const AFTER = sha("f");

function report(extra: Record<string, unknown> = {}) {
  return {
    id: REPORT,
    dataset_id: DATASET,
    dataset_version: 2,
    dataset_sha256: sha("1"),
    report_seq: 1,
    report_sha256: sha("2"),
    detector_versions: { pii: "1" },
    detectors_reported: ["pii"],
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
  };
}

function resolution(extra: Record<string, unknown> = {}) {
  return {
    id: "66666666-6666-6666-6666-666666666666",
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
  };
}

function issue(id: string, extra: Record<string, unknown> = {}) {
  return {
    issue_id: id,
    report_id: REPORT,
    detector: "pii",
    category: "personal_data",
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
  };
}

const ISSUES = [
  issue(ISSUE_OPEN),
  issue(ISSUE_WARN, { severity: "warning" }),
  issue(ISSUE_FAIL, { is_detector_failure: true }),
  issue(ISSUE_DONE, { state: "resolved", resolution: resolution() }),
];

type Reply = { ok: boolean; status: number; body: unknown } | "network";

// The server's echo of the default exclude flow below (ISSUE_OPEN, position 0, reason "because"
// against resolution_version 3): every field matches what the UI sent, so a test overrides only
// the field it wants to differ.
function posted(
  item: Record<string, unknown> = {},
  top: Record<string, unknown> = {},
): { ok: boolean; status: number; body: { items: unknown[] } & Record<string, unknown> } {
  return {
    ok: true,
    status: 201,
    body: {
      quality_report_id: REPORT,
      resolution_version: 4,
      replayed: false,
      items: [
        resolution({
          issue_id: ISSUE_OPEN,
          action: "exclude",
          engine_decision: "resolved",
          excluded_batch_positions: [0],
          successor_dataset_id: null,
          before_evidence_sha256: EVIDENCE,
          after_evidence_sha256: AFTER,
          reason: "because",
          resolution_version_after: 4,
          ...item,
        }),
      ],
      ...top,
    },
  };
}
let replies: Record<string, Reply>;
let calls: Array<{ url: string; init: RequestInit }>;

function defaults(): Record<string, Reply> {
  return {
    "GET quality-report": { ok: true, status: 200, body: report() },
    "GET issues": { ok: true, status: 200, body: { items: ISSUES, next_after: null } },
    "GET issue-resolutions": { ok: true, status: 200, body: { items: [resolution()] } },
    "POST resolve-issue": posted(),
  };
}

function routeOf(url: string): string {
  return url.replace(BASE, "").replace(/^\//, "").split("?")[0] as string;
}

beforeEach(() => {
  replies = defaults();
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      const reply = replies[`${init.method} ${routeOf(url)}`];
      if (reply === undefined) throw new Error(`unmocked ${init.method} ${url}`);
      if (reply === "network") throw new Error("connection reset");
      return { ok: reply.ok, status: reply.status, json: async () => reply.body };
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount(overrides: Record<string, string> = {}) {
  return render(
    createElement(DatasetIssueResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      datasetId: DATASET,
      ...overrides,
    }),
  );
}

async function load() {
  fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
}

async function loaded() {
  const view = mount();
  await load();
  return view;
}

function review(issueId: string) {
  fireEvent.click(screen.getByRole("button", { name: `Review ${issueId.slice(0, 8)}` }));
}
function reasonBox() {
  return screen.getByLabelText("Rationale") as HTMLTextAreaElement;
}
function type(box: HTMLElement, value: string) {
  fireEvent.change(box, { target: { value } });
}
function submitButton() {
  return screen.getByRole("button", { name: /Record decision|Submitting/ }) as HTMLButtonElement;
}

function slowResolve() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const base = replies["POST resolve-issue"] as { ok: boolean; status: number; body: unknown };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      const reply = replies[`${init.method} ${routeOf(url)}`];
      if (reply === undefined || reply === "network") throw new Error(`unmocked ${url}`);
      if (init.method === "POST") await gate;
      return { ok: reply.ok, status: reply.status, json: async () => reply.body };
    }),
  );
  return { release, base };
}

test("[UX02-R5-1] while a decision is in flight every per-issue Review button is disabled and a click opens nothing", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  const { release } = slowResolve();
  await act(async () => {
    fireEvent.click(submitButton());
  });
  const other = screen.getByTestId(`review-${ISSUE_WARN}`) as HTMLButtonElement;
  const own = screen.getByTestId(`review-${ISSUE_OPEN}`) as HTMLButtonElement;
  expect(other.disabled).toBe(true);
  expect(own.disabled).toBe(true);
  // NEGATIVE: the real default code, the attack input present (a click on another issue mid-flight).
  fireEvent.click(other);
  expect(screen.queryByLabelText("Rationale")).not.toBeNull();
  expect((reasonBox() as HTMLTextAreaElement).value).toBe("because");
  await act(async () => {
    release();
  });
  await waitFor(() => expect(screen.queryByText("Submitting...")).toBeNull());
  // after the reply the buttons are usable again
  expect((screen.getByTestId(`review-${ISSUE_WARN}`) as HTMLButtonElement).disabled).toBe(false);
});

// [UX02-R5-2] RA10 (0500A): the list's own copy of a connection wins over the copy created here.
test("[UX02-R5-2] a created connection that the later list reply also carries shows the LIST's copy", async () => {
  const PROJ = PROJECT;
  const mk = (name: string) => ({
    id: "77777777-7777-7777-7777-777777777777",
    organization_id: ORG,
    project_id: PROJ,
    source_type: "s3",
    display_name: name,
    has_credential: true,
    has_connection_config: false,
    allowed_purposes: ["training"],
    security_classification: "internal",
    connection_status: "active",
    last_validated_at: null,
    created_by: "user-1",
    created_at: "2026-09-01T00:00:00+00:00",
    updated_at: "2026-09-01T00:00:00+00:00",
    revoked_at: null,
  });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST")
        return { ok: true, status: 201, json: async () => mk("Created copy") };
      await gate;
      return { ok: true, status: 200, json: async () => [mk("List copy")] };
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: "http://localhost:8000",
      organizationId: ORG,
      projectId: PROJ,
      locale: "en",
    }),
  );
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "Created copy" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "internal" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));
  await waitFor(() =>
    expect(
      screen.getByTestId("dataset-source-option-77777777-7777-7777-7777-777777777777"),
    ).toBeTruthy(),
  );
  await act(async () => {
    release();
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
  const option = screen.getAllByTestId(
    "dataset-source-option-77777777-7777-7777-7777-777777777777",
  );
  expect(option).toHaveLength(1);
  expect(option[0]?.textContent).toContain("List copy");
  expect(option[0]?.textContent).not.toContain("Created copy");
});

// [UX02-R5-3] F3/F10: a failure or a rejected fetch from the OLD dataset's submit never shows on the new one.
for (const mode of ["http-500", "rejected"] as const) {
  test(`[UX02-R5-3] a ${mode} submit reply for the previous dataset paints nothing after the dataset changed`, async () => {
    const view = await loaded();
    review(ISSUE_OPEN);
    type(reasonBox(), "because");
    fireEvent.click(screen.getByLabelText("position 0"));
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const NEXT = "99999999-9999-9999-9999-999999999999";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        if (init.method === "POST") {
          await gate;
          if (mode === "rejected") throw new Error("connection reset");
          return { ok: false, status: 500, json: async () => ({ detail: "boom" }) };
        }
        return { ok: true, status: 200, json: async () => ({ items: [], next_after: null, url }) };
      }),
    );
    await act(async () => {
      fireEvent.click(submitButton());
    });
    view.rerender(
      createElement(DatasetIssueResolutionUI, {
        apiBaseUrl: API_BASE,
        organizationId: ORG,
        projectId: PROJECT,
        datasetId: NEXT,
      }),
    );
    await act(async () => {
      release();
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
    expect(screen.queryByTestId("dir-error")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
}

// [UX02-R5-4] LATE-CREATE-CLOBBERS-AUTHORIZED and MALFORMED-200 (a list item without project_id).
const CONN = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id,
  organization_id: ORG,
  project_id: PROJECT,
  source_type: "s3",
  display_name: name,
  has_credential: true,
  has_connection_config: false,
  allowed_purposes: ["training"],
  security_classification: "internal",
  connection_status: "active",
  last_validated_at: null,
  created_by: "user-1",
  created_at: "2026-09-01T00:00:00+00:00",
  updated_at: "2026-09-01T00:00:00+00:00",
  revoked_at: null,
  ...extra,
});
const mount500 = () =>
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      locale: "en",
    }),
  );

test("[UX02-R5-4a] a late create reply keeps the selection and the Authorized result the user is looking at", async () => {
  const A = CONN("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "Existing A");
  const NEW = CONN("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "Fresh one");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/authorize$/.test(url))
        return {
          ok: true,
          status: 200,
          json: async () => ({ authorized: true, purpose: "training" }),
        };
      if (init?.method === "POST") {
        await gate;
        return { ok: true, status: 201, json: async () => NEW };
      }
      return { ok: true, status: 200, json: async () => [A] };
    }),
  );
  mount500();
  await waitFor(() => expect(screen.getByTestId(`dataset-source-option-${A.id}`)).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "Fresh one" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "internal" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));
  fireEvent.click(screen.getByTestId(`dataset-source-option-${A.id}`));
  fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
    target: { value: "training" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-check-authorization-button"));
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-authorization-result-authorized")).toBeTruthy(),
  );
  await act(async () => {
    release();
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
  expect(screen.getByTestId(`dataset-source-option-${NEW.id}`)).toBeTruthy();
  expect(screen.getByTestId("dataset-source-authorization-result-authorized")).toBeTruthy();
  expect(screen.getByTestId("dataset-source-authorization-selected-connection").textContent).toBe(
    "Existing A",
  );
});

test("[UX02-R5-4b] a 200 list item without project_id is a malformed reply, never an empty list", async () => {
  const bad: Record<string, unknown> = CONN("cccccccc-cccc-cccc-cccc-cccccccccccc", "No project");
  delete bad.project_id;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => [bad] })),
  );
  mount500();
  await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
  expect(screen.queryByTestId(`dataset-source-option-${bad.id}`)).toBeNull();
});
