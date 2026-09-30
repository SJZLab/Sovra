// @vitest-environment jsdom
//
// FTF-0500A -- Dataset source selector + purpose-authorization UI.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `DatasetSourceSelectorPurposeAuthorizationUi`,
// drives real user interaction and asserts on the real `fetch` calls it
// makes against FTF-0500's live CRUD/authorize API contract -- request
// method, URL and JSON body -- never a snapshot or hand-written HTML
// string. `fetch` itself is mocked (no live server in a unit test), but
// the component code path exercised is the real one that would hit the
// actual backend.
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E (no Playwright here -- the same precedent `ftf_0408.spec.ts`/
// `ftf_04a03.spec.ts` already established).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  DatasetSourceConnectionSummary,
  DatasetSourceSelectorPurposeAuthorizationUi,
  datasetSourceAuthorizeUrl,
  datasetSourceConnectionsUrl,
} from "../components/dataset-factory/dataset_source_selector_purpose_authorization_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";

function connection(
  overrides: Partial<DatasetSourceConnectionSummary> = {},
): DatasetSourceConnectionSummary {
  return {
    id: "33333333-3333-3333-3333-333333333333",
    organization_id: ORG_ID,
    project_id: PROJECT_ID,
    source_type: "s3",
    display_name: "Support tickets export",
    has_credential: true,
    has_connection_config: false,
    allowed_purposes: ["training"],
    security_classification: "confidential",
    connection_status: "active",
    last_validated_at: null,
    created_by: "user-1",
    created_at: "2026-09-01T00:00:00+00:00",
    updated_at: "2026-09-01T00:00:00+00:00",
    revoked_at: null,
    ...overrides,
  };
}

const CONNECTIONS_URL = datasetSourceConnectionsUrl(API_BASE, ORG_ID, PROJECT_ID);

function mockFetch(handlers: {
  list?: () => { ok: boolean; status?: number; json: () => Promise<unknown> };
  onCreate?: (init: RequestInit) => { ok: boolean; status?: number; json: () => Promise<unknown> };
  onAuthorize?: (
    connectionId: string,
    init: RequestInit,
  ) => { ok: boolean; status?: number; json: () => Promise<unknown> };
}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url === CONNECTIONS_URL) {
      if (!init || init.method === undefined) {
        return handlers.list ? handlers.list() : { ok: true, json: async () => [] };
      }
      if (init.method === "POST" && handlers.onCreate) {
        return handlers.onCreate(init);
      }
    }
    const authorizeMatch = url.match(/dataset-sources\/([^/]+)\/authorize$/);
    const connectionId = authorizeMatch?.[1];
    if (connectionId && init?.method === "POST" && handlers.onAuthorize) {
      return handlers.onAuthorize(connectionId, init);
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

test("URL helpers point at the real FTF-0500 API contract", () => {
  expect(datasetSourceConnectionsUrl(API_BASE, ORG_ID, PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}/dataset-sources`,
  );
  expect(datasetSourceAuthorizeUrl(API_BASE, ORG_ID, PROJECT_ID, "conn-1")).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}/dataset-sources/conn-1/authorize`,
  );
});

test("existing connections render from the real backend list, none pre-selected", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [connection()] }),
    }),
  );

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() =>
    expect(screen.getByTestId(`dataset-source-option-${connection().id}`)).toBeTruthy(),
  );
  expect(screen.queryByTestId("dataset-source-authorization-section")).toBeNull();
});

test("the Review button stays disabled until a purpose and privacy boundary are explicitly chosen", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  const reviewButton = screen.getByTestId("dataset-source-review-button") as HTMLButtonElement;
  expect(reviewButton.disabled).toBe(true);

  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "Support tickets export" },
  });
  expect(reviewButton.disabled).toBe(true);

  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  expect(reviewButton.disabled).toBe(true);

  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  expect(reviewButton.disabled).toBe(false);
});

test("Review shows a real preview of exactly what will be submitted, never the raw credential, and does not call the backend yet", async () => {
  const fetchMock = mockFetch({ list: () => ({ ok: true, json: async () => [] }) });
  vi.stubGlobal("fetch", fetchMock);

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());

  fireEvent.change(screen.getByTestId("dataset-source-type-select"), {
    target: { value: "s3" },
  });
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "Support tickets export" },
  });
  fireEvent.change(screen.getByTestId("dataset-source-credential-input"), {
    target: { value: "super-secret-token" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));

  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-preview-source-type").textContent).toBe("s3");
  expect(screen.getByTestId("dataset-source-preview-display-name").textContent).toBe(
    "Support tickets export",
  );
  expect(screen.getByTestId("dataset-source-preview-credential").textContent).toBe(
    "credential provided",
  );
  expect(screen.getByTestId("dataset-source-preview-purposes").textContent).toBe("training");
  expect(screen.getByTestId("dataset-source-preview-security-classification").textContent).toBe(
    "confidential",
  );
  // The raw credential value is never echoed anywhere in the preview.
  expect(screen.queryByText("super-secret-token")).toBeNull();
  // No POST has been made yet -- "Review" only previews, it never submits.
  expect(fetchMock.mock.calls.some((call) => call[1]?.method === "POST")).toBe(false);
});

test("Back returns to the form without submitting anything", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "Support tickets export" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());

  fireEvent.click(screen.getByTestId("dataset-source-back-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  expect((screen.getByTestId("dataset-source-display-name-input") as HTMLInputElement).value).toBe(
    "Support tickets export",
  );
});

test("Confirm and create POSTs the real payload, selects the new connection, and resets the form -- a real failure preserves the draft", async () => {
  const created = connection({ id: "new-conn-id", allowed_purposes: ["training"] });
  const fetchMock = mockFetch({
    list: () => ({ ok: true, json: async () => [] }),
    onCreate: (init) => {
      expect(init.method).toBe("POST");
      return { ok: true, json: async () => created };
    },
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-type-select"), {
    target: { value: "s3" },
  });
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "Support tickets export" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());

  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));

  await waitFor(() => {
    const writeCall = fetchMock.mock.calls.find(
      (call) => call[0] === CONNECTIONS_URL && call[1]?.method === "POST",
    );
    expect(writeCall).toBeTruthy();
    const body = JSON.parse(writeCall![1]!.body as string);
    expect(body).toEqual({
      source_type: "s3",
      display_name: "Support tickets export",
      allowed_purposes: ["training"],
      security_classification: "confidential",
    });
    expect(Object.prototype.hasOwnProperty.call(body, "credential")).toBe(false);
  });

  // Form resets and the newly created connection is selected for the
  // authorization panel below.
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  expect((screen.getByTestId("dataset-source-display-name-input") as HTMLInputElement).value).toBe(
    "",
  );
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-authorization-section")).toBeTruthy(),
  );
});

test("a real creation failure surfaces the server error and preserves the user's own draft", async () => {
  const fetchMock = mockFetch({
    list: () => ({ ok: true, json: async () => [] }),
    onCreate: () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: "display_name already in use" }),
    }),
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "Support tickets export" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());
  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));

  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-create-error").textContent).toBe(
      "display_name already in use",
    ),
  );
  // Still on the preview step, draft intact -- nothing was silently reverted.
  expect(screen.getByTestId("dataset-source-preview-display-name").textContent).toBe(
    "Support tickets export",
  );
});

test("selecting an existing connection reveals the authorization panel; a granted purpose is authorized for real", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  const fetchMock = mockFetch({
    list: () => ({ ok: true, json: async () => [existing] }),
    onAuthorize: (connectionId, init) => {
      expect(connectionId).toBe(existing.id);
      const body = JSON.parse(init.body as string);
      expect(body).toEqual({ purpose: "training" });
      return { ok: true, json: async () => ({ authorized: true, purpose: "training" }) };
    },
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() =>
    expect(screen.getByTestId(`dataset-source-option-${existing.id}`)).toBeTruthy(),
  );
  fireEvent.click(screen.getByTestId(`dataset-source-option-${existing.id}`));

  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-authorization-section")).toBeTruthy(),
  );
  fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
    target: { value: "training" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-check-authorization-button"));

  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-authorization-result-authorized")).toBeTruthy(),
  );
});

test("checking a purpose the connection was never granted surfaces the real 403 denial, not a client-side guess", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  const fetchMock = mockFetch({
    list: () => ({ ok: true, json: async () => [existing] }),
    onAuthorize: () => ({
      ok: false,
      status: 403,
      json: async () => ({
        detail: `dataset source connection ${existing.id} is not granted for purpose 'baseline_only'`,
      }),
    }),
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() =>
    expect(screen.getByTestId(`dataset-source-option-${existing.id}`)).toBeTruthy(),
  );
  fireEvent.click(screen.getByTestId(`dataset-source-option-${existing.id}`));
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-authorization-section")).toBeTruthy(),
  );

  // The dropdown deliberately offers every purpose in the closed
  // vocabulary, not only the ones this connection already grants -- so
  // this negative case is provable at all.
  fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
    target: { value: "baseline_only" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-check-authorization-button"));

  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-authorization-result-denied").textContent).toBe(
      `dataset source connection ${existing.id} is not granted for purpose 'baseline_only'`,
    ),
  );
  expect(screen.queryByTestId("dataset-source-authorization-result-authorized")).toBeNull();
});

test("a real list-fetch failure surfaces the server error, not a silent empty list", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: false, status: 500, json: async () => ({ detail: "internal error" }) }),
    }),
  );

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-list-error").textContent).toBe("internal error"),
  );
});
