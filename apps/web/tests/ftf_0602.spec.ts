// @vitest-environment jsdom
//
// FTF-0602 -- Evaluation case editor/import UI.
//
// A real component/DOM-level test (Vitest + React Testing Library + jsdom):
// renders the actual `EvaluationCaseEditorImport`, drives real user
// interaction and asserts on the exact `fetch` calls (method, URL, JSON body)
// made against the frozen FTF-0602-P1 routes. `fetch` is mocked (no live
// server in a unit test); the code path is the real one.
//
// Disclosed: component-level, not a browser E2E -- the repository has no
// Next.js `app/` page tree yet (FTF-H029), same precedent as ftf_0308.spec.ts.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  caseUrl,
  casesUrl,
  EvaluationCaseEditorImport,
  nonIndependentCriticalRows,
  parseImportText,
} from "../components/evaluation-factory/evaluation_case_editor_import";

const API_BASE = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const SUITE = "33333333-3333-3333-3333-333333333333";
const CASE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const CASE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const LIST_URL = `${API_BASE}/api/v1/organizations/${ORG}/projects/${PROJECT}/evaluation-suites/${SUITE}/cases`;
const SECRET = "SECRET-EXPECTED-ANSWER-4711";

function evalCase(id: string, key: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    organization_id: ORG,
    project_id: PROJECT,
    suite_id: SUITE,
    case_key: key,
    category: "refunds",
    severity: "high",
    critical: false,
    language: "en",
    group_key: `group-${key}`,
    input_json: { prompt: "refund order 1" },
    expected_json: { state: "refunded", note: SECRET },
    grader_type: "exact_match",
    grader_config_json: {},
    weight: 1,
    created_by: "user@example.com",
    created_at: "2026-10-08T00:00:00+00:00",
    updated_at: "2026-10-08T00:00:00+00:00",
    ...extra,
  };
}

function importRow(key: string, extra: Record<string, unknown> = {}) {
  return {
    case_key: key,
    category: "refunds",
    severity: "high",
    language: "en",
    group_key: `group-${key}`,
    input_json: { prompt: "p" },
    expected_json: { state: "refunded" },
    grader_type: "exact_match",
    ...extra,
  };
}

type Reply = { ok: boolean; status?: number; body: unknown };
let replies: Record<string, Reply[]>;

function enqueue(method: string, url: string, ...queued: Reply[]) {
  const key = `${method} ${url}`;
  replies[key] = [...(replies[key] ?? []), ...queued];
}
const ok = (body: unknown): Reply => ({ ok: true, status: 200, body });
const created = (body: unknown): Reply => ({ ok: true, status: 201, body });
const fail = (status: number, body: unknown): Reply => ({ ok: false, status, body });

function calls(method: string, url: string) {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.filter(
    ([calledUrl, init]) => calledUrl === url && (init?.method ?? "GET") === method,
  );
}
function sentBody(method: string, url: string): unknown {
  const call = calls(method, url)[0];
  if (!call) throw new Error(`no ${method} was made`);
  return JSON.parse(call[1].body);
}
function totalCalls(): number {
  return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length;
}

beforeEach(() => {
  replies = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string }) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const next = replies[key]?.shift();
      if (!next) throw new Error(`unexpected request ${key}`);
      return { ok: next.ok, status: next.status ?? 200, json: async () => next.body };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderUI() {
  return render(
    createElement(EvaluationCaseEditorImport, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      suiteId: SUITE,
    }),
  );
}

async function loadCases(items: unknown[], nextAfter: string | null = null) {
  enqueue("GET", LIST_URL, ok({ items, next_after: nextAfter }));
  fireEvent.click(screen.getByTestId("load-cases-button"));
  await waitFor(() => expect(screen.getByTestId("case-list")).toBeTruthy());
}

function typeImport(text: string) {
  fireEvent.change(screen.getByTestId("import-text"), { target: { value: text } });
}
function clickImport() {
  fireEvent.click(screen.getByTestId("import-button"));
}
async function openEditor(id: string) {
  fireEvent.click(screen.getByTestId(`edit-case-${id}`));
  await waitFor(() => expect(screen.getByTestId("case-form")).toBeTruthy());
}
function setField(testId: string, value: string) {
  fireEvent.change(screen.getByTestId(testId), { target: { value } });
}
function clickSave() {
  fireEvent.click(screen.getByTestId("save-case-button"));
}

test("URL helpers point at the frozen routes", () => {
  expect(casesUrl(API_BASE, ORG, PROJECT, SUITE)).toBe(LIST_URL);
  expect(casesUrl(`${API_BASE}/`, ORG, PROJECT, SUITE)).toBe(LIST_URL);
  expect(caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A)).toBe(`${LIST_URL}/${CASE_A}`);
});

test("nothing is requested on render", () => {
  renderUI();
  expect(totalCalls()).toBe(0);
});

test("listing shows case metadata with the server's order and never the expected answer", async () => {
  renderUI();
  await loadCases([evalCase(CASE_B, "b-case"), evalCase(CASE_A, "a-case")]);
  const keys = [CASE_B, CASE_A].map((id) => screen.getByTestId(`case-key-${id}`).textContent);
  expect(keys).toEqual(["b-case", "a-case"]);
  expect(calls("GET", LIST_URL).length).toBe(1);
  expect(document.body.textContent).not.toContain(SECRET);
});

test("Load more passes the encoded next_after cursor and appends the page", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a case/1")], "a case/1");
  const second = `${LIST_URL}?after=${encodeURIComponent("a case/1")}`;
  enqueue("GET", second, ok({ items: [evalCase(CASE_B, "b-case")], next_after: null }));
  fireEvent.click(screen.getByTestId("load-more-button"));
  await waitFor(() => expect(screen.getByTestId(`case-row-${CASE_B}`)).toBeTruthy());
  expect(screen.getByTestId(`case-row-${CASE_A}`)).toBeTruthy();
  expect(calls("GET", second).length).toBe(1);
  expect(screen.queryByTestId("load-more-button")).toBeNull();
});

test("a restricted final suite shows no case content, and clears cases already on screen", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")], "a-case");
  enqueue(
    "GET",
    `${LIST_URL}?after=a-case`,
    fail(403, { detail: "final_suite_content_restricted" }),
  );
  fireEvent.click(screen.getByTestId("load-more-button"));
  await waitFor(() => expect(screen.getByTestId("restricted-notice")).toBeTruthy());
  expect(screen.queryByTestId("case-list")).toBeNull();
  expect(document.body.textContent).not.toContain("a-case");
  expect(document.body.textContent).not.toContain(SECRET);
});

test("a plain role_not_authorized listing is an error, not an empty list", async () => {
  renderUI();
  enqueue("GET", LIST_URL, fail(403, { detail: "role_not_authorized" }));
  fireEvent.click(screen.getByTestId("load-cases-button"));
  await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
  expect(screen.getByTestId("list-error").textContent).toContain("role may not");
  expect(screen.queryByTestId("case-list")).toBeNull();
});

test("a malformed listing is an error", async () => {
  renderUI();
  enqueue("GET", LIST_URL, ok({ items: [{ nope: true }], next_after: null }));
  fireEvent.click(screen.getByTestId("load-cases-button"));
  await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
  expect(screen.queryByTestId("case-list")).toBeNull();
});

test("import posts exactly the pasted cases, with nothing defaulted", async () => {
  renderUI();
  const rows = [importRow("c1"), importRow("c2", { critical: true, grader_type: "tool_name" })];
  enqueue("POST", LIST_URL, created({ items: [evalCase(CASE_A, "c1"), evalCase(CASE_B, "c2")] }));
  typeImport(JSON.stringify(rows));
  clickImport();
  await waitFor(() => expect(screen.getByTestId("notice")).toBeTruthy());
  expect(sentBody("POST", LIST_URL)).toEqual({ cases: rows });
  expect(screen.getByTestId("notice").textContent).toContain("Imported 2 cases");
  expect((screen.getByTestId("import-text") as HTMLTextAreaElement).value).toBe("");
});

test("a row missing group_key is sent as it is (the server says required), never filled in", async () => {
  renderUI();
  const row = importRow("c1");
  delete (row as Record<string, unknown>).group_key;
  enqueue(
    "POST",
    LIST_URL,
    fail(400, {
      detail: "validation_failed",
      errors: [{ index: 0, field: "group_key", code: "required" }],
    }),
  );
  typeImport(JSON.stringify([row]));
  clickImport();
  await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
  expect(sentBody("POST", LIST_URL)).toEqual({ cases: [row] });
  expect(screen.getByTestId("import-field-errors").textContent).toContain(
    "Row 1 / group_key: required",
  );
  expect(screen.queryByTestId("notice")).toBeNull();
});

test("import accepts JSON Lines", async () => {
  renderUI();
  const rows = [importRow("c1"), importRow("c2")];
  enqueue("POST", LIST_URL, created({ items: [evalCase(CASE_A, "c1"), evalCase(CASE_B, "c2")] }));
  typeImport(`${JSON.stringify(rows[0])}\n\n${JSON.stringify(rows[1])}\n`);
  clickImport();
  await waitFor(() => expect(screen.getByTestId("notice")).toBeTruthy());
  expect(sentBody("POST", LIST_URL)).toEqual({ cases: rows });
});

test("a chosen file is parsed in the browser and fills the import box", async () => {
  renderUI();
  const text = JSON.stringify([importRow("c1")]);
  const file = new File([text], "cases.json", { type: "application/json" });
  fireEvent.change(screen.getByTestId("import-file"), { target: { files: [file] } });
  await waitFor(() =>
    expect((screen.getByTestId("import-text") as HTMLTextAreaElement).value).toBe(text),
  );
  expect(totalCalls()).toBe(0);
});

test("unparseable or out-of-range imports send nothing", async () => {
  renderUI();
  const bad: [string, string][] = [
    ["", "Paste or choose"],
    ["[{", "not valid JSON"],
    ['{"a": 1}\nnot json', "Line 2"],
    ["[]", "1 to 500"],
    [JSON.stringify(Array.from({ length: 501 }, (_, i) => importRow(`k${i}`))), "1 to 500"],
    ["[1, 2]", "Row 1 is not a JSON object"],
  ];
  for (const [text, expected] of bad) {
    typeImport(text);
    clickImport();
    await waitFor(() => expect(screen.getByTestId("import-error").textContent).toContain(expected));
  }
  expect(totalCalls()).toBe(0);
});

test("exactly 500 cases are allowed", () => {
  const text = JSON.stringify(Array.from({ length: 500 }, (_, i) => importRow(`k${i}`)));
  const parsed = parseImportText(text);
  expect("cases" in parsed && parsed.cases.length).toBe(500);
});

test("a critical case graded by model self-report is blocked before any request", async () => {
  renderUI();
  for (const grader of ["exact_match", "contains_fact", "llm_judge", "json_schema", "latency"]) {
    typeImport(
      JSON.stringify([importRow("ok"), importRow("bad", { critical: true, grader_type: grader })]),
    );
    clickImport();
    await waitFor(() => expect(screen.getByTestId("import-error").textContent).toContain("Row 2"));
    expect(screen.getByTestId("import-error").textContent).toContain("independent");
  }
  expect(totalCalls()).toBe(0);
});

test("every independent grader is accepted for a critical case", () => {
  for (const grader of [
    "tool_name",
    "tool_arguments",
    "tool_sequence",
    "state_transition",
    "forbidden_action",
    "required_refusal",
  ]) {
    expect(
      nonIndependentCriticalRows([importRow("k", { critical: true, grader_type: grader })]),
    ).toEqual([]);
  }
  expect(
    nonIndependentCriticalRows([importRow("k", { critical: false, grader_type: "llm_judge" })]),
  ).toEqual([]);
});

test("the route's critical_oracle_not_independent 400 is shown, naming row and field but never the value", async () => {
  renderUI();
  enqueue(
    "POST",
    LIST_URL,
    fail(400, {
      detail: "validation_failed",
      errors: [
        {
          index: 0,
          field: "grader_type",
          code: "critical_oracle_not_independent",
          value: SECRET,
        },
      ],
    }),
  );
  typeImport(JSON.stringify([importRow("c1", { input_json: { prompt: SECRET } })]));
  clickImport();
  await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
  const errors = screen.getByTestId("import-field-errors").textContent ?? "";
  expect(errors).toContain("Row 1 / grader_type: critical_oracle_not_independent");
  expect(errors).not.toContain(SECRET);
  expect(screen.getByTestId("import-error").textContent).not.toContain(SECRET);
});

test("409 duplicate_case_key and suite_locked are errors, never an import", async () => {
  renderUI();
  for (const [detail, text] of [
    ["duplicate_case_key", "already exists"],
    ["suite_locked", "locked"],
  ]) {
    enqueue("POST", LIST_URL, fail(409, { detail }));
    typeImport(JSON.stringify([importRow("c1")]));
    clickImport();
    await waitFor(() => expect(screen.getByTestId("import-error").textContent).toContain(text));
    expect(screen.queryByTestId("notice")).toBeNull();
  }
});

test("a network failure or a malformed 201 on import is an error, never an import", async () => {
  renderUI();
  typeImport(JSON.stringify([importRow("c1")]));
  clickImport(); // no reply queued: the mocked fetch throws
  await waitFor(() =>
    expect(screen.getByTestId("import-error").textContent).toContain("network error"),
  );
  expect(screen.queryByTestId("notice")).toBeNull();

  enqueue("POST", LIST_URL, created({ items: "nope" }));
  clickImport();
  await waitFor(() =>
    expect(screen.getByTestId("import-error").textContent).toContain("malformed"),
  );
  expect(screen.queryByTestId("notice")).toBeNull();
});

test("a 403 on import for a restricted final suite shows no content", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  enqueue("POST", LIST_URL, fail(403, { detail: "final_suite_content_restricted" }));
  typeImport(JSON.stringify([importRow("c1")]));
  clickImport();
  await waitFor(() => expect(screen.getByTestId("restricted-notice")).toBeTruthy());
  expect(screen.queryByTestId("case-list")).toBeNull();
});

test("the editor shows case_key as fixed text and offers no way to change it", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  await openEditor(CASE_A);
  expect(screen.getByTestId("edit-case-key").textContent).toBe("a-case");
  const inputs = Array.from(document.querySelectorAll("form input, form textarea, form select"));
  expect(inputs.some((node) => (node as HTMLInputElement).value === "a-case")).toBe(false);
});

test("saving patches only the fields that changed", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  await openEditor(CASE_A);
  const url = caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A);
  enqueue("PATCH", url, ok(evalCase(CASE_A, "a-case", { severity: "low", language: "ar" })));
  setField("field-severity", "low");
  setField("field-language", "ar");
  clickSave();
  await waitFor(() => expect(screen.getByTestId("notice")).toBeTruthy());
  expect(sentBody("PATCH", url)).toEqual({ severity: "low", language: "ar" });
  expect(screen.getByTestId(`case-row-${CASE_A}`).textContent).toContain("low");
});

test("editing the expected state and weight sends parsed values", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  await openEditor(CASE_A);
  const url = caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A);
  enqueue("PATCH", url, ok(evalCase(CASE_A, "a-case", { weight: 2.5 })));
  setField("field-expected-json", '{"state": "cancelled"}');
  setField("field-weight", "2.5");
  clickSave();
  await waitFor(() => expect(screen.getByTestId("notice")).toBeTruthy());
  expect(sentBody("PATCH", url)).toEqual({ expected_json: { state: "cancelled" }, weight: 2.5 });
});

test("saving with no change, bad JSON, an empty expected state or a bad weight sends nothing", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  await openEditor(CASE_A);
  clickSave();
  expect(screen.getByTestId("edit-error").textContent).toContain("Nothing was changed");

  setField("field-input-json", "{oops");
  clickSave();
  expect(screen.getByTestId("edit-error").textContent).toContain(
    "input_json must be a JSON object",
  );

  setField("field-input-json", "[1]");
  clickSave();
  expect(screen.getByTestId("edit-error").textContent).toContain(
    "input_json must be a JSON object",
  );

  setField("field-input-json", '{"prompt": "refund order 1"}');
  setField("field-expected-json", "{}");
  clickSave();
  expect(screen.getByTestId("edit-error").textContent).toContain("expected_json must not be empty");

  setField("field-expected-json", '{"state": "refunded", "note": "x"}');
  for (const weight of ["0", "-1", "abc", ""]) {
    setField("field-weight", weight);
    clickSave();
    expect(screen.getByTestId("edit-error").textContent).toContain("weight");
  }
  expect(totalCalls()).toBe(1); // only the initial listing
});

test("marking a case critical while it keeps a self-report grader is blocked, with no request", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case", { grader_type: "llm_judge" })]);
  await openEditor(CASE_A);
  fireEvent.click(screen.getByTestId("field-critical"));
  clickSave();
  expect(screen.getByTestId("edit-error").textContent).toContain("independent");
  expect(totalCalls()).toBe(1);
});

test("moving an already-critical case to a self-report grader is blocked, with no request", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case", { critical: true, grader_type: "tool_name" })]);
  await openEditor(CASE_A);
  setField("field-grader-type", "contains_fact");
  clickSave();
  expect(screen.getByTestId("edit-error").textContent).toContain("independent");
  expect(totalCalls()).toBe(1);
});

test("critical together with an independent grader is sent in one patch", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case", { grader_type: "llm_judge" })]);
  await openEditor(CASE_A);
  const url = caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A);
  enqueue(
    "PATCH",
    url,
    ok(evalCase(CASE_A, "a-case", { critical: true, grader_type: "forbidden_action" })),
  );
  fireEvent.click(screen.getByTestId("field-critical"));
  setField("field-grader-type", "forbidden_action");
  clickSave();
  await waitFor(() => expect(screen.getByTestId("notice")).toBeTruthy());
  expect(sentBody("PATCH", url)).toEqual({ critical: true, grader_type: "forbidden_action" });
});

test("a failed save leaves the listed case untouched and shows the error", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  await openEditor(CASE_A);
  const url = caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A);
  setField("field-severity", "low");

  enqueue("PATCH", url, fail(409, { detail: "suite_locked" }));
  clickSave();
  await waitFor(() => expect(screen.getByTestId("edit-error").textContent).toContain("locked"));
  expect(screen.getByTestId(`case-row-${CASE_A}`).textContent).toContain("high");

  enqueue(
    "PATCH",
    url,
    fail(400, {
      detail: "validation_failed",
      errors: [{ index: null, field: "severity", code: "invalid_value" }],
    }),
  );
  clickSave();
  await waitFor(() => expect(screen.getByTestId("edit-field-errors")).toBeTruthy());
  expect(screen.getByTestId("edit-field-errors").textContent).toContain(
    "Request / severity: invalid_value",
  );
  expect(screen.getByTestId(`case-row-${CASE_A}`).textContent).toContain("high");
  expect(screen.queryByTestId("notice")).toBeNull();

  clickSave(); // no reply queued: the mocked fetch throws
  await waitFor(() =>
    expect(screen.getByTestId("edit-error").textContent).toContain("network error"),
  );
  expect(screen.getByTestId(`case-row-${CASE_A}`).textContent).toContain("high");
});

test("a 403 on save for a restricted final suite closes the editor and shows no content", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  await openEditor(CASE_A);
  const url = caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A);
  enqueue("PATCH", url, fail(403, { detail: "final_suite_content_restricted" }));
  setField("field-severity", "low");
  clickSave();
  await waitFor(() => expect(screen.getByTestId("restricted-notice")).toBeTruthy());
  expect(screen.queryByTestId("case-form")).toBeNull();
  expect(screen.queryByTestId("case-list")).toBeNull();
});

test("every request carries credentials", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  enqueue("POST", LIST_URL, created({ items: [evalCase(CASE_B, "b-case")] }));
  typeImport(JSON.stringify([importRow("b-case")]));
  clickImport();
  await waitFor(() => expect(screen.getByTestId("notice")).toBeTruthy());
  await openEditor(CASE_A);
  const url = caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A);
  enqueue("PATCH", url, ok(evalCase(CASE_A, "a-case", { severity: "low" })));
  setField("field-severity", "low");
  clickSave();
  await waitFor(() => expect(calls("PATCH", url).length).toBe(1));
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  for (const [, init] of fetchMock.mock.calls) expect(init.credentials).toBe("include");
});

test("a malformed save response is an error and the listed case is untouched", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  await openEditor(CASE_A);
  const url = caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A);
  enqueue("PATCH", url, ok({ id: CASE_A }));
  setField("field-severity", "low");
  clickSave();
  await waitFor(() => expect(screen.getByTestId("edit-error").textContent).toContain("malformed"));
  expect(screen.queryByTestId("notice")).toBeNull();
  expect(screen.getByTestId(`case-row-${CASE_A}`).textContent).toContain("high");
});

// ---- round 2 (Control-plane L2 return, tests only): Law 17, a denied write is never shown as success ----

test("a 403 role_not_authorized on import is an error: no success notice, list unchanged", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  enqueue("POST", LIST_URL, fail(403, { detail: "role_not_authorized" }));
  typeImport(JSON.stringify([importRow("c1")]));
  clickImport();
  await waitFor(() => expect(screen.getByTestId("import-error")).toBeTruthy());
  expect(screen.getByTestId("import-error").textContent).toContain("role");
  expect(screen.queryByTestId("notice")).toBeNull();
  expect(screen.queryByTestId("restricted-notice")).toBeNull();
  expect(calls("POST", LIST_URL).length).toBe(1);
  const rows = screen.getByTestId("case-list").querySelectorAll("li, tr");
  expect(rows.length).toBe(1);
  expect(screen.getByTestId(`case-row-${CASE_A}`)).toBeTruthy();
  expect(screen.queryByText(/Imported/)).toBeNull();
});

test("a 403 role_not_authorized on save is an error: no 'Saved', row unchanged", async () => {
  renderUI();
  await loadCases([evalCase(CASE_A, "a-case")]);
  await openEditor(CASE_A);
  const url = caseUrl(API_BASE, ORG, PROJECT, SUITE, CASE_A);
  enqueue("PATCH", url, fail(403, { detail: "role_not_authorized" }));
  setField("field-severity", "low");
  clickSave();
  await waitFor(() => expect(screen.getByTestId("edit-error")).toBeTruthy());
  expect(screen.getByTestId("edit-error").textContent).toContain("role");
  expect(screen.queryByTestId("notice")).toBeNull();
  expect(screen.queryByTestId("restricted-notice")).toBeNull();
  expect(screen.queryByText(/Saved/)).toBeNull();
  expect(screen.getByTestId(`case-row-${CASE_A}`).textContent).toContain("high");
});
