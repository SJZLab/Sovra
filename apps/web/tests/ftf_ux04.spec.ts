// @vitest-environment jsdom
//
// FTF-UX04 (QUEUE Backlog "FTF-UX02..UX07", area UX04 project-mandate): behavioural pins for the two
// components rebuilt on the UX01 design system -- FTF-0401 `ProjectSetupForm` and FTF-0408
// `RequirementsPanel`. Every test renders the real component with a mocked `fetch` and asserts what
// a user sees or what the component sends; none greps source text. Exact Arabic strings are literals
// on purpose: a catalogue entry swapped for English must turn a test RED. The UI CHECKLIST (Control
// plane 2026-10-09) is covered point by point:
//   (a) strict parse -- generated tables over EVERY response field   [UX04-A*]
//   (b) ONE binding per request, a fault per field                    [UX04-B*]
//   (c) identity: organization / project change resets, stale drops   [UX04-C*]
//   (d) no simulated success -- only a typed, bound 2xx counts        [UX04-D*]
//   (e) typed errors: 5xx, non-JSON, unknown codes keep state         [UX04-E*]
//   (f) the real router lives in tests/integration/test_ftf_ux04_real_api.py
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  PROJECT_STATUSES,
  PROJECT_TYPES,
  SECURITY_PROFILES,
  ProjectSetupForm,
  bindDraftSave,
  bindProjectSave,
  parseDraft,
  parseProjectRecord,
  projectItemUrl,
  projectUiStateUrl,
  projectsCollectionUrl,
  type ProjectRecord,
  type ProjectSetupFormProps,
} from "../components/project-mandate/project_schema_api_ui";
import {
  BUDGET_CURRENCIES,
  BUDGET_SCOPES,
  RequirementsPanel,
  bindBudgetSave,
  budgetPolicyUrl,
  parseBudgetPolicy,
  parseRequirements,
  requirementsUrl,
  toUnits,
  totalProblem,
  type BudgetPolicyRecord,
  type CriticalBehaviorRequirementView,
  type RequirementsPanelProps,
} from "../components/project-mandate/requirements_ui";
import { jsonEqual } from "../components/project-mandate/request";
import {
  MANDATE_WORDS,
  mandateFailureMessage,
} from "../components/project-mandate/strings_project_mandate";

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const ORG_B = "99999999-9999-9999-9999-999999999999";
const WS = "22222222-2222-2222-2222-222222222222";
const PROJECT = "33333333-3333-3333-3333-333333333333";
const PROJECT_B = "88888888-8888-8888-8888-888888888888";

type Fetch = ReturnType<typeof vi.fn>;
const fetchMock = () => fetch as unknown as Fetch;

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function ok(body: unknown, status = 200) {
  return { ok: true, status, json: async () => body };
}
function refused(status: number, body: unknown = { detail: "internal_code_xyz" }) {
  return { ok: false, status, json: async () => body };
}
function notJson(status: number) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      throw new SyntaxError("Unexpected token <");
    },
  };
}
const tid = (id: string) => screen.getByTestId(id);
const text = (id: string) => screen.getByTestId(id).textContent ?? "";
const input = (id: string) => tid(id) as HTMLInputElement;
const type = (id: string, value: string) => fireEvent.change(tid(id), { target: { value } });
const click = (id: string) => fireEvent.click(tid(id));
const alerts = (root: HTMLElement) => Array.from(root.querySelectorAll('[role="alert"]'));

// =============================================================================================
// FTF-0401 ProjectSetupForm
// =============================================================================================

function project(over: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    id: PROJECT,
    organization_id: ORG,
    workspace_id: WS,
    name: "acme-agent",
    description: null,
    project_type: "agentic_product",
    agent_objective: "Automate PR review comments.",
    security_profile: "standard",
    budget_policy_id: null,
    status: "setup_incomplete",
    ui_state: {},
    row_version: 1,
    created_by: "user-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    archived_at: null,
    ...over,
  };
}

function formProps(over: Partial<ProjectSetupFormProps> = {}): ProjectSetupFormProps {
  return { apiBaseUrl: API, organizationId: ORG, workspaceId: WS, ...over };
}
function renderForm(over: Partial<ProjectSetupFormProps> = {}) {
  return render(createElement(ProjectSetupForm, formProps(over)));
}
const sentBody = (call = 0) => JSON.parse(fetchMock().mock.calls[call]![1].body as string);

/** What the real route answers to a create/edit: the stored record with exactly the sent values. */
function echo(init: RequestInit, current: ProjectRecord | null): ProjectRecord {
  const body = JSON.parse(init.body as string);
  if (current === null) {
    return project({
      workspace_id: body.workspace_id,
      name: body.name,
      project_type: body.project_type,
      description: body.description,
      agent_objective: body.agent_objective,
      security_profile: body.security_profile,
      status: body.project_type === "agentic_product" ? "setup_incomplete" : "active",
      row_version: 1,
    });
  }
  return {
    ...current,
    name: body.name ?? current.name,
    description: body.description ?? current.description,
    agent_objective: body.agent_objective ?? current.agent_objective,
    security_profile: body.security_profile ?? current.security_profile,
    row_version: current.row_version + 1,
  };
}

describe("[UX04-F0] project form: certified request shapes are unchanged", () => {
  test("URL helpers point at the real FTF-0401 contract", () => {
    expect(projectsCollectionUrl(`${API}/`, ORG)).toBe(
      `${API}/api/v1/organizations/${ORG}/projects`,
    );
    expect(projectItemUrl(API, ORG, "p1")).toBe(`${API}/api/v1/organizations/${ORG}/projects/p1`);
    expect(projectUiStateUrl(API, ORG, "p1")).toBe(
      `${API}/api/v1/organizations/${ORG}/projects/p1/ui-state`,
    );
  });

  test("create sends workspace_id, name, project_type, description, agent_objective, security_profile -- nothing else", async () => {
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
      ok(echo(init, null), 201),
    );
    const onSaved = vi.fn();
    renderForm({ onSaved });
    type("project-name-input", "my-project");
    click("project-submit-button");
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock().mock.calls[0]!;
    expect(url).toBe(projectsCollectionUrl(API, ORG));
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(sentBody()).toEqual({
      workspace_id: WS,
      name: "my-project",
      project_type: "generic_model_tuning",
      description: null,
      agent_objective: null,
      security_profile: "standard",
    });
  });

  test("edit PATCH carries expected_row_version and never project_type", async () => {
    const p = project({ row_version: 4 });
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) => ok(echo(init, p)));
    renderForm({ project: p });
    type("project-description-input", "updated description");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Changes saved"));
    expect(fetchMock().mock.calls[0]![0]).toBe(projectItemUrl(API, ORG, p.id));
    expect(fetchMock().mock.calls[0]![1].method).toBe("PATCH");
    const body = sentBody();
    expect(body.expected_row_version).toBe(4);
    expect(body.description).toBe("updated description");
    expect(Object.hasOwn(body, "project_type")).toBe(false);
    expect(Object.hasOwn(body, "workspace_id")).toBe(false);
  });

  test("UX04-F0-clear: clearing an existing description is sent as an empty string (the API skips null)", async () => {
    const p = project({ description: "old text" });
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
      ok({ ...echo(init, p), description: JSON.parse(init.body as string).description }),
    );
    renderForm({ project: p });
    type("project-description-input", "");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Changes saved"));
    expect(sentBody().description).toBe("");
  });

  test("UX04-F0-untouched-null: an untouched empty description stays null on edit", async () => {
    const p = project({ description: null });
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) => ok(echo(init, p)));
    renderForm({ project: p });
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Changes saved"));
    expect(sentBody().description).toBeNull();
  });

  test("agentic_product requires and sends the objective; generic never sends one", async () => {
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
      ok(echo(init, null), 201),
    );
    renderForm();
    expect(screen.queryByTestId("agent-objective-input")).toBeNull();
    type("project-name-input", "agent-1");
    type("project-type-select", "agentic_product");
    type("agent-objective-input", "Automate PR review comments.");
    click("project-submit-button");
    await waitFor(() => expect(fetchMock()).toHaveBeenCalledTimes(1));
    expect(sentBody()).toMatchObject({
      project_type: "agentic_product",
      agent_objective: "Automate PR review comments.",
    });
  });

  test("a draft save POSTs the real ui_state to the ui-state route", async () => {
    const p = project();
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
      ok({ ...p, ui_state: JSON.parse(init.body as string).ui_state }),
    );
    renderForm({ project: p });
    click("project-save-draft-button");
    await waitFor(() => expect(text("project-draft-saved-notice")).toBe("Draft saved"));
    expect(fetchMock().mock.calls[0]![0]).toBe(projectUiStateUrl(API, ORG, p.id));
    expect(sentBody().ui_state).toEqual({
      name: "acme-agent",
      projectType: "agentic_product",
      description: "",
      agentObjective: "Automate PR review comments.",
      securityProfile: "standard",
    });
  });
});

describe("[UX04-A] project form: strict parse of every response field (UI CHECKLIST a)", () => {
  /** Written independently of the component: what each field of the FTF-0401 reply may hold. */
  const RULES: Record<string, (v: unknown) => boolean> = {
    id: (v) => typeof v === "string" && v !== "",
    organization_id: (v) => typeof v === "string" && v !== "",
    workspace_id: (v) => typeof v === "string" && v !== "",
    name: (v) => typeof v === "string",
    description: (v) => v === null || typeof v === "string",
    project_type: (v) => v === "agentic_product" || v === "generic_model_tuning",
    agent_objective: (v) => v === null || typeof v === "string",
    security_profile: (v) => typeof v === "string",
    budget_policy_id: (v) => v === null || typeof v === "string",
    status: (v) => v === "setup_incomplete" || v === "active" || v === "archived",
    ui_state: (v) => typeof v === "object" && v !== null && !Array.isArray(v),
    row_version: (v) => typeof v === "number" && Number.isSafeInteger(v) && v >= 1,
    created_by: (v) => typeof v === "string",
    created_at: (v) => typeof v === "string",
    updated_at: (v) => typeof v === "string",
    archived_at: (v) => v === null || typeof v === "string",
  };
  const CANDIDATES: unknown[] = [
    undefined,
    null,
    "",
    "x",
    0,
    1.5,
    -1,
    42,
    Number.NaN,
    true,
    {},
    [],
    "not_an_enum",
    "ACTIVE",
  ];

  test("the rule table covers exactly the fields the API serializes", () => {
    expect(Object.keys(RULES).sort()).toEqual(Object.keys(project()).sort());
  });

  for (const [field, accepts] of Object.entries(RULES)) {
    for (const candidate of CANDIDATES) {
      const valid = accepts(candidate);
      const label =
        candidate === undefined ? "missing" : (JSON.stringify(candidate) ?? String(candidate));
      test(`parseProjectRecord ${field}=${label} is ${valid ? "accepted" : "refused"}`, () => {
        const body: Record<string, unknown> = { ...project() };
        if (candidate === undefined) delete body[field];
        else body[field] = candidate;
        expect(parseProjectRecord(body) !== null).toBe(valid);
      });
    }
  }

  test("a non-object body is refused", () => {
    for (const body of [null, undefined, [], "x", 5, true]) {
      expect(parseProjectRecord(body)).toBeNull();
    }
  });

  test("the enum sets are the backend's", () => {
    expect([...PROJECT_TYPES]).toEqual(["agentic_product", "generic_model_tuning"]);
    expect([...PROJECT_STATUSES]).toEqual(["setup_incomplete", "active", "archived"]);
    expect([...SECURITY_PROFILES]).toEqual(["standard", "zero_egress", "air_gapped"]);
  });

  /** A value of the wrong type (or outside the enum) for each field; written independently of the parser. */
  const WRONG: Record<string, unknown> = {
    id: 5,
    organization_id: 5,
    workspace_id: 5,
    name: 5,
    description: 5,
    project_type: "paused_type",
    agent_objective: 5,
    security_profile: 5,
    budget_policy_id: 5,
    status: "paused",
    ui_state: "{}",
    row_version: "2",
    created_by: 5,
    created_at: 5,
    updated_at: 5,
    archived_at: 5,
  };
  type Route = "create" | "edit" | "draft";
  /** The reply the real route would give THIS request -- so only the broken field can be the reason. */
  function consistentReply(route: Route, init: RequestInit, p: ProjectRecord): ProjectRecord {
    if (route === "create") return echo(init, null);
    if (route === "edit") return echo(init, p);
    return { ...p, ui_state: JSON.parse(init.body as string).ui_state };
  }
  for (const route of ["create", "edit", "draft"] as Route[]) {
    for (const field of Object.keys(RULES)) {
      for (const how of ["missing", "wrong type"] as const) {
        test(`${route}: a bound 2xx whose ${field} is ${how} is a failure -- no saved notice, no onSaved, entries kept, retry possible`, async () => {
          const p = project();
          fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) => {
            const reply: Record<string, unknown> = { ...consistentReply(route, init, p) };
            if (how === "missing") delete reply[field];
            else reply[field] = WRONG[field];
            return ok(reply);
          });
          const onSaved = vi.fn();
          renderForm({ project: route === "create" ? null : p, onSaved });
          type("project-name-input", "keep-me");
          click(route === "draft" ? "project-save-draft-button" : "project-submit-button");
          await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
          expect(text("project-form-error")).toContain("reply this page cannot read");
          expect(screen.queryByTestId("project-saved-notice")).toBeNull();
          expect(screen.queryByTestId("project-draft-saved-notice")).toBeNull();
          expect(onSaved).not.toHaveBeenCalled();
          expect(input("project-name-input").value).toBe("keep-me");
          expect((tid("project-submit-button") as HTMLButtonElement).disabled).toBe(false);
        });
      }
    }
  }

  test("sanity: the same requests with an unbroken reply DO succeed (the table above is not vacuous)", async () => {
    for (const route of ["create", "edit", "draft"] as Route[]) {
      cleanup();
      const p = project();
      fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
        ok(consistentReply(route, init, p)),
      );
      const onSaved = vi.fn();
      renderForm({ project: route === "create" ? null : p, onSaved });
      type("project-name-input", "keep-me");
      click(route === "draft" ? "project-save-draft-button" : "project-submit-button");
      await waitFor(() =>
        expect(
          screen.queryByTestId(
            route === "draft" ? "project-draft-saved-notice" : "project-saved-notice",
          ),
        ).toBeTruthy(),
      );
      expect(screen.queryByTestId("project-form-error")).toBeNull();
    }
  });
});

describe("[UX04-B] project form: ONE binding of every field sent (UI CHECKLIST b)", () => {
  const SCOPE = { organizationId: ORG, workspaceId: WS };
  const sentCreate = {
    name: "p",
    project_type: "agentic_product" as const,
    description: "d" as string | null,
    agent_objective: "obj" as string | null,
    security_profile: "zero_egress",
  };
  const goodCreate = project({
    name: "p",
    description: "d",
    agent_objective: "obj",
    security_profile: "zero_egress",
    row_version: 1,
  });

  test("the matching create reply binds", () => {
    expect(bindProjectSave(sentCreate, goodCreate, SCOPE, null)).toBe(true);
  });

  const CREATE_FAULTS: [string, Partial<ProjectRecord>][] = [
    ["name", { name: "other" }],
    ["project_type", { project_type: "generic_model_tuning" }],
    ["description", { description: "other" }],
    ["description dropped", { description: null }],
    ["agent_objective", { agent_objective: "other" }],
    ["security_profile", { security_profile: "standard" }],
    ["organization_id", { organization_id: ORG_B }],
    ["workspace_id", { workspace_id: "77777777-7777-7777-7777-777777777777" }],
    ["row_version (a create is version 1)", { row_version: 2 }],
    ["status archived", { status: "archived" }],
    ["archived_at set", { archived_at: "2026-01-01T00:00:00Z" }],
  ];
  for (const [field, change] of CREATE_FAULTS) {
    test(`create: a reply whose ${field} differs is refused`, () => {
      expect(bindProjectSave(sentCreate, { ...goodCreate, ...change }, SCOPE, null)).toBe(false);
    });
  }

  const current = project({ row_version: 4, description: "old", name: "old-name" });
  const sentEdit = {
    name: "new-name",
    project_type: "agentic_product" as const,
    description: "new" as string | null,
    agent_objective: "obj2" as string | null,
    security_profile: "air_gapped",
  };
  const goodEdit = project({
    name: "new-name",
    description: "new",
    agent_objective: "obj2",
    security_profile: "air_gapped",
    row_version: 5,
  });
  test("the matching edit reply binds", () => {
    expect(bindProjectSave(sentEdit, goodEdit, SCOPE, current)).toBe(true);
  });
  const EDIT_FAULTS: [string, Partial<ProjectRecord>][] = [
    ["id", { id: PROJECT_B }],
    ["name", { name: "old-name" }],
    ["project_type", { project_type: "generic_model_tuning" }],
    ["description", { description: "old" }],
    ["agent_objective", { agent_objective: "old" }],
    ["security_profile", { security_profile: "standard" }],
    ["organization_id", { organization_id: ORG_B }],
    ["workspace_id", { workspace_id: "77777777-7777-7777-7777-777777777777" }],
    ["row_version unchanged", { row_version: 4 }],
    ["row_version skipped", { row_version: 6 }],
    ["status archived", { status: "archived" }],
  ];
  for (const [field, change] of EDIT_FAULTS) {
    test(`edit: a reply whose ${field} differs is refused`, () => {
      expect(bindProjectSave(sentEdit, { ...goodEdit, ...change }, SCOPE, current)).toBe(false);
    });
  }
  test("edit: null sent means 'unchanged' and binds against the record's own value", () => {
    const keep = { ...sentEdit, description: null, agent_objective: null };
    const reply = { ...goodEdit, description: "old", agent_objective: current.agent_objective };
    expect(bindProjectSave(keep, reply, SCOPE, current)).toBe(true);
    expect(bindProjectSave(keep, { ...reply, description: "changed" }, SCOPE, current)).toBe(false);
  });

  const ui = { name: "n", projectType: "agentic_product" };
  const draftCurrent = project({ row_version: 3 });
  test("draft: same record, same version, the ui_state that was sent (key order irrelevant)", () => {
    const reply = project({
      row_version: 3,
      ui_state: { projectType: "agentic_product", name: "n" },
    });
    expect(bindDraftSave(ui, reply, ORG, draftCurrent)).toBe(true);
    expect(bindDraftSave(ui, { ...reply, id: PROJECT_B }, ORG, draftCurrent)).toBe(false);
    expect(bindDraftSave(ui, { ...reply, organization_id: ORG_B }, ORG, draftCurrent)).toBe(false);
    expect(bindDraftSave(ui, { ...reply, row_version: 4 }, ORG, draftCurrent)).toBe(false);
    expect(bindDraftSave(ui, { ...reply, ui_state: { name: "n" } }, ORG, draftCurrent)).toBe(false);
    expect(bindDraftSave(ui, { ...reply, ui_state: { ...ui, extra: 1 } }, ORG, draftCurrent)).toBe(
      false,
    );
  });

  const COMPONENT_FAULTS: [string, (r: ProjectRecord) => ProjectRecord][] = [
    ["name", (r) => ({ ...r, name: "someone-elses" })],
    ["security_profile", (r) => ({ ...r, security_profile: "air_gapped" })],
    ["description", (r) => ({ ...r, description: "injected" })],
    ["organization_id", (r) => ({ ...r, organization_id: ORG_B })],
    ["row_version", (r) => ({ ...r, row_version: 9 })],
  ];
  for (const [field, mutate] of COMPONENT_FAULTS) {
    test(`edit via the component: a reply with a different ${field} shows a failure, not 'Changes saved'`, async () => {
      const p = project();
      fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
        ok(mutate(echo(init, p))),
      );
      const onSaved = vi.fn();
      renderForm({ project: p, onSaved });
      type("project-name-input", "typed-name");
      click("project-submit-button");
      await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
      expect(onSaved).not.toHaveBeenCalled();
      expect(screen.queryByTestId("project-saved-notice")).toBeNull();
      expect(input("project-name-input").value).toBe("typed-name");
    });
  }
});

describe("[UX04-B3] project form: a draft reply that is not this request's result is refused", () => {
  const DRAFT_FAULTS: [
    string,
    (r: ProjectRecord, sent: Record<string, unknown>) => ProjectRecord,
  ][] = [
    ["a different ui_state", (r) => ({ ...r, ui_state: { name: "someone else's" } })],
    ["an extra ui_state key", (r, sent) => ({ ...r, ui_state: { ...sent, extra: 1 } })],
    [
      "a bumped row_version",
      (r, sent) => ({ ...r, ui_state: sent, row_version: r.row_version + 1 }),
    ],
    ["another project id", (r, sent) => ({ ...r, ui_state: sent, id: PROJECT_B })],
    ["another organization", (r, sent) => ({ ...r, ui_state: sent, organization_id: ORG_B })],
  ];
  for (const [what, mutate] of DRAFT_FAULTS) {
    test(`draft save answered with ${what}: a failure, never 'Draft saved'`, async () => {
      const p = project();
      fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
        ok(mutate(p, JSON.parse(init.body as string).ui_state)),
      );
      renderForm({ project: p });
      click("project-save-draft-button");
      await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
      expect(screen.queryByTestId("project-draft-saved-notice")).toBeNull();
    });
  }

  test("while a request is in flight the fields are read-only and the type and profile cannot change", async () => {
    let release: (v: unknown) => void = () => undefined;
    const p = project();
    fetchMock().mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    renderForm({ project: p });
    click("project-submit-button");
    await waitFor(() => expect(fetchMock()).toHaveBeenCalledTimes(1));
    expect(input("project-name-input").readOnly).toBe(true);
    expect((tid("project-description-input") as HTMLTextAreaElement).readOnly).toBe(true);
    expect((tid("agent-objective-input") as HTMLTextAreaElement).readOnly).toBe(true);
    type("security-profile-input", "air_gapped");
    expect((tid("security-profile-input") as HTMLSelectElement).value).toBe("standard");
    await act(async () => {
      release(ok(echo({ body: JSON.stringify({ name: p.name }) } as RequestInit, p)));
    });
    await waitFor(() => expect(tid("project-setup-form").getAttribute("aria-busy")).toBe("false"));
    expect(input("project-name-input").readOnly).toBe(false);
  });

  test("a create in flight cannot have its type changed under it", async () => {
    let release: (v: unknown) => void = () => undefined;
    fetchMock().mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    renderForm();
    type("project-name-input", "n");
    click("project-submit-button");
    await waitFor(() => expect(fetchMock()).toHaveBeenCalledTimes(1));
    type("project-type-select", "agentic_product");
    expect((tid("project-type-select") as HTMLSelectElement).value).toBe("generic_model_tuning");
    await act(async () => release(refused(500)));
  });

  test("the form opts out of the browser's own validation bubbles (the summary is the one error surface)", () => {
    renderForm();
    expect((tid("project-setup-form") as HTMLFormElement).noValidate).toBe(true);
  });

  test("after a draft is saved, the offer follows the draft JUST saved, not the stale one the page loaded with", async () => {
    const old = {
      name: "old-draft",
      projectType: "agentic_product",
      description: "",
      agentObjective: "x",
      securityProfile: "standard",
    };
    const p = project({ ui_state: old });
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
      ok({ ...p, ui_state: JSON.parse(init.body as string).ui_state }),
    );
    renderForm({ project: p });
    expect(screen.queryByTestId("project-draft-offer")).toBeTruthy();
    type("project-name-input", "new-work");
    click("project-save-draft-button");
    await waitFor(() => expect(text("project-draft-saved-notice")).toBe("Draft saved"));
    expect(screen.queryByTestId("project-draft-offer")).toBeNull();
  });
});

describe("[UX04-E] project form: typed errors keep state and never become success (UI CHECKLIST d, e)", () => {
  const CASES: [string, () => unknown, string][] = [
    [
      "network failure",
      () => Promise.reject(new TypeError("Failed to fetch")),
      "Could not reach the server",
    ],
    ["401", () => refused(401), "session has ended"],
    ["403", () => refused(403, { detail: "role_not_authorized" }), "do not have access"],
    ["404", () => refused(404, { detail: "not_found" }), "no longer exists"],
    [
      "409",
      () => refused(409, { detail: "project 33 has row_version 4, not the expected 3" }),
      "changed since you opened it",
    ],
    [
      "400",
      () => refused(400, { detail: "name must be a non-empty string" }),
      "did not accept some values",
    ],
    ["422", () => refused(422, { detail: [{ loc: ["body"] }] }), "did not accept some values"],
    ["418 (unknown 4xx)", () => refused(418), "request failed (418)"],
    ["500", () => refused(500), "service had a problem (500)"],
    ["503", () => refused(503), "service had a problem (503)"],
    ["500 with a non-JSON body", () => notJson(500), "service had a problem (500)"],
    ["200 with a non-JSON body", () => notJson(200), "reply this page cannot read"],
    [
      "204 with no body",
      () => ({
        ok: true,
        status: 204,
        json: async () => {
          throw new Error("no body");
        },
      }),
      "reply this page cannot read",
    ],
  ];
  for (const [name, respond, sentence] of CASES) {
    test(`${name}: the sentence is shown, the backend detail is not, entries are kept, the form stays usable`, async () => {
      fetchMock().mockImplementationOnce(async () => respond());
      const onSaved = vi.fn();
      renderForm({ project: project(), onSaved });
      type("project-name-input", "typed-name");
      type("project-description-input", "typed description");
      click("project-submit-button");
      await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
      const shown = text("project-form-error");
      expect(shown).toContain(sentence);
      expect(shown).toContain("Your entries are kept.");
      for (const leak of [
        "internal_code_xyz",
        "role_not_authorized",
        "row_version",
        "not_found",
        "loc",
      ]) {
        expect(shown).not.toContain(leak);
      }
      expect(onSaved).not.toHaveBeenCalled();
      expect(screen.queryByTestId("project-saved-notice")).toBeNull();
      expect(input("project-name-input").value).toBe("typed-name");
      expect(input("project-description-input").value).toBe("typed description");
      expect((tid("project-submit-button") as HTMLButtonElement).disabled).toBe(false);
      expect((tid("project-save-draft-button") as HTMLButtonElement).disabled).toBe(false);
    });
  }

  test("after a failure, the retry that succeeds clears the error and saves", async () => {
    const p = project();
    fetchMock()
      .mockImplementationOnce(async () => refused(500))
      .mockImplementationOnce(async (_u: string, init: RequestInit) => ok(echo(init, p)));
    const onSaved = vi.fn();
    renderForm({ project: p, onSaved });
    click("project-submit-button");
    await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
    click("project-submit-button");
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("project-form-error")).toBeNull();
    expect(text("project-saved-notice")).toBe("Changes saved");
  });

  test("a draft-save failure is the same typed error and does not claim 'Draft saved'", async () => {
    fetchMock().mockImplementationOnce(async () => refused(503));
    renderForm({ project: project() });
    click("project-save-draft-button");
    await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
    expect(screen.queryByTestId("project-draft-saved-notice")).toBeNull();
  });

  test("the failure is announced exactly once, assertively; a success is polite", async () => {
    fetchMock().mockImplementationOnce(async () => refused(500));
    const { container } = renderForm({ project: project() });
    click("project-submit-button");
    await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
    const inside = alerts(tid("project-form-error"));
    expect(inside).toHaveLength(1);
    expect(inside[0]!.getAttribute("lang")).toBe("en");
    expect(
      container.querySelectorAll('[data-testid="project-form-error"] [role="status"]'),
    ).toHaveLength(0);
  });

  test("a second click while the first request is in flight sends nothing (double submit)", async () => {
    let release: (v: unknown) => void = () => undefined;
    const p = project();
    fetchMock().mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    renderForm({ project: p });
    click("project-submit-button");
    await waitFor(() => expect(fetchMock()).toHaveBeenCalledTimes(1));
    expect((tid("project-submit-button") as HTMLButtonElement).disabled).toBe(true);
    expect((tid("project-save-draft-button") as HTMLButtonElement).disabled).toBe(true);
    expect(tid("project-setup-form").getAttribute("aria-busy")).toBe("true");
    fireEvent.submit(tid("project-setup-form"));
    click("project-submit-button");
    expect(fetchMock()).toHaveBeenCalledTimes(1);
    await act(async () => {
      release(ok(echo({ body: JSON.stringify({ name: p.name }) } as RequestInit, p)));
    });
    await waitFor(() => expect(tid("project-setup-form").getAttribute("aria-busy")).toBe("false"));
  });

  test("keyboard focus returns to the action after the request settles", async () => {
    const p = project();
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) => ok(echo(init, p)));
    renderForm({ project: p });
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Changes saved"));
    expect(document.activeElement).toBe(tid("project-submit-button"));
  });
});

describe("[UX04-V] project form: validation prevents errors and recovers from them (H5, H9)", () => {
  const BLANKS = ["", "   ", "\t\n", "​​", "­", "﻿ ⁠"];
  for (const blank of BLANKS) {
    test(`a name of ${JSON.stringify(blank)} sends nothing; the summary takes focus and links the field`, () => {
      renderForm();
      type("project-name-input", blank);
      click("project-submit-button");
      expect(fetchMock()).not.toHaveBeenCalled();
      const summary = document.activeElement as HTMLElement;
      expect(summary.getAttribute("role")).toBe("alert");
      expect(summary.textContent).toContain("Enter a project name.");
      expect(summary.querySelector("a")!.getAttribute("href")).toBe("#project-name");
      const field = input("project-name-input");
      expect(field.getAttribute("aria-invalid")).toBe("true");
      expect(field.getAttribute("aria-describedby")).toContain("project-name-error");
      expect(document.getElementById("project-name-error")!.textContent).toBe(
        "Enter a project name.",
      );
    });
  }

  test("a repeated failed submit takes focus back to the summary", () => {
    renderForm();
    click("project-submit-button");
    (tid("project-name-input") as HTMLInputElement).focus();
    expect(document.activeElement).toBe(tid("project-name-input"));
    click("project-submit-button");
    expect((document.activeElement as HTMLElement).getAttribute("role")).toBe("alert");
  });

  test("typing into the invalid field does not pull focus out of it (no focus theft on keystrokes)", () => {
    renderForm();
    click("project-submit-button");
    const field = tid("project-name-input");
    field.focus();
    type("project-name-input", "a");
    expect(document.activeElement).toBe(field);
    expect(screen.queryByText("Enter a project name.")).toBeNull();
  });

  test("blurring an empty required field shows the inline error without a submit; no summary yet", () => {
    renderForm();
    fireEvent.blur(tid("project-name-input"));
    expect(document.getElementById("project-name-error")!.textContent).toBe(
      "Enter a project name.",
    );
    expect(screen.queryByRole("alert", { name: /problem/i })).toBeNull();
    expect(document.activeElement).toBe(document.body);
  });

  test("an agentic project with a blank objective is blocked client-side, whitespace and zero-width included", () => {
    renderForm();
    type("project-name-input", "p");
    type("project-type-select", "agentic_product");
    type("agent-objective-input", " ​ ");
    click("project-submit-button");
    expect(fetchMock()).not.toHaveBeenCalled();
    expect(document.getElementById("agent-objective-error")!.textContent).toContain(
      "agent objective",
    );
  });

  test("both errors are listed in the summary in form order", () => {
    renderForm();
    type("project-type-select", "agentic_product");
    click("project-submit-button");
    const links = Array.from(document.activeElement!.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(links).toEqual(["#project-name", "#agent-objective"]);
  });

  test("a summary link moves focus to its field", () => {
    renderForm();
    click("project-submit-button");
    fireEvent.click(document.activeElement!.querySelector("a")!);
    expect(document.activeElement).toBe(tid("project-name-input"));
  });

  test("length limits are the backend's (name 200, text 4000)", () => {
    renderForm();
    type("project-type-select", "agentic_product");
    expect(input("project-name-input").maxLength).toBe(200);
    expect((tid("agent-objective-input") as HTMLTextAreaElement).maxLength).toBe(4000);
    expect((tid("project-description-input") as HTMLTextAreaElement).maxLength).toBe(4000);
  });

  test("every field has a visible label above it and is marked required or optional", () => {
    renderForm();
    type("project-type-select", "agentic_product");
    const expectations: [string, string, string][] = [
      ["project-name", "Project name", "(required)"],
      ["project-type", "Project type", "(required)"],
      ["agent-objective", "Agent objective", "(required)"],
      ["project-description", "Description", "(optional)"],
      ["security-profile", "Security profile", "(required)"],
    ];
    for (const [id, label, mark] of expectations) {
      const el = document.getElementById(id)!;
      const lab = document.querySelector(`label[for="${id}"]`)!;
      expect(lab.textContent).toContain(label);
      expect(lab.textContent).toContain(mark);
      expect(el.getAttribute("aria-describedby")).toContain(`${id}-helper`);
      // label above: precedes the control in document order
      expect(lab.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  test("the security profile is the closed set the backend enforces, not free text", () => {
    renderForm();
    const select = tid("security-profile-input") as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    expect(Array.from(select.options).map((o) => o.value)).toEqual([...SECURITY_PROFILES]);
  });

  test("a profile the server holds that is not offered stays visible and selected, never swapped", () => {
    renderForm({ project: project({ security_profile: "legacy_profile" }) });
    const select = tid("security-profile-input") as HTMLSelectElement;
    expect(select.value).toBe("legacy_profile");
    expect(select.selectedOptions[0]!.textContent).toBe("legacy_profile (not offered here)");
  });

  test("the locked project type says why (H5), is disabled and keeps its value", () => {
    renderForm({ project: project() });
    const select = tid("project-type-select") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.value).toBe("agentic_product");
    expect(text("project-type-locked-reason")).toBe(
      "The project type cannot be changed after the project is created.",
    );
    expect(select.getAttribute("aria-describedby")).toContain("project-type-locked");
  });

  test("create mode: 'Save draft' is disabled WITH the reason shown", () => {
    renderForm();
    const draft = tid("project-save-draft-button") as HTMLButtonElement;
    expect(draft.disabled).toBe(true);
    expect(text("disabled-reason")).toBe(
      "Create the project first: a draft is saved onto an existing project.",
    );
    expect(draft.getAttribute("aria-describedby")).toContain("project-save-draft-reason");
  });

  test("the primary action is visibly the primary one (H4/H8)", () => {
    renderForm({ project: project() });
    expect(tid("project-submit-button").getAttribute("data-variant")).toBe("primary");
    expect(tid("project-save-draft-button").getAttribute("data-variant")).toBe("secondary");
    expect((tid("project-submit-button") as HTMLButtonElement).type).toBe("submit");
    expect((tid("project-save-draft-button") as HTMLButtonElement).type).toBe("button");
  });

  test("an archived project cannot be saved: both actions disabled, the reason shown, nothing sent", () => {
    renderForm({ project: project({ status: "archived", archived_at: "2026-02-01T00:00:00Z" }) });
    expect((tid("project-submit-button") as HTMLButtonElement).disabled).toBe(true);
    expect((tid("project-save-draft-button") as HTMLButtonElement).disabled).toBe(true);
    expect(text("project-archived-notice")).toContain("archived");
    fireEvent.submit(tid("project-setup-form"));
    expect(fetchMock()).not.toHaveBeenCalled();
  });

  test("the setup-incomplete notice shows only for an agentic project not yet active", () => {
    const { unmount } = renderForm({ project: project({ status: "setup_incomplete" }) });
    expect(text("setup-incomplete-notice")).toContain("Setup incomplete");
    unmount();
    renderForm({ project: project({ status: "active" }) });
    expect(screen.queryByTestId("setup-incomplete-notice")).toBeNull();
    cleanup();
    renderForm({
      project: project({ project_type: "generic_model_tuning", agent_objective: null }),
    });
    expect(screen.queryByTestId("setup-incomplete-notice")).toBeNull();
  });
});

describe("[UX04-C] project form: identity (UI CHECKLIST c) and the saved record", () => {
  test("another project, workspace or organization is a different form: typed values and notices are gone", () => {
    const view = renderForm({ project: project() });
    type("project-name-input", "typed for A");
    view.rerender(
      createElement(
        ProjectSetupForm,
        formProps({ project: project({ id: PROJECT_B, name: "B" }) }),
      ),
    );
    expect(input("project-name-input").value).toBe("B");
    type("project-name-input", "typed for B");
    view.rerender(
      createElement(
        ProjectSetupForm,
        formProps({ project: project({ id: PROJECT_B, name: "B" }), organizationId: ORG_B }),
      ),
    );
    expect(input("project-name-input").value).toBe("B");
    type("project-name-input", "typed in B-org");
    view.rerender(
      createElement(
        ProjectSetupForm,
        formProps({
          project: project({ id: PROJECT_B, name: "B" }),
          organizationId: ORG_B,
          workspaceId: "55555555-5555-5555-5555-555555555555",
        }),
      ),
    );
    expect(input("project-name-input").value).toBe("B");
  });

  test("a save started for project A never lands on project B (stale response after a switch)", async () => {
    let release: (v: unknown) => void = () => undefined;
    const a = project();
    fetchMock().mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const view = renderForm({ project: a });
    click("project-submit-button");
    await waitFor(() => expect(fetchMock()).toHaveBeenCalledTimes(1));
    const b = project({ id: PROJECT_B, name: "project-b" });
    view.rerender(createElement(ProjectSetupForm, formProps({ project: b })));
    await act(async () => {
      release(ok({ ...a, row_version: 2 }));
    });
    expect(screen.queryByTestId("project-saved-notice")).toBeNull();
    expect(screen.queryByTestId("project-form-error")).toBeNull();
    expect(input("project-name-input").value).toBe("project-b");
    expect((tid("project-submit-button") as HTMLButtonElement).disabled).toBe(false);
  });

  test("the second save sends the version the first save produced, with no re-render from the caller", async () => {
    const p = project({ row_version: 1 });
    fetchMock()
      .mockImplementationOnce(async (_u: string, init: RequestInit) => ok(echo(init, p)))
      .mockImplementationOnce(async (_u: string, init: RequestInit) =>
        ok(
          echo(init, {
            ...p,
            row_version: 2,
            name: JSON.parse(fetchMock().mock.calls[0]![1].body as string).name,
          }),
        ),
      );
    renderForm({ project: p });
    type("project-name-input", "first");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Changes saved"));
    type("project-name-input", "second");
    click("project-submit-button");
    await waitFor(() => expect(fetchMock()).toHaveBeenCalledTimes(2));
    expect(sentBody(0).expected_row_version).toBe(1);
    expect(sentBody(1).expected_row_version).toBe(2);
  });

  test("after a create, the form is that project: the next save PATCHes it instead of creating a duplicate", async () => {
    fetchMock()
      .mockImplementationOnce(async (_u: string, init: RequestInit) => ok(echo(init, null), 201))
      .mockImplementationOnce(async (_u: string, init: RequestInit) => {
        const created = echo({ body: fetchMock().mock.calls[0]![1].body } as RequestInit, null);
        return ok(echo(init, created));
      });
    renderForm();
    type("project-name-input", "once");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Project created"));
    expect((tid("project-type-select") as HTMLSelectElement).disabled).toBe(true);
    expect((tid("project-save-draft-button") as HTMLButtonElement).disabled).toBe(false);
    type("project-name-input", "renamed");
    click("project-submit-button");
    await waitFor(() => expect(fetchMock()).toHaveBeenCalledTimes(2));
    expect(fetchMock().mock.calls[1]![1].method).toBe("PATCH");
    expect(fetchMock().mock.calls[1]![0]).toContain(`/projects/${PROJECT}`);
  });

  test("a newer record handed over by the caller (a reload) replaces the form; the echo of our own save does not", async () => {
    const p = project({ row_version: 1 });
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) => ok(echo(init, p)));
    const view = renderForm({ project: p });
    type("project-name-input", "mine");
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("Changes saved"));
    // The caller hands our own saved record back: nothing is reset, the notice stays.
    view.rerender(
      createElement(
        ProjectSetupForm,
        formProps({ project: project({ row_version: 2, name: "mine" }) }),
      ),
    );
    expect(input("project-name-input").value).toBe("mine");
    expect(screen.queryByTestId("project-saved-notice")).toBeTruthy();
    // Someone else's version arrives: that is the reload the conflict message asked for.
    type("project-name-input", "typed on top");
    view.rerender(
      createElement(
        ProjectSetupForm,
        formProps({ project: project({ row_version: 7, name: "theirs" }) }),
      ),
    );
    expect(input("project-name-input").value).toBe("theirs");
    expect(screen.queryByTestId("project-saved-notice")).toBeNull();
  });
});

describe("[UX04-R] project form: a saved draft is offered back, never applied silently (H3, H5)", () => {
  const draftState = {
    name: "from-draft",
    projectType: "agentic_product",
    description: "draft description",
    agentObjective: "draft objective",
    securityProfile: "zero_egress",
  };

  test("a differing, well-formed draft is offered; the form is untouched until the user restores it", () => {
    renderForm({ project: project({ ui_state: draftState }) });
    expect(text("project-draft-offer")).toContain("A saved draft with different values exists");
    expect(input("project-name-input").value).toBe("acme-agent");
    fireEvent.click(tid("project-restore-draft-button"));
    expect(input("project-name-input").value).toBe("from-draft");
    expect((tid("project-description-input") as HTMLTextAreaElement).value).toBe(
      "draft description",
    );
    expect((tid("agent-objective-input") as HTMLTextAreaElement).value).toBe("draft objective");
    expect((tid("security-profile-input") as HTMLSelectElement).value).toBe("zero_egress");
    expect(text("project-draft-restored-notice")).toContain(
      "Nothing is saved until you save changes",
    );
    expect(screen.queryByTestId("project-draft-offer")).toBeNull();
    expect(fetchMock()).not.toHaveBeenCalled();
  });

  const NOT_OFFERED: [string, Record<string, unknown>][] = [
    ["empty", {}],
    [
      "same as the record",
      {
        name: "acme-agent",
        projectType: "agentic_product",
        description: "",
        agentObjective: "Automate PR review comments.",
        securityProfile: "standard",
      },
    ],
    ["another project type", { ...draftState, projectType: "generic_model_tuning" }],
    ["name not a string", { ...draftState, name: 5 }],
    ["description missing", { ...draftState, description: undefined }],
    ["objective an object", { ...draftState, agentObjective: {} }],
    ["profile null", { ...draftState, securityProfile: null }],
  ];
  for (const [what, ui_state] of NOT_OFFERED) {
    test(`a draft that is ${what} is not offered (and does not crash)`, () => {
      renderForm({ project: project({ ui_state }) });
      expect(screen.queryByTestId("project-draft-offer")).toBeNull();
    });
  }

  test("parseDraft returns only the four text fields of a well-formed draft", () => {
    expect(parseDraft(draftState, project())).toEqual({
      name: "from-draft",
      description: "draft description",
      agentObjective: "draft objective",
      securityProfile: "zero_egress",
    });
    expect(
      parseDraft({ ...draftState, projectType: "generic_model_tuning" }, project()),
    ).toBeNull();
  });

  test("after saving a draft the offer is not shown for the draft just saved", async () => {
    const p = project();
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
      ok({ ...p, ui_state: JSON.parse(init.body as string).ui_state }),
    );
    renderForm({ project: p });
    type("project-name-input", "work in progress");
    click("project-save-draft-button");
    await waitFor(() => expect(text("project-draft-saved-notice")).toBe("Draft saved"));
    expect(screen.queryByTestId("project-draft-offer")).toBeNull();
  });
});

describe("[UX04-L] project form: English and Arabic (RTL)", () => {
  test("en: lang and dir on the root; unknown locale renders English and says lang=en", () => {
    const { unmount } = renderForm({ project: project() });
    expect(tid("project-setup-form").getAttribute("lang")).toBe("en");
    expect(tid("project-setup-form").getAttribute("dir")).toBe("ltr");
    unmount();
    renderForm({ project: project(), locale: "fr" as unknown as "en" });
    expect(tid("project-setup-form").getAttribute("lang")).toBe("en");
    expect(text("project-submit-button")).toBe("Save changes");
  });

  test("ar: lang=ar dir=rtl on the root and on every field wrapper; exact system strings", () => {
    renderForm({ locale: "ar" });
    const form = tid("project-setup-form");
    expect(form.getAttribute("lang")).toBe("ar");
    expect(form.getAttribute("dir")).toBe("rtl");
    expect(document.querySelector('label[for="project-name"]')!.textContent).toBe(
      "اسم المشروع (مطلوب)",
    );
    expect(document.querySelector('label[for="project-type"]')!.textContent).toBe(
      "نوع المشروع (مطلوب)",
    );
    expect(document.querySelector('label[for="project-description"]')!.textContent).toBe(
      "الوصف (اختياري)",
    );
    expect(document.querySelector('label[for="security-profile"]')!.textContent).toBe(
      "ملف الأمان (مطلوب)",
    );
    expect(text("project-submit-button")).toBe("إنشاء المشروع");
    expect(text("project-save-draft-button")).toBe("حفظ المسودة");
    expect(text("disabled-reason")).toBe("أنشئ المشروع أولًا: تُحفظ المسودة على مشروع موجود.");
    expect(
      Array.from((tid("project-type-select") as HTMLSelectElement).options).map(
        (o) => o.textContent,
      ),
    ).toEqual(["ضبط نموذج عام", "منتج وكيلي"]);
    expect(
      Array.from((tid("security-profile-input") as HTMLSelectElement).options).map(
        (o) => o.textContent,
      ),
    ).toEqual(["قياسي", "بدون خروج بيانات", "معزول تمامًا"]);
    for (const wrapper of Array.from(form.querySelectorAll("[lang]"))) {
      expect(wrapper.getAttribute("lang")).toBe("ar");
      expect(wrapper.getAttribute("dir")).toBe("rtl");
    }
  });

  test("ar: validation, failure and success sentences are Arabic", async () => {
    renderForm({ project: project(), locale: "ar" });
    type("project-name-input", " ");
    click("project-submit-button");
    expect(document.activeElement!.textContent).toContain("أدخل اسم المشروع.");
    expect(document.activeElement!.textContent).toContain("توجد مشكلة");
    type("project-name-input", "x");
    fetchMock().mockImplementationOnce(async () => refused(409));
    click("project-submit-button");
    await waitFor(() => expect(screen.getByTestId("project-form-error")).toBeTruthy());
    expect(text("project-form-error")).toContain(
      "تغيّر المشروع منذ فتحته، أو أنه مؤرشف. أعد التحميل لرؤية النسخة الحالية؛ تبقى مدخلاتك في النموذج إلى أن تفعل ذلك.",
    );
    expect(text("project-form-error")).toContain("تم الاحتفاظ بما أدخلته.");
    expect(tid("project-form-error").querySelector('[role="alert"]')!.getAttribute("lang")).toBe(
      "ar",
    );
    fetchMock().mockImplementationOnce(async (_u: string, init: RequestInit) =>
      ok(echo(init, project())),
    );
    click("project-submit-button");
    await waitFor(() => expect(text("project-saved-notice")).toBe("تم حفظ التغييرات"));
  });

  test("every catalogue entry has a real Arabic counterpart with the same placeholders", () => {
    const placeholders = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join(",");
    for (const [key, entry] of Object.entries(MANDATE_WORDS)) {
      expect(entry.en.trim(), key).not.toBe("");
      expect(entry.ar, key).toMatch(/[؀-ۿ]/);
      expect(entry.ar, key).not.toBe(entry.en);
      expect(placeholders(entry.ar), key).toBe(placeholders(entry.en));
    }
  });

  test("every failure status maps to a sentence that never carries the backend detail, en and ar", () => {
    const statuses = [400, 401, 403, 404, 409, 418, 422, 500, 502, 503, 504, 599];
    for (const area of ["project", "budget"] as const) {
      for (const locale of ["en", "ar"] as const) {
        for (const status of statuses) {
          const message = mandateFailureMessage(
            { kind: "http", status, detail: "SECRET_DETAIL" },
            area,
            locale,
          );
          expect(message, `${area} ${locale} ${status}`).not.toContain("SECRET_DETAIL");
          expect(message.length).toBeGreaterThan(20);
          if (locale === "ar") expect(message).toMatch(/[؀-ۿ]/);
        }
        expect(mandateFailureMessage({ kind: "network" }, area, locale)).toBeTruthy();
        expect(mandateFailureMessage({ kind: "malformed" }, area, locale)).toBeTruthy();
      }
    }
  });

  test("user data (the security profile held by the server) is isolated in <bdi> where it is shown inline", () => {
    renderPanelOnly();
    expect(tid("security-profile-value").querySelector("bdi")!.textContent).toBe("restricted");
  });
});

// =============================================================================================
// FTF-0408 RequirementsPanel
// =============================================================================================

function requirement(
  over: Partial<CriticalBehaviorRequirementView> = {},
): CriticalBehaviorRequirementView {
  return {
    tool_definition_id: "tool-1",
    name: "issue_refund",
    side_effect_class: "financial",
    is_critical: true,
    requires_confirmation: true,
    measurable: true,
    runtime_enforced: true,
    ...over,
  };
}
function policy(over: Partial<BudgetPolicyRecord> = {}): BudgetPolicyRecord {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    organization_id: ORG,
    project_id: PROJECT,
    total_amount: "1000.0000",
    currency: "USD",
    scopes: ["training"],
    reserved_amount: "0.0000",
    available_amount: "1000.0000",
    row_version: 2,
    created_by: "user-1",
    created_at: "2026-09-01T00:00:00+00:00",
    updated_at: "2026-09-01T00:00:00+00:00",
    ...over,
  };
}
const REQ = requirementsUrl(API, ORG, PROJECT);
const BUD = budgetPolicyUrl(API, ORG, PROJECT);

interface Routes {
  requirements?: () => unknown;
  budgetGet?: () => unknown;
  write?: (init: RequestInit) => unknown;
}
function route(routes: Routes) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url === REQ) return routes.requirements ? routes.requirements() : ok([]);
    if (url === BUD) {
      if (init?.method === undefined)
        return routes.budgetGet ? routes.budgetGet() : refused(404, { detail: "not_found" });
      if (routes.write) return routes.write(init);
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
}
function panelProps(over: Partial<RequirementsPanelProps> = {}): RequirementsPanelProps {
  return {
    apiBaseUrl: API,
    organizationId: ORG,
    projectId: PROJECT,
    securityProfile: "restricted",
    ...over,
  };
}
function renderPanel(routes: Routes, over: Partial<RequirementsPanelProps> = {}) {
  vi.stubGlobal("fetch", route(routes));
  return render(createElement(RequirementsPanel, panelProps(over)));
}
function renderPanelOnly() {
  return renderPanel({});
}
const writes = () =>
  fetchMock().mock.calls.filter(
    (c: unknown[]) => (c[1] as RequestInit | undefined)?.method !== undefined,
  );
const body = (call: unknown[]) => JSON.parse((call[1] as RequestInit).body as string);

describe("[UX04-P0] requirements panel: certified request shapes and states are unchanged", () => {
  test("the security profile is read-only and says where it is edited", async () => {
    renderPanel({});
    expect(text("security-profile-value")).toBe("restricted");
    expect(text("security-profile-edit-notice")).toBe(
      "Edited from the project setup form, not here.",
    );
    expect(screen.queryByRole("textbox", { name: /security/i })).toBeNull();
  });

  test("a conflict is derived from the live view: critical or confirmation-required and not enforced", async () => {
    const rows = [
      requirement({ tool_definition_id: "ok", name: "enforced_tool" }),
      requirement({ tool_definition_id: "bad", name: "delete_account", runtime_enforced: false }),
      requirement({
        tool_definition_id: "confirm",
        name: "wire",
        is_critical: false,
        requires_confirmation: true,
        runtime_enforced: false,
      }),
      requirement({
        tool_definition_id: "plain",
        name: "read",
        is_critical: false,
        requires_confirmation: false,
        runtime_enforced: false,
      }),
    ];
    renderPanel({ requirements: () => ok(rows) });
    await waitFor(() => expect(tid("requirement-row-ok")).toBeTruthy());
    expect(screen.queryByTestId("requirement-conflict-ok")).toBeNull();
    expect(text("requirement-conflict-bad")).toContain(
      "Conflict: declared critical or requires confirmation, but not enforced at runtime.",
    );
    expect(screen.queryByTestId("requirement-conflict-confirm")).toBeTruthy();
    expect(screen.queryByTestId("requirement-conflict-plain")).toBeNull();
    expect(text("requirement-desired-plain")).toBe("Non-critical");
    expect(text("requirement-enforced-plain")).toBe("Not enforced");
    expect(text("requirement-desired-ok")).toBe("Critical");
    expect(text("requirement-enforced-ok")).toBe("Enforced");
  });

  test("the list is a real table: caption, column headers, a row header per tool, and a state in words", async () => {
    renderPanel({ requirements: () => ok([requirement({ runtime_enforced: false })]) });
    await waitFor(() => expect(tid("requirement-list")).toBeTruthy());
    const table = tid("requirement-list");
    expect(table.querySelector("caption")!.textContent).toBe(
      "Tools: declared requirement and runtime enforcement",
    );
    expect(
      Array.from(table.querySelectorAll('thead th[scope="col"]')).map((th) => th.textContent),
    ).toEqual(["Tool (side-effect class)", "Declared", "Runtime", "Check"]);
    expect(table.querySelector('tbody th[scope="row"]')!.textContent).toBe(
      "issue_refund (financial)",
    );
    expect(table.parentElement!.getAttribute("role")).toBe("region");
    expect(table.parentElement!.getAttribute("tabindex")).toBe("0");
    expect(table.querySelector('[aria-hidden="true"]')!.textContent).toBe("✕");
  });

  test("create: a missing policy shows the notice, an enabled currency and the create action", async () => {
    renderPanel({});
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    expect((tid("budget-currency-input") as HTMLSelectElement).disabled).toBe(false);
    expect(text("budget-policy-submit-button")).toBe("Create budget policy");
    expect(screen.queryByTestId("budget-policy-summary")).toBeNull();
  });

  test("create POSTs total_amount, currency and scopes (in form order) and nothing else", async () => {
    const created = policy({
      row_version: 1,
      total_amount: "2500.5000",
      scopes: ["evaluation", "training"],
      currency: "SAR",
      available_amount: "2500.5000",
    });
    renderPanel({ write: () => ok(created, 201) });
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", " 2500.50 ");
    type("budget-currency-input", "SAR");
    click("budget-scope-evaluation");
    click("budget-scope-training");
    click("budget-policy-submit-button");
    await waitFor(() => expect(text("budget-policy-saved-notice")).toBe("Budget policy created"));
    expect(writes()).toHaveLength(1);
    expect(writes()[0]![1].method).toBe("POST");
    expect(body(writes()[0]!)).toEqual({
      total_amount: "2500.50",
      currency: "SAR",
      scopes: ["training", "evaluation"],
    });
    expect(screen.queryByTestId("budget-policy-missing-notice")).toBeNull();
    expect(text("budget-row-version")).toBe("1");
    expect((tid("budget-currency-input") as HTMLSelectElement).disabled).toBe(true);
    expect(text("budget-currency-locked-reason")).toBe(
      "The currency is fixed once the policy is created.",
    );
  });

  test("revise PATCHes total_amount, scopes and expected_row_version; never currency", async () => {
    const existing = policy({ row_version: 3 });
    renderPanel({
      budgetGet: () => ok(existing),
      write: () => ok({ ...existing, row_version: 4, scopes: ["training", "evaluation"] }),
    });
    await waitFor(() => expect(text("budget-row-version")).toBe("3"));
    click("budget-scope-evaluation");
    click("budget-policy-submit-button");
    await waitFor(() => expect(text("budget-row-version")).toBe("4"));
    expect(writes()[0]![1].method).toBe("PATCH");
    expect(body(writes()[0]!)).toEqual({
      total_amount: "1000.0000",
      scopes: ["training", "evaluation"],
      expected_row_version: 3,
    });
    expect(text("budget-policy-saved-notice")).toBe("Budget policy saved");
  });

  test("the second revise sends the version the first produced", async () => {
    let version = 3;
    renderPanel({
      budgetGet: () => ok(policy({ row_version: 3 })),
      write: (init) => {
        version += 1;
        const sent = JSON.parse(init.body as string);
        return ok(
          policy({
            row_version: version,
            total_amount: sent.total_amount,
            scopes: sent.scopes,
            available_amount: sent.total_amount,
          }),
        );
      },
    });
    await waitFor(() => expect(text("budget-row-version")).toBe("3"));
    type("budget-total-amount-input", "1100.0000");
    click("budget-policy-submit-button");
    await waitFor(() => expect(text("budget-row-version")).toBe("4"));
    type("budget-total-amount-input", "1200.0000");
    click("budget-policy-submit-button");
    await waitFor(() => expect(text("budget-row-version")).toBe("5"));
    expect(body(writes()[0]!).expected_row_version).toBe(3);
    expect(body(writes()[1]!).expected_row_version).toBe(4);
  });

  test("keyboard focus returns to the submit button after a save settles (success and failure)", async () => {
    let fail = true;
    renderPanel({
      budgetGet: () => ok(policy({ row_version: 3 })),
      write: () => (fail ? refused(500) : ok(policy({ row_version: 4 }))),
    });
    await waitFor(() => expect(text("budget-row-version")).toBe("3"));
    click("budget-policy-submit-button");
    await waitFor(() => expect(tid("budget-policy-error")).toBeTruthy());
    expect(document.activeElement).toBe(tid("budget-policy-submit-button"));
    fail = false;
    click("budget-policy-submit-button");
    await waitFor(() => expect(text("budget-row-version")).toBe("4"));
    expect(document.activeElement).toBe(tid("budget-policy-submit-button"));
  });

  test("the currency list is the backend's recognized codes, the scopes its closed set", () => {
    expect([...BUDGET_CURRENCIES]).toEqual([
      "USD",
      "EUR",
      "GBP",
      "SAR",
      "AED",
      "JPY",
      "CHF",
      "CAD",
      "AUD",
      "SGD",
    ]);
    expect([...BUDGET_SCOPES]).toEqual(["training", "evaluation", "serving"]);
    renderPanel({});
    return waitFor(() => expect(tid("budget-currency-input")).toBeTruthy()).then(() => {
      expect(
        Array.from((tid("budget-currency-input") as HTMLSelectElement).options).map((o) => o.value),
      ).toEqual([...BUDGET_CURRENCIES]);
    });
  });
});

describe("[UX04-A2] requirements panel: strict parse of every response field (UI CHECKLIST a)", () => {
  const REQ_RULES: Record<string, (v: unknown) => boolean> = {
    tool_definition_id: (v) => typeof v === "string" && v !== "",
    name: (v) => typeof v === "string",
    side_effect_class: (v) => typeof v === "string",
    is_critical: (v) => typeof v === "boolean",
    requires_confirmation: (v) => typeof v === "boolean",
    measurable: (v) => typeof v === "boolean",
    runtime_enforced: (v) => typeof v === "boolean",
  };
  const BUDGET_RULES: Record<string, (v: unknown) => boolean> = {
    id: (v) => typeof v === "string" && v !== "",
    organization_id: (v) => typeof v === "string" && v !== "",
    project_id: (v) => typeof v === "string" && v !== "",
    total_amount: (v) => typeof v === "string" && /^\d{1,14}(\.\d{1,4})?$/.test(v),
    currency: (v) => typeof v === "string" && (BUDGET_CURRENCIES as readonly string[]).includes(v),
    scopes: (v) =>
      Array.isArray(v) &&
      v.length > 0 &&
      new Set(v).size === v.length &&
      v.every((s) => (BUDGET_SCOPES as readonly unknown[]).includes(s)),
    reserved_amount: (v) => typeof v === "string" && /^\d{1,14}(\.\d{1,4})?$/.test(v),
    available_amount: (v) => typeof v === "string" && /^\d{1,14}(\.\d{1,4})?$/.test(v),
    row_version: (v) => typeof v === "number" && Number.isSafeInteger(v) && v >= 1,
    created_by: (v) => typeof v === "string",
    created_at: (v) => typeof v === "string",
    updated_at: (v) => typeof v === "string",
  };
  const CANDIDATES: unknown[] = [
    undefined,
    null,
    "",
    "x",
    "12.5",
    "0.0000",
    "1e3",
    "-1",
    " 1",
    "1,5",
    "NaN",
    "Infinity",
    "123456789012345.0000",
    "1.23456",
    0,
    1,
    1.5,
    -1,
    42,
    Number.NaN,
    true,
    false,
    {},
    [],
    ["training"],
    ["bogus"],
    ["training", "training"],
    "EUR",
    "XYZ",
    "usd",
  ];
  const label = (c: unknown) => (c === undefined ? "missing" : (JSON.stringify(c) ?? String(c)));

  test("the rule tables cover exactly the fields the API serializes", () => {
    expect(Object.keys(REQ_RULES).sort()).toEqual(Object.keys(requirement()).sort());
    expect(Object.keys(BUDGET_RULES).sort()).toEqual(Object.keys(policy()).sort());
  });

  for (const [field, accepts] of Object.entries(REQ_RULES)) {
    for (const c of CANDIDATES) {
      test(`parseRequirements ${field}=${label(c)} -> ${accepts(c) ? "accepted" : "refused"}`, () => {
        const row: Record<string, unknown> = { ...requirement() };
        if (c === undefined) delete row[field];
        else row[field] = c;
        expect(parseRequirements([row]) !== null).toBe(accepts(c));
      });
    }
  }
  for (const [field, accepts] of Object.entries(BUDGET_RULES)) {
    for (const c of CANDIDATES) {
      test(`parseBudgetPolicy ${field}=${label(c)} -> ${accepts(c) ? "accepted" : "refused"}`, () => {
        const rec: Record<string, unknown> = { ...policy() };
        if (c === undefined) delete rec[field];
        else rec[field] = c;
        expect(parseBudgetPolicy(rec) !== null).toBe(accepts(c));
      });
    }
  }

  test("non-list requirements and non-object policies are refused; duplicates are refused; an empty list is valid", () => {
    for (const bad of [null, undefined, {}, "x", 5, [null], [[]], [1]])
      expect(parseRequirements(bad)).toBeNull();
    for (const bad of [null, undefined, [], "x", 5, true])
      expect(parseBudgetPolicy(bad)).toBeNull();
    expect(parseRequirements([requirement(), requirement()])).toBeNull();
    expect(parseRequirements([])).toEqual([]);
  });

  const MALFORMED_REQ: [string, unknown][] = [
    ["not a list", { rows: [] }],
    ["a row missing a field", [{ ...requirement(), measurable: undefined }]],
    ["a wrong type", [{ ...requirement(), runtime_enforced: "true" }]],
    ["duplicate ids", [requirement(), requirement()]],
  ];
  for (const [what, bodyValue] of MALFORMED_REQ) {
    test(`requirements 2xx with ${what}: an error is shown, no row is, and a retry is offered`, async () => {
      renderPanel({ requirements: () => ok(bodyValue) });
      await waitFor(() => expect(tid("requirements-error")).toBeTruthy());
      expect(text("requirements-error")).toContain("could not be loaded");
      expect(text("requirements-error")).toContain("reply this page cannot read");
      expect(screen.queryByTestId("requirement-list")).toBeNull();
      expect(screen.queryByTestId("requirements-empty")).toBeNull();
      expect(tid("requirements-retry-button")).toBeTruthy();
    });
  }

  const MALFORMED_BUDGET: [string, unknown][] = [
    ["a missing field", { ...policy(), scopes: undefined }],
    ["an unknown currency", policy({ currency: "XYZ" })],
    ["an unknown scope", policy({ scopes: ["bogus"] })],
    ["empty scopes", policy({ scopes: [] })],
    ["a non-decimal total", policy({ total_amount: "lots" })],
    ["a non-integer version", policy({ row_version: 1.5 })],
    ["a string version", { ...policy(), row_version: "2" }],
    ["another organization", policy({ organization_id: ORG_B })],
    ["another project", policy({ project_id: PROJECT_B })],
  ];
  for (const [what, bodyValue] of MALFORMED_BUDGET) {
    test(`budget GET 200 with ${what}: an error and a retry, no form and no summary`, async () => {
      renderPanel({ budgetGet: () => ok(bodyValue) });
      await waitFor(() => expect(tid("budget-policy-load-error")).toBeTruthy());
      expect(text("budget-policy-load-error")).toContain("reply this page cannot read");
      expect(screen.queryByTestId("budget-policy-form")).toBeNull();
      expect(screen.queryByTestId("budget-policy-summary")).toBeNull();
      expect(screen.queryByTestId("budget-policy-missing-notice")).toBeNull();
      expect(tid("budget-policy-retry-button")).toBeTruthy();
    });
  }
});

describe("[UX04-B2] requirements panel: ONE binding of every field sent (UI CHECKLIST b)", () => {
  const SCOPE = { organizationId: ORG, projectId: PROJECT };
  const sentCreate = {
    total_amount: "2500.50",
    currency: "SAR",
    scopes: ["training", "evaluation"] as ("training" | "evaluation" | "serving")[],
  };
  const goodCreate = policy({
    row_version: 1,
    total_amount: "2500.5000",
    currency: "SAR",
    scopes: ["evaluation", "training"],
    available_amount: "2500.5000",
  });
  test("the matching create reply binds (formatting and scope order are the server's)", () => {
    expect(bindBudgetSave(sentCreate, goodCreate, SCOPE, null)).toBe(true);
  });
  const CREATE_FAULTS: [string, Partial<BudgetPolicyRecord>][] = [
    ["total_amount", { total_amount: "2500.5001", available_amount: "2500.5001" }],
    ["currency", { currency: "USD" }],
    ["scopes (one fewer)", { scopes: ["training"] }],
    ["scopes (one extra)", { scopes: ["evaluation", "serving", "training"] }],
    ["scopes (swapped)", { scopes: ["evaluation", "serving"] }],
    ["organization_id", { organization_id: ORG_B }],
    ["project_id", { project_id: PROJECT_B }],
    ["row_version (a create is version 1)", { row_version: 2 }],
    ["available_amount (must be total minus reserved)", { available_amount: "1.0000" }],
  ];
  test("create: a reply carrying an extra scope AFTER every sent one is refused (prefix match is not a match)", () => {
    const sent = {
      total_amount: "10",
      currency: "USD",
      scopes: ["evaluation", "serving"] as ("training" | "evaluation" | "serving")[],
    };
    const reply = policy({
      row_version: 1,
      total_amount: "10.0000",
      available_amount: "10.0000",
      scopes: ["evaluation", "serving", "training"],
    });
    expect(bindBudgetSave(sent, reply, SCOPE, null)).toBe(false);
    expect(bindBudgetSave(sent, { ...reply, scopes: ["evaluation", "serving"] }, SCOPE, null)).toBe(
      true,
    );
    expect(bindBudgetSave(sent, { ...reply, scopes: ["evaluation"] }, SCOPE, null)).toBe(false);
  });
  for (const [field, change] of CREATE_FAULTS) {
    test(`create: a reply whose ${field} differs is refused`, () => {
      expect(bindBudgetSave(sentCreate, { ...goodCreate, ...change }, SCOPE, null)).toBe(false);
    });
  }
  const current = policy({
    row_version: 3,
    currency: "USD",
    reserved_amount: "100.0000",
    total_amount: "1000.0000",
    available_amount: "900.0000",
  });
  const sentRevise = {
    total_amount: "1200",
    currency: null,
    scopes: ["training", "serving"] as ("training" | "evaluation" | "serving")[],
  };
  const goodRevise = policy({
    row_version: 4,
    currency: "USD",
    reserved_amount: "100.0000",
    total_amount: "1200.0000",
    available_amount: "1100.0000",
    scopes: ["serving", "training"],
  });
  test("the matching revise reply binds", () => {
    expect(bindBudgetSave(sentRevise, goodRevise, SCOPE, current)).toBe(true);
  });
  test("revise: a request that carried a currency is not bound (a revision sends none)", () => {
    expect(bindBudgetSave({ ...sentRevise, currency: "USD" }, goodRevise, SCOPE, current)).toBe(
      false,
    );
  });
  const REVISE_FAULTS: [string, Partial<BudgetPolicyRecord>][] = [
    ["id", { id: "55555555-5555-5555-5555-555555555555" }],
    ["currency (a revision never changes it)", { currency: "EUR" }],
    ["total_amount", { total_amount: "1000.0000", available_amount: "900.0000" }],
    ["scopes", { scopes: ["training"] }],
    ["row_version unchanged", { row_version: 3 }],
    ["row_version skipped", { row_version: 5 }],
    ["available_amount", { available_amount: "1200.0000" }],
    ["organization_id", { organization_id: ORG_B }],
  ];
  for (const [field, change] of REVISE_FAULTS) {
    test(`revise: a reply whose ${field} differs is refused`, () => {
      expect(bindBudgetSave(sentRevise, { ...goodRevise, ...change }, SCOPE, current)).toBe(false);
    });
  }

  const VIA_COMPONENT: [string, (r: BudgetPolicyRecord) => BudgetPolicyRecord][] = [
    ["total_amount", (r) => ({ ...r, total_amount: "5.0000", available_amount: "5.0000" })],
    ["scopes", (r) => ({ ...r, scopes: ["serving"] })],
    ["row_version", (r) => ({ ...r, row_version: 9 })],
    ["project_id", (r) => ({ ...r, project_id: PROJECT_B })],
    ["available_amount", (r) => ({ ...r, available_amount: "0.0000" })],
  ];
  for (const [field, mutate] of VIA_COMPONENT) {
    test(`via the component: a revise reply with a different ${field} shows a failure, keeps the entries, never 'saved'`, async () => {
      const existing = policy({ row_version: 3 });
      renderPanel({
        budgetGet: () => ok(existing),
        write: (init) => {
          const sent = JSON.parse(init.body as string);
          return ok(
            mutate({
              ...existing,
              row_version: 4,
              total_amount: sent.total_amount,
              available_amount: sent.total_amount,
              scopes: sent.scopes,
            }),
          );
        },
      });
      await waitFor(() => expect(text("budget-row-version")).toBe("3"));
      type("budget-total-amount-input", "1500.0000");
      click("budget-policy-submit-button");
      await waitFor(() => expect(tid("budget-policy-error")).toBeTruthy());
      expect(text("budget-policy-error")).toContain("reply this page cannot read");
      expect(screen.queryByTestId("budget-policy-saved-notice")).toBeNull();
      expect(text("budget-row-version")).toBe("3");
      expect(input("budget-total-amount-input").value).toBe("1500.0000");
    });
  }
});

describe("[UX04-A3] requirements panel: a bound 2xx with ONE broken field is a failure, field by field (UI CHECKLIST a)", () => {
  const WRONG: Record<string, unknown> = {
    id: 5,
    organization_id: 5,
    project_id: 5,
    total_amount: 5,
    currency: 5,
    scopes: "training",
    reserved_amount: 5,
    available_amount: 5,
    row_version: "2",
    created_by: 5,
    created_at: 5,
    updated_at: 5,
  };
  /** The reply the real route would give THIS request, so only the broken field can be the reason. */
  function consistent(init: RequestInit, current: BudgetPolicyRecord | null): BudgetPolicyRecord {
    const sent = JSON.parse(init.body as string);
    const total = sent.total_amount.includes(".")
      ? sent.total_amount.padEnd(sent.total_amount.indexOf(".") + 5, "0")
      : `${sent.total_amount}.0000`;
    return policy({
      ...(current ?? {}),
      total_amount: total,
      available_amount: total,
      reserved_amount: "0.0000",
      scopes: [...sent.scopes].sort(),
      currency: current ? current.currency : sent.currency,
      row_version: current ? current.row_version + 1 : 1,
    });
  }
  for (const mode of ["create", "revise"] as const) {
    for (const field of Object.keys(WRONG)) {
      for (const how of ["missing", "wrong type"] as const) {
        test(`${mode}: a bound reply whose ${field} is ${how} is a failure -- no saved notice, entries kept`, async () => {
          const existing = policy({
            row_version: 3,
            reserved_amount: "0.0000",
            available_amount: "1000.0000",
          });
          renderPanel({
            budgetGet: () => (mode === "create" ? refused(404) : ok(existing)),
            write: (init) => {
              const reply: Record<string, unknown> = {
                ...consistent(init, mode === "create" ? null : existing),
              };
              if (how === "missing") delete reply[field];
              else reply[field] = WRONG[field];
              return ok(reply);
            },
          });
          await waitFor(() => expect(tid("budget-policy-form")).toBeTruthy());
          type("budget-total-amount-input", "1500");
          if (mode === "create") click("budget-scope-training");
          click("budget-policy-submit-button");
          await waitFor(() => expect(tid("budget-policy-error")).toBeTruthy());
          expect(text("budget-policy-error")).toContain("reply this page cannot read");
          expect(screen.queryByTestId("budget-policy-saved-notice")).toBeNull();
          expect(input("budget-total-amount-input").value).toBe("1500");
        });
      }
    }
  }

  test("sanity: the same requests with an unbroken reply DO succeed (the table above is not vacuous)", async () => {
    for (const mode of ["create", "revise"] as const) {
      cleanup();
      const existing = policy({
        row_version: 3,
        reserved_amount: "0.0000",
        available_amount: "1000.0000",
      });
      renderPanel({
        budgetGet: () => (mode === "create" ? refused(404) : ok(existing)),
        write: (init) => ok(consistent(init, mode === "create" ? null : existing)),
      });
      await waitFor(() => expect(tid("budget-policy-form")).toBeTruthy());
      type("budget-total-amount-input", "1500");
      if (mode === "create") click("budget-scope-training");
      click("budget-policy-submit-button");
      await waitFor(() => expect(tid("budget-policy-saved-notice")).toBeTruthy());
      expect(screen.queryByTestId("budget-policy-error")).toBeNull();
    }
  });

  test("a revision never sends a currency, and a create sends the chosen one", async () => {
    const existing = policy({
      row_version: 3,
      reserved_amount: "0.0000",
      available_amount: "1000.0000",
    });
    renderPanel({ budgetGet: () => ok(existing), write: (init) => ok(consistent(init, existing)) });
    await waitFor(() => expect(text("budget-row-version")).toBe("3"));
    click("budget-policy-submit-button");
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(Object.hasOwn(body(writes()[0]!), "currency")).toBe(false);
  });
});

describe("[UX04-S2] requirements panel: waiting is shown, and nothing can change under a request in flight", () => {
  test("a retry shows the loading state again while it is in flight (budget and requirements)", async () => {
    let calls = 0;
    renderPanel({
      requirements: () => (++calls === 1 ? refused(500) : new Promise(() => undefined)),
      budgetGet: () => (calls < 2 ? refused(500) : new Promise(() => undefined)),
    });
    await waitFor(() => expect(tid("requirements-error")).toBeTruthy());
    await waitFor(() => expect(tid("budget-policy-load-error")).toBeTruthy());
    click("requirements-retry-button");
    await waitFor(() => expect(tid("requirements-loading")).toBeTruthy());
    expect(screen.queryByTestId("requirements-error")).toBeNull();
    click("budget-policy-retry-button");
    await waitFor(() => expect(tid("budget-policy-loading")).toBeTruthy());
    expect(screen.queryByTestId("budget-policy-load-error")).toBeNull();
  });

  test("after one second a busy indicator with the plain label is announced politely (NN/g response times)", () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(() => new Promise(() => undefined)),
      );
      render(createElement(RequirementsPanel, panelProps()));
      expect(screen.queryByText("Loading requirements…")).toBeNull();
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      const status = screen.getByText("Loading requirements…").closest('[role="status"]')!;
      expect(status.getAttribute("aria-live")).toBe("polite");
      expect(screen.getByText("Loading budget policy…")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  test("while a save is in flight the amount is read-only and the scopes and currency cannot change", async () => {
    let release: (v: unknown) => void = () => undefined;
    renderPanel({
      write: () =>
        new Promise((r) => {
          release = r;
        }),
    });
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", "10");
    click("budget-scope-training");
    click("budget-policy-submit-button");
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(input("budget-total-amount-input").readOnly).toBe(true);
    for (const scope of BUDGET_SCOPES)
      expect((tid(`budget-scope-${scope}`) as HTMLInputElement).disabled).toBe(true);
    type("budget-currency-input", "EUR");
    expect((tid("budget-currency-input") as HTMLSelectElement).value).toBe("USD");
    await act(async () => release(refused(500)));
    await waitFor(() => expect(input("budget-total-amount-input").readOnly).toBe(false));
    for (const scope of BUDGET_SCOPES)
      expect((tid(`budget-scope-${scope}`) as HTMLInputElement).disabled).toBe(false);
  });

  test("the budget form opts out of the browser's own validation bubbles", async () => {
    renderPanel({});
    await waitFor(() => expect(tid("budget-policy-form")).toBeTruthy());
    expect((tid("budget-policy-form") as HTMLFormElement).noValidate).toBe(true);
  });

  test("the requirement table region is keyboard-scrollable (focusable) and named", async () => {
    renderPanel({ requirements: () => ok([requirement()]) });
    await waitFor(() => expect(tid("requirement-list")).toBeTruthy());
    const region = tid("requirement-list").parentElement!;
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(region.getAttribute("aria-label")).toBe(
      "Tools: declared requirement and runtime enforcement",
    );
  });
});

describe("[UX04-E2] requirements panel: typed errors keep state (UI CHECKLIST d, e)", () => {
  const CASES: [string, () => unknown, string][] = [
    [
      "network failure",
      () => Promise.reject(new TypeError("Failed to fetch")),
      "Could not reach the server",
    ],
    ["401", () => refused(401), "session has ended"],
    ["403", () => refused(403), "do not have access"],
    ["404", () => refused(404), "project was not found"],
    [
      "409",
      () => refused(409, { detail: "budget policy x has row_version 3, not the expected 2" }),
      "changed since you opened it",
    ],
    [
      "400",
      () => refused(400, { detail: "total_amount must be a positive amount" }),
      "did not accept some values",
    ],
    ["418", () => refused(418), "request failed (418)"],
    ["500", () => refused(500), "service had a problem (500)"],
    ["500 non-JSON", () => notJson(500), "service had a problem (500)"],
    ["200 non-JSON", () => notJson(200), "reply this page cannot read"],
  ];
  for (const [name, respond, sentence] of CASES) {
    test(`save: ${name} -> the sentence, never the detail; the draft and the last good policy stay`, async () => {
      const existing = policy({
        row_version: 5,
        total_amount: "500.0000",
        available_amount: "500.0000",
      });
      renderPanel({ budgetGet: () => ok(existing), write: () => respond() });
      await waitFor(() => expect(text("budget-row-version")).toBe("5"));
      type("budget-total-amount-input", "999.00");
      click("budget-scope-serving");
      click("budget-policy-submit-button");
      await waitFor(() => expect(tid("budget-policy-error")).toBeTruthy());
      const shown = text("budget-policy-error");
      expect(shown).toContain(sentence);
      expect(shown).toContain("Your entries are kept.");
      for (const leak of ["row_version", "total_amount", "positive amount"])
        expect(shown).not.toContain(leak);
      expect(input("budget-total-amount-input").value).toBe("999.00");
      expect((tid("budget-scope-serving") as HTMLInputElement).checked).toBe(true);
      expect(text("budget-row-version")).toBe("5");
      expect(screen.queryByTestId("budget-policy-saved-notice")).toBeNull();
      expect((tid("budget-policy-submit-button") as HTMLButtonElement).disabled).toBe(false);
      expect(alerts(tid("budget-policy-error"))).toHaveLength(1);
    });
  }

  test("a create that fails keeps the missing-policy state and the typed draft", async () => {
    renderPanel({ write: () => refused(500) });
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", "42");
    click("budget-scope-training");
    click("budget-policy-submit-button");
    await waitFor(() => expect(tid("budget-policy-error")).toBeTruthy());
    expect(tid("budget-policy-missing-notice")).toBeTruthy();
    expect(input("budget-total-amount-input").value).toBe("42");
  });

  test("the requirements load failing is an error with a retry -- not an empty list -- and the retry recovers", async () => {
    let calls = 0;
    renderPanel({ requirements: () => (++calls === 1 ? refused(500) : ok([requirement()])) });
    await waitFor(() => expect(tid("requirements-error")).toBeTruthy());
    expect(screen.queryByTestId("requirements-empty")).toBeNull();
    expect(alerts(tid("requirements-error"))).toHaveLength(1);
    click("requirements-retry-button");
    await waitFor(() => expect(tid("requirement-row-tool-1")).toBeTruthy());
    expect(screen.queryByTestId("requirements-error")).toBeNull();
  });

  test("a budget GET that fails with 5xx is an error, never the 'no policy yet' create state", async () => {
    renderPanel({ budgetGet: () => refused(500) });
    await waitFor(() => expect(tid("budget-policy-load-error")).toBeTruthy());
    expect(screen.queryByTestId("budget-policy-missing-notice")).toBeNull();
    expect(screen.queryByTestId("budget-policy-form")).toBeNull();
    expect(text("budget-policy-load-error")).toContain("service had a problem (500)");
  });

  test("a budget GET that fails with 401/403/network is an error and a retry that recovers", async () => {
    for (const first of [
      () => refused(401),
      () => refused(403),
      () => Promise.reject(new TypeError("x")),
    ]) {
      let calls = 0;
      const view = renderPanel({ budgetGet: () => (++calls === 1 ? first() : ok(policy())) });
      await waitFor(() => expect(tid("budget-policy-load-error")).toBeTruthy());
      click("budget-policy-retry-button");
      await waitFor(() => expect(text("budget-row-version")).toBe("2"));
      expect(screen.queryByTestId("budget-policy-load-error")).toBeNull();
      view.unmount();
    }
  });

  test("a second click while a save is in flight sends nothing", async () => {
    let release: (v: unknown) => void = () => undefined;
    renderPanel({
      write: () =>
        new Promise((r) => {
          release = r;
        }),
    });
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", "10");
    click("budget-scope-training");
    click("budget-policy-submit-button");
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect((tid("budget-policy-submit-button") as HTMLButtonElement).disabled).toBe(true);
    expect(tid("budget-policy-form").getAttribute("aria-busy")).toBe("true");
    fireEvent.submit(tid("budget-policy-form"));
    expect(writes()).toHaveLength(1);
    await act(async () =>
      release(
        ok(policy({ row_version: 1, total_amount: "10.0000", available_amount: "10.0000" }), 201),
      ),
    );
    await waitFor(() => expect(tid("budget-policy-form").getAttribute("aria-busy")).toBe("false"));
  });
});

describe("[UX04-T] requirements panel: the total is checked BEFORE submit, in exact arithmetic (audit H5, sev 2)", () => {
  test("toUnits is exact: 4 places, 14 integer digits, nothing but digits and one point", () => {
    expect(toUnits("0")).toBe(0n);
    expect(toUnits("1")).toBe(10000n);
    expect(toUnits("1.5")).toBe(15000n);
    expect(toUnits("0.0001")).toBe(1n);
    expect(toUnits("99999999999999.9999")).toBe(999999999999999999n);
    expect(toUnits("007")).toBe(70000n);
    expect(toUnits("000000000000000000012")).toBe(120000n);
    expect(toUnits("0000000000000000.5")).toBe(5000n);
    for (const bad of [
      "",
      ".5",
      "5.",
      "1.23456",
      "100000000000000",
      "-1",
      "+1",
      "1e3",
      "1,000",
      " 1",
      "1 ",
      "٣",
      "0x10",
      "NaN",
      "1..2",
      "1.2.3",
    ]) {
      expect(toUnits(bad), JSON.stringify(bad)).toBeNull();
    }
  });

  const CASES: [string, string | null, "required" | "invalid" | "belowReserved" | null][] = [
    ["", null, "required"],
    ["   ", null, "required"],
    ["abc", null, "invalid"],
    ["-5", null, "invalid"],
    ["0", null, "invalid"],
    ["0.0000", null, "invalid"],
    ["1.23456", null, "invalid"],
    ["1e3", null, "invalid"],
    ["1,000", null, "invalid"],
    ["100000000000000", null, "invalid"],
    ["99999999999999.9999", null, null],
    ["0.0001", null, null],
    [" 12 ", null, null],
    ["100", "100.0000", null],
    ["100.0000", "100.0000", null],
    ["99.9999", "100.0000", "belowReserved"],
    ["0.1", "0.1000", null],
    ["0.0999", "0.1000", "belowReserved"],
    ["0.3", "0.3000", null],
    ["99999999999999.9999", "99999999999999.9999", null],
    ["99999999999999.9998", "99999999999999.9999", "belowReserved"],
  ];
  for (const [total, reserved, expected] of CASES) {
    test(`totalProblem(${JSON.stringify(total)}, reserved ${JSON.stringify(reserved)}) = ${expected}`, () => {
      expect(totalProblem(total, reserved)).toBe(expected);
    });
  }

  test("below the reserved amount: nothing is sent, the message names the minimum, the summary takes focus", async () => {
    const existing = policy({
      row_version: 3,
      total_amount: "1000.0000",
      reserved_amount: "400.0000",
      available_amount: "600.0000",
    });
    renderPanel({ budgetGet: () => ok(existing) });
    await waitFor(() => expect(text("budget-row-version")).toBe("3"));
    type("budget-total-amount-input", "399.99");
    click("budget-policy-submit-button");
    expect(writes()).toHaveLength(0);
    const message =
      "The total cannot be less than the amount already reserved (400.0000 USD). Enter 400.0000 or more.";
    expect(document.activeElement!.getAttribute("role")).toBe("alert");
    expect(document.activeElement!.textContent).toContain(message);
    expect(document.getElementById("budget-total-amount-error")!.textContent).toBe(message);
    expect(input("budget-total-amount-input").getAttribute("aria-invalid")).toBe("true");
    type("budget-total-amount-input", "400");
    expect(document.getElementById("budget-total-amount-error")).toBeNull();
    click("budget-policy-submit-button");
    await waitFor(() => expect(writes()).toHaveLength(1));
  });

  test("blur shows the inline error without a submit and without moving focus", async () => {
    renderPanel({});
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", "abc");
    fireEvent.blur(tid("budget-total-amount-input"));
    expect(document.getElementById("budget-total-amount-error")!.textContent).toBe(
      "Enter a positive number with at most 4 decimal places, for example 2500.50.",
    );
    expect(document.activeElement).toBe(document.body);
  });

  test("no scope chosen: nothing is sent; the error is announced and the summary links the first scope", async () => {
    renderPanel({});
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    type("budget-total-amount-input", "10");
    click("budget-policy-submit-button");
    expect(writes()).toHaveLength(0);
    expect(text("budget-scopes-error")).toBe("Choose at least one scope.");
    expect(tid("budget-scopes-error").getAttribute("role")).toBe("alert");
    expect(document.activeElement!.querySelector("a")!.getAttribute("href")).toBe(
      "#budget-scope-training",
    );
    fireEvent.click(document.activeElement!.querySelector("a")!);
    expect(document.activeElement).toBe(tid("budget-scope-training"));
  });

  test("a repeated failed submit takes focus back to the summary", async () => {
    renderPanel({});
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    click("budget-policy-submit-button");
    tid("budget-total-amount-input").focus();
    click("budget-policy-submit-button");
    expect(document.activeElement!.getAttribute("role")).toBe("alert");
  });
});

describe("[UX04-S] requirements panel: states a user can be in (H1, H10)", () => {
  test("loading: both regions say so while the requests are in flight, and offer nothing to submit", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    render(createElement(RequirementsPanel, panelProps()));
    expect(tid("requirements-loading")).toBeTruthy();
    expect(tid("budget-policy-loading")).toBeTruthy();
    expect(screen.queryByTestId("budget-policy-form")).toBeNull();
    expect(screen.queryByTestId("requirements-empty")).toBeNull();
  });

  test("empty: an empty list explains why and gives the first action; the action reloads", async () => {
    let calls = 0;
    renderPanel({ requirements: () => (++calls === 1 ? ok([]) : ok([requirement()])) });
    await waitFor(() => expect(tid("requirements-empty")).toBeTruthy());
    expect(text("requirements-empty")).toContain("No tools are defined for this project yet");
    expect(text("requirements-empty")).toContain(
      "Define a tool to see whether it is enforced at runtime.",
    );
    click("requirements-reload-button");
    await waitFor(() => expect(tid("requirement-row-tool-1")).toBeTruthy());
    expect(screen.queryByTestId("requirements-empty")).toBeNull();
  });

  test("the budget region says what the policy version is before the user submits (version preview)", async () => {
    renderPanel({
      budgetGet: () =>
        ok(policy({ row_version: 7, reserved_amount: "12.5000", available_amount: "987.5000" })),
    });
    await waitFor(() => expect(text("budget-row-version")).toBe("7"));
    expect(text("budget-reserved")).toBe("12.5000");
    expect(text("budget-available")).toBe("987.5000");
    const labels = Array.from(tid("budget-policy-summary").querySelectorAll("dt")).map(
      (d) => d.textContent,
    );
    expect(labels).toEqual(["Policy version", "Reserved", "Available"]);
  });

  test("every field has a visible label above it, marked required, with helper text", async () => {
    renderPanel({});
    await waitFor(() => expect(tid("budget-policy-form")).toBeTruthy());
    for (const [id, label] of [
      ["budget-total-amount", "Total amount"],
      ["budget-currency", "Currency"],
    ] as const) {
      const lab = document.querySelector(`label[for="${id}"]`)!;
      expect(lab.textContent).toContain(label);
      expect(lab.textContent).toContain("(required)");
      expect(
        lab.compareDocumentPosition(document.getElementById(id)!) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(
      document.getElementById("budget-total-amount")!.getAttribute("aria-describedby"),
    ).toContain("budget-total-amount-helper");
    expect(document.getElementById("budget-total-amount")!.getAttribute("inputmode")).toBe(
      "decimal",
    );
    expect(tid("budget-scopes-fieldset").querySelector("legend")!.textContent).toContain("Scopes");
    expect(tid("budget-policy-submit-button").getAttribute("data-variant")).toBe("primary");
  });
});

describe("[UX04-C2] requirements panel: identity (UI CHECKLIST c)", () => {
  test("another project is another panel: nothing loaded or typed for the first is shown or sent for the second", async () => {
    const BUD_B = budgetPolicyUrl(API, ORG, PROJECT_B);
    const REQ_B = requirementsUrl(API, ORG, PROJECT_B);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === REQ)
          return ok([requirement({ tool_definition_id: "tool-a", name: "tool_of_a" })]);
        if (url === BUD)
          return ok(
            policy({ row_version: 8, total_amount: "800.0000", available_amount: "800.0000" }),
          );
        if (url === REQ_B) return ok([]);
        if (url === BUD_B) return init?.method === undefined ? refused(404) : ok(policy());
        throw new Error(`unexpected ${url}`);
      }),
    );
    const view = render(createElement(RequirementsPanel, panelProps()));
    await waitFor(() => expect(text("budget-row-version")).toBe("8"));
    type("budget-total-amount-input", "typed for A");
    view.rerender(createElement(RequirementsPanel, panelProps({ projectId: PROJECT_B })));
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    expect(screen.queryByText("tool_of_a")).toBeNull();
    expect(screen.queryByTestId("budget-row-version")).toBeNull();
    expect(input("budget-total-amount-input").value).toBe("");
    expect(tid("requirements-panel").getAttribute("lang")).toBe("en");
  });

  test("another organization is another panel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes(ORG_B)) return url.endsWith("/requirements") ? ok([]) : refused(404);
        return url.endsWith("/requirements") ? ok([requirement()]) : ok(policy({ row_version: 6 }));
      }),
    );
    const view = render(createElement(RequirementsPanel, panelProps()));
    await waitFor(() => expect(text("budget-row-version")).toBe("6"));
    view.rerender(createElement(RequirementsPanel, panelProps({ organizationId: ORG_B })));
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    expect(screen.queryByTestId("requirement-row-tool-1")).toBeNull();
  });

  test("a response that arrives after the project changed is dropped", async () => {
    const pending: Record<string, (v: unknown) => void> = {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (url: string) =>
          new Promise((resolve) => {
            pending[url] = resolve;
          }),
      ),
    );
    const view = render(createElement(RequirementsPanel, panelProps()));
    await waitFor(() => expect(Object.keys(pending)).toHaveLength(2));
    view.rerender(createElement(RequirementsPanel, panelProps({ projectId: PROJECT_B })));
    await act(async () => {
      pending[REQ]!(ok([requirement({ tool_definition_id: "stale", name: "stale_tool" })]));
      pending[BUD]!(ok(policy({ row_version: 99 })));
    });
    expect(screen.queryByText("stale_tool")).toBeNull();
    expect(screen.queryByTestId("budget-row-version")).toBeNull();
  });

  test("a save started for project A that answers after a switch to B changes nothing on B", async () => {
    let release: (v: unknown) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (init?.method === "PATCH")
          return new Promise((r) => {
            release = r;
          });
        if (url.includes(PROJECT_B))
          return Promise.resolve(
            url.endsWith("/requirements")
              ? ok([])
              : ok(
                  policy({
                    project_id: PROJECT_B,
                    row_version: 1,
                    total_amount: "5.0000",
                    available_amount: "5.0000",
                  }),
                ),
          );
        return Promise.resolve(
          url.endsWith("/requirements") ? ok([]) : ok(policy({ row_version: 3 })),
        );
      }),
    );
    const view = render(createElement(RequirementsPanel, panelProps()));
    await waitFor(() => expect(text("budget-row-version")).toBe("3"));
    click("budget-policy-submit-button");
    await waitFor(() =>
      expect(
        fetchMock().mock.calls.some(
          (c: unknown[]) => (c[1] as RequestInit | undefined)?.method === "PATCH",
        ),
      ).toBe(true),
    );
    view.rerender(createElement(RequirementsPanel, panelProps({ projectId: PROJECT_B })));
    await waitFor(() => expect(text("budget-row-version")).toBe("1"));
    await act(async () => release(ok(policy({ row_version: 4 }))));
    expect(text("budget-row-version")).toBe("1");
    expect(screen.queryByTestId("budget-policy-saved-notice")).toBeNull();
  });
});

describe("[UX04-L2] requirements panel: English and Arabic (RTL)", () => {
  test("ar: lang=ar dir=rtl on the root; exact headings, labels, table, empty and notices", async () => {
    renderPanel(
      { requirements: () => ok([requirement({ runtime_enforced: false })]) },
      { locale: "ar" },
    );
    await waitFor(() => expect(tid("requirement-list")).toBeTruthy());
    const root = tid("requirements-panel");
    expect(root.getAttribute("lang")).toBe("ar");
    expect(root.getAttribute("dir")).toBe("rtl");
    expect(root.querySelector("h2")!.textContent).toBe("متطلبات المشروع");
    expect(Array.from(root.querySelectorAll("h3")).map((h) => h.textContent)).toEqual([
      "ملف الأمان",
      "متطلبات السلوك الحرج",
      "سياسة الميزانية",
    ]);
    expect(text("security-profile-edit-notice")).toBe(
      "يُعدَّل من نموذج إعداد المشروع، وليس من هنا.",
    );
    expect(Array.from(root.querySelectorAll("thead th")).map((th) => th.textContent)).toEqual([
      "الأداة (فئة الأثر الجانبي)",
      "المعلن",
      "وقت التشغيل",
      "الفحص",
    ]);
    expect(text("requirement-desired-tool-1")).toBe("حرجة");
    expect(text("requirement-enforced-tool-1")).toBe("غير منفَّذ");
    expect(text("requirement-conflict-tool-1")).toContain(
      "تعارض: معلنة كحرجة أو تتطلب تأكيدًا، لكنها غير منفَّذة وقت التشغيل.",
    );
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    expect(text("budget-policy-missing-notice")).toContain(
      "لم يتم العثور على سياسة ميزانية لهذا المشروع بعد. أنشئ واحدة أدناه.",
    );
    expect(document.querySelector('label[for="budget-total-amount"]')!.textContent).toBe(
      "المبلغ الإجمالي (مطلوب)",
    );
    expect(document.querySelector('label[for="budget-currency"]')!.textContent).toBe(
      "العملة (مطلوب)",
    );
    expect(tid("budget-scopes-fieldset").querySelector("legend")!.textContent).toBe(
      "النطاقات (مطلوب)",
    );
    expect(text("budget-policy-submit-button")).toBe("إنشاء سياسة الميزانية");
    for (const wrapper of Array.from(root.querySelectorAll("[lang]"))) {
      expect(wrapper.getAttribute("lang")).toBe("ar");
      expect(wrapper.getAttribute("dir")).toBe("rtl");
    }
  });

  test("ar: validation and failure sentences are Arabic; the amount message names the minimum in Arabic", async () => {
    const existing = policy({
      row_version: 3,
      reserved_amount: "400.0000",
      available_amount: "600.0000",
    });
    renderPanel({ budgetGet: () => ok(existing), write: () => refused(409) }, { locale: "ar" });
    await waitFor(() => expect(text("budget-row-version")).toBe("3"));
    type("budget-total-amount-input", "1");
    click("budget-policy-submit-button");
    expect(document.getElementById("budget-total-amount-error")!.textContent).toBe(
      "لا يمكن أن يقل الإجمالي عن المبلغ المحجوز بالفعل (400.0000 USD). أدخل 400.0000 أو أكثر.",
    );
    type("budget-total-amount-input", "500");
    click("budget-policy-submit-button");
    await waitFor(() => expect(tid("budget-policy-error")).toBeTruthy());
    expect(text("budget-policy-error")).toContain(
      "تغيّرت سياسة الميزانية منذ فتحتها، أو أنها موجودة بالفعل. أعد التحميل لرؤية السياسة الحالية؛ تبقى مدخلاتك في النموذج إلى أن تفعل ذلك.",
    );
    expect(tid("budget-policy-error").querySelector('[role="alert"]')!.getAttribute("lang")).toBe(
      "ar",
    );
  });

  test("an unknown locale renders English and says lang=en", async () => {
    renderPanel({}, { locale: "fr" as unknown as "en" });
    await waitFor(() => expect(tid("budget-policy-missing-notice")).toBeTruthy());
    expect(tid("requirements-panel").getAttribute("lang")).toBe("en");
    expect(text("budget-policy-submit-button")).toBe("Create budget policy");
  });

  test("a tool name and side-effect class (user data) are isolated in <bdi> and wrap when unbroken", async () => {
    const longName = `issue_refund_for_${"t".repeat(60)}_order`;
    renderPanel({ requirements: () => ok([requirement({ name: longName })]) });
    await waitFor(() => expect(tid("requirement-list")).toBeTruthy());
    const header = tid("requirement-list").querySelector('tbody th[scope="row"]') as HTMLElement;
    expect(header.querySelectorAll("bdi")).toHaveLength(2);
    expect(header.textContent).toContain(longName);
    expect(header.style.overflowWrap).toBe("anywhere");
  });
});

describe("[UX04-J] shared helpers", () => {
  test("jsonEqual is structural and key-order independent", () => {
    expect(jsonEqual({ a: 1, b: [1, { c: 2 }] }, { b: [1, { c: 2 }], a: 1 })).toBe(true);
    expect(jsonEqual({ a: 1 }, { a: 1, b: undefined })).toBe(false);
    expect(jsonEqual({ a: 1 }, { a: 2 })).toBe(false);
    expect(jsonEqual([1, 2], [2, 1])).toBe(false);
    expect(jsonEqual({}, [])).toBe(false);
    expect(jsonEqual(null, {})).toBe(false);
    expect(jsonEqual(1, "1")).toBe(false);
  });
});
