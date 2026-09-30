// @vitest-environment jsdom
//
// FTF-0408 -- Requirements review panel.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `RequirementsPanel`, drives real user
// interaction and asserts on the real `fetch` calls it makes against the
// live FTF-0407/FTF-0403 API contracts this task assembles -- request
// method, URL and JSON body -- never a snapshot or hand-written HTML
// string. `fetch` itself is mocked (no live server in a unit test), but
// the component code path exercised is the real one that would hit the
// actual backend.
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E (no Playwright here -- the same precedent `ftf_0401.spec.ts`
// already established).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  BudgetPolicyRecord,
  CriticalBehaviorRequirementView,
  RequirementsPanel,
  budgetPolicyUrl,
  requirementsUrl,
} from "../components/project-mandate/requirements_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";

function requirement(
  overrides: Partial<CriticalBehaviorRequirementView> = {},
): CriticalBehaviorRequirementView {
  return {
    tool_definition_id: "tool-1",
    name: "issue_refund",
    side_effect_class: "financial",
    is_critical: true,
    requires_confirmation: true,
    measurable: true,
    runtime_enforced: true,
    ...overrides,
  };
}

function budgetPolicy(overrides: Partial<BudgetPolicyRecord> = {}): BudgetPolicyRecord {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    organization_id: ORG_ID,
    project_id: PROJECT_ID,
    total_amount: "1000.0000",
    currency: "USD",
    scopes: ["training"],
    reserved_amount: "0.0000",
    available_amount: "1000.0000",
    row_version: 2,
    created_by: "user-1",
    created_at: "2026-09-01T00:00:00+00:00",
    updated_at: "2026-09-01T00:00:00+00:00",
    ...overrides,
  };
}

const REQ_URL = requirementsUrl(API_BASE, ORG_ID, PROJECT_ID);
const BUDGET_URL = budgetPolicyUrl(API_BASE, ORG_ID, PROJECT_ID);

function mockFetch(handlers: {
  requirements?: () => { ok: boolean; status?: number; json: () => Promise<unknown> };
  budgetGet?: () => { ok: boolean; status?: number; json: () => Promise<unknown> };
  onBudgetWrite?: (init: RequestInit) => {
    ok: boolean;
    status?: number;
    json: () => Promise<unknown>;
  };
}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url === REQ_URL) {
      return handlers.requirements ? handlers.requirements() : { ok: true, json: async () => [] };
    }
    if (url === BUDGET_URL) {
      if (!init || init.method === undefined) {
        return handlers.budgetGet
          ? handlers.budgetGet()
          : { ok: false, status: 404, json: async () => ({ detail: "not_found" }) };
      }
      if (handlers.onBudgetWrite) {
        return handlers.onBudgetWrite(init);
      }
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("URL helpers point at the real FTF-0407/FTF-0403 API contracts", () => {
  expect(requirementsUrl(API_BASE, ORG_ID, PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}/requirements`,
  );
  expect(budgetPolicyUrl(API_BASE, ORG_ID, PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}/budget-policy`,
  );
});

test("security profile is displayed read-only, with no edit control here", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      requirements: () => ({ ok: true, json: async () => [] }),
      budgetGet: () => ({ ok: false, status: 404, json: async () => ({ detail: "not_found" }) }),
    }),
  );

  render(
    createElement(RequirementsPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      securityProfile: "restricted",
    }),
  );

  await waitFor(() =>
    expect(screen.getByTestId("security-profile-value").textContent).toBe("restricted"),
  );
  expect(screen.queryByRole("textbox", { name: /security/i })).toBeNull();
});

test("a tool declared critical/requires-confirmation but not runtime-enforced surfaces a real conflict, never silently resolved", async () => {
  const enforced = requirement({ tool_definition_id: "tool-enforced" });
  const conflicted = requirement({
    tool_definition_id: "tool-conflict",
    name: "delete_account",
    runtime_enforced: false,
  });

  vi.stubGlobal(
    "fetch",
    mockFetch({
      requirements: () => ({ ok: true, json: async () => [enforced, conflicted] }),
      budgetGet: () => ({ ok: false, status: 404, json: async () => ({ detail: "not_found" }) }),
    }),
  );

  render(
    createElement(RequirementsPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      securityProfile: "standard",
    }),
  );

  await waitFor(() => expect(screen.getByTestId("requirement-row-tool-enforced")).toBeTruthy());
  expect(screen.queryByTestId("requirement-conflict-tool-enforced")).toBeNull();
  expect(screen.getByTestId("requirement-conflict-tool-conflict")).toBeTruthy();
});

test("a real requirements-fetch failure surfaces the server error, not a silent empty list", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      requirements: () => ({
        ok: false,
        status: 500,
        json: async () => ({ detail: "internal error" }),
      }),
      budgetGet: () => ({ ok: false, status: 404, json: async () => ({ detail: "not_found" }) }),
    }),
  );

  render(
    createElement(RequirementsPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      securityProfile: "standard",
    }),
  );

  await waitFor(() =>
    expect(screen.getByTestId("requirements-error").textContent).toBe("internal error"),
  );
});

test("no budget policy yet: shows the missing notice and an enabled currency field for creation", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      requirements: () => ({ ok: true, json: async () => [] }),
      budgetGet: () => ({ ok: false, status: 404, json: async () => ({ detail: "not_found" }) }),
    }),
  );

  render(
    createElement(RequirementsPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      securityProfile: "standard",
    }),
  );

  await waitFor(() => expect(screen.getByTestId("budget-policy-missing-notice")).toBeTruthy());
  const currencyInput = screen.getByTestId("budget-currency-input") as HTMLInputElement;
  expect(currencyInput.disabled).toBe(false);
  expect(screen.getByTestId("budget-policy-submit-button").textContent).toBe(
    "Create budget policy",
  );
});

test("creating a budget policy POSTs total_amount/currency/scopes for real", async () => {
  const created = budgetPolicy();
  const fetchMock = mockFetch({
    requirements: () => ({ ok: true, json: async () => [] }),
    budgetGet: () => ({ ok: false, status: 404, json: async () => ({ detail: "not_found" }) }),
    onBudgetWrite: (init) => {
      expect(init.method).toBe("POST");
      return { ok: true, json: async () => created };
    },
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    createElement(RequirementsPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      securityProfile: "standard",
    }),
  );

  await waitFor(() => expect(screen.getByTestId("budget-policy-missing-notice")).toBeTruthy());

  fireEvent.change(screen.getByTestId("budget-total-amount-input"), {
    target: { value: "1000.00" },
  });
  fireEvent.click(screen.getByTestId("budget-scope-training"));
  fireEvent.click(screen.getByTestId("budget-policy-submit-button"));

  await waitFor(() => {
    const writeCall = fetchMock.mock.calls.find(
      (call) => call[0] === BUDGET_URL && call[1]?.method === "POST",
    );
    expect(writeCall).toBeTruthy();
    const body = JSON.parse(writeCall![1]!.body as string);
    expect(body).toEqual({ total_amount: "1000.00", currency: "USD", scopes: ["training"] });
  });
  await waitFor(() => expect(screen.queryByTestId("budget-policy-missing-notice")).toBeNull());
});

test("existing budget policy: currency is disabled, and revising PATCHes total_amount/scopes/expected_row_version, never currency", async () => {
  const existing = budgetPolicy({ row_version: 3, scopes: ["training"] });
  const revised = { ...existing, row_version: 4, scopes: ["training", "evaluation"] };
  const fetchMock = mockFetch({
    requirements: () => ({ ok: true, json: async () => [] }),
    budgetGet: () => ({ ok: true, json: async () => existing }),
    onBudgetWrite: (init) => {
      expect(init.method).toBe("PATCH");
      return { ok: true, json: async () => revised };
    },
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    createElement(RequirementsPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      securityProfile: "standard",
    }),
  );

  await waitFor(() => expect(screen.getByTestId("budget-row-version").textContent).toBe("3"));
  const currencyInput = screen.getByTestId("budget-currency-input") as HTMLInputElement;
  expect(currencyInput.disabled).toBe(true);

  fireEvent.click(screen.getByTestId("budget-scope-evaluation"));
  fireEvent.click(screen.getByTestId("budget-policy-submit-button"));

  await waitFor(() => {
    const writeCall = fetchMock.mock.calls.find(
      (call) => call[0] === BUDGET_URL && call[1]?.method === "PATCH",
    );
    expect(writeCall).toBeTruthy();
    const body = JSON.parse(writeCall![1]!.body as string);
    expect(body.expected_row_version).toBe(3);
    expect(Object.prototype.hasOwnProperty.call(body, "currency")).toBe(false);
    expect(new Set(body.scopes)).toEqual(new Set(["training", "evaluation"]));
  });
  await waitFor(() => expect(screen.getByTestId("budget-row-version").textContent).toBe("4"));
});

test("a real 409 concurrency conflict surfaces the server error and preserves the user's own draft, never silently reverting it", async () => {
  const existing = budgetPolicy({ row_version: 5, total_amount: "500.0000" });
  const fetchMock = mockFetch({
    requirements: () => ({ ok: true, json: async () => [] }),
    budgetGet: () => ({ ok: true, json: async () => existing }),
    onBudgetWrite: () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: "expected_row_version mismatch" }),
    }),
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    createElement(RequirementsPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      securityProfile: "standard",
    }),
  );

  await waitFor(() => expect(screen.getByTestId("budget-row-version").textContent).toBe("5"));

  fireEvent.change(screen.getByTestId("budget-total-amount-input"), {
    target: { value: "999.00" },
  });
  fireEvent.click(screen.getByTestId("budget-policy-submit-button"));

  await waitFor(() =>
    expect(screen.getByTestId("budget-policy-error").textContent).toBe(
      "expected_row_version mismatch",
    ),
  );
  // The draft the user typed is preserved, not reverted back to "500.0000".
  expect((screen.getByTestId("budget-total-amount-input") as HTMLInputElement).value).toBe(
    "999.00",
  );
});
