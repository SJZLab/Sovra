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
  replies["POST resolve-issue"] = posted({
    excluded_batch_positions: [0, 5],
    reason: "row leaks a name",
  });
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
  replies["POST resolve-issue"] = posted({
    action: "correct",
    successor_dataset_id: SUCCESSOR,
    excluded_batch_positions: [],
    reason: "fixed upstream",
  });
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
  replies["POST resolve-issue"] = posted({
    issue_id: ISSUE_WARN,
    action: "waive",
    engine_decision: "waived",
    after_evidence_sha256: null,
    excluded_batch_positions: [],
    reason: "known test fixture",
  });
  await submit();
  expect(screen.getByTestId("recorded-decision").textContent).toContain("waive (waived)");
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

const OTHER_UUID = "99999999-9999-9999-9999-999999999999";

async function expectRejectedEcho(
  label: string,
  reply: Reply,
  drive: () => Promise<void> = async () => {
    review(ISSUE_OPEN);
    type(reasonBox(), "because");
    fireEvent.click(screen.getByLabelText("position 0"));
  },
) {
  cleanup();
  replies = defaults();
  calls = [];
  await loaded();
  await drive();
  replies["POST resolve-issue"] = reply;
  await submit();
  expect(screen.getByRole("alert").textContent, label).toBe(
    "resolution response does not match the request",
  );
  expect(screen.queryByTestId("recorded-decision"), label).toBeNull();
  expect(
    calls.filter((c) => c.init.method === "GET"),
    label,
  ).toHaveLength(3); // no success reload
}

test("FTF-0516-C control: the fully matching echo is recorded and the screen reloads", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  await submit();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByTestId("recorded-decision").textContent).toContain("exclude (resolved)");
  expect(calls.filter((c) => c.init.method === "GET")).toHaveLength(6);
});

test("FTF-0516-C exclude: any field of the 2xx echo that differs from what was sent is an error, never 'Decision recorded'", async () => {
  const cases: Array<[string, Reply]> = [
    ["top report", posted({}, { quality_report_id: OTHER_UUID })],
    ["item report", posted({ quality_report_id: OTHER_UUID })],
    ["issue", posted({ issue_id: ISSUE_WARN })],
    ["action", posted({ action: "waive", engine_decision: "waived", after_evidence_sha256: null })],
    ["before evidence", posted({ before_evidence_sha256: sha("0") })],
    ["reason swapped", posted({ reason: "a different rationale" })],
    ["reason untrimmed", posted({ reason: "because " })],
    ["positions other", posted({ excluded_batch_positions: [2] })],
    ["positions extra", posted({ excluded_batch_positions: [0, 5] })],
    ["positions missing", posted({ excluded_batch_positions: [] })],
    ["successor on exclude", posted({ successor_dataset_id: SUCCESSOR })],
    ["decision waived on exclude", posted({ engine_decision: "waived" })],
    ["after evidence missing", posted({ after_evidence_sha256: null })],
    ["version after is not expected+1 (low)", posted({ resolution_version_after: 3 })],
    ["version after is not expected+1 (high)", posted({ resolution_version_after: 5 })],
    ["top version low", posted({}, { resolution_version: 3 })],
    ["top version high", posted({}, { resolution_version: 5 })],
    ["no decision", posted({}, { items: [] })],
    [
      "two decisions",
      posted(
        {},
        { items: [posted().body.items[0], posted({ issue_id: ISSUE_WARN }).body.items[0]] },
      ),
    ],
  ];
  for (const [label, reply] of cases) await expectRejectedEcho(label, reply);
});

test("FTF-0516-C exclude: the echoed positions must match in order as sent (sorted, de-duplicated)", async () => {
  const drive = async () => {
    review(ISSUE_OPEN);
    type(reasonBox(), "because");
    fireEvent.click(screen.getByLabelText("position 5"));
    fireEvent.click(screen.getByLabelText("position 0"));
  };
  await expectRejectedEcho("unsorted", posted({ excluded_batch_positions: [5, 0] }), drive);
  await expectRejectedEcho(
    "same length other",
    posted({ excluded_batch_positions: [0, 2] }),
    drive,
  );
  cleanup();
  replies = defaults();
  replies["POST resolve-issue"] = posted({ excluded_batch_positions: [0, 5] });
  await loaded();
  await drive();
  await submit();
  expect(screen.getByTestId("recorded-decision")).toBeTruthy();
});

test("FTF-0516-C waive: the echo must be a waived decision with no after-evidence", async () => {
  const drive = async () => {
    review(ISSUE_WARN);
    fireEvent.change(screen.getByLabelText("Action"), { target: { value: "waive" } });
    type(reasonBox(), "known test fixture");
  };
  const ok = {
    issue_id: ISSUE_WARN,
    action: "waive",
    engine_decision: "waived",
    after_evidence_sha256: null,
    excluded_batch_positions: [],
    reason: "known test fixture",
  };
  await expectRejectedEcho("resolved", posted({ ...ok, engine_decision: "resolved" }), drive);
  await expectRejectedEcho(
    "after evidence present",
    posted({ ...ok, after_evidence_sha256: AFTER }),
    drive,
  );
  await expectRejectedEcho("positions", posted({ ...ok, excluded_batch_positions: [0] }), drive);
  await expectRejectedEcho("successor", posted({ ...ok, successor_dataset_id: SUCCESSOR }), drive);
  cleanup();
  replies = defaults();
  replies["POST resolve-issue"] = posted(ok);
  await loaded();
  await drive();
  await submit();
  expect(screen.getByTestId("recorded-decision").textContent).toContain("waive (waived)");
});

for (const action of ["correct", "new_version"] as const) {
  test(`FTF-0516-C ${action}: the echo must carry exactly the sent successor, no positions and an after-evidence`, async () => {
    const drive = async () => {
      review(ISSUE_OPEN);
      fireEvent.change(screen.getByLabelText("Action"), { target: { value: action } });
      type(reasonBox(), "because");
      type(screen.getByLabelText("Successor dataset id"), SUCCESSOR);
    };
    const ok = { action, successor_dataset_id: SUCCESSOR, excluded_batch_positions: [] };
    await expectRejectedEcho(
      "other successor",
      posted({ ...ok, successor_dataset_id: OTHER_UUID }),
      drive,
    );
    await expectRejectedEcho("no successor", posted({ ...ok, successor_dataset_id: null }), drive);
    await expectRejectedEcho("positions", posted({ ...ok, excluded_batch_positions: [0] }), drive);
    await expectRejectedEcho(
      "no after evidence",
      posted({ ...ok, after_evidence_sha256: null }),
      drive,
    );
    await expectRejectedEcho("waived", posted({ ...ok, engine_decision: "waived" }), drive);
    await expectRejectedEcho("other action", posted({ ...ok, action: "exclude" }), drive);
    cleanup();
    replies = defaults();
    replies["POST resolve-issue"] = posted(ok);
    await loaded();
    await drive();
    await submit();
    expect(screen.getByTestId("recorded-decision").textContent).toContain(`${action} (resolved)`);
  });
}

test("FTF-0516-C a replayed identical retry that echoes the original decision is recorded", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["POST resolve-issue"] = posted({}, { replayed: true });
  await submit();
  expect(screen.getByTestId("recorded-decision")).toBeTruthy();
});

test("FTF-0516-C a non-boolean replayed flag or a non-array items is malformed, not recorded", async () => {
  await expectRejectedEcho("replayed string", posted({}, { replayed: "no" }));
  await expectRejectedEcho("items object", posted({}, { items: {} }));
  await expectRejectedEcho("extra key", posted({}, { extra: 1 }));
  await expectRejectedEcho("version zero", posted({}, { resolution_version: 0 }));
  await expectRejectedEcho("version float", posted({}, { resolution_version: 4.5 }));
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
  replies["POST resolve-issue"] = posted({ reason: "because, edited" });
  await submit();
  const concluded = sentBody(3).idempotency_key;
  review(ISSUE_OPEN);
  type(reasonBox(), "because, edited");
  fireEvent.click(screen.getByLabelText("position 0"));
  await submit();
  expect(posts()).toHaveLength(5);
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

test("FTF-0516-D2 a nested decision must belong to its issue, its report and its state, with a named actor and reason", async () => {
  const decided = (extra: Record<string, unknown>, state = "resolved") =>
    issue(ISSUE_DONE, { state, resolution: resolution({ issue_id: ISSUE_DONE, ...extra }) });
  const bad: Array<[string, unknown]> = [
    ["other issue", decided({ issue_id: ISSUE_OPEN })],
    ["other report", decided({ quality_report_id: OTHER_UUID })],
    ["state resolved but decision waived", decided({ engine_decision: "waived" })],
    ["state waived but decision resolved", decided({ engine_decision: "resolved" }, "waived")],
    ["blank actor", decided({ actor_id: "   " })],
    ["empty actor", decided({ actor_id: "" })],
    ["blank reason", decided({ reason: " \n " })],
  ];
  for (const [label, item] of bad) {
    cleanup();
    replies = defaults();
    replies["GET issues"] = { ok: true, status: 200, body: { items: [item], next_after: null } };
    mount();
    await load();
    expect(screen.getByRole("alert").textContent, label).toBe("malformed quality response");
    expect(screen.queryByTestId("report"), label).toBeNull();
  }
  cleanup();
  replies = defaults();
  replies["GET issues"] = {
    ok: true,
    status: 200,
    body: { items: [decided({ engine_decision: "waived" }, "waived")], next_after: null },
  };
  mount();
  await load();
  expect(screen.getByTestId(`evidence-${ISSUE_DONE}`)).toBeTruthy();
  // the same rule holds for the recent-decisions listing
  for (const extra of [{ actor_id: " " }, { reason: "  " }]) {
    cleanup();
    replies = defaults();
    replies["GET issue-resolutions"] = {
      ok: true,
      status: 200,
      body: { items: [resolution(extra)] },
    };
    mount();
    await load();
    expect(screen.getByRole("alert").textContent).toBe("malformed quality response");
  }
});

function gateGets(match: (url: string, init: RequestInit) => boolean) {
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const real = fetch as unknown as ReturnType<typeof vi.fn>;
  const original = real.getMockImplementation();
  real.mockImplementation(async (url: string, init: RequestInit) => {
    if (match(url, init)) await gate;
    return (original as Handler)(url, init);
  });
  return () => release?.();
}

function rerenderDataset(view: ReturnType<typeof render>, datasetId: string) {
  view.rerender(
    createElement(DatasetIssueResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      datasetId,
    }),
  );
}

test("FTF-0516-H2 a submit during a reload is not possible, so Loading never sticks and Load stays usable", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  const release = gateGets((_url, init) => init.method === "GET");
  fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
  expect(screen.getByRole("button", { name: "Loading..." })).toBeTruthy();
  expect(submitButton().disabled).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "Resolve issue" })); // bypass the button
  fireEvent.click(submitButton());
  await Promise.resolve();
  expect(posts()).toHaveLength(0);
  release();
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
  expect(
    (screen.getByRole("button", { name: "Load quality report" }) as HTMLButtonElement).disabled,
  ).toBe(false);
  // once settled, the same decision can be sent
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  expect(submitButton().disabled).toBe(false);
});

test("FTF-0516-H3 load more is disabled during a submit, and a submit during load more is not possible", async () => {
  replies["GET issues"] = {
    ok: true,
    status: 200,
    body: { items: [issue(ISSUE_OPEN)], next_after: ISSUE_OPEN },
  };
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  const releaseMore = gateGets((url, init) => init.method === "GET" && url.includes("after="));
  fireEvent.click(screen.getByRole("button", { name: "Load more issues" }));
  expect(submitButton().disabled).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "Resolve issue" }));
  await Promise.resolve();
  expect(posts()).toHaveLength(0);
  releaseMore();
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
  // and the other way round: while a decision is in flight the paging button is disabled
  const releasePost = gateGets((_url, init) => init.method === "POST");
  await act(async () => {
    fireEvent.click(submitButton());
  });
  expect(
    (screen.getByRole("button", { name: "Load more issues" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Load quality report" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  releasePost();
  await waitFor(() => expect(screen.queryByText("Submitting...")).toBeNull());
});

test("FTF-0516-K the report, issues and decisions are each rendered from the server's own values", async () => {
  replies["GET quality-report"] = {
    ok: true,
    status: 200,
    body: report({
      dataset_version: 7,
      report_seq: 5,
      issue_count: 9,
      blocking_count: 4,
      warning_count: 5,
      blocks_split: false,
      blocks_training: true,
      resolution_version: 12,
    }),
  };
  replies["GET issue-resolutions"] = {
    ok: true,
    status: 200,
    body: {
      items: [
        resolution({
          id: "66666666-6666-6666-6666-666666666601",
          action: "waive",
          engine_decision: "waived",
          after_evidence_sha256: null,
          reason: "known fixture",
          actor_id: "admin-9",
          actor_role: "workspace_admin",
          created_at: "2026-10-09T10:11:12+00:00",
        }),
      ],
    },
  };
  await loaded();
  const dl = screen.getByTestId("report").textContent as string;
  expect(dl).toContain("Dataset version7");
  expect(dl).toContain(`#5 ${sha("2")}`);
  expect(dl).toContain("9 (4 blocking, 5 warning)");
  expect(dl).toContain("Blocks splitno");
  expect(dl).toContain("Blocks trainingyes");
  expect(dl).toContain("Resolution version12");
  const recent = screen.getByRole("list", { name: "Recent decisions" }).textContent as string;
  expect(recent).toContain("waive (waived)");
  expect(recent).toContain(`issue ${ISSUE_DONE}`);
  expect(recent).toContain(`before ${EVIDENCE} after not recorded`);
  expect(recent).toContain("by admin-9 (workspace_admin)");
  expect(recent).toContain("at 2026-10-09T10:11:12+00:00");
  expect(recent).toContain(": known fixture");
  cleanup();
  replies["GET quality-report"] = {
    ok: true,
    status: 200,
    body: report({ blocks_split: true, blocks_training: false }),
  };
  mount();
  await load();
  const flipped = screen.getByTestId("report").textContent as string;
  expect(flipped).toContain("Blocks splityes");
  expect(flipped).toContain("Blocks trainingno");
});

test("FTF-0516-K each issue line shows severity, detector, category, reason code, state, positions and its decision", async () => {
  replies["GET issues"] = {
    ok: true,
    status: 200,
    body: {
      items: [
        issue(ISSUE_OPEN, { batch_positions: [] }),
        issue(ISSUE_WARN, {
          severity: "warning",
          detector: "dup",
          category: "duplicates",
          reason_code: "near_duplicate",
          batch_positions: [3, 4],
        }),
        issue(ISSUE_DONE, {
          state: "waived",
          resolution: resolution({
            issue_id: ISSUE_DONE,
            engine_decision: "waived",
            action: "waive",
            after_evidence_sha256: null,
            actor_id: "admin-2",
            actor_role: "organization_admin",
            reason: "fixture row",
          }),
        }),
      ],
      next_after: null,
    },
  };
  await loaded();
  expect(screen.getByTestId(`issue-${ISSUE_OPEN}`).textContent).toBe(
    "blocking pii/personal_data: pii_detected [open] positions noneReview aaaaaaaa",
  );
  expect(screen.getByTestId(`issue-${ISSUE_WARN}`).textContent).toContain(
    "warning dup/duplicates: near_duplicate [open] positions 3, 4",
  );
  const done = screen.getByTestId(`issue-${ISSUE_DONE}`).textContent as string;
  expect(done).toContain("[waived]");
  expect(done).toContain(
    `before ${EVIDENCE} after not recorded by admin-2 (organization_admin): fixture row`,
  );
  expect(screen.queryByRole("button", { name: "Load more issues" })).toBeNull();
});

test("FTF-0516-L selecting an issue shows it and resets the draft, a prior banner and any error", async () => {
  await loaded();
  review(ISSUE_OPEN);
  expect(screen.getByTestId("selected-issue").textContent).toBe(
    `Issue ${ISSUE_OPEN} (blocking, evidence ${EVIDENCE})`,
  );
  type(reasonBox(), "half typed");
  fireEvent.click(screen.getByLabelText("position 2"));
  fireEvent.click(screen.getByLabelText("position 2")); // toggled back off
  expect((screen.getByLabelText("position 2") as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByLabelText("position 5"));
  expect((screen.getByLabelText("position 5") as HTMLInputElement).checked).toBe(true);
  review(ISSUE_WARN);
  expect(reasonBox().value).toBe("");
  expect((screen.getByLabelText("Action") as HTMLSelectElement).value).toBe("exclude");
  expect(screen.getAllByRole("checkbox").every((box) => !(box as HTMLInputElement).checked)).toBe(
    true,
  );
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["POST resolve-issue"] = {
    ok: false,
    status: 409,
    body: { detail: "issue_already_decided" },
  };
  await submit();
  expect(screen.getByRole("alert")).toBeTruthy();
  review(ISSUE_OPEN);
  expect(screen.queryByRole("alert")).toBeNull();
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["POST resolve-issue"] = posted();
  await submit();
  expect(screen.getByTestId("recorded-decision")).toBeTruthy();
  expect(screen.queryByTestId("selected-issue")).toBeNull(); // form closed, draft cleared
  review(ISSUE_OPEN);
  expect(screen.queryByTestId("recorded-decision")).toBeNull();
  expect(reasonBox().value).toBe("");
});

test("FTF-0516-L the action control swaps the fields: positions for exclude, a successor for correct/new_version, neither for waive", async () => {
  await loaded();
  review(ISSUE_WARN);
  const select = screen.getByLabelText("Action") as HTMLSelectElement;
  expect([...select.options].map((o) => o.value)).toEqual([
    "exclude",
    "correct",
    "new_version",
    "waive",
  ]);
  expect(screen.getByText("Positions to exclude")).toBeTruthy();
  expect(screen.queryByLabelText("Successor dataset id")).toBeNull();
  for (const action of ["correct", "new_version"]) {
    fireEvent.change(select, { target: { value: action } });
    expect(screen.queryByText("Positions to exclude")).toBeNull();
    expect(screen.getByLabelText("Successor dataset id")).toBeTruthy();
  }
  fireEvent.change(select, { target: { value: "waive" } });
  expect(screen.queryByText("Positions to exclude")).toBeNull();
  expect(screen.queryByLabelText("Successor dataset id")).toBeNull();
  review(ISSUE_OPEN);
  const blocking = screen.getByLabelText("Action") as HTMLSelectElement;
  expect([...blocking.options].map((o) => o.value)).toEqual(["exclude", "correct", "new_version"]);
});

test("FTF-0516-M new_version and waive send exactly their own fields", async () => {
  await loaded();
  review(ISSUE_OPEN);
  fireEvent.change(screen.getByLabelText("Action"), { target: { value: "new_version" } });
  type(reasonBox(), "rebuilt");
  type(screen.getByLabelText("Successor dataset id"), SUCCESSOR);
  replies["POST resolve-issue"] = posted({
    action: "new_version",
    successor_dataset_id: SUCCESSOR,
    excluded_batch_positions: [],
    reason: "rebuilt",
  });
  await submit();
  expect(sentBody().resolutions).toEqual([
    {
      issue_id: ISSUE_OPEN,
      action: "new_version",
      reason: "rebuilt",
      expected_before_evidence_sha256: EVIDENCE,
      successor_dataset_id: SUCCESSOR,
    },
  ]);
  expect(String(sentBody().idempotency_key)).toMatch(/^ftf-0516-/);
  expect(Object.keys(sentBody()).sort()).toEqual([
    "expected_resolution_version",
    "idempotency_key",
    "quality_report_id",
    "resolutions",
  ]);
  review(ISSUE_WARN);
  fireEvent.change(screen.getByLabelText("Action"), { target: { value: "waive" } });
  type(reasonBox(), "fixture");
  replies["POST resolve-issue"] = posted({
    issue_id: ISSUE_WARN,
    action: "waive",
    engine_decision: "waived",
    after_evidence_sha256: null,
    excluded_batch_positions: [],
    reason: "fixture",
  });
  await submit();
  expect(sentBody(1).resolutions).toEqual([
    {
      issue_id: ISSUE_WARN,
      action: "waive",
      reason: "fixture",
      expected_before_evidence_sha256: EVIDENCE,
    },
  ]);
  expect(posts()[1]?.init.headers).toEqual({ "Content-Type": "application/json" });
});

for (const [detail, text] of [
  ["invalid_body", "The server rejected the decision as malformed."],
  ["positions_not_in_issue", "The selected positions are not part of this issue."],
  ["waiver_not_allowed_for_blocking", "A blocking issue cannot be waived."],
  ["detector_failure_requires_rerun", "A detector failure requires a rerun, not a decision."],
  ["successor_not_later_version", "The successor must be a later version of this dataset."],
  ["request_too_large", "The request was too large."],
  ["idempotency_key_reused", "That request key was already used for a different decision."],
] as const) {
  test(`FTF-0516-N error code ${detail} is shown as its own message`, async () => {
    await loaded();
    review(ISSUE_OPEN);
    type(reasonBox(), "because");
    fireEvent.click(screen.getByLabelText("position 0"));
    replies["POST resolve-issue"] = { ok: false, status: 400, body: { detail } };
    await submit();
    expect(screen.getByRole("alert").textContent).toBe(text);
  });
}

test("FTF-0516-N an error body without a string detail is the generic status message", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["POST resolve-issue"] = { ok: false, status: 502, body: { message: "bad gateway" } };
  await submit();
  expect(screen.getByRole("alert").textContent).toBe("request failed (502)");
  replies["POST resolve-issue"] = { ok: false, status: 503, body: { detail: 5 } };
  await submit();
  expect(screen.getByRole("alert").textContent).toBe("request failed (503)");
});

test("FTF-0516-O a reload that fails clears the screen instead of leaving stale data, and a good one replaces it", async () => {
  await loaded();
  expect(screen.getByTestId("report")).toBeTruthy();
  review(ISSUE_OPEN);
  replies["GET issues"] = { ok: false, status: 500, body: { detail: "down" } };
  fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
  expect(screen.getByRole("alert").textContent).toBe("down");
  expect(screen.queryByTestId("report")).toBeNull();
  expect(screen.queryByRole("button", { name: /^Review / })).toBeNull();
  replies["GET issues"] = "network";
  fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
  expect(screen.getByRole("alert").textContent).toContain("network error");
  expect(screen.queryByTestId("report")).toBeNull();
  replies["GET issues"] = { ok: true, status: 200, body: { items: [], next_after: null } };
  replies["GET quality-report"] = { ok: true, status: 200, body: report({ report_seq: 2 }) };
  fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByTestId("report").textContent).toContain(`#2 ${sha("2")}`);
});

test("FTF-0516-O when the reload after a recorded decision fails the screen is cleared and the failure shown", async () => {
  await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["GET quality-report"] = { ok: false, status: 500, body: { detail: "down" } };
  await submit();
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
  expect(screen.getByRole("alert").textContent).toBe("down");
  expect(screen.queryByTestId("report")).toBeNull();
  expect(screen.queryByRole("list", { name: "Issues" })).toBeNull();
});

test("FTF-0516-P paging: a page of another report, a bad body, an error and a network failure are each an error and append nothing", async () => {
  const first: Reply = {
    ok: true,
    status: 200,
    body: { items: [issue(ISSUE_OPEN)], next_after: ISSUE_OPEN },
  };
  const cases: Array<[string, Reply, RegExp]> = [
    [
      "other report",
      {
        ok: true,
        status: 200,
        body: { items: [issue(ISSUE_WARN, { report_id: OTHER_UUID })], next_after: null },
      },
      /^quality response does not match the request$/,
    ],
    [
      "malformed",
      { ok: true, status: 200, body: { items: [{ nope: 1 }], next_after: null } },
      /^malformed quality response$/,
    ],
    ["http error", { ok: false, status: 404, body: { detail: "not_found" } }, /was not found/],
    ["network", "network", /network error: connection reset/],
  ];
  for (const [label, reply, text] of cases) {
    cleanup();
    replies = defaults();
    replies["GET issues"] = first;
    await loaded();
    replies["GET issues"] = reply;
    fireEvent.click(screen.getByRole("button", { name: "Load more issues" }));
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    expect(screen.getByRole("alert").textContent, label).toMatch(text);
    expect(screen.queryByTestId(`issue-${ISSUE_WARN}`), label).toBeNull();
    expect(screen.getByTestId(`issue-${ISSUE_OPEN}`), label).toBeTruthy();
    expect(screen.getByRole("button", { name: "Load more issues" }), label).toBeTruthy();
  }
});

test("FTF-0516-P a load-more response that lands after a dataset switch is ignored", async () => {
  replies["GET issues"] = {
    ok: true,
    status: 200,
    body: { items: [issue(ISSUE_OPEN)], next_after: ISSUE_OPEN },
  };
  const view = await loaded();
  const release = gateGets((url) => url.includes("after="));
  fireEvent.click(screen.getByRole("button", { name: "Load more issues" }));
  rerenderDataset(view, "77777777-7777-7777-7777-777777777777");
  release();
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(screen.queryByTestId("report")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText("Loading...")).toBeNull();
});

test("FTF-0516-Q the idempotency key does not survive a switch of dataset", async () => {
  const view = await loaded();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  replies["POST resolve-issue"] = "network";
  await submit();
  rerenderDataset(view, "77777777-7777-7777-7777-777777777777");
  rerenderDataset(view, DATASET);
  await load();
  review(ISSUE_OPEN);
  type(reasonBox(), "because");
  fireEvent.click(screen.getByLabelText("position 0"));
  await submit();
  expect(posts()).toHaveLength(2);
  expect(sentBody(1).idempotency_key).not.toBe(sentBody(0).idempotency_key);
});

test("FTF-0516-R the URL builders encode their inputs and the identity prefix is exact", async () => {
  expect(issuesUrl(BASE, "a b")).toBe(`${BASE}/issues?limit=50&after=a%20b`);
  expect(issuesUrl(BASE, null)).toBe(`${BASE}/issues?limit=50`);
  expect(resolutionsUrl(BASE)).toBe(`${BASE}/issue-resolutions?limit=50`);
  expect(qualityReportUrl(BASE)).toBe(`${BASE}/quality-report`);
  cleanup();
  mount({ apiBaseUrl: `${API_BASE}/`, organizationId: "o r/g" });
  await load();
  const prefix = `${API_BASE}/api/v1/organizations/o%20r%2Fg/projects/${PROJECT}/datasets/${DATASET}/`;
  expect(calls.every((c) => c.url.startsWith(prefix))).toBe(true);
});

test("FTF-0516-O2 a reload that is malformed, for another dataset, or a network failure also clears the stale screen", async () => {
  const failures: Array<[string, string, Reply, RegExp]> = [
    [
      "malformed",
      "GET quality-report",
      { ok: true, status: 200, body: { nope: 1 } },
      /^malformed quality response$/,
    ],
    [
      "other dataset",
      "GET quality-report",
      { ok: true, status: 200, body: report({ dataset_id: OTHER_UUID }) },
      /^quality response does not match the request$/,
    ],
    [
      "issue of another report",
      "GET issues",
      {
        ok: true,
        status: 200,
        body: { items: [issue(ISSUE_OPEN, { report_id: OTHER_UUID })], next_after: null },
      },
      /^quality response does not match the request$/,
    ],
    ["network", "GET issue-resolutions", "network", /network error: connection reset/],
  ];
  for (const [label, route, reply, text] of failures) {
    cleanup();
    replies = defaults();
    await loaded();
    expect(screen.getByTestId("report")).toBeTruthy();
    replies[route] = reply;
    fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    expect(screen.getByRole("alert").textContent, label).toMatch(text);
    expect(screen.queryByTestId("report"), label).toBeNull();
    expect(screen.queryByRole("list", { name: "Issues" }), label).toBeNull();
    expect(screen.queryByRole("list", { name: "Recent decisions" }), label).toBeNull();
  }
});
