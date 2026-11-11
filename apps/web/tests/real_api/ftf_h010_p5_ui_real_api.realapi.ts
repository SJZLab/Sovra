// @vitest-environment jsdom
//
// FTF-H010-P5 UI (UI CHECKLIST f) -- the real import wizard against the REAL FTF-H010-P1/P2/P3
// routes on PostgreSQL 16 as sovra_app. Nothing about the server is mocked. Test doubles, all
// disclosed: (1) the transport wrapper below adds the test-principal header in place of the OIDC
// session cookie and can drop ONE POST response after the server has processed it (a lost
// response); (2) `/__harness/advance` is the STAND-IN for the absent FTF-0801/H016 dispatcher -- it
// calls the real certified claim/validate activities for one job, and is never product behaviour.
//
// Run by tests/integration/test_ftf_h010_p5_ui_real_api.py, which seeds the data, passes it in the
// environment and verifies the database independently; not part of the default vitest include.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ImportModelWizard } from "../../components/model-import/import_model_wizard_ui";

const ENV = (globalThis as unknown as { process: { env: Record<string, string | undefined> } })
  .process.env;
function required(name: string): string {
  const value = ENV[name];
  if (!value)
    throw new Error(
      `${name} is required (run via tests/integration/test_ftf_h010_p5_ui_real_api.py)`,
    );
  return value;
}

interface Seeds {
  org1: string;
  org2: string;
  project: string;
  foreign_project: string;
  conn_ok: string;
  conn_dead: string;
  base_version: string;
  artifacts: Record<string, string>;
  ref: string;
}
const API = required("FTF_H010P5_UI_API_BASE");
const SEEDS = JSON.parse(required("FTF_H010P5_UI_SEEDS")) as Seeds;
const PRINCIPALS = JSON.parse(required("FTF_H010P5_UI_PRINCIPALS")) as Record<string, string>;
const realFetch = globalThis.fetch;

let principal = PRINCIPALS.organization_admin!;
let dropNextPost = false;
const importsUrl = (project: string) =>
  `${API}/api/v1/organizations/${SEEDS.org1}/projects/${project}/model-imports`;

beforeEach(() => {
  principal = PRINCIPALS.organization_admin!;
  dropNextPost = false;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const headers = new Headers(init.headers);
    headers.set("X-Test-Principal", principal);
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

interface JobRow {
  id: string;
  requested_origin: string;
  state: string;
  failure_code: string | null;
  external_provider_model_ref: string | null;
  trainable_artifact_id: string | null;
}
async function listJobs(project = SEEDS.project): Promise<JobRow[]> {
  const response = await realFetch(importsUrl(project), {
    headers: { "X-Test-Principal": PRINCIPALS.organization_admin! },
  });
  return (await response.json()) as JobRow[];
}
async function advance(jobId: string, extra: Record<string, unknown> = {}) {
  const response = await realFetch(`${API}/__harness/advance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, ...extra }),
  });
  expect(response.status).toBe(200);
  return (await response.json()) as { state: string; failure_code: string | null };
}

function mount(role = "organization_admin", project = SEEDS.project) {
  principal = PRINCIPALS[role]!;
  return render(
    createElement(ImportModelWizard, {
      apiBaseUrl: API,
      organizationId: SEEDS.org1,
      projectId: project,
      role,
    }),
  );
}
const tick = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function chooseOrigin(label: RegExp) {
  await waitFor(() => screen.getByRole("radio", { name: label }));
  fireEvent.click(screen.getByRole("radio", { name: label }));
  fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }));
}
async function confirmAndStart() {
  await waitFor(() => screen.getByTestId("import-confirm"));
  fireEvent.click(screen.getByTestId("import-confirm"));
  fireEvent.click(screen.getByTestId("import-submit"));
}
async function startEndpoint(connectionId: string, ref: string) {
  await chooseOrigin(/Endpoint model/);
  await waitFor(() => expect(screen.getByLabelText(/Gateway connection/)).toBeTruthy());
  const select = screen.getByLabelText(/Gateway connection/) as HTMLSelectElement;
  await waitFor(() =>
    expect(Array.from(select.options).some((o) => o.value === connectionId)).toBe(true),
  );
  fireEvent.change(select, { target: { value: connectionId } });
  await waitFor(() => expect(screen.getByLabelText(/Model reference/)).toBeTruthy());
  fireEvent.change(screen.getByLabelText(/Model reference/), { target: { value: ref } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await waitFor(() => screen.getByTestId("import-summary"));
  expect(screen.getByTestId("import-expected-lineage").textContent).toBe("Unknown");
  await confirmAndStart();
}
async function startWeights(artifactId: string) {
  await chooseOrigin(/Full weights/);
  await waitFor(() => screen.getByLabelText(/Model artifact/));
  const select = screen.getByLabelText(/Model artifact/) as HTMLSelectElement;
  await waitFor(() =>
    expect(Array.from(select.options).some((o) => o.value === artifactId)).toBe(true),
  );
  fireEvent.change(select, { target: { value: artifactId } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await waitFor(() => screen.getByTestId("import-summary"));
  expect(screen.getByTestId("import-expected-lineage").textContent).toBe("Partial");
  await confirmAndStart();
}
async function startAdapter(artifactId: string, baseId: string) {
  await chooseOrigin(/Adapter and base/);
  await waitFor(() => screen.getByLabelText(/Adapter artifact/));
  const select = screen.getByLabelText(/Adapter artifact/) as HTMLSelectElement;
  await waitFor(() =>
    expect(Array.from(select.options).some((o) => o.value === artifactId)).toBe(true),
  );
  fireEvent.change(select, { target: { value: artifactId } });
  await waitFor(() => screen.getByLabelText(/Base model version/));
  const base = screen.getByLabelText(/Base model version/) as HTMLSelectElement;
  await waitFor(() => expect(Array.from(base.options).some((o) => o.value === baseId)).toBe(true));
  fireEvent.change(base, { target: { value: baseId } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await waitFor(() => screen.getByTestId("import-summary"));
  await confirmAndStart();
}
async function jobFor(match: (j: JobRow) => boolean): Promise<JobRow> {
  let found: JobRow | undefined;
  await waitFor(async () => {
    found = (await listJobs()).find(match);
    expect(found).toBeTruthy();
  });
  return found!;
}
const stateText = (id: string) => screen.getByTestId(`job-${id}`).textContent ?? "";
async function expectJobShows(id: string, state: string, text: RegExp) {
  await waitFor(
    () => {
      expect(screen.getByTestId(`job-${id}`).getAttribute("data-state")).toBe(state);
      expect(stateText(id)).toMatch(text);
    },
    { timeout: 40_000, interval: 250 },
  );
}

test("R1 real endpoint import: queued, R10 reload mid-queue restores it, validated -> succeeded, no digest", async () => {
  const ref = `${SEEDS.ref}-r1`;
  const view = mount();
  await startEndpoint(SEEDS.conn_ok, ref);
  await waitFor(() => screen.getByTestId("import-notice"));
  const job = await jobFor((j) => j.external_provider_model_ref === ref);
  expect(job.state).toBe("queued");
  await expectJobShows(job.id, "queued", /validation has not started/);
  expect(stateText(job.id)).not.toMatch(/Validated/);
  // R10 (reload mid-queue): a fresh mount restores the job from the server.
  view.unmount();
  mount();
  await expectJobShows(job.id, "queued", /validation has not started/);
  expect((await advance(job.id)).state).toBe("succeeded");
  await expectJobShows(job.id, "succeeded", /Validated/);
  expect(document.body.textContent).not.toMatch(/[0-9a-f]{64}/);
  expect(document.body.textContent).not.toContain("127.0.0.1:");
});

test("R2 real endpoint: a resolved-model echo that differs -> failed route_identity_unverified", async () => {
  const ref = `${SEEDS.ref}-r2`;
  mount();
  await startEndpoint(SEEDS.conn_ok, ref);
  const job = await jobFor((j) => j.external_provider_model_ref === ref);
  expect((await advance(job.id, { echo: "some-other-model" })).failure_code).toBe(
    "route_identity_unverified",
  );
  await expectJobShows(job.id, "failed", /did not match/);
});

test("R3 real endpoint: gateway unreachable -> blocked_external, never failed or success", async () => {
  const ref = `${SEEDS.ref}-r3`;
  mount();
  await startEndpoint(SEEDS.conn_dead, ref);
  const job = await jobFor((j) => j.external_provider_model_ref === ref);
  const out = await advance(job.id);
  expect([out.state, out.failure_code]).toEqual(["blocked_external", "gateway_unavailable"]);
  await expectJobShows(job.id, "blocked_external", /Blocked/);
  expect(stateText(job.id)).not.toMatch(/Validated|failed/i);
});

test("R4 real weights import: succeeded, lineage Partial", async () => {
  mount("ml_engineer");
  await startWeights(SEEDS.artifacts.r4!);
  const job = await jobFor((j) => j.trainable_artifact_id === SEEDS.artifacts.r4);
  expect((await advance(job.id)).state).toBe("succeeded");
  await expectJobShows(job.id, "succeeded", /Validated/);
});

test("R5 real weights import: digest mismatch -> failed digest_mismatch", async () => {
  mount("ml_engineer");
  await startWeights(SEEDS.artifacts.r5!);
  const job = await jobFor((j) => j.trainable_artifact_id === SEEDS.artifacts.r5);
  expect((await advance(job.id)).failure_code).toBe("digest_mismatch");
  await expectJobShows(job.id, "failed", /recorded digest/);
});

test("R6 real adapter import on a mismatched base -> adapter_base_mismatch", async () => {
  mount("ml_engineer");
  await startAdapter(SEEDS.artifacts.r6!, SEEDS.base_version);
  const job = await jobFor((j) => j.trainable_artifact_id === SEEDS.artifacts.r6);
  expect((await advance(job.id)).failure_code).toBe("adapter_base_mismatch");
  await expectJobShows(job.id, "failed", /does not match the chosen base/);
});

test("R7 real weights import: license revoked before validation -> license_not_accepted", async () => {
  mount("ml_engineer");
  await startWeights(SEEDS.artifacts.r7!);
  const job = await jobFor((j) => j.trainable_artifact_id === SEEDS.artifacts.r7);
  const revoked = await realFetch(`${API}/__harness/revoke-license`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ artifact_id: SEEDS.artifacts.r7 }),
  });
  expect(revoked.status).toBe(200);
  expect((await advance(job.id)).failure_code).toBe("license_not_accepted");
  await expectJobShows(job.id, "failed", /license has not been accepted/);
});

test("R8 viewer and evaluator are denied; ml_engineer on the endpoint branch needs an admin (server agrees)", async () => {
  for (const role of ["viewer", "evaluator"]) {
    cleanup();
    mount(role);
    await waitFor(() => screen.getByTestId("import-permission-denied"));
    expect(screen.queryByTestId("import-submit")).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
    const direct = await realFetch(importsUrl(SEEDS.project), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Test-Principal": PRINCIPALS[role]! },
      body: JSON.stringify({
        requested_origin: "imported_weights",
        trainable_artifact_id: SEEDS.artifacts.r4,
      }),
    });
    expect(direct.status).toBe(403);
  }
  cleanup();
  mount("ml_engineer");
  await chooseOrigin(/Endpoint model/);
  await waitFor(() => screen.getByTestId("endpoint-admin-required"));
  expect(screen.queryByRole("button", { name: /Review import/ })).toBeNull();
  const conns = await realFetch(`${API}/api/v1/organizations/${SEEDS.org1}/connections/litellm`, {
    headers: { "X-Test-Principal": PRINCIPALS.ml_engineer! },
  });
  expect(conns.status).toBe(403);
});

test("R9 a foreign project: not_found is shown and nothing is written", async () => {
  mount("ml_engineer", SEEDS.foreign_project);
  await startWeights(SEEDS.artifacts.r4!);
  const alert = await screen.findByTestId("import-submit-error");
  expect(alert.getAttribute("role")).toBe("alert");
  expect(alert.textContent).toMatch(/Not found/);
  expect(document.body.textContent).not.toMatch(/Queued/);
});

test("R11 the POST response is dropped: exactly one job exists and the wizard shows it", async () => {
  mount("ml_engineer");
  dropNextPost = true;
  await startWeights(SEEDS.artifacts.r11!);
  await waitFor(
    () => expect(screen.getByTestId("import-notice").textContent).toMatch(/was created/),
    {
      timeout: 15_000,
    },
  );
  await tick(500);
  const rows = (await listJobs()).filter((j) => j.trainable_artifact_id === SEEDS.artifacts.r11);
  expect(rows).toHaveLength(1);
  expect(screen.getByTestId(`job-${rows[0]!.id}`)).toBeTruthy();
});

test("R12 a private_s3 artifact -> blocked_external metadata_files_unavailable", async () => {
  mount("ml_engineer");
  await startWeights(SEEDS.artifacts.r12!);
  const job = await jobFor((j) => j.trainable_artifact_id === SEEDS.artifacts.r12);
  const out = await advance(job.id);
  expect([out.state, out.failure_code]).toEqual(["blocked_external", "metadata_files_unavailable"]);
  await expectJobShows(job.id, "blocked_external", /metadata files/);
});
