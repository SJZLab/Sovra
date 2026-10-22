// @vitest-environment jsdom
//
// FTF-04A21 -- Manifest review/edit UI with per-field evidence.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `ManifestReviewEditUI`, drives real user
// interaction (editing fields, saving, resolving conflicts, locking) and
// asserts on the real `fetch` calls it makes against the live FTF-04A19/
// FTF-04A20 API contract -- request method, URL and JSON body. `fetch`
// itself is a URL-routed fake (no live server in a unit test) and the
// storage is a real in-memory Storage implementation, but the component
// code path exercised is the real one.
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E -- same precedent as `ftf_0214.spec.ts` (no Next.js `app/`
// page tree yet; FTF-H029 creates the first one).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  draftStorageKey,
  evidenceForField,
  evidenceUrl,
  lineDiff,
  ManifestReviewEditUI,
  manifestUrl,
  type EvidenceRecord,
  type ManifestRecord,
} from "../components/target-application/manifest_review_edit_ui_with_per_field_evidence";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const MANIFEST_ID = "33333333-3333-3333-3333-333333333333";
const MANIFEST_PATH = `/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}/manifests/${MANIFEST_ID}`;

const ALL_JSON_FIELDS = [
  "agent_entrypoint_json",
  "current_model_binding_json",
  "system_prompt_refs_json",
  "tool_contracts_json",
  "input_contract_json",
  "output_contract_json",
  "mandate_json",
  "forbidden_actions_json",
  "permission_model_json",
  "required_context_json",
  "runtime_dependencies_json",
  "evaluation_harness_json",
  "trace_source_contract_json",
  "deployment_contract_json",
];

class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
}

const STORAGE_KEY = draftStorageKey(ORG_ID, PROJECT_ID, MANIFEST_ID);

function manifestRecord(overrides: Record<string, unknown> = {}): ManifestRecord {
  const base: Record<string, unknown> = {
    id: MANIFEST_ID,
    organization_id: ORG_ID,
    project_id: PROJECT_ID,
    target_source_snapshot_id: "44444444-4444-4444-4444-444444444444",
    version: 1,
    status: "review_required",
    agent_name: "support-agent",
    manifest_sha256: null,
    created_by: "user-1",
    approved_by: null,
    approved_at: null,
    created_at: "2026-10-07T00:00:00+00:00",
  };
  for (const field of ALL_JSON_FIELDS) base[field] = null;
  base.agent_entrypoint_json = { path: "agent/main.py", symbol: "run" };
  base.current_model_binding_json = {
    resolved: true,
    model_alias: "gpt-prod",
    role: "current_production",
  };
  base.runtime_dependencies_json = { python: ">=3.11" };
  return { ...base, ...overrides } as ManifestRecord;
}

function evidenceRow(overrides: Partial<EvidenceRecord> = {}): EvidenceRecord {
  return {
    id: "ev-1",
    organization_id: ORG_ID,
    agent_manifest_id: MANIFEST_ID,
    field_path: "agent_entrypoint_json",
    evidence_type: "repo_symbol",
    repository_commit_sha: "abcdef0123456789abcdef0123456789abcdef01",
    file_path: "agent/main.py",
    line_start: 10,
    line_end: 24,
    symbol: "run",
    content_hash: "sha256:feed",
    confidence_score: 0.9,
    ...overrides,
  };
}

interface Server {
  manifest: ManifestRecord;
  evidence: EvidenceRecord[];
  evidenceStatus: number;
  calls: { method: string; path: string; body: unknown }[];
  patchStatus: number;
  lockStatus: number;
}

let server: Server;

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function installServer(partial: Partial<Server> = {}) {
  server = {
    manifest: manifestRecord(),
    evidence: [evidenceRow()],
    evidenceStatus: 200,
    calls: [],
    patchStatus: 200,
    lockStatus: 200,
    ...partial,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(url);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      server.calls.push({ method, path: parsed.pathname, body });
      if (parsed.pathname === `${MANIFEST_PATH}/evidence` && method === "GET") {
        return server.evidenceStatus === 200
          ? jsonResponse(200, server.evidence)
          : jsonResponse(server.evidenceStatus, { detail: "evidence unavailable" });
      }
      if (parsed.pathname === MANIFEST_PATH && method === "GET") {
        return jsonResponse(200, server.manifest);
      }
      if (parsed.pathname === MANIFEST_PATH && method === "PATCH") {
        if (server.patchStatus !== 200) {
          return jsonResponse(server.patchStatus, {
            detail:
              "agent manifest 33333333-3333-3333-3333-333333333333 is 'locked' -- content can only be edited while draft or review_required (this task's own negative_acceptance)",
          });
        }
        server.manifest = { ...server.manifest, ...(body as Record<string, unknown>) };
        return jsonResponse(200, server.manifest);
      }
      if (parsed.pathname === `${MANIFEST_PATH}/submit-for-review` && method === "POST") {
        server.manifest = { ...server.manifest, status: "review_required" };
        return jsonResponse(200, server.manifest);
      }
      if (parsed.pathname === `${MANIFEST_PATH}/lock` && method === "POST") {
        if (server.lockStatus !== 200) {
          return jsonResponse(server.lockStatus, {
            detail:
              "agent manifest 33333333-3333-3333-3333-333333333333 is missing required field(s) ['agent_entrypoint_json'] -- this task's own negative_acceptance: missing critical evidence/model/runtime tuple blocks activation",
          });
        }
        server.manifest = { ...server.manifest, status: "locked", manifest_sha256: "a".repeat(64) };
        return jsonResponse(200, server.manifest);
      }
      return jsonResponse(404, { detail: "not_found" });
    }),
  );
}

function callsOf(method: string) {
  return server.calls.filter((call) => call.method === method);
}

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  installServer();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderUI(storageOverride: unknown = storage) {
  return render(
    createElement(ManifestReviewEditUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      manifestId: MANIFEST_ID,
      storage: storageOverride as Storage | null,
    }),
  );
}

async function renderLoaded() {
  const view = renderUI();
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  return view;
}

function input(field: string) {
  return screen.getByTestId(`field-input-${field}`) as HTMLTextAreaElement;
}

function edit(field: string, value: string) {
  fireEvent.change(input(field), { target: { value } });
}

function lockButton() {
  return screen.getByTestId("lock-button") as HTMLButtonElement;
}

function saveButton() {
  return screen.getByTestId("save-draft-button") as HTMLButtonElement;
}

test("URL helpers point at the real FTF-04A19/FTF-04A20 API contract", () => {
  expect(manifestUrl(API_BASE, ORG_ID, PROJECT_ID, MANIFEST_ID)).toBe(
    `${API_BASE}${MANIFEST_PATH}`,
  );
  expect(evidenceUrl(`${API_BASE}/`, ORG_ID, PROJECT_ID, MANIFEST_ID)).toBe(
    `${API_BASE}${MANIFEST_PATH}/evidence`,
  );
});

test("mount only reads: it never saves, submits or locks on its own", async () => {
  await renderLoaded();
  expect(server.calls.map((call) => call.method).sort()).toEqual(["GET", "GET"]);
});

test("each field shows only its own evidence, and says so when there is none", async () => {
  installServer({
    evidence: [
      evidenceRow(),
      evidenceRow({
        id: "ev-2",
        field_path: "current_model_binding_json.model_alias",
        evidence_type: "config",
        symbol: null,
        line_start: null,
        line_end: null,
        file_path: "config/models.yaml",
        confidence_score: null,
      }),
    ],
  });
  await renderLoaded();
  expect(screen.getByTestId("evidence-type-ev-1").textContent).toBe("Repository symbol");
  expect(screen.getByTestId("evidence-location-ev-1").textContent).toBe("agent/main.py:10-24");
  expect(screen.getByTestId("evidence-commit-ev-1").textContent).toBe("abcdef012345");
  expect(screen.getByTestId("evidence-confidence-ev-1").textContent).toBe("Confidence: 0.9");
  // dotted field_path attaches to its leading segment only
  const bindingEvidence = screen.getByTestId("field-evidence-current_model_binding_json");
  expect(bindingEvidence.querySelector('[data-testid="evidence-ev-2"]')).toBeTruthy();
  expect(bindingEvidence.querySelector('[data-testid="evidence-ev-1"]')).toBeNull();
  expect(screen.getByTestId("evidence-confidence-ev-2").textContent).toBe(
    "Confidence: not available",
  );
  // a field with no row says so instead of showing nothing or inventing one
  expect(screen.getByTestId("field-no-evidence-mandate_json").textContent).toBe(
    "No recorded evidence",
  );
  expect(screen.queryByTestId("field-no-evidence-agent_entrypoint_json")).toBeNull();
});

test("evidenceForField matches the leading dot-segment exactly, never a prefix", () => {
  const rows = [
    evidenceRow({ id: "a", field_path: "mandate_json" }),
    evidenceRow({ id: "b", field_path: "mandate_json.goal" }),
    evidenceRow({ id: "c", field_path: "mandate_json_extra" }),
  ];
  expect(evidenceForField(rows, "mandate_json").map((row) => row.id)).toEqual(["a", "b"]);
});

test("an evidence load failure refuses to render fields rather than showing 'no evidence'", async () => {
  installServer({ evidenceStatus: 500 });
  renderUI();
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-load-error").textContent).toContain(
    "The service had a problem (500). Wait a minute, then try again.",
  );
  expect(screen.queryByTestId("field-mandate_json")).toBeNull();
});

test("overriding a field shows a line diff against the server value and labels it overridden", async () => {
  await renderLoaded();
  expect(screen.queryByTestId("field-overridden-runtime_dependencies_json")).toBeNull();
  edit("runtime_dependencies_json", JSON.stringify({ python: ">=3.12" }, null, 2));
  const diff = screen.getByTestId("field-diff-runtime_dependencies_json");
  const removed = diff.querySelector('[data-diff="removed"]');
  const added = diff.querySelector('[data-diff="added"]');
  expect(removed?.textContent).toContain('">=3.11"');
  expect(added?.textContent).toContain('">=3.12"');
  expect(screen.getByTestId("field-overridden-runtime_dependencies_json")).toBeTruthy();
  expect(
    screen.getByTestId("field-state-runtime_dependencies_json").getAttribute("data-state"),
  ).toBe("unsaved");
  // editing back to the server value removes the override
  edit("runtime_dependencies_json", JSON.stringify({ python: ">=3.11" }, null, 2));
  expect(screen.queryByTestId("field-overridden-runtime_dependencies_json")).toBeNull();
  expect(storage.getItem(STORAGE_KEY)).toBeNull();
});

test("lineDiff marks only the changed lines", () => {
  expect(lineDiff("a\nb\nc", "a\nB\nc")).toEqual([
    { kind: "same", text: "a" },
    { kind: "removed", text: "b" },
    { kind: "added", text: "B" },
    { kind: "same", text: "c" },
  ]);
  expect(lineDiff("x", "x").every((line) => line.kind === "same")).toBe(true);
});

test("D3: lineDiff marks a line appended after the last server line as added, never dropped", () => {
  expect(lineDiff("a", "a\nb")).toEqual([
    { kind: "same", text: "a" },
    { kind: "added", text: "b" },
  ]);
  expect(lineDiff("a\nb", "a\nb\nc\nd")).toEqual([
    { kind: "same", text: "a" },
    { kind: "same", text: "b" },
    { kind: "added", text: "c" },
    { kind: "added", text: "d" },
  ]);
});

test("D3 (UI): appending a line after the server's last line shows it as an added diff line", async () => {
  await renderLoaded();
  edit("runtime_dependencies_json", JSON.stringify({ python: ">=3.11" }, null, 2) + "\nextra");
  const diff = screen.getByTestId("field-diff-runtime_dependencies_json");
  const addedLines = Array.from(diff.querySelectorAll('[data-diff="added"]')).map(
    (node) => node.textContent,
  );
  expect(addedLines.some((text) => text?.includes("extra"))).toBe(true);
  expect(diff.querySelector('[data-diff="removed"]')).toBeNull();
});

test("P7: draftStorageKey is unique per organization, project and manifest", () => {
  const base = draftStorageKey(ORG_ID, PROJECT_ID, MANIFEST_ID);
  const otherOrg = draftStorageKey("org-2", PROJECT_ID, MANIFEST_ID);
  const otherProject = draftStorageKey(ORG_ID, "project-2", MANIFEST_ID);
  const otherManifest = draftStorageKey(ORG_ID, PROJECT_ID, "manifest-2");
  const keys = new Set([base, otherOrg, otherProject, otherManifest]);
  expect(keys.size).toBe(4);
});

test("P7 (UI): a draft stored for one manifest never appears when rendering a different manifest", async () => {
  const OTHER_MANIFEST_ID = "99999999-9999-9999-9999-999999999999";
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ agent_name: { text: "manifest-a-draft", baseText: "support-agent" } }),
  );
  const otherManifest = manifestRecord({ id: OTHER_MANIFEST_ID });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(url);
      const method = init?.method ?? "GET";
      if (parsed.pathname.endsWith("/evidence") && method === "GET") {
        return jsonResponse(200, []);
      }
      if (parsed.pathname.endsWith(`/manifests/${OTHER_MANIFEST_ID}`) && method === "GET") {
        return jsonResponse(200, otherManifest);
      }
      return jsonResponse(404, { detail: "not_found" });
    }),
  );
  render(
    createElement(ManifestReviewEditUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      manifestId: OTHER_MANIFEST_ID,
      storage,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  expect(screen.queryByTestId("draft-restored")).toBeNull();
  expect(input("agent_name").value).toBe("support-agent");
  // the manifest-A draft is untouched in storage, scoped to its own key
  expect(storage.getItem(STORAGE_KEY)).toContain("manifest-a-draft");
});

test("Save draft PATCHes only the changed fields with parsed values and clears the draft", async () => {
  await renderLoaded();
  edit("agent_name", "renamed-agent");
  edit("mandate_json", '{"goal":"resolve tickets"}');
  expect(storage.getItem(STORAGE_KEY)).not.toBeNull();
  fireEvent.click(saveButton());
  await waitFor(() => expect(callsOf("PATCH")).toHaveLength(1));
  const patch = callsOf("PATCH")[0]!;
  expect(patch.path).toBe(MANIFEST_PATH);
  expect(patch.body).toEqual({
    agent_name: "renamed-agent",
    mandate_json: { goal: "resolve tickets" },
  });
  await waitFor(() =>
    expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("clean"),
  );
  expect(storage.getItem(STORAGE_KEY)).toBeNull();
  expect(saveButton().disabled).toBe(true);
});

test("a refused save keeps every edit, locally and on screen", async () => {
  installServer({ patchStatus: 409 });
  await renderLoaded();
  edit("mandate_json", '{"goal":"keep me"}');
  fireEvent.click(saveButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-action-error").textContent).toContain(
    "The manifest is not in a state that allows this: it may be locked, changed elsewhere, or missing a required part. Reload to see the current version; your edits stay in the editor until you do.",
  );
  expect(input("mandate_json").value).toBe('{"goal":"keep me"}');
  expect(storage.getItem(STORAGE_KEY)).toContain("keep me");
  expect(screen.getByTestId("field-state-mandate_json").getAttribute("data-state")).toBe("unsaved");
});

test("a page refresh does not lose unsaved work", async () => {
  const first = await renderLoaded();
  edit("mandate_json", '{"goal":"survive refresh"}');
  first.unmount();
  cleanup();
  // a fresh mount, same storage: the refresh
  renderUI();
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  expect(input("mandate_json").value).toBe('{"goal":"survive refresh"}');
  expect(screen.getByTestId("draft-restored")).toBeTruthy();
  expect(screen.getByTestId("field-state-mandate_json").getAttribute("data-state")).toBe("unsaved");
  // restored work is not silently saved
  expect(callsOf("PATCH")).toHaveLength(0);
});

test("a stored draft equal to the server's value is dropped, not shown as restored work", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ agent_name: { text: "support-agent", baseText: "support-agent" } }),
  );
  await renderLoaded();
  expect(screen.queryByTestId("draft-restored")).toBeNull();
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("clean");
});

test("a stored draft for an unknown field or a malformed blob is ignored", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ not_a_field: { text: "x", baseText: "y" }, agent_name: { text: 5 } }),
  );
  await renderLoaded();
  expect(screen.queryByTestId("draft-restored")).toBeNull();
  cleanup();
  storage.setItem(STORAGE_KEY, "{not json");
  renderUI();
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  expect(screen.queryByTestId("draft-restored")).toBeNull();
});

test("unusable storage never breaks the page and edits still work", async () => {
  const broken = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("denied");
    },
    removeItem: () => {
      throw new Error("denied");
    },
  };
  renderUI(broken);
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  edit("agent_name", "still-works");
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("unsaved");
  fireEvent.click(saveButton());
  await waitFor(() => expect(callsOf("PATCH")).toHaveLength(1));
});

test("a draft based on an older server value is a conflict that blocks save and lock until resolved", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ agent_name: { text: "my-name", baseText: "older-server-name" } }),
  );
  await renderLoaded();
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("conflict");
  expect(screen.getByTestId("field-conflict-server-agent_name").textContent).toBe("support-agent");
  expect(screen.getByTestId("field-conflict-mine-agent_name").textContent).toBe("my-name");
  expect(saveButton().disabled).toBe(true);
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "Agent name: unresolved conflict. Choose a version.",
  );

  // Keep mine rebases the draft; it is then ordinary unsaved work, never auto-saved.
  fireEvent.click(screen.getByTestId("keep-mine-agent_name"));
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("unsaved");
  expect(input("agent_name").value).toBe("my-name");
  expect(callsOf("PATCH")).toHaveLength(0);
  expect(saveButton().disabled).toBe(false);
  expect(lockButton().disabled).toBe(true);
});

test("C6: typing into a conflicting field keeps it a conflict and Lock stays blocked", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ agent_name: { text: "my-name", baseText: "older-server-name" } }),
  );
  await renderLoaded();
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("conflict");
  // the user keeps typing without choosing "keep mine" or "take server's" first
  edit("agent_name", "my-name-v2");
  expect(input("agent_name").value).toBe("my-name-v2");
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("conflict");
  expect(screen.getByTestId("field-conflict-mine-agent_name")).toBeTruthy();
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "Agent name: unresolved conflict. Choose a version.",
  );
  expect(callsOf("PATCH")).toHaveLength(0);
});

test("Take server's drops the conflicting draft and clears the lock blocker", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ agent_name: { text: "my-name", baseText: "older-server-name" } }),
  );
  await renderLoaded();
  fireEvent.click(screen.getByTestId("take-server-agent_name"));
  expect(input("agent_name").value).toBe("support-agent");
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("clean");
  expect(lockButton().disabled).toBe(false);
  expect(storage.getItem(STORAGE_KEY)).toBeNull();
});

test("invalid JSON is flagged, blocks save and lock, and sends nothing", async () => {
  await renderLoaded();
  edit("mandate_json", "{not json");
  expect(screen.getByTestId("field-state-mandate_json").getAttribute("data-state")).toBe("invalid");
  expect(saveButton().disabled).toBe(true);
  expect(lockButton().disabled).toBe(true);
  fireEvent.click(saveButton());
  expect(callsOf("PATCH")).toHaveLength(0);
});

test("Lock is enabled only when every gate is clear, and then POSTs /lock", async () => {
  await renderLoaded();
  expect(lockButton().disabled).toBe(false);
  expect(screen.queryByTestId("lock-blockers")).toBeNull();
  fireEvent.click(lockButton());
  await waitFor(() => expect(callsOf("POST")).toHaveLength(1));
  expect(callsOf("POST")[0]!.path).toBe(`${MANIFEST_PATH}/lock`);
  await waitFor(() =>
    expect(screen.getByTestId("manifest-status").textContent).toContain("Locked"),
  );
  expect(screen.getByTestId("manifest-sha256")).toBeTruthy();
});

test("Lock is blocked while the manifest is still a draft", async () => {
  installServer({ manifest: manifestRecord({ status: "draft" }) });
  await renderLoaded();
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "Submit the manifest for review first.",
  );
  fireEvent.click(lockButton());
  expect(callsOf("POST")).toHaveLength(0);
});

test.each([
  ["agent_entrypoint_json", "Agent entrypoint: required before locking."],
  ["current_model_binding_json", "Current model binding: required before locking."],
  ["runtime_dependencies_json", "Runtime dependencies: required before locking."],
])("Lock is blocked when critical field %s is missing", async (field, message) => {
  installServer({ manifest: manifestRecord({ [field]: null }) });
  await renderLoaded();
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(message);
});

test("a model binding with an alias but no explicit role never satisfies the role gate", async () => {
  installServer({
    manifest: manifestRecord({
      current_model_binding_json: { resolved: true, model_alias: "gpt-prod" },
    }),
  });
  await renderLoaded();
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "Choose an explicit role for the current model.",
  );
  expect((screen.getByTestId("model-role-select") as HTMLSelectElement).value).toBe("");
});

test("an unrecognised role string does not satisfy the role gate", async () => {
  installServer({
    manifest: manifestRecord({
      current_model_binding_json: { resolved: true, role: "primary" },
    }),
  });
  await renderLoaded();
  expect(lockButton().disabled).toBe(true);
});

test("an unresolved model binding blocks lock even when a role is present", async () => {
  installServer({
    manifest: manifestRecord({
      current_model_binding_json: { resolved: false, role: "current_production" },
    }),
  });
  await renderLoaded();
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "The current model binding is unresolved.",
  );
});

test("choosing a role writes it into the binding draft, saves through PATCH, then unlocks Lock", async () => {
  installServer({
    manifest: manifestRecord({
      current_model_binding_json: { resolved: true, model_alias: "gpt-prod" },
    }),
  });
  await renderLoaded();
  fireEvent.change(screen.getByTestId("model-role-select"), { target: { value: "baseline" } });
  expect(JSON.parse(input("current_model_binding_json").value)).toEqual({
    resolved: true,
    model_alias: "gpt-prod",
    role: "baseline",
  });
  // unsaved: Lock stays blocked until the role is persisted
  expect(lockButton().disabled).toBe(true);
  fireEvent.click(saveButton());
  await waitFor(() => expect(callsOf("PATCH")).toHaveLength(1));
  expect(callsOf("PATCH")[0]!.body).toEqual({
    current_model_binding_json: { resolved: true, model_alias: "gpt-prod", role: "baseline" },
  });
  await waitFor(() => expect(lockButton().disabled).toBe(false));
});

test("unsaved edits block Lock and Submit for review", async () => {
  await renderLoaded();
  edit("mandate_json", '{"goal":"x"}');
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "Mandate: has unsaved changes. Save the draft.",
  );
  cleanup();
  installServer({ manifest: manifestRecord({ status: "draft" }) });
  storage = new MemoryStorage();
  renderUI();
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  const submit = screen.getByTestId("submit-for-review-button") as HTMLButtonElement;
  expect(submit.disabled).toBe(false);
  edit("mandate_json", '{"goal":"x"}');
  expect(submit.disabled).toBe(true);
});

test("Submit for review POSTs the real route from a draft manifest", async () => {
  installServer({ manifest: manifestRecord({ status: "draft" }) });
  await renderLoaded();
  fireEvent.click(screen.getByTestId("submit-for-review-button"));
  await waitFor(() => expect(callsOf("POST")).toHaveLength(1));
  expect(callsOf("POST")[0]!.path).toBe(`${MANIFEST_PATH}/submit-for-review`);
  await waitFor(() =>
    expect(screen.getByTestId("manifest-status").textContent).toContain("In review"),
  );
});

test("a backend lock refusal is shown and the manifest is not shown as locked", async () => {
  installServer({ lockStatus: 409 });
  await renderLoaded();
  fireEvent.click(lockButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-action-error").textContent).toContain(
    "The manifest is not in a state that allows this: it may be locked, changed elsewhere, or missing a required part. Reload to see the current version; your edits stay in the editor until you do.",
  );
  expect(screen.getByTestId("manifest-status").textContent).toContain("In review");
});

test("a locked manifest is read-only: edits are ignored and every action is disabled", async () => {
  installServer({
    manifest: manifestRecord({ status: "locked", manifest_sha256: "b".repeat(64) }),
  });
  await renderLoaded();
  expect(screen.getByTestId("manifest-read-only")).toBeTruthy();
  expect(input("agent_name").readOnly).toBe(true);
  edit("agent_name", "tamper");
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("clean");
  expect(input("agent_name").value).toBe("support-agent");
  expect(storage.getItem(STORAGE_KEY)).toBeNull();
  expect(saveButton().disabled).toBe(true);
  expect(lockButton().disabled).toBe(true);
  expect((screen.getByTestId("submit-for-review-button") as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByTestId("model-role-select") as HTMLSelectElement).disabled).toBe(true);
});

test("R3: a stored draft restored onto a locked manifest leaves Save disabled and sends no PATCH", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ agent_name: { text: "stale-draft", baseText: "support-agent" } }),
  );
  installServer({
    manifest: manifestRecord({ status: "locked", manifest_sha256: "b".repeat(64) }),
  });
  await renderLoaded();
  expect(screen.getByTestId("manifest-read-only")).toBeTruthy();
  // the draft is restored (not conflicting), so only the locked status can disable Save
  expect(screen.getByTestId("draft-restored")).toBeTruthy();
  expect(screen.getByTestId("field-state-agent_name").getAttribute("data-state")).toBe("unsaved");
  expect(saveButton().disabled).toBe(true);
  fireEvent.click(saveButton());
  expect(callsOf("PATCH")).toHaveLength(0);
});

test("a manifest load failure renders only the error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse(404, { detail: "not_found" })),
  );
  renderUI();
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-load-error").textContent).toContain(
    "This manifest no longer exists, or you do not have access to it. Go back and choose another one.",
  );
  expect(screen.queryByTestId("lock-button")).toBeNull();
});

test("a network failure on load is surfaced, never rendered as an empty manifest", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("offline");
    }),
  );
  renderUI();
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-load-error").textContent).toContain(
    "Could not reach the server. Check your connection, then try again.",
  );
});

test("clearing the agent name saves null, never an empty string", async () => {
  await renderLoaded();
  edit("agent_name", "");
  fireEvent.click(saveButton());
  await waitFor(() => expect(callsOf("PATCH")).toHaveLength(1));
  expect(callsOf("PATCH")[0]!.body).toEqual({ agent_name: null });
});

test("S8: a JSON field edited to the literal null is sent as an explicit null in the PATCH body, not dropped", async () => {
  await renderLoaded();
  // runtime_dependencies_json starts non-null ({python: ">=3.11"}), so editing to "null" is a real change
  edit("runtime_dependencies_json", "null");
  expect(
    screen.getByTestId("field-state-runtime_dependencies_json").getAttribute("data-state"),
  ).toBe("unsaved");
  fireEvent.click(saveButton());
  await waitFor(() => expect(callsOf("PATCH")).toHaveLength(1));
  const body = callsOf("PATCH")[0]!.body as Record<string, unknown>;
  expect("runtime_dependencies_json" in body).toBe(true);
  expect(body.runtime_dependencies_json).toBeNull();
});

// ---------------------------------------------------------------------------------------------
// FTF-UX03 round 2 (QUEUE 0g): bilingual en/ar, UX01 components, typed errors, strict parse of every
// reply field, bound replies, identity reset, no simulated progress. Each block names the behaviour
// it pins; the fault -> RED table in the submission records the mutation that turns each one red.
// ---------------------------------------------------------------------------------------------
import { act } from "@testing-library/react";

import {
  blockerMessage,
  EDITABLE_FIELDS,
  type LockBlocker,
  parseEvidence,
  parseManifest,
  unboundFields,
} from "../components/target-application/manifest_review_edit_ui_with_per_field_evidence";
import {
  evidenceTypeLabel,
  fieldLabel,
  roleLabel,
  t04a21,
} from "../components/target-application/strings_04a21";

type Handler = (
  url: URL,
  method: string,
  body: unknown,
) => ReturnType<typeof jsonResponse> | Promise<ReturnType<typeof jsonResponse>>;

function stubFetch(handler: Handler) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(url);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ method, path: parsed.pathname, body });
      return handler(parsed, method, body);
    }),
  );
  return calls;
}

/** A well-behaved server whose individual replies the caller can override. */
function stubServer(
  opts: {
    manifest?: unknown;
    evidence?: unknown;
    patch?: (body: Record<string, unknown>) => unknown;
    submit?: unknown;
    lock?: unknown;
  } = {},
) {
  const manifest = "manifest" in opts ? opts.manifest : manifestRecord();
  const evidence = "evidence" in opts ? opts.evidence : [evidenceRow()];
  return stubFetch((url, method, body) => {
    if (url.pathname === `${MANIFEST_PATH}/evidence`) return jsonResponse(200, evidence);
    if (url.pathname === MANIFEST_PATH && method === "GET") return jsonResponse(200, manifest);
    if (url.pathname === MANIFEST_PATH && method === "PATCH") {
      const reply = opts.patch
        ? opts.patch(body as Record<string, unknown>)
        : { ...(manifest as object), ...(body as object) };
      return jsonResponse(200, reply);
    }
    if (url.pathname === `${MANIFEST_PATH}/submit-for-review`) {
      return jsonResponse(
        200,
        opts.submit ?? { ...(manifest as object), status: "review_required" },
      );
    }
    if (url.pathname === `${MANIFEST_PATH}/lock`) {
      return jsonResponse(
        200,
        opts.lock ?? { ...(manifest as object), status: "locked", manifest_sha256: "a".repeat(64) },
      );
    }
    return jsonResponse(404, { detail: "not_found" });
  });
}

function renderLocale(locale: string, storageOverride: unknown = storage) {
  return render(
    createElement(ManifestReviewEditUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      manifestId: MANIFEST_ID,
      storage: storageOverride as Storage | null,
      locale: locale as "en" | "ar",
    }),
  );
}

function shell() {
  return screen.getByTestId("manifest-review-ui");
}

// --- [UX03-A] bilingual: exact Arabic strings, lang/dir on every state -------------------------

test("[UX03-A1] Arabic: every system string is the exact Arabic catalogue string, in an rtl/ar shell", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ agent_name: { text: "my-name", baseText: "older-server-name" } }),
  );
  renderLocale("ar");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  expect(shell().getAttribute("lang")).toBe("ar");
  expect(shell().getAttribute("dir")).toBe("rtl");
  expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("مراجعة بيان الوكيل");
  expect(screen.getByTestId("manifest-status").textContent).toContain("قيد المراجعة");
  expect(saveButton().textContent).toBe("حفظ المسودة");
  expect(lockButton().textContent).toBe("قفل البيان");
  expect(screen.getByTestId("submit-for-review-button").textContent).toBe("إرسال للمراجعة");
  expect(screen.getByTestId("field-no-evidence-mandate_json").textContent).toBe(
    "لا توجد أدلة مسجلة",
  );
  expect(screen.getByTestId("evidence-type-ev-1").textContent).toBe("رمز مستودع");
  expect(screen.getByTestId("evidence-confidence-ev-1").textContent).toBe("الثقة: 0.9");
  expect(screen.getByTestId("field-state-agent_name").textContent).toContain("تعارض");
  expect(screen.getByTestId("field-conflict-agent_name").textContent).toContain(
    "تغيّر هذا الحقل على الخادم بعد أن بدأت تعديله. اختر النسخة التي تريد الاحتفاظ بها.",
  );
  expect(screen.getByTestId("keep-mine-agent_name").textContent).toBe("الاحتفاظ بنسختي");
  expect(screen.getByTestId("take-server-agent_name").textContent).toBe("استخدام نسخة الخادم");
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "اسم الوكيل: تعارض غير محسوم. اختر نسخة.",
  );
  expect(screen.getByTestId("draft-restored").textContent).toContain(
    "تمت استعادة تعديلاتك غير المحفوظة من هذا المتصفح. لم تُحفظ على الخادم بعد.",
  );
  expect(screen.getByLabelText("اسم الوكيل")).toBeTruthy();
  expect(screen.getByLabelText("دور النموذج الحالي")).toBeTruthy();
  // no English system string is left on the page
  const text = shell().textContent ?? "";
  for (const english of [
    "Save draft",
    "Lock manifest",
    "No recorded evidence",
    "Conflict",
    "Evidence",
  ]) {
    expect(text, english).not.toContain(english);
  }
});

test("[UX03-A2] lang and dir are set on EVERY state: loading, load error, populated, read-only, conflict", async () => {
  const states: { name: string; setup: () => void; ready: () => void }[] = [
    {
      name: "loading",
      setup: () => stubFetch(() => new Promise(() => undefined)),
      ready: () => expect(screen.getByTestId("manifest-loading")).toBeTruthy(),
    },
    {
      name: "load error",
      setup: () => stubFetch(() => jsonResponse(500, {})),
      ready: () => expect(screen.getByTestId("manifest-load-error")).toBeTruthy(),
    },
    {
      name: "populated",
      setup: () => stubServer(),
      ready: () => expect(screen.getByTestId("manifest-status")).toBeTruthy(),
    },
    {
      name: "read-only",
      setup: () =>
        stubServer({
          manifest: manifestRecord({ status: "locked", manifest_sha256: "b".repeat(64) }),
        }),
      ready: () => expect(screen.getByTestId("manifest-read-only")).toBeTruthy(),
    },
    {
      name: "conflict",
      setup: () => {
        storage.setItem(
          STORAGE_KEY,
          JSON.stringify({ agent_name: { text: "mine", baseText: "older" } }),
        );
        stubServer();
      },
      ready: () => expect(screen.getByTestId("field-conflict-agent_name")).toBeTruthy(),
    },
  ];
  for (const state of states) {
    for (const [locale, lang, dir] of [
      ["en", "en", "ltr"],
      ["ar", "ar", "rtl"],
      ["fr", "en", "ltr"],
    ] as const) {
      cleanup();
      storage = new MemoryStorage();
      state.setup();
      renderLocale(locale);
      await waitFor(state.ready);
      expect(shell().getAttribute("lang"), `${state.name}/${locale} lang`).toBe(lang);
      expect(shell().getAttribute("dir"), `${state.name}/${locale} dir`).toBe(dir);
    }
  }
});

test("[UX03-A3] server data (hash, path, symbol, commit) is isolated in <bdi dir=ltr>; JSON editors are ltr", async () => {
  installServer({
    manifest: manifestRecord({ status: "locked", manifest_sha256: "c".repeat(64) }),
  });
  renderLocale("ar");
  await waitFor(() => expect(screen.getByTestId("manifest-sha256")).toBeTruthy());
  for (const id of [
    "manifest-sha256",
    "evidence-location-ev-1",
    "evidence-symbol-ev-1",
    "evidence-commit-ev-1",
  ]) {
    const node = screen.getByTestId(id);
    const bdi = node.tagName === "BDI" ? node : node.querySelector("bdi");
    expect(bdi, id).toBeTruthy();
    expect(bdi!.getAttribute("dir"), id).toBe("ltr");
  }
  expect(input("mandate_json").getAttribute("dir")).toBe("ltr");
  expect(input("agent_name").getAttribute("dir")).toBe("auto");
});

test("[UX03-A4] the catalogue is complete: every key has a non-empty English and Arabic string and every field has a label", () => {
  for (const field of EDITABLE_FIELDS) {
    expect(fieldLabel(field, "en")).not.toBe(field);
    expect(fieldLabel(field, "ar")).toMatch(/[؀-ۿ]/);
    expect(fieldLabel(field, "ar")).not.toBe(fieldLabel(field, "en"));
  }
  for (const role of ["current_production", "baseline", "candidate", "teacher", "judge"]) {
    expect(roleLabel(role, "ar")).toMatch(/[؀-ۿ]/);
    expect(roleLabel(role, "en")).not.toBe(role);
  }
  for (const type of [
    "repo_file",
    "repo_symbol",
    "config",
    "test",
    "runtime_probe",
    "user_input",
  ]) {
    expect(evidenceTypeLabel(type, "ar")).toMatch(/[؀-ۿ]/);
    expect(evidenceTypeLabel(type, "en")).not.toBe(type);
  }
  expect(t04a21("saveDraft", "ar")).toBe("حفظ المسودة");
  // an unknown field / role / type shows itself, never "undefined" and never throws
  expect(fieldLabel("toString", "en")).toBe("toString");
  expect(roleLabel("__proto__", "en")).toBe("__proto__");
  expect(evidenceTypeLabel("constructor", "ar")).toBe("constructor");
});

// --- [UX03-B] typed errors: nothing blank, nothing unmounted, always a way forward -------------

test("[UX03-B1] a rejected fetch on load is an announced error with a Retry that reloads for real", async () => {
  let online = false;
  const calls = stubFetch((url) => {
    if (!online) throw new Error("offline");
    if (url.pathname.endsWith("/evidence")) return jsonResponse(200, [evidenceRow()]);
    return jsonResponse(200, manifestRecord());
  });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain(
    "Could not reach the server. Check your connection, then try again.",
  );
  expect(screen.queryByTestId("lock-button")).toBeNull();
  const before = calls.length;
  online = true;
  fireEvent.click(screen.getByTestId("manifest-retry-button"));
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  expect(calls.length).toBeGreaterThan(before);
  expect(screen.queryByTestId("manifest-load-error")).toBeNull();
});

test("[UX03-B2] the load error and Retry are Arabic in an Arabic page", async () => {
  stubFetch(() => {
    throw new Error("offline");
  });
  renderLocale("ar");
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  expect(screen.getByRole("alert").textContent).toContain(
    "تعذّر الاتصال بالخادم. تحقق من اتصالك ثم حاول مرة أخرى.",
  );
  expect(screen.getByTestId("manifest-retry-button").textContent).toBe("حاول مرة أخرى");
});

test("[UX03-B3] an unreadable (non-JSON) 200 body is a malformed-reply error, never a blank page", async () => {
  stubFetch((url) =>
    url.pathname.endsWith("/evidence")
      ? jsonResponse(200, [])
      : ({
          ok: true,
          status: 200,
          json: async () => {
            throw new SyntaxError("Unexpected token <");
          },
        } as unknown as ReturnType<typeof jsonResponse>),
  );
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-load-error").textContent).toContain(
    "The server sent a reply this page cannot read.",
  );
  expect(screen.queryByTestId("field-mandate_json")).toBeNull();
});

test.each([
  [401, "Your session has ended. Sign in again, then retry."],
  [
    403,
    "You do not have access to this. Ask an organization administrator to grant it, then retry.",
  ],
  [503, "The service had a problem (503). Wait a minute, then try again."],
])("[UX03-B4] a %s on load names the problem and the next step", async (status, sentence) => {
  stubFetch(() => jsonResponse(status, {}));
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-load-error").textContent).toContain(sentence);
});

test("[UX03-B5] a rejected PATCH is announced, keeps every draft, and the page stays mounted and retryable", async () => {
  let fail = true;
  stubFetch((url, method, body) => {
    if (url.pathname.endsWith("/evidence")) return jsonResponse(200, [evidenceRow()]);
    if (method === "PATCH") {
      if (fail) throw new Error("offline");
      return jsonResponse(200, { ...manifestRecord(), ...(body as object) });
    }
    return jsonResponse(200, manifestRecord());
  });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  edit("mandate_json", '{"goal":"keep me"}');
  fireEvent.click(saveButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-action-error").textContent).toContain(
    "Could not reach the server. Check your connection, then try again.",
  );
  expect(input("mandate_json").value).toBe('{"goal":"keep me"}');
  expect(storage.getItem(STORAGE_KEY)).toContain("keep me");
  expect(screen.queryByTestId("manifest-action-notice")).toBeNull();
  fail = false;
  expect(saveButton().disabled).toBe(false);
  fireEvent.click(saveButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-notice")).toBeTruthy());
});

test.each([
  [
    "a 500 with a non-JSON body",
    () => ({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("html");
      },
    }),
  ],
  ["a 502 with an unknown code", () => jsonResponse(502, { detail: "brand_new_code" })],
  ["a 418 with an unknown code", () => jsonResponse(418, { detail: "teapot" })],
])("[UX03-B6] %s on Lock is retryable and keeps the manifest un-locked", async (_name, reply) => {
  stubFetch((url, method) => {
    if (url.pathname.endsWith("/evidence")) return jsonResponse(200, [evidenceRow()]);
    if (method === "POST") return reply() as ReturnType<typeof jsonResponse>;
    return jsonResponse(200, manifestRecord());
  });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  fireEvent.click(lockButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-status").getAttribute("data-manifest-status")).toBe(
    "review_required",
  );
  expect(screen.queryByTestId("manifest-action-notice")).toBeNull();
  expect(lockButton().disabled).toBe(false);
});

test("[UX03-B7] invalid JSON is tied to its editor: aria-invalid, aria-describedby -> an announced message", async () => {
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  expect(input("mandate_json").getAttribute("aria-invalid")).toBeNull();
  edit("mandate_json", "{not json");
  const editor = input("mandate_json");
  expect(editor.getAttribute("aria-invalid")).toBe("true");
  const error = screen.getByTestId("field-error-mandate_json");
  expect(error.getAttribute("role")).toBe("alert");
  expect(error.textContent).toBe(
    "This is not valid JSON. Fix the syntax, for example a missing quote or bracket, to be able to save.",
  );
  expect(editor.getAttribute("aria-describedby")!.split(" ")).toContain(error.id);
  edit("mandate_json", '{"ok":1}');
  expect(screen.queryByTestId("field-error-mandate_json")).toBeNull();
  expect(input("mandate_json").getAttribute("aria-invalid")).toBeNull();
});

// --- [UX03-C] strict parse of EVERY reply field (QUEUE UI CHECKLIST a) --------------------------

const MANIFEST_SCALAR_FAULTS: [string, string, unknown][] = [
  ["id", "wrong type", 5],
  ["id", "blank", ""],
  ["project_id", "wrong type", null],
  ["project_id", "blank", ""],
  ["organization_id", "wrong type", 5],
  ["organization_id", "blank", ""],
  ["organization_id", "another organization", "99999999-9999-9999-9999-999999999999"],
  ["target_source_snapshot_id", "wrong type", null],
  ["target_source_snapshot_id", "blank", ""],
  ["status", "outside the enum", "approved"],
  ["status", "wrong type", 7],
  ["status", "wrong case", "LOCKED"],
  ["version", "string", "1"],
  ["version", "zero", 0],
  ["version", "fractional", 1.5],
  ["version", "null", null],
  ["manifest_sha256", "number", 5],
  ["agent_name", "number", 5],
  ["agent_name", "object", {}],
];

const MANIFEST_MISSING = [
  "id",
  "project_id",
  "organization_id",
  "target_source_snapshot_id",
  "status",
  "version",
  "manifest_sha256",
  "agent_name",
  ...EDITABLE_FIELDS.filter((f) => f !== "agent_name"),
];

async function expectMalformedAndInert() {
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-load-error").textContent).toContain(
    "The server sent a reply this page cannot read.",
  );
  for (const control of [
    "save-draft-button",
    "submit-for-review-button",
    "lock-button",
    "model-role-select",
  ]) {
    expect(screen.queryByTestId(control), control).toBeNull();
  }
  expect(screen.queryByTestId("field-agent_name")).toBeNull();
  expect(screen.queryByTestId("manifest-status")).toBeNull();
}

test.each(MANIFEST_SCALAR_FAULTS)(
  "[UX03-C1] manifest.%s %s -> malformed, no action control",
  async (field, _kind, value) => {
    stubServer({ manifest: manifestRecord({ [field]: value }) });
    renderLocale("en");
    await expectMalformedAndInert();
  },
);

test.each(MANIFEST_MISSING)(
  "[UX03-C2] manifest without %s -> malformed, no action control",
  async (field) => {
    const record = manifestRecord() as Record<string, unknown>;
    delete record[field];
    stubServer({ manifest: record });
    renderLocale("en");
    await expectMalformedAndInert();
  },
);

test.each([
  ["null", null],
  ["an array", []],
  ["a string", "manifest"],
])("[UX03-C3] a manifest body that is %s -> malformed", async (_name, body) => {
  stubServer({ manifest: body });
  renderLocale("en");
  await expectMalformedAndInert();
});

const EVIDENCE_KEYS = [
  "id",
  "organization_id",
  "agent_manifest_id",
  "field_path",
  "evidence_type",
  "repository_commit_sha",
  "file_path",
  "line_start",
  "line_end",
  "symbol",
  "content_hash",
  "confidence_score",
];
const EVIDENCE_WRONG: [string, unknown][] = [
  ["id", 5],
  ["id", ""],
  ["agent_manifest_id", "99999999-9999-9999-9999-999999999999"],
  ["agent_manifest_id", 5],
  ["agent_manifest_id", null],
  ["organization_id", "99999999-9999-9999-9999-999999999999"],
  ["organization_id", 5],
  ["organization_id", null],
  ["field_path", null],
  ["evidence_type", "guess"],
  ["evidence_type", 3],
  ["repository_commit_sha", 5],
  ["file_path", 5],
  ["line_start", "10"],
  ["line_end", "24"],
  ["symbol", 5],
  ["content_hash", 5],
  ["confidence_score", "0.9"],
  ["confidence_score", Number.NaN],
];

test.each(EVIDENCE_WRONG)(
  "[UX03-C4] evidence.%s = %j -> malformed, never 'no evidence'",
  async (key, value) => {
    stubServer({ evidence: [evidenceRow({ [key]: value } as Partial<EvidenceRecord>)] });
    renderLocale("en");
    await expectMalformedAndInert();
  },
);

test.each(EVIDENCE_KEYS)("[UX03-C5] evidence row without %s -> malformed", async (key) => {
  const row = { ...evidenceRow() } as Record<string, unknown>;
  delete row[key];
  stubServer({ evidence: [row] });
  renderLocale("en");
  await expectMalformedAndInert();
});

test.each([
  ["an object", {}],
  ["null", null],
  ["a row that is a string", ["ev"]],
  ["a good row followed by a bad one", [evidenceRow(), { id: "x" }]],
])("[UX03-C6] an evidence body that is %s -> malformed", async (_name, body) => {
  stubServer({ evidence: body });
  renderLocale("en");
  await expectMalformedAndInert();
});

const EV_CTX = { manifestId: MANIFEST_ID, organizationId: ORG_ID };

test("[UX03-C7] the parsers accept the real shapes and reject the wrong ones directly", () => {
  expect(parseManifest(manifestRecord())).not.toBeNull();
  expect(parseManifest({ ...manifestRecord(), status: "superseded" })).not.toBeNull();
  expect(parseManifest({ ...manifestRecord(), manifest_sha256: "a".repeat(64) })).not.toBeNull();
  expect(parseManifest(undefined)).toBeNull();
  expect(parseEvidence([], EV_CTX)).toEqual([]);
  expect(
    parseEvidence(
      [evidenceRow({ line_start: null, line_end: null, confidence_score: null })],
      EV_CTX,
    ),
  ).toHaveLength(1);
  expect(parseEvidence(undefined, EV_CTX)).toBeNull();
});

// --- [UX03-D] one binding function: the reply must match what was sent --------------------------

const SNAPSHOT_ID = "44444444-4444-4444-4444-444444444444";
const CTX = {
  manifestId: MANIFEST_ID,
  projectId: PROJECT_ID,
  organizationId: ORG_ID,
  snapshotId: SNAPSHOT_ID,
};

test("[UX03-D1] unboundFields names every mismatch and nothing else", () => {
  const base = manifestRecord();
  const patch = {
    kind: "patch" as const,
    sent: { mandate_json: { goal: "x" }, agent_name: null },
    previousStatus: "review_required",
  };
  const good = { ...base, mandate_json: { goal: "x" }, agent_name: null };
  expect(unboundFields(patch, good, CTX)).toEqual([]);
  expect(unboundFields(patch, { ...good, id: "other" }, CTX)).toEqual(["id"]);
  expect(unboundFields(patch, { ...good, project_id: "other" }, CTX)).toEqual(["project_id"]);
  expect(unboundFields(patch, { ...good, organization_id: "other" }, CTX)).toEqual([
    "organization_id",
  ]);
  expect(unboundFields(patch, { ...good, target_source_snapshot_id: "other" }, CTX)).toEqual([
    "target_source_snapshot_id",
  ]);
  expect(unboundFields(patch, { ...good, status: "locked" }, CTX)).toEqual(["status"]);
  expect(unboundFields(patch, { ...good, mandate_json: { goal: "y" } }, CTX)).toEqual([
    "mandate_json",
  ]);
  expect(unboundFields(patch, { ...good, agent_name: "kept" }, CTX)).toEqual(["agent_name"]);
  // key order inside a JSON object is not a difference; array order is
  expect(
    unboundFields(
      {
        kind: "patch",
        sent: { tool_contracts_json: { a: 1, b: [1, 2] } },
        previousStatus: "review_required",
      },
      { ...base, tool_contracts_json: { b: [1, 2], a: 1 } },
      CTX,
    ),
  ).toEqual([]);
  expect(
    unboundFields(
      { kind: "patch", sent: { tool_contracts_json: [1, 2] }, previousStatus: "review_required" },
      { ...base, tool_contracts_json: [2, 1] },
      CTX,
    ),
  ).toEqual(["tool_contracts_json"]);
  expect(unboundFields({ kind: "submit" }, { ...base, status: "review_required" }, CTX)).toEqual(
    [],
  );
  expect(unboundFields({ kind: "submit" }, { ...base, status: "draft" }, CTX)).toEqual(["status"]);
  expect(
    unboundFields(
      { kind: "lock" },
      { ...base, status: "locked", manifest_sha256: "a".repeat(64) },
      CTX,
    ),
  ).toEqual([]);
  expect(
    unboundFields(
      { kind: "lock" },
      { ...base, status: "review_required", manifest_sha256: null },
      CTX,
    ),
  ).toEqual(["status", "manifest_sha256"]);
});

const SAMPLE_EDIT: Record<string, string> = Object.fromEntries(
  EDITABLE_FIELDS.map((f) => [f, f === "agent_name" ? "renamed" : '{"changed":true}']),
);

test.each(EDITABLE_FIELDS)(
  "[UX03-D2] a PATCH reply that does not carry the saved %s is refused: not saved, draft kept",
  async (field) => {
    stubServer({
      patch: (body) => {
        const reply = { ...manifestRecord(), ...body } as Record<string, unknown>;
        reply[field] = field === "agent_name" ? "somebody-else" : { server: "differs" };
        return reply;
      },
    });
    renderLocale("en");
    await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
    edit(field, SAMPLE_EDIT[field]!);
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
    expect(screen.getByTestId("manifest-action-error").textContent).toContain(
      "The server's reply does not match what was sent, so nothing was marked as done.",
    );
    expect(screen.queryByTestId("manifest-action-notice")).toBeNull();
    expect(input(field).value).toBe(SAMPLE_EDIT[field]);
    expect(screen.getByTestId(`field-state-${field}`).getAttribute("data-state")).toBe("unsaved");
    expect(storage.getItem(STORAGE_KEY)).toContain(field);
  },
);

test.each([
  ["another manifest's id", { id: "99999999-9999-9999-9999-999999999999" }],
  ["another project's id", { project_id: "88888888-8888-8888-8888-888888888888" }],
  ["a changed status", { status: "locked" }],
])("[UX03-D3] a PATCH reply carrying %s is refused", async (_name, change) => {
  stubServer({ patch: (body) => ({ ...manifestRecord(), ...body, ...change }) });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  edit("mandate_json", '{"goal":"x"}');
  fireEvent.click(saveButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-status").getAttribute("data-manifest-status")).toBe(
    "review_required",
  );
  expect(screen.getByTestId("field-state-mandate_json").getAttribute("data-state")).toBe("unsaved");
});

test("[UX03-D4] a submit reply still in draft, and a lock reply that is not locked, are refused", async () => {
  stubServer({
    manifest: manifestRecord({ status: "draft" }),
    submit: manifestRecord({ status: "draft" }),
  });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  fireEvent.click(screen.getByTestId("submit-for-review-button"));
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.queryByTestId("manifest-action-notice")).toBeNull();
  expect(screen.getByTestId("manifest-status").getAttribute("data-manifest-status")).toBe("draft");
  cleanup();
  stubServer({ lock: manifestRecord({ status: "locked", manifest_sha256: null }) });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  fireEvent.click(lockButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-status").getAttribute("data-manifest-status")).toBe(
    "review_required",
  );
  expect(screen.queryByTestId("manifest-sha256")).toBeNull();
});

test("[UX03-D5] a malformed 200 reply to an action is refused and never replaces the manifest", async () => {
  stubServer({ patch: () => ({ nope: true }) });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  edit("mandate_json", '{"goal":"x"}');
  fireEvent.click(saveButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-action-error").textContent).toContain("cannot read");
  expect(screen.getByTestId("manifest-status")).toBeTruthy();
  expect(input("mandate_json").value).toBe('{"goal":"x"}');
});

// --- [UX03-E] identity: every prop that names the manifest resets drafts, results and errors ----

test.each([
  ["manifestId", { manifestId: "55555555-5555-5555-5555-555555555555" }],
  ["projectId", { projectId: "66666666-6666-6666-6666-666666666666" }],
  ["organizationId", { organizationId: "77777777-7777-7777-7777-777777777777" }],
  ["apiBaseUrl", { apiBaseUrl: "http://other.example:9000" }],
])(
  "[UX03-E1] changing %s drops the previous manifest, its drafts and its notices at once",
  async (_name, change) => {
    const props = {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      manifestId: MANIFEST_ID,
      storage: storage as unknown as Storage,
    };
    const view = render(createElement(ManifestReviewEditUI, props));
    await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
    edit("mandate_json", '{"goal":"mine"}');
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.getByTestId("manifest-action-notice")).toBeTruthy());
    edit("agent_name", "unsaved-name");
    stubFetch(() => new Promise(() => undefined)); // the next manifest never answers
    view.rerender(createElement(ManifestReviewEditUI, { ...props, ...change }));
    expect(screen.getByTestId("manifest-loading")).toBeTruthy();
    expect(screen.queryByTestId("field-input-agent_name")).toBeNull();
    expect(screen.queryByTestId("manifest-action-notice")).toBeNull();
    expect(screen.queryByTestId("manifest-status")).toBeNull();
    expect(screen.queryByTestId("draft-restored")).toBeNull();
  },
);

test("[UX03-E2] a slow reply for the previous manifest never lands on the new one", async () => {
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  stubFetch(async (url) => {
    await gate;
    return url.pathname.endsWith("/evidence")
      ? jsonResponse(200, [])
      : jsonResponse(200, manifestRecord({ agent_name: "from-the-old-manifest" }));
  });
  const props = {
    apiBaseUrl: API_BASE,
    organizationId: ORG_ID,
    projectId: PROJECT_ID,
    manifestId: MANIFEST_ID,
    storage: storage as unknown as Storage,
  };
  const view = render(createElement(ManifestReviewEditUI, props));
  stubFetch(() => new Promise(() => undefined));
  view.rerender(
    createElement(ManifestReviewEditUI, {
      ...props,
      manifestId: "55555555-5555-5555-5555-555555555555",
    }),
  );
  await act(async () => {
    release?.();
    await gate;
  });
  expect(screen.queryByTestId("field-input-agent_name")).toBeNull();
  expect(screen.getByTestId("manifest-loading")).toBeTruthy();
});

// --- [UX03-F] no simulated progress or success ---------------------------------------------------

test("[UX03-F1] nothing is shown as saved until a typed 2xx arrives; meanwhile the page is busy and inert", async () => {
  let reply: (() => void) | undefined;
  stubFetch((url, method, body) => {
    if (url.pathname.endsWith("/evidence")) return jsonResponse(200, [evidenceRow()]);
    if (method === "PATCH") {
      return new Promise((resolve) => {
        reply = () => resolve(jsonResponse(200, { ...manifestRecord(), ...(body as object) }));
      });
    }
    return jsonResponse(200, manifestRecord());
  });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  edit("mandate_json", '{"goal":"x"}');
  fireEvent.click(saveButton());
  await waitFor(() => expect(saveButton().disabled).toBe(true));
  expect(shell().getAttribute("aria-busy")).toBe("true");
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("submit-for-review-button")).toHaveProperty("disabled", true);
  expect(screen.queryByTestId("manifest-action-notice")).toBeNull();
  expect(screen.getByTestId("field-state-mandate_json").getAttribute("data-state")).toBe("unsaved");
  await act(async () => reply?.());
  await waitFor(() => expect(screen.getByTestId("manifest-action-notice")).toBeTruthy());
  expect(screen.getByTestId("manifest-action-notice").textContent).toContain("Draft saved.");
  expect(shell().getAttribute("aria-busy")).toBeNull();
});

test("[UX03-F2] the success notice is announced politely and names what happened (save, submit, lock)", async () => {
  stubServer({ manifest: manifestRecord({ status: "draft" }) });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  fireEvent.click(screen.getByTestId("submit-for-review-button"));
  await waitFor(() => expect(screen.getByTestId("manifest-action-notice")).toBeTruthy());
  expect(screen.getByTestId("manifest-action-notice").textContent).toContain(
    "Submitted for review.",
  );
  expect(screen.getByTestId("manifest-action-notice").querySelector("[role=status]")).toBeTruthy();
});

test("[UX03-F3] while loading: busy shell, the loading text, and no 'no evidence' empty state", async () => {
  stubFetch(() => new Promise(() => undefined));
  renderLocale("en");
  expect(shell().getAttribute("aria-busy")).toBe("true");
  expect(screen.getByTestId("manifest-loading").textContent).toBe("Loading manifest…");
  expect(screen.queryByTestId("field-no-evidence-mandate_json")).toBeNull();
  expect(screen.queryByTestId("lock-button")).toBeNull();
});

test("[UX03-F4] a busy indicator appears after 1 s of waiting (NN/g response times)", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
  try {
    stubFetch(() => new Promise(() => undefined));
    renderLocale("en");
    expect(screen.queryByRole("status")).toBeNull();
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByRole("status").textContent).toContain("Loading manifest…");
  } finally {
    vi.useRealTimers();
  }
});

// --- [UX03-G] UX01 components: targets, names, reasons, state not by colour alone ---------------

test("[UX03-G1] every button is a UX01 Button: >= 24 px target token, in every state", async () => {
  storage.setItem(STORAGE_KEY, JSON.stringify({ agent_name: { text: "mine", baseText: "older" } }));
  await renderLoaded();
  const buttons = Array.from(shell().querySelectorAll("button"));
  expect(buttons.length).toBeGreaterThanOrEqual(5);
  for (const button of buttons) {
    expect(button.style.minBlockSize, button.textContent ?? "").toBe("var(--target-min)");
    expect(button.style.minInlineSize).toBe("var(--target-min)");
    expect(button.getAttribute("data-variant")).toBeTruthy();
  }
});

test("[UX03-G2] a disabled action states WHY, in the page's language (H5)", async () => {
  installServer({ manifest: manifestRecord({ status: "draft" }) });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  const reasons = () => screen.getAllByTestId("disabled-reason").map((n) => n.textContent);
  expect(reasons()).toContain("There are no unsaved changes");
  expect(reasons()).toContain("Finish the items listed below first");
  expect(saveButton().getAttribute("aria-describedby")).toBeTruthy();
  cleanup();
  installServer({
    manifest: manifestRecord({ status: "locked", manifest_sha256: "d".repeat(64) }),
  });
  renderLocale("ar");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  expect(screen.getAllByTestId("disabled-reason").map((n) => n.textContent)).toContain(
    "لا يمكن تعديل بيان مقفل أو مستبدل",
  );
});

test("[UX03-G3] controls have programmatic names: labelled editors, distinct conflict buttons", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      agent_name: { text: "mine", baseText: "older" },
      mandate_json: { text: '{"a":1}', baseText: '{"x":1}' },
    }),
  );
  await renderLoaded();
  for (const field of EDITABLE_FIELDS) {
    expect(screen.getByLabelText(fieldLabel(field, "en")), field).toBe(input(field));
  }
  expect(screen.getByTestId("keep-mine-agent_name").getAttribute("aria-label")).toBe(
    "Keep my version of Agent name",
  );
  expect(screen.getByTestId("keep-mine-mandate_json").getAttribute("aria-label")).toBe(
    "Keep my version of Mandate",
  );
  expect(screen.getByTestId("take-server-agent_name").getAttribute("aria-label")).toBe(
    "Use the server's version of Agent name",
  );
  const ids = new Set(Array.from(shell().querySelectorAll("[id]")).map((n) => n.id));
  expect(ids.size).toBe(shell().querySelectorAll("[id]").length);
});

test("[UX03-G4] state is words + an icon, never colour alone: field state, status, diff marks", async () => {
  await renderLoaded();
  edit("runtime_dependencies_json", JSON.stringify({ python: ">=3.12" }, null, 2));
  const state = screen.getByTestId("field-state-runtime_dependencies_json");
  expect(state.textContent).toBe("● Unsaved changes");
  expect(state.querySelector("[aria-hidden=true]")).toBeTruthy();
  const diff = screen.getByTestId("field-diff-runtime_dependencies_json");
  expect(diff.querySelector('[data-diff="added"]')!.textContent!.startsWith("+ ")).toBe(true);
  expect(diff.querySelector('[data-diff="removed"]')!.textContent!.startsWith("- ")).toBe(true);
  expect(screen.getByTestId("manifest-status").textContent).toContain("In review");
});

test("[UX03-G5] a conflict is announced in words with its own banner, not only styled", async () => {
  storage.setItem(STORAGE_KEY, JSON.stringify({ agent_name: { text: "mine", baseText: "older" } }));
  await renderLoaded();
  const region = screen.getByTestId("field-conflict-agent_name");
  const banner = region.querySelector("[data-kind=warning]");
  expect(banner).toBeTruthy();
  expect(banner!.getAttribute("role")).toBe("status");
  expect(banner!.textContent).toContain("Choose which version to keep.");
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("! Conflict");
  expect(input("agent_name").getAttribute("aria-describedby")!.split(" ")).toContain(region.id);
});

test("[UX03-G6] blockerMessage and the lock list are localized for every blocker kind", () => {
  const cases: [LockBlocker, string, string][] = [
    [
      { code: "needs_review" as const },
      "Submit the manifest for review first.",
      "أرسل البيان للمراجعة أولًا.",
    ],
    [
      { code: "invalid" as const, field: "mandate_json" },
      "Mandate: the JSON is not valid.",
      "التفويض: بنية JSON غير صالحة.",
    ],
    [
      { code: "role" as const },
      "Choose an explicit role for the current model.",
      "اختر دورًا صريحًا للنموذج الحالي.",
    ],
    [
      { code: "binding_object" as const },
      "The current model binding must be a JSON object.",
      "يجب أن يكون ربط النموذج الحالي كائن JSON.",
    ],
  ];
  for (const [blocker, en, ar] of cases) {
    expect(blockerMessage(blocker, "en")).toBe(en);
    expect(blockerMessage(blocker, "ar")).toBe(ar);
  }
});

test("[UX03-G7] evidence with a start line but no end line shows the start only, never ':10-null'", async () => {
  stubServer({ evidence: [evidenceRow({ line_start: 10, line_end: null })] });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("evidence-location-ev-1")).toBeTruthy());
  expect(screen.getByTestId("evidence-location-ev-1").textContent).toBe("agent/main.py:10");
});

// --- [UX03-R1] pre-review: replies are bound to the manifest/project that was asked for ---------

const OTHER_ID = "99999999-9999-9999-9999-999999999999";

test.each([
  ["id", OTHER_ID],
  ["project_id", OTHER_ID],
])(
  "[UX03-R1] a GET manifest reply whose %s names another resource is malformed and renders nothing of it",
  async (field, value) => {
    stubServer({ manifest: manifestRecord({ [field]: value, agent_name: "OTHER-AGENT" }) });
    renderLocale("en");
    await expectMalformedAndInert();
    expect(document.body.textContent).not.toContain("OTHER-AGENT");
  },
);

test("[UX03-R1] evidence of another manifest is never shown as this manifest's evidence", async () => {
  stubServer({ evidence: [evidenceRow({ agent_manifest_id: OTHER_ID, symbol: "OTHER-SYMBOL" })] });
  renderLocale("en");
  await expectMalformedAndInert();
  expect(document.body.textContent).not.toContain("OTHER-SYMBOL");
});

test("[UX03-R1] parseEvidence binds agent_manifest_id and organization_id, one fault each", () => {
  expect(parseEvidence([evidenceRow()], EV_CTX)).toHaveLength(1);
  expect(parseEvidence([evidenceRow({ agent_manifest_id: OTHER_ID })], EV_CTX)).toBeNull();
  expect(parseEvidence([evidenceRow({ organization_id: OTHER_ID })], EV_CTX)).toBeNull();
  expect(
    parseEvidence(
      [evidenceRow(), evidenceRow({ id: "ev-2", agent_manifest_id: OTHER_ID })],
      EV_CTX,
    ),
  ).toBeNull();
});

test("[UX03-R1] edits typed while a save is in flight are refused (read-only), never lost or reverted", async () => {
  let reply: (() => void) | undefined;
  stubFetch((url, method, body) => {
    if (url.pathname.endsWith("/evidence")) return jsonResponse(200, [evidenceRow()]);
    if (method === "PATCH") {
      return new Promise((resolve) => {
        reply = () => resolve(jsonResponse(200, { ...manifestRecord(), ...(body as object) }));
      });
    }
    return jsonResponse(200, manifestRecord());
  });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  edit("mandate_json", '{"goal":"first"}');
  fireEvent.click(saveButton());
  await waitFor(() => expect(saveButton().disabled).toBe(true));
  const stored = storage.getItem(draftStorageKey(ORG_ID, PROJECT_ID, MANIFEST_ID));
  expect(input("mandate_json").readOnly).toBe(true);
  expect(input("agent_name").readOnly).toBe(true);
  expect((screen.getByTestId("model-role-select") as HTMLSelectElement).disabled).toBe(true);
  edit("agent_name", "typed-during-save");
  edit("mandate_json", '{"goal":"second"}');
  expect(input("agent_name").value).not.toBe("typed-during-save");
  expect(input("mandate_json").value).toBe('{"goal":"first"}');
  expect(storage.getItem(draftStorageKey(ORG_ID, PROJECT_ID, MANIFEST_ID))).toBe(stored);
  await act(async () => reply?.());
  await waitFor(() => expect(screen.getByTestId("manifest-action-notice")).toBeTruthy());
  expect(input("mandate_json").readOnly).toBe(false);
  edit("agent_name", "after");
  expect(input("agent_name").value).toBe("after");
});

// --- [UX03-R2] pre-review round 2: tenant binding on every manifest reply, real backend failures ---

const OTHER_ORG = "99999999-9999-9999-9999-999999999999";

test("[UX03-R2-ORG1] a save reply from another organization is refused: nothing shown as saved, drafts kept", async () => {
  stubServer({ patch: (body) => ({ ...manifestRecord(), ...body, organization_id: OTHER_ORG }) });
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
  edit("mandate_json", '{"goal":"x"}');
  fireEvent.click(saveButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.queryByTestId("manifest-action-notice")).toBeNull();
  expect(input("mandate_json").value).toBe('{"goal":"x"}');
});

test("[UX03-R2-ORG2] submit and lock replies from another organization, or another snapshot, are refused", async () => {
  for (const [name, bad] of [
    ["organization", { organization_id: OTHER_ORG }],
    ["snapshot", { target_source_snapshot_id: "55555555-5555-5555-5555-555555555555" }],
  ] as const) {
    cleanup();
    stubServer({ submit: manifestRecord({ status: "review_required", ...bad }) });
    renderLocale("en");
    await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
    cleanup();
    stubServer({
      manifest: manifestRecord({ status: "draft" }),
      submit: manifestRecord({ status: "review_required", ...bad }),
    });
    renderLocale("en");
    await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
    fireEvent.click(screen.getByTestId("submit-for-review-button"));
    await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
    expect(screen.getByTestId("manifest-status").getAttribute("data-manifest-status"), name).toBe(
      "draft",
    );
    cleanup();
    stubServer({
      lock: manifestRecord({ status: "locked", manifest_sha256: "a".repeat(64), ...bad }),
    });
    renderLocale("en");
    await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
    fireEvent.click(lockButton());
    await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
    expect(screen.getByTestId("manifest-status").getAttribute("data-manifest-status"), name).toBe(
      "review_required",
    );
  }
});

// The exact bodies apps/api/app/target_connectors/agent_manifest_routes.py answers with.
const BACKEND_NOT_FOUND = { detail: "not_found" };
const BACKEND_LOCKED = {
  detail: `agent manifest ${MANIFEST_ID} is 'locked' -- content can only be edited while draft or review_required (this task's own negative_acceptance)`,
};
const BACKEND_INCOMPLETE = {
  detail: `agent manifest ${MANIFEST_ID} is missing required field(s) ['agent_entrypoint_json'] -- this task's own negative_acceptance: missing critical evidence/model/runtime tuple blocks activation`,
};
const BACKEND_INVALID = { detail: "unknown AgentManifest field(s): ['bogus']" };

const SAFE = {
  notFound: {
    en: "This manifest no longer exists, or you do not have access to it. Go back and choose another one.",
    ar: "لم يعد هذا البيان موجودًا، أو ليست لديك صلاحية الوصول إليه. ارجع واختر بيانًا آخر.",
  },
  conflict: {
    en: "The manifest is not in a state that allows this: it may be locked, changed elsewhere, or missing a required part. Reload to see the current version; your edits stay in the editor until you do.",
    ar: "البيان ليس في حالة تسمح بذلك: قد يكون مقفلًا أو تم تغييره من مكان آخر أو ينقصه جزء مطلوب. أعد التحميل لرؤية النسخة الحالية؛ تبقى تعديلاتك في المحرر إلى أن تفعل ذلك.",
  },
  invalid: {
    en: "The server rejected some of what was entered. Check the values you changed, then try again.",
    ar: "رفض الخادم بعض ما تم إدخاله. تحقق من القيم التي غيّرتها ثم حاول مرة أخرى.",
  },
};

function expectNoBackendLeak(text: string, locale: "en" | "ar") {
  expect(text).not.toMatch(/not_found/);
  expect(text).not.toContain(MANIFEST_ID);
  expect(text).not.toContain("negative_acceptance");
  expect(text).not.toContain("_json");
  expect(text).not.toContain("Check what you entered");
  if (locale === "ar") expect(text.replace(/[^A-Za-z]+/g, " ").trim()).toBe("");
}

describe.each(["en", "ar"] as const)("[UX03-R2-REAL-BODIES] %s", (locale) => {
  test("load 404 not_found: a localized 'no longer exists / no access' sentence, no code", async () => {
    stubFetch(() => jsonResponse(404, BACKEND_NOT_FOUND));
    renderLocale(locale);
    await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
    const text = screen.getByTestId("manifest-load-error").textContent!;
    expect(text).toContain(SAFE.notFound[locale]);
    expectNoBackendLeak(text, locale);
  });

  test("save 409 AgentManifestLockedError: the conflict sentence with reload, edits kept, no backend text", async () => {
    stubServer({});
    renderLocale(locale);
    await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
    stubFetch((url, method) => {
      if (method === "PATCH") return jsonResponse(409, BACKEND_LOCKED);
      return jsonResponse(404, BACKEND_NOT_FOUND);
    });
    edit("mandate_json", '{"goal":"keep"}');
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
    const text = screen.getByTestId("manifest-action-error").textContent!;
    expect(text).toContain(SAFE.conflict[locale]);
    expectNoBackendLeak(text, locale);
    expect(input("mandate_json").value).toBe('{"goal":"keep"}');
  });

  test("lock 409 AgentManifestIncompleteError: the same safe conflict sentence", async () => {
    stubServer({});
    renderLocale(locale);
    await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
    stubFetch(() => jsonResponse(409, BACKEND_INCOMPLETE));
    fireEvent.click(lockButton());
    await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
    const text = screen.getByTestId("manifest-action-error").textContent!;
    expect(text).toContain(SAFE.conflict[locale]);
    expectNoBackendLeak(text, locale);
  });

  test("save 400 validation: the validation sentence, never the backend text", async () => {
    stubServer({});
    renderLocale(locale);
    await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
    stubFetch(() => jsonResponse(400, BACKEND_INVALID));
    edit("mandate_json", '{"goal":"x"}');
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
    const text = screen.getByTestId("manifest-action-error").textContent!;
    expect(text).toContain(SAFE.invalid[locale]);
    expect(text).not.toContain("bogus");
    expectNoBackendLeak(text, locale);
  });
});

test("[UX03-R2-PARSE] parseManifest itself refuses a manifest that does not name its organization or snapshot", () => {
  for (const field of ["organization_id", "target_source_snapshot_id"]) {
    for (const bad of [undefined, null, 5, ""]) {
      const record = { ...manifestRecord(), [field]: bad } as Record<string, unknown>;
      if (bad === undefined) delete record[field];
      expect(parseManifest(record), `${field}=${String(bad)}`).toBeNull();
    }
  }
});

test("[UX03-R2-OTHER4XX] an unrecognised 4xx shows a generic sentence with the status, never the backend's words", async () => {
  stubFetch(() => jsonResponse(418, { detail: "teapot_short_and_stout internal detail" }));
  renderLocale("en");
  await waitFor(() => expect(screen.getByTestId("manifest-load-error")).toBeTruthy());
  const text = screen.getByTestId("manifest-load-error").textContent!;
  expect(text).toContain(
    "The request failed (418). Try again; if it keeps happening, contact your administrator.",
  );
  expect(text).not.toContain("teapot");
});

// FTF-UX03 round 3, point 2: every save / submit / lock failure is announced assertively -- the
// error Banner is role="alert" (never role="status" or a plain div), exactly once, inside the
// action-error wrapper, in both locales. A Banner demoted to info/warning or a plain div is RED.
describe.each([
  ["save refused 409", "save", { patchStatus: 409 }],
  ["save refused 500", "save", { patchStatus: 500 }],
  ["lock refused 409", "lock", { lockStatus: 409 }],
  ["lock refused 500", "lock", { lockStatus: 500 }],
  ["submit-for-review network failure", "submit", {}],
] as const)("[UX03-R3-R4] %s is announced as role=alert", (_name, action, partial) => {
  for (const locale of ["en", "ar"] as const) {
    test(`${locale}`, async () => {
      installServer({
        manifest: manifestRecord({ status: action === "submit" ? "draft" : "review_required" }),
        ...partial,
      });
      if (action === "submit") {
        const base = fetch as unknown as ReturnType<typeof vi.fn>;
        const inner = base.getMockImplementation()! as (
          url: string,
          init?: RequestInit,
        ) => Promise<unknown>;
        base.mockImplementation(async (url: string, init?: RequestInit) => {
          if (String(url).endsWith("/submit-for-review")) throw new TypeError("network down");
          return inner(url, init);
        });
      }
      render(
        createElement(ManifestReviewEditUI, {
          apiBaseUrl: API_BASE,
          organizationId: ORG_ID,
          projectId: PROJECT_ID,
          manifestId: MANIFEST_ID,
          storage: storage as unknown as Storage,
          locale,
        }),
      );
      await waitFor(() => expect(screen.getByTestId("manifest-status")).toBeTruthy());
      if (action === "save") {
        edit("agent_name", "renamed-agent");
        fireEvent.click(saveButton());
      } else if (action === "lock") {
        fireEvent.click(lockButton());
      } else {
        fireEvent.click(screen.getByTestId("submit-for-review-button"));
      }
      await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
      const wrapper = screen.getByTestId("manifest-action-error");
      const alerts = wrapper.querySelectorAll('[role="alert"]');
      expect(alerts).toHaveLength(1);
      expect(alerts[0]!.textContent!.trim().length).toBeGreaterThan(2);
      expect(wrapper.querySelector('[role="status"]')).toBeNull();
      expect(alerts[0]!.getAttribute("lang")).toBe(locale);
    });
  }
});
