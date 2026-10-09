// @vitest-environment jsdom
//
// FTF-0516 -- Dataset issue resolution UI.
//
// A real component/DOM-level test (Vitest + React Testing Library + jsdom): renders the actual
// `DatasetIssueResolutionUI`, drives real clicks/typing and asserts on the exact `fetch` calls
// (method, URL, credentials, body) made against the frozen FTF-0516-P1 routes. `fetch` is mocked
// (no live server in a unit test). Disclosed: component-level, not a browser E2E -- the repository
// has no Next.js `app/` page tree yet (FTF-H029); the component is UNWIRED to a page.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  DatasetIssueResolutionUI,
  allowedActions,
  issuesUrl,
  qualityReportUrl,
  resolutionsUrl,
  resolveUrl,
  validReason,
} from "../components/dataset-factory/dataset_issue_resolution_ui";

const API_BASE = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const DATASET = "33333333-3333-3333-3333-333333333333";
const REPORT = "44444444-4444-4444-4444-444444444444";
const SUCCESSOR = "55555555-5555-5555-5555-555555555555";
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

type Handler = (url: string, init: RequestInit) => Promise<unknown>;
type Reply = { ok: boolean; status: number; body: unknown } | "network";
let replies: Record<string, Reply>;
let calls: Array<{ url: string; init: RequestInit }>;

function defaults(): Record<string, Reply> {
  return {
    "GET quality-report": { ok: true, status: 200, body: report() },
    "GET issues": { ok: true, status: 200, body: { items: ISSUES, next_after: null } },
    "GET issue-resolutions": { ok: true, status: 200, body: { items: [resolution()] } },
    "POST resolve-issue": {
      ok: true,
      status: 201,
      body: {
        quality_report_id: REPORT,
        resolution_version: 4,
        replayed: false,
        items: [resolution({ issue_id: ISSUE_OPEN, excluded_batch_positions: [0, 5] })],
      },
    },
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
function posts() {
  return calls.filter((c) => c.init.method === "POST");
}
function sentBody(index = 0): Record<string, unknown> {
  return JSON.parse(posts()[index]?.init.body as string) as Record<string, unknown>;
}
async function submit() {
  await act(async () => {
    fireEvent.click(submitButton());
  });
  await waitFor(() => expect(screen.queryByText("Submitting...")).toBeNull());
}

test("FTF-0516-A1 load: exact GETs with credentials, report, issues, residual risk and decisions shown", async () => {
  await loaded();
  expect(calls.map((c) => `${c.init.method} ${c.url}`).sort()).toEqual(
    [
      `GET ${qualityReportUrl(BASE)}`,
      `GET ${issuesUrl(BASE, null)}`,
      `GET ${resolutionsUrl(BASE)}`,
    ].sort(),
  );
  expect(calls.every((c) => c.init.credentials === "include")).toBe(true);
  expect(screen.getByTestId("report").textContent).toContain("3"); // resolution version
  expect(screen.getByTestId("residual-risk").textContent).toContain("not proof");
  expect(screen.getAllByText(/blocking pii\/personal_data: pii_detected \[open\]/).length).toBe(2);
  // before/after evidence + audit actor for an already decided issue come from the server
  const decided = screen.getByTestId(`evidence-${ISSUE_DONE}`).textContent as string;
  expect(decided).toContain(EVIDENCE);
  expect(decided).toContain(AFTER);
  expect(decided).toContain("user-1 (ml_engineer)");
  expect(resolveUrl(BASE)).toBe(`${BASE}/resolve-issue`);
});

test("FTF-0516-A2 exclude: exact POST body carries the server's report, version and evidence; success shown from the response", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "  row leaks a name  ");
  fireEvent.click(screen.getByLabelText("position 5"));
  fireEvent.click(screen.getByLabelText("position 0"));
  await submit();
  expect(posts()).toHaveLength(1);
  expect(posts()[0]?.url).toBe(resolveUrl(BASE));
  expect(posts()[0]?.init.credentials).toBe("include");
  const body = sentBody();
  expect(body.quality_report_id).toBe(REPORT);
  expect(body.expected_resolution_version).toBe(3);
  expect(String(body.idempotency_key).length).toBeGreaterThan(0);
  expect(body.resolutions).toEqual([
    {
      issue_id: ISSUE_OPEN,
      action: "exclude",
      reason: "row leaks a name", // trimmed
      expected_before_evidence_sha256: EVIDENCE, // F: from the server, never invented
      excluded_batch_positions: [0, 5], // strictly increasing whatever the click order
    },
  ]);
  const status = screen.getByTestId("recorded-decision").textContent as string;
  expect(status).toContain("exclude (resolved) by user-1 as ml_engineer");
  expect(status).toContain(EVIDENCE);
  expect(status).toContain(AFTER);
  // the screen reloads so state/version come from the server
  expect(calls.filter((c) => c.init.method === "GET")).toHaveLength(6);
});

test("FTF-0516-A3 correct and new_version require a successor UUID and send exactly it", async () => {
  await loaded();
  review(ISSUE_OPEN);
  fireEvent.change(screen.getByLabelText("Action"), { target: { value: "correct" } });
  type(reasonBox(), "fixed upstream");
  expect(submitButton().disabled).toBe(true);
  expect(screen.getByText("A successor dataset id (UUID) is required.")).toBeTruthy();
  type(screen.getByLabelText("Successor dataset id"), "not-a-uuid");
  expect(submitButton().disabled).toBe(true);
  type(screen.getByLabelText("Successor dataset id"), ` ${SUCCESSOR} `);
  expect(submitButton().disabled).toBe(false);
  replies["POST resolve-issue"] = {
    ok: true,
    status: 201,
    body: {
      quality_report_id: REPORT,
      resolution_version: 4,
      replayed: false,
      items: [
        resolution({ issue_id: ISSUE_OPEN, action: "correct", successor_dataset_id: SUCCESSOR }),
      ],
    },
  };
  await submit();
  const item = (sentBody().resolutions as Array<Record<string, unknown>>)[0];
  expect(item?.action).toBe("correct");
  expect(item?.successor_dataset_id).toBe(SUCCESSOR);
  expect("excluded_batch_positions" in (item ?? {})).toBe(false);
  expect(screen.getByTestId("recorded-decision").textContent).toContain("correct");
});

test("FTF-0516-A4 a rationale is required for every action: blank, whitespace and over 2000 characters never send", async () => {
  await loaded();
  review(ISSUE_OPEN);
  fireEvent.click(screen.getByLabelText("position 0"));
  expect(submitButton().disabled).toBe(true); // empty
  expect(screen.getByText("A rationale is required.")).toBeTruthy();
  type(reasonBox(), "   \n\t ");
  expect(submitButton().disabled).toBe(true); // whitespace-only
  type(reasonBox(), "x".repeat(2001));
  expect(submitButton().disabled).toBe(true);
  expect(screen.getByText("The rationale must be at most 2000 characters.")).toBeTruthy();
  fireEvent.submit(screen.getByRole("form", { name: "Resolve issue" })); // bypass the button
  await Promise.resolve();
  expect(posts()).toHaveLength(0);
  type(reasonBox(), "x".repeat(2000));
  expect(submitButton().disabled).toBe(false);
  type(reasonBox(), "\u{1F600}".repeat(2000)); // 2000 code points = 4000 UTF-16 units
  expect(submitButton().disabled).toBe(false);
  type(reasonBox(), "\u{1F600}".repeat(2001));
  expect(submitButton().disabled).toBe(true);
  expect(validReason(" a ")).toBe("a");
  expect(validReason("")).toBeNull();
});

test("FTF-0516-A5 waive (false positive) needs a rationale and is offered for a warning only", async () => {
  await loaded();
  review(ISSUE_OPEN); // blocking
  const blocking = (screen.getByLabelText("Action") as HTMLSelectElement).options;
  expect([...blocking].map((o) => o.value)).not.toContain("waive");
  review(ISSUE_WARN);
  const select = screen.getByLabelText("Action") as HTMLSelectElement;
  expect([...select.options].map((o) => o.value)).toContain("waive");
  fireEvent.change(select, { target: { value: "waive" } });
  expect(submitButton().disabled).toBe(true);
  expect(screen.getByText("A rationale is required.")).toBeTruthy();
  type(reasonBox(), "known test fixture");
  replies["POST resolve-issue"] = {
    ok: true,
    status: 201,
    body: {
      quality_report_id: REPORT,
      resolution_version: 4,
      replayed: false,
      items: [
        resolution({
          issue_id: ISSUE_WARN,
          action: "waive",
          engine_decision: "waived",
          after_evidence_sha256: null,
        }),
      ],
    },
  };
  await submit();
  const item = (sentBody().resolutions as Array<Record<string, unknown>>)[0];
  expect(item?.action).toBe("waive");
  expect(item?.reason).toBe("known test fixture");
  expect(allowedActions(ISSUES[0] as never)).not.toContain("waive");
});

test("FTF-0516-A6 a detector failure and an already decided issue offer no decision control", async () => {
  await loaded();
  review(ISSUE_FAIL);
  expect(screen.getByText("A detector failure requires a rerun, not a decision.")).toBeTruthy();
  expect(screen.queryByLabelText("Rationale")).toBeNull();
  expect(screen.queryByRole("button", { name: /Record decision/ })).toBeNull();
  review(ISSUE_DONE);
  expect(screen.getByText(/Already resolved; a decision cannot be changed here\./)).toBeTruthy();
  expect(screen.queryByLabelText("Rationale")).toBeNull();
  expect(posts()).toHaveLength(0);
});

test("FTF-0516-A7 there is no control that edits a record or a locked dataset in place", async () => {
  await loaded();
  review(ISSUE_OPEN);
  const labels = screen.getAllByRole("button").map((b) => b.textContent);
  expect(labels.some((l) => /edit|overwrite|delete|save/i.test(l ?? ""))).toBe(false);
  // The only writes the screen can make are POST resolve-issue.
  expect(calls.every((c) => c.init.method === "GET")).toBe(true);
});

for (const [status, detail, text] of [
  [409, "report_version_conflict", /changed since this screen was loaded/],
  [409, "issue_already_decided", /already has a decision/],
  [409, "evidence_mismatch", /evidence changed/],
  [409, "report_superseded", /newer quality report/],
  [403, "waive_not_permitted", /may not waive/],
  [400, "positions_not_in_issue", /not part of this issue/],
  [404, "not_found", /was not found/],
  [409, "successor_not_revalidated", /not been revalidated/],
  [500, "boom", /boom/],
] as const) {
  test(`FTF-0516-B ${status} ${detail} is an error, never a recorded decision`, async () => {
    await loaded();
    review(ISSUE_OPEN);
    type(reasonBox(), "because");
    fireEvent.click(screen.getByLabelText("position 0"));
    replies["POST resolve-issue"] = { ok: false, status, body: { detail } };
    await submit();
    expect(screen.getByRole("alert").textContent).toMatch(text);
    expect(screen.queryByTestId("recorded-decision")).toBeNull();
    expect(calls.filter((c) => c.init.method === "GET")).toHaveLength(3); // no success reload
  });
}

test("FTF-0516-B a network failure on submit is an error, not a success", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["POST resolve-issue"] = "network";
  await submit();
  expect(screen.getByRole("alert").textContent).toContain("network error: connection reset");
  expect(screen.queryByTestId("recorded-decision")).toBeNull();
});

test("FTF-0516-C a 200 with a mismatched echo is rejected, not recorded", async () => {
  for (const patch of [
    { quality_report_id: "99999999-9999-9999-9999-999999999999" }, // other report
    { items: [resolution({ issue_id: ISSUE_WARN })] }, // other issue
    { items: [resolution({ issue_id: ISSUE_OPEN, action: "waive" })] }, // other action
    { items: [resolution({ issue_id: ISSUE_OPEN, before_evidence_sha256: sha("0") })] }, // other evidence
    { items: [] }, // no decision at all
    { items: [resolution({ issue_id: ISSUE_OPEN }), resolution({ issue_id: ISSUE_WARN })] }, // extra
  ]) {
    cleanup();
    replies = defaults();
    calls = [];
    await loaded();
    review(ISSUE_OPEN);
    type(reasonBox(), "because");
    fireEvent.click(screen.getByLabelText("position 0"));
    replies["POST resolve-issue"] = {
      ok: true,
      status: 201,
      body: {
        quality_report_id: REPORT,
        resolution_version: 4,
        replayed: false,
        items: [resolution({ issue_id: ISSUE_OPEN })],
        ...patch,
      },
    };
    await submit();
    expect(screen.getByRole("alert").textContent).toBe(
      "resolution response does not match the request",
    );
    expect(screen.queryByTestId("recorded-decision")).toBeNull();
  }
});

test("FTF-0516-D malformed bodies are a typed error and are never rendered", async () => {
  const bad: Array<[string, unknown]> = [
    ["GET quality-report", { ...report(), unexpected: "<img src=x>" }],
    ["GET quality-report", report({ is_clean: "no" })],
    ["GET quality-report", report({ resolution_version: -1 })],
    ["GET quality-report", report({ residual_risk_disclosure: "" })],
    ["GET quality-report", report({ dataset_sha256: "xyz" })],
    ["GET issues", { items: [issue(ISSUE_OPEN, { severity: "fatal" })], next_after: null }],
    [
      "GET issues",
      { items: [issue(ISSUE_OPEN, { state: "open", resolution: resolution() })], next_after: null },
    ],
    [
      "GET issues",
      { items: [issue(ISSUE_OPEN, { state: "resolved", resolution: null })], next_after: null },
    ],
    ["GET issues", { items: [issue(ISSUE_OPEN, { evidence_sha256: "short" })], next_after: null }],
    ["GET issues", { items: [issue(ISSUE_OPEN, { extra: 1 })], next_after: null }],
    ["GET issues", { items: [issue(ISSUE_OPEN, { batch_positions: [-1] })], next_after: null }],
    ["GET issues", { items: ISSUES, next_after: "nope" }],
    ["GET issues", "not an object"],
    ["GET issue-resolutions", { items: [resolution({ actor_role: "root" })] }],
    ["GET issue-resolutions", { items: [resolution({ reason: "" })] }],
    ["GET issue-resolutions", { items: [{ ...resolution(), extra: 1 }] }],
  ];
  for (const [route, body] of bad) {
    cleanup();
    replies = defaults();
    calls = [];
    replies[route] = { ok: true, status: 200, body };
    mount();
    await load();
    expect(
      screen.getByRole("alert").textContent,
      `${route} ${JSON.stringify(body).slice(0, 60)}`,
    ).toBe("malformed quality response");
    expect(screen.queryByTestId("report")).toBeNull();
    expect(document.body.textContent).not.toContain("<img");
  }
});

test("FTF-0516-D a report for another dataset or an issue of another report is an error", async () => {
  replies["GET quality-report"] = {
    ok: true,
    status: 200,
    body: report({ dataset_id: "99999999-9999-9999-9999-999999999999" }),
  };
  mount();
  await load();
  expect(screen.getByRole("alert").textContent).toBe("quality response does not match the request");
  cleanup();
  replies = defaults();
  replies["GET issues"] = {
    ok: true,
    status: 200,
    body: {
      items: [issue(ISSUE_OPEN, { report_id: "99999999-9999-9999-9999-999999999999" })],
      next_after: null,
    },
  };
  mount();
  await load();
  expect(screen.getByRole("alert").textContent).toBe("quality response does not match the request");
  expect(screen.queryByTestId("report")).toBeNull();
});

for (const [route, status, detail, text] of [
  ["GET quality-report", 404, "quality_report_not_found", /No quality report has been recorded/],
  ["GET quality-report", 403, "forbidden", /forbidden/],
  ["GET issues", 404, "not_found", /was not found/],
  ["GET issue-resolutions", 500, "oops", /oops/],
] as const) {
  test(`FTF-0516-E load ${route} ${status} is an error screen, never an empty report`, async () => {
    replies[route] = { ok: false, status, body: { detail } };
    mount();
    await load();
    expect(screen.getByRole("alert").textContent).toMatch(text);
    expect(screen.queryByTestId("report")).toBeNull();
  });
}

test("FTF-0516-E load network failure is an error", async () => {
  replies["GET issues"] = "network";
  mount();
  await load();
  expect(screen.getByRole("alert").textContent).toContain("network error: connection reset");
  expect(screen.queryByTestId("report")).toBeNull();
});

test("FTF-0516-F a stale load is ignored after the dataset prop changes", async () => {
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const real = fetch as unknown as ReturnType<typeof vi.fn>;
  const original = real.getMockImplementation();
  real.mockImplementation(async (url: string, init: RequestInit) => {
    if (url.includes(DATASET) && init.method === "GET") await gate;
    return (original as Handler)(url, init);
  });
  const view = mount();
  fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
  view.rerender(
    createElement(DatasetIssueResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      datasetId: "77777777-7777-7777-7777-777777777777",
    }),
  );
  release?.();
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(screen.queryByTestId("report")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("FTF-0516-F switching dataset clears the loaded report and any open decision form before the next render", async () => {
  const view = await loaded();
  review(ISSUE_OPEN);
  expect(screen.getByLabelText("Rationale")).toBeTruthy();
  view.rerender(
    createElement(DatasetIssueResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      datasetId: "77777777-7777-7777-7777-777777777777",
    }),
  );
  expect(screen.queryByTestId("report")).toBeNull();
  expect(screen.queryByLabelText("Rationale")).toBeNull();
  expect(screen.queryByTestId("recorded-decision")).toBeNull();
});

test("FTF-0516-F a submit response that lands after a dataset switch is ignored", async () => {
  const view = await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const real = fetch as unknown as ReturnType<typeof vi.fn>;
  const original = real.getMockImplementation();
  real.mockImplementation(async (url: string, init: RequestInit) => {
    if (init.method === "POST") await gate;
    return (original as Handler)(url, init);
  });
  await act(async () => {
    fireEvent.click(submitButton());
  });
  view.rerender(
    createElement(DatasetIssueResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      datasetId: "77777777-7777-7777-7777-777777777777",
    }),
  );
  release?.();
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(screen.queryByTestId("recorded-decision")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("FTF-0516-G idempotency: the key is reused for an identical retry and replaced when the request changes or concluded", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["POST resolve-issue"] = "network";
  await submit();
  await submit(); // identical retry
  expect(posts()).toHaveLength(2);
  expect(sentBody(1).idempotency_key).toBe(sentBody(0).idempotency_key);
  type(reasonBox(), "because, edited"); // a different request
  await submit();
  expect(sentBody(2).idempotency_key).not.toBe(sentBody(0).idempotency_key);
  // after a conclusive success the next decision gets a fresh key
  replies["POST resolve-issue"] = defaults()["POST resolve-issue"] as Reply;
  await submit();
  const concluded = sentBody(3).idempotency_key;
  review(ISSUE_OPEN);
  type(reasonBox(), "because, edited");
  fireEvent.click(screen.getByLabelText("position 0"));
  await submit();
  expect(sentBody(4).idempotency_key).not.toBe(concluded);
});

test("FTF-0516-G a key rejected as reused is dropped so the next attempt gets a new one", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["POST resolve-issue"] = {
    ok: false,
    status: 409,
    body: { detail: "idempotency_key_reused" },
  };
  await submit();
  await submit();
  expect(sentBody(1).idempotency_key).not.toBe(sentBody(0).idempotency_key);
});

test("FTF-0516-H double submit while in flight sends one request", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const real = fetch as unknown as ReturnType<typeof vi.fn>;
  const original = real.getMockImplementation();
  real.mockImplementation(async (url: string, init: RequestInit) => {
    if (init.method === "POST") await gate;
    return (original as Handler)(url, init);
  });
  await act(async () => {
    fireEvent.click(submitButton());
  });
  expect(submitButton().disabled).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "Resolve issue" }));
  fireEvent.click(submitButton());
  release?.();
  await waitFor(() => expect(posts()).toHaveLength(1));
});

test("FTF-0516-I exclude needs at least one position, and only the issue's own positions are offered", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  expect(submitButton().disabled).toBe(true);
  expect(screen.getByText("Select at least one position to exclude.")).toBeTruthy();
  expect(screen.getAllByRole("checkbox")).toHaveLength(3);
  expect(screen.queryByLabelText("position 1")).toBeNull();
});

test("FTF-0516-J paging: Load more requests the server's next_after and appends", async () => {
  replies["GET issues"] = {
    ok: true,
    status: 200,
    body: { items: [issue(ISSUE_OPEN)], next_after: ISSUE_OPEN },
  };
  await loaded();
  replies["GET issues"] = {
    ok: true,
    status: 200,
    body: { items: [issue(ISSUE_WARN, { severity: "warning" })], next_after: null },
  };
  fireEvent.click(screen.getByRole("button", { name: "Load more issues" }));
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
  expect(calls.at(-1)?.url).toBe(issuesUrl(BASE, ISSUE_OPEN));
  expect(screen.getByTestId(`issue-${ISSUE_OPEN}`)).toBeTruthy();
  expect(screen.getByTestId(`issue-${ISSUE_WARN}`)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Load more issues" })).toBeNull();
});
