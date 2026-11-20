// @vitest-environment jsdom
//
// FTF-0603 -- Evaluation suite validation/locking UI.
//
// Renders the real `SuiteValidationLocking`, drives real interaction and asserts on the exact
// `fetch` calls made against the frozen FTF-0601 / FTF-0602-P1 routes. `fetch` is stubbed with
// queued replies (no live server in a component test); the code path is the real one.
// Acceptance: lock only after validation; the hash covers suite identity and every case.
// Negative acceptance: missing critical categories, an insufficient sample plan and an
// unresolved expected output each block the lock, in the control and in the handler.
import { createHash } from "node:crypto";

import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  canonicalJson,
  computeSuiteSha256,
  defaultPlan,
  hasUnresolvedExpected,
  MAX_CASE_PAGES,
  sha256Hex,
  SUITE_LOCK_STRING_KEYS,
  SUITE_LOCK_STRINGS,
  SuiteValidationLocking,
  validateSuite,
  type SamplePlan,
  type SuiteCase,
  type SuiteRecord,
} from "../components/evaluation-factory/suite_validation_locking";

const API_BASE = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const SUITE = "33333333-3333-3333-3333-333333333333";
const SUITE_URL = `${API_BASE}/api/v1/organizations/${ORG}/projects/${PROJECT}/evaluation-suites/${SUITE}`;
const CASES_URL = `${SUITE_URL}/cases`;
const LOCK_URL = `${SUITE_URL}/lock`;
const SECRET = "SECRET-EXPECTED-ANSWER-4711";

function suiteRecord(extra: Partial<SuiteRecord> = {}): SuiteRecord {
  return {
    id: SUITE,
    agent_manifest_id: "44444444-4444-4444-4444-444444444444",
    version: 1,
    status: "draft",
    purpose: "development",
    name: "Refund suite",
    grading_logic_version: 1,
    reviewer_id: "reviewer@example.com",
    locked_at: null,
    ...extra,
  };
}

function suiteBody(extra: Partial<SuiteRecord> = {}) {
  return {
    ...suiteRecord(extra),
    organization_id: ORG,
    project_id: PROJECT,
    created_by: "creator@example.com",
    created_at: "2026-11-20T00:00:00+00:00",
  };
}

let caseCounter = 0;
function suiteCase(key: string, extra: Partial<SuiteCase> = {}): SuiteCase {
  caseCounter += 1;
  return {
    id: `aaaaaaaa-aaaa-aaaa-aaaa-${String(caseCounter).padStart(12, "0")}`,
    case_key: key,
    category: "refunds",
    severity: "high",
    critical: true,
    language: "en",
    group_key: `group-${key}`,
    input_json: { prompt: "refund order 1" },
    expected_json: { state: "refunded", note: SECRET },
    grader_type: "state_transition",
    grader_config_json: {},
    weight: 1,
    ...extra,
  };
}

const PLAN: SamplePlan = {
  requiredCategories: ["refunds", "security"],
  minCases: 4,
  minCritical: 2,
  minPerCategory: 2,
};

function goodCases(): SuiteCase[] {
  return [
    suiteCase("r1"),
    suiteCase("r2", { critical: false, grader_type: "exact_match" }),
    suiteCase("s1", { category: "security", grader_type: "forbidden_action" }),
    suiteCase("s2", { category: "security", critical: false, grader_type: "contains_fact" }),
  ];
}

// ---------------------------------------------------------------------------------------------
// Pure logic
// ---------------------------------------------------------------------------------------------

test("[SHA256] sha256Hex matches the platform SHA-256 on vectors incl. multi-block and unicode", () => {
  const vectors = [
    "",
    "abc",
    "a".repeat(55),
    "a".repeat(56),
    "a".repeat(63),
    "a".repeat(64),
    "a".repeat(65),
    "a".repeat(1000),
    "مرحبا بالعالم ✓",
  ];
  for (const text of vectors) {
    expect(sha256Hex(text), JSON.stringify(text.slice(0, 20))).toBe(
      createHash("sha256").update(text, "utf8").digest("hex"),
    );
  }
});

test("[HASH] suite_sha256 is order independent and changes with identity or any case field", () => {
  const suite = suiteRecord();
  const cases = goodCases();
  const base = computeSuiteSha256(suite, cases);
  expect(base).toMatch(/^[0-9a-f]{64}$/);
  expect(computeSuiteSha256(suite, [...cases].reverse())).toBe(base);
  // server ids/timestamps are not part of the identity
  expect(
    computeSuiteSha256(
      suite,
      cases.map((c) => ({ ...c, id: `other-${c.id}` })),
    ),
  ).toBe(base);

  const mutated = (patch: Partial<SuiteCase>) =>
    computeSuiteSha256(suite, [{ ...cases[0]!, ...patch }, ...cases.slice(1)]);
  const patches: Partial<SuiteCase>[] = [
    { case_key: "r1x" },
    { category: "other" },
    { severity: "low" },
    { critical: false },
    { language: "ar" },
    { group_key: "g" },
    { input_json: { prompt: "x" } },
    { expected_json: { state: "other" } },
    { grader_type: "tool_name" },
    { grader_config_json: { a: 1 } },
    { weight: 2 },
  ];
  for (const patch of patches) expect(mutated(patch), JSON.stringify(patch)).not.toBe(base);
  expect(computeSuiteSha256(suite, cases.slice(1))).not.toBe(base);
  for (const patch of [
    { id: "x" },
    { agent_manifest_id: "55555555-5555-5555-5555-555555555555" },
    { version: 2 },
    { purpose: "final" as const },
    { grading_logic_version: 2 },
  ]) {
    expect(computeSuiteSha256({ ...suite, ...patch }, cases), JSON.stringify(patch)).not.toBe(base);
  }
  expect(canonicalJson({ b: 1, a: [2, { d: 1, c: 2 }] })).toBe('{"a":[2,{"c":2,"d":1}],"b":1}');
});

test("[VALIDATE] a complete suite passes the plan with no findings", () => {
  expect(validateSuite(goodCases(), PLAN)).toEqual([]);
});

test("[NEG] missing critical category blocks (case exists but none critical, or none at all)", () => {
  const noCritical = goodCases().map((c) =>
    c.category === "security" ? { ...c, critical: false } : c,
  );
  expect(validateSuite(noCritical, PLAN).map((f) => `${f.code}:${f.subject}`)).toContain(
    "missing_critical_category:security",
  );
  const noCategory = goodCases().filter((c) => c.category !== "security");
  const codes = validateSuite(noCategory, PLAN).map((f) => f.code);
  expect(codes).toContain("missing_critical_category");
  expect(codes).toContain("insufficient_category_sample");
});

test("[NEG] insufficient sample plan blocks: total, critical and per-category floors", () => {
  const codes = (cases: SuiteCase[], plan = PLAN) => validateSuite(cases, plan).map((f) => f.code);
  expect(codes(goodCases(), { ...PLAN, minCases: 5 })).toContain("insufficient_total_cases");
  expect(codes(goodCases(), { ...PLAN, minCritical: 3 })).toContain("insufficient_critical_cases");
  expect(codes(goodCases(), { ...PLAN, minPerCategory: 3 })).toContain(
    "insufficient_category_sample",
  );
  // boundary: exactly at the floor passes
  expect(codes(goodCases(), { ...PLAN, minCases: 4, minCritical: 2, minPerCategory: 2 })).toEqual(
    [],
  );
  expect(codes([], PLAN)).toContain("no_cases");
});

test("[NEG] the final reference floors default to 1000 cases and 100 critical", () => {
  expect(defaultPlan("final")).toMatchObject({ minCases: 1000, minCritical: 100 });
  expect(defaultPlan("development")).toMatchObject({ minCases: 1, minCritical: 0 });
  const codes = validateSuite(goodCases(), {
    ...defaultPlan("final"),
    requiredCategories: ["refunds"],
  });
  expect(codes.map((f) => f.code)).toEqual(
    expect.arrayContaining(["insufficient_total_cases", "insufficient_critical_cases"]),
  );
});

test("[NEG] a plan with no required category or invalid numbers blocks", () => {
  expect(
    validateSuite(goodCases(), { ...PLAN, requiredCategories: [] }).map((f) => f.code),
  ).toContain("plan_has_no_categories");
  for (const bad of [Number.NaN, -1, 1.5, 0]) {
    expect(
      validateSuite(goodCases(), { ...PLAN, minCases: bad }).map((f) => f.code),
      String(bad),
    ).toContain("plan_invalid");
  }
  expect(validateSuite(goodCases(), { ...PLAN, minPerCategory: 0 }).map((f) => f.code)).toContain(
    "plan_invalid",
  );
});

test("[NEG] unresolved expected output blocks (placeholder, null, empty, nested, array)", () => {
  for (const expected of [
    { state: "TODO" },
    { state: " tbd " },
    { state: "" },
    { state: null },
    { nested: { deep: ["ok", "UNRESOLVED"] } },
    { list: [] },
    { nested: {} },
  ]) {
    expect(hasUnresolvedExpected(expected), JSON.stringify(expected)).toBe(true);
    const cases = goodCases();
    cases[1] = { ...cases[1]!, expected_json: expected };
    expect(
      validateSuite(cases, PLAN).map((f) => `${f.code}:${f.subject}`),
      JSON.stringify(expected),
    ).toContain("unresolved_expected_output:r2");
  }
  expect(hasUnresolvedExpected({ state: "refunded", count: 0, ok: false })).toBe(false);
});

test("[NEG] a critical case on a model-self-report grader blocks", () => {
  const cases = goodCases();
  cases[0] = { ...cases[0]!, grader_type: "llm_judge" };
  expect(validateSuite(cases, PLAN).map((f) => `${f.code}:${f.subject}`)).toContain(
    "critical_oracle_not_independent:r1",
  );
});

// ---------------------------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------------------------

type Reply = { ok: boolean; status?: number; body?: unknown; bad?: boolean; reject?: boolean };
let replies: Record<string, Reply[]>;

function enqueue(method: string, url: string, ...queued: Reply[]) {
  const key = `${method} ${url}`;
  replies[key] = [...(replies[key] ?? []), ...queued];
}
const ok = (body: unknown): Reply => ({ ok: true, status: 200, body });
const fail = (status: number, body: unknown): Reply => ({ ok: false, status, body });

function calls(method: string, url: string) {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.filter(
    ([calledUrl, init]) => calledUrl === url && (init?.method ?? "GET") === method,
  );
}

beforeEach(() => {
  replies = {};
  caseCounter = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string }) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const next = replies[key]?.shift();
      if (!next) throw new Error(`unexpected request ${key}`);
      if (next.reject) throw new TypeError("Failed to fetch");
      return {
        ok: next.ok,
        status: next.status ?? 200,
        json: async () => {
          if (next.bad) throw new SyntaxError("Unexpected token <");
          return next.body;
        },
      };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderUI(locale: "en" | "ar" = "en") {
  return render(
    createElement(SuiteValidationLocking, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      suiteId: SUITE,
      locale,
    }),
  );
}

function enqueueLoad(cases: SuiteCase[], suite: Partial<SuiteRecord> = {}) {
  enqueue("GET", SUITE_URL, ok(suiteBody(suite)));
  enqueue("GET", CASES_URL, ok({ items: cases, next_after: null }));
}

async function load(cases: SuiteCase[], suite: Partial<SuiteRecord> = {}) {
  enqueueLoad(cases, suite);
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-summary")).toBeTruthy());
}

function setInput(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
function fillPlan(plan: {
  categories: string;
  minCases: string;
  minCritical: string;
  perCategory: string;
}) {
  setInput(/Required critical categories/, plan.categories);
  setInput(/Minimum total cases/, plan.minCases);
  setInput(/Minimum critical cases/, plan.minCritical);
  setInput(/Minimum cases per required category/, plan.perCategory);
}
const GOOD_PLAN = {
  categories: "refunds, security",
  minCases: "4",
  minCritical: "2",
  perCategory: "2",
};
const lockButton = () => screen.getByRole("button", { name: /Lock suite/ }) as HTMLButtonElement;
const findingCodes = () =>
  [...document.querySelectorAll("[data-finding]")].map((el) => el.getAttribute("data-finding"));

test("[UI] nothing is requested until Load; the empty state offers the load action", () => {
  renderUI();
  expect(screen.getByText("No suite loaded")).toBeTruthy();
  expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  expect(screen.queryByRole("button", { name: /Lock suite/ })).toBeNull();
});

test("[UI] Load reads the suite then every case page; the hash shown is the computed one", async () => {
  renderUI();
  enqueue("GET", SUITE_URL, ok(suiteBody()));
  const all = goodCases();
  enqueue("GET", CASES_URL, ok({ items: all.slice(0, 2), next_after: "cursor-1" }));
  enqueue("GET", `${CASES_URL}?after=cursor-1`, ok({ items: all.slice(2), next_after: null }));
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-sha")).toBeTruthy());
  expect(calls("GET", `${CASES_URL}?after=cursor-1`).length).toBe(1);
  expect(screen.getByTestId("suite-lock-summary").textContent).toContain("4 cases");
  expect(screen.getByTestId("suite-lock-sha").textContent).toBe(
    computeSuiteSha256(suiteRecord(), all),
  );
});

test("[NEG-UI] each blocking finding disables Lock, names category/case key and never the value", async () => {
  renderUI();
  const cases = goodCases();
  cases[1] = { ...cases[1]!, expected_json: { state: "TODO", note: SECRET } };
  await load(cases.filter((c) => c.category !== "security"));
  fillPlan(GOOD_PLAN);
  expect(lockButton().disabled).toBe(true);
  const codes = findingCodes();
  expect(codes).toContain("missing_critical_category");
  expect(codes).toContain("insufficient_category_sample");
  expect(codes).toContain("insufficient_total_cases");
  expect(codes).toContain("unresolved_expected_output");
  expect(screen.getByTestId("suite-lock-findings").textContent).toContain("security");
  expect(screen.getByTestId("suite-lock-findings").textContent).toContain("r2");
  expect(document.body.textContent).not.toContain(SECRET);
  fireEvent.click(lockButton());
  expect(calls("POST", LOCK_URL).length).toBe(0);
});

test("[UI] lock is disabled until a plan is declared even when the cases look complete", async () => {
  renderUI();
  await load(goodCases());
  expect(findingCodes()).toContain("plan_has_no_categories");
  expect(lockButton().disabled).toBe(true);
});

test("[UI] a complete suite locks: reload, recompute, POST once, show the locked notice", async () => {
  renderUI();
  const cases = goodCases();
  await load(cases);
  fillPlan(GOOD_PLAN);
  expect(findingCodes()).toEqual([]);
  expect(screen.getByTestId("suite-lock-passed")).toBeTruthy();
  expect(lockButton().disabled).toBe(false);
  const expectedSha = computeSuiteSha256(suiteRecord(), cases);
  enqueueLoad(cases);
  enqueue(
    "POST",
    LOCK_URL,
    ok(suiteBody({ status: "locked", locked_at: "2026-11-20T01:00:00+00:00" })),
  );
  fireEvent.click(lockButton());
  fireEvent.click(screen.getByTestId("suite-lock-confirm-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-notice")).toBeTruthy());
  expect(calls("POST", LOCK_URL).length).toBe(1);
  expect(calls("POST", LOCK_URL)[0]![1].credentials).toBe("include");
  expect(screen.getByTestId("suite-lock-notice").textContent).toContain(expectedSha);
  expect(screen.getByTestId("suite-lock-notice").textContent).toContain("4 cases");
  expect(screen.getByTestId("suite-lock-already")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Lock suite/ })).toBeNull();
});

test("[NEG-UI] the handler re-validates: a suite that regressed after review is not locked", async () => {
  renderUI();
  const cases = goodCases();
  await load(cases);
  fillPlan(GOOD_PLAN);
  const regressed = cases.map((c, i) => (i === 1 ? { ...c, expected_json: { state: "TBD" } } : c));
  enqueueLoad(regressed);
  fireEvent.click(lockButton());
  fireEvent.click(screen.getByTestId("suite-lock-confirm-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-error")).toBeTruthy());
  expect(calls("POST", LOCK_URL).length).toBe(0);
  expect(screen.getByTestId("suite-lock-error").textContent).toContain("Nothing was locked");
  expect(findingCodes()).toContain("unresolved_expected_output");
});

test("[NEG-UI] a changed-but-still-valid suite (different hash) is not locked", async () => {
  renderUI();
  const cases = goodCases();
  await load(cases);
  fillPlan(GOOD_PLAN);
  const changed = cases.map((c, i) => (i === 3 ? { ...c, weight: 3 } : c));
  enqueueLoad(changed);
  fireEvent.click(lockButton());
  fireEvent.click(screen.getByTestId("suite-lock-confirm-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-error")).toBeTruthy());
  expect(calls("POST", LOCK_URL).length).toBe(0);
  expect(screen.getByTestId("suite-lock-error").textContent).toContain(
    "changed after you reviewed",
  );
  expect(screen.getByTestId("suite-lock-sha").textContent).toBe(
    computeSuiteSha256(suiteRecord(), changed),
  );
});

async function lockWith(reply: Reply) {
  renderUI();
  const cases = goodCases();
  await load(cases);
  fillPlan(GOOD_PLAN);
  enqueueLoad(cases);
  enqueue("POST", LOCK_URL, reply);
  fireEvent.click(lockButton());
  fireEvent.click(screen.getByTestId("suite-lock-confirm-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-error")).toBeTruthy());
  return screen.getByTestId("suite-lock-error").textContent ?? "";
}

test("[LAW17] lock failures never read as a lock: 403, 409, 500, network, non-JSON, wrong body", async () => {
  expect(
    await lockWith(fail(403, { detail: "evaluation suite can only be locked by its reviewer" })),
  ).toContain("assigned reviewer");
  expect(screen.queryByTestId("suite-lock-notice")).toBeNull();
  cleanup();
  expect(await lockWith(fail(409, { detail: "x" }))).toContain("refused the lock");
  cleanup();
  expect(await lockWith(fail(500, { detail: "boom" }))).toContain("request failed");
  cleanup();
  expect(await lockWith({ ok: false, reject: true })).toContain("could not be reached");
  cleanup();
  expect(await lockWith({ ok: true, bad: true })).toContain("not in the expected form");
  cleanup();
  for (const body of [
    suiteBody({ status: "draft" }),
    suiteBody({ status: "draft", locked_at: "2026-11-20T01:00:00+00:00" }),
    suiteBody({ status: "locked", locked_at: null }),
    { ...suiteBody({ status: "locked", locked_at: "2026-11-20T01:00:00+00:00" }), id: "other" },
    null,
  ]) {
    expect(await lockWith(ok(body))).toContain("not in the expected form");
    expect(screen.queryByTestId("suite-lock-notice")).toBeNull();
    cleanup();
  }
});

test("[LAW17] load failures: 404, network, non-JSON, malformed list, repeated cursor, runaway pages", async () => {
  renderUI();
  enqueue("GET", SUITE_URL, fail(404, { detail: "not_found" }));
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-error")).toBeTruthy());
  expect(screen.getByTestId("suite-lock-error").textContent).toContain("not found");
  expect(screen.queryByTestId("suite-lock-summary")).toBeNull();

  enqueue("GET", SUITE_URL, { ok: false, reject: true });
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() =>
    expect(screen.getByTestId("suite-lock-error").textContent).toContain("could not be reached"),
  );

  enqueue("GET", SUITE_URL, ok(suiteBody()));
  enqueue("GET", CASES_URL, ok({ items: [{ id: "x" }], next_after: null }));
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() =>
    expect(screen.getByTestId("suite-lock-error").textContent).toContain(
      "not in the expected form",
    ),
  );

  enqueue("GET", SUITE_URL, ok(suiteBody()));
  enqueue("GET", CASES_URL, ok({ items: [suiteCase("c1")], next_after: "same" }));
  enqueue("GET", `${CASES_URL}?after=same`, ok({ items: [suiteCase("c2")], next_after: "same" }));
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() =>
    expect(screen.getByTestId("suite-lock-error").textContent).toContain("did not finish"),
  );
  expect(screen.queryByTestId("suite-lock-summary")).toBeNull();
  expect(MAX_CASE_PAGES).toBe(200);
});

test("[NEG-UI] duplicate case_key across pages is a malformed listing, never validated", async () => {
  renderUI();
  enqueue("GET", SUITE_URL, ok(suiteBody()));
  enqueue("GET", CASES_URL, ok({ items: [suiteCase("dup"), suiteCase("dup")], next_after: null }));
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-error")).toBeTruthy());
  expect(screen.queryByTestId("suite-lock-sha")).toBeNull();
});

test("[SEC] 403 final_suite_content_restricted clears all content and shows no case data", async () => {
  renderUI();
  enqueue("GET", SUITE_URL, ok(suiteBody({ purpose: "final" })));
  enqueue("GET", CASES_URL, fail(403, { detail: "final_suite_content_restricted" }));
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-restricted")).toBeTruthy());
  expect(screen.queryByTestId("suite-lock-summary")).toBeNull();
  expect(screen.queryByTestId("suite-lock-sha")).toBeNull();
  expect(screen.queryByRole("button", { name: /Lock suite/ })).toBeNull();
});

test("[UI] an already locked suite offers no lock control and no plan", async () => {
  renderUI();
  await load(goodCases(), { status: "locked", locked_at: "2026-11-20T01:00:00+00:00" });
  expect(screen.getByTestId("suite-lock-already")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Lock suite/ })).toBeNull();
  expect(screen.queryByLabelText(/Required critical categories/)).toBeNull();
});

test("[UI] a final suite starts from the 1000/100 reference floors", async () => {
  renderUI();
  await load(goodCases(), { purpose: "final" });
  setInput(/Required critical categories/, "refunds");
  expect(findingCodes()).toEqual(
    expect.arrayContaining(["insufficient_total_cases", "insufficient_critical_cases"]),
  );
  expect(lockButton().disabled).toBe(true);
});

test("[I18N] ar renders rtl, localized findings and no raw finding code", async () => {
  const { container } = renderUI("ar");
  enqueueLoad(goodCases());
  fireEvent.click(screen.getByRole("button", { name: SUITE_LOCK_STRINGS.loadButton.ar }));
  await waitFor(() => expect(screen.getByTestId("suite-lock-summary")).toBeTruthy());
  const root = container.querySelector("section[lang]")!;
  expect(root.getAttribute("dir")).toBe("rtl");
  expect(root.getAttribute("lang")).toBe("ar");
  expect(screen.getByTestId("suite-lock-findings").textContent).toContain(
    SUITE_LOCK_STRINGS.fCode_plan_has_no_categories.ar,
  );
  expect(screen.getByTestId("suite-lock-findings").textContent).not.toContain(
    "plan_has_no_categories",
  );
});

test("[I18N] every catalogue entry has non-empty en and ar with the same placeholders", () => {
  const placeholders = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join(",");
  for (const key of SUITE_LOCK_STRING_KEYS) {
    const entry = SUITE_LOCK_STRINGS[key];
    expect(entry.en.trim().length, key).toBeGreaterThan(0);
    expect(entry.ar.trim().length, key).toBeGreaterThan(0);
    expect(placeholders(entry.ar), key).toBe(placeholders(entry.en));
    expect(
      /[A-Za-z]{3,}/.test(entry.ar.replace(/suite_sha256|\{\w+\}/g, "")),
      `${key} ar has latin`,
    ).toBe(false);
  }
});

test("[UI] Lock asks for confirmation first: no POST until Confirm; Cancel sends nothing", async () => {
  renderUI();
  await load(goodCases());
  fillPlan(GOOD_PLAN);
  expect(screen.queryByTestId("suite-lock-confirm")).toBeNull();
  fireEvent.click(lockButton());
  expect(screen.getByTestId("suite-lock-confirm").textContent).toContain("permanent");
  expect(document.activeElement).toBe(screen.getByTestId("suite-lock-confirm"));
  expect(calls("POST", LOCK_URL).length).toBe(0);
  fireEvent.click(screen.getByTestId("suite-lock-cancel-button"));
  expect(screen.queryByTestId("suite-lock-confirm")).toBeNull();
  expect(calls("POST", LOCK_URL).length).toBe(0);
  // editing the plan so it no longer passes withdraws a pending confirmation
  fireEvent.click(lockButton());
  expect(screen.getByTestId("suite-lock-confirm")).toBeTruthy();
  setInput(/Minimum total cases/, "999");
  expect(screen.queryByTestId("suite-lock-confirm")).toBeNull();
  expect(calls("POST", LOCK_URL).length).toBe(0);
});
