// @vitest-environment jsdom
//
// FTF-UX04 (UI CHECKLIST f) -- the real project form (FTF-0401) and the real requirements panel
// (FTF-0408) against the REAL routers (build_project_router, build_budget_policy_router,
// build_critical_behavior_requirement_router) on PostgreSQL 16 as sovra_app. Nothing about the
// server is mocked. The only test double is the transport wrapper below, which adds the test
// principal header the routers' principal_resolver reads, in place of the OIDC session cookie the
// product will use.
//
// Run by tests/integration/test_ftf_ux04_real_api.py (which seeds the organizations, workspaces,
// projects and tools, passes them in the environment and then verifies the database independently);
// not part of the default vitest include.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  ProjectSetupForm,
  type ProjectRecord,
} from "../../components/project-mandate/project_schema_api_ui";
import { RequirementsPanel } from "../../components/project-mandate/requirements_ui";

// No @types/node in this workspace (a new dependency is an owner ask): read the environment the
// Python harness passes in through the runtime global.
const ENV = (globalThis as unknown as { process: { env: Record<string, string | undefined> } })
  .process.env;
function required(name: string): string {
  const value = ENV[name];
  if (!value)
    throw new Error(`${name} is required (run via tests/integration/test_ftf_ux04_real_api.py)`);
  return value;
}

interface Seeds {
  org1: string;
  org2: string;
  ws1: string;
  ws2: string;
  /** Project ids by scenario, all in org1 unless named otherwise. */
  edit: string;
  stale: string;
  draft: string;
  role: string;
  budget: string;
  budgetStale: string;
  budgetRole: string;
  foreign: string; // a project of org2
  requirements: string;
}
const API = required("UX04_API_BASE");
const SEEDS = JSON.parse(required("UX04_SEEDS")) as Seeds;
const ADMIN = required("UX04_PRINCIPAL_ADMIN");
const EVALUATOR = required("UX04_PRINCIPAL_EVALUATOR");
const ORG2_ADMIN = required("UX04_PRINCIPAL_ORG2_ADMIN");

interface Call {
  method: string;
  path: string;
  status: number;
}
let calls: Call[] = [];
let principal = ADMIN;
const realFetch = globalThis.fetch;

beforeEach(() => {
  calls = [];
  principal = ADMIN;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    headers.set("X-Test-Principal", principal);
    const response = await realFetch(url, { ...init, headers });
    calls.push({
      method: init.method ?? "GET",
      path: new URL(url).pathname,
      status: response.status,
    });
    return response;
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const projectsPath = (org: string) => `/api/v1/organizations/${org}/projects`;
const itemPath = (org: string, id: string) => `${projectsPath(org)}/${id}`;
const budgetPath = (org: string, id: string) => `${itemPath(org, id)}/budget-policy`;

/** A direct API call as another actor, outside the component (the competing editor). */
async function direct(
  method: string,
  path: string,
  who: string,
  body?: unknown,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): Promise<{ status: number; json: Record<string, any> }> {
  const response = await realFetch(`${API}${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-Test-Principal": who },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: response.status, json: await response.json().catch(() => null) };
}
async function load(org: string, id: string, who = ADMIN): Promise<ProjectRecord> {
  const { status, json } = await direct("GET", itemPath(org, id), who);
  expect(status).toBe(200);
  return json as ProjectRecord;
}
const tid = (id: string) => screen.getByTestId(id);
const text = (id: string) => screen.getByTestId(id).textContent ?? "";
const type = (id: string, value: string) => fireEvent.change(tid(id), { target: { value } });
const click = (id: string) => fireEvent.click(tid(id));
const writes = () => calls.filter((c) => c.method !== "GET");

function renderForm(extra: Record<string, unknown> = {}) {
  return render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API,
      organizationId: SEEDS.org1,
      workspaceId: SEEDS.ws1,
      ...extra,
    } as never),
  );
}
function renderPanel(projectId: string, extra: Record<string, unknown> = {}) {
  return render(
    createElement(RequirementsPanel, {
      apiBaseUrl: API,
      organizationId: SEEDS.org1,
      projectId,
      securityProfile: "standard",
      ...extra,
    } as never),
  );
}

describe("FTF-0401 project form against the real router", () => {
  test("R1 create a generic project: 201, bound, 'Project created'", async () => {
    renderForm();
    type("project-name-input", "ux04-r1-generic");
    type("project-description-input", "made by the UI");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Project created"));
    expect(calls).toEqual([{ method: "POST", path: projectsPath(SEEDS.org1), status: 201 }]);
  });

  test("R2 create an agentic project, then the next save PATCHes it (no duplicate create)", async () => {
    const saved: ProjectRecord[] = [];
    renderForm({ onSaved: (p: ProjectRecord) => saved.push(p) });
    type("project-name-input", "ux04-r2-agentic");
    type("project-type-select", "agentic_product");
    type("agent-objective-input", "Triage support tickets.");
    type("security-profile-input", "zero_egress");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Project created"));
    expect(saved).toHaveLength(1);
    expect(saved[0]!.status).toBe("setup_incomplete");
    expect(saved[0]!.security_profile).toBe("zero_egress");
    type("project-name-input", "ux04-r2-agentic-renamed");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Changes saved"));
    expect(calls.map((c) => `${c.method} ${c.status}`)).toEqual(["POST 201", "PATCH 200"]);
    expect(calls[1]!.path).toBe(itemPath(SEEDS.org1, saved[0]!.id));
  });

  test("R3 edit: save, then CLEAR the description (sent as an empty string, stored as one), version +1 each time", async () => {
    const record = await load(SEEDS.org1, SEEDS.edit);
    renderForm({ project: record });
    type("project-description-input", "first description");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Changes saved"));
    type("project-description-input", "");
    click("project-submit-button");
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    await waitFor(() => expect(tid("project-submit-button")).toBeTruthy());
    const after = await load(SEEDS.org1, SEEDS.edit);
    expect(after.description).toBe("");
    expect(after.row_version).toBe(record.row_version + 2);
    expect(calls.every((c) => c.status === 200)).toBe(true);
  });

  test("R4 stale edit: another editor saved first -> 409, the sentence, the entries kept, nothing overwritten", async () => {
    const record = await load(SEEDS.org1, SEEDS.stale);
    const other = await direct("PATCH", itemPath(SEEDS.org1, SEEDS.stale), ADMIN, {
      name: "ux04-r4-by-other-editor",
      expected_row_version: record.row_version,
    });
    expect(other.status).toBe(200);
    renderForm({ project: record });
    type("project-name-input", "ux04-r4-by-the-ui");
    click("project-submit-button");
    await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
    expect(text("project-form-error")).toContain("changed since you opened it");
    expect(text("project-form-error")).not.toContain("row_version");
    expect((tid("project-name-input") as HTMLInputElement).value).toBe("ux04-r4-by-the-ui");
    expect(calls).toEqual([
      { method: "PATCH", path: itemPath(SEEDS.org1, SEEDS.stale), status: 409 },
    ]);
    expect((await load(SEEDS.org1, SEEDS.stale)).name).toBe("ux04-r4-by-other-editor");
  });

  test("R5 draft: saved through the real route, the version unchanged, offered back on the next open and restorable", async () => {
    const record = await load(SEEDS.org1, SEEDS.draft);
    renderForm({ project: record });
    type("project-name-input", "ux04-r5-work-in-progress");
    type("project-description-input", "half-written notes");
    click("project-save-draft-button");
    await waitFor(() => expect(text("project-draft-saved-notice")).toBe("Draft saved"));
    expect(calls).toEqual([
      { method: "POST", path: `${itemPath(SEEDS.org1, SEEDS.draft)}/ui-state`, status: 200 },
    ]);
    cleanup();
    const reopened = await load(SEEDS.org1, SEEDS.draft);
    expect(reopened.row_version).toBe(record.row_version);
    expect(reopened.name).toBe(record.name);
    renderForm({ project: reopened });
    expect(tid("project-draft-offer")).toBeTruthy();
    expect((tid("project-name-input") as HTMLInputElement).value).toBe(record.name);
    click("project-restore-draft-button");
    expect((tid("project-name-input") as HTMLInputElement).value).toBe("ux04-r5-work-in-progress");
    expect((tid("project-description-input") as HTMLTextAreaElement).value).toBe(
      "half-written notes",
    );
  });

  test("R6 a read-only role cannot save: 403 -> the access sentence, nothing changed", async () => {
    const record = await load(SEEDS.org1, SEEDS.role, EVALUATOR);
    principal = EVALUATOR;
    renderForm({ project: record });
    type("project-name-input", "ux04-r6-should-not-land");
    click("project-submit-button");
    await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
    expect(text("project-form-error")).toContain("do not have access");
    expect(calls).toEqual([
      { method: "PATCH", path: itemPath(SEEDS.org1, SEEDS.role), status: 403 },
    ]);
    expect((await load(SEEDS.org1, SEEDS.role)).name).toBe(record.name);
  });

  test("R7 another organization's project through this organization's path: 404 shown, nothing written", async () => {
    const foreign = await load(SEEDS.org2, SEEDS.foreign, ORG2_ADMIN);
    renderForm({ project: foreign });
    type("project-name-input", "ux04-r7-cross-tenant");
    click("project-submit-button");
    await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
    expect(text("project-form-error")).toContain("no longer exists, or you do not have access");
    expect(calls).toEqual([
      { method: "PATCH", path: itemPath(SEEDS.org1, SEEDS.foreign), status: 404 },
    ]);
    expect((await load(SEEDS.org2, SEEDS.foreign, ORG2_ADMIN)).name).toBe(foreign.name);
  });

  test("R8 a profile less strict than the organization's default: the server refuses (400), the sentence names why, no project is created", async () => {
    principal = ORG2_ADMIN;
    render(
      createElement(ProjectSetupForm, {
        apiBaseUrl: API,
        organizationId: SEEDS.org2,
        workspaceId: SEEDS.ws2,
      }),
    );
    type("project-name-input", "ux04-r8-relaxed");
    // org2's default is zero_egress; the form's default is "standard"
    click("project-submit-button");
    await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
    expect(text("project-form-error")).toContain("not less strict than your organization");
    expect(calls).toEqual([{ method: "POST", path: projectsPath(SEEDS.org2), status: 400 }]);
    type("security-profile-input", "zero_egress");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Project created"));
    expect(calls.map((c) => c.status)).toEqual([400, 201]);
  });
});

describe("FTF-0408 requirements panel against the real routers", () => {
  test("R9 the real /requirements view: rows, states in words, no invented conflict", async () => {
    renderPanel(SEEDS.requirements);
    await waitFor(() => expect(screen.getByTestId("requirement-list")).toBeTruthy());
    const rows = Array.from(tid("requirement-list").querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(2);
    const byName = Object.fromEntries(
      rows.map((r) => [r.querySelector("bdi")!.textContent, r.textContent ?? ""]),
    );
    expect(byName["issue_refund"]).toContain("Critical");
    expect(byName["issue_refund"]).toContain("Enforced");
    expect(byName["lookup_order"]).toContain("Non-critical");
    expect(byName["lookup_order"]).toContain("Not enforced");
    expect(screen.queryAllByTestId(/^requirement-conflict-/)).toHaveLength(0);
    // no policy yet for this project: the create state, from the router's real 404
    await waitFor(() => expect(screen.getByTestId("budget-policy-missing-notice")).toBeTruthy());
    expect(calls.map((c) => `${c.method} ${c.status}`).sort()).toEqual(["GET 200", "GET 404"]);
  });

  test("R10 create then revise a budget policy: bound replies, versions 1 then 2, exact amounts", async () => {
    renderPanel(SEEDS.budget);
    await waitFor(() => expect(screen.getByTestId("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", "2500.50");
    type("budget-currency-input", "SAR");
    click("budget-scope-training");
    click("budget-scope-evaluation");
    click("budget-policy-submit-button");
    await waitFor(() => expect(text("budget-policy-saved-notice")).toBe("Budget policy created"));
    expect(text("budget-row-version")).toBe("1");
    expect(text("budget-available")).toBe("2500.5000");
    type("budget-total-amount-input", "3000");
    click("budget-scope-serving");
    click("budget-policy-submit-button");
    await waitFor(() => expect(text("budget-row-version")).toBe("2"));
    expect(text("budget-available")).toBe("3000.0000");
    expect(writes().map((c) => `${c.method} ${c.status}`)).toEqual(["POST 201", "PATCH 200"]);
    const fetched = await direct("GET", budgetPath(SEEDS.org1, SEEDS.budget), ADMIN);
    expect(fetched.json.total_amount).toBe("3000.0000");
    expect(fetched.json.scopes).toEqual(["evaluation", "serving", "training"]);
    expect(fetched.json.currency).toBe("SAR");
  });

  test("R11 a total below the reserved amount is stopped before any request, with the minimum named; a stale revise is a 409", async () => {
    const create = await direct("POST", budgetPath(SEEDS.org1, SEEDS.budgetStale), ADMIN, {
      total_amount: "1000",
      currency: "USD",
      scopes: ["training"],
    });
    expect(create.status).toBe(201);
    const reserve = await direct(
      "POST",
      `${budgetPath(SEEDS.org1, SEEDS.budgetStale)}/reserve`,
      ADMIN,
      { amount: "400", scope: "training" },
    );
    expect(reserve.status).toBe(200);
    const version = reserve.json.row_version as number;
    renderPanel(SEEDS.budgetStale);
    await waitFor(() => expect(text("budget-row-version")).toBe(String(version)));
    expect(text("budget-reserved")).toBe("400.0000");
    calls = [];
    type("budget-total-amount-input", "399.99");
    click("budget-policy-submit-button");
    expect(document.getElementById("budget-total-amount-error")!.textContent).toContain(
      "less than the amount already reserved (400.0000 USD)",
    );
    expect(writes()).toHaveLength(0);
    // another editor revises first; the UI's version is now stale
    const other = await direct("PATCH", budgetPath(SEEDS.org1, SEEDS.budgetStale), ADMIN, {
      total_amount: "1200",
      expected_row_version: version,
    });
    expect(other.status).toBe(200);
    type("budget-total-amount-input", "500");
    click("budget-policy-submit-button");
    await waitFor(() => expect(screen.getByTestId("budget-policy-error")).toBeTruthy());
    expect(text("budget-policy-error")).toContain("changed since you opened it");
    expect((tid("budget-total-amount-input") as HTMLInputElement).value).toBe("500");
    expect(writes().map((c) => c.status)).toEqual([409]);
    const stored = await direct("GET", budgetPath(SEEDS.org1, SEEDS.budgetStale), ADMIN);
    expect(stored.json.total_amount).toBe("1200.0000");
  });

  test("R12 a read-only role cannot create: 403 -> the access sentence, no policy stored", async () => {
    principal = EVALUATOR;
    renderPanel(SEEDS.budgetRole);
    await waitFor(() => expect(screen.getByTestId("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", "10");
    click("budget-scope-training");
    click("budget-policy-submit-button");
    await waitFor(() => expect(screen.getByTestId("budget-policy-error")).toBeTruthy());
    expect(text("budget-policy-error")).toContain("do not have access");
    expect(writes().map((c) => c.status)).toEqual([403]);
  });

  test("R13 another organization's project: the router's 404 becomes the create state, and creating is refused (404 shown), nothing stored", async () => {
    renderPanel(SEEDS.foreign);
    await waitFor(() => expect(screen.getByTestId("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", "10");
    click("budget-scope-training");
    click("budget-policy-submit-button");
    await waitFor(() => expect(screen.getByTestId("budget-policy-error")).toBeTruthy());
    expect(text("budget-policy-error")).toContain("project was not found");
    expect(writes().map((c) => c.status)).toEqual([404]);
  });
});
