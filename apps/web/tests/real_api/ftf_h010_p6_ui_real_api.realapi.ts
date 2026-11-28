// @vitest-environment jsdom
//
// FTF-H010-P6 UI -- the real model version panel against the REAL FTF-1001 item route and the REAL
// FTF-H010-P4 roles / revoke / actions routes on PostgreSQL 16 as sovra_app. Nothing about the
// server is mocked. Test doubles, all disclosed: (1) the transport wrapper below adds the
// test-principal header in place of the OIDC session cookie, can drop ONE POST response after the
// server has processed it (a lost response), and for R4 holds two POSTs until both are issued so
// they reach the server together; (2) `/__harness/revoke-license` stands in for FTF-0305's
// revocation and is never product behaviour.
//
// Run by tests/integration/test_ftf_h010_p6_ui_real_api.py, which seeds the data, passes it in the
// environment and verifies the database independently; not part of the default vitest include.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ModelVersionPanel } from "../../components/model-import/model_version_panel_ui";

const ENV = (globalThis as unknown as { process: { env: Record<string, string | undefined> } })
  .process.env;
function required(name: string): string {
  const value = ENV[name];
  if (!value)
    throw new Error(
      `${name} is required (run via tests/integration/test_ftf_h010_p6_ui_real_api.py)`,
    );
  return value;
}

interface Seeds {
  org1: string;
  org2: string;
  projects: Record<string, string>;
  foreign_project: string;
  versions: Record<string, string>;
  r3_artifact: string;
  r1_ref: string;
}
const API = required("FTF_H010P6_UI_API_BASE");
const SEEDS = JSON.parse(required("FTF_H010P6_UI_SEEDS")) as Seeds;
const V = SEEDS.versions;
const realFetch = globalThis.fetch;

let principal = "organization_admin";
let principalByVersion: Record<string, string> = {};
let dropNextPost = false;
let barrier: { waiting: (() => void)[]; size: number } | null = null;

const versionUrl = (project: string, version: string) =>
  `${API}/api/v1/organizations/${SEEDS.org1}/projects/${project}/model-versions/${version}`;

beforeEach(() => {
  principal = "organization_admin";
  principalByVersion = {};
  dropNextPost = false;
  barrier = null;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const headers = new Headers(init.headers);
    const owner = Object.keys(principalByVersion).find((v) => url.includes(v));
    headers.set("X-Test-Principal", owner ? principalByVersion[owner]! : principal);
    if (method === "POST" && barrier) {
      const b = barrier;
      await new Promise<void>((release) => {
        b.waiting.push(release);
        if (b.waiting.length === b.size) b.waiting.forEach((r) => r());
      });
    }
    const response = await realFetch(url, { ...init, headers });
    if (method === "POST" && dropNextPost) {
      dropNextPost = false; // the server answered and recorded; the browser never sees the answer
      throw new TypeError("fetch failed: injected connection loss after the server answered");
    }
    return response;
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount(version: string, project = SEEDS.projects.p1!) {
  return render(
    createElement(ModelVersionPanel, {
      apiBaseUrl: API,
      organizationId: SEEDS.org1,
      projectId: project,
      modelVersionId: version,
    }),
  );
}

async function ready(scope: ReturnType<typeof within> | typeof screen = screen) {
  await waitFor(() => scope.getByTestId("lineage-facts"), { timeout: 15_000 });
  await waitFor(() => scope.getByTestId("decision-evaluate"), { timeout: 15_000 });
  await waitFor(() => scope.getByTestId("assign-open"), { timeout: 15_000 });
}

async function assign(role: string, scope: ReturnType<typeof within> | typeof screen = screen) {
  fireEvent.change(scope.getByLabelText("Role to assign"), { target: { value: role } });
  fireEvent.click(scope.getByTestId("assign-open"));
  await waitFor(() => screen.getByTestId("role-dialog"));
  fireEvent.click(screen.getByTestId("dialog-confirm"));
}

async function outcome(scope: ReturnType<typeof within> | typeof screen = screen) {
  await waitFor(
    () => {
      const el = scope.queryByTestId("role-notice") ?? scope.queryByTestId("role-error");
      expect(el).toBeTruthy();
    },
    { timeout: 15_000 },
  );
  return (scope.queryByTestId("role-notice") ?? scope.getByTestId("role-error")) as HTMLElement;
}

async function decision(action: string) {
  return {
    cell: screen.getByTestId(`decision-${action}`),
    reason: screen.queryByTestId(`reason-${action}`)?.textContent ?? "",
    plan: screen.queryByTestId(`plan-${action}`)?.textContent ?? "",
  };
}

test("R1 endpoint with a trainable-looking ref and a teacher role -> continue_tuning endpoint_not_trainable; lineage honest", async () => {
  mount(V.r1!);
  await ready();
  expect(screen.getByTestId("lineage-origin").textContent).toContain("external_endpoint");
  expect(screen.getByTestId("lineage-completeness").textContent).toContain("unknown");
  expect(screen.getByTestId("lineage-parent").textContent).toBe("Unknown");
  expect(screen.getByTestId("lineage-training-data").textContent).toBe("Unknown");
  expect(screen.getByTestId("lineage-digest").textContent).toBe("No training artifact");
  expect(screen.getByTestId("lineage-fingerprint").querySelector("bdi")!.textContent).toBe(
    "fp_r1_observed",
  );
  expect(screen.getByTestId("lineage-provider-ref").textContent).toBe(SEEDS.r1_ref);
  expect(screen.getByTestId("model-version-panel").textContent).not.toMatch(/sha256:/);
  await assign("teacher");
  expect((await outcome()).textContent).toBe("Role assigned: Teacher");
  await waitFor(() => screen.getByText("Active"), { timeout: 15_000 });
  await waitFor(() => screen.getByTestId("decision-continue_tuning"));
  const d = await decision("continue_tuning");
  expect(d.cell.dataset.allowed).toBe("false");
  expect(d.reason).toContain("(endpoint_not_trainable)");
  expect((await decision("exact_resume")).reason).toContain("(no_trusted_checkpoint)");
  expect((await decision("evaluate")).cell.dataset.allowed).toBe("true");
  expect(within(screen.getByTestId("actions-section")).queryAllByRole("button")).toEqual([]);
});

test("R2 imported adapter on its exact base -> continue_tuning allowed as a new run; exact_resume no_trusted_checkpoint", async () => {
  mount(V.r2!);
  await ready();
  expect(screen.getByTestId("lineage-parent").textContent).toBe(V.base);
  expect(screen.getByTestId("lineage-digest").textContent).toBe(`sha256:${"d".repeat(64)}`);
  expect(screen.getByTestId("lineage-completeness").textContent).toContain("partial");
  const ct = await decision("continue_tuning");
  expect(ct.cell.dataset.allowed).toBe("true");
  expect(ct.plan).toBe("A new training run from step 0, with no optimizer state.");
  const er = await decision("exact_resume");
  expect(er.cell.dataset.allowed).toBe("false");
  expect(er.reason).toContain("(no_trusted_checkpoint)");
});

test("R3 license revoked -> every use action not available (license_not_accepted)", async () => {
  mount(V.r3!);
  await ready();
  expect((await decision("continue_tuning")).cell.dataset.allowed).toBe("true");
  cleanup();
  const r = await realFetch(`${API}/__harness/revoke-license`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ artifact_id: SEEDS.r3_artifact }),
  });
  expect(r.status).toBe(200);
  mount(V.r3!);
  await ready();
  for (const a of [
    "evaluate",
    "compare",
    "continue_tuning",
    "exact_resume",
    "request_qualification",
    "retain_incumbent",
    "deploy_route",
  ]) {
    const d = await decision(a);
    expect(d.cell.dataset.allowed, a).toBe("false");
    expect(d.reason, a).toContain("(license_not_accepted)");
  }
  expect((await decision("retire")).cell.dataset.allowed).toBe("false");
  expect(screen.queryAllByText("Allowed").length).toBe(0);
});

test("R4 two admin panels assign current_production concurrently -> one succeeds, the other shows incumbent_exists", async () => {
  principalByVersion = { [V.r4a!]: "organization_admin", [V.r4b!]: "organization_admin_2" };
  const a = mount(V.r4a!, SEEDS.projects.p_r4!);
  const b = mount(V.r4b!, SEEDS.projects.p_r4!);
  const wa = within(a.container);
  const wb = within(b.container);
  await ready(wa);
  await ready(wb);
  barrier = { waiting: [], size: 2 };
  await assign("current_production", wa);
  await waitFor(() => expect(screen.queryByTestId("role-dialog")).toBeNull());
  await assign("current_production", wb);
  const [oa, ob] = [await outcome(wa), await outcome(wb)];
  const texts = [oa.textContent ?? "", ob.textContent ?? ""];
  expect(texts.filter((t) => t === "Role assigned: Current production").length).toBe(1);
  expect(texts.filter((t) => t.includes("(incumbent_exists)")).length).toBe(1);
  const loser = oa.getAttribute("data-testid") === "role-error" ? oa : ob;
  expect(loser.getAttribute("role")).toBe("alert");
});

test("R5 judge on a candidate -> self_judging_role_conflict", async () => {
  mount(V.r5!, SEEDS.projects.p_r5!);
  await ready();
  await assign("candidate");
  expect((await outcome()).textContent).toBe("Role assigned: Candidate");
  await waitFor(() =>
    expect(screen.getByTestId("assign-open").hasAttribute("disabled")).toBe(false),
  );
  await assign("judge");
  await waitFor(() => screen.getByTestId("role-error"), { timeout: 15_000 });
  expect(screen.getByTestId("role-error").textContent).toContain("(self_judging_role_conflict)");
});

test("R6 ml_engineer assigning judge -> the server's 403 is shown", async () => {
  principal = "ml_engineer";
  mount(V.r6!);
  await ready();
  await assign("judge");
  await waitFor(() => screen.getByTestId("role-error"), { timeout: 15_000 });
  expect(screen.getByTestId("role-error").textContent).toContain("(forbidden)");
  expect(screen.getByTestId("role-error-recovery").textContent).not.toBe("");
});

test("R7 an unvalidated FTF-1001 synchronous row -> import_not_validated everywhere", async () => {
  mount(V.r7!);
  await ready();
  for (const a of [
    "evaluate",
    "compare",
    "continue_tuning",
    "exact_resume",
    "request_qualification",
    "retain_incumbent",
    "deploy_route",
    "retire",
  ]) {
    const d = await decision(a);
    expect(d.cell.dataset.allowed, a).toBe("false");
    expect(d.reason, a).toContain("(import_not_validated)");
  }
  await assign("candidate");
  await waitFor(() => screen.getByTestId("role-error"), { timeout: 15_000 });
  expect(screen.getByTestId("role-error").textContent).toContain("(import_not_validated)");
});

test("R8 a revoke whose response is lost, then Try again -> the revoked state, not an error", async () => {
  const seeded = await realFetch(`${versionUrl(SEEDS.projects.p1!, V.r8!)}/roles`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Test-Principal": "organization_admin" },
    body: JSON.stringify({ role: "candidate", idempotency_key: `seed-r8-${Date.now()}` }),
  });
  expect(seeded.status).toBe(201);
  mount(V.r8!);
  await ready();
  await waitFor(() => screen.getByTestId("revoke-candidate"), { timeout: 15_000 });
  fireEvent.click(screen.getByTestId("revoke-candidate"));
  await waitFor(() => screen.getByTestId("role-dialog"));
  dropNextPost = true;
  fireEvent.click(screen.getByTestId("dialog-confirm"));
  await waitFor(() => screen.getByTestId("role-retry"), { timeout: 15_000 });
  fireEvent.click(screen.getByTestId("role-retry"));
  await waitFor(() => screen.getByTestId("role-notice"), { timeout: 15_000 });
  expect(screen.queryByTestId("role-error")).toBeNull();
  expect(screen.getByTestId("role-notice").textContent).toContain("already revoked");
  await waitFor(() => screen.getByText("Revoked"), { timeout: 15_000 });
  expect(screen.queryByTestId("revoke-candidate")).toBeNull();
});

test("R9 a foreign version -> not_found in every section, nothing shown as data", async () => {
  mount(V.foreign!);
  for (const id of ["lineage-error", "roles-error", "actions-error"]) {
    await waitFor(() => screen.getByTestId(id), { timeout: 15_000 });
    expect(screen.getByTestId(id).textContent, id).toContain("(not_found)");
  }
  expect(screen.queryByTestId("lineage-facts")).toBeNull();
  expect(screen.queryByTestId("decision-evaluate")).toBeNull();
});

test("R10 an assign whose response is lost, then Try again -> a 200 replay with the same key, one row", async () => {
  mount(V.r10!);
  await ready();
  dropNextPost = true;
  await assign("baseline");
  await waitFor(() => screen.getByTestId("role-retry"), { timeout: 15_000 });
  fireEvent.click(screen.getByTestId("role-retry"));
  await waitFor(() => screen.getByTestId("role-notice"), { timeout: 15_000 });
  expect(screen.getByTestId("role-notice").textContent).toBe("Role assigned: Baseline");
  await waitFor(() => expect(screen.getAllByText("Active").length).toBe(1), { timeout: 15_000 });
});
