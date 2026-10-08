// @vitest-environment jsdom
//
// FTF-0308 -- Model-to-artifact mapping UI.
//
// A real component/DOM-level test (Vitest + React Testing Library + jsdom):
// renders the actual `ModelToArtifactMappingUI`, drives real user
// interaction and asserts on the exact `fetch` calls (method, URL, JSON
// body) made against the frozen FTF-0308-P1 routes. `fetch` is mocked (no
// live server in a unit test); the code path is the real one.
//
// Disclosed: component-level, not a browser E2E -- the repository has no
// Next.js `app/` page tree yet (FTF-H029), same precedent as ftf_0214.spec.ts.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  artifactsUrl,
  mappingChangesUrl,
  mappingUrl,
  ModelToArtifactMappingUI,
} from "../components/artifact-capabilities/model_to_artifact_mapping_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const CONN_ID = "22222222-2222-2222-2222-222222222222";
const ENTRY_ID = "44444444-4444-4444-4444-444444444444";
const ART_READY = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const ART_OTHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const ART_REGISTERED = "cccccccc-cccc-cccc-cccc-cccccccccccc";
// An alias that looks like an artifact name: the UI must never match on it.
const ALIAS = "llama-3-8b-instruct";
const MAP_URL = `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONN_ID}/models/${ALIAS}/training-artifact`;

function artifact(id: string, name: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    organization_id: ORG_ID,
    display_name: name,
    source_type: "huggingface",
    source_uri: `meta-llama/${name}`,
    model_family: "llama",
    base_model_revision: "0123456789abcdef0123456789abcdef01234567",
    weights_digest: `sha256:${"a".repeat(64)}`,
    license_id: "llama3",
    license_accepted_by: "admin@example.com",
    license_accepted_at: "2026-10-01T00:00:00+00:00",
    trust_remote_code_allowed: false,
    required_python_packages: ["torch==2.4.0"],
    minimum_gpu_profile: { gpu: "A100", memory_gb: 40 },
    status,
    created_at: "2026-10-01T00:00:00+00:00",
    updated_at: "2026-10-01T00:00:00+00:00",
    archived_at: null,
    ...extra,
  };
}

function mappingBody(
  version: number,
  artifactId: string | null,
  extra: Record<string, unknown> = {},
) {
  return {
    mapping_state: artifactId ? "mapped" : "unmapped",
    model_catalog_entry_id: artifactId ? ENTRY_ID : null,
    litellm_connection_id: CONN_ID,
    public_model_name: ALIAS,
    trainable_artifact_id: artifactId,
    catalog_version: version,
    last_verified_accessible_at: artifactId ? "2026-10-07T00:00:00+00:00" : null,
    last_changed_at: artifactId ? "2026-10-07T00:00:00+00:00" : null,
    artifact: artifactId ? artifact(artifactId, "Llama-3-8B", "ready") : null,
    ...extra,
  };
}

const ARTIFACT_LIST = [
  artifact(ART_READY, "Llama-3-8B", "ready"),
  artifact(ART_OTHER, "Llama-3-70B", "ready"),
  artifact(ART_REGISTERED, "Llama-3-1B", "registered"),
];

type Reply = { ok: boolean; status?: number; body: unknown };
let replies: Record<string, Reply[]>;

function enqueue(method: string, url: string, ...queued: Reply[]) {
  const key = `${method} ${url}`;
  replies[key] = [...(replies[key] ?? []), ...queued];
}

function ok(body: unknown): Reply {
  return { ok: true, status: 200, body };
}

function fail(status: number, detail: string): Reply {
  return { ok: false, status, body: { detail } };
}

function calls(method: string, url: string) {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.filter(
    ([calledUrl, init]) => calledUrl === url && (init?.method ?? "GET") === method,
  );
}

function postedBody(url: string): unknown {
  const call = calls("POST", url)[0];
  if (!call) throw new Error("no POST was made");
  return JSON.parse(call[1].body);
}

beforeEach(() => {
  replies = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string }) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const next = replies[key]?.shift();
      if (!next) throw new Error(`unexpected request ${key}`);
      return { ok: next.ok, status: next.status ?? 200, json: async () => next.body };
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderUI(modelName = ALIAS) {
  return render(
    createElement(ModelToArtifactMappingUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONN_ID,
      modelName,
    }),
  );
}

async function loadMapping(body: unknown) {
  enqueue("GET", MAP_URL, ok(body));
  fireEvent.click(screen.getByTestId("load-mapping-button"));
  await waitFor(() => expect(screen.getByTestId("mapping-state")).toBeTruthy());
}

async function loadArtifacts() {
  enqueue("GET", artifactsUrl(API_BASE, ORG_ID), ok(ARTIFACT_LIST));
  fireEvent.click(screen.getByTestId("load-artifacts-button"));
  await waitFor(() => expect(screen.getByTestId(`artifact-row-${ART_READY}`)).toBeTruthy());
}

test("URL helpers point at the frozen routes and keep '/' in a path-style model name", () => {
  expect(mappingUrl(API_BASE, ORG_ID, CONN_ID, ALIAS)).toBe(MAP_URL);
  expect(mappingChangesUrl(API_BASE, ORG_ID, CONN_ID, ALIAS)).toBe(`${MAP_URL}/changes`);
  expect(artifactsUrl(API_BASE, ORG_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/artifacts`,
  );
  expect(mappingUrl(`${API_BASE}/`, ORG_ID, CONN_ID, "openai/gpt 4")).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONN_ID}` +
      `/models/openai/gpt%204/training-artifact`,
  );
});

test("nothing is requested on render, and an unmapped alias is shown as unmapped", async () => {
  renderUI();
  expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  await loadMapping(mappingBody(0, null));
  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Not mapped");
  expect(screen.getByTestId("mapping-catalog-version").textContent).toContain("0");
  expect(screen.queryByTestId("mapped-artifact-details")).toBeNull();
  expect((screen.getByTestId("unmap-button") as HTMLButtonElement).disabled).toBe(true);
});

test("acceptance: a mapped alias displays the artifact's provenance and limits from the server", async () => {
  renderUI();
  await loadMapping(mappingBody(3, ART_READY));
  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Mapped");
  expect(screen.getByTestId("mapped-artifact-source").textContent).toBe(
    "huggingface: meta-llama/Llama-3-8B",
  );
  expect(screen.getByTestId("mapped-artifact-revision").textContent).toBe(
    "0123456789abcdef0123456789abcdef01234567",
  );
  expect(screen.getByTestId("mapped-artifact-digest").textContent).toBe(`sha256:${"a".repeat(64)}`);
  expect(screen.getByTestId("mapped-artifact-license").textContent).toContain(
    "accepted by admin@example.com",
  );
  expect(screen.getByTestId("mapped-artifact-trust-remote-code").textContent).toBe("not allowed");
  expect(screen.getByTestId("mapped-artifact-packages").textContent).toBe("torch==2.4.0");
  expect(screen.getByTestId("mapped-artifact-gpu").textContent).toBe(
    '{"gpu":"A100","memory_gb":40}',
  );
});

test("missing provenance fields render as 'not recorded', never a guessed value", async () => {
  renderUI();
  await loadMapping(
    mappingBody(1, ART_READY, {
      artifact: artifact(ART_READY, "Llama-3-8B", "ready", {
        weights_digest: null,
        license_accepted_by: null,
        license_accepted_at: null,
        required_python_packages: [],
        minimum_gpu_profile: {},
      }),
    }),
  );
  expect(screen.getByTestId("mapped-artifact-digest").textContent).toBe("not recorded");
  expect(screen.getByTestId("mapped-artifact-license").textContent).toContain(
    "accepted by not recorded",
  );
  expect(screen.getByTestId("mapped-artifact-packages").textContent).toBe("none recorded");
  expect(screen.getByTestId("mapped-artifact-gpu").textContent).toBe("not recorded");
});

test("negative_acceptance: no artifact is guessed from the alias -- none selected, Map disabled, nothing sent", async () => {
  renderUI(); // alias "llama-3-8b-instruct" resembles the "Llama-3-8B" artifact
  await loadMapping(mappingBody(0, null));
  await loadArtifacts();
  expect(screen.getByTestId("no-artifact-selected")).toBeTruthy();
  expect(screen.queryByTestId("selected-artifact-details")).toBeNull();
  expect((screen.getByTestId("map-button") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByTestId("map-button"));
  expect(calls("POST", MAP_URL).length).toBe(0);
  // Listing order is the server's: the UI does not reorder by alias similarity.
  const rows = screen.getByTestId("artifact-list").querySelectorAll("li");
  expect(Array.from(rows).map((row) => row.getAttribute("data-testid"))).toEqual([
    `artifact-row-${ART_READY}`,
    `artifact-row-${ART_OTHER}`,
    `artifact-row-${ART_REGISTERED}`,
  ]);
});

test("only ready artifacts are selectable; a registered one is disabled with the reason shown", async () => {
  renderUI();
  await loadMapping(mappingBody(0, null));
  await loadArtifacts();
  const button = screen.getByTestId(
    `artifact-select-button-${ART_REGISTERED}`,
  ) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  expect(screen.getByTestId(`artifact-not-ready-${ART_REGISTERED}`)).toBeTruthy();
  fireEvent.click(button);
  expect(screen.getByTestId("no-artifact-selected")).toBeTruthy();
  expect((screen.getByTestId("map-button") as HTMLButtonElement).disabled).toBe(true);
});

test("explicit selection then Map sends the chosen id and the loaded catalog version, once", async () => {
  renderUI();
  await loadMapping(mappingBody(0, null));
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_READY}`));
  expect(screen.getByTestId("selected-artifact-source").textContent).toContain(
    "meta-llama/Llama-3-8B",
  );

  enqueue("POST", MAP_URL, ok({ ...mappingBody(1, ART_READY), changed: true }));
  fireEvent.click(screen.getByTestId("map-button"));
  await waitFor(() => expect(screen.getByTestId("mapping-notice")).toBeTruthy());

  expect(calls("POST", MAP_URL).length).toBe(1);
  expect(calls("POST", MAP_URL)[0]?.[1].credentials).toBe("include");
  expect(postedBody(MAP_URL)).toEqual({
    trainable_artifact_id: ART_READY,
    expected_catalog_version: 0,
  });
  expect(screen.getByTestId("mapping-catalog-version").textContent).toContain("1");
  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Mapped");
  expect(screen.getByTestId("mapping-notice").textContent).toContain("0 -> 1");
  // A finished write clears the selection: the next change is a new explicit choice.
  expect(screen.getByTestId("no-artifact-selected")).toBeTruthy();
});

test("negative_acceptance: changing an existing mapping needs a confirmation that names the invalidation", async () => {
  renderUI();
  await loadMapping(mappingBody(2, ART_READY));
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_OTHER}`));
  fireEvent.click(screen.getByTestId("map-button"));

  // First click only asks; nothing is sent.
  expect(calls("POST", MAP_URL).length).toBe(0);
  const text = screen.getByTestId("confirm-change-text").textContent ?? "";
  expect(text).toContain("invalidates related qualification");
  expect(text).toContain("version 2");

  enqueue("POST", MAP_URL, ok({ ...mappingBody(3, ART_OTHER), changed: true }));
  fireEvent.click(screen.getByTestId("confirm-change-button"));
  await waitFor(() => expect(screen.getByTestId("mapping-notice")).toBeTruthy());
  expect(postedBody(MAP_URL)).toEqual({
    trainable_artifact_id: ART_OTHER,
    expected_catalog_version: 2,
  });
  const notice = screen.getByTestId("mapping-notice").textContent ?? "";
  expect(notice).toContain("2 -> 3");
  expect(notice).toContain("no longer applies");
  expect(screen.queryByTestId("confirm-change")).toBeNull();
});

test("cancelling the confirmation sends nothing and leaves the mapping untouched", async () => {
  renderUI();
  await loadMapping(mappingBody(2, ART_READY));
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_OTHER}`));
  fireEvent.click(screen.getByTestId("map-button"));
  fireEvent.click(screen.getByTestId("cancel-change-button"));
  expect(screen.queryByTestId("confirm-change")).toBeNull();
  expect(calls("POST", MAP_URL).length).toBe(0);
  expect(screen.getByTestId("mapping-catalog-version").textContent).toContain("2");
});

test("unmapping asks first, then posts a null artifact id with the loaded version", async () => {
  renderUI();
  await loadMapping(mappingBody(4, ART_READY));
  fireEvent.click(screen.getByTestId("unmap-button"));
  expect(calls("POST", MAP_URL).length).toBe(0);
  expect(screen.getByTestId("confirm-change-text").textContent).toContain("Removing this mapping");

  enqueue("POST", MAP_URL, ok({ ...mappingBody(5, null), changed: true }));
  fireEvent.click(screen.getByTestId("confirm-change-button"));
  await waitFor(() => expect(screen.getByTestId("mapping-notice")).toBeTruthy());
  expect(postedBody(MAP_URL)).toEqual({
    trainable_artifact_id: null,
    expected_catalog_version: 4,
  });
  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Not mapped");
});

test("selecting the artifact the alias already has is not offered as a change", async () => {
  renderUI();
  await loadMapping(mappingBody(2, ART_READY));
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_READY}`));
  expect((screen.getByTestId("map-button") as HTMLButtonElement).disabled).toBe(true);
});

test("a changed:false reply is reported as no change, not as a version move", async () => {
  renderUI();
  await loadMapping(mappingBody(2, ART_READY));
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_OTHER}`));
  fireEvent.click(screen.getByTestId("map-button"));
  enqueue("POST", MAP_URL, ok({ ...mappingBody(2, ART_OTHER), changed: false }));
  fireEvent.click(screen.getByTestId("confirm-change-button"));
  await waitFor(() => expect(screen.getByTestId("mapping-notice")).toBeTruthy());
  expect(screen.getByTestId("mapping-notice").textContent).toContain("No change");
  expect(screen.getByTestId("mapping-notice").textContent).not.toContain("no longer applies");
});

test.each([
  [409, "catalog_version_conflict", "changed since it was loaded"],
  [409, "model_not_accessible", "does not currently list"],
  [409, "artifact_not_ready", "not ready"],
  [409, "connection_revoked", "revoked"],
  [503, "gateway_unavailable", "could not be reached"],
  [404, "not_found", "not found"],
])(
  "a %i %s reply is an error, and the displayed mapping is unchanged (Law 17)",
  async (status, code, text) => {
    renderUI();
    await loadMapping(mappingBody(2, ART_READY));
    await loadArtifacts();
    fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_OTHER}`));
    fireEvent.click(screen.getByTestId("map-button"));
    enqueue("POST", MAP_URL, fail(status, code));
    fireEvent.click(screen.getByTestId("confirm-change-button"));
    await waitFor(() => expect(screen.getByTestId("write-error")).toBeTruthy());
    expect(screen.getByTestId("write-error").textContent).toContain(text);
    expect(screen.queryByTestId("mapping-notice")).toBeNull();
    expect(screen.getByTestId("mapping-catalog-version").textContent).toContain("2");
    expect(screen.getByTestId("mapped-artifact-name").textContent).toBe("Llama-3-8B");
  },
);

test("a 403 from the route is surfaced as-is: the UI never authorizes the effect itself", async () => {
  renderUI();
  await loadMapping(mappingBody(0, null));
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_READY}`));
  enqueue("POST", MAP_URL, fail(403, "role_not_authorized"));
  fireEvent.click(screen.getByTestId("map-button"));
  await waitFor(() => expect(screen.getByTestId("write-error")).toBeTruthy());
  expect(screen.getByTestId("write-error").textContent).toBe("role_not_authorized");
  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Not mapped");
});

test("a network failure on write never records a mapping", async () => {
  renderUI();
  await loadMapping(mappingBody(0, null));
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_READY}`));
  // No queued reply: the mock fetch throws.
  fireEvent.click(screen.getByTestId("map-button"));
  await waitFor(() => expect(screen.getByTestId("write-error")).toBeTruthy());
  expect(screen.getByTestId("write-error").textContent).toContain("network error");
  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Not mapped");
});

test("malformed responses are errors, not state", async () => {
  renderUI();
  enqueue("GET", MAP_URL, ok({ mapping_state: "mapped" }));
  fireEvent.click(screen.getByTestId("load-mapping-button"));
  await waitFor(() => expect(screen.getByTestId("mapping-error")).toBeTruthy());
  expect(screen.getByTestId("mapping-error").textContent).toBe("malformed mapping response");
  expect(screen.queryByTestId("mapping-state")).toBeNull();

  enqueue("GET", artifactsUrl(API_BASE, ORG_ID), ok({ not: "a list" }));
  fireEvent.click(screen.getByTestId("load-artifacts-button"));
  await waitFor(() => expect(screen.getByTestId("artifacts-error")).toBeTruthy());
});

test("Map cannot be used before the current mapping is loaded", async () => {
  renderUI();
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_READY}`));
  expect((screen.getByTestId("map-button") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByTestId("map-button"));
  expect(calls("POST", MAP_URL).length).toBe(0);
});

test("change history lists the append-only rows, and an empty history says so", async () => {
  renderUI();
  const url = `${MAP_URL}/changes`;
  enqueue("GET", url, ok({ changes: [] }));
  fireEvent.click(screen.getByTestId("load-changes-button"));
  await waitFor(() => expect(screen.getByTestId("changes-empty")).toBeTruthy());

  enqueue(
    "GET",
    url,
    ok({
      changes: [
        {
          id: "55555555-5555-5555-5555-555555555555",
          model_catalog_entry_id: ENTRY_ID,
          previous_trainable_artifact_id: ART_READY,
          new_trainable_artifact_id: ART_OTHER,
          previous_catalog_version: 1,
          new_catalog_version: 2,
          actor: "user-1",
          request_id: "req-1",
          created_at: "2026-10-07T00:00:00+00:00",
        },
      ],
    }),
  );
  fireEvent.click(screen.getByTestId("load-changes-button"));
  await waitFor(() => expect(screen.getByTestId("changes-list")).toBeTruthy());
  const row =
    screen.getByTestId("change-row-55555555-5555-5555-5555-555555555555").textContent ?? "";
  expect(row).toContain("v1 -> v2");
  expect(row).toContain(ART_OTHER);
  expect(row).toContain("user-1");
});

// ---- round 2 (Control-plane L2 return, tests only) ----

test("negative_acceptance: an artifact whose display_name equals the alias exactly is never preselected or auto-mapped", async () => {
  renderUI(); // alias ALIAS
  enqueue("GET", MAP_URL, ok(mappingBody(0, null)));
  fireEvent.click(screen.getByTestId("load-mapping-button"));
  await waitFor(() => expect(screen.getByTestId("mapping-state")).toBeTruthy());
  const exact = "dddddddd-dddd-dddd-dddd-dddddddddddd";
  enqueue(
    "GET",
    artifactsUrl(API_BASE, ORG_ID),
    ok([artifact(exact, ALIAS, "ready"), ...ARTIFACT_LIST]),
  );
  fireEvent.click(screen.getByTestId("load-artifacts-button"));
  await waitFor(() => expect(screen.getByTestId(`artifact-row-${exact}`)).toBeTruthy());

  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Not mapped");
  expect(screen.getByTestId("no-artifact-selected")).toBeTruthy();
  expect(screen.queryByTestId("selected-artifact-details")).toBeNull();
  expect((screen.getByTestId("map-button") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByTestId("map-button"));
  expect(calls("POST", MAP_URL).length).toBe(0);
  // Still unmapped after the click, and no POST was ever made (no auto-map).
  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Not mapped");
  expect(screen.queryByTestId("mapping-notice")).toBeNull();
});

test.each([
  ["empty object", {}],
  ["mapping without changed flag", mappingBody(1, ART_READY)],
  ["non-boolean changed", { ...mappingBody(1, ART_READY), changed: "false" }],
  ["array body", []],
])("a malformed 200 reply to the write (%s) is an error, never 'No change' or mapped", async (_n, body) => {
  renderUI();
  await loadMapping(mappingBody(0, null));
  await loadArtifacts();
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_READY}`));
  enqueue("POST", MAP_URL, ok(body));
  fireEvent.click(screen.getByTestId("map-button"));
  await waitFor(() => expect(screen.getByTestId("write-error")).toBeTruthy());
  expect(screen.getByTestId("write-error").textContent).toBe("malformed mapping response");
  expect(screen.queryByTestId("mapping-notice")).toBeNull();
  expect(screen.getByTestId("mapping-state-label").textContent).toContain("Not mapped");
  expect(screen.getByTestId("mapping-catalog-version").textContent).toContain("0");
});

test("the GET mapping request sends credentials", async () => {
  renderUI();
  await loadMapping(mappingBody(0, null));
  expect(calls("GET", MAP_URL)[0]?.[1].credentials).toBe("include");
});
