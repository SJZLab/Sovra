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
import { afterEach, beforeEach, expect, test, vi } from "vitest";

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
          return jsonResponse(server.patchStatus, { detail: "manifest is locked" });
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
          return jsonResponse(server.lockStatus, { detail: "missing required field(s)" });
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
  expect(screen.getByTestId("evidence-type-ev-1").textContent).toBe("repo_symbol");
  expect(screen.getByTestId("evidence-location-ev-1").textContent).toBe("agent/main.py:10-24");
  expect(screen.getByTestId("evidence-commit-ev-1").textContent).toBe("abcdef012345");
  expect(screen.getByTestId("evidence-confidence-ev-1").textContent).toBe("confidence: 0.9");
  // dotted field_path attaches to its leading segment only
  const bindingEvidence = screen.getByTestId("field-evidence-current_model_binding_json");
  expect(bindingEvidence.querySelector('[data-testid="evidence-ev-2"]')).toBeTruthy();
  expect(bindingEvidence.querySelector('[data-testid="evidence-ev-1"]')).toBeNull();
  expect(screen.getByTestId("evidence-confidence-ev-2").textContent).toBe("confidence: n/a");
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
  expect(screen.getByTestId("manifest-load-error").textContent).toBe("evidence unavailable");
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
  expect(screen.getByTestId("field-state-runtime_dependencies_json").textContent).toBe("unsaved");
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
    expect(screen.getByTestId("field-state-agent_name").textContent).toBe("clean"),
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
  expect(screen.getByTestId("manifest-action-error").textContent).toBe("manifest is locked");
  expect(input("mandate_json").value).toBe('{"goal":"keep me"}');
  expect(storage.getItem(STORAGE_KEY)).toContain("keep me");
  expect(screen.getByTestId("field-state-mandate_json").textContent).toBe("unsaved");
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
  expect(screen.getByTestId("field-state-mandate_json").textContent).toBe("unsaved");
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
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("clean");
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
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("unsaved");
  fireEvent.click(saveButton());
  await waitFor(() => expect(callsOf("PATCH")).toHaveLength(1));
});

test("a draft based on an older server value is a conflict that blocks save and lock until resolved", async () => {
  storage.setItem(
    STORAGE_KEY,
    JSON.stringify({ agent_name: { text: "my-name", baseText: "older-server-name" } }),
  );
  await renderLoaded();
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("conflict");
  expect(screen.getByTestId("field-conflict-server-agent_name").textContent).toBe("support-agent");
  expect(screen.getByTestId("field-conflict-mine-agent_name").textContent).toBe("my-name");
  expect(saveButton().disabled).toBe(true);
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "agent_name: unresolved conflict",
  );

  // Keep mine rebases the draft; it is then ordinary unsaved work, never auto-saved.
  fireEvent.click(screen.getByTestId("keep-mine-agent_name"));
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("unsaved");
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
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("conflict");
  // the user keeps typing without choosing "keep mine" or "take server's" first
  edit("agent_name", "my-name-v2");
  expect(input("agent_name").value).toBe("my-name-v2");
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("conflict");
  expect(screen.getByTestId("field-conflict-mine-agent_name")).toBeTruthy();
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain(
    "agent_name: unresolved conflict",
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
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("clean");
  expect(lockButton().disabled).toBe(false);
  expect(storage.getItem(STORAGE_KEY)).toBeNull();
});

test("invalid JSON is flagged, blocks save and lock, and sends nothing", async () => {
  await renderLoaded();
  edit("mandate_json", "{not json");
  expect(screen.getByTestId("field-state-mandate_json").textContent).toBe("invalid");
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
    expect(screen.getByTestId("manifest-status").textContent).toBe("Status: locked"),
  );
  expect(screen.getByTestId("manifest-sha256")).toBeTruthy();
});

test("Lock is blocked while the manifest is still a draft", async () => {
  installServer({ manifest: manifestRecord({ status: "draft" }) });
  await renderLoaded();
  expect(lockButton().disabled).toBe(true);
  expect(screen.getByTestId("lock-blockers").textContent).toContain("submitted for review first");
  fireEvent.click(lockButton());
  expect(callsOf("POST")).toHaveLength(0);
});

test.each([
  ["agent_entrypoint_json", "agent_entrypoint_json: required before lock"],
  ["current_model_binding_json", "current_model_binding_json: required before lock"],
  ["runtime_dependencies_json", "runtime_dependencies_json: required before lock"],
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
    "current model role not set to an explicit role",
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
    "current model binding is unresolved",
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
    "mandate_json: unsaved changes",
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
    expect(screen.getByTestId("manifest-status").textContent).toBe("Status: review_required"),
  );
});

test("a backend lock refusal is shown and the manifest is not shown as locked", async () => {
  installServer({ lockStatus: 409 });
  await renderLoaded();
  fireEvent.click(lockButton());
  await waitFor(() => expect(screen.getByTestId("manifest-action-error")).toBeTruthy());
  expect(screen.getByTestId("manifest-action-error").textContent).toBe("missing required field(s)");
  expect(screen.getByTestId("manifest-status").textContent).toBe("Status: review_required");
});

test("a locked manifest is read-only: edits are ignored and every action is disabled", async () => {
  installServer({
    manifest: manifestRecord({ status: "locked", manifest_sha256: "b".repeat(64) }),
  });
  await renderLoaded();
  expect(screen.getByTestId("manifest-read-only")).toBeTruthy();
  expect(input("agent_name").readOnly).toBe(true);
  edit("agent_name", "tamper");
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("clean");
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
  expect(screen.getByTestId("field-state-agent_name").textContent).toBe("unsaved");
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
  expect(screen.getByTestId("manifest-load-error").textContent).toBe("not_found");
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
  expect(screen.getByTestId("manifest-load-error").textContent).toBe("network error: offline");
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
  expect(screen.getByTestId("field-state-runtime_dependencies_json").textContent).toBe("unsaved");
  fireEvent.click(saveButton());
  await waitFor(() => expect(callsOf("PATCH")).toHaveLength(1));
  const body = callsOf("PATCH")[0]!.body as Record<string, unknown>;
  expect("runtime_dependencies_json" in body).toBe(true);
  expect(body.runtime_dependencies_json).toBeNull();
});
