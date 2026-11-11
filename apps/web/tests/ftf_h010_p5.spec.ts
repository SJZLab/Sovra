// @vitest-environment jsdom
//
// FTF-H010-P5 -- Import wizard UI (component/DOM level; UI CHECKLIST a-e). Renders the real
// `ImportModelWizard` against a route-table `fetch`. Real-router coverage is the real-API job;
// real-Chromium coverage is ftf_h010_p5_browser.spec.ts. UNWIRED until FTF-H029 mounts it.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  FAILURE_MESSAGES,
  IMPORT_JOB_STATES,
  ImportModelWizard,
  POLL_MAX_MS,
  POLL_START_MS,
  bindsTo,
  failureText,
  parseJob,
} from "../components/model-import/import_model_wizard_ui";

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const CONN = "33333333-3333-3333-3333-333333333333";
const ART = "44444444-4444-4444-4444-444444444444";
const ADAPTER = "55555555-5555-5555-5555-555555555555";
const BASE = "66666666-6666-6666-6666-666666666666";
const UNVALIDATED = "77777777-7777-7777-7777-777777777777";
const JOB = "88888888-8888-8888-8888-888888888888";
const RESULT = "99999999-9999-9999-9999-999999999999";
const ORG_BASE = `${API}/api/v1/organizations/${ORG}`;
const IMPORTS = `${ORG_BASE}/projects/${PROJECT}/model-imports`;
const VERSIONS = `${ORG_BASE}/projects/${PROJECT}/model-versions`;
const sha = (c: string) => c.repeat(64);

function job(extra: Record<string, unknown> = {}) {
  return {
    id: JOB,
    organization_id: ORG,
    project_id: PROJECT,
    requested_origin: "external_endpoint",
    parent_model_version_id: null,
    trainable_artifact_id: null,
    external_provider_model_ref: "ft:my-model",
    litellm_connection_id: CONN,
    result_model_version_id: null,
    state: "queued",
    failure_code: null,
    created_by: "user-1",
    created_at: "2026-11-01T00:00:00+00:00",
    updated_at: "2026-11-01T00:00:00+00:00",
    ...extra,
  };
}
const artifact = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  display_name: `art-${id.slice(0, 2)}`,
  source_type: "local",
  status: "ready",
  license_id: "apache-2.0",
  license_accepted_at: "2026-10-01T00:00:00+00:00",
  weights_digest: sha("a"),
  ...extra,
});

type Reply = { status?: number; body?: unknown; malformed?: boolean; reject?: boolean };
type Routes = Record<string, Reply | (() => Reply)>;
let calls: { method: string; url: string; body?: string; credentials?: RequestCredentials }[] = [];
let routes: Routes = {};

function defaults(): Routes {
  return {
    [`GET ${IMPORTS}`]: { body: [] },
    [`GET ${ORG_BASE}/projects/${PROJECT}`]: { body: { security_profile: "standard" } },
    [`GET ${ORG_BASE}/connections/litellm`]: {
      body: [
        {
          id: CONN,
          name: "main-gw",
          status: "active",
          base_url: "https://secret-gateway.example/",
        },
        { id: "rev", name: "old-gw", status: "revoked", base_url: "https://old.example/" },
      ],
    },
    [`GET ${ORG_BASE}/connections/litellm/${CONN}/catalog`]: {
      body: { entries: [{ id: "ft:my-model", status: "callable" }] },
    },
    [`GET ${ORG_BASE}/artifacts`]: {
      body: [
        artifact(ART),
        artifact(ADAPTER, { display_name: "my-adapter" }),
        artifact("aa", { display_name: "unready", status: "registered" }),
        artifact("bb", { display_name: "nolicense", license_accepted_at: null }),
      ],
    },
    [`GET ${VERSIONS}`]: {
      body: [
        { id: BASE, version_number: 1, import_kind: "full_weight", weights_digest: sha("b") },
        { id: UNVALIDATED, version_number: 2, import_kind: "endpoint", weights_digest: null },
      ],
    },
  };
}

beforeEach(() => {
  calls = [];
  routes = defaults();
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({
      method,
      url,
      body: init?.body as string | undefined,
      credentials: init?.credentials,
    });
    const r = routes[`${method} ${url}`];
    const reply = typeof r === "function" ? r() : r;
    if (!reply)
      return Promise.resolve({
        ok: false,
        status: 404,
        json: async () => ({ detail: "not_found" }),
      });
    if (reply.reject) return Promise.reject(new TypeError("Failed to fetch"));
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
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

type Props = Partial<Parameters<typeof ImportModelWizard>[0]>;
function mount(props: Props = {}) {
  return render(
    createElement(ImportModelWizard, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: PROJECT,
      role: "organization_admin",
      ...props,
    }),
  );
}
const posts = () => calls.filter((c) => c.method === "POST");
const listGets = () => calls.filter((c) => c.method === "GET" && c.url === IMPORTS);
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}
const radio = (name: RegExp) => screen.getByRole("radio", { name });
async function pick(origin: RegExp) {
  fireEvent.click(radio(origin));
  fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }));
  await settle();
}
async function fillEndpoint() {
  await pick(/Endpoint model/);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  fireEvent.change(screen.getByLabelText(/Model reference/), { target: { value: "ft:my-model" } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
}
async function toReviewEndpoint() {
  await fillEndpoint();
  await settle();
}
async function confirmAndStart() {
  fireEvent.click(screen.getByTestId("import-confirm"));
  fireEvent.click(screen.getByTestId("import-submit"));
  await settle();
}

// ---- H010P5-A: mount / persisted list ------------------------------------------------------
test("H010P5-A1: jobs are read on mount and survive a reload", async () => {
  routes[`GET ${IMPORTS}`] = { body: [job()] };
  mount();
  await waitFor(() => expect(screen.getByTestId(`job-${JOB}`)).toBeTruthy());
  expect(listGets()).toHaveLength(1);
});

test("H010P5-A2: queued says validation has not started; no progress bar, percentage or ETA", async () => {
  routes[`GET ${IMPORTS}`] = { body: [job()] };
  const { container } = mount();
  await waitFor(() => screen.getByTestId(`job-${JOB}`));
  expect(screen.getByTestId("job-state-text").textContent).toContain(
    "Queued – validation has not started",
  );
  expect(container.querySelector("progress,[role=progressbar]")).toBeNull();
  expect(container.textContent).not.toMatch(/\d+\s?%|ETA/);
  expect(container.textContent).not.toMatch(/Validated/);
});

// ---- H010P5-B: strict parse table (UI CHECKLIST a) ------------------------------------------
const BAD: Record<string, unknown> = {
  id: 5,
  organization_id: null,
  project_id: 7,
  requested_origin: "teleport",
  parent_model_version_id: 3,
  trainable_artifact_id: {},
  external_provider_model_ref: 9,
  litellm_connection_id: [],
  result_model_version_id: 1,
  state: "done",
  failure_code: 4,
  created_by: null,
  created_at: 1,
  updated_at: null,
};
for (const [field, bad] of Object.entries(BAD)) {
  test(`H010P5-B: wrong value for ${field} -> malformed alert, no job rows`, async () => {
    routes[`GET ${IMPORTS}`] = { body: [job({ [field]: bad })] };
    mount();
    const alert = await screen.findByTestId("import-list-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(screen.queryByTestId(`job-${JOB}`)).toBeNull();
  });
}
test("H010P5-B: missing field, foreign org, null item, non-array, malformed json, 500", async () => {
  const noState: Record<string, unknown> = job();
  delete noState.state;
  const cases: Reply[] = [
    { body: [noState] },
    { body: [job({ organization_id: "other" })] },
    { body: [job({ project_id: "other" })] },
    { body: [null] },
    { body: {} },
    { malformed: true },
    { status: 500, body: {} },
    { reject: true },
  ];
  for (const c of cases) {
    cleanup();
    routes[`GET ${IMPORTS}`] = c;
    mount();
    expect((await screen.findByTestId("import-list-error")).getAttribute("role")).toBe("alert");
    expect(screen.queryByTestId(`job-${JOB}`)).toBeNull();
    expect(screen.queryByText(/No imports yet/)).toBeNull();
  }
});
test("H010P5-B: succeeded without a result version is malformed, never success", () => {
  expect(parseJob(job({ state: "succeeded", result_model_version_id: null }))).toBeNull();
  expect(parseJob(job({ state: "succeeded", result_model_version_id: RESULT }))).not.toBeNull();
});

// ---- H010P5-C: endpoint branch ---------------------------------------------------------------
test("H010P5-C1: endpoint form has no artifact/digest/hash field and never shows base_url", async () => {
  const { container } = mount();
  await pick(/Endpoint model/);
  expect(container.textContent).not.toMatch(/secret-gateway|https:\/\//);
  expect(container.textContent).not.toMatch(/digest|hash|sha256/i);
  expect(container.querySelector('[id$="-artifact"]')).toBeNull();
  const options = within(screen.getByLabelText(/Gateway connection/)).getAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual(["Choose a connection", "main-gw"]);
});

test("H010P5-C2: ml_engineer on the endpoint branch sees admin-required, no submit; weights still usable", async () => {
  mount({ role: "ml_engineer" });
  await pick(/Endpoint model/);
  expect(screen.getByText("Requires an organization administrator")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Review import/ })).toBeNull();
  expect(calls.some((c) => c.url.endsWith("/connections/litellm"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: /^Back$/ }));
  await pick(/Full weights/);
  expect(screen.getByLabelText(/Model artifact/)).toBeTruthy();
});

test("H010P5-C3: viewer and evaluator get a permission-denied state with no wizard controls", async () => {
  for (const role of ["viewer", "evaluator"]) {
    cleanup();
    mount({ role });
    await settle();
    expect(screen.getByTestId("import-permission-denied")).toBeTruthy();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByTestId("import-submit")).toBeNull();
  }
});

test("H010P5-C4: a ref is sent verbatim (not trimmed, not case-folded)", async () => {
  const sent = "  Ft:Weird  Ref ";
  routes[`POST ${IMPORTS}`] = {
    status: 201,
    body: job({ external_provider_model_ref: sent }),
  };
  mount();
  await pick(/Endpoint model/);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  fireEvent.change(screen.getByLabelText(/Model reference/), { target: { value: sent } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await confirmAndStart();
  expect(JSON.parse(posts()[0]!.body!)).toEqual({
    requested_origin: "external_endpoint",
    parent_model_version_id: null,
    trainable_artifact_id: null,
    external_provider_model_ref: sent,
    litellm_connection_id: CONN,
  });
});

test("H010P5-C5: field validation -> ErrorSummary takes focus; control chars / length / empty refused", async () => {
  mount();
  await pick(/Endpoint model/);
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await settle();
  const summary = document.querySelector('[role="alert"][tabindex="-1"]') as HTMLElement;
  expect(summary).toBeTruthy();
  expect(document.activeElement).toBe(summary);
  expect(posts()).toHaveLength(0);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  for (const [ref, msg] of [
    ["a".repeat(2049), /longer than 2048/],
    ["bad\u0000ref", /not allowed/],
    ["   ", /Enter the model reference/],
  ] as const) {
    fireEvent.change(screen.getByLabelText(/Model reference/), { target: { value: ref } });
    fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
    expect(await screen.findByText(msg)).toBeTruthy();
    expect(screen.queryByTestId("import-submit")).toBeNull();
  }
  fireEvent.change(screen.getByLabelText(/Model reference/), {
    target: { value: "a".repeat(2048) },
  });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await settle();
  expect(screen.getByTestId("import-submit")).toBeTruthy();
});

// ---- H010P5-D: weights / adapter -------------------------------------------------------------
test("H010P5-D1: weights form shows status, license state, digest in <bdi>; blocked reasons before submit", async () => {
  mount();
  await pick(/Full weights/);
  const select = screen.getByLabelText(/Model artifact/);
  fireEvent.change(select, { target: { value: ART } });
  const facts = screen.getByTestId("artifact-facts");
  expect(facts.textContent).toContain("ready");
  expect(facts.textContent).toContain("accepted");
  expect(facts.querySelector("bdi")?.textContent).toBe("ready");
  expect(facts.textContent).toContain(sha("a"));
  expect(screen.queryByTestId("artifact-blocked-reason")).toBeNull();
  fireEvent.change(select, { target: { value: "aa" } });
  expect(screen.getByTestId("artifact-blocked-reason").textContent).toMatch(/Not ready/);
  fireEvent.change(select, { target: { value: "bb" } });
  expect(screen.getByTestId("artifact-blocked-reason").textContent).toMatch(/License not accepted/);
});

test("H010P5-D2: adapter base list holds only full-weight versions, chosen by exact id", async () => {
  routes[`POST ${IMPORTS}`] = {
    status: 201,
    body: job({
      requested_origin: "imported_adapter",
      trainable_artifact_id: ADAPTER,
      parent_model_version_id: BASE,
      external_provider_model_ref: null,
      litellm_connection_id: null,
    }),
  };
  mount({ role: "ml_engineer" });
  await pick(/Adapter and base/);
  const base = screen.getByLabelText(/Base model version/);
  expect(within(base).getAllByRole("option")).toHaveLength(2);
  expect(base.innerHTML).not.toContain(UNVALIDATED);
  fireEvent.change(screen.getByLabelText(/Adapter artifact/), { target: { value: ADAPTER } });
  fireEvent.change(base, { target: { value: BASE } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await confirmAndStart();
  const body = JSON.parse(posts()[0]!.body!);
  expect(body).toEqual({
    requested_origin: "imported_adapter",
    parent_model_version_id: BASE,
    trainable_artifact_id: ADAPTER,
    external_provider_model_ref: null,
    litellm_connection_id: null,
  });
  expect(JSON.stringify(body)).not.toContain("base_model_name_or_path");
});

test("H010P5-D3: adapter without a base is refused before any request", async () => {
  mount({ role: "ml_engineer" });
  await pick(/Adapter and base/);
  fireEvent.change(screen.getByLabelText(/Adapter artifact/), { target: { value: ADAPTER } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await settle();
  expect(screen.queryByTestId("import-submit")).toBeNull();
  expect(posts()).toHaveLength(0);
});

// ---- H010P5-E: review + submit ---------------------------------------------------------------
test("H010P5-E1: review shows summary, security profile (read-only), lineage Unknown/Partial; submit needs the confirm", async () => {
  mount();
  await toReviewEndpoint();
  expect(screen.getByTestId("import-security-profile").textContent).toBe("standard");
  expect(screen.getByTestId("import-expected-lineage").textContent).toBe("Unknown");
  const submit = screen.getByTestId("import-submit") as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  expect(screen.getByTestId("disabled-reason")).toBeTruthy();
  fireEvent.click(submit);
  expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByTestId("import-confirm"));
  expect(submit.disabled).toBe(false);
});

test("H010P5-E2: lineage is Partial for weights", async () => {
  mount();
  await pick(/Full weights/);
  fireEvent.change(screen.getByLabelText(/Model artifact/), { target: { value: ART } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await settle();
  expect(screen.getByTestId("import-expected-lineage").textContent).toBe("Partial");
});

test("H010P5-E3: a bound 201 adds the queued job, queued text, never success", async () => {
  routes[`POST ${IMPORTS}`] = { status: 201, body: job() };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  expect(posts()).toHaveLength(1);
  expect(screen.getByTestId(`job-${JOB}`).getAttribute("data-state")).toBe("queued");
  expect(screen.getByTestId("import-notice").textContent).toMatch(/queued/);
  expect(screen.queryByText(/Validated/)).toBeNull();
});

test("H010P5-E4: a double click sends one POST", async () => {
  routes[`POST ${IMPORTS}`] = { status: 201, body: job() };
  mount();
  await toReviewEndpoint();
  fireEvent.click(screen.getByTestId("import-confirm"));
  const submit = screen.getByTestId("import-submit") as HTMLButtonElement;
  await act(async () => {
    submit.click();
    submit.click();
  });
  await settle();
  expect(posts()).toHaveLength(1);
});

// ---- H010P5-F: lost response / unknown outcome (never a blind re-POST) --------------------------
test("H010P5-F1: network failure on POST -> re-list, found job shown, exactly one POST", async () => {
  routes[`POST ${IMPORTS}`] = { reject: true };
  mount();
  await toReviewEndpoint();
  routes[`GET ${IMPORTS}`] = { body: [job({ id: "new-job" })] };
  await confirmAndStart();
  await waitFor(() => screen.getByTestId("job-new-job"));
  expect(posts()).toHaveLength(1);
  expect(screen.getByTestId("import-notice").textContent).toMatch(/was created/);
});

test("H010P5-F2: lost response with no matching job -> resubmit only by an explicit click", async () => {
  routes[`POST ${IMPORTS}`] = { reject: true };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  expect(posts()).toHaveLength(1);
  expect(screen.getByTestId("import-notice").textContent).toMatch(/No import was created/);
  // No automatic retry happened; the wizard is still on review with the form intact.
  await settle();
  expect(posts()).toHaveLength(1);
  routes[`POST ${IMPORTS}`] = { status: 201, body: job() };
  fireEvent.click(screen.getByTestId("import-submit"));
  await settle();
  expect(posts()).toHaveLength(2);
});

test("H010P5-F3: a 201 that does not bind to what was sent is not success (verify, no POST #2)", async () => {
  routes[`POST ${IMPORTS}`] = { status: 201, body: job({ external_provider_model_ref: "other" }) };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  expect(posts()).toHaveLength(1);
  expect(screen.queryByTestId(`job-${JOB}`)).toBeNull();
  expect(screen.getByTestId("import-submit-error").getAttribute("role")).toBe("alert");
});

test("H010P5-F4: 200 (not 201), non-JSON 2xx and {} are not success", async () => {
  for (const reply of [
    { status: 200, body: job() },
    { status: 201, malformed: true },
    { status: 201, body: {} },
    { status: 201, body: job({ state: "succeeded", result_model_version_id: RESULT }) },
  ] as Reply[]) {
    cleanup();
    calls = [];
    routes[`POST ${IMPORTS}`] = reply;
    mount();
    await toReviewEndpoint();
    await confirmAndStart();
    await settle();
    expect(posts()).toHaveLength(1);
    expect(screen.queryByTestId(`job-${JOB}`)).toBeNull();
    expect(screen.getByTestId("import-submit-error").getAttribute("role")).toBe("alert");
  }
});

test("H010P5-F5: 5xx keeps the form, announces role=alert, verifies, never auto-retries", async () => {
  routes[`POST ${IMPORTS}`] = { status: 503, body: {} };
  mount();
  await toReviewEndpoint();
  const before = listGets().length;
  await confirmAndStart();
  await settle();
  expect(posts()).toHaveLength(1);
  expect(listGets().length).toBeGreaterThan(before);
  expect(screen.getByTestId("import-submit-error").getAttribute("role")).toBe("alert");
  expect(screen.getByTestId("import-submit")).toBeTruthy();
});

test("H010P5-F6: 403/404/400 are shown as alerts and take focus", async () => {
  for (const [status, detail] of [
    [403, "forbidden"],
    [404, "not_found"],
    [400, "invalid_body"],
  ] as const) {
    cleanup();
    routes[`POST ${IMPORTS}`] = { status, body: { detail } };
    mount();
    await toReviewEndpoint();
    await confirmAndStart();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5));
    });
    const alert = screen.getByTestId("import-submit-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(document.activeElement).toBe(alert);
  }
});

// ---- H010P5-G: job states --------------------------------------------------------------------
test("H010P5-G1: blocked_external is never shown as failed or succeeded", async () => {
  routes[`GET ${IMPORTS}`] = {
    body: [job({ state: "blocked_external", failure_code: "metadata_files_unavailable" })],
  };
  const { container } = mount();
  await waitFor(() => screen.getByTestId(`job-${JOB}`));
  const li = screen.getByTestId(`job-${JOB}`);
  expect(li.textContent).toMatch(/Blocked/);
  expect(li.textContent).toMatch(/metadata files/);
  expect(li.textContent).not.toMatch(/failed|Validated/i);
  expect(container.querySelector('[data-status="failed"]')).toBeNull();
});

test("H010P5-G2: an unknown failure code renders 'Validation failed' with the code in <bdi>", async () => {
  routes[`GET ${IMPORTS}`] = { body: [job({ state: "failed", failure_code: "brand_new_code" })] };
  mount();
  await waitFor(() => screen.getByTestId(`job-${JOB}`));
  const li = screen.getByTestId(`job-${JOB}`);
  expect(li.textContent).toMatch(/Validation failed/);
  expect(
    Array.from(li.querySelectorAll("bdi")).some((b) => b.textContent === "brand_new_code"),
  ).toBe(true);
  expect(li.textContent).not.toMatch(/Validated/);
  expect(failureText(null, "en")).toBe("Validation failed");
});

test("H010P5-G3: failed with a null code is still failed, never empty or success", async () => {
  routes[`GET ${IMPORTS}`] = { body: [job({ state: "failed", failure_code: null })] };
  mount();
  await waitFor(() => screen.getByTestId(`job-${JOB}`));
  expect(screen.getByTestId("job-state-text").textContent).toMatch(/Validation failed/);
});

test("H010P5-G4: succeeded exposes the result version via onModelVersion, no digest for an endpoint", async () => {
  routes[`GET ${IMPORTS}`] = {
    body: [job({ state: "succeeded", result_model_version_id: RESULT })],
  };
  const onModelVersion = vi.fn();
  const { container } = mount({ onModelVersion });
  await waitFor(() => screen.getByTestId(`job-${JOB}`));
  fireEvent.click(screen.getByRole("button", { name: /View model version/ }));
  expect(onModelVersion).toHaveBeenCalledWith(RESULT);
  expect(container.textContent).not.toMatch(/[0-9a-f]{64}/);
});

test("H010P5-G5: every state renders a distinct label", async () => {
  const states = IMPORT_JOB_STATES.map((s, i) =>
    job({
      id: `j${i}`,
      state: s,
      failure_code:
        s === "failed"
          ? "digest_mismatch"
          : s === "blocked_external"
            ? "gateway_unavailable"
            : null,
      result_model_version_id: s === "succeeded" ? RESULT : null,
    }),
  );
  routes[`GET ${IMPORTS}`] = { body: states };
  mount();
  await waitFor(() => screen.getByTestId("job-j0"));
  const texts = IMPORT_JOB_STATES.map((_, i) => screen.getByTestId(`job-j${i}`).textContent);
  expect(new Set(texts).size).toBe(5);
});

// ---- H010P5-H: polling -----------------------------------------------------------------------
test("H010P5-H1: polling backs off 2s -> 30s cap and stops at a terminal state", async () => {
  vi.useFakeTimers();
  let n = 0;
  routes[`GET ${IMPORTS}`] = () => {
    n += 1;
    return {
      body: [
        job({
          state: n >= 7 ? "failed" : "queued",
          failure_code: n >= 7 ? "digest_mismatch" : null,
        }),
      ],
    };
  };
  mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(listGets()).toHaveLength(1);
  const delays = [
    POLL_START_MS,
    POLL_START_MS * 2,
    POLL_START_MS * 4,
    POLL_START_MS * 8,
    POLL_MAX_MS,
    POLL_MAX_MS,
  ];
  let expected = 1;
  for (const d of delays) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(d - 1);
    });
    expect(listGets()).toHaveLength(expected);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expected += 1;
    expect(listGets()).toHaveLength(expected);
  }
  expect(screen.getByTestId(`job-${JOB}`).getAttribute("data-state")).toBe("failed");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5 * POLL_MAX_MS);
  });
  expect(listGets()).toHaveLength(expected);
  expect(screen.getByTestId("import-announce").getAttribute("aria-live")).toBe("polite");
});

test("H010P5-H2: no polling when every job is already terminal", async () => {
  vi.useFakeTimers();
  routes[`GET ${IMPORTS}`] = { body: [job({ state: "failed", failure_code: "digest_mismatch" })] };
  mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5 * POLL_MAX_MS);
  });
  expect(listGets()).toHaveLength(1);
});

// ---- H010P5-I: bilingual -----------------------------------------------------------------------
test("H010P5-I1: every failure code has en and ar text; ar shows no English fallback", () => {
  for (const [code, m] of Object.entries(FAILURE_MESSAGES)) {
    expect(m.en.length, code).toBeGreaterThan(0);
    expect(m.ar.length, code).toBeGreaterThan(0);
    expect(m.ar, code).not.toMatch(/[A-Za-z]/);
    expect(failureText(code, "ar")).toBe(m.ar);
  }
});

test("H010P5-I2: under ar the whole screen has lang/dir and no Latin UI text (user data in bdi)", async () => {
  routes[`GET ${IMPORTS}`] = {
    body: [
      job({ state: "failed", failure_code: "digest_mismatch" }),
      job({ id: "j2", state: "queued" }),
      job({ id: "j3", state: "failed", failure_code: "unmapped_code" }),
    ],
  };
  const { container } = mount({ locale: "ar" });
  await waitFor(() => screen.getByTestId(`job-${JOB}`));
  const root = screen.getByTestId("import-model-wizard");
  expect(root.getAttribute("lang")).toBe("ar");
  expect(root.getAttribute("dir")).toBe("rtl");
  const clone = container.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("bdi").forEach((b) => b.remove());
  expect(clone.textContent).not.toMatch(/[A-Za-z]{3,}/);
});

test("H010P5-I3: under ar the wizard steps, errors and review have no English", async () => {
  mount({ locale: "ar" });
  fireEvent.click(radio(/نقطة اتصال/));
  fireEvent.click(screen.getByRole("button", { name: "متابعة" }));
  await settle();
  fireEvent.change(screen.getByLabelText(/اتصال البوابة/), { target: { value: CONN } });
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "مراجعة الاستيراد" }));
  await settle();
  const root = screen.getByTestId("import-model-wizard");
  const clone = root.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("bdi,option,input").forEach((b) => b.remove());
  expect(clone.textContent).not.toMatch(/[A-Za-z]{3,}/);
});

// ---- H010P5-J: identity / binding ---------------------------------------------------------------
test("H010P5-J1: changing project remounts: drafts reset and the old project's reply is ignored", async () => {
  const other = "abababab-abab-abab-abab-abababababab";
  const { rerender } = mount();
  await pick(/Full weights/);
  fireEvent.change(screen.getByLabelText(/Model artifact/), { target: { value: ART } });
  routes[`GET ${ORG_BASE}/projects/${other}/model-imports`] = { body: [] };
  rerender(
    createElement(ImportModelWizard, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: other,
      role: "organization_admin",
    }),
  );
  await settle();
  expect(screen.getByLabelText(/Full weights/)).toBeTruthy();
  expect((screen.getByRole("radio", { name: /Full weights/ }) as HTMLInputElement).checked).toBe(
    false,
  );
  expect(window.localStorage.length).toBe(0);
});

test("H010P5-J2: bindsTo compares every sent field", () => {
  const sent = {
    requested_origin: "external_endpoint" as const,
    parent_model_version_id: null,
    trainable_artifact_id: null,
    external_provider_model_ref: "ft:my-model",
    litellm_connection_id: CONN,
  };
  const j = parseJob(job())!;
  expect(bindsTo(j, sent, ORG, PROJECT)).toBe(true);
  for (const [k, v] of Object.entries({
    organization_id: "x",
    project_id: "x",
    requested_origin: "imported_weights",
    parent_model_version_id: "x",
    trainable_artifact_id: "x",
    external_provider_model_ref: "x",
    litellm_connection_id: "x",
  })) {
    expect(bindsTo({ ...j, [k]: v } as never, sent, ORG, PROJECT), k).toBe(false);
  }
});

test("H010P5-J3: options load failures are alerts with a retry; empty lists explain themselves", async () => {
  routes[`GET ${ORG_BASE}/artifacts`] = { status: 500, body: {} };
  mount();
  await pick(/Full weights/);
  expect((await screen.findByTestId("import-options-error")).getAttribute("role")).toBe("alert");
  cleanup();
  routes[`GET ${ORG_BASE}/artifacts`] = { body: [] };
  mount();
  await pick(/Full weights/);
  expect(screen.getByText("No artifacts registered")).toBeTruthy();
  cleanup();
  routes[`GET ${ORG_BASE}/artifacts`] = { body: [{ id: 3 }] };
  mount();
  await pick(/Full weights/);
  expect((await screen.findByTestId("import-options-error")).getAttribute("role")).toBe("alert");
});

test("H010P5-J4: empty job list shows the empty state, not a fake job", async () => {
  mount();
  expect(await screen.findByText("No imports yet")).toBeTruthy();
});

// ---- H010P5-K: wrap (the real-layout overflow proof is in the Chromium spec) ----------------------
test("H010P5-K1: the root and every job row wrap long unbroken values", async () => {
  routes[`GET ${IMPORTS}`] = { body: [job({ external_provider_model_ref: "x".repeat(2048) })] };
  mount();
  await waitFor(() => screen.getByTestId(`job-${JOB}`));
  expect(screen.getByTestId("import-model-wizard").style.overflowWrap).toBe("anywhere");
  expect(screen.getByTestId(`job-${JOB}`).style.overflowWrap).toBe("anywhere");
});

test("H010P5-K2: every request (reads and the write) sends the session cookie", async () => {
  routes[`POST ${IMPORTS}`] = { status: 201, body: job() };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  expect(calls.length).toBeGreaterThanOrEqual(5);
  for (const c of calls) expect(c.credentials, `${c.method} ${c.url}`).toBe("include");
});
