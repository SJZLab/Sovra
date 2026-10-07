// @vitest-environment jsdom
//
// FTF-0308 -- Model-to-artifact mapping UI.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `ModelToArtifactMappingUI`, drives real user
// interaction (loading the real catalog/artifact lists, explicitly
// selecting a model alias and an artifact) and asserts on the real
// `fetch` calls it makes against the live FTF-0214/FTF-0301/FTF-0308 API
// contract -- request method and URL -- never a snapshot. `fetch` itself
// is mocked (no live server in a unit test), but the component code path
// exercised is the real one that would hit the actual backend
// (`app.litellm.model_catalog_routes`).
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E -- same precedent `ftf_0214.spec.ts` already established
// (this repository has no Next.js `app/` page tree yet; FTF-H029 is the
// task that creates the first one).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  artifactsUrl,
  catalogUrl,
  ModelToArtifactMappingUI,
  trainingCapabilityUrl,
} from "../components/artifact-capabilities/model_to_artifact_mapping_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const CONNECTION_ID = "22222222-2222-2222-2222-222222222222";
const READY_ARTIFACT_ID = "33333333-3333-3333-3333-333333333333";
const REGISTERED_ARTIFACT_ID = "44444444-4444-4444-4444-444444444444";
const MODEL_A = "model-a";
const MODEL_B = "model-b";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderUI() {
  return render(
    createElement(ModelToArtifactMappingUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONNECTION_ID,
    }),
  );
}

function fetchMock() {
  return fetch as unknown as ReturnType<typeof vi.fn>;
}

function mockCatalog() {
  fetchMock().mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      connection_status: "active",
      access_state: "healthy",
      entries: [
        { id: MODEL_A, status: "callable" },
        { id: MODEL_B, status: "callable" },
      ],
    }),
  });
}

function mockArtifacts() {
  fetchMock().mockResolvedValueOnce({
    ok: true,
    json: async () => [
      { id: READY_ARTIFACT_ID, display_name: "llama-8b-ready", status: "ready" },
      { id: REGISTERED_ARTIFACT_ID, display_name: "llama-8b-draft", status: "registered" },
    ],
  });
}

function mockCapability(body: Record<string, unknown>) {
  fetchMock().mockResolvedValueOnce({ ok: true, json: async () => body });
}

function readyCapabilityBody(modelId: string, artifactId: string) {
  return {
    model_id: modelId,
    access: "granted",
    capability: "trainable_open_weight",
    artifact_id: artifactId,
    artifact_availability: "ready",
    license_approved: true,
    role: null,
    evidence_probed_at: null,
    detail: "",
    artifact: {
      id: artifactId,
      display_name: "llama-8b-ready",
      source_type: "huggingface",
      source_uri: "meta-llama/Meta-Llama-3-8B",
      model_family: "llama-3",
      base_model_revision: "a".repeat(40),
      weights_digest: "sha256:" + "b".repeat(64),
      license_id: "llama3",
      status: "ready",
      trust_remote_code_allowed: false,
      required_python_packages: ["transformers>=4.40"],
      minimum_gpu_profile: { min_gpu_vram_gb: 24 },
    },
  };
}

async function loadModelsAndArtifacts() {
  mockCatalog();
  mockArtifacts();
  renderUI();
  fireEvent.click(screen.getByTestId("load-models-button"));
  await waitFor(() => expect(screen.getByTestId(`model-row-${MODEL_A}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId("load-artifacts-button"));
  await waitFor(() => expect(screen.getByTestId(`artifact-row-${READY_ARTIFACT_ID}`)).toBeTruthy());
}

test("URL helpers point at the real FTF-0214/FTF-0301/FTF-0308 API contract", () => {
  expect(catalogUrl(API_BASE, ORG_ID, CONNECTION_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONNECTION_ID}/catalog`,
  );
  expect(artifactsUrl(API_BASE, ORG_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/artifacts`,
  );
  expect(trainingCapabilityUrl(API_BASE, ORG_ID, CONNECTION_ID, MODEL_A, null)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONNECTION_ID}` +
      `/models/${MODEL_A}/training-capability`,
  );
  expect(trainingCapabilityUrl(API_BASE, ORG_ID, CONNECTION_ID, MODEL_A, READY_ARTIFACT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONNECTION_ID}` +
      `/models/${MODEL_A}/training-capability?artifact_id=${READY_ARTIFACT_ID}`,
  );
  expect(trainingCapabilityUrl(API_BASE, ORG_ID, CONNECTION_ID, "openai/gpt-4o", null)).toContain(
    "/models/openai%2Fgpt-4o/training-capability",
  );
});

test("negative_acceptance: no model and no artifact are pre-selected", async () => {
  mockCatalog();
  mockArtifacts();
  renderUI();
  expect(screen.getByTestId("no-model-selected")).toBeTruthy();
  expect(screen.getByTestId("no-artifact-selected")).toBeTruthy();
  expect(screen.queryByTestId("capability-results")).toBeNull();

  fireEvent.click(screen.getByTestId("load-models-button"));
  await waitFor(() => expect(screen.getByTestId(`model-row-${MODEL_A}`)).toBeTruthy());
  // Listing models alone never implies a selection, and never fires a
  // capability request on its own.
  expect(screen.getByTestId("no-model-selected")).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("acceptance: selecting a real alias and a real artifact fetches the real training-capability route", async () => {
  await loadModelsAndArtifacts();

  fireEvent.click(screen.getByTestId(`model-select-button-${MODEL_A}`));
  expect(screen.queryByTestId("no-model-selected")).toBeNull();
  // No artifact selected yet -- no capability request fired.
  expect(fetch).toHaveBeenCalledTimes(2);

  mockCapability(readyCapabilityBody(MODEL_A, READY_ARTIFACT_ID));
  fireEvent.click(screen.getByTestId(`artifact-select-button-${READY_ARTIFACT_ID}`));

  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  const [url, init] = fetchMock().mock.calls[2]!;
  expect(url).toBe(
    trainingCapabilityUrl(API_BASE, ORG_ID, CONNECTION_ID, MODEL_A, READY_ARTIFACT_ID),
  );
  expect(init.method).toBe("GET");

  await waitFor(() =>
    expect(screen.getByTestId("capability-value").textContent).toContain("trainable_open_weight"),
  );
  expect(screen.getByTestId("capability-access").textContent).toContain("granted");
  expect(screen.getByTestId("capability-license-approved").textContent).toContain("yes");
  expect(screen.getByTestId("provenance-display-name").textContent).toBe("llama-8b-ready");
  expect(screen.getByTestId("provenance-digest").textContent).toContain("sha256:");
  expect(screen.getByTestId("provenance-limits").textContent).toContain("24");
});

test("negative_acceptance: changing the mapped artifact clears the stale result before the new one loads", async () => {
  await loadModelsAndArtifacts();
  fireEvent.click(screen.getByTestId(`model-select-button-${MODEL_A}`));
  mockCapability(readyCapabilityBody(MODEL_A, READY_ARTIFACT_ID));
  fireEvent.click(screen.getByTestId(`artifact-select-button-${READY_ARTIFACT_ID}`));
  await waitFor(() =>
    expect(screen.getByTestId("capability-value").textContent).toContain("trainable_open_weight"),
  );

  // Re-map to the still-registered artifact -- a fetch that resolves slowly.
  let resolveSecond: (value: unknown) => void = () => {};
  fetchMock().mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveSecond = resolve;
      }),
  );
  fireEvent.click(screen.getByTestId(`artifact-select-button-${REGISTERED_ARTIFACT_ID}`));

  // The stale trainable_open_weight result must be gone immediately, before
  // the new request has even resolved -- never shown stale for the new mapping.
  expect(screen.queryByTestId("capability-results")).toBeNull();
  expect(screen.getByTestId("capability-loading")).toBeTruthy();
  // The in-flight request itself must carry the NEW artifact id, never a
  // stale-closure reuse of the previously-selected one.
  const [staleCheckUrl] = fetchMock().mock.calls[fetchMock().mock.calls.length - 1]!;
  expect(staleCheckUrl).toBe(
    trainingCapabilityUrl(API_BASE, ORG_ID, CONNECTION_ID, MODEL_A, REGISTERED_ARTIFACT_ID),
  );

  resolveSecond({
    ok: true,
    json: async () => ({
      model_id: MODEL_A,
      access: "granted",
      capability: "unknown",
      artifact_id: REGISTERED_ARTIFACT_ID,
      artifact_availability: "registered",
      license_approved: false,
      role: null,
      evidence_probed_at: null,
      detail: "",
      artifact: {
        id: REGISTERED_ARTIFACT_ID,
        display_name: "llama-8b-draft",
        source_type: "huggingface",
        source_uri: "meta-llama/Meta-Llama-3-8B",
        model_family: "llama-3",
        base_model_revision: "main",
        weights_digest: null,
        license_id: "llama3",
        status: "registered",
        trust_remote_code_allowed: false,
        required_python_packages: [],
        minimum_gpu_profile: {},
      },
    }),
  });

  await waitFor(() =>
    expect(screen.getByTestId("capability-value").textContent).toContain("unknown"),
  );
  expect(screen.getByTestId("provenance-display-name").textContent).toBe("llama-8b-draft");
  expect(screen.getByTestId("provenance-digest").textContent).toBe("unresolved");
});

test("negative_acceptance: changing the selected model alias also invalidates the prior mapping's result", async () => {
  await loadModelsAndArtifacts();
  fireEvent.click(screen.getByTestId(`model-select-button-${MODEL_A}`));
  mockCapability(readyCapabilityBody(MODEL_A, READY_ARTIFACT_ID));
  fireEvent.click(screen.getByTestId(`artifact-select-button-${READY_ARTIFACT_ID}`));
  await waitFor(() =>
    expect(screen.getByTestId("capability-value").textContent).toContain("trainable_open_weight"),
  );

  mockCapability(readyCapabilityBody(MODEL_B, READY_ARTIFACT_ID));
  fireEvent.click(screen.getByTestId(`model-select-button-${MODEL_B}`));

  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
  const [url] = fetchMock().mock.calls[3]!;
  expect(url).toBe(
    trainingCapabilityUrl(API_BASE, ORG_ID, CONNECTION_ID, MODEL_B, READY_ARTIFACT_ID),
  );
  await waitFor(() =>
    expect(screen.getByTestId("capability-model-id").textContent).toContain(MODEL_B),
  );
});

test("negative_acceptance: a real 404 (foreign/nonexistent artifact_id) is surfaced, never retried as success", async () => {
  await loadModelsAndArtifacts();
  fireEvent.click(screen.getByTestId(`model-select-button-${MODEL_A}`));
  fetchMock().mockResolvedValueOnce({
    ok: false,
    status: 404,
    json: async () => ({ detail: "artifact_not_found" }),
  });
  fireEvent.click(screen.getByTestId(`artifact-select-button-${READY_ARTIFACT_ID}`));

  await waitFor(() =>
    expect(screen.getByTestId("capability-error").textContent).toContain("artifact_not_found"),
  );
  expect(screen.queryByTestId("capability-results")).toBeNull();
});

test("network error while fetching capability is surfaced, never an empty or fabricated success", async () => {
  await loadModelsAndArtifacts();
  fireEvent.click(screen.getByTestId(`model-select-button-${MODEL_A}`));
  fetchMock().mockRejectedValueOnce(new Error("connection refused"));
  fireEvent.click(screen.getByTestId(`artifact-select-button-${READY_ARTIFACT_ID}`));

  await waitFor(() =>
    expect(screen.getByTestId("capability-error").textContent).toContain("connection refused"),
  );
  expect(screen.queryByTestId("capability-results")).toBeNull();
});
