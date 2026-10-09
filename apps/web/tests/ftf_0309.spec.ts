// @vitest-environment jsdom
//
// FTF-0309 -- Capability compatibility UI.
//
// A real component/DOM-level test (Vitest + React Testing Library + jsdom):
// renders the actual `CapabilityCompatibilityUI`, drives real clicks and
// asserts on the exact `fetch` call (method, URL) made against the frozen
// FTF-0309-P1 route, and on Train's disabled state plus the reason shown for
// each typed state. `fetch` is mocked (no live server in a unit test).
//
// Disclosed: component-level, not a browser E2E -- the repository has no
// Next.js `app/` page tree yet (FTF-H029), same precedent as ftf_0308.spec.ts.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  CapabilityCompatibilityUI,
  capabilitiesUrl,
} from "../components/artifact-capabilities/capability_compatibility_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const CONN_ID = "22222222-2222-2222-2222-222222222222";
const PROJECT_ID = "33333333-3333-3333-3333-333333333333";
// Looks like a normalisable alias: it must be sent verbatim.
const ALIAS = "Org/Llama-3-8B Instruct";
const URL = `${API_BASE}/api/v1/organizations/${ORG_ID}/connections/litellm/${CONN_ID}/models/Org/Llama-3-8B%20Instruct/capabilities?project_id=${PROJECT_ID}`;

const KNOWN = { state: "known" };
const unknown = (reason: string) => ({ state: "unknown", reason });

function capabilities(extra: Record<string, unknown> = {}) {
  return {
    litellm_connection_id: CONN_ID,
    project_id: PROJECT_ID,
    public_model_name: ALIAS,
    connection_status: "active",
    access: "granted",
    capability: "trainable_open_weight",
    trainable: true,
    capability_detail: "mapped artifact",
    capability_reason: null,
    role: "primary",
    artifact: {
      artifact_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      availability: "ready",
      license_approved: true,
      weights_digest: `sha256:${"a".repeat(64)}`,
    },
    catalog_version: 3,
    method_support: KNOWN,
    min_gpu_vram_gb: {
      ...KNOWN,
      tested_gb: 80,
      declared_minimum_gpu_profile: { gpu: "A100", memory_gb: 40 },
      declared_evidence: "declared",
    },
    chat_template: KNOWN,
    evidence: {
      discovery_checked_at: "2026-10-09T08:00:00+00:00",
      tool_call_probe_checked_at: null,
      tool_call_probe_present: false,
    },
    ...extra,
  };
}

type Reply = { ok: boolean; status: number; body: unknown };
let reply: Reply | "network";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const current = reply;
      if (current === "network") throw new Error("connection reset");
      return { ok: current.ok, status: current.status, json: async () => current.body };
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderUI(onTrain?: (m: string) => void) {
  return render(
    createElement(CapabilityCompatibilityUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      connectionId: CONN_ID,
      projectId: PROJECT_ID,
      modelName: ALIAS,
      onTrain,
    }),
  );
}

async function load(body: Reply | "network") {
  reply = body;
  const onTrain = vi.fn();
  renderUI(onTrain);
  fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
  await waitFor(() => expect(screen.queryByText("Checking...")).toBeNull());
  return onTrain;
}

const ok = (body: unknown): Reply => ({ ok: true, status: 200, body });
const train = () => screen.getByRole("button", { name: "Train" }) as HTMLButtonElement;

test("FTF-0309-A1 all dimensions known: exact GET, evidence shown, Train enabled and explicit", async () => {
  const onTrain = await load(ok(capabilities()));
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0]?.[0]).toBe(URL);
  expect(fetchMock.mock.calls[0]?.[1].method).toBe("GET");
  expect(capabilitiesUrl(API_BASE, ORG_ID, CONN_ID, ALIAS, PROJECT_ID)).toBe(URL);
  expect(screen.getByText(/2026-10-09T08:00:00/, { selector: "dd" })).toBeTruthy();
  expect(screen.getByTestId("declared-vram").textContent).toContain("declared, not tested");
  expect(train().disabled).toBe(false);
  expect(onTrain).not.toHaveBeenCalled(); // never auto-started
  fireEvent.click(train());
  expect(onTrain).toHaveBeenCalledWith(ALIAS);
});

for (const [name, patch, reason] of [
  [
    "method_support",
    { method_support: unknown("no_backing_evidence") },
    "Method support is unknown: no_backing_evidence",
  ],
  [
    "min_gpu_vram_gb",
    {
      min_gpu_vram_gb: {
        ...unknown("not_tested"),
        declared_minimum_gpu_profile: { memory_gb: 40 },
        declared_evidence: "declared",
      },
    },
    "Tested VRAM is unknown: not_tested",
  ],
  [
    "chat_template",
    { chat_template: unknown("no_backing_evidence") },
    "Chat template is unknown: no_backing_evidence",
  ],
] as const) {
  test(`FTF-0309-N1 unknown ${name} disables Train and shows the server reason`, async () => {
    const onTrain = await load(ok(capabilities(patch)));
    expect(train().disabled).toBe(true);
    expect(screen.getByText(reason)).toBeTruthy();
    fireEvent.click(train());
    expect(onTrain).not.toHaveBeenCalled();
  });
}

test("FTF-0309-N1 an unrecognised dimension state blocks (fail closed)", async () => {
  await load(ok(capabilities({ chat_template: { state: "maybe" } })));
  expect(train().disabled).toBe(true);
});

test("FTF-0309-N2 insufficient tested VRAM disables Train and names the numbers", async () => {
  await load(
    ok(
      capabilities({
        min_gpu_vram_gb: {
          state: "known",
          tested_gb: 16,
          declared_minimum_gpu_profile: { gpu: "A100", memory_gb: 40 },
        },
      }),
    ),
  );
  expect(train().disabled).toBe(true);
  expect(screen.getByText(/Tested VRAM 16 GB is below the declared minimum 40 GB/)).toBeTruthy();
});

test("FTF-0309-N3 access not_granted disables Train even when trainable is true", async () => {
  await load(ok(capabilities({ access: "not_granted" })));
  expect(train().disabled).toBe(true);
  expect(screen.getByText("Gateway access is not_granted")).toBeTruthy();
});

test("FTF-0309-N4 trainable false, unready artifact and unapproved license each disable Train", async () => {
  await load(ok(capabilities({ trainable: false, capability_reason: "no_trainable_evidence" })));
  expect(train().disabled).toBe(true);
  expect(screen.getByText("Not trainable: no_trainable_evidence")).toBeTruthy();
  cleanup();
  await load(
    ok(capabilities({ artifact: { ...capabilities().artifact, availability: "registered" } })),
  );
  expect(train().disabled).toBe(true);
  cleanup();
  await load(
    ok(capabilities({ artifact: { ...capabilities().artifact, license_approved: false } })),
  );
  expect(train().disabled).toBe(true);
  expect(screen.getByText("Artifact license is not approved")).toBeTruthy();
});

test("FTF-0309-N5 the real frozen shape (everything unknown) never enables Train", async () => {
  await load(
    ok(
      capabilities({
        method_support: unknown("no_backing_evidence"),
        min_gpu_vram_gb: {
          ...unknown("not_tested"),
          declared_minimum_gpu_profile: { memory_gb: 40 },
          declared_evidence: "declared",
        },
        chat_template: unknown("no_backing_evidence"),
      }),
    ),
  );
  expect(train().disabled).toBe(true);
});

test("FTF-0309-N6 503 and 404 are errors with no recommendation and no Train button", async () => {
  await load({ ok: false, status: 503, body: { detail: "gateway_unavailable" } });
  expect(screen.getByRole("alert").textContent).toContain("could not be reached");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  cleanup();
  await load({ ok: false, status: 404, body: { detail: "not_found" } });
  expect(screen.getByRole("alert").textContent).toContain("not found");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  cleanup();
  await load("network");
  expect(screen.getByRole("alert").textContent).toContain("network error: connection reset");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  cleanup();
  await load(ok({ trainable: true }));
  expect(screen.getByRole("alert").textContent).toContain("malformed");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
});
