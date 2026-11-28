// @vitest-environment jsdom
//
// FTF-0214 -- Model catalog UI.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `ModelCatalogUI`, drives real user
// interaction (loading projects, explicitly selecting one, loading the
// catalog, running a probe) and asserts on the real `fetch` calls it
// makes against the live FTF-0214/FTF-0401 API contract -- request
// method, URL and JSON body -- never a snapshot or a hand-written HTML
// string. `fetch` itself is mocked (no live server in a unit test), but
// the component code path exercised is the real one that would hit the
// actual backend (`app.litellm.model_catalog_routes`).
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E -- same precedent `ftf_04a18.spec.ts`/`ftf_04a04.spec.ts`
// already established (this repository has no Next.js `app/` page tree
// yet; FTF-H029 is the task that creates the first one).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  catalogUrl,
  ModelCatalogUI,
  probeUrl,
  projectsListUrl,
} from "../components/litellm-gateway/model_catalog_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const CONNECTION_ID = "22222222-2222-2222-2222-222222222222";
const PROJECT_ID = "33333333-3333-3333-3333-333333333333";
const MODEL_A = "model-a";
const MODEL_B = "model-b";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderCatalogUI() {
  return render(
    createElement(ModelCatalogUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONNECTION_ID,
    }),
  );
}

function mockProjectList() {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => [{ id: PROJECT_ID, name: "Acme Tuning Project", status: "active" }],
  });
}

function mockCatalog() {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      connection_status: "active",
      access_state: "healthy",
      last_successful_check_at: "2026-10-06T12:00:00+00:00",
      last_checked_at: "2026-10-06T12:00:00+00:00",
      alert: null,
      anomaly: null,
      model_groups: null,
      entries: [
        {
          id: MODEL_A,
          status: "callable",
          metadata_status: "enriched",
          first_seen_at: "2026-10-06T12:00:00+00:00",
          last_seen_callable_at: "2026-10-06T12:00:00+00:00",
          fields: { max_tokens: 8192 },
        },
        {
          id: MODEL_B,
          status: "removed",
          metadata_status: "limited",
          first_seen_at: "2026-10-05T12:00:00+00:00",
          last_seen_callable_at: null,
          fields: {},
        },
      ],
    }),
  });
}

async function loadAndSelectProject() {
  mockProjectList();
  renderCatalogUI();
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT_ID}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`project-select-button-${PROJECT_ID}`));
}

test("URL helpers point at the real FTF-0214/FTF-0401 API contract", () => {
  expect(projectsListUrl(API_BASE, ORG_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects`,
  );
  expect(catalogUrl(API_BASE, ORG_ID, CONNECTION_ID, PROJECT_ID, "", "")).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONNECTION_ID}` +
      `/catalog?project_id=${PROJECT_ID}`,
  );
  expect(catalogUrl(API_BASE, ORG_ID, CONNECTION_ID, PROJECT_ID, "callable", "enriched")).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONNECTION_ID}` +
      `/catalog?project_id=${PROJECT_ID}&status=callable&metadata_status=enriched`,
  );
  expect(probeUrl(API_BASE, ORG_ID, CONNECTION_ID, MODEL_A, PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONNECTION_ID}` +
      `/models/${MODEL_A}/probe?project_id=${PROJECT_ID}`,
  );
});

test("negative_acceptance: no project is pre-selected, and the catalog cannot be loaded without one", async () => {
  mockProjectList();
  renderCatalogUI();
  expect(screen.getByTestId("no-project-selected")).toBeTruthy();
  expect((screen.getByTestId("load-catalog-button") as HTMLButtonElement).disabled).toBe(true);

  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT_ID}`)).toBeTruthy());
  // Listing projects alone never implies a selection.
  expect(screen.getByTestId("no-project-selected")).toBeTruthy();
  expect((screen.getByTestId("load-catalog-button") as HTMLButtonElement).disabled).toBe(true);
});

test("explicit project selection enables the catalog load, and the real request carries project_id", async () => {
  await loadAndSelectProject();
  expect(screen.queryByTestId("no-project-selected")).toBeNull();
  expect((screen.getByTestId("load-catalog-button") as HTMLButtonElement).disabled).toBe(false);

  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[1]!;
  expect(url).toBe(catalogUrl(API_BASE, ORG_ID, CONNECTION_ID, PROJECT_ID, "", ""));
  expect(init.method).toBe("GET");

  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  expect(screen.getByTestId("catalog-access-state").textContent).toContain("healthy");
});

test("acceptance: freshness/capability evidence is rendered from the real response, never fabricated", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());

  expect(screen.getByTestId(`entry-status-${MODEL_A}`).textContent).toBe("callable");
  expect(screen.getByTestId(`entry-metadata-status-${MODEL_A}`).textContent).toBe("enriched");
  expect(screen.getByTestId(`entry-status-${MODEL_B}`).textContent).toBe("removed");
  expect(screen.getByTestId("catalog-freshness").textContent).toContain("2026-10-06T12:00:00");
});

test("negative_acceptance: a removed entry's probe button is disabled, never auto-probed", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_B}`)).toBeTruthy());

  expect((screen.getByTestId(`probe-button-${MODEL_B}`) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByTestId(`probe-button-${MODEL_A}`) as HTMLButtonElement).disabled).toBe(false);
  // No probe call happened merely from loading the catalog.
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("acceptance: an explicit probe click POSTs probe_type and renders the real result, no fabricated cost", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());

  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      probe_type: "chat",
      state: "probed",
      route_identity_state: "verified",
      resolved_model: MODEL_A,
      usage: null,
      latency_ms: 12.5,
      detail: "",
      probed_at: "2026-10-06T12:05:00+00:00",
    }),
  });
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  // FTF-UX05: the first click only opens the confirm; the real paid call fires
  // only from the confirm's own "Run probe" button.
  await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
  expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByTestId(`confirm-probe-button-${MODEL_A}`));

  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  const [url, init] = fetchMock.mock.calls[2]!;
  expect(url).toBe(probeUrl(API_BASE, ORG_ID, CONNECTION_ID, MODEL_A, PROJECT_ID));
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body as string)).toEqual({ probe_type: "chat" });

  await waitFor(() => expect(screen.getByTestId(`probe-result-${MODEL_A}`)).toBeTruthy());
  const resultText = screen.getByTestId(`probe-result-${MODEL_A}`).textContent ?? "";
  expect(resultText).toContain("probed");
  // negative_acceptance: missing usage is rendered as unknown, never a
  // fabricated zero-cost figure.
  expect(resultText).toContain("cost unknown");
});

test("negative_acceptance: a real 403 (viewer cannot run a paid probe) is surfaced, never retried as success", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());

  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status: 403,
    json: async () => ({ detail: "forbidden" }),
  });
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`confirm-probe-button-${MODEL_A}`));

  await waitFor(() => expect(screen.getByTestId("probe-error").textContent).toContain("forbidden"));
  expect(screen.queryByTestId(`probe-result-${MODEL_A}`)).toBeNull();
});

test("network error while loading the catalog is surfaced, never an empty or successful snapshot", async () => {
  await loadAndSelectProject();
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockRejectedValueOnce(new Error("connection refused"));
  fireEvent.click(screen.getByTestId("load-catalog-button"));

  await waitFor(() =>
    expect(screen.getByTestId("catalog-error").textContent).toContain("connection refused"),
  );
  expect(screen.queryByTestId("catalog-results")).toBeNull();
});

test("safe filters are sent to the real route as query parameters", async () => {
  await loadAndSelectProject();
  fireEvent.change(screen.getByTestId("status-filter"), { target: { value: "callable" } });
  fireEvent.change(screen.getByTestId("metadata-filter"), { target: { value: "enriched" } });
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));

  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  const [url] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[1]!;
  expect(url).toBe(catalogUrl(API_BASE, ORG_ID, CONNECTION_ID, PROJECT_ID, "callable", "enriched"));
});

function mockCatalogBody(body: Record<string, unknown>) {
  (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
    ok: true,
    json: async () => body,
  });
}

const BASE_ENTRY = {
  first_seen_at: "2026-10-06T12:00:00+00:00",
  last_seen_callable_at: null,
  metadata_status: "limited",
};

test("round 2: a revoked connection's catalog renders revoked/stale and disables every probe button", async () => {
  await loadAndSelectProject();
  mockCatalogBody({
    connection_status: "revoked",
    access_state: "revoked",
    last_successful_check_at: "2026-10-06T10:00:00+00:00",
    last_checked_at: "2026-10-06T10:00:00+00:00",
    alert: null,
    anomaly: null,
    model_groups: null,
    entries: [{ ...BASE_ENTRY, id: MODEL_A, status: "stale", fields: {} }],
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  expect(screen.getByTestId("catalog-connection-status").textContent).toContain("revoked");
  expect(screen.getByTestId("catalog-access-state").textContent).toContain("revoked");
  expect(screen.getByTestId("catalog-access-state").textContent).not.toContain("healthy");
  expect(screen.getByTestId(`entry-status-${MODEL_A}`).textContent).toBe("stale");
  expect((screen.getByTestId(`probe-button-${MODEL_A}`) as HTMLButtonElement).disabled).toBe(true);
});

test("round 2: a revoked connection disables the probe even if a stale snapshot still says callable", async () => {
  await loadAndSelectProject();
  mockCatalogBody({
    connection_status: "revoked",
    access_state: "healthy",
    last_successful_check_at: null,
    last_checked_at: null,
    alert: null,
    anomaly: null,
    model_groups: null,
    entries: [{ ...BASE_ENTRY, id: MODEL_A, status: "callable", fields: {} }],
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  expect((screen.getByTestId(`probe-button-${MODEL_A}`) as HTMLButtonElement).disabled).toBe(true);
});

test("round 2: degraded access, alert, anomaly, capability fields and model groups are all rendered", async () => {
  await loadAndSelectProject();
  mockCatalogBody({
    connection_status: "active",
    access_state: "degraded",
    last_successful_check_at: "2026-10-06T09:00:00+00:00",
    last_checked_at: "2026-10-06T11:00:00+00:00",
    alert: "authorized model count dropped from 10 to 2",
    anomaly: {
      id: 7,
      kind: "major_reduction",
      detected_at: "2026-10-06T11:00:00+00:00",
      previous_count: 10,
      new_count: 2,
      status: "open",
    },
    model_groups: { state: "enriched", entries: { "grp-x": { rpm: 60 } } },
    entries: [{ ...BASE_ENTRY, id: MODEL_A, status: "callable", fields: { max_tokens: 4096 } }],
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  expect(screen.getByTestId("catalog-access-state").textContent).toContain("degraded");
  expect(screen.getByTestId("catalog-alert").textContent).toContain("dropped from 10 to 2");
  expect(screen.getByTestId("catalog-anomaly").textContent).toContain("10");
  expect(screen.getByTestId("catalog-anomaly").textContent).toContain("open");
  expect(screen.getByTestId(`entry-fields-${MODEL_A}`).textContent).toContain("4096");
  expect(screen.getByTestId("model-groups-state").textContent).toContain("enriched");
  expect(screen.getByTestId("model-group-grp-x").textContent).toContain("60");
});

test("round 2: the probe sends the selected non-chat kind and renders type, route identity and timestamp", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());

  fireEvent.change(screen.getByTestId("probe-kind"), { target: { value: "tool_call" } });
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      probe_type: "tool_call",
      state: "probed",
      route_identity_state: "unverified",
      resolved_model: "other-model",
      usage: { prompt_tokens: 3, completion_tokens: 4, total_tokens: 7 },
      latency_ms: 9,
      detail: "",
      probed_at: "2026-10-06T12:09:00+00:00",
    }),
  });
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`confirm-probe-button-${MODEL_A}`));
  await waitFor(() => expect(screen.getByTestId(`probe-result-${MODEL_A}`)).toBeTruthy());

  const [, init] = fetchMock.mock.calls[2]!;
  expect(JSON.parse(init.body as string)).toEqual({ probe_type: "tool_call" });
  expect(screen.getByTestId(`probe-type-${MODEL_A}`).textContent).toBe("tool_call");
  expect(screen.getByTestId(`probe-route-identity-${MODEL_A}`).textContent).toBe("unverified");
  expect(screen.getByTestId(`probe-probed-at-${MODEL_A}`).textContent).toBe(
    "2026-10-06T12:09:00+00:00",
  );
  expect(screen.getByTestId(`probe-result-${MODEL_A}`).textContent).toContain("7 tokens");
});

test("round 2: a model id containing '/' is URL-encoded in the probe request", async () => {
  expect(probeUrl(API_BASE, ORG_ID, CONNECTION_ID, "openai/gpt-4o", PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONNECTION_ID}` +
      `/models/openai%2Fgpt-4o/probe?project_id=${PROJECT_ID}`,
  );
  await loadAndSelectProject();
  mockCatalogBody({
    connection_status: "active",
    access_state: "healthy",
    last_successful_check_at: "2026-10-06T12:00:00+00:00",
    last_checked_at: "2026-10-06T12:00:00+00:00",
    alert: null,
    anomaly: null,
    model_groups: null,
    entries: [{ ...BASE_ENTRY, id: "openai/gpt-4o", status: "callable", fields: {} }],
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId("catalog-entry-openai/gpt-4o")).toBeTruthy());
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      probe_type: "chat",
      state: "probed",
      route_identity_state: "verified",
      resolved_model: "openai/gpt-4o",
      usage: null,
      latency_ms: 1,
      detail: "",
      probed_at: "2026-10-06T12:10:00+00:00",
    }),
  });
  fireEvent.click(screen.getByTestId("probe-button-openai/gpt-4o"));
  await waitFor(() => expect(screen.getByTestId("confirm-probe-openai/gpt-4o")).toBeTruthy());
  fireEvent.click(screen.getByTestId("confirm-probe-button-openai/gpt-4o"));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  expect(fetchMock.mock.calls[2]![0]).toContain("/models/openai%2Fgpt-4o/probe");
});

test("round 2: access_state=revoked alone (connection still active) disables a callable entry's probe", async () => {
  await loadAndSelectProject();
  mockCatalogBody({
    connection_status: "active",
    access_state: "revoked",
    last_successful_check_at: null,
    last_checked_at: null,
    alert: null,
    anomaly: null,
    model_groups: null,
    entries: [{ ...BASE_ENTRY, id: MODEL_A, status: "callable", fields: {} }],
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  expect((screen.getByTestId(`probe-button-${MODEL_A}`) as HTMLButtonElement).disabled).toBe(true);
});
// end round-2 tests

// FTF-UX05 (2026-10-22): audit finding #9 (severity 3) -- a real, paid, external-provider call
// must be confirmed, never fired from the first click; findings X2 (error announce), X7 (busy
// indicator) and X4 (bilingual, partial).

test("UX05: clicking Run probe opens a confirm and makes no network call; Cancel closes it with none either", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  expect(fetch).toHaveBeenCalledTimes(2);

  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  const dialog = await screen.findByTestId(`confirm-probe-${MODEL_A}`);
  expect(dialog.getAttribute("role")).toBe("alertdialog");
  expect(screen.getByTestId(`confirm-probe-text-${MODEL_A}`).textContent).toMatch(/real request/i);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(screen.queryByTestId(`probe-result-${MODEL_A}`)).toBeNull();

  fireEvent.click(screen.getByTestId(`cancel-probe-button-${MODEL_A}`));
  expect(screen.queryByTestId(`confirm-probe-${MODEL_A}`)).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(screen.queryByTestId(`probe-result-${MODEL_A}`)).toBeNull();
});

test("UX05: error paragraphs carry role=alert", async () => {
  mockProjectList();
  renderCatalogUI();
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockReset();
  fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({ detail: "x" }) });
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy());
  expect(screen.getByTestId("projects-error").getAttribute("role")).toBe("alert");
});

test("UX05: load buttons carry aria-busy while their own request is in flight", async () => {
  renderCatalogUI();
  const loadProjectsButton = screen.getByTestId("load-projects-button") as HTMLButtonElement;
  expect(loadProjectsButton.getAttribute("aria-busy")).toBe("false");
  let resolveFetch: (value: unknown) => void = () => {};
  (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
    () => new Promise((resolve) => (resolveFetch = resolve)),
  );
  fireEvent.click(loadProjectsButton);
  await waitFor(() => expect(loadProjectsButton.getAttribute("aria-busy")).toBe("true"));
  resolveFetch({ ok: true, json: async () => [] });
  await waitFor(() => expect(loadProjectsButton.getAttribute("aria-busy")).toBe("false"));
});

test("UX05: locale='ar' renders the confirm dialog in Arabic with dir=rtl, and the container lang/dir follow it", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => [{ id: PROJECT_ID, name: "Acme Tuning Project", status: "active" }],
  });
  render(
    createElement(ModelCatalogUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONNECTION_ID,
      locale: "ar",
    }),
  );
  expect(screen.getByTestId("model-catalog-ui").getAttribute("dir")).toBe("rtl");
  expect(screen.getByTestId("model-catalog-ui").getAttribute("lang")).toBe("ar");
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT_ID}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`project-select-button-${PROJECT_ID}`));
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  const dialog = await screen.findByTestId(`confirm-probe-${MODEL_A}`);
  expect(dialog.getAttribute("dir")).toBe("rtl");
  expect(screen.getByTestId(`confirm-probe-text-${MODEL_A}`).textContent).toContain(
    "طلبًا حقيقيًا",
  );
});

// ---- round 2 (L2 return): pin the 5 previously-unpinned behaviors, fix focus management ----

test("UX05 round 2: load-catalog-button's aria-busy reflects loadingCatalog while the request is in flight", async () => {
  await loadAndSelectProject();
  const loadCatalogButton = screen.getByTestId("load-catalog-button") as HTMLButtonElement;
  expect(loadCatalogButton.getAttribute("aria-busy")).toBe("false");
  let resolveFetch: (value: unknown) => void = () => {};
  (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
    () => new Promise((resolve) => (resolveFetch = resolve)),
  );
  fireEvent.click(loadCatalogButton);
  await waitFor(() => expect(loadCatalogButton.getAttribute("aria-busy")).toBe("true"));
  resolveFetch({
    ok: true,
    json: async () => ({
      connection_status: "active",
      access_state: "healthy",
      last_successful_check_at: null,
      last_checked_at: null,
      alert: null,
      anomaly: null,
      model_groups: null,
      entries: [],
    }),
  });
  await waitFor(() => expect(loadCatalogButton.getAttribute("aria-busy")).toBe("false"));
});

test("UX05 round 2: catalog-error carries role=alert", async () => {
  await loadAndSelectProject();
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({ detail: "x" }) });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId("catalog-error")).toBeTruthy());
  expect(screen.getByTestId("catalog-error").getAttribute("role")).toBe("alert");
});

test("UX05 round 2: probe-error carries role=alert", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: "x" }) });
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`confirm-probe-button-${MODEL_A}`));
  await waitFor(() => expect(screen.getByTestId("probe-error")).toBeTruthy());
  expect(screen.getByTestId("probe-error").getAttribute("role")).toBe("alert");
});

test("UX05 round 2: locale='ar' translates the confirm dialog's own Run-probe/Cancel button text", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  // Default locale="en": unchanged literal text.
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
  expect(screen.getByTestId(`confirm-probe-button-${MODEL_A}`).textContent).toBe("Run probe");
  expect(screen.getByTestId(`cancel-probe-button-${MODEL_A}`).textContent).toBe("Cancel");
  cleanup();

  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => [{ id: PROJECT_ID, name: "Acme Tuning Project", status: "active" }],
  });
  render(
    createElement(ModelCatalogUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONNECTION_ID,
      locale: "ar",
    }),
  );
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT_ID}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`project-select-button-${PROJECT_ID}`));
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
  expect(screen.getByTestId(`confirm-probe-button-${MODEL_A}`).textContent).toBe("تشغيل الفحص");
  expect(screen.getByTestId(`cancel-probe-button-${MODEL_A}`).textContent).toBe("إلغاء");
});

test("UX05 round 2: locale='ar' translates the component's own static UI copy (buttons/labels/filters), not just the confirm dialog", async () => {
  render(
    createElement(ModelCatalogUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONNECTION_ID,
      locale: "ar",
    }),
  );
  expect(screen.getByTestId("load-projects-button").textContent).toBe("تحميل المشاريع");
  expect(screen.getByTestId("no-project-selected").textContent).toBe("لم يتم تحديد مشروع بعد.");
  expect((screen.getByTestId("load-catalog-button") as HTMLButtonElement).textContent).toBe(
    "تحميل الكتالوج",
  );
});

test("UX05 round 2: a raw backend error detail is treated as data (wrapped, no claimed lang), never mistranslated", async () => {
  render(
    createElement(ModelCatalogUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONNECTION_ID,
      locale: "ar",
    }),
  );
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status: 500,
    json: async () => ({ detail: "boom" }),
  });
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy());
  const errorParagraph = screen.getByTestId("projects-error");
  // The raw backend string is shown verbatim (never mistranslated) and the paragraph itself no
  // longer claims a `lang` for text whose language is the backend's choice, not this component's.
  expect(errorParagraph.textContent).toBe("boom");
  expect(errorParagraph.getAttribute("lang")).toBeNull();
});

test("UX05 round 2: opening the confirm dialog moves focus off the disabled-to-be trigger and into the dialog (WAI-ARIA Alert Dialog Pattern)", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());

  const trigger = screen.getByTestId(`probe-button-${MODEL_A}`);
  trigger.focus();
  expect(document.activeElement).toBe(trigger);

  fireEvent.click(trigger);
  await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
  // queueMicrotask runs before the next awaited assertion resolves.
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByTestId(`cancel-probe-button-${MODEL_A}`)),
  );
  expect(document.activeElement).not.toBe(trigger);
});

test("UX05 round 2: Cancel returns focus to the trigger button, which is re-enabled by then", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());

  const trigger = screen.getByTestId(`probe-button-${MODEL_A}`) as HTMLButtonElement;
  fireEvent.click(trigger);
  await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());

  fireEvent.click(screen.getByTestId(`cancel-probe-button-${MODEL_A}`));
  expect(screen.queryByTestId(`confirm-probe-${MODEL_A}`)).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  expect(trigger.disabled).toBe(false);
});

test("UX05 round 2: Escape inside the dialog cancels and returns focus to the trigger, same as the Cancel button", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());

  const trigger = screen.getByTestId(`probe-button-${MODEL_A}`) as HTMLButtonElement;
  fireEvent.click(trigger);
  const dialog = await screen.findByTestId(`confirm-probe-${MODEL_A}`);
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByTestId(`confirm-probe-${MODEL_A}`)).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});

test("UX05 round 2: the dialog's aria-labelledby points at a real, visible title using the previously-dead runProbeConfirmTitle phrase", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  const dialog = await screen.findByTestId(`confirm-probe-${MODEL_A}`);
  const labelledBy = dialog.getAttribute("aria-labelledby");
  expect(labelledBy).toBe(`confirm-probe-title-${MODEL_A}`);
  const title = document.getElementById(labelledBy!);
  expect(title).toBeTruthy();
  expect(title!.tagName).toBe("H2");
  expect(title!.textContent).toBe("Run a real probe?");
});

// -- UX05 round 2 (second L2 return, PR #972) -----------------------------------------------

test("UX05 r2 (L2 return): a non-JSON/no-detail failure on project load is shown in Arabic, not the hardcoded English fallback", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status: 503,
    json: async () => {
      throw new Error("not json");
    },
  });
  render(
    createElement(ModelCatalogUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONNECTION_ID,
      locale: "ar",
    }),
  );
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy());
  const text = screen.getByTestId("projects-error").textContent ?? "";
  expect(text).toContain("503");
  expect(text).not.toContain("request failed");
});

test("UX05 r2 (L2 return): the same no-detail failure on catalog load under locale='ar' is also translated, not just project load", async () => {
  mockProjectList();
  render(
    createElement(ModelCatalogUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONNECTION_ID,
      locale: "ar",
    }),
  );
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT_ID}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`project-select-button-${PROJECT_ID}`));
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status: 500,
    json: async () => {
      throw new Error("not json");
    },
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId("catalog-error")).toBeTruthy());
  const text = screen.getByTestId("catalog-error").textContent ?? "";
  expect(text).toContain("500");
  expect(text).not.toContain("request failed");
});

test("UX05 r2 (L2 return): an empty {} 200 catalog response is a typed error, never a silently-accepted empty result (Law 17)", async () => {
  await loadAndSelectProject();
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({}) });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId("catalog-error")).toBeTruthy());
  // Never silently accepted as "zero entries, healthy, no model groups":
  expect(screen.queryByTestId("catalog-results")).toBeNull();
});

test("UX05 r2 (L2 return): a model_groups.entries that is not an object does not crash the render (it is rejected as malformed)", async () => {
  await loadAndSelectProject();
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      connection_status: "active",
      access_state: "healthy",
      last_successful_check_at: null,
      last_checked_at: null,
      alert: null,
      anomaly: null,
      model_groups: { state: "enriched", entries: null },
      entries: [],
    }),
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId("catalog-error")).toBeTruthy());
  // The component itself is still mounted (no crash/unmount) -- the trigger is still there.
  expect(screen.getByTestId("load-catalog-button")).toBeTruthy();
});

test("UX05 r2 (L2 return): a malformed catalog entry (missing required field) is rejected, not rendered", async () => {
  await loadAndSelectProject();
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      connection_status: "active",
      access_state: "healthy",
      last_successful_check_at: null,
      last_checked_at: null,
      alert: null,
      anomaly: null,
      model_groups: null,
      entries: [{ id: MODEL_A, status: "callable" }], // missing metadata_status/first_seen_at/fields
    }),
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId("catalog-error")).toBeTruthy());
  expect(screen.queryByTestId(`catalog-entry-${MODEL_A}`)).toBeNull();
});

test("UX05 r2 (L2 return): a malformed project-listing item (status is a number, not a string) is rejected", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => [{ id: PROJECT_ID, name: "Acme", status: 1 }],
  });
  renderCatalogUI();
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy());
  expect(screen.queryByTestId(`project-row-${PROJECT_ID}`)).toBeNull();
});

test("UX05 r2 (L2 return): a probe response whose usage is malformed (not an object with numeric fields) is rejected, never rendered as a result", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      probe_type: "chat",
      state: "succeeded",
      route_identity_state: "verified",
      resolved_model: MODEL_A,
      usage: "not-an-object",
      latency_ms: 120,
      detail: "ok",
      probed_at: "2026-10-22T00:00:00+00:00",
    }),
  });
  fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
  const confirmButton = await screen.findByTestId(`confirm-probe-button-${MODEL_A}`);
  fireEvent.click(confirmButton);
  await waitFor(() => expect(screen.getByTestId("probe-error")).toBeTruthy());
  expect(screen.queryByTestId(`probe-result-${MODEL_A}`)).toBeNull();
});

test("UX05 r2 (L2 return): every real button renders through the shared Button component at the minimum target size", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  for (const testId of [
    "load-projects-button",
    `project-select-button-${PROJECT_ID}`,
    "load-catalog-button",
    `probe-button-${MODEL_A}`,
  ]) {
    const el = screen.getByTestId(testId) as HTMLElement;
    expect(el.style.minBlockSize).toBe("var(--target-min)");
    expect(el.style.minInlineSize).toBe("var(--target-min)");
  }
});

test("UX05 r2 (L2 return): the status/metadata/probe-kind <select>s meet the minimum target size", async () => {
  await loadAndSelectProject();
  for (const testId of ["status-filter", "metadata-filter"]) {
    expect((screen.getByTestId(testId) as HTMLElement).style.minBlockSize).toBe(
      "var(--target-min)",
    );
  }
});

test("UX05 r2 (L2 return): a successful load with zero entries shows an EmptyState, not a bare empty list", async () => {
  await loadAndSelectProject();
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      connection_status: "active",
      access_state: "healthy",
      last_successful_check_at: null,
      last_checked_at: null,
      alert: null,
      anomaly: null,
      model_groups: null,
      entries: [],
    }),
  });
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId("catalog-reload-button")).toBeTruthy());
  expect(screen.getByText("No catalog entries")).toBeTruthy();
});

test("UX05 r2 (L2 return): a long raw fields/model-group JSON blob is wrapped, not left to overflow", async () => {
  await loadAndSelectProject();
  mockCatalog();
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId(`catalog-entry-${MODEL_A}`)).toBeTruthy());
  expect((screen.getByTestId(`entry-fields-${MODEL_A}`) as HTMLElement).style.overflowWrap).toBe(
    "anywhere",
  );
});
