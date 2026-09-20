// @vitest-environment jsdom
//
// FTF-0401 -- Project create/edit form.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `ProjectSetupForm`, drives real user
// interaction (typing, submitting) and asserts on the real `fetch` calls
// it makes against the live FTF-0401 API contract -- request method, URL
// and JSON body -- never a snapshot or hand-written HTML string. `fetch`
// itself is mocked (no live server in a unit test), but the component
// code path exercised is the real one that would hit the actual backend.
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E (no Playwright here -- see the component's own docstring
// for why: no Next.js app shell exists yet to navigate within, the same
// precedent `ftf_0101.spec.ts` already established).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  ProjectRecord,
  ProjectSetupForm,
  projectItemUrl,
  projectUiStateUrl,
  projectsCollectionUrl,
} from "../components/project-mandate/project_schema_api_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const WORKSPACE_ID = "22222222-2222-2222-2222-222222222222";

function existingProject(overrides: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    id: "33333333-3333-3333-3333-333333333333",
    organization_id: ORG_ID,
    workspace_id: WORKSPACE_ID,
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
    ...overrides,
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("URL helpers point at the real FTF-0401 API contract", () => {
  expect(projectsCollectionUrl(API_BASE, ORG_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects`,
  );
  expect(projectItemUrl(API_BASE, ORG_ID, "p1")).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/p1`,
  );
  expect(projectUiStateUrl(API_BASE, ORG_ID, "p1")).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/p1/ui-state`,
  );
});

test("create mode: submitting a generic project POSTs the real payload, agent_objective is null", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () =>
      existingProject({ project_type: "generic_model_tuning", agent_objective: null }),
  });

  const onSaved = vi.fn();
  render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      workspaceId: WORKSPACE_ID,
      onSaved,
    }),
  );

  fireEvent.change(screen.getByTestId("project-name-input"), { target: { value: "my-project" } });
  fireEvent.click(screen.getByTestId("project-submit-button"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [url, init] = fetchMock.mock.calls[0]!;
  expect(url).toBe(projectsCollectionUrl(API_BASE, ORG_ID));
  expect(init.method).toBe("POST");
  const body = JSON.parse(init.body as string);
  expect(body).toEqual({
    workspace_id: WORKSPACE_ID,
    name: "my-project",
    project_type: "generic_model_tuning",
    description: null,
    agent_objective: null,
    security_profile: "standard",
  });
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
});

test("create mode: selecting agentic_product requires and sends a real agent_objective", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => existingProject() });

  render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      workspaceId: WORKSPACE_ID,
    }),
  );

  fireEvent.change(screen.getByTestId("project-name-input"), { target: { value: "agent-1" } });
  fireEvent.change(screen.getByTestId("project-type-select"), {
    target: { value: "agentic_product" },
  });
  expect(screen.getByTestId("agent-objective-input")).toBeTruthy();
  fireEvent.change(screen.getByTestId("agent-objective-input"), {
    target: { value: "Automate PR review comments." },
  });
  fireEvent.click(screen.getByTestId("project-submit-button"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const body = JSON.parse(fetchMock.mock.calls[0]![1].body as string);
  expect(body.project_type).toBe("agentic_product");
  expect(body.agent_objective).toBe("Automate PR review comments.");
});

test("edit mode: project_type control is disabled (backend immutability mirrored in the UI)", () => {
  render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      workspaceId: WORKSPACE_ID,
      project: existingProject(),
    }),
  );
  const select = screen.getByTestId("project-type-select") as HTMLSelectElement;
  expect(select.disabled).toBe(true);
  expect(select.value).toBe("agentic_product");
});

test("edit mode: submitting PATCHes with the real expected_row_version, never project_type", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  const project = existingProject({ row_version: 4 });
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ ...project, row_version: 5 }) });

  render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      workspaceId: WORKSPACE_ID,
      project,
    }),
  );
  fireEvent.change(screen.getByTestId("project-description-input"), {
    target: { value: "updated description" },
  });
  fireEvent.click(screen.getByTestId("project-submit-button"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [url, init] = fetchMock.mock.calls[0]!;
  expect(url).toBe(projectItemUrl(API_BASE, ORG_ID, project.id));
  expect(init.method).toBe("PATCH");
  const body = JSON.parse(init.body as string);
  expect(body.expected_row_version).toBe(4);
  expect(body.description).toBe("updated description");
  expect(Object.prototype.hasOwnProperty.call(body, "project_type")).toBe(false);
});

test("edit mode: setup_incomplete notice shows only for an agentic project not yet active", () => {
  render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      workspaceId: WORKSPACE_ID,
      project: existingProject({ status: "setup_incomplete" }),
    }),
  );
  expect(screen.getByTestId("setup-incomplete-notice").textContent).toContain("Setup incomplete");
});

test("edit mode: Save draft POSTs real ui_state to the ui-state route", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ saved: true }) });

  const project = existingProject();
  render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      workspaceId: WORKSPACE_ID,
      project,
    }),
  );
  fireEvent.click(screen.getByTestId("project-save-draft-button"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [url, init] = fetchMock.mock.calls[0]!;
  expect(url).toBe(projectUiStateUrl(API_BASE, ORG_ID, project.id));
  expect(init.method).toBe("POST");
  const body = JSON.parse(init.body as string);
  expect(body.ui_state).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByTestId("project-draft-saved-notice").textContent).toBe("Draft saved"),
  );
});

test("create mode: Save draft button is disabled -- nothing exists yet to attach draft state to", () => {
  render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      workspaceId: WORKSPACE_ID,
    }),
  );
  const button = screen.getByTestId("project-save-draft-button") as HTMLButtonElement;
  expect(button.disabled).toBe(true);
});

test("a failed submit surfaces the real server-provided error detail, never a generic swallow", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status: 409,
    json: async () => ({ detail: "expected_row_version mismatch" }),
  });

  render(
    createElement(ProjectSetupForm, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      workspaceId: WORKSPACE_ID,
      project: existingProject(),
    }),
  );
  fireEvent.click(screen.getByTestId("project-submit-button"));

  await waitFor(() =>
    expect(screen.getByTestId("project-form-error").textContent).toBe(
      "expected_row_version mismatch",
    ),
  );
});
