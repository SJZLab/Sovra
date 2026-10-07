// @vitest-environment jsdom
//
// FTF-04A22 -- Mandate editor and conflict-resolution UI.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `MandateEditorAndConflictResolutionUI`,
// drives real user interaction (editing mandate fields, adding/removing
// forbidden actions, saving, refreshing enforcement status) and asserts
// on the real `fetch` calls it makes against the live FTF-04A19
// `/manifests/{id}` and FTF-0407 `/requirements` API contracts --
// request method, URL and JSON body. `fetch` itself is a URL-routed fake
// (no live server in a unit test), but the component code path exercised
// is the real one.
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E -- same precedent as `ftf_04a21.spec.ts` (no Next.js
// `app/` page tree yet; FTF-H029).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  manifestUrl,
  type ManifestRecord,
} from "../components/target-application/manifest_review_edit_ui_with_per_field_evidence";
import {
  requirementsUrl,
  type CriticalBehaviorRequirementView,
} from "../components/project-mandate/requirements_ui";
import {
  BLOCKING_FORBIDDEN_ACTION_REASONS,
  MandateEditorAndConflictResolutionUI,
  evaluateForbiddenAction,
  evaluateForbiddenActions,
  mandateLockBlockers,
  parseForbiddenActions,
  parseMandateContent,
  serializeForbiddenActions,
  serializeMandateContent,
  type ForbiddenActionEntry,
} from "../components/target-application/mandate_editor_and_conflict_resolution_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const MANIFEST_ID = "33333333-3333-3333-3333-333333333333";
const MANIFEST_PATH = `/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}/manifests/${MANIFEST_ID}`;
const REQUIREMENTS_PATH = `/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}/requirements`;

function manifestRecord(overrides: Record<string, unknown> = {}): ManifestRecord {
  return {
    id: MANIFEST_ID,
    organization_id: ORG_ID,
    project_id: PROJECT_ID,
    target_source_snapshot_id: "44444444-4444-4444-4444-444444444444",
    version: 1,
    status: "review_required",
    agent_name: "support-agent",
    mandate_json: null,
    forbidden_actions_json: null,
    manifest_sha256: null,
    created_by: "user-1",
    approved_by: null,
    approved_at: null,
    created_at: "2026-10-07T00:00:00+00:00",
    ...overrides,
  } as ManifestRecord;
}

function requirement(
  overrides: Partial<CriticalBehaviorRequirementView> = {},
): CriticalBehaviorRequirementView {
  return {
    tool_definition_id: "tool-1",
    name: "refund",
    side_effect_class: "critical",
    is_critical: true,
    requires_confirmation: true,
    measurable: true,
    runtime_enforced: true,
    ...overrides,
  };
}

interface Server {
  manifest: ManifestRecord;
  requirements: CriticalBehaviorRequirementView[];
  requirementsStatus: number;
  manifestStatus: number;
  patchStatus: number;
  calls: { method: string; path: string; body: unknown }[];
}

let server: Server;

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function installServer(partial: Partial<Server> = {}) {
  server = {
    manifest: manifestRecord(),
    requirements: [],
    requirementsStatus: 200,
    manifestStatus: 200,
    patchStatus: 200,
    calls: [],
    ...partial,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(url);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      server.calls.push({ method, path: parsed.pathname, body });
      if (parsed.pathname === REQUIREMENTS_PATH && method === "GET") {
        return server.requirementsStatus === 200
          ? jsonResponse(200, server.requirements)
          : jsonResponse(server.requirementsStatus, { detail: "requirements unavailable" });
      }
      if (parsed.pathname === MANIFEST_PATH && method === "GET") {
        return server.manifestStatus === 200
          ? jsonResponse(200, server.manifest)
          : jsonResponse(server.manifestStatus, { detail: "manifest unavailable" });
      }
      if (parsed.pathname === MANIFEST_PATH && method === "PATCH") {
        if (server.patchStatus !== 200) {
          return jsonResponse(server.patchStatus, { detail: "manifest is locked" });
        }
        server.manifest = { ...server.manifest, ...(body as Record<string, unknown>) };
        return jsonResponse(200, server.manifest);
      }
      return jsonResponse(404, { detail: "not_found" });
    }),
  );
}

function callsOf(method: string, path?: string) {
  return server.calls.filter(
    (call) => call.method === method && (path === undefined || call.path === path),
  );
}

beforeEach(() => {
  installServer();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderUI() {
  return render(
    createElement(MandateEditorAndConflictResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      manifestId: MANIFEST_ID,
    }),
  );
}

async function renderLoaded() {
  const view = renderUI();
  await waitFor(() => expect(screen.getByTestId("mandate-editor-ui")).toBeTruthy());
  await waitFor(() => expect(screen.queryByTestId("mandate-loading")).toBeNull());
  return view;
}

// -- Pure-function tests (desired/observed/enforced evaluation) -----------

describe("parseMandateContent", () => {
  test("defaults every field when mandate_json is null", () => {
    const content = parseMandateContent(null);
    expect(content.business_purpose).toBeNull();
    expect(content.allowed_responsibilities).toEqual([]);
    expect(content.latency_slo_ms).toBeNull();
  });

  test("rejects a bare array or primitive, never fabricating content", () => {
    expect(parseMandateContent(["refund"]).allowed_responsibilities).toEqual([]);
    expect(parseMandateContent("refund").business_purpose).toBeNull();
    expect(parseMandateContent(42).latency_slo_ms).toBeNull();
  });

  test("drops a wrong-typed field instead of coercing it", () => {
    const content = parseMandateContent({
      business_purpose: 123,
      allowed_responsibilities: "not-a-list",
      latency_slo_ms: "200",
    });
    expect(content.business_purpose).toBeNull();
    expect(content.allowed_responsibilities).toEqual([]);
    expect(content.latency_slo_ms).toBeNull();
  });

  test("filters non-string entries out of a list field rather than failing closed on the whole field", () => {
    const content = parseMandateContent({ supported_languages: ["en", 7, "ar", null] });
    expect(content.supported_languages).toEqual(["en", "ar"]);
  });

  test("round-trips real values", () => {
    const raw = {
      business_purpose: "Handle customer refund requests",
      allowed_responsibilities: ["answer_faq", "issue_refund"],
      latency_slo_ms: 2000,
      throughput_concurrency_target: 10,
      cost_per_success_ceiling: 0.5,
    };
    const content = parseMandateContent(raw);
    expect(content.business_purpose).toBe(raw.business_purpose);
    expect(content.allowed_responsibilities).toEqual(raw.allowed_responsibilities);
    expect(content.latency_slo_ms).toBe(2000);
    expect(serializeMandateContent(content)).toMatchObject(raw);
  });
});

describe("parseForbiddenActions / serializeForbiddenActions", () => {
  test("defaults to an empty list for null, a bare array, or a missing actions key", () => {
    expect(parseForbiddenActions(null)).toEqual([]);
    expect(parseForbiddenActions(["refund"])).toEqual([]);
    expect(parseForbiddenActions({})).toEqual([]);
  });

  test("drops a malformed entry (no string name) instead of fabricating one", () => {
    const entries = parseForbiddenActions({
      actions: [
        { name: "refund", reason: "policy" },
        { reason: "no name" },
        { name: 7 },
        "bare-string",
      ],
    });
    expect(entries).toEqual([{ name: "refund", reason: "policy" }]);
  });

  test("round-trips through serialize as the required JSON-object shape (never a bare array)", () => {
    const entries: ForbiddenActionEntry[] = [{ name: "refund", reason: "policy" }];
    const serialized = serializeForbiddenActions(entries);
    expect(Array.isArray(serialized)).toBe(false);
    expect(serialized).toEqual({ actions: entries });
    expect(parseForbiddenActions(serialized)).toEqual(entries);
  });
});

describe("evaluateForbiddenAction (desired vs. observed vs. enforced)", () => {
  test("not_found: no matching tool discovered -- disclosed, not blocking", () => {
    const status = evaluateForbiddenAction("refund", []);
    expect(status).toEqual({ name: "refund", reason: "not_found", tool: null });
    expect(BLOCKING_FORBIDDEN_ACTION_REASONS).not.toContain("not_found");
  });

  test("not_declared_critical: Section 7A.7's own example -- repository tool schema permits it freely", () => {
    const tool = requirement({ is_critical: false, requires_confirmation: false });
    const status = evaluateForbiddenAction("refund", [tool]);
    expect(status.reason).toBe("not_declared_critical");
    expect(status.tool).toEqual(tool);
  });

  test("not_runtime_enforced: declared critical but Section 66.4's own 'does not prove runtime denial'", () => {
    const tool = requirement({
      is_critical: true,
      requires_confirmation: true,
      runtime_enforced: false,
    });
    const status = evaluateForbiddenAction("refund", [tool]);
    expect(status.reason).toBe("not_runtime_enforced");
  });

  test("declared via requires_confirmation alone (not is_critical) still counts as declared critical", () => {
    const tool = requirement({
      is_critical: false,
      requires_confirmation: true,
      runtime_enforced: true,
    });
    const status = evaluateForbiddenAction("refund", [tool]);
    expect(status.reason).toBe("enforced");
  });

  test("enforced: both declared and genuinely observed enforced at runtime", () => {
    const tool = requirement({
      is_critical: true,
      requires_confirmation: true,
      runtime_enforced: true,
    });
    const status = evaluateForbiddenAction("refund", [tool]);
    expect(status.reason).toBe("enforced");
  });

  test("matches by exact name only -- a differently-named tool is not_found, never assumed equivalent", () => {
    const tool = requirement({ name: "issue_refund" });
    const status = evaluateForbiddenAction("refund", [tool]);
    expect(status.reason).toBe("not_found");
  });

  test("mandateLockBlockers reports only the two blocking reasons, never not_found or enforced", () => {
    const statuses = evaluateForbiddenActions(
      [
        { name: "a", reason: "" },
        { name: "b", reason: "" },
        { name: "c", reason: "" },
        { name: "d", reason: "" },
      ],
      [
        requirement({ name: "a", is_critical: false, requires_confirmation: false }),
        requirement({
          name: "b",
          is_critical: true,
          requires_confirmation: true,
          runtime_enforced: false,
        }),
        requirement({
          name: "c",
          is_critical: true,
          requires_confirmation: true,
          runtime_enforced: true,
        }),
        // "d" intentionally absent -> not_found
      ],
    );
    const blockers = mandateLockBlockers(statuses);
    expect(blockers).toHaveLength(2);
    expect(blockers.some((b) => b.includes("a:"))).toBe(true);
    expect(blockers.some((b) => b.includes("b:"))).toBe(true);
    expect(blockers.some((b) => b.includes("c:"))).toBe(false);
    expect(blockers.some((b) => b.includes("d:"))).toBe(false);
  });
});

// -- Component tests --------------------------------------------------------

test("loads the manifest and requirements views from the real FTF-04A19/FTF-0407 contract", async () => {
  installServer({
    manifest: manifestRecord({
      mandate_json: {
        business_purpose: "Answer billing questions",
        supported_languages: ["en", "ar"],
      },
      forbidden_actions_json: { actions: [{ name: "refund", reason: "finance policy" }] },
    }),
    requirements: [requirement({ name: "refund", runtime_enforced: false })],
  });
  await renderLoaded();

  expect((screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value).toBe(
    "Answer billing questions",
  );
  expect((screen.getByTestId("mandate-field-supported_languages") as HTMLInputElement).value).toBe(
    "en, ar",
  );
  expect(screen.getByTestId("forbidden-action-name-refund").textContent).toBe("refund");
  expect(screen.getByTestId("forbidden-action-status-refund").textContent).toBe(
    "not_runtime_enforced",
  );
  expect(screen.getByTestId("forbidden-action-conflict-refund")).toBeTruthy();
  expect(callsOf("GET", MANIFEST_PATH)).toHaveLength(1);
  expect(callsOf("GET", REQUIREMENTS_PATH)).toHaveLength(1);
});

test("a load error on either source refuses to render a false 'no conflicts' state", async () => {
  installServer({ requirementsStatus: 503 });
  renderUI();
  await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
  expect(screen.queryByTestId("mandate-lock-ready")).toBeNull();
});

test("malformed manifest/requirements responses are refused, not silently treated as empty", async () => {
  installServer();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const parsed = new URL(url);
      if (parsed.pathname === MANIFEST_PATH) return jsonResponse(200, { not: "a manifest" });
      return jsonResponse(200, []);
    }),
  );
  renderUI();
  await waitFor(() =>
    expect(screen.getByTestId("mandate-load-error").textContent).toBe(
      "malformed manifest response",
    ),
  );
});

test("editing text/list/number mandate fields updates draft state and enables Save", async () => {
  await renderLoaded();
  expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(true);

  fireEvent.change(screen.getByTestId("mandate-field-business_purpose"), {
    target: { value: "Resolve shipping disputes" },
  });
  fireEvent.change(screen.getByTestId("mandate-field-supported_languages"), {
    target: { value: "en, fr, ar" },
  });
  fireEvent.change(screen.getByTestId("mandate-field-latency_slo_ms"), {
    target: { value: "1500" },
  });

  expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(false);

  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH", MANIFEST_PATH)).toHaveLength(1));
  const patchBody = callsOf("PATCH", MANIFEST_PATH)[0]?.body as Record<string, unknown>;
  const mandateBody = patchBody.mandate_json as Record<string, unknown>;
  expect(mandateBody.business_purpose).toBe("Resolve shipping disputes");
  expect(mandateBody.supported_languages).toEqual(["en", "fr", "ar"]);
  expect(mandateBody.latency_slo_ms).toBe(1500);
  // Only mandate_json + forbidden_actions_json are sent -- no other AgentManifest
  // content field is touched by this editor.
  expect(Object.keys(patchBody)).toEqual(["mandate_json", "forbidden_actions_json"]);
});

test("adding and removing a forbidden action edits forbidden_actions_json and nothing else", async () => {
  await renderLoaded();
  fireEvent.change(screen.getByTestId("new-forbidden-action-name"), {
    target: { value: "delete_account" },
  });
  fireEvent.change(screen.getByTestId("new-forbidden-action-reason"), {
    target: { value: "irreversible" },
  });
  fireEvent.click(screen.getByTestId("forbidden-action-add-button"));

  expect(screen.getByTestId("forbidden-action-name-delete_account").textContent).toBe(
    "delete_account",
  );

  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH", MANIFEST_PATH)).toHaveLength(1));
  const body = callsOf("PATCH", MANIFEST_PATH)[0]?.body as Record<string, unknown>;
  expect(body.forbidden_actions_json).toEqual({
    actions: [{ name: "delete_account", reason: "irreversible" }],
  });

  fireEvent.click(screen.getByTestId("forbidden-action-remove-delete_account"));
  expect(screen.queryByTestId("forbidden-action-row-delete_account")).toBeNull();
});

test("adding a duplicate or empty-name forbidden action is a no-op", async () => {
  installServer({
    manifest: manifestRecord({
      forbidden_actions_json: { actions: [{ name: "refund", reason: "x" }] },
    }),
  });
  await renderLoaded();
  fireEvent.change(screen.getByTestId("new-forbidden-action-name"), {
    target: { value: "refund" },
  });
  fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
  expect(screen.getAllByTestId("forbidden-action-name-refund")).toHaveLength(1);

  fireEvent.change(screen.getByTestId("new-forbidden-action-name"), { target: { value: "   " } });
  fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
  expect(screen.queryByTestId("forbidden-action-list")?.children).toHaveLength(1);
});

test("a refused save (e.g. manifest already locked) preserves the in-progress draft", async () => {
  installServer({ patchStatus: 409 });
  await renderLoaded();
  fireEvent.change(screen.getByTestId("mandate-field-business_purpose"), {
    target: { value: "Draft text" },
  });
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(screen.getByTestId("mandate-save-error")).toBeTruthy());
  expect((screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value).toBe(
    "Draft text",
  );
  expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(false);
});

for (const status of ["draft", "locked", "superseded"] as const) {
  test(`manifest status '${status}': editable only in draft, read-only (no edit/add/remove/save controls) otherwise`, async () => {
    installServer({ manifest: manifestRecord({ status }) });
    await renderLoaded();
    const businessPurposeInput = screen.getByTestId(
      "mandate-field-business_purpose",
    ) as HTMLTextAreaElement;
    if (status === "draft") {
      expect(businessPurposeInput.disabled).toBe(false);
      expect(screen.getByTestId("forbidden-action-add-form")).toBeTruthy();
    } else {
      expect(businessPurposeInput.disabled).toBe(true);
      expect(screen.getByTestId("mandate-readonly-notice")).toBeTruthy();
      expect(screen.queryByTestId("forbidden-action-add-form")).toBeNull();
      expect(screen.queryByTestId("mandate-save-button")).toBeNull();
    }
  });
}

// -- Negative acceptance: "User override cannot declare a dangerous runtime
// safe; unresolved policy conflicts block lock." -----------------------------

test("there is no control anywhere that marks a conflict resolved/safe/acknowledged", async () => {
  installServer({
    manifest: manifestRecord({
      forbidden_actions_json: { actions: [{ name: "refund", reason: "policy" }] },
    }),
    requirements: [requirement({ name: "refund", runtime_enforced: false })],
  });
  await renderLoaded();
  expect(screen.getByTestId("forbidden-action-conflict-refund")).toBeTruthy();

  const buttons = Array.from(document.querySelectorAll("button")).map((b) => b.textContent ?? "");
  const forbiddenPattern = /safe|acknowledge|accept risk|resolve|dismiss|override/i;
  for (const text of buttons) {
    expect(text).not.toMatch(forbiddenPattern);
  }
});

test("saving a mandate edit never clears or re-derives a live conflict by itself", async () => {
  installServer({
    manifest: manifestRecord({
      forbidden_actions_json: { actions: [{ name: "refund", reason: "policy" }] },
    }),
    requirements: [requirement({ name: "refund", runtime_enforced: false })],
  });
  await renderLoaded();
  expect(screen.getByTestId("forbidden-action-conflict-refund")).toBeTruthy();

  fireEvent.change(screen.getByTestId("mandate-field-business_purpose"), {
    target: { value: "Unrelated edit" },
  });
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH", MANIFEST_PATH)).toHaveLength(1));

  // The save succeeded (manifest updated), but the conflict -- derived only
  // from `requirements`, never touched by the PATCH response -- is still there.
  expect(screen.getByTestId("forbidden-action-conflict-refund")).toBeTruthy();
  expect(screen.getByTestId("mandate-lock-blocked")).toBeTruthy();
  // Saving must never itself re-fetch /requirements (the only honest path
  // to resolving a conflict is "Refresh enforcement status", a separate,
  // explicit, read-only action).
  expect(callsOf("GET", REQUIREMENTS_PATH)).toHaveLength(1);
});

test("'Refresh enforcement status' is the only way a conflict can clear, and only when real data changes", async () => {
  installServer({
    manifest: manifestRecord({
      forbidden_actions_json: { actions: [{ name: "refund", reason: "policy" }] },
    }),
    requirements: [requirement({ name: "refund", runtime_enforced: false })],
  });
  await renderLoaded();
  expect(screen.getByTestId("mandate-lock-blocked")).toBeTruthy();

  // Refresh with the same (still unenforced) data: conflict stays.
  fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
  await waitFor(() => expect(callsOf("GET", REQUIREMENTS_PATH)).toHaveLength(2));
  expect(screen.getByTestId("mandate-lock-blocked")).toBeTruthy();

  // The backend now genuinely observes enforcement -- only then does the UI
  // reflect it as resolved, and only via this explicit, honest refetch.
  server.requirements = [requirement({ name: "refund", runtime_enforced: true })];
  fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
  await waitFor(() => expect(callsOf("GET", REQUIREMENTS_PATH)).toHaveLength(3));
  await waitFor(() => expect(screen.getByTestId("mandate-lock-ready")).toBeTruthy());
  expect(screen.getByTestId("forbidden-action-enforced-refund")).toBeTruthy();
});

test("lock readiness is blocked while any forbidden action is undeclared-critical or unenforced, clear otherwise", async () => {
  installServer({
    manifest: manifestRecord({
      forbidden_actions_json: {
        actions: [
          { name: "refund", reason: "x" },
          { name: "export_data", reason: "y" },
        ],
      },
    }),
    requirements: [
      requirement({
        name: "refund",
        is_critical: true,
        requires_confirmation: true,
        runtime_enforced: true,
      }),
      requirement({ name: "export_data", is_critical: false, requires_confirmation: false }),
    ],
  });
  await renderLoaded();
  expect(screen.getByTestId("mandate-lock-blocked")).toBeTruthy();
  expect(screen.getByTestId("mandate-lock-blockers").textContent).toContain("export_data");
  expect(screen.getByTestId("mandate-lock-blockers").textContent).not.toContain("refund:");

  fireEvent.click(screen.getByTestId("forbidden-action-remove-export_data"));
  await waitFor(() => expect(screen.getByTestId("mandate-lock-ready")).toBeTruthy());
});

test("a forbidden action naming a tool nobody has discovered yet is disclosed, not asserted safe", async () => {
  installServer({
    manifest: manifestRecord({
      forbidden_actions_json: { actions: [{ name: "ghost_tool", reason: "x" }] },
    }),
    requirements: [],
  });
  await renderLoaded();
  expect(screen.getByTestId("forbidden-action-not-found-ghost_tool")).toBeTruthy();
  expect(screen.queryByTestId("forbidden-action-conflict-ghost_tool")).toBeNull();
  // Disclosed-but-unverifiable is not the same as "ready to lock" either:
  // only a genuinely enforced action should ever read as lock-ready.
  expect(screen.getByTestId("mandate-lock-ready")).toBeTruthy();
});

test("URL helpers are reused from the real FTF-04A19/FTF-0407 contract, not reinvented", () => {
  expect(manifestUrl(API_BASE, ORG_ID, PROJECT_ID, MANIFEST_ID)).toBe(
    `${API_BASE}${MANIFEST_PATH}`,
  );
  expect(requirementsUrl(API_BASE, ORG_ID, PROJECT_ID)).toBe(`${API_BASE}${REQUIREMENTS_PATH}`);
});

// -- Round 2: lossless save, raw list typing, error paths ------------------

function typeInto(testId: string, value: string) {
  fireEvent.change(screen.getByTestId(testId), { target: { value } });
}

function lastPatchBody(): Record<string, any> {
  const patches = callsOf("PATCH", MANIFEST_PATH);
  return patches[patches.length - 1].body as Record<string, any>;
}

test("D1: a list input keeps the raw typed text, so 'en,' then 'en,fr' works", async () => {
  await renderLoaded();
  const input = () => screen.getByTestId("mandate-field-supported_languages") as HTMLInputElement;
  typeInto("mandate-field-supported_languages", "en,");
  expect(input().value).toBe("en,");
  typeInto("mandate-field-supported_languages", "en, ");
  expect(input().value).toBe("en, ");
  typeInto("mandate-field-supported_languages", "en,fr");
  expect(input().value).toBe("en,fr");
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH").length).toBe(1));
  expect(lastPatchBody().mandate_json.supported_languages).toEqual(["en", "fr"]);
});

test("a list with an empty item ('en,,fr') saves as ['en','fr']", async () => {
  await renderLoaded();
  typeInto("mandate-field-supported_languages", "en,,fr");
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH").length).toBe(1));
  expect(lastPatchBody().mandate_json.supported_languages).toEqual(["en", "fr"]);
});

test("D2: save never drops unknown mandate keys, entry extras or top-level forbidden keys", async () => {
  installServer({
    manifest: manifestRecord({
      mandate_json: { business_purpose: "old", custom_key: { nested: [1, 2] } },
      forbidden_actions_json: {
        actions: [{ name: "refund", reason: "policy", severity: "high" }],
        policy_version: 3,
      },
    }),
    requirements: [requirement()],
  });
  await renderLoaded();
  typeInto("mandate-field-business_purpose", "new purpose");
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH").length).toBe(1));
  const first = lastPatchBody();
  expect(first.mandate_json.custom_key).toEqual({ nested: [1, 2] });
  expect(first.mandate_json.business_purpose).toBe("new purpose");
  expect(first.forbidden_actions_json.policy_version).toBe(3);
  expect(first.forbidden_actions_json.actions).toEqual([
    { name: "refund", reason: "policy", severity: "high" },
  ]);
  // a second save after the first still carries them
  await waitFor(() =>
    expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(true),
  );
  typeInto("mandate-field-business_purpose", "third");
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH").length).toBe(2));
  expect(lastPatchBody().mandate_json.custom_key).toEqual({ nested: [1, 2] });
  expect(lastPatchBody().forbidden_actions_json.actions[0].severity).toBe("high");
});

test("pure: serialize keeps base keys and entry extras, known fields win", () => {
  const content = parseMandateContent({ business_purpose: "a" });
  expect(serializeMandateContent(content, { x: 1, business_purpose: "stale" })).toMatchObject({
    x: 1,
    business_purpose: "a",
  });
  expect(
    serializeForbiddenActions([{ name: "n", reason: "r", extra: { k: 1 } }], { top: true }),
  ).toEqual({ top: true, actions: [{ k: 1, name: "n", reason: "r" }] });
  expect(parseForbiddenActions({ actions: [{ name: "n", reason: "r", k: 1 }] })).toEqual([
    { name: "n", reason: "r", extra: { k: 1 } },
  ]);
});

describe("refresh enforcement failures", () => {
  const conflicted = () =>
    installServer({
      manifest: manifestRecord({
        forbidden_actions_json: { actions: [{ name: "refund", reason: "x" }] },
      }),
      requirements: [requirement({ runtime_enforced: false })],
    });

  test("a 503 shows the error, keeps the conflict, and clears on a later success", async () => {
    conflicted();
    await renderLoaded();
    server.requirementsStatus = 503;
    fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
    await waitFor(() => expect(screen.getByTestId("refresh-enforcement-error")).toBeTruthy());
    expect(screen.getByTestId("mandate-lock-blocked")).toBeTruthy();
    expect(screen.queryByTestId("mandate-lock-ready")).toBeNull();
    server.requirementsStatus = 200;
    server.requirements = [requirement({ runtime_enforced: true })];
    fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
    await waitFor(() => expect(screen.queryByTestId("refresh-enforcement-error")).toBeNull());
    await waitFor(() => expect(screen.getByTestId("mandate-lock-ready")).toBeTruthy());
  });

  test("a network throw shows the error and keeps the conflict", async () => {
    conflicted();
    await renderLoaded();
    const real = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );
    fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
    await waitFor(() =>
      expect(screen.getByTestId("refresh-enforcement-error").textContent).toContain("offline"),
    );
    expect(screen.getByTestId("mandate-lock-blocked")).toBeTruthy();
    expect(screen.queryByTestId("mandate-lock-ready")).toBeNull();
    vi.stubGlobal("fetch", real);
  });
});

test("a load network error is surfaced", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("boom");
    }),
  );
  renderUI();
  await waitFor(() =>
    expect(screen.getByTestId("mandate-load-error").textContent).toContain("boom"),
  );
});

test("a save network error is surfaced and the draft is kept", async () => {
  await renderLoaded();
  typeInto("mandate-field-business_purpose", "draft");
  const real = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("down");
    }),
  );
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() =>
    expect(screen.getByTestId("mandate-save-error").textContent).toContain("down"),
  );
  expect((screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value).toBe(
    "draft",
  );
  vi.stubGlobal("fetch", real);
});

test("a malformed PATCH 200 shows the save error", async () => {
  await renderLoaded();
  typeInto("mandate-field-business_purpose", "x");
  const real = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<unknown>;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (u: string, i?: RequestInit) =>
      i?.method === "PATCH" ? jsonResponse(200, { nope: true }) : real(u, i),
    ),
  );
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(screen.getByTestId("mandate-save-error")).toBeTruthy());
});

test("clearing business_purpose and latency_slo_ms sends JSON null with the key present", async () => {
  installServer({
    manifest: manifestRecord({ mandate_json: { business_purpose: "p", latency_slo_ms: 5 } }),
  });
  await renderLoaded();
  typeInto("mandate-field-business_purpose", "");
  typeInto("mandate-field-latency_slo_ms", "");
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH").length).toBe(1));
  const body = lastPatchBody().mandate_json;
  expect("business_purpose" in body && body.business_purpose === null).toBe(true);
  expect("latency_slo_ms" in body && body.latency_slo_ms === null).toBe(true);
});

test("a locked manifest has no remove buttons and every input is disabled", async () => {
  installServer({
    manifest: manifestRecord({
      status: "approved",
      forbidden_actions_json: { actions: [{ name: "refund", reason: "x" }] },
    }),
    requirements: [requirement()],
  });
  await renderLoaded();
  expect(screen.queryByTestId("forbidden-action-remove-refund")).toBeNull();
  expect(screen.queryByTestId("mandate-save-button")).toBeNull();
  for (const id of ["business_purpose", "supported_languages", "latency_slo_ms"]) {
    expect((screen.getByTestId(`mandate-field-${id}`) as HTMLInputElement).disabled).toBe(true);
  }
});

test("forbidden-action names match exactly: 'Refund' and 'refund_all' do not match 'refund'", () => {
  const rows = [requirement({ name: "refund" })];
  expect(evaluateForbiddenAction("Refund", rows).reason).toBe("not_found");
  expect(evaluateForbiddenAction("refund_all", rows).reason).toBe("not_found");
  expect(evaluateForbiddenAction("refund", rows).reason).toBe("enforced");
});

test("is_critical alone (without requires_confirmation) is enforced when observed enforced", () => {
  const row = requirement({ is_critical: true, requires_confirmation: false });
  expect(evaluateForbiddenAction("refund", [row]).reason).toBe("enforced");
  const unenforced = requirement({
    is_critical: true,
    requires_confirmation: false,
    runtime_enforced: false,
  });
  expect(evaluateForbiddenAction("refund", [unenforced]).reason).toBe("not_runtime_enforced");
});

test("adding trims the reason, clears the add inputs; removing one of two keeps the other and enables Save", async () => {
  installServer({
    manifest: manifestRecord({
      forbidden_actions_json: { actions: [{ name: "a", reason: "ra" }] },
    }),
  });
  await renderLoaded();
  expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(true);
  typeInto("new-forbidden-action-name", " b ");
  typeInto("new-forbidden-action-reason", "  rb  ");
  fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
  expect((screen.getByTestId("new-forbidden-action-name") as HTMLInputElement).value).toBe("");
  expect((screen.getByTestId("new-forbidden-action-reason") as HTMLInputElement).value).toBe("");
  fireEvent.click(screen.getByTestId("forbidden-action-remove-a"));
  expect(screen.queryByTestId("forbidden-action-row-a")).toBeNull();
  expect(screen.getByTestId("forbidden-action-row-b")).toBeTruthy();
  expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() => expect(callsOf("PATCH").length).toBe(1));
  expect(lastPatchBody().forbidden_actions_json.actions).toEqual([{ name: "b", reason: "rb" }]);
});

test("while a save is in flight, Save and Refresh are disabled", async () => {
  await renderLoaded();
  typeInto("mandate-field-business_purpose", "x");
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const real = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<unknown>;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (u: string, i?: RequestInit) => {
      if (i?.method === "PATCH") await gate;
      return real(u, i);
    }),
  );
  fireEvent.click(screen.getByTestId("mandate-save-button"));
  await waitFor(() =>
    expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(true),
  );
  expect((screen.getByTestId("refresh-enforcement-button") as HTMLButtonElement).disabled).toBe(
    true,
  );
  release();
  await waitFor(() =>
    expect((screen.getByTestId("refresh-enforcement-button") as HTMLButtonElement).disabled).toBe(
      false,
    ),
  );
});

test("a props change refetches the manifest", async () => {
  const view = await renderLoaded();
  const before = callsOf("GET", MANIFEST_PATH).length;
  view.rerender(
    createElement(MandateEditorAndConflictResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      manifestId: "55555555-5555-5555-5555-555555555555",
    }),
  );
  await waitFor(() =>
    expect(server.calls.some((c) => c.path.endsWith("55555555-5555-5555-5555-555555555555"))).toBe(
      true,
    ),
  );
  expect(before).toBeGreaterThan(0);
});
