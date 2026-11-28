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
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  canonicalJson,
  computeSuiteSha256,
  defaultPlan,
  FINDING_CODES,
  hasUnresolvedExpected,
  MAX_CASE_PAGES,
  MAX_PLAN_CATEGORIES,
  SECTION_26_CATEGORIES,
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

const SCOPE = { organizationId: ORG, projectId: PROJECT };
const REPO_ROOT = join(__dirname, "..", "..", "..");

const PLAN: SamplePlan = {
  required_categories: ["refunds", "security"],
  min_independent_tasks: 4,
  min_critical_scenarios: 2,
  min_cases_per_category: 2,
};

function goodCases(): SuiteCase[] {
  return [
    suiteCase("r1"),
    suiteCase("r2", { critical: false, grader_type: "exact_match" }),
    suiteCase("s1", { category: "security", grader_type: "forbidden_action" }),
    suiteCase("s2", { category: "security", critical: false, grader_type: "contains_fact" }),
  ];
}

const codesOf = (findings: { code: string; subject: string | null }[]) =>
  findings.map((f) => `${f.code}:${f.subject}`);

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

interface Vector {
  format: string;
  input: {
    organization_id: string;
    project_id: string;
    suite: Omit<SuiteRecord, "status" | "name" | "reviewer_id" | "locked_at">;
    cases: Omit<SuiteCase, "id">[];
  };
  canonical_json: string;
  suite_sha256: string;
}
const VECTOR: Vector = JSON.parse(
  readFileSync(
    join(REPO_ROOT, "packages/contracts/test-vectors/evaluation-suite-sha256.json"),
    "utf8",
  ),
) as Vector;

test("[HASH] the browser reproduces the server's shared suite_sha256 test vector byte for byte", () => {
  const suite: SuiteRecord = {
    ...VECTOR.input.suite,
    status: "draft",
    name: "ignored",
    reviewer_id: null,
    locked_at: null,
  };
  const cases = VECTOR.input.cases.map((c, i) => ({ ...c, id: `server-id-${i}` }));
  expect(VECTOR.format).toBe("sovra.evaluation_suite.sha256.v1");
  expect(
    computeSuiteSha256(
      { organizationId: VECTOR.input.organization_id, projectId: VECTOR.input.project_id },
      suite,
      cases,
    ),
  ).toBe(VECTOR.suite_sha256);
  // the vector's own canonical bytes also hash to its digest (the vector is self-consistent)
  expect(createHash("sha256").update(VECTOR.canonical_json, "utf8").digest("hex")).toBe(
    VECTOR.suite_sha256,
  );
});

test("[HASH] suite_sha256 is order independent and changes with scope, identity or any case field", () => {
  const suite = suiteRecord();
  const cases = goodCases();
  const base = computeSuiteSha256(SCOPE, suite, cases);
  expect(base).toMatch(/^[0-9a-f]{64}$/);
  expect(computeSuiteSha256(SCOPE, suite, [...cases].reverse())).toBe(base);
  // server ids/timestamps are not part of the identity
  expect(
    computeSuiteSha256(
      SCOPE,
      suite,
      cases.map((c) => ({ ...c, id: `other-${c.id}` })),
    ),
  ).toBe(base);

  const mutated = (patch: Partial<SuiteCase>) =>
    computeSuiteSha256(SCOPE, suite, [{ ...cases[0]!, ...patch }, ...cases.slice(1)]);
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
  expect(computeSuiteSha256(SCOPE, suite, cases.slice(1))).not.toBe(base);
  for (const patch of [
    { id: "x" },
    { agent_manifest_id: "55555555-5555-5555-5555-555555555555" },
    { version: 2 },
    { purpose: "final" as const },
    { grading_logic_version: 2 },
  ]) {
    expect(
      computeSuiteSha256(SCOPE, { ...suite, ...patch }, cases),
      JSON.stringify(patch),
    ).not.toBe(base);
  }
  // the hash binds the organization and the project
  expect(
    computeSuiteSha256({ ...SCOPE, organizationId: "o-2" }, suite, cases),
    "organization",
  ).not.toBe(base);
  expect(computeSuiteSha256({ ...SCOPE, projectId: "p-2" }, suite, cases), "project").not.toBe(
    base,
  );
  expect(canonicalJson({ b: 1, a: [2, { d: 1, c: 2 }] })).toBe('{"a":[2,{"c":2,"d":1}],"b":1}');
});

test("[PIN] the Section 26 list and the finding codes are the server's, verbatim", () => {
  const source = readFileSync(
    join(REPO_ROOT, "apps/api/app/evaluation/evaluation_suite_lock_gate.py"),
    "utf8",
  );
  const block = (name: string) => {
    const match = new RegExp(`${name}[^=]*= \\(([^)]*)\\)`).exec(source);
    expect(match, name).not.toBeNull();
    return [...match![1]!.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  };
  expect([...SECTION_26_CATEGORIES]).toEqual(block("SECTION_26_CATEGORIES"));
  expect([...FINDING_CODES]).toEqual(block("FINDING_CODES"));
  expect(SECTION_26_CATEGORIES.length).toBe(22);
  expect(MAX_PLAN_CATEGORIES).toBe(Number(/MAX_PLAN_CATEGORIES = (\d+)/.exec(source)![1]));
});

test("[VALIDATE] a complete suite passes the plan with no findings", () => {
  expect(validateSuite(goodCases(), PLAN, "development")).toEqual([]);
});

test("[NEG] missing critical category blocks (case exists but none critical, or none at all)", () => {
  const noCritical = goodCases().map((c) =>
    c.category === "security" ? { ...c, critical: false } : c,
  );
  expect(codesOf(validateSuite(noCritical, PLAN, "development"))).toContain(
    "missing_critical_category:security",
  );
  const noCategory = goodCases().filter((c) => c.category !== "security");
  const codes = validateSuite(noCategory, PLAN, "development").map((f) => f.code);
  expect(codes).toContain("missing_critical_category");
  expect(codes).toContain("insufficient_category_sample");
});

test("[NEG] insufficient sample plan blocks: independent tasks, critical scenarios, per category", () => {
  const codes = (cases: SuiteCase[], plan = PLAN) =>
    validateSuite(cases, plan, "development").map((f) => f.code);
  expect(codes(goodCases(), { ...PLAN, min_independent_tasks: 5 })).toContain(
    "insufficient_independent_tasks",
  );
  expect(codes(goodCases(), { ...PLAN, min_critical_scenarios: 3 })).toContain(
    "insufficient_critical_scenarios",
  );
  expect(codes(goodCases(), { ...PLAN, min_cases_per_category: 3 })).toContain(
    "insufficient_category_sample",
  );
  // boundary: exactly at the floor passes
  expect(codes(goodCases(), { ...PLAN })).toEqual([]);
  expect(codes([], PLAN)).toContain("no_cases");
});

test("[NEG] independence counts distinct group_key values, never rows (Section 69.2)", () => {
  // four rows, all one conversation: one independent task, one critical scenario
  const oneConversation = goodCases().map((c) => ({ ...c, group_key: "one-conversation" }));
  const findings = validateSuite(oneConversation, PLAN, "development");
  expect(findings).toContainEqual({
    code: "insufficient_independent_tasks",
    subject: null,
    need: 4,
    have: 1,
  });
  expect(findings).toContainEqual({
    code: "insufficient_critical_scenarios",
    subject: null,
    need: 2,
    have: 1,
  });
  expect(findings).toContainEqual({
    code: "insufficient_category_sample",
    subject: "refunds",
    need: 2,
    have: 1,
  });
});

test("[NEG] the final suite defaults to every Section 26 category and the 1000 / 100 floors", () => {
  const plan = defaultPlan("final");
  expect(plan.required_categories).toEqual([...SECTION_26_CATEGORIES]);
  expect(plan.min_independent_tasks).toBe(1000);
  expect(plan.min_critical_scenarios).toBe(100);
  expect(defaultPlan("development")).toMatchObject({
    required_categories: [],
    min_independent_tasks: 1,
    min_critical_scenarios: 0,
  });
  const codes = validateSuite(goodCases(), plan, "final").map((f) => f.code);
  expect(codes).toEqual(
    expect.arrayContaining(["insufficient_independent_tasks", "insufficient_critical_scenarios"]),
  );
});

test("[NEG] a final plan cannot lower a floor or drop a Section 26 category; a development plan may raise but not go under 1", () => {
  const finalPlan = defaultPlan("final");
  const lowered = { ...finalPlan, min_independent_tasks: 1, min_critical_scenarios: 0 };
  expect(validateSuite(goodCases(), lowered, "final").map((f) => f.code)).toEqual(
    expect.arrayContaining(["sample_plan_below_floor"]),
  );
  expect(codesOf(validateSuite(goodCases(), lowered, "final"))).toEqual(
    expect.arrayContaining([
      "sample_plan_below_floor:min_independent_tasks",
      "sample_plan_below_floor:min_critical_scenarios",
    ]),
  );
  for (const dropped of SECTION_26_CATEGORIES) {
    const plan = {
      ...finalPlan,
      required_categories: finalPlan.required_categories.filter((c) => c !== dropped),
    };
    expect(codesOf(validateSuite(goodCases(), plan, "final")), dropped).toContain(
      `missing_critical_category:${dropped}`,
    );
  }
  // a plan may add categories on top of Section 26 without a finding about the plan itself
  const raised = {
    ...finalPlan,
    required_categories: [...finalPlan.required_categories, "refunds"],
    min_independent_tasks: 2000,
  };
  expect(codesOf(validateSuite(goodCases(), raised, "final"))).not.toContain(
    "sample_plan_below_floor:min_independent_tasks",
  );
  // a development suite needs at least one independent task per category and one task
  expect(
    codesOf(validateSuite(goodCases(), { ...PLAN, min_independent_tasks: 0 }, "development")),
  ).toContain("sample_plan_below_floor:min_independent_tasks");
});

test("[NEG] a plan with no required category or invalid numbers blocks", () => {
  expect(
    codesOf(validateSuite(goodCases(), { ...PLAN, required_categories: [] }, "development")),
  ).toContain("sample_plan_below_floor:required_categories");
  for (const bad of [Number.NaN, -1, 1.5, 1_000_001]) {
    expect(
      codesOf(validateSuite(goodCases(), { ...PLAN, min_independent_tasks: bad }, "development")),
      String(bad),
    ).toContain("sample_plan_missing:min_independent_tasks");
  }
  expect(
    codesOf(validateSuite(goodCases(), { ...PLAN, min_cases_per_category: 0 }, "development")),
  ).toContain("sample_plan_below_floor:min_cases_per_category");
});

test("[NEG] the 201st category is a finding, never silently dropped", () => {
  const many = Array.from({ length: MAX_PLAN_CATEGORIES + 1 }, (_, i) => `cat-${i}`);
  expect(
    codesOf(validateSuite(goodCases(), { ...PLAN, required_categories: many }, "development")),
  ).toContain("sample_plan_missing:required_categories");
  const exactly = many.slice(0, MAX_PLAN_CATEGORIES);
  expect(
    codesOf(validateSuite(goodCases(), { ...PLAN, required_categories: exactly }, "development")),
  ).not.toContain("sample_plan_missing:required_categories");
  const tooLong = ["x".repeat(101)];
  expect(
    codesOf(validateSuite(goodCases(), { ...PLAN, required_categories: tooLong }, "development")),
  ).toContain("sample_plan_missing:required_categories");
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
    expect(codesOf(validateSuite(cases, PLAN, "development")), JSON.stringify(expected)).toContain(
      "unresolved_expected_output:r2",
    );
  }
  expect(hasUnresolvedExpected({ state: "refunded", count: 0, ok: false })).toBe(false);
});

test("[NEG] a whole number past 2^53 - 1 has no hash both sides agree on and blocks", () => {
  const cases = goodCases();
  cases[2] = { ...cases[2]!, input_json: { id: 2 ** 53 } };
  expect(codesOf(validateSuite(cases, PLAN, "development"))).toContain("non_canonical_number:s1");
  const safe = goodCases();
  safe[2] = { ...safe[2]!, input_json: { id: 9_007_199_254_740_991 } };
  expect(codesOf(validateSuite(safe, PLAN, "development"))).not.toContain(
    "non_canonical_number:s1",
  );
});

test("[PARITY] a critical case on a model-self-report grader is the server's call, not a client-only block", () => {
  // The server has no oracle-independence clause; a client-only gate would be a browser-only rule.
  const cases = goodCases();
  cases[0] = { ...cases[0]!, grader_type: "llm_judge" };
  expect(validateSuite(cases, PLAN, "development")).toEqual([]);
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
  tasks: string;
  critical: string;
  perCategory: string;
}) {
  for (const [id, value] of [
    ["plan-categories", plan.categories],
    ["plan-min-tasks", plan.tasks],
    ["plan-min-critical", plan.critical],
    ["plan-min-per-category", plan.perCategory],
  ] as const) {
    fireEvent.change(screen.getByTestId(id), { target: { value } });
  }
}
const GOOD_PLAN = {
  categories: "refunds, security",
  tasks: "4",
  critical: "2",
  perCategory: "2",
};
const lockButton = () => screen.getByRole("button", { name: /Lock suite/ }) as HTMLButtonElement;
const findingCodes = () =>
  [...document.querySelectorAll("[data-finding]")].map((el) => el.getAttribute("data-finding"));
const lockBody = (index = 0) =>
  JSON.parse(calls("POST", LOCK_URL)[index]![1].body as string) as Record<string, unknown>;

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
    computeSuiteSha256(SCOPE, suiteRecord(), all),
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
  expect(codes).toContain("insufficient_independent_tasks");
  expect(codes).toContain("unresolved_expected_output");
  expect(screen.getByTestId("suite-lock-findings").textContent).toContain("security");
  expect(screen.getByTestId("suite-lock-findings").textContent).toContain("r2");
  expect(document.body.textContent).not.toContain(SECRET);
  fireEvent.click(lockButton());
  expect(calls("POST", LOCK_URL).length).toBe(0);
});

test("[UI] a development suite starts with no category: lock is disabled until a plan is declared", async () => {
  renderUI();
  await load(goodCases());
  expect(findingCodes()).toContain("sample_plan_below_floor");
  expect(screen.getByTestId("suite-lock-findings").textContent).toContain(
    "Required critical categories",
  );
  expect(lockButton().disabled).toBe(true);
});

test("[UI] a complete suite locks: reload, recompute, POST the plan and the reviewed hash once", async () => {
  renderUI();
  const cases = goodCases();
  await load(cases);
  fillPlan(GOOD_PLAN);
  expect(findingCodes()).toEqual([]);
  expect(screen.getByTestId("suite-lock-passed")).toBeTruthy();
  expect(lockButton().disabled).toBe(false);
  const expectedSha = computeSuiteSha256(SCOPE, suiteRecord(), cases);
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
  // exactly the body the server's `parse_lock_request` takes: the four-field plan and the hash
  expect(lockBody()).toEqual({
    sample_plan: {
      required_categories: ["refunds", "security"],
      min_independent_tasks: 4,
      min_critical_scenarios: 2,
      min_cases_per_category: 2,
    },
    suite_sha256: expectedSha,
  });
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
  expect(screen.getByTestId("suite-lock-error").textContent).toContain(
    "Validation failed on the latest cases. Nothing was locked",
  );
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
    computeSuiteSha256(SCOPE, suiteRecord(), changed),
  );
});

async function lockWith(reply: Reply, locale: "en" | "ar" = "en") {
  renderUI(locale);
  const cases = goodCases();
  enqueueLoad(cases);
  fireEvent.click(screen.getByTestId("suite-lock-load-button"));
  await waitFor(() => expect(screen.getByTestId("suite-lock-summary")).toBeTruthy());
  fillPlan(GOOD_PLAN);
  enqueueLoad(cases);
  enqueue("POST", LOCK_URL, reply);
  fireEvent.click(screen.getByTestId("suite-lock-lock-button"));
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
  for (const reply of [
    { ok: false, reject: true } as Reply,
    { ok: true, bad: true } as Reply,
    ok(suiteBody({ status: "draft" })),
    ok(suiteBody({ status: "draft", locked_at: "2026-11-20T01:00:00+00:00" })),
    ok(suiteBody({ status: "locked", locked_at: null })),
    ok({ ...suiteBody({ status: "locked", locked_at: "2026-11-20T01:00:00+00:00" }), id: "other" }),
    ok(null),
  ]) {
    // The request was sent: the suite may now be locked, so the page must NOT claim nothing changed.
    const text = await lockWith(reply);
    expect(text).toContain("may now be locked");
    expect(text).not.toContain("Nothing was changed");
    expect(screen.getByTestId("suite-lock-error-recovery").textContent).toMatch(/current state/i);
    expect(screen.queryByTestId("suite-lock-notice")).toBeNull();
    cleanup();
  }
});

test("[SERVER-GATE] a 409 suite_lock_validation_failed shows each typed finding and says the suite did not change", async () => {
  const findings = [
    { code: "missing_critical_category", subject: "security", need: null, have: null },
    { code: "insufficient_independent_tasks", subject: null, need: 4, have: 3 },
    { code: "unresolved_expected_output", subject: "r2", need: null, have: null },
    { code: "suite_sha256_mismatch", subject: null, need: null, have: null },
  ];
  const text = await lockWith(
    fail(409, { detail: { code: "suite_lock_validation_failed", findings } }),
  );
  expect(text).toContain("The suite was not changed");
  expect(findingCodes()).toEqual(findings.map((f) => f.code));
  const shown = screen.getByTestId("suite-lock-server-findings").textContent ?? "";
  expect(shown).toContain("security");
  expect(shown).toContain("has 3 independent tasks; the plan needs 4");
  expect(shown).toContain("r2");
  expect(screen.queryByTestId("suite-lock-notice")).toBeNull();
  // the suite is still a draft with the lock control available
  expect(lockButton()).toBeTruthy();
});

test("[SERVER-GATE] an unknown, malformed or empty finding list is a plain refusal, never a made-up finding", async () => {
  for (const findings of [
    [],
    [{ code: "not_a_code", subject: null }],
    [{ code: "no_cases", subject: 5 }],
    [{ code: "no_cases", subject: null, need: "4" }],
    "oops",
  ]) {
    const text = await lockWith(
      fail(409, { detail: { code: "suite_lock_validation_failed", findings } }),
    );
    expect(text).toContain("refused the lock");
    expect(screen.queryByTestId("suite-lock-server-findings")).toBeNull();
    cleanup();
  }
});

test("[SERVER-GATE] a 400 invalid_lock_request names the field and not the value", async () => {
  const text = await lockWith(
    fail(400, {
      detail: { code: "invalid_lock_request", field: "sample_plan.min_critical_scenarios" },
    }),
  );
  expect(text).toContain("sample_plan.min_critical_scenarios");
  expect(text).toContain("The suite was not changed");
});

test("[SERVER-GATE] server findings render localized under ar, with no raw code", async () => {
  await lockWith(
    fail(409, {
      detail: {
        code: "suite_lock_validation_failed",
        findings: [{ code: "no_cases", subject: null, need: null, have: null }],
      },
    }),
    "ar",
  );
  const shown = screen.getByTestId("suite-lock-server-findings").textContent ?? "";
  expect(shown).toContain(SUITE_LOCK_STRINGS.fCode_no_cases.ar);
  expect(shown).not.toContain("no_cases");
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

test("[UI] a final suite starts from every Section 26 category and the 1000 / 100 floors, visible in the fields", async () => {
  renderUI();
  await load(goodCases(), { purpose: "final" });
  const categories = screen.getByLabelText(/Required critical categories/) as HTMLInputElement;
  for (const name of SECTION_26_CATEGORIES) expect(categories.value).toContain(name);
  expect(
    (screen.getByLabelText(/Minimum independent tasks(?! per)/) as HTMLInputElement).value,
  ).toBe("1000");
  expect((screen.getByLabelText(/Minimum critical scenarios/) as HTMLInputElement).value).toBe(
    "100",
  );
  expect(findingCodes()).toEqual(
    expect.arrayContaining(["insufficient_independent_tasks", "insufficient_critical_scenarios"]),
  );
  expect(lockButton().disabled).toBe(true);
});

test("[NEG-UI] a final plan cannot be edited under the floors or without a Section 26 category", async () => {
  renderUI();
  await load(goodCases(), { purpose: "final" });
  setInput(/Minimum independent tasks(?! per)/, "1");
  setInput(/Minimum critical scenarios/, "0");
  expect(screen.getByTestId("suite-lock-findings").textContent).toContain("must be at least 1000");
  expect(screen.getByTestId("suite-lock-findings").textContent).toContain("must be at least 100");
  setInput(/Required critical categories/, "refunds");
  expect(findingCodes().filter((c) => c === "missing_critical_category").length).toBeGreaterThan(
    SECTION_26_CATEGORIES.length - 1,
  );
  expect(lockButton().disabled).toBe(true);
});

test("[NEG-UI] the 201st category is shown as a finding, never silently dropped", async () => {
  renderUI();
  await load(goodCases());
  fillPlan({
    ...GOOD_PLAN,
    categories: Array.from({ length: MAX_PLAN_CATEGORIES + 1 }, (_, i) => `c${i}`).join(", "),
  });
  expect(findingCodes()).toContain("sample_plan_missing");
  expect(lockButton().disabled).toBe(true);
  expect(calls("POST", LOCK_URL).length).toBe(0);
});

test("[I18N] ar renders rtl, localized findings, localized purpose and status and no raw enum", async () => {
  const { container } = renderUI("ar");
  enqueueLoad(goodCases(), { purpose: "final" });
  fireEvent.click(screen.getByRole("button", { name: SUITE_LOCK_STRINGS.loadButton.ar }));
  await waitFor(() => expect(screen.getByTestId("suite-lock-summary")).toBeTruthy());
  const root = container.querySelector("section[lang]")!;
  expect(root.getAttribute("dir")).toBe("rtl");
  expect(root.getAttribute("lang")).toBe("ar");
  const summary = screen.getByTestId("suite-lock-summary").textContent ?? "";
  expect(summary).toContain(SUITE_LOCK_STRINGS.purposeFinal.ar);
  expect(summary).toContain(SUITE_LOCK_STRINGS.statusDraft.ar);
  expect(summary).not.toMatch(/\bfinal\b|\bdraft\b/);
  const findings = screen.getByTestId("suite-lock-findings").textContent ?? "";
  expect(findings).toContain("مهمة مستقلة");
  expect(findings).not.toContain("insufficient_independent_tasks");
  expect(findings).not.toContain("min_independent_tasks");
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
  // every code the server can answer with has its own sentence in both languages
  for (const code of FINDING_CODES) {
    expect(SUITE_LOCK_STRING_KEYS as readonly string[], code).toContain(`fCode_${code}`);
  }
});

test("[UI] Lock opens the UX01 confirmation dialog: no POST until Confirm; Cancel and Escape send nothing and return focus", async () => {
  renderUI();
  await load(goodCases());
  fillPlan(GOOD_PLAN);
  expect(screen.queryByRole("alertdialog")).toBeNull();
  const opener = lockButton();
  opener.focus();
  fireEvent.click(opener);
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog.textContent).toContain("permanent");
  expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
  expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
  expect(calls("POST", LOCK_URL).length).toBe(0);
  fireEvent.click(screen.getByTestId("suite-lock-cancel-button"));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(document.activeElement).toBe(opener);
  expect(calls("POST", LOCK_URL).length).toBe(0);
  // Escape closes it too and focus returns to the opener
  fireEvent.click(opener);
  const second = await screen.findByRole("alertdialog");
  fireEvent.keyDown(second, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(document.activeElement).toBe(opener);
  expect(calls("POST", LOCK_URL).length).toBe(0);
  // editing the plan so it no longer passes withdraws a pending confirmation
  fireEvent.click(opener);
  await screen.findByRole("alertdialog");
  setInput(/Minimum independent tasks(?! per)/, "999");
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(calls("POST", LOCK_URL).length).toBe(0);
});

test("[UI] a failed lock moves focus to the announced error", async () => {
  await lockWith(fail(500, { detail: "boom" }));
  await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("suite-lock-error")));
  expect(screen.getByTestId("suite-lock-error").getAttribute("role")).toBe("alert");
});
