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

test("FTF-UX02 r2 severity-2 (required): a loading state is shown while the connection list is in flight", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => undefined)), // never resolves: still loading
  );

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  // Before this round, nothing at all was shown while `connections === null`
  // (no loading indicator, no empty state, no list) -- a dead screen.
  expect(screen.getByRole("status")).toBeTruthy();
  expect(screen.queryByTestId("dataset-source-empty-state")).toBeNull();
  expect(screen.queryByTestId("dataset-source-connection-list")?.children.length ?? 0).toBe(0);
});

test("FTF-UX02 r2 severity-2 (required): a list-fetch error is announced (role=alert), not a silent paragraph", async () => {
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

  const alertEl = await screen.findByRole("alert");
  expect(alertEl.textContent).toBe("internal error");
});

test("FTF-UX02 r2: the new-connection chrome (heading, labels, buttons) is bilingual, not hardcoded English", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  const root = screen.getByTestId("dataset-source-selector");
  expect(root.getAttribute("lang")).toBe("ar");
  expect(root.getAttribute("dir")).toBe("rtl");
  expect(screen.getByTestId("dataset-source-review-button").textContent).toMatch(/[؀-ۿ]/);
  expect(screen.getByLabelText(/الاسم المعروض/).tagName).toBe("INPUT");
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

test("FTF-UX02 empty state: no connections yet explains why and offers the first action", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-empty-state").textContent).toContain(
    "No dataset source connections yet",
  );
});

test("FTF-UX02 empty state is absent when a connection exists", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({ list: () => ({ ok: true, json: async () => [connection()] }) }),
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
  expect(screen.queryByTestId("dataset-source-empty-state")).toBeNull();
});

test("FTF-UX02 X4: the empty state is Arabic and right-to-left when locale is ar", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy());
  const empty = screen.getByTestId("dataset-source-empty-state");
  expect(empty.closest("[dir]")?.getAttribute("dir")).toBe("rtl");
  expect(empty.textContent).toMatch(/[\u0600-\u06FF]/);
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

// ---------------------------------------------------------------------------
// FTF-UX02 round 2, pre-review round 1 (independent L2-style review of PR #947):
// findings 2 (UNPINNED-F4 / connection_status), 3 (NOT-BILINGUAL-0500A), 4
// (ERRORS-NOT-ANNOUNCED-0500A), 6 (STALE-IDENTITY-0500A) and 7 (UX01-REBUILD-UNPINNED).
// ---------------------------------------------------------------------------

test("FTF-UX02 r2 pre-review r1 finding 2 (pinned, UNPINNED-F4): the connection option names its connection_status, in English and Arabic", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [connection({ connection_status: "revoked" })] }),
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
    expect(screen.getByTestId(`dataset-source-option-${connection().id}`).textContent).toContain(
      "Revoked",
    ),
  );
});

test("FTF-UX02 r2 pre-review r1 finding 2 (pinned): connection_status is Arabic when locale is ar", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({ list: () => ({ ok: true, json: async () => [connection()] }) }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId(`dataset-source-option-${connection().id}`).textContent).toContain(
      "نشط",
    ),
  );
});

test("FTF-UX02 r2 pre-review r1 finding 3 (pinned): the connection's display name is wrapped in <bdi>, not bare text", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({ list: () => ({ ok: true, json: async () => [connection()] }) }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => {
    const option = screen.getByTestId(`dataset-source-option-${connection().id}`);
    const bdi = option.querySelector("bdi");
    expect(bdi?.textContent).toBe(connection().display_name);
  });
});

test("FTF-UX02 r2 pre-review r1 finding 3 (pinned): the dataset-source heading is the exact Arabic translation when locale is ar", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "اتصالات مصادر البيانات" })).toBeTruthy(),
  );
});

test("FTF-UX02 r2 pre-review r1 finding 3 (pinned): the empty-state title is the exact Arabic translation, not left English", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-empty-state").textContent).toContain(
      "لا توجد اتصالات بمصادر البيانات بعد",
    ),
  );
});

test("FTF-UX02 r2 pre-review r1 finding 3 (pinned): 'Confirm and create' is the exact Arabic translation on the preview step", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "x" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-confirm-button").textContent).toBe("تأكيد وإنشاء"),
  );
});

test("FTF-UX02 r2 pre-review r1 finding 4 (ERRORS-NOT-ANNOUNCED-0500A): a rejected list fetch is a typed, announced, retryable error, not an endless loading state", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("network down"))),
  );

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  const alertEl = await screen.findByRole("alert");
  expect(alertEl.getAttribute("data-testid")).toBe("dataset-source-list-error");
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByTestId("dataset-source-list-retry-button")).toBeTruthy();
});

test("FTF-UX02 r2 pre-review r1 finding 4: a 200 body shaped {items: []} is a typed error, never a crash", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({ items: [] }) })),
  );

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-list-error")).toBeTruthy());
  // Proves the tree is still alive and did not unmount from a thrown render error.
  expect(screen.getByTestId("dataset-source-selector")).toBeTruthy();
});

test("FTF-UX02 r2 pre-review r1 finding 4: a 200 body of null is a typed error, not an endless spinner", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => null })),
  );

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-list-error")).toBeTruthy());
  expect(screen.queryByRole("status")).toBeNull();
});

test("FTF-UX02 r2 pre-review r1 finding 4: Retry re-issues the real list request and can recover", async () => {
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      calls += 1;
      if (calls === 1) return Promise.reject(new Error("network down"));
      return { ok: true, json: async () => [] };
    }),
  );

  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("dataset-source-list-retry-button")).toBeTruthy());
  fireEvent.click(screen.getByTestId("dataset-source-list-retry-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy());
  expect(calls).toBe(2);
});

test("FTF-UX02 r2 pre-review r1 finding 4: a rejected create request surfaces a role=alert error and re-enables the form, never silent", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [] }),
      onCreate: () => {
        throw new Error("network down");
      },
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "x" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());
  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));

  await waitFor(() => expect(screen.getByTestId("dataset-source-create-error")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-create-error").getAttribute("role")).toBe("alert");
  expect((screen.getByTestId("dataset-source-confirm-button") as HTMLButtonElement).disabled).toBe(
    false,
  );
});

test("FTF-UX02 r2 pre-review r1 finding 4: a rejected authorize request re-enables 'Check authorization', never stuck disabled", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [existing] }),
      onAuthorize: () => {
        throw new Error("network down");
      },
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
    expect(screen.getByTestId("dataset-source-authorization-error")).toBeTruthy(),
  );
  expect(screen.getByTestId("dataset-source-authorization-error").getAttribute("role")).toBe(
    "alert",
  );
  expect(
    (screen.getByTestId("dataset-source-check-authorization-button") as HTMLButtonElement).disabled,
  ).toBe(false);
});

test("FTF-UX02 r2 pre-review r1 finding 4 (pinned): the authorization-denied message carries role=alert", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [existing] }),
      onAuthorize: () => ({ ok: false, status: 403, json: async () => ({ detail: "denied" }) }),
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
    expect(screen.getByTestId("dataset-source-authorization-result-denied")).toBeTruthy(),
  );
  expect(
    screen.getByTestId("dataset-source-authorization-result-denied").getAttribute("role"),
  ).toBe("alert");
});

test("FTF-UX02 r2 pre-review r1 finding 6 (STALE-IDENTITY-0500A): changing project mid-flight shows loading immediately and a late reply for the old identity never overwrites the new one", async () => {
  let resolveFirst: ((value: unknown) => void) | undefined;
  const fetchMock = vi.fn((url: string) => {
    if (url === CONNECTIONS_URL) {
      return new Promise((resolve) => {
        resolveFirst = resolve;
      });
    }
    return Promise.resolve({ ok: true, json: async () => [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  const { rerender } = render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  expect(screen.getByRole("status")).toBeTruthy();

  const OTHER_PROJECT_ID = "44444444-4444-4444-4444-444444444444";
  rerender(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: OTHER_PROJECT_ID,
    }),
  );
  // Still (newly) loading -- a remounted instance, not the stale one left showing its old state.
  expect(screen.getByRole("status")).toBeTruthy();

  await waitFor(() => expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy());

  // The OLD identity's in-flight request now resolves late, with data for the OLD project.
  resolveFirst?.({ ok: true, json: async () => [connection()] });
  await new Promise((r) => setTimeout(r, 0));
  // The new identity's empty state must still be showing -- the late reply must not have landed.
  expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy();
  expect(screen.queryByTestId(`dataset-source-option-${connection().id}`)).toBeNull();
});

test("FTF-UX02 r2 pre-review r1 finding 7 (pinned, UX01-REBUILD-UNPINNED): the Review button is the real UX01 Button, not a native <button>", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-review-button").hasAttribute("data-variant")).toBe(
    true,
  );
});

test("FTF-UX02 r2 pre-review r1 finding 7 (pinned): the loading indicator carries aria-busy", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => undefined)),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
});

// ---------------------------------------------------------------------------
// FTF-UX02 round 2, pre-review round 2 (independent L2-style re-review of PR #947):
// findings 2 (F3a/Arabic-string surface), 4 (F4e + SERVER-DETAIL-ENGLISH-IN-AR +
// AUTH-RESULT-SILENT-AND-FOCUS-LOST), 5 (LABELS-ABOVE-FIELDS-PURPOSE-TO-CHECK), 6
// (STICKY-CHECKING-AND-BUTTON-PINS-UNPINNED) and 7 (EVIDENCE-ACCURACY).
// ---------------------------------------------------------------------------

test("FTF-UX02 r2 pre-review r2 finding 2 (F3a-UNPINNED surface): every new Arabic chrome string is the exact translation, not left English", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [existing] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId(`dataset-source-option-${existing.id}`)).toBeTruthy(),
  );
  // Authorization heading, Check-authorization button and the security-classification
  // field label -- none had an exact Arabic assertion before this round.
  expect(screen.getByTestId(`dataset-source-option-${existing.id}`).textContent).toContain("نشط");
  fireEvent.click(screen.getByTestId(`dataset-source-option-${existing.id}`));
  await waitFor(() => expect(screen.getByRole("heading", { name: "تفويض الغرض" })).toBeTruthy());
  expect(screen.getByTestId("dataset-source-check-authorization-button").textContent).toBe(
    "فحص التفويض",
  );
  const classificationLabel = document.querySelector(
    'label[for="dataset-source-security-classification"]',
  );
  expect(classificationLabel?.textContent).toBe("تصنيف الأمان (حدود الخصوصية)");
});

test("FTF-UX02 r2 pre-review r2 finding 2: a successful authorization check announces the exact Arabic 'Authorized' text", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [existing] }),
      onAuthorize: () => ({ ok: true, json: async () => ({ authorized: true }) }),
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
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
    expect(screen.getByTestId("dataset-source-authorization-result-authorized").textContent).toBe(
      "مُفوَّض",
    ),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 2: the preview notice and credential-provided text are the exact Arabic translations", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "x" },
  });
  fireEvent.change(screen.getByTestId("dataset-source-credential-input"), {
    target: { value: "secret" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-preview-notice").textContent).toContain(
    "راجع قبل الإنشاء.",
  );
  expect(screen.getByTestId("dataset-source-preview-credential").textContent).toBe(
    "تم تقديم بيانات الاعتماد",
  );
});

test("FTF-UX02 r2 pre-review r2 finding 2: the selected connection's name in the authorization panel is wrapped in <bdi>", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [existing] }) }));
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
  await waitFor(() => {
    const el = screen.getByTestId("dataset-source-authorization-selected-connection");
    expect(el.querySelector("bdi")?.textContent).toBe(existing.display_name);
  });
});

test("FTF-UX02 r2 pre-review r2 finding 4 (F4e-UNPINNED): a 5xx with no server detail shows the bilingual retry message, not raw 'request failed (N)'", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: false, status: 503, json: async () => ({}) }),
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
    expect(screen.getByTestId("dataset-source-list-error").textContent).toBe(
      "Something went wrong loading this. You can retry.",
    ),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 4 (SERVER-DETAIL-ENGLISH-IN-AR): a not-granted denial is a real Arabic sentence, not raw English server prose", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [existing] }),
      onAuthorize: () => ({
        ok: false,
        status: 403,
        json: async () => ({
          detail: `dataset source connection ${existing.id} is not granted for purpose 'baseline_only'`,
        }),
      }),
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
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
    target: { value: "baseline_only" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-check-authorization-button"));
  await waitFor(() => {
    const text = screen.getByTestId("dataset-source-authorization-result-denied").textContent!;
    expect(text).toBe("هذا الاتصال غير مُفوَّض للغرض: baseline_only");
    expect(text).not.toContain("is not granted");
  });
});

test("FTF-UX02 r2 pre-review r2 finding 4 (SERVER-DETAIL-ENGLISH-IN-AR): a 400 validation detail becomes a real Arabic field-required sentence", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [] }),
      onCreate: () => ({
        ok: false,
        status: 400,
        json: async () => ({ detail: "display_name is required and must be a string" }),
      }),
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "x" },
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
      "الاسم المعروض مطلوب.",
    ),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 4 (SERVER-DETAIL-ENGLISH-IN-AR): an unrecognised detail falls back to a generic Arabic retry message, never raw English", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [] }),
      onCreate: () => ({
        ok: false,
        status: 409,
        json: async () => ({ detail: "display_name already in use" }),
      }),
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "x" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());
  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));
  await waitFor(() => {
    const text = screen.getByTestId("dataset-source-create-error").textContent!;
    expect(text).toBe("حدث خطأ غير متوقع. يمكنك إعادة المحاولة.");
    expect(text).not.toContain("already in use");
  });
});

test("FTF-UX02 r2 pre-review r2 finding 4 (SERVER-DETAIL-ENGLISH-IN-AR): English locale is unaffected -- the server's own detail still shows verbatim", async () => {
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

test("FTF-UX02 r2 pre-review r3 (coverage): the Arabic network-error retry message is the exact catalogue translation, not just 'is in Arabic'", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => {
        throw new Error("network down");
      },
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-list-error").textContent).toBe(
      "حدث خطأ في الشبكة. يمكنك إعادة المحاولة.",
    ),
  );
});

test("FTF-UX02 r2 pre-review r3 (coverage): the Arabic request-failed retry message is the exact catalogue translation when the server sends no detail", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: false, status: 503, json: async () => ({}) }),
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-list-error").textContent).toBe(
      "حدث خطأ أثناء التحميل. يمكنك إعادة المحاولة.",
    ),
  );
});

test("FTF-UX02 r2 pre-review r3 (coverage): the Arabic malformed-response retry message is the exact catalogue translation for a non-array 200 body", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => ({}) }),
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-list-error").textContent).toBe(
      "تعذّرت قراءة رد الخادم. يمكنك إعادة المحاولة.",
    ),
  );
});

test("FTF-UX02 r2 pre-review r3 (coverage): the retry button's own label is the exact Arabic catalogue translation, not just present", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => {
        throw new Error("network down");
      },
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-list-retry-button").textContent).toBe(
      "إعادة المحاولة",
    ),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 5 (AUTH-RESULT-SILENT-AND-FOCUS-LOST): the Authorized result carries role=status", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [existing] }),
      onAuthorize: () => ({ ok: true, json: async () => ({ authorized: true }) }),
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
    expect(
      screen.getByTestId("dataset-source-authorization-result-authorized").getAttribute("role"),
    ).toBe("status"),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 5 (AUTH-RESULT-SILENT-AND-FOCUS-LOST): focus returns to 'Check authorization' after it re-enables, never <body>", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [existing] }),
      onAuthorize: () => {
        throw new Error("network down");
      },
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
    expect(document.activeElement).toBe(
      screen.getByTestId("dataset-source-check-authorization-button"),
    ),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 5: focus returns to 'Confirm and create' after a create failure, never <body>", async () => {
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [] }),
      onCreate: () => ({ ok: false, status: 409, json: async () => ({ detail: "conflict" }) }),
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "x" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());
  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByTestId("dataset-source-confirm-button")),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 5: focus returns to the new 'Retry' button when the retried fetch fails again, never <body>", async () => {
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      calls += 1;
      return Promise.reject(new Error("still down"));
    }),
  );
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-list-retry-button")).toBeTruthy());
  fireEvent.click(screen.getByTestId("dataset-source-list-retry-button"));
  await waitFor(() => expect(calls).toBe(2));
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByTestId("dataset-source-list-retry-button")),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 6 (STICKY-CHECKING-AND-BUTTON-PINS-UNPINNED): a non-403 authorize failure leaves 'Check authorization' re-enabled, never stuck", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal(
    "fetch",
    mockFetch({
      list: () => ({ ok: true, json: async () => [existing] }),
      onAuthorize: () => ({ ok: false, status: 500, json: async () => ({ detail: "boom" }) }),
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
    expect(screen.getByTestId("dataset-source-authorization-error")).toBeTruthy(),
  );
  expect(
    (screen.getByTestId("dataset-source-check-authorization-button") as HTMLButtonElement).disabled,
  ).toBe(false);
  expect(screen.queryByTestId("dataset-source-authorization-result-authorized")).toBeNull();
});

test("FTF-UX02 r2 pre-review r2 finding 6 (pinned): 'Confirm and create' and 'Check authorization' render the real UX01 Button, not a native <button>", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [existing] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "x" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "confidential" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  await waitFor(() => expect(screen.getByTestId("dataset-source-preview")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-confirm-button").hasAttribute("data-variant")).toBe(
    true,
  );
  fireEvent.click(screen.getByTestId(`dataset-source-option-${existing.id}`));
  await waitFor(() =>
    expect(
      screen.getByTestId("dataset-source-check-authorization-button").hasAttribute("data-variant"),
    ).toBe(true),
  );
});

test("FTF-UX02 r2 pre-review r2 finding 5 (LABELS-ABOVE-FIELDS-PURPOSE-TO-CHECK, DOM order pin): the authorization panel's label has a block style, same as the others", async () => {
  const existing = connection({ allowed_purposes: ["training"] });
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [existing] }) }));
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
  await waitFor(() => {
    const label = document.querySelector('label[for="dataset-source-purpose-to-check"]');
    expect((label as HTMLElement | null)?.style.display).toBe("block");
  });
});

test("FTF-UX02 r2 pre-review r1 finding 5 (LABELS-ABOVE-FIELDS misclaimed): the Credential label is not duplicated as '(optional) (optional)'", async () => {
  vi.stubGlobal("fetch", mockFetch({ list: () => ({ ok: true, json: async () => [] }) }));
  render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("dataset-source-form")).toBeTruthy());
  const label = document.querySelector('label[for="dataset-source-credential"]');
  expect(label?.textContent?.replace(/\s+/g, " ").trim()).toBe("Credential (optional)");
});
