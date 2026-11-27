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
const FW_UNVALIDATED = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const BASE_JOB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
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
  source_type: "local_filesystem",
  status: "ready",
  license_id: "apache-2.0",
  license_accepted_at: "2026-10-01T00:00:00+00:00",
  weights_digest: sha("a"),
  ...extra,
});

type Reply = {
  status?: number;
  body?: unknown;
  malformed?: boolean;
  reject?: boolean;
  hang?: boolean;
};
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
        // An FTF-1001 synchronous full_weight row that no succeeded import job produced.
        {
          id: FW_UNVALIDATED,
          version_number: 3,
          import_kind: "full_weight",
          weights_digest: sha("c"),
        },
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
    expect((await screen.findAllByText(msg)).length).toBeGreaterThanOrEqual(2); // summary + inline
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

const baseJob = () =>
  job({
    id: BASE_JOB,
    requested_origin: "imported_weights",
    trainable_artifact_id: ART,
    external_provider_model_ref: null,
    litellm_connection_id: null,
    state: "succeeded",
    result_model_version_id: BASE,
  });

test("H010P5-D2: adapter base list holds only versions a succeeded weights job produced, by exact id", async () => {
  routes[`GET ${IMPORTS}`] = { body: [baseJob()] };
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
  // BASE-LIST-UNVALIDATED: an unvalidated full_weight row is never offered.
  expect(base.innerHTML).not.toContain(FW_UNVALIDATED);
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

// ---- H010P5-R1: independent pre-review round 1 (FTF-H010-P5-r1) -----------------------------
test("H010P5-R1-1: an unvalidated full_weight row is never a base; a succeeded weights job's version is", async () => {
  // No job produced BASE or FW_UNVALIDATED: the base list is empty, not "every full_weight".
  mount({ role: "ml_engineer" });
  await pick(/Adapter and base/);
  expect(screen.queryByLabelText(/Base model version/)).toBeNull();
  expect(screen.getByText(/No validated base versions/)).toBeTruthy();
});

test("H010P5-R1-2: submit is blocked until the job list has loaded (no false 'created' after a lost reply)", async () => {
  let lists = 0;
  routes[`GET ${IMPORTS}`] = () => {
    lists += 1;
    // First list GET rejected; later ones return an OLDER failed job for the same ref/connection.
    return lists === 1
      ? { reject: true }
      : { body: [job({ id: "old", state: "failed", failure_code: "digest_mismatch" })] };
  };
  routes[`POST ${IMPORTS}`] = { reject: true };
  mount();
  await toReviewEndpoint();
  fireEvent.click(screen.getByTestId("import-confirm"));
  const submit = screen.getByTestId("import-submit") as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.click(submit);
  await settle();
  expect(posts()).toHaveLength(0);
  expect(screen.queryByText(/The import was created/)).toBeNull();
  // Reload the jobs: the baseline now exists and submit is available again.
  fireEvent.click(screen.getByRole("button", { name: /Reload jobs/ }));
  await settle();
  expect((screen.getByTestId("import-submit") as HTMLButtonElement).disabled).toBe(false);
});

test("H010P5-R1-3: blocked_external with an unknown code never says 'failed' and shows the code in <bdi>", async () => {
  routes[`GET ${IMPORTS}`] = {
    body: [job({ state: "blocked_external", failure_code: "brand_new_block" })],
  };
  mount();
  await waitFor(() => screen.getByTestId(`job-${JOB}`));
  const li = screen.getByTestId(`job-${JOB}`);
  expect(li.textContent).toMatch(/Blocked/);
  expect(li.textContent).not.toMatch(/failed/i);
  expect(
    Array.from(li.querySelectorAll("bdi")).some((b) => b.textContent === "brand_new_block"),
  ).toBe(true);
});

test("H010P5-R1-4: field errors are inline on the UX01 Field and on selects, wired by aria-describedby", async () => {
  mount();
  await pick(/Endpoint model/);
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await settle();
  const sel = screen.getByLabelText(/Gateway connection/);
  const selErr = screen.getByTestId("connection-error");
  expect(sel.getAttribute("aria-invalid")).toBe("true");
  expect(sel.getAttribute("aria-describedby")).toBe(selErr.id);
  fireEvent.change(sel, { target: { value: CONN } });
  await settle();
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await settle();
  const input = screen.getByLabelText(/Model reference/);
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect(input.getAttribute("aria-required")).toBe("true");
  const ids = (input.getAttribute("aria-describedby") ?? "").split(" ");
  expect(
    ids.some((id) => document.getElementById(id)?.textContent === "Enter the model reference."),
  ).toBe(true);
  // artifact select
  cleanup();
  mount();
  await pick(/Full weights/);
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await settle();
  const art = screen.getByLabelText(/Model artifact/);
  expect(art.getAttribute("aria-describedby")).toBe(screen.getByTestId("artifact-error").id);
});

test("H010P5-R1-5: after a created import, and after a found-after-lost-reply, focus is on the notice, never <body>", async () => {
  routes[`POST ${IMPORTS}`] = { status: 201, body: job({ id: "new1" }) };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  expect(document.activeElement).toBe(screen.getByTestId("import-notice"));
  cleanup();

  let lists = 0;
  routes[`GET ${IMPORTS}`] = () => {
    lists += 1;
    // 1 = mount, 2 = fresh baseline before the POST, 3 = the verify re-list.
    return { body: lists <= 2 ? [] : [job({ id: "new2" })] };
  };
  routes[`POST ${IMPORTS}`] = { reject: true };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  expect(screen.getByTestId("import-notice").textContent).toMatch(/was created/);
  expect(document.activeElement).toBe(screen.getByTestId("import-notice"));
});

test("H010P5-R1-6: a rejected, failed or malformed project GET shows an announced alert, not a silent 'Unavailable'", async () => {
  for (const reply of [
    { reject: true },
    { status: 500, body: {} },
    { malformed: true },
    { body: {} },
  ]) {
    routes[`GET ${ORG_BASE}/projects/${PROJECT}`] = reply;
    const { unmount } = mount();
    await settle();
    await settle();
    const alert = screen.getByTestId("import-profile-error");
    expect(alert.getAttribute("role")).toBe("alert");
    unmount();
  }
});

test("H010P5-R1-7: the ref input is described by its help text (aria-describedby pinned)", async () => {
  mount();
  await pick(/Endpoint model/);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  const input = screen.getByLabelText(/Model reference/);
  const ids = (input.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean);
  expect(ids.length).toBeGreaterThan(0);
  expect(ids.map((id) => document.getElementById(id)?.textContent ?? "").join(" ")).toMatch(
    /Sent exactly as typed/,
  );
});

test("H010P5-R1-8: the ref check matches the server: code points, astral accepted, lone surrogate and control refused", async () => {
  mount();
  await pick(/Endpoint model/);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  const review = () => fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  const setRef = (v: string) =>
    fireEvent.change(screen.getByLabelText(/Model reference/), { target: { value: v } });
  setRef("ft:\u{20000}-model");
  review();
  await settle();
  expect(screen.getByTestId("import-submit")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /^Back$/ }));
  await settle();
  setRef("\u{20000}".repeat(2048)); // 2048 code points, 4096 UTF-16 units
  review();
  await settle();
  expect(screen.getByTestId("import-submit")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /^Back$/ }));
  await settle();
  setRef("\u{20000}".repeat(2049));
  review();
  await settle();
  expect(screen.queryByTestId("import-submit")).toBeNull();
  setRef("bad\ud800ref");
  review();
  await settle();
  expect(screen.queryByTestId("import-submit")).toBeNull();
});

test("H010P5-R1-9: a failed poll keeps polling with backoff; the error is shown; it stops at a terminal state", async () => {
  vi.useFakeTimers();
  let n = 0;
  routes[`GET ${IMPORTS}`] = () => {
    n += 1;
    if (n === 1) return { body: [job()] };
    if (n <= 3) return { reject: true };
    return { body: [job({ state: "failed", failure_code: "digest_mismatch" })] };
  };
  mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_START_MS * 2 + 1);
  });
  expect(screen.getByTestId("import-list-error")).toBeTruthy();
  // One act per step: React flushes the re-arm at act exit.
  for (let i = 0; i < 6; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MAX_MS);
    });
  }
  expect(listGets().length).toBeGreaterThanOrEqual(4);
  expect(screen.getByTestId(`job-${JOB}`).getAttribute("data-state")).toBe("failed");
  const settled = listGets().length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5 * POLL_MAX_MS);
  });
  expect(listGets()).toHaveLength(settled);
});

// ---- H010P5-R2: pre-review round 2 --------------------------------------------------------
test("H010P5-R2-1: the baseline is re-read right before the POST; another user's identical job never counts as 'created'", async () => {
  let lists = 0;
  routes[`GET ${IMPORTS}`] = () => {
    lists += 1;
    // Mount: empty. Before the POST: another user's identical (failed) job has appeared.
    return {
      body: lists === 1 ? [] : [job({ id: "theirs", state: "failed", created_by: "user-2" })],
    };
  };
  routes[`POST ${IMPORTS}`] = { reject: true };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  expect(screen.getByTestId("import-notice").textContent).toMatch(/No import was created/);
  expect(screen.queryByText(/^The import was created/)).toBeNull();
  expect(posts()).toHaveLength(1);
  expect(lists).toBe(3);
});

test("H010P5-R2-1b: if the fresh baseline cannot be read, nothing is POSTed and an alert is announced", async () => {
  let lists = 0;
  routes[`GET ${IMPORTS}`] = () => {
    lists += 1;
    return lists === 1 ? { body: [] } : { reject: true };
  };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  expect(posts()).toHaveLength(0);
  expect(screen.getByTestId("import-submit-error").getAttribute("role")).toBe("alert");
});

test("H010P5-R2-2: a job that already existed before the submit is never taken as the created one", async () => {
  // The same job id is in the baseline (mount + fresh list) and in the verify re-list.
  routes[`GET ${IMPORTS}`] = { body: [job({ id: "old1" })] };
  routes[`POST ${IMPORTS}`] = { reject: true };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  expect(screen.getByTestId("import-notice").textContent).toMatch(/No import was created/);
});

test("H010P5-R2-3: a failed job-list GET shows an error and retry for the base list, never the empty state", async () => {
  routes[`GET ${IMPORTS}`] = { status: 500, body: { detail: "boom" } };
  mount({ role: "ml_engineer" });
  await pick(/Adapter and base/);
  await settle();
  expect(screen.getByTestId("import-bases-error").getAttribute("role")).toBe("alert");
  expect(screen.queryByText(/No validated base versions/)).toBeNull();
  expect(screen.getAllByRole("button", { name: /Reload jobs/ }).length).toBeGreaterThan(0);
});

test("H010P5-R2-4: stale/removed catalog entries are not offered; a catalog error has a retry", async () => {
  routes[`GET ${ORG_BASE}/connections/litellm/${CONN}/catalog`] = {
    body: {
      entries: [
        { id: "ft:a", status: "callable" },
        { id: "ft:dead", status: "removed" },
        { id: "ft:old", status: "stale" },
      ],
    },
  };
  mount();
  await pick(/Endpoint model/);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  const sel = screen.getByLabelText(/Model from the connection catalog/);
  expect(sel.innerHTML).toContain("ft:a");
  expect(sel.innerHTML).not.toContain("ft:dead");
  expect(sel.innerHTML).not.toContain("ft:old");
  cleanup();

  routes[`GET ${ORG_BASE}/connections/litellm/${CONN}/catalog`] = { status: 500, body: {} };
  mount();
  await pick(/Endpoint model/);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  expect(screen.getByTestId("import-catalog-error")).toBeTruthy();
  routes[`GET ${ORG_BASE}/connections/litellm/${CONN}/catalog`] = {
    body: { entries: [{ id: "ft:a", status: "callable" }] },
  };
  fireEvent.click(screen.getByRole("button", { name: /Reload jobs/ }));
  await settle();
  expect(screen.queryByTestId("import-catalog-error")).toBeNull();
});

test("H010P5-R2-5: the import notice is a role=status live region", async () => {
  routes[`POST ${IMPORTS}`] = { status: 201, body: job({ id: "new1" }) };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  expect(screen.getByTestId("import-notice").getAttribute("role")).toBe("status");
});

test("H010P5-R2-6: the endpoint branch offers no input other than connection / catalog / ref (aria-label-only digest fields included)", async () => {
  mount();
  await pick(/Endpoint model/);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  const root = screen.getByTestId("import-model-wizard");
  const controls = Array.from(root.querySelectorAll("input, textarea, select")).filter(
    (el) =>
      (el as HTMLInputElement).type !== "radio" && (el as HTMLInputElement).type !== "checkbox",
  );
  // connection select, catalog select, ref input - nothing else.
  expect(controls).toHaveLength(3);
  expect(root.querySelector("input[aria-label], textarea[aria-label]")).toBeNull();
});

test("H010P5-R2-8: the base select shows its inline error wired by aria-describedby", async () => {
  routes[`GET ${IMPORTS}`] = { body: [baseJob()] };
  mount({ role: "ml_engineer" });
  await pick(/Adapter and base/);
  fireEvent.change(screen.getByLabelText(/Adapter artifact/), { target: { value: ADAPTER } });
  fireEvent.click(screen.getByRole("button", { name: /Review import/ }));
  await settle();
  const base = screen.getByLabelText(/Base model version/);
  expect(base.getAttribute("aria-invalid")).toBe("true");
  expect(base.getAttribute("aria-describedby")).toBe(screen.getByTestId("base-error").id);
  expect(screen.getByTestId("base-error").textContent).not.toBe("");
});

test("H010P5-R2-9: 'status updated' is announced only when a job changed; a created job is listed after the older ones", async () => {
  vi.useFakeTimers();
  let lists = 0;
  routes[`GET ${IMPORTS}`] = () => {
    lists += 1;
    return { body: [job({ id: "j1", state: lists >= 3 ? "validating" : "queued" })] };
  };
  mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  // First poll: nothing changed -> no announcement.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(lists).toBe(2);
  expect(screen.getByTestId("import-announce").textContent).toBe("");
  // Second poll: the state changed -> announced.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(screen.getByTestId("import-announce").textContent).toContain("Import status updated.");
});

test("H010P5-R2-9b: a newly created job is listed after the older ones (the server orders ascending by created_at)", async () => {
  routes[`GET ${IMPORTS}`] = { body: [job({ id: "old1" })] };
  routes[`POST ${IMPORTS}`] = { status: 201, body: job({ id: "new1" }) };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  const ids = screen
    .getAllByTestId(/^job-/)
    .map((e) => e.getAttribute("data-testid"))
    .filter((t) => t === "job-old1" || t === "job-new1");
  expect(ids).toEqual(["job-old1", "job-new1"]);
});

// ---- H010P5-R3: review round-3 blockers ------------------------------------------------------
test("H010P5-R3-1: the live region is mounted from the start and every transition changes its text", async () => {
  vi.useFakeTimers();
  const seq = ["queued", "validating", "failed"];
  let lists = 0;
  routes[`GET ${IMPORTS}`] = () => {
    const state = seq[Math.min(lists, seq.length - 1)] as string;
    lists += 1;
    return {
      body: [
        job({
          id: "j1",
          state,
          failure_code: state === "failed" ? "metadata_files_unavailable" : null,
        }),
      ],
    };
  };
  mount();
  const region = () => screen.getByTestId("import-announce");
  expect(region().getAttribute("role")).toBe("status");
  expect(region().getAttribute("aria-live")).toBe("polite");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  const first = region().textContent;
  expect(first).toContain("Validating");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  const second = region().textContent;
  expect(second).toContain("Validation failed");
  expect(second).not.toBe(first);
});

test("H010P5-R3-2: while the jobs GET is pending the adapter base list shows loading, never an invented error", async () => {
  routes[`GET ${IMPORTS}`] = { hang: true };
  mount();
  fireEvent.click(radio(/Adapter/));
  fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }));
  await settle();
  expect(screen.queryAllByRole("alert")).toHaveLength(0);
  expect(screen.queryByTestId("import-bases-error")).toBeNull();
  expect(screen.getByTestId("import-bases-loading").getAttribute("role")).toBe("status");
});

test("H010P5-R3-3: options and catalog show a loading status while their GET is pending", async () => {
  routes[`GET ${ORG_BASE}/artifacts`] = { hang: true };
  mount();
  await pick(/Full weights/);
  expect(screen.queryByTestId("import-options-error")).toBeNull();
  expect(screen.getByText("Loading…")).toBeTruthy();
  cleanup();
  routes = defaults();
  routes[`GET ${ORG_BASE}/connections/litellm/${CONN}/catalog`] = { hang: true };
  mount();
  await pick(/Endpoint model/);
  fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
  await settle();
  expect(screen.queryByTestId("import-catalog-error")).toBeNull();
  expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
});

test("H010P5-R3-4: a 5xx POST that committed is found by the verify read, not left open for a duplicate", async () => {
  let committed = false;
  routes[`GET ${IMPORTS}`] = () => ({ body: committed ? [job({ id: "created1" })] : [] });
  routes[`POST ${IMPORTS}`] = () => {
    committed = true;
    return { status: 503, body: {} };
  };
  mount();
  await toReviewEndpoint();
  await confirmAndStart();
  await settle();
  await settle();
  expect(posts()).toHaveLength(1);
  expect(screen.getByTestId("job-created1")).toBeTruthy();
});

test("H010P5-R3-5: identity change in organization or role fully remounts and drops the draft", async () => {
  for (const change of [
    { organizationId: "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd" },
    { role: "ml_engineer" },
  ]) {
    cleanup();
    const { rerender } = mount();
    await pick(/Full weights/);
    expect(screen.getByLabelText(/Model artifact/)).toBeTruthy();
    rerender(
      createElement(ImportModelWizard, {
        apiBaseUrl: API,
        organizationId: ORG,
        projectId: PROJECT,
        role: "organization_admin",
        ...change,
      }),
    );
    await settle();
    expect(screen.queryByLabelText(/Model artifact/)).toBeNull();
    if ("role" in change)
      expect(
        (screen.getByRole("radio", { name: /Full weights/ }) as HTMLInputElement).checked,
      ).toBe(false);
  }
});

test("H010P5-R3-6: polling stops at blocked_external (terminal) and never backs off into a hammer", async () => {
  vi.useFakeTimers();
  let lists = 0;
  routes[`GET ${IMPORTS}`] = () => {
    lists += 1;
    return {
      body: [job({ state: "blocked_external", failure_code: "metadata_files_unavailable" })],
    };
  };
  mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
  });
  expect(lists).toBe(1);
});

// UI CHECKLIST (a): a generated strict-parse table over every response the wizard reads.
const TABLE: {
  name: string;
  url: string;
  open: () => Promise<void>;
  good: unknown;
  alertId: string;
}[] = [
  {
    name: "connections",
    url: `GET ${ORG_BASE}/connections/litellm`,
    good: [{ id: CONN, name: "main-gw", status: "active", base_url: "https://x/" }],
    open: async () => {
      await pick(/Endpoint model/);
    },
    alertId: "import-options-error",
  },
  {
    name: "artifacts",
    url: `GET ${ORG_BASE}/artifacts`,
    good: [artifact(ART)],
    open: async () => {
      await pick(/Full weights/);
    },
    alertId: "import-options-error",
  },
  {
    name: "model-versions",
    url: `GET ${VERSIONS}`,
    good: [{ id: BASE, version_number: 1, import_kind: "full_weight", weights_digest: sha("b") }],
    open: async () => {
      await pick(/Adapter/);
    },
    alertId: "import-options-error",
  },
];
const WRONG: unknown[] = [5, null, [], {}, true];
const ENUM_BAD: Record<string, [string, unknown]> = {
  connections: ["status", "paused"],
  artifacts: ["status", "deleted"],
  "artifacts#source_type": ["source_type", "ftp"],
  "model-versions": ["import_kind", "teleport"],
};
for (const t of TABLE) {
  const first = (t.good as Record<string, unknown>[])[0] as Record<string, unknown>;
  const mutations: [string, unknown][] = [];
  for (const field of Object.keys(first)) {
    if (field === "base_url") continue; // deliberately never read
    for (const w of WRONG) {
      if (w === 5 && field === "version_number") continue; // 5 is a valid number
      mutations.push([field, w]);
    }
    mutations.push([field, undefined]);
  }
  mutations.push(ENUM_BAD[t.name] as [string, unknown]);
  const extra = ENUM_BAD[`${t.name}#source_type`];
  if (extra) mutations.push(extra);
  for (const [field, bad] of mutations) {
    // null is a valid value for the nullable fields: skip exactly those.
    if (bad === null && ["license_id", "license_accepted_at", "weights_digest"].includes(field))
      continue;
    test(`H010P5-R3-B ${t.name}.${field} = ${JSON.stringify(bad)} -> options alert, nothing offered`, async () => {
      const item = { ...first, [field]: bad };
      if (bad === undefined) delete (item as Record<string, unknown>)[field];
      routes[t.url] = { body: [item] };
      mount();
      await t.open();
      await settle();
      await settle();
      const alerts = screen.queryAllByTestId(
        /import-(options|bases)-error|base-error|artifact-error/,
      );
      expect(alerts.length, `${t.name}.${field}`).toBeGreaterThan(0);
    });
  }
}
test("H010P5-R3-B catalog: wrong types / missing fields -> catalog alert", async () => {
  for (const entry of [
    { id: 5, status: "callable" },
    { id: "ft:a", status: 7 },
    { status: "callable" },
    { id: "ft:a" },
    { id: "ft:a", status: "CALLABLE" },
    { id: "ft:a", status: "bogus" },
    null,
  ]) {
    cleanup();
    routes[`GET ${ORG_BASE}/connections/litellm/${CONN}/catalog`] = { body: { entries: [entry] } };
    mount();
    await pick(/Endpoint model/);
    fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
    await settle();
    expect(screen.getByTestId("import-catalog-error")).toBeTruthy();
  }
});

test("H010P5-R3-B catalog: entries not an array / body not an object -> catalog alert", async () => {
  for (const body of [{}, { entries: null }, { entries: {} }, [], null, 5]) {
    cleanup();
    routes[`GET ${ORG_BASE}/connections/litellm/${CONN}/catalog`] = { body };
    mount();
    await pick(/Endpoint model/);
    fireEvent.change(screen.getByLabelText(/Gateway connection/), { target: { value: CONN } });
    await settle();
    expect(screen.getByTestId("import-catalog-error")).toBeTruthy();
  }
});

test("H010P5-R3-7: a late catalog reply for a previously chosen connection never lands on the new one", async () => {
  const CONN2 = "99999999-9999-9999-9999-999999999999";
  let releaseA: (v: unknown) => void = () => undefined;
  routes[`GET ${ORG_BASE}/connections/litellm`] = {
    body: [
      { id: CONN, name: "main-gw", status: "active", base_url: "https://x/" },
      { id: CONN2, name: "second-gw", status: "active", base_url: "https://y/" },
    ],
  };
  routes[`GET ${ORG_BASE}/connections/litellm/${CONN2}/catalog`] = {
    body: { entries: [{ id: "model-of-B", status: "callable" }] },
  };
  const base = globalThis.fetch;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === `${ORG_BASE}/connections/litellm/${CONN}/catalog`)
      return new Promise((res) => {
        releaseA = () =>
          res({
            ok: true,
            status: 200,
            json: async () => ({ entries: [{ id: "model-of-A", status: "callable" }] }),
          });
      });
    return base(url, init);
  });
  mount();
  await pick(/Endpoint model/);
  const sel = screen.getByLabelText(/Gateway connection/);
  fireEvent.change(sel, { target: { value: CONN } });
  await settle();
  fireEvent.change(sel, { target: { value: CONN2 } });
  await settle();
  await act(async () => {
    releaseA(null);
    await Promise.resolve();
    await Promise.resolve();
  });
  const cat = screen.getByLabelText(/Model from the connection catalog/);
  expect(cat.innerHTML).toContain("model-of-B");
  expect(cat.innerHTML).not.toContain("model-of-A");
});

test("H010P5-R3-8: the announcement is localized: Arabic state labels, no English tokens", async () => {
  vi.useFakeTimers();
  let lists = 0;
  routes[`GET ${IMPORTS}`] = () => {
    lists += 1;
    return { body: [job({ id: "j1", state: lists >= 2 ? "validating" : "queued" })] };
  };
  mount({ locale: "ar" });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  const text = screen.getByTestId("import-announce").textContent ?? "";
  expect(text).toContain("تم تحديث حالة الاستيراد.");
  expect(text).toContain("قيد التحقق");
  expect(text).not.toMatch(/[A-Za-z]/);
});

// ---- H010P5-R4: pre-review round 3 --------------------------------------------------------
test("H010P5-R4-1: a poll that started before the POST cannot erase the created job or stop polling", async () => {
  vi.useFakeTimers();
  const NEW = "77777777-7777-7777-7777-777777777777";
  routes[`GET ${IMPORTS}`] = { body: [job()] };
  routes[`POST ${IMPORTS}`] = { status: 201, body: job({ id: NEW, state: "queued" }) };
  let release: () => void = () => undefined;
  let gets = 0;
  const base = globalThis.fetch;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === IMPORTS && (init?.method ?? "GET") === "GET") {
      gets += 1;
      if (gets === 2)
        // The poll: started before the POST, answered after it, with the OLD list.
        return new Promise((res) => {
          release = () => res({ ok: true, status: 200, json: async () => [job()] });
        });
    }
    return base(url, init);
  });
  mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_START_MS * 2 + 1);
  });
  expect(gets).toBe(2); // the poll is in flight
  await fillEndpoint();
  await settle();
  await confirmAndStart();
  expect(posts()).toHaveLength(1);
  expect(screen.getByTestId(`job-${NEW}`)).toBeTruthy();
  await act(async () => {
    release();
    await vi.advanceTimersByTimeAsync(0);
  });
  // The stale reply (without the created job) is dropped.
  expect(screen.getByTestId(`job-${NEW}`)).toBeTruthy();
  // And polling is still armed: a later poll fires.
  const before = listGets().length;
  for (let i = 0; i < 3; i += 1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MAX_MS);
    });
  expect(listGets().length).toBeGreaterThan(before);
});

test("H010P5-R4-2: artifact status, source type and security profile are localized under ar; unknown profile shown verbatim", async () => {
  // Weights form: facts are Arabic.
  mount({ locale: "ar", role: "ml_engineer" });
  await settle();
  fireEvent.click(screen.getAllByRole("radio")[1]!);
  fireEvent.click(screen.getByRole("button", { name: /./ }));
  await settle();
  fireEvent.change(screen.getAllByRole("combobox")[0]!, { target: { value: ART } });
  const facts = screen.getByTestId("artifact-facts").textContent ?? "";
  expect(facts).toContain("جاهز");
  expect(facts).not.toContain("huggingface");
  expect(facts).not.toMatch(/\bready\b/);
});

test("H010P5-R4-3: the profile is localized under ar and an unknown name is shown verbatim", async () => {
  routes[`GET ${ORG_BASE}/projects/${PROJECT}`] = { body: { security_profile: "air_gapped" } };
  mount({ locale: "ar" });
  await toReviewEndpointAr();
  expect(screen.getByTestId("import-security-profile").textContent).toBe("معزول عن الشبكة");
  cleanup();
  routes[`GET ${ORG_BASE}/projects/${PROJECT}`] = { body: { security_profile: "custom_x" } };
  mount({ locale: "ar" });
  await toReviewEndpointAr();
  expect(screen.getByTestId("import-security-profile").textContent).toBe("custom_x");
});
async function toReviewEndpointAr() {
  fireEvent.click(screen.getAllByRole("radio")[0]!);
  fireEvent.click(screen.getByRole("button", { name: /./ }));
  await settle();
  fireEvent.change(screen.getAllByRole("combobox")[0]!, { target: { value: CONN } });
  await settle();
  fireEvent.change(screen.getAllByRole("textbox")[0]!, { target: { value: "ft:my-model" } });
  const buttons = screen.getAllByRole("button");
  fireEvent.click(buttons[buttons.length - 1]!);
  await settle();
}

test("H010P5-R4-4: a failed options load for a connection the user has since left is dropped, not shown", async () => {
  const CONN2 = "99999999-9999-9999-9999-999999999999";
  let rejectA: () => void = () => undefined;
  routes[`GET ${ORG_BASE}/connections/litellm`] = {
    body: [
      { id: CONN, name: "main-gw", status: "active", base_url: "https://x/" },
      { id: CONN2, name: "second-gw", status: "active", base_url: "https://y/" },
    ],
  };
  routes[`GET ${ORG_BASE}/connections/litellm/${CONN2}/catalog`] = {
    body: { entries: [{ id: "model-of-B", status: "callable" }] },
  };
  const base = globalThis.fetch;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === `${ORG_BASE}/connections/litellm/${CONN}/catalog`)
      return new Promise((_res, rej) => {
        rejectA = () => rej(new TypeError("Failed to fetch"));
      });
    return base(url, init);
  });
  mount();
  await pick(/Endpoint model/);
  const sel = screen.getByLabelText(/Gateway connection/);
  fireEvent.change(sel, { target: { value: CONN } });
  await settle();
  fireEvent.change(sel, { target: { value: CONN2 } });
  await settle();
  await act(async () => {
    rejectA();
    await Promise.resolve();
    await Promise.resolve();
  });
  // The late NETWORK ERROR of the abandoned connection must not replace B's catalog.
  const cat = screen.getByLabelText(/Model from the connection catalog/);
  expect(cat.innerHTML).toContain("model-of-B");
  expect(screen.queryByTestId("catalog-error")).toBeNull();
});

test("H010P5-R4-5: a poll that starts while the POST is in flight cannot erase the created job, and its stale reply is not announced", async () => {
  vi.useFakeTimers();
  const NEW = "77777777-7777-7777-7777-777777777777";
  routes[`GET ${IMPORTS}`] = { body: [job()] };
  let releasePost: () => void = () => undefined;
  let releasePoll: () => void = () => undefined;
  let gets = 0;
  const base = globalThis.fetch;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url === IMPORTS && method === "POST")
      return new Promise((res) => {
        releasePost = () =>
          res({ ok: true, status: 201, json: async () => job({ id: NEW, state: "queued" }) });
      });
    if (url === IMPORTS && method === "GET") {
      gets += 1;
      // gets: 1 mount, 2 submit baseline, 3 = the poll that fires during the POST.
      if (gets === 3)
        return new Promise((res) => {
          releasePoll = () =>
            res({ ok: true, status: 200, json: async () => [job({ state: "validating" })] });
        });
    }
    return base(url, init);
  });
  mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await fillEndpoint();
  await settle();
  fireEvent.click(screen.getByTestId("import-confirm"));
  fireEvent.click(screen.getByTestId("import-submit"));
  await settle();
  // The POST is pending; the poll timer fires and its GET starts (the newest list request).
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MAX_MS);
  });
  expect(gets).toBe(3);
  await act(async () => {
    releasePost();
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByTestId(`job-${NEW}`)).toBeTruthy();
  await act(async () => {
    releasePoll();
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByTestId(`job-${NEW}`)).toBeTruthy();
  expect(screen.getByTestId("import-announce").textContent ?? "").not.toContain(
    "Import status updated.",
  );
});
