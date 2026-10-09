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

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    // The frozen contract allows only `unknown` here (ModelCapabilityUnknown).
    method_support: unknown("no_backing_evidence"),
    min_gpu_vram_gb: {
      ...unknown("not_tested"),
      declared_minimum_gpu_profile: { gpu: "A100", memory_gb: 40 },
      declared_evidence: "declared",
    },
    chat_template: unknown("no_backing_evidence"),
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

test("FTF-0309-A1 the real frozen shape: exact GET, evidence shown, Train present but disabled and never auto-fired", async () => {
  const onTrain = await load(ok(capabilities()));
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0]?.[0]).toBe(URL);
  expect(fetchMock.mock.calls[0]?.[1].method).toBe("GET");
  expect(fetchMock.mock.calls[0]?.[1].credentials).toBe("include"); // F17
  expect(capabilitiesUrl(API_BASE, ORG_ID, CONN_ID, ALIAS, PROJECT_ID)).toBe(URL);
  expect(screen.getByText(/2026-10-09T08:00:00/, { selector: "dd" })).toBeTruthy();
  expect(screen.getByTestId("declared-vram").textContent).toContain("declared, not tested");
  expect(train().disabled).toBe(true);
  fireEvent.click(train());
  expect(onTrain).not.toHaveBeenCalled();
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

test("FTF-0309-N1 an unrecognised dimension state is a typed error with no Train (fail closed)", async () => {
  await load(ok(capabilities({ chat_template: { state: "maybe" } })));
  expect(screen.getByRole("alert").textContent).toContain("malformed");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
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

const OTHER_PROJECT = "44444444-4444-4444-4444-444444444444";
const OTHER_CONN = "55555555-5555-5555-5555-555555555555";

function props(extra: Record<string, unknown> = {}) {
  return {
    apiBaseUrl: API_BASE,
    organizationId: ORG_ID,
    connectionId: CONN_ID,
    projectId: PROJECT_ID,
    modelName: ALIAS,
    onTrain: vi.fn(),
    ...extra,
  };
}

async function check() {
  fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
  await waitFor(() => expect(screen.queryByText("Checking...")).toBeNull());
}

for (const [what, patch] of [
  ["organizationId", { organizationId: "66666666-6666-6666-6666-666666666666" }],
  ["modelName", { modelName: "Org/Other" }],
  ["projectId", { projectId: OTHER_PROJECT }],
  ["connectionId", { connectionId: OTHER_CONN }],
] as const) {
  test(`FTF-0309-PR1 a ${what} switch clears the old result so Train cannot act on an unchecked identity`, async () => {
    reply = ok(capabilities());
    const first = props();
    const view = render(createElement(CapabilityCompatibilityUI, first));
    await check();
    expect(screen.getByRole("button", { name: "Train" })).toBeTruthy(); // a result is on screen
    view.rerender(createElement(CapabilityCompatibilityUI, { ...first, ...patch }));
    expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
    expect(first.onTrain).not.toHaveBeenCalled();
  });
}

test("FTF-0309-PR2 a response that lands after a model switch is ignored", async () => {
  let release: (value: unknown) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    ),
  );
  const first = props();
  const view = render(createElement(CapabilityCompatibilityUI, first));
  fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
  view.rerender(createElement(CapabilityCompatibilityUI, { ...first, modelName: "Org/Other" }));
  release({ ok: true, status: 200, json: async () => capabilities() });
  await new Promise((r) => setTimeout(r, 10));
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Check compatibility" })).toBeTruthy();
});

for (const [what, patch] of [
  ["public_model_name", { public_model_name: "Org/Other" }],
  ["project_id", { project_id: OTHER_PROJECT }],
  ["litellm_connection_id", { litellm_connection_id: OTHER_CONN }],
] as const) {
  test(`FTF-0309-PR3 a 200 echoing a different ${what} is an error with no Train`, async () => {
    await load(ok(capabilities(patch)));
    expect(screen.getByRole("alert").textContent).toContain("does not match the request");
    expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  });
}

test("FTF-0309-PR4 license_approved must be the boolean true; the string 'false' never enables Train", async () => {
  await load(
    ok(capabilities({ artifact: { ...capabilities().artifact, license_approved: "false" } })),
  );
  expect(screen.getByRole("alert").textContent).toContain("malformed");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
});

test("FTF-0309-F16 a missing dimension, an unknown top-level key and an unknown-without-reason are typed errors", async () => {
  const missing = capabilities() as Record<string, unknown>;
  delete missing.chat_template;
  await load(ok(missing));
  expect(screen.getByRole("alert").textContent).toContain("malformed");
  cleanup();
  await load(ok(capabilities({ recommended_model: "Org/Llama" })));
  expect(screen.getByRole("alert").textContent).toContain("malformed");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  cleanup();
  await load(ok(capabilities({ method_support: { state: "unknown" } })));
  expect(screen.getByRole("alert").textContent).toContain("malformed");
});

test("FTF-0309-F13 an unknown row shows the server's own reason in the row", async () => {
  await load(ok(capabilities({ method_support: unknown("no_backing_evidence") })));
  expect(screen.getByTestId("dimension-Method support").textContent).toContain(
    "reason: no_backing_evidence",
  );
});

test("FTF-0309-F22 the blockers list is rendered and accessible when Train is blocked", async () => {
  await load(ok(capabilities({ access: "not_granted" })));
  const list = screen.getByRole("list", { name: "Why training is blocked" }); // role queries skip hidden nodes
  expect(list.textContent).toContain("Gateway access is not_granted");
});

function reactOnClick(button: HTMLElement): () => void {
  const key = Object.keys(button).find((k) => k.startsWith("__reactProps$"));
  const handler = key
    ? (button as unknown as Record<string, { onClick?: () => void }>)[key]?.onClick
    : undefined;
  if (!handler) throw new Error("Train has no React onClick handler");
  return handler;
}

test("FTF-0309-PR13b the click handler itself never calls onTrain (not only the disabled attribute)", async () => {
  reply = ok(capabilities());
  const onTrain = vi.fn();
  renderUI(onTrain);
  await check();
  const click = reactOnClick(train());
  act(() => click());
  expect(onTrain).not.toHaveBeenCalled();
  expect(screen.queryByText(/Training requested/)).toBeNull();
  expect(screen.queryByRole("status")).toBeNull();
});

test("FTF-0309-R1 a row shows the server's reason and never a tested value or borrowed date", async () => {
  await load(ok(capabilities()));
  const vram = screen.getByTestId("dimension-Tested VRAM");
  expect(vram.textContent).toContain("reason: not_tested");
  expect(vram.textContent).toContain("no evidence date returned by the server");
  expect(vram.textContent).not.toContain("GB");
  for (const label of ["Method support", "Tested VRAM", "Chat template"]) {
    expect(screen.getByTestId(`dimension-${label}`).textContent).not.toContain("2026-10-09");
  }
});

test("FTF-0309-PR2b a stale response cannot alter the state of the newer request", async () => {
  const pending: Array<(value: unknown) => void> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise((resolve) => {
          pending.push(resolve);
        }),
    ),
  );
  const first = props();
  const view = render(createElement(CapabilityCompatibilityUI, first));
  fireEvent.click(screen.getByRole("button", { name: "Check compatibility" })); // request 1
  view.rerender(createElement(CapabilityCompatibilityUI, { ...first, modelName: "Org/Other" }));
  fireEvent.click(screen.getByRole("button", { name: "Check compatibility" })); // request 2
  expect(screen.getByText("Checking...")).toBeTruthy();
  await act(async () => {
    pending[0]?.({ ok: false, status: 503, json: async () => ({ detail: "gateway_unavailable" }) });
    await new Promise((r) => setTimeout(r, 10));
  });
  expect(screen.queryByRole("alert")).toBeNull(); // old 503 must not surface on the new check
  expect(screen.getByText("Checking...")).toBeTruthy(); // nor end the new check's loading state
  await act(async () => {
    pending[1]?.({
      ok: true,
      status: 200,
      json: async () => capabilities({ public_model_name: "Org/Other" }),
    });
    await new Promise((r) => setTimeout(r, 10));
  });
  expect(train().disabled).toBe(true);
  expect(screen.getByRole("button", { name: "Train" })).toBeTruthy();
});

test("FTF-0309-PR1b switching away and back does not resurrect the old result", async () => {
  reply = ok(capabilities());
  const first = props();
  const view = render(createElement(CapabilityCompatibilityUI, first));
  await check();
  view.rerender(createElement(CapabilityCompatibilityUI, { ...first, modelName: "Org/Other" }));
  view.rerender(createElement(CapabilityCompatibilityUI, first));
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
});

test("FTF-0309-PR2d a stale error body read after a switch never surfaces as an alert", async () => {
  let release: (value: unknown) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 503,
      json: () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    })),
  );
  const first = props();
  const view = render(createElement(CapabilityCompatibilityUI, first));
  fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  view.rerender(createElement(CapabilityCompatibilityUI, { ...first, modelName: "Org/Other" }));
  await act(async () => {
    release({ detail: "gateway_unavailable" });
    await new Promise((r) => setTimeout(r, 10));
  });
  expect(screen.queryByRole("alert")).toBeNull();
});

// ---- Round 3 (control-plane ruling): `unknown` is the only state the frozen contract allows ----

test("FTF-0309-K1 a `known` dimension is malformed: no Train, onTrain never fires (K1, K3)", async () => {
  for (const dim of ["method_support", "min_gpu_vram_gb", "chat_template"]) {
    cleanup();
    const onTrain = await load(ok(capabilities({ [dim]: { state: "known" } })));
    expect(screen.getByRole("alert").textContent).toContain("malformed");
    expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
    expect(onTrain).not.toHaveBeenCalled();
  }
  for (const dim of ["method_support", "min_gpu_vram_gb", "chat_template"]) {
    cleanup(); // a `known` that even carries a reason is still outside the frozen contract
    await load(ok(capabilities({ [dim]: { state: "known", reason: "tested" } })));
    expect(screen.getByRole("alert").textContent).toContain("malformed");
    expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  }
  cleanup();
  await load(
    ok(
      capabilities({
        min_gpu_vram_gb: {
          state: "known",
          tested_gb: 80,
          declared_minimum_gpu_profile: { memory_gb: 40 },
          declared_evidence: "declared",
        },
      }),
    ),
  );
  expect(screen.getByRole("alert").textContent).toContain("malformed");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
});

test("FTF-0309-F33 declared_evidence other than 'declared' is malformed", async () => {
  for (const evidence of ["tested", "", null, true]) {
    cleanup();
    await load(
      ok(
        capabilities({
          min_gpu_vram_gb: { ...unknown("not_tested"), declared_evidence: evidence },
        }),
      ),
    );
    expect(screen.getByRole("alert").textContent).toContain("malformed");
    expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  }
});

test("FTF-0309-F34 a tested_gb or memory_gb field on a dimension is malformed, even on an unknown one", async () => {
  for (const extra of [{ tested_gb: 80 }, { tested_gb: null }, { memory_gb: 40 }]) {
    for (const dim of ["method_support", "min_gpu_vram_gb", "chat_template"]) {
      cleanup();
      await load(ok(capabilities({ [dim]: { ...unknown("not_tested"), ...extra } })));
      expect(screen.getByRole("alert").textContent).toContain("malformed");
      expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
    }
  }
});

test("FTF-0309-F35 any other dimension key, or a non-string / empty reason, is malformed", async () => {
  for (const dim of [
    { ...unknown("r"), recommended: true },
    { state: "unknown", reason: 7 },
    { state: "unknown", reason: "" },
    { state: "UNKNOWN", reason: "r" },
    { state: "unknown", reason: "r", declared_evidence: "declared", note: "x" },
  ]) {
    cleanup();
    await load(ok(capabilities({ chat_template: dim })));
    expect(screen.getByRole("alert").textContent).toContain("malformed");
    expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  }
});

test("FTF-0309-F36 the declared minimum profile is shown as declared and may carry memory_gb", async () => {
  await load(ok(capabilities()));
  expect(screen.getByTestId("declared-vram").textContent).toContain('"memory_gb":40');
  expect(screen.queryByRole("alert")).toBeNull();
});

test("FTF-0309-F39 a re-check that fails clears the earlier result: alert shown, Train absent", async () => {
  reply = ok(capabilities());
  renderUI(vi.fn());
  await check();
  expect(screen.getByRole("button", { name: "Train" })).toBeTruthy();
  reply = { ok: false, status: 503, body: { detail: "gateway_unavailable" } };
  await check();
  expect(screen.getByRole("alert").textContent).toContain("could not be reached");
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  expect(screen.queryByTestId("declared-vram")).toBeNull();
});

test("FTF-0309-F7 a 200 whose body is still being read when the model switches is ignored", async () => {
  let release: (value: unknown) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    })),
  );
  const first = props();
  const view = render(createElement(CapabilityCompatibilityUI, first));
  fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  view.rerender(createElement(CapabilityCompatibilityUI, { ...first, modelName: "Org/Other" }));
  await act(async () => {
    release(capabilities({ public_model_name: "Org/Other" }));
    await new Promise((r) => setTimeout(r, 10));
  });
  expect(screen.queryByRole("button", { name: "Train" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByTestId("declared-vram")).toBeNull();
});
