// @vitest-environment jsdom
//
// FTF-H011-P4 (UI CHECKLIST f) -- the real dataset mapping wizard against the REAL FTF-H011-P3
// routes (build_dataset_mapping_router) on a real uvicorn server over PostgreSQL 16 as sovra_app
// and the live SeaweedFS S3 API, with every dataset uploaded through the real FTF-0501 routes.
// Nothing about the server is mocked. The only test double is the transport wrapper below, which
// adds the test principal header the router's principal_resolver reads (in place of the OIDC
// session cookie the product will use) and keeps every response body for the canary scan. The
// harness's /__harness/* routes stand in for an operator stopping the object store or narrowing a
// grant mid-flow, and are labelled as such.
//
// Run by tests/integration/test_ftf_h011_ui_real_api.py (which seeds the connections, tools and
// datasets, passes them in the environment and then verifies the database independently); not
// part of the default vitest include.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { DatasetMappingWizard } from "../../components/dataset-factory/dataset_mapping_wizard_ui";

const ENV = (globalThis as unknown as { process: { env: Record<string, string | undefined> } })
  .process.env;
function required(name: string): string {
  const value = ENV[name];
  if (!value)
    throw new Error(`${name} is required (run via tests/integration/test_ftf_h011_ui_real_api.py)`);
  return value;
}

interface Seeds {
  org: string;
  project: string;
  datasets: Record<string, string>;
  admin: string;
  ml: string;
  canaries: string[];
}
const API = required("FTF_H011_UI_API_BASE");
const SEEDS = JSON.parse(required("FTF_H011_UI_SEEDS")) as Seeds;

let principal = SEEDS.admin;
const bodies: { principal: string; url: string; text: string }[] = [];
const realFetch = globalThis.fetch;

beforeEach(() => {
  principal = SEEDS.admin;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    headers.set("X-Test-Principal", principal);
    const response = await realFetch(url, { ...init, headers });
    const text = await response.clone().text();
    bodies.push({ principal, url, text });
    return response;
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function harness(action: string): Promise<void> {
  const r = await realFetch(`${API}/__harness/${action}`, { method: "POST" });
  expect(r.status, `harness ${action}`).toBe(204);
}

function mount(dataset: string) {
  render(
    createElement(DatasetMappingWizard, {
      apiBaseUrl: API,
      organizationId: SEEDS.org,
      projectId: SEEDS.project,
      datasetId: dataset,
    }),
  );
}
const T = { timeout: 60_000 };
function type(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
function choose(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
async function start(dataset: string, format: RegExp) {
  mount(dataset);
  await screen.findByTestId("mapping-versions-empty", {}, T);
  fireEvent.click(screen.getByRole("radio", { name: format }));
}
async function probe() {
  fireEvent.click(screen.getByTestId("probe-button"));
  await screen.findByTestId("mapping-step-map", {}, T);
}
function messages(roles: [string, string][], extra: Record<string, string> = {}) {
  fireEvent.click(screen.getByRole("radio", { name: /A list of messages/ }));
  type(/Messages location/, extra.pointer ?? "/conversation");
  type(/^Role key/, "role");
  type(/^Content key/, "content");
  roles.forEach(([raw, target], i) => {
    if (i > 0) fireEvent.click(screen.getByTestId("add-role"));
    type(new RegExp(`Raw role value ${i + 1}`), raw);
    choose(new RegExp(`Maps to \\(role ${i + 1}\\)`), target);
  });
  if (extra.toolCalls) type(/^Tool calls key/, extra.toolCalls);
  if (extra.toolCallId) type(/^Tool call ID key/, extra.toolCallId);
  if (extra.name) type(/^Tool name key/, extra.name);
}
async function preview() {
  fireEvent.click(screen.getByTestId("preview-button"));
  await waitFor(
    () =>
      expect(
        screen.queryByTestId("preview-result") ?? screen.queryByTestId("preview-error"),
      ).toBeTruthy(),
    T,
  );
}
async function save() {
  fireEvent.click(screen.getByTestId("to-propose"));
  fireEvent.click(screen.getByTestId("save-mapping"));
  await screen.findByTestId("mapping-step-approve", {}, T);
}
async function approve(reason: string) {
  type(/Reason for the decision/, reason);
  fireEvent.click(screen.getByTestId("approve-confirm"));
  fireEvent.click(screen.getByTestId("approve-button"));
}
const state = () => screen.getByTestId("mapping-state").textContent ?? "";

test("R1 real windows-1256 ';' CSV with an Arabic header on row 3 is approved", async () => {
  await start(SEEDS.datasets.r1!, /CSV/);
  choose(/Text encoding/, "windows-1256");
  choose(/Delimiter/, ";");
  type(/Rows before the header/, "2");
  await probe();
  const table = screen.getByTestId("probe-table");
  for (const header of ["سؤال", "جواب", "سري"]) expect(table.textContent).toContain(header);
  fireEvent.click(screen.getByRole("radio", { name: /A prompt and a completion/ }));
  choose(/^Prompt/, "0");
  choose(/^Completion/, "1");
  await preview();
  expect(screen.getByTestId("preview-row-0").getAttribute("data-outcome")).toBe("accepted");
  expect(screen.getByTestId("preview-row-0").textContent).toContain("مرحبا");
  await save();
  expect(screen.getByTestId("mapping-purpose").textContent).toContain("training");
  await approve("Arabic header on row 3");
  await waitFor(() => expect(state()).toContain("Approved"), T);
  expect(screen.getByTestId("mapping-notice").textContent).toContain("approved for training");
});

test("R2 real JSONL with tool calls: the unknown tool and the orphan id are rejected rows, shown", async () => {
  await start(SEEDS.datasets.r2!, /JSON Lines/);
  await probe();
  messages(
    [
      ["user", "user"],
      ["assistant", "assistant"],
      ["tool", "tool"],
    ],
    { pointer: "/messages", toolCalls: "tool_calls", toolCallId: "tool_call_id", name: "name" },
  );
  await preview();
  expect(screen.getByTestId("preview-row-0").getAttribute("data-outcome")).toBe("accepted");
  expect(screen.getByTestId("preview-row-1").getAttribute("data-outcome")).toBe("rejected");
  expect(screen.getByTestId("preview-row-2").getAttribute("data-outcome")).toBe("rejected");
  expect(screen.getByTestId("preview-row-1-code").textContent).toBeTruthy();
  expect(screen.getByTestId("preview-row-2-code").textContent).toBeTruthy();
  expect(state()).toContain("Validation required");
});

test("R3 real no assistant in role_map: the server's 400 is shown and nothing is stored", async () => {
  await start(SEEDS.datasets.r3!, /JSON Lines/);
  await probe();
  messages([["human", "user"]]);
  expect(screen.getByTestId("no-assistant-warning")).toBeTruthy();
  await preview();
  expect(screen.getByTestId("preview-alert").textContent).toContain("assistant");
  expect(state()).toContain("Validation required");
  expect(screen.getByTestId("to-propose").hasAttribute("disabled")).toBe(true);
});

test("R4 real unmapped raw role: proposable, but the approval 409 is shown", async () => {
  await start(SEEDS.datasets.r4!, /JSON Lines/);
  await probe();
  messages([
    ["human", "user"],
    ["bot", "assistant"],
  ]);
  await preview();
  expect(screen.getByTestId("observed-roles").textContent).toContain("not in the role map");
  await save();
  await approve("try it");
  await screen.findByTestId("decision-error", {}, T);
  expect(screen.getByTestId("decision-error").textContent).toContain("role or message key");
  expect(state()).not.toContain("Approved");
});

test("R5 real evaluation-only grant: probe 403 shown as Blocked", async () => {
  await start(SEEDS.datasets.r5a!, /JSON Lines/);
  fireEvent.click(screen.getByTestId("probe-button"));
  await screen.findByTestId("probe-error", {}, T);
  expect(state()).toContain("Blocked — The data source is not authorized");
});

test("R5 real grant narrowed after the proposal: approve 403 shown, never approved", async () => {
  await start(SEEDS.datasets.r5b!, /JSON Lines/);
  await probe();
  messages([
    ["human", "user"],
    ["bot", "assistant"],
  ]);
  await preview();
  await save();
  await harness("narrow-r5b");
  await approve("narrowed meanwhile");
  await screen.findByTestId("decision-error", {}, T);
  expect(screen.getByTestId("decision-error").textContent).toContain("not authorized");
  expect(state()).toContain("Blocked");
});

test("R5 real held-out dataset on an evaluation-only grant: approved for its own purpose only", async () => {
  await start(SEEDS.datasets.r5c!, /JSON Lines/);
  await probe();
  messages([
    ["human", "user"],
    ["bot", "assistant"],
  ]);
  await preview();
  await save();
  expect(screen.getByTestId("mapping-purpose").textContent).toContain("held-out evaluation");
  await approve("evaluation only");
  await waitFor(() => expect(state()).toContain("Approved"), T);
  const sent = bodies.filter((b) => b.url.endsWith("/approve"));
  expect(sent.length).toBeGreaterThan(0);
});

test("R6 real ml_engineer on a confidential connection sees only masked values", async () => {
  principal = SEEDS.ml;
  const before = bodies.length;
  await start(SEEDS.datasets.r6!, /JSON Lines/);
  await probe();
  expect(screen.getByTestId("masked-notice")).toBeTruthy();
  const root = screen.getByTestId("dataset-mapping-wizard");
  expect(root.querySelectorAll('[data-masked="true"]').length).toBeGreaterThan(0);
  for (const canary of SEEDS.canaries) expect(root.textContent).not.toContain(canary);
  const mine = bodies.slice(before).filter((b) => b.principal === SEEDS.ml);
  expect(mine.length).toBeGreaterThan(0);
  for (const b of mine) for (const canary of SEEDS.canaries) expect(b.text).not.toContain(canary);
});

test("R7 real foreign dataset: 404 shown, nothing listed", async () => {
  mount(SEEDS.datasets.r7!);
  await screen.findByTestId("mapping-versions-error", {}, T);
  expect(screen.getByTestId("mapping-versions-alert").textContent).toContain("not found");
});

test("R8 real storage stopped: the probe is Blocked, then works when it is back", async () => {
  await start(SEEDS.datasets.r8!, /JSON Lines/);
  await harness("storage-down");
  try {
    fireEvent.click(screen.getByTestId("probe-button"));
    await screen.findByTestId("probe-error", {}, T);
    expect(state()).toContain("Blocked — Dataset storage cannot be reached");
  } finally {
    await harness("storage-up");
  }
  await probe();
  expect(state()).not.toContain("Blocked");
});

test("R9 real JSON with a container pointer: records read from /rows, one accepted one rejected", async () => {
  await start(SEEDS.datasets.r9!, /JSON \(one document\)/);
  type(/Records location/, "/rows");
  fireEvent.change(screen.getByLabelText(/Top-level keys to ignore/), {
    target: { value: "meta" },
  });
  await probe();
  expect(within(screen.getByTestId("mapping-step-map")).getAllByText("p1").length).toBeGreaterThan(
    0,
  );
  fireEvent.click(screen.getByRole("radio", { name: /A prompt and a completion/ }));
  type(/^Prompt/, "prompt");
  type(/^Completion/, "completion");
  await preview();
  expect(screen.getByTestId("preview-row-0").getAttribute("data-outcome")).toBe("accepted");
  expect(screen.getByTestId("preview-row-1").getAttribute("data-outcome")).toBe("rejected");
});

test("canary scan: no response to anyone carried an object key, a bucket or a storage URL", () => {
  expect(bodies.length).toBeGreaterThan(10);
  for (const b of bodies) {
    expect(b.text).not.toMatch(/X-Amz-|s3:\/\/|sovra-ftfh011ui-/);
  }
});
