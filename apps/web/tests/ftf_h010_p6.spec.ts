// @vitest-environment jsdom
//
// FTF-H010-P6 -- Model version panel (component/DOM level). Renders the real `ModelVersionPanel`
// against a route-table `fetch`. Real-router coverage is the real-API job
// (tests/integration/test_ftf_h010_p6_ui_real_api.py); real-Chromium coverage is
// ftf_h010_p6_browser.spec.ts.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  MODEL_ACTIONS,
  MODEL_ROLES,
  ModelVersionPanel,
  REASON_MESSAGES,
  newKey,
  parseActions,
  reasonText,
} from "../components/model-import/model_version_panel_ui";

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const VER = "33333333-3333-3333-3333-333333333333";
const PARENT = "44444444-4444-4444-4444-444444444444";
const ITEM = `${API}/api/v1/organizations/${ORG}/projects/${PROJECT}/model-versions/${VER}`;
const ROLES = `${ITEM}/roles`;
const ACTIONS = `${ITEM}/actions`;
const sha = (c: string) => `sha256:${c.repeat(64)}`;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function endpointItem(extra: Record<string, unknown> = {}) {
  return {
    id: VER,
    organization_id: ORG,
    project_id: PROJECT,
    version_number: 7,
    import_kind: "endpoint",
    trainable_artifact_id: null,
    weights_digest: null,
    endpoint_reference: null,
    base_model_revision: null,
    origin: "external_endpoint",
    lineage_completeness: "unknown",
    adapter_only: false,
    parent_model_version_id: null,
    external_provider_model_ref: "ft:gpt-4o-mini:acme:teacher-lora",
    observed_deployment_fingerprint: "fp_44709d6fcb",
    created_by: "seed",
    created_at: "2026-11-01T00:00:00+00:00",
    ...extra,
  };
}
const weightsItem = (extra: Record<string, unknown> = {}) =>
  endpointItem({
    import_kind: "full_weight",
    origin: "imported_weights",
    lineage_completeness: "partial",
    weights_digest: sha("a"),
    external_provider_model_ref: null,
    observed_deployment_fingerprint: null,
    ...extra,
  });

function role(r: string, extra: Record<string, unknown> = {}) {
  return {
    id: `role-${r}`,
    organization_id: ORG,
    project_id: PROJECT,
    model_version_id: VER,
    role: r,
    assigned_by: "user-admin",
    assigned_at: "2026-11-02T00:00:00+00:00",
    idempotency_key: "k",
    revoked_at: null,
    revoked_by: null,
    revoke_reason_code: null,
    ...extra,
  };
}

/** The row the server returns for an assign: the role and the key that were SENT. */
const echo =
  (status = 201) =>
  (init?: RequestInit): Reply => {
    const sent = JSON.parse(String(init?.body)) as { role: string; idempotency_key: string };
    return { status, body: role(sent.role, { idempotency_key: sent.idempotency_key }) };
  };

function decisions(over: Record<string, unknown> = {}) {
  const base: Record<string, unknown> = {};
  for (const a of MODEL_ACTIONS) base[a] = { allowed: true, code: null, plan: null };
  base.continue_tuning = {
    allowed: true,
    code: null,
    plan: {
      kind: "new_run",
      parent_model_version_id: VER,
      run_id: "r",
      start_step: 0,
      optimizer_state: "none",
      separate_cost: true,
    },
  };
  base.exact_resume = { allowed: false, code: "no_trusted_checkpoint", plan: null };
  base.request_qualification = { allowed: false, code: "prerequisite_not_available", plan: null };
  base.deploy_route = { allowed: false, code: "prerequisite_not_available", plan: null };
  base.retire = { allowed: false, code: "prerequisite_not_available", plan: null };
  base.retain_incumbent = { allowed: false, code: "not_current_production", plan: null };
  return {
    model_version_id: VER,
    origin: "imported_weights",
    validated: true,
    roles: [],
    actions: { ...base, ...over },
  };
}

type Reply = {
  status?: number;
  body?: unknown;
  malformed?: boolean;
  reject?: boolean;
  hang?: boolean;
};
type Routes = Record<string, Reply | ((init?: RequestInit) => Reply)>;
let calls: { method: string; url: string; body?: string; credentials?: RequestCredentials }[] = [];
let routes: Routes = {};

beforeEach(() => {
  calls = [];
  routes = {
    [`GET ${ITEM}`]: { body: weightsItem() },
    [`GET ${ROLES}`]: { body: [] },
    [`GET ${ACTIONS}`]: { body: decisions() },
  };
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({
      method,
      url,
      body: init?.body as string | undefined,
      credentials: init?.credentials,
    });
    const r = routes[`${method} ${url}`];
    const reply = typeof r === "function" ? r(init) : r;
    if (!reply)
      return Promise.resolve({
        ok: false,
        status: 404,
        json: async () => ({ detail: "not_found" }),
      });
    if (reply.reject) return Promise.reject(new TypeError("Failed to fetch"));
    if (reply.hang) return new Promise(() => undefined);
    const status = reply.status ?? 200;
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: async () => {
        if (reply.malformed) throw new SyntaxError("Unexpected token <");
        return reply.body;
      },
    });
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount(locale: "en" | "ar" = "en") {
  return render(
    createElement(ModelVersionPanel, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: PROJECT,
      modelVersionId: VER,
      locale,
    }),
  );
}
async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}
const posts = () => calls.filter((c) => c.method === "POST");
const actionsSection = () => screen.getByTestId("actions-section");

// ---- lineage --------------------------------------------------------------------------------

test("H010P6-L1: endpoint lineage: origin/completeness verbatim, parent and training data Unknown, no placeholder digest", async () => {
  routes[`GET ${ITEM}`] = { body: endpointItem() };
  mount();
  await waitFor(() => screen.getByTestId("lineage-facts"));
  expect(screen.getByTestId("lineage-origin").querySelector("bdi")!.textContent).toBe(
    "external_endpoint",
  );
  expect(screen.getByTestId("lineage-completeness").querySelector("bdi")!.textContent).toBe(
    "unknown",
  );
  expect(screen.getByTestId("lineage-parent").textContent).toBe("Unknown");
  expect(screen.getByTestId("lineage-training-data").textContent).toBe("Unknown");
  expect(screen.getByTestId("lineage-digest").textContent).toBe("No training artifact");
  // no digest-shaped text anywhere on the panel for an endpoint
  expect(screen.getByTestId("model-version-panel").textContent).not.toMatch(/[0-9a-f]{64}|sha256:/);
  const fp = screen.getByTestId("lineage-fingerprint");
  expect(fp.querySelector("bdi")!.textContent).toBe("fp_44709d6fcb");
  expect(screen.getByTestId("lineage-provider-ref").querySelector("bdi")!.textContent).toBe(
    "ft:gpt-4o-mini:acme:teacher-lora",
  );
  expect(screen.getByTestId("panel-version-label").textContent).toBe("7");
});

test("H010P6-L2: weights lineage shows the server's digest; no fingerprint row when absent; a parent id when present", async () => {
  routes[`GET ${ITEM}`] = { body: weightsItem({ parent_model_version_id: PARENT }) };
  mount();
  await waitFor(() => screen.getByTestId("lineage-facts"));
  expect(screen.getByTestId("lineage-digest").querySelector("bdi")!.textContent).toBe(sha("a"));
  expect(screen.queryByTestId("lineage-fingerprint")).toBeNull();
  expect(screen.getByTestId("lineage-parent").querySelector("bdi")!.textContent).toBe(PARENT);
  expect(screen.getByTestId("lineage-completeness").textContent).toContain("partial");
  expect(screen.getByTestId("lineage-training-data").textContent).toBe("Unknown");
});

test("H010P6-L3: an unknown origin value is shown verbatim as unrecognised, never mapped to a known one", async () => {
  routes[`GET ${ITEM}`] = { body: weightsItem({ origin: "imported_magic", weights_digest: null }) };
  mount();
  await waitFor(() => screen.getByTestId("lineage-facts"));
  expect(screen.getByTestId("lineage-origin").textContent).toBe(
    "Unrecognised value (imported_magic)",
  );
  expect(screen.getByTestId("lineage-digest").textContent).toBe("Unknown");
});

for (const [label, reply] of [
  ["404 not_found", { status: 404, body: { detail: "not_found" } }],
  ["malformed json 200", { malformed: true }],
  ["{} 200", { body: {} }],
  ["another version's id", { body: weightsItem({ id: PARENT }) }],
  ["network failure", { reject: true }],
] as [string, Reply][]) {
  test(`H010P6-L4: lineage load ${label} -> one announced error with a recovery step, no facts`, async () => {
    routes[`GET ${ITEM}`] = reply;
    mount();
    await waitFor(() => screen.getByTestId("lineage-error"));
    const alert = screen.getByTestId("lineage-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(screen.getByTestId("lineage-error-recovery").textContent).not.toBe("");
    expect(screen.queryByTestId("lineage-facts")).toBeNull();
    if (label.startsWith("404")) expect(alert.textContent).toContain("(not_found)");
  });
}

test("H010P6-L5: loading is announced (role=status, aria-busy) for each section", async () => {
  routes[`GET ${ITEM}`] = { hang: true };
  routes[`GET ${ROLES}`] = { hang: true };
  routes[`GET ${ACTIONS}`] = { hang: true };
  mount();
  const busy = screen.getAllByRole("status").filter((n) => n.getAttribute("aria-busy") === "true");
  expect(busy.length).toBe(3);
});

// ---- actions --------------------------------------------------------------------------------

test("H010P6-A1: every action row reflects the server decision; denials carry the localized reason and code", async () => {
  mount();
  await waitFor(() => screen.getByTestId("decision-evaluate"));
  expect(screen.getByTestId("decision-evaluate").dataset.allowed).toBe("true");
  expect(screen.getByTestId("decision-evaluate").textContent).toContain("Allowed");
  expect(screen.getByTestId("decision-exact_resume").dataset.allowed).toBe("false");
  expect(screen.getByTestId("decision-exact_resume").textContent).toContain("Not available");
  expect(screen.getByTestId("reason-exact_resume").textContent).toBe(
    "There is no trusted checkpoint to resume from. (no_trusted_checkpoint)",
  );
  expect(screen.getByTestId("plan-continue_tuning").textContent).toContain("step 0");
  for (const a of MODEL_ACTIONS) expect(screen.getByTestId(`decision-${a}`)).toBeTruthy();
});

test("H010P6-A2: 'Allowed' is never shown when the server denies (endpoint_not_trainable)", async () => {
  routes[`GET ${ACTIONS}`] = {
    body: decisions({
      continue_tuning: { allowed: false, code: "endpoint_not_trainable", plan: null },
    }),
  };
  mount();
  await waitFor(() => screen.getByTestId("decision-continue_tuning"));
  const cell = screen.getByTestId("decision-continue_tuning");
  expect(cell.dataset.allowed).toBe("false");
  expect(cell.textContent).not.toContain("Allowed");
  expect(screen.getByTestId("reason-continue_tuning").textContent).toContain(
    "endpoint_not_trainable",
  );
  expect(screen.queryByTestId("plan-continue_tuning")).toBeNull();
});

test("H010P6-A3: an unknown reason renders Not available + the code, never Allowed (fail closed)", async () => {
  routes[`GET ${ACTIONS}`] = {
    body: decisions({ evaluate: { allowed: false, code: "a_reason_from_the_future", plan: null } }),
  };
  mount();
  await waitFor(() => screen.getByTestId("decision-evaluate"));
  expect(screen.getByTestId("decision-evaluate").dataset.allowed).toBe("false");
  expect(screen.getByTestId("reason-evaluate").textContent).toBe(
    "Reason not recognised. (a_reason_from_the_future)",
  );
});

test("H010P6-A4: inconsistent or missing decisions fail closed", () => {
  const rows = parseActions(
    decisions({
      evaluate: { allowed: true, code: "license_not_accepted", plan: null },
      compare: { allowed: "true", code: null, plan: null },
      continue_tuning: undefined,
      teleport: { allowed: true, code: null, plan: null },
    }),
    VER,
  )!;
  const by = Object.fromEntries(rows.map((r) => [r.action, r]));
  expect(by.evaluate!.allowed).toBe(false);
  expect(by.evaluate!.code).toBe("license_not_accepted");
  expect(by.compare!.allowed).toBe(false);
  expect(by.continue_tuning!.allowed).toBe(false);
  expect(by.teleport!.allowed).toBe(false);
  expect(by.teleport!.code).toBe("unknown_action");
  expect(parseActions({ ...decisions(), model_version_id: PARENT }, VER)).toBeNull();
  expect(parseActions({ model_version_id: VER }, VER)).toBeNull();
});

test("H010P6-A5: the actions section renders no button or link for any action (no placeholder controls)", async () => {
  mount();
  await waitFor(() => screen.getByTestId("decision-evaluate"));
  const section = actionsSection();
  const table = section.querySelector("table")!;
  expect(within(table).queryAllByRole("button")).toEqual([]);
  expect(within(section).queryAllByRole("link")).toEqual([]);
  // the only button allowed is a column sort control, and DataTable has none here
  expect(within(section).queryAllByRole("button")).toEqual([]);
});

test("H010P6-A6: an exact_resume plan shows the checkpoint step, a new_run shows step 0", async () => {
  routes[`GET ${ACTIONS}`] = {
    body: decisions({
      exact_resume: {
        allowed: true,
        code: null,
        plan: {
          kind: "exact_resume",
          checkpoint_id: "c",
          start_step: 1200,
          run_id: "r",
          same_run_lineage: true,
        },
      },
    }),
  };
  mount();
  await waitFor(() => screen.getByTestId("plan-exact_resume"));
  expect(screen.getByTestId("plan-exact_resume").textContent).toBe(
    "Exact resume from checkpoint step 1200",
  );
  expect(screen.getByTestId("plan-continue_tuning").textContent).toBe(
    "A new training run from step 0, with no optimizer state.",
  );
});

test("H010P6-A7: an import_not_validated version shows it on every action", async () => {
  const all: Record<string, unknown> = {};
  for (const a of MODEL_ACTIONS)
    all[a] = { allowed: false, code: "import_not_validated", plan: null };
  routes[`GET ${ACTIONS}`] = { body: { ...decisions(), validated: false, actions: all } };
  mount();
  await waitFor(() => screen.getByTestId("decision-evaluate"));
  for (const a of MODEL_ACTIONS)
    expect(screen.getByTestId(`reason-${a}`).textContent).toContain("import_not_validated");
});

test("H010P6-A8: actions load failure is announced, never an all-Allowed table", async () => {
  routes[`GET ${ACTIONS}`] = { status: 409, body: { detail: "model_facts_invalid" } };
  mount();
  await waitFor(() => screen.getByTestId("actions-error"));
  expect(screen.getByTestId("actions-error").textContent).toContain("model_facts_invalid");
  expect(screen.queryByTestId("decision-evaluate")).toBeNull();
});

// ---- roles ----------------------------------------------------------------------------------

test("H010P6-R1: roles list active and revoked rows; only active rows offer Revoke; empty state with an action", async () => {
  routes[`GET ${ROLES}`] = {
    body: [role("candidate"), role("baseline", { revoked_at: "2026-11-03T00:00:00+00:00" })],
  };
  mount();
  await waitFor(() => screen.getByTestId("role-state-role-candidate"));
  expect(screen.getByTestId("role-state-role-candidate").textContent).toBe("Active");
  expect(screen.getByTestId("role-state-role-baseline").textContent).toBe("Revoked");
  expect(screen.getByTestId("revoke-candidate")).toBeTruthy();
  expect(screen.queryByTestId("revoke-baseline")).toBeNull();
  cleanup();
  routes[`GET ${ROLES}`] = { body: [] };
  mount();
  await waitFor(() => screen.getByText("No roles yet"));
  expect(screen.getByText("Assign a role below.")).toBeTruthy();
});

test("H010P6-R2: roles list with a null item or another version's row is malformed (announced)", async () => {
  routes[`GET ${ROLES}`] = { body: [role("candidate"), null] };
  mount();
  await waitFor(() => screen.getByTestId("roles-error"));
  expect(screen.getByTestId("roles-error").getAttribute("role")).toBe("alert");
  cleanup();
  routes[`GET ${ROLES}`] = { body: [role("candidate", { model_version_id: PARENT })] };
  mount();
  await waitFor(() => screen.getByTestId("roles-error"));
});

test("H010P6-R3: the role select offers exactly the server's five roles to every reader", async () => {
  mount();
  await waitFor(() => screen.getByTestId("assign-open"));
  const select = screen.getByLabelText("Role to assign") as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toEqual([...MODEL_ROLES]);
});

async function openAssign(r: string) {
  await waitFor(() => screen.getByTestId("assign-open"));
  fireEvent.change(screen.getByLabelText("Role to assign"), { target: { value: r } });
  fireEvent.click(screen.getByTestId("assign-open"));
  await waitFor(() => screen.getByTestId("role-dialog"));
}

test("H010P6-R4: assign confirm dialog names the role and the version; cancel sends nothing", async () => {
  mount();
  await waitFor(() => screen.getByTestId("lineage-facts"));
  await openAssign("judge");
  const dialog = screen.getByTestId("role-dialog");
  expect(within(dialog).getByTestId("dialog-role").textContent).toBe("Judge");
  expect(within(dialog).getByTestId("dialog-version").textContent).toBe("7");
  expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
  fireEvent.click(screen.getByTestId("dialog-cancel"));
  await settle();
  expect(screen.queryByTestId("role-dialog")).toBeNull();
  expect(posts()).toEqual([]);
});

test("H010P6-R5: a confirmed assign POSTs exactly {role, idempotency_key=uuid} with credentials and shows the notice", async () => {
  routes[`POST ${ROLES}`] = echo();
  mount();
  await openAssign("candidate");
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await waitFor(() => screen.getByTestId("role-notice"));
  expect(posts().length).toBe(1);
  const sent = JSON.parse(posts()[0]!.body!) as Record<string, unknown>;
  expect(Object.keys(sent).sort()).toEqual(["idempotency_key", "role"]);
  expect(sent.role).toBe("candidate");
  expect(String(sent.idempotency_key)).toMatch(UUID_RE);
  expect(posts()[0]!.credentials).toBe("include");
  expect(screen.getByTestId("role-notice").getAttribute("role")).toBe("status");
  expect(screen.getByTestId("role-notice").textContent).toBe("Role assigned: Candidate");
  // the roles and actions are re-read after the write
  expect(calls.filter((c) => c.url === ROLES && c.method === "GET").length).toBeGreaterThanOrEqual(
    2,
  );
  expect(calls.filter((c) => c.url === ACTIONS).length).toBeGreaterThanOrEqual(2);
});

test("H010P6-R6: a lost assign response is retried with the SAME idempotency key; a new confirm uses a new one", async () => {
  let n = 0;
  routes[`POST ${ROLES}`] = (init) => (++n === 1 ? { reject: true } : echo(200)(init));
  mount();
  await openAssign("teacher");
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await waitFor(() => screen.getByTestId("role-retry"));
  expect(screen.getByTestId("role-error").getAttribute("role")).toBe("alert");
  screen.getByTestId("role-retry").focus();
  fireEvent.click(screen.getByTestId("role-retry"));
  await waitFor(() => screen.getByTestId("role-notice"));
  // the retry button unmounted while pending: focus is put back on the outcome, never <body>
  await waitFor(() =>
    expect(document.activeElement?.getAttribute("data-testid")).toBe("role-outcome"),
  );
  const [first, second] = posts().map((p) => JSON.parse(p.body!).idempotency_key as string);
  expect(second).toBe(first);
  routes[`POST ${ROLES}`] = { status: 409, body: { detail: "role_already_assigned" } };
  await openAssign("teacher");
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await waitFor(() => screen.getByTestId("role-error"));
  const third = JSON.parse(posts()[2]!.body!).idempotency_key as string;
  expect(third).not.toBe(first);
  expect(screen.queryByTestId("role-retry")).toBeNull();
});

test("H010P6-R7: a non-admin's current_production attempt shows the server's 403 (permission recovery)", async () => {
  routes[`POST ${ROLES}`] = { status: 403, body: { detail: "forbidden" } };
  mount();
  await openAssign("current_production");
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await waitFor(() => screen.getByTestId("role-error"));
  expect(screen.getByTestId("role-error").textContent).toBe(
    "The role was not assigned. Your role in this organization cannot make this change. (forbidden)",
  );
  expect(screen.getByTestId("role-error-recovery").textContent).toMatch(/access|permission/i);
});

test("H010P6-R8: incumbent_exists and self_judging_role_conflict are shown with their codes", async () => {
  for (const code of ["incumbent_exists", "self_judging_role_conflict"]) {
    routes[`POST ${ROLES}`] = { status: 409, body: { detail: code } };
    mount();
    await openAssign(code === "incumbent_exists" ? "current_production" : "judge");
    fireEvent.click(screen.getByTestId("dialog-confirm"));
    await waitFor(() => screen.getByTestId("role-error"));
    expect(screen.getByTestId("role-error").textContent).toContain(`(${code})`);
    expect(screen.getByTestId("role-error").textContent).toContain(reasonText(code, "en"));
    cleanup();
  }
});

test("H010P6-R9: revoke confirm -> POST .../roles/{role}/revoke with no body; a retry's 409 role_not_assigned re-fetches and shows the revoked state, not an error", async () => {
  routes[`GET ${ROLES}`] = { body: [role("candidate")] };
  let n = 0;
  routes[`POST ${ROLES}/candidate/revoke`] = () =>
    ++n === 1 ? { reject: true } : { status: 409, body: { detail: "role_not_assigned" } };
  mount();
  await waitFor(() => screen.getByTestId("revoke-candidate"));
  fireEvent.click(screen.getByTestId("revoke-candidate"));
  await waitFor(() => screen.getByTestId("role-dialog"));
  expect(screen.getByTestId("role-dialog").getAttribute("role")).toBe("alertdialog");
  expect(within(screen.getByTestId("role-dialog")).getByTestId("dialog-role").textContent).toBe(
    "Candidate",
  );
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await waitFor(() => screen.getByTestId("role-retry"));
  expect(posts()[0]!.body).toBeUndefined();
  routes[`GET ${ROLES}`] = {
    body: [role("candidate", { revoked_at: "2026-11-04T00:00:00+00:00" })],
  };
  const getsBefore = calls.filter((c) => c.url === ROLES && c.method === "GET").length;
  fireEvent.click(screen.getByTestId("role-retry"));
  await waitFor(() => screen.getByTestId("role-notice"));
  expect(screen.queryByTestId("role-error")).toBeNull();
  expect(screen.getByTestId("role-notice").textContent).toContain("already revoked");
  await waitFor(() =>
    expect(screen.getByTestId("role-state-role-candidate").textContent).toBe("Revoked"),
  );
  expect(calls.filter((c) => c.url === ROLES && c.method === "GET").length).toBeGreaterThan(
    getsBefore,
  );
});

test("H010P6-R10: focus lands on the outcome region after a confirmed write, never <body>", async () => {
  routes[`POST ${ROLES}`] = echo();
  mount();
  await openAssign("candidate");
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await waitFor(() => screen.getByTestId("role-notice"));
  await waitFor(() =>
    expect(document.activeElement?.getAttribute("data-testid")).toBe("role-outcome"),
  );
});

test("H010P6-R10b: focus moves to the outcome region as the dialog closes, while the write is pending", async () => {
  routes[`POST ${ROLES}`] = { hang: true };
  mount();
  await openAssign("candidate");
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await settle();
  expect(screen.queryByTestId("role-dialog")).toBeNull();
  await waitFor(() =>
    expect(document.activeElement?.getAttribute("data-testid")).toBe("role-outcome"),
  );
});

test("H010P6-R11: a double confirm sends one POST", async () => {
  routes[`POST ${ROLES}`] = { hang: true };
  mount();
  await openAssign("candidate");
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await settle();
  const opener = screen.getByTestId("assign-open") as HTMLButtonElement;
  expect(opener.disabled).toBe(true);
  fireEvent.click(opener);
  await settle();
  expect(screen.queryByTestId("role-dialog")).toBeNull();
  expect(posts().length).toBe(1);
});

// ---- language -------------------------------------------------------------------------------

test("H010P6-I1: ar sets lang/dir and renders Arabic for lineage, decisions and reasons", async () => {
  routes[`GET ${ITEM}`] = { body: endpointItem() };
  routes[`GET ${ACTIONS}`] = {
    body: decisions({
      continue_tuning: { allowed: false, code: "endpoint_not_trainable", plan: null },
    }),
  };
  mount("ar");
  await waitFor(() => screen.getByTestId("decision-continue_tuning"));
  const root = screen.getByTestId("model-version-panel");
  expect(root.getAttribute("lang")).toBe("ar");
  expect(root.getAttribute("dir")).toBe("rtl");
  expect(screen.getByTestId("lineage-parent").textContent).toBe("غير معروف");
  expect(screen.getByTestId("lineage-digest").textContent).toBe("لا توجد قطعة تدريب");
  expect(screen.getByTestId("decision-continue_tuning").textContent).toContain("غير متاح");
  const clone = root.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("bdi").forEach((b) => b.remove());
  expect(clone.textContent).not.toMatch(/[A-Za-z]{3,}/);
});

test("H010P6-I2: every reason code has en and ar text", () => {
  for (const [code, m] of Object.entries(REASON_MESSAGES)) {
    expect(m.en, code).not.toBe("");
    expect(m.ar, code).toMatch(/[؀-ۿ]/);
  }
});

test("H010P6-R12: the idempotency key is a v4 UUID with or without crypto.randomUUID (insecure context)", () => {
  expect(newKey()).toMatch(UUID_RE);
  const original = crypto.randomUUID;
  try {
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
    const a = newKey();
    const b = newKey();
    expect(a).toMatch(UUID_RE);
    expect(a).not.toBe(b);
  } finally {
    Object.defineProperty(crypto, "randomUUID", { value: original, configurable: true });
  }
});

// ---- review round 1: bound writes, every GET's malformed replies, strict parsing ---------------

for (const [label, reply] of [
  ["a non-JSON 200", { status: 200, malformed: true }],
  ["a {} 200", { status: 201, body: {} }],
  ["a row naming another role", { status: 201, body: role("judge") }],
  [
    "a row with another idempotency key",
    { status: 201, body: role("candidate", { idempotency_key: "other" }) },
  ],
  ["a row that is already revoked", { status: 201, body: "REVOKED" }],
  ["another version's row", { status: 201, body: role("candidate", { model_version_id: PARENT }) }],
] as [string, Reply][]) {
  test(`H010P6-W1: an assign 2xx with ${label} is NOT success: announced unknown outcome, retry keeps the key`, async () => {
    routes[`POST ${ROLES}`] =
      reply.body === "REVOKED"
        ? (init) => {
            const r = echo()(init);
            (r.body as Record<string, unknown>).revoked_at = "2026-11-05T00:00:00+00:00";
            return r;
          }
        : reply;
    mount();
    await openAssign("candidate");
    fireEvent.click(screen.getByTestId("dialog-confirm"));
    await waitFor(() => screen.getByTestId("role-error"));
    expect(screen.queryByTestId("role-notice")).toBeNull();
    expect(screen.getByTestId("role-error").getAttribute("role")).toBe("alert");
    expect(screen.getByTestId("role-error").textContent).toContain("does not confirm");
    routes[`POST ${ROLES}`] = echo(200);
    fireEvent.click(screen.getByTestId("role-retry"));
    await waitFor(() => screen.getByTestId("role-notice"));
    const keys = posts().map((p) => JSON.parse(p.body!).idempotency_key as string);
    expect(keys[1]).toBe(keys[0]);
  });
}

for (const [label, body] of [
  ["a {} 200", {}],
  ["a row still active", role("candidate")],
  ["a row naming another role", role("baseline", { revoked_at: "2026-11-05T00:00:00+00:00" })],
] as [string, unknown][]) {
  test(`H010P6-W2: a revoke 200 with ${label} is NOT success`, async () => {
    routes[`GET ${ROLES}`] = { body: [role("candidate")] };
    routes[`POST ${ROLES}/candidate/revoke`] = { status: 200, body };
    mount();
    await waitFor(() => screen.getByTestId("revoke-candidate"));
    fireEvent.click(screen.getByTestId("revoke-candidate"));
    await waitFor(() => screen.getByTestId("role-dialog"));
    fireEvent.click(screen.getByTestId("dialog-confirm"));
    await waitFor(() => screen.getByTestId("role-error"));
    expect(screen.queryByTestId("role-notice")).toBeNull();
  });
}

test("H010P6-W3: a bound revoke 200 shows the revoked notice", async () => {
  routes[`GET ${ROLES}`] = { body: [role("candidate")] };
  routes[`POST ${ROLES}/candidate/revoke`] = {
    status: 200,
    body: role("candidate", { revoked_at: "2026-11-05T00:00:00+00:00" }),
  };
  mount();
  await waitFor(() => screen.getByTestId("revoke-candidate"));
  fireEvent.click(screen.getByTestId("revoke-candidate"));
  await waitFor(() => screen.getByTestId("role-dialog"));
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await waitFor(() => screen.getByTestId("role-notice"));
  expect(screen.getByTestId("role-notice").textContent).toBe("Role revoked: Candidate");
});

for (const section of ["roles", "actions"] as const) {
  for (const [label, reply] of [
    ["a rejected fetch", { reject: true }],
    ["a non-JSON 200", { malformed: true }],
    ["a {} 200", { body: {} }],
    ["a null item", { body: section === "roles" ? [null] : { ...decisions(), actions: null } }],
    ["a 500", { status: 500, body: {} }],
  ] as [string, Reply][]) {
    test(`H010P6-G1: ${section} GET ${label} -> one announced error with recovery, never data or an empty state`, async () => {
      routes[`GET ${section === "roles" ? ROLES : ACTIONS}`] = reply;
      mount();
      await waitFor(() => screen.getByTestId(`${section}-error`));
      expect(screen.getByTestId(`${section}-error`).getAttribute("role")).toBe("alert");
      expect(screen.getByTestId(`${section}-error-recovery`).textContent).not.toBe("");
      if (section === "roles") expect(screen.queryByText("No roles yet")).toBeNull();
      else expect(screen.queryByTestId("decision-evaluate")).toBeNull();
    });
  }
}

for (const [field, value] of [
  ["version_number", "7"],
  ["parent_model_version_id", 5],
  ["weights_digest", { a: 1 }],
  ["external_provider_model_ref", 1],
  ["observed_deployment_fingerprint", true],
  ["origin", null],
  ["lineage_completeness", 3],
] as [string, unknown][]) {
  test(`H010P6-P1: item ${field} of the wrong type is malformed, never rendered`, async () => {
    routes[`GET ${ITEM}`] = { body: endpointItem({ [field]: value }) };
    mount();
    await waitFor(() => screen.getByTestId("lineage-error"));
    expect(screen.queryByTestId("lineage-facts")).toBeNull();
  });
}

for (const [field, value] of [
  ["revoked_at", undefined],
  ["revoked_at", 0],
  ["assigned_by", null],
  ["assigned_at", 1],
  ["role", 2],
  ["id", null],
] as [string, unknown][]) {
  test(`H010P6-P2: role row ${field}=${String(value)} is malformed, never a role state`, async () => {
    const row: Record<string, unknown> = role("candidate");
    if (value === undefined) delete row[field];
    else row[field] = value;
    routes[`GET ${ROLES}`] = { body: [row] };
    mount();
    await waitFor(() => screen.getByTestId("roles-error"));
    expect(screen.queryByText("Revoked")).toBeNull();
    expect(screen.queryByText("Active")).toBeNull();
  });
}

test("H010P6-P3: an exact_resume 'allowed' at step 0 or without a plan is not shown as allowed", async () => {
  for (const plan of [
    { kind: "exact_resume", start_step: 0 },
    null,
    { kind: "new_run", start_step: 3 },
  ]) {
    routes[`GET ${ACTIONS}`] = {
      body: decisions({
        exact_resume: { allowed: true, code: null, plan },
        continue_tuning: { allowed: true, code: null, plan },
      }),
    };
    mount();
    await waitFor(() => screen.getByTestId("decision-exact_resume"));
    expect(screen.getByTestId("decision-exact_resume").dataset.allowed).toBe("false");
    expect(screen.getByTestId("decision-continue_tuning").dataset.allowed).toBe("false");
    expect(screen.getByTestId("actions-section").textContent).not.toMatch(/step 0\b(?!,)/);
    cleanup();
  }
});

test("H010P6-P4: a plan of the other kind (resume carrying a new run) is not shown as allowed", async () => {
  routes[`GET ${ACTIONS}`] = {
    body: decisions({
      exact_resume: { allowed: true, code: null, plan: { kind: "new_run", start_step: 0 } },
    }),
  };
  mount();
  await waitFor(() => screen.getByTestId("decision-exact_resume"));
  expect(screen.getByTestId("decision-exact_resume").dataset.allowed).toBe("false");
});
