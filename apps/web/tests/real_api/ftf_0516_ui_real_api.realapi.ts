// @vitest-environment jsdom
//
// FTF-0516 UI round 5 (UI CHECKLIST f) -- the real issue-resolution component against the REAL
// FTF-0516-P1 router (build_dataset_quality_router) on PostgreSQL 16 as sovra_app. Nothing about
// the server is mocked. The only test double is the transport wrapper below, which (1) adds the
// test principal header the router's principal_resolver reads, in place of the OIDC session
// cookie the product will use, and (2) can drop ONE response after the server has processed the
// request, to model a lost response.
//
// Run by tests/integration/test_ftf_0516_ui_real_api.py (which seeds the datasets, reports and
// issues, passes them in the environment and then verifies the database independently); not part
// of the default vitest include.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { DatasetIssueResolutionUI } from "../../components/dataset-factory/dataset_issue_resolution_ui";

// No @types/node in this workspace (a new dependency is an owner ask): read the environment the
// Python harness passes in through the runtime global.
const ENV = (globalThis as unknown as { process: { env: Record<string, string | undefined> } })
  .process.env;

function required(name: string): string {
  const value = ENV[name];
  if (!value)
    throw new Error(`${name} is required (run via tests/integration/test_ftf_0516_ui_real_api.py)`);
  return value;
}

interface IssueSeed {
  id: string;
  evidence: string;
  positions: number[];
  failure: boolean;
  severity: string;
}
interface Seed {
  org: string;
  project: string;
  dataset: string;
  report: string;
  issues: Record<string, IssueSeed>;
  successor?: string;
}
const API_BASE = required("FTF_0516_UI_API_BASE");
const SEEDS = JSON.parse(required("FTF_0516_UI_SEEDS")) as Record<string, Seed>;
const PRINCIPAL_ML = required("FTF_0516_UI_PRINCIPAL_ML");
const PRINCIPAL_ADMIN = required("FTF_0516_UI_PRINCIPAL_ADMIN");

interface Call {
  method: string;
  url: string;
  status: number | "lost";
  requestBody?: Record<string, unknown>;
}
let records: Call[] = [];
let principal = PRINCIPAL_ML;
let loseNextPostResponse = false;
const realFetch = globalThis.fetch;

beforeEach(() => {
  records = [];
  principal = PRINCIPAL_ML;
  loseNextPostResponse = false;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const headers = new Headers(init.headers);
    headers.set("X-Test-Principal", principal);
    const requestBody = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    const response = await realFetch(url, { ...init, headers });
    if (method === "POST" && loseNextPostResponse) {
      // the server answered (and recorded the decision); the browser never sees the answer
      loseNextPostResponse = false;
      records.push({ method, url, status: "lost", requestBody });
      throw new TypeError("fetch failed: injected connection loss after the server answered");
    }
    records.push({ method, url, status: response.status, requestBody });
    return response;
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** A direct API call as another actor, outside the component (the competing reviewer). */
async function directResolve(
  seed: Seed,
  asPrincipal: string,
  issue: IssueSeed,
  version: number,
  action = "exclude",
): Promise<number> {
  const response = await realFetch(
    `${API_BASE}/api/v1/organizations/${seed.org}/projects/${seed.project}/datasets/${seed.dataset}/resolve-issue`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Test-Principal": asPrincipal },
      body: JSON.stringify({
        quality_report_id: seed.report,
        expected_resolution_version: version,
        idempotency_key: `competing-${globalThis.crypto.randomUUID()}`,
        resolutions: [
          {
            issue_id: issue.id,
            action,
            reason: "competing reviewer",
            expected_before_evidence_sha256: issue.evidence,
            ...(action === "exclude" ? { excluded_batch_positions: [issue.positions[0]] } : {}),
          },
        ],
      }),
    },
  );
  return response.status;
}

function mount(seed: Seed) {
  return render(
    createElement(DatasetIssueResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: seed.org,
      projectId: seed.project,
      datasetId: seed.dataset,
    }),
  );
}
async function load() {
  fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
  await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull(), { timeout: 30_000 });
}
function review(issue: IssueSeed) {
  fireEvent.click(screen.getByRole("button", { name: `Review ${issue.id.slice(0, 8)}` }));
}
function type(box: HTMLElement, value: string) {
  fireEvent.change(box, { target: { value } });
}
function reasonBox() {
  return screen.getByLabelText("Rationale") as HTMLTextAreaElement;
}
function submitButton() {
  return screen.getByRole("button", { name: /Record decision|Submitting/ }) as HTMLButtonElement;
}
async function submit() {
  await act(async () => {
    fireEvent.click(submitButton());
  });
  await waitFor(() => expect(screen.queryByText("Submitting...")).toBeNull(), { timeout: 30_000 });
}
function posts(): Call[] {
  return records.filter((r) => r.method === "POST");
}
const SHA = /^[0-9a-f]{64}$/;

test("R1 real exclude: recorded, the server's before and after evidence are shown", async () => {
  const seed = SEEDS.r1 as Seed;
  const pii = seed.issues.pii_secret_email as IssueSeed;
  mount(seed);
  await load();
  expect(screen.queryByRole("alert")).toBeNull();
  review(pii);
  type(reasonBox(), "removing the row that holds the email address");
  fireEvent.click(screen.getByLabelText("position 2"));
  await submit();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(posts()).toHaveLength(1);
  expect(posts()[0]?.status).toBe(201);
  expect(posts()[0]?.requestBody?.expected_resolution_version).toBe(0);
  const shown = screen.getByTestId("recorded-decision").textContent as string;
  expect(shown).toContain("exclude (resolved) by user-org1-ml_engineer as ml_engineer");
  const before = /Before evidence ([0-9a-f]{64})/.exec(shown)?.[1];
  const after = /after evidence ([0-9a-f]{64})/.exec(shown)?.[1];
  expect(before).toBe(pii.evidence); // the server's stored evidence, not invented by the UI
  expect(after).toMatch(SHA);
  expect(after).not.toBe(before);
});

test("R2 real correct: an uppercase-typed successor is sent lowercase and recorded", async () => {
  const seed = SEEDS.r2 as Seed;
  const warn = seed.issues.ui_c_warn as IssueSeed;
  mount(seed);
  await load();
  review(warn);
  fireEvent.change(screen.getByLabelText("Action"), { target: { value: "correct" } });
  type(reasonBox(), "fixed in the later version");
  type(
    screen.getByLabelText("Successor dataset id"),
    ` ${(seed.successor as string).toUpperCase()} `,
  );
  await submit();
  expect(screen.queryByRole("alert")).toBeNull();
  const resolutions = posts()[0]?.requestBody?.resolutions as Array<Record<string, unknown>>;
  expect(resolutions[0]?.successor_dataset_id).toBe(seed.successor);
  expect(posts()[0]?.status).toBe(201);
  expect(screen.getByTestId("recorded-decision").textContent).toContain("correct");
});

test("R3 real waive: 403 for ml_engineer records nothing; recorded for workspace_admin", async () => {
  const seed = SEEDS.r3 as Seed;
  const warn = seed.issues.ui_w_warn as IssueSeed;
  mount(seed);
  await load();
  review(warn);
  fireEvent.change(screen.getByLabelText("Action"), { target: { value: "waive" } });
  type(reasonBox(), "false positive, reviewed");
  await submit();
  expect(posts()[0]?.status).toBe(403);
  expect(screen.getByRole("alert").textContent).toBe("Your role may not waive an issue.");
  expect(screen.queryByTestId("recorded-decision")).toBeNull();
  // the same decision as a workspace admin
  cleanup();
  principal = PRINCIPAL_ADMIN;
  mount(seed);
  await load();
  review(warn);
  fireEvent.change(screen.getByLabelText("Action"), { target: { value: "waive" } });
  type(reasonBox(), "false positive, reviewed");
  await submit();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(posts()[1]?.status).toBe(201);
  expect(screen.getByTestId("recorded-decision").textContent).toContain(
    "waive (waived) by user-org1-workspace_admin as workspace_admin",
  );
});

test("R4 real stale view: a decision that landed first makes the submit a shown 409, nothing recorded", async () => {
  const seed = SEEDS.r4 as Seed;
  const mine = seed.issues.ui_s_warn_a as IssueSeed;
  const theirs = seed.issues.ui_s_warn_b as IssueSeed;
  mount(seed);
  await load(); // the view holds resolution_version 0
  review(mine);
  type(reasonBox(), "my decision on a stale view");
  fireEvent.click(screen.getByLabelText("position 0"));
  expect(await directResolve(seed, PRINCIPAL_ML, theirs, 0)).toBe(201);
  await submit();
  expect(posts()[0]?.status).toBe(409);
  expect(screen.getByRole("alert").textContent).toBe(
    "The decisions changed since this screen was loaded. Reload and review again.",
  );
  expect(screen.queryByTestId("recorded-decision")).toBeNull();
});

test("R5 real lost response: another decision lands, the identical retry is recorded as ONE decision", async () => {
  const seed = SEEDS.r5 as Seed;
  const mine = seed.issues.ui_l_warn_a as IssueSeed;
  const theirs = seed.issues.ui_l_warn_b as IssueSeed;
  mount(seed);
  await load();
  review(mine);
  type(reasonBox(), "decision whose response is lost");
  fireEvent.click(screen.getByLabelText("position 0"));
  loseNextPostResponse = true;
  await submit(); // the server records it; the browser never hears
  expect(posts()[0]?.status).toBe("lost");
  expect(screen.getByRole("alert").textContent).toContain("network error:");
  expect(screen.queryByTestId("recorded-decision")).toBeNull();
  // another reviewer decides the OTHER issue meanwhile: the report's version moves on
  expect(await directResolve(seed, PRINCIPAL_ML, theirs, 1)).toBe(201);
  await submit(); // identical retry (same body, same idempotency key)
  expect(posts()).toHaveLength(2);
  expect(posts()[1]?.requestBody).toEqual(posts()[0]?.requestBody);
  expect(posts()[1]?.status).toBe(200); // a replay, not a second decision
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByTestId("recorded-decision").textContent).toContain("exclude (resolved)");
});

test("R6 real detector failure: the issue offers no decision control and nothing is posted", async () => {
  const seed = SEEDS.r6 as Seed;
  const failure = Object.values(seed.issues).find((i) => i.failure) as IssueSeed;
  expect(failure).toBeTruthy();
  mount(seed);
  await load();
  review(failure);
  expect(screen.getByText("A detector failure requires a rerun, not a decision.")).toBeTruthy();
  expect(screen.queryByLabelText("Rationale")).toBeNull();
  expect(screen.queryByRole("button", { name: /Record decision/ })).toBeNull();
  expect(posts()).toHaveLength(0);
});

test("R7 real foreign dataset: another organization's dataset is a shown 404, nothing is rendered", async () => {
  const seed = SEEDS.r7 as Seed;
  mount(seed);
  await load();
  expect(records.some((r) => r.status === 404)).toBe(true);
  expect(screen.getByRole("alert").textContent).toBe("The dataset or issue was not found.");
  expect(screen.queryByTestId("report")).toBeNull();
  expect(screen.queryByRole("button", { name: /^Review / })).toBeNull();
  expect(posts()).toHaveLength(0);
});
