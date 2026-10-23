// @vitest-environment jsdom
//
// FTF-UX05 round 3 (QUEUE 0e; UX_STANDARD.md §4): the jsdom pins for the round-3 points that are
// about behaviour the browser spec (`ftf_ux05_browser.spec.ts`) cannot reach as cheaply -- every
// Law 17 error path of the four components (rejected fetch, non-JSON 200, malformed shape), the
// bilingual error text, the empty / loading states, and the named round-1/round-2 faults that were
// GREEN (F06, F09, F10, F15, F16, F26, F31, N05, N06, N14, N19, N25, N28, N29, N30, N32).
//
// Every assertion that compares text under locale="ar" compares against a literal Arabic string,
// never against `phrase()` itself, so a fault that forces English cannot pass by agreement.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { CapabilityCompatibilityUI } from "../components/artifact-capabilities/capability_compatibility_ui";
import { ModelToArtifactMappingUI } from "../components/artifact-capabilities/model_to_artifact_mapping_ui";
import { CostProfileSearchPanel } from "../components/cost-and-model-search/costprofile_schema_ui";
import { ModelCatalogUI } from "../components/litellm-gateway/model_catalog_ui";
import { phrase } from "../components/ui/phrases";

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const CONN = "22222222-2222-2222-2222-222222222222";
const PROJECT = "33333333-3333-3333-3333-333333333333";
const MODEL_A = "model-a";
const ALIAS = "llama-3-8b-instruct";
const ART_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const ART_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const RATE_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";

const COST_BASE = `/api/v1/organizations/${ORG}/cost-profiles`;
const SEARCH_PATH = `/api/v1/organizations/${ORG}/projects/${PROJECT}/costs/cost-per-success/search`;
const PROJECTS_PATH = `/api/v1/organizations/${ORG}/projects`;
const CATALOG_PATH = `/api/v1/organizations/${ORG}/connections/litellm/${CONN}/catalog`;
const PROBE_PATH = `/api/v1/organizations/${ORG}/connections/litellm/${CONN}/models/${MODEL_A}/probe`;
const MAP_PATH = `/api/v1/organizations/${ORG}/connections/litellm/${CONN}/models/${ALIAS}/training-artifact`;
const ARTIFACTS_PATH = `/api/v1/organizations/${ORG}/artifacts`;
const CAP_PATH = `/api/v1/organizations/${ORG}/connections/litellm/${CONN}/models/${ALIAS}/capabilities`;

// ---------------------------------------------------------------------------------------------
// A route-table fetch stub (the same technique the browser spec uses).
// ---------------------------------------------------------------------------------------------
interface Route {
  method: string;
  /** Matched against the END of the request's pathname. */
  path: string;
  status?: number;
  body?: unknown;
  pending?: boolean;
  reject?: unknown;
  /** Resolves `ok` with a body whose `json()` throws a SyntaxError that quotes the body. */
  malformedJson?: boolean;
}

let routes: Route[] = [];
let calls: Array<{ method: string; pathname: string; body: unknown }> = [];

function stub(...table: Route[]) {
  routes = table;
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
      const method = init?.method ?? "GET";
      const pathname = new URL(url).pathname;
      calls.push({ method, pathname, body: init?.body ? JSON.parse(init.body) : undefined });
      for (const route of routes) {
        if (route.method !== method || !pathname.endsWith(route.path)) continue;
        if (route.pending) return new Promise(() => undefined);
        if ("reject" in route) throw route.reject;
        const status = route.status ?? 200;
        return {
          ok: status >= 200 && status < 300,
          status,
          json: async () => {
            if (route.malformedJson) {
              throw new SyntaxError(`Unexpected token '<', "<html><body>" is not valid JSON`);
            }
            return route.body;
          },
        };
      }
      return { ok: false, status: 404, json: async () => ({ detail: "not stubbed" }) };
    }),
  );
}

beforeEach(() => {
  routes = [];
  calls = [];
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

type Locale = "en" | "ar";

// ---------------------------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------------------------
function rate(over: Record<string, unknown> = {}) {
  return {
    id: RATE_ID,
    organization_id: ORG,
    profile_type: "provider",
    label: "OpenAI list price",
    rate_unit: "per_input_token",
    rate_amount: "0.0000025",
    currency: "USD",
    source: "https://example.com/pricing",
    rate_recorded_at: "2026-10-01T00:00:00+00:00",
    verified_at: null,
    expires_at: null,
    row_version: 1,
    created_by: "u",
    created_at: "2026-10-01T00:00:00+00:00",
    updated_at: "2026-10-01T00:00:00+00:00",
    ...over,
  };
}

function searchBody(over: Record<string, unknown> = {}) {
  return {
    results: [{ ...rate(), certifiable_cheapest: false, disqualified_reason: "unverified" }],
    current_cheapest_certified: null,
    ...over,
  };
}

function renderCost(locale: Locale = "en") {
  return render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: PROJECT,
      locale,
    }),
  );
}

function renderCatalog(locale: Locale = "en") {
  return render(
    createElement(ModelCatalogUI, {
      apiBaseUrl: API,
      organizationId: ORG,
      connectionId: CONN,
      locale,
    }),
  );
}

function renderMapping(locale: Locale = "en", modelName = ALIAS) {
  return render(
    createElement(ModelToArtifactMappingUI, {
      apiBaseUrl: API,
      organizationId: ORG,
      connectionId: CONN,
      modelName,
      locale,
    }),
  );
}

function renderCompat(locale: Locale = "en", modelName = ALIAS) {
  return render(
    createElement(CapabilityCompatibilityUI, {
      apiBaseUrl: API,
      organizationId: ORG,
      connectionId: CONN,
      projectId: PROJECT,
      modelName,
      locale,
    }),
  );
}

function artifact(id: string, name: string, status = "ready") {
  return {
    id,
    display_name: name,
    source_type: "huggingface",
    source_uri: `meta-llama/${name}`,
    model_family: "llama",
    base_model_revision: "0123456789abcdef0123456789abcdef01234567",
    weights_digest: `sha256:${"a".repeat(64)}`,
    license_id: "llama3",
    license_accepted_by: "admin@example.com",
    license_accepted_at: "2026-10-01T00:00:00+00:00",
    trust_remote_code_allowed: false,
    required_python_packages: ["torch==2.4.0"],
    minimum_gpu_profile: { gpu: "A100", memory_gb: 40 },
    status,
  };
}

function mappingBody(version: number, artifactId: string | null, extra = {}) {
  return {
    mapping_state: artifactId ? "mapped" : "unmapped",
    model_catalog_entry_id: artifactId ? "44444444-4444-4444-4444-444444444444" : null,
    litellm_connection_id: CONN,
    public_model_name: ALIAS,
    trainable_artifact_id: artifactId,
    catalog_version: version,
    last_verified_accessible_at: artifactId ? "2026-10-07T00:00:00+00:00" : null,
    last_changed_at: artifactId ? "2026-10-07T00:00:00+00:00" : null,
    artifact: artifactId ? artifact(artifactId, "Llama-3-8B") : null,
    ...extra,
  };
}

function catalogBody(entries: unknown[] = [catalogEntry()], extra: Record<string, unknown> = {}) {
  return {
    connection_status: "active",
    access_state: "healthy",
    last_successful_check_at: "2026-10-06T12:00:00+00:00",
    last_checked_at: "2026-10-06T12:00:00+00:00",
    alert: null,
    anomaly: null,
    model_groups: null,
    entries,
    ...extra,
  };
}

function catalogEntry(id = MODEL_A) {
  return {
    id,
    status: "callable",
    metadata_status: "enriched",
    first_seen_at: "2026-10-06T12:00:00+00:00",
    last_seen_callable_at: "2026-10-06T12:00:00+00:00",
    fields: { max_tokens: 8192 },
  };
}

const PROJECT_ROWS = [{ id: PROJECT, name: "Acme Tuning Project", status: "active" }];

async function loadCatalogReady(locale: Locale = "en", body: unknown = catalogBody()) {
  stub(
    { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
    { method: "GET", path: CATALOG_PATH, body },
    {
      method: "POST",
      path: PROBE_PATH,
      body: {
        probe_type: "chat",
        state: "ok",
        route_identity_state: "verified",
        resolved_model: null,
        usage: null,
        latency_ms: 3,
        detail: "",
        probed_at: "2026-10-06T12:00:00+00:00",
      },
    },
  );
  renderCatalog(locale);
  fireEvent.click(screen.getByTestId("load-projects-button"));
  await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`project-select-button-${PROJECT}`));
  fireEvent.click(screen.getByTestId("load-catalog-button"));
  await waitFor(() => expect(screen.getByTestId("catalog-results")).toBeTruthy());
}

async function loadMappingAndArtifacts(locale: Locale = "en", mappedTo: string | null = ART_A) {
  stub(
    { method: "GET", path: MAP_PATH, body: mappingBody(2, mappedTo) },
    {
      method: "GET",
      path: ARTIFACTS_PATH,
      body: [artifact(ART_A, "Llama-3-8B"), artifact(ART_B, "Llama-3-70B")],
    },
    {
      method: "POST",
      path: MAP_PATH,
      body: { ...mappingBody(3, ART_B), changed: true },
    },
  );
  renderMapping(locale);
  fireEvent.click(screen.getByTestId("load-mapping-button"));
  await waitFor(() => expect(screen.getByTestId("mapping-state")).toBeTruthy());
  fireEvent.click(screen.getByTestId("load-artifacts-button"));
  await waitFor(() => expect(screen.getByTestId(`artifact-row-${ART_B}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`artifact-select-button-${ART_B}`));
  fireEvent.click(screen.getByTestId("map-button"));
  await waitFor(() => expect(screen.getByTestId("confirm-change")).toBeTruthy());
}

// ---------------------------------------------------------------------------------------------
// Point 1 -- ERRORS-SILENT (Law 17): the four 1301 cases
// ---------------------------------------------------------------------------------------------
describe("1301 errors are announced, localized, recoverable and never read as 'no certified rate'", () => {
  test("R3-1a a rejected fetch on search: role=alert, recovery step, cheapest rate shown UNKNOWN (not 'none certified')", async () => {
    stub({ method: "GET", path: SEARCH_PATH, reject: new TypeError("Failed to fetch") });
    renderCost();
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    const alert = screen.getByTestId("cost-profile-form-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toBe("network error: Failed to fetch");
    expect(screen.getByTestId("cost-profile-form-error-recovery").textContent).toBe(
      "Check your connection and try again. If it keeps failing, contact an administrator.",
    );
    expect(screen.queryByTestId("no-certified-cheapest-rate")).toBeNull();
    expect(screen.getByTestId("cheapest-rate-unknown").textContent).toBe(
      "Cheapest certified rate unknown: the search has not completed.",
    );
    expect(screen.queryByText(/No certified cheapest rate yet/)).toBeNull();
  });

  test("R3-1b a non-JSON 200 on search: a localized malformed-response message that never echoes the parser text or the body", async () => {
    stub({ method: "GET", path: SEARCH_PATH, malformedJson: true });
    renderCost();
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    const text = screen.getByTestId("cost-profile-form-error").textContent ?? "";
    expect(text).toBe("The server sent a response this page cannot read. Nothing was changed.");
    expect(text).not.toMatch(/Unexpected token|<html>|JSON/);
    expect(screen.queryByTestId("no-certified-cheapest-rate")).toBeNull();
    expect(screen.getByTestId("cheapest-rate-unknown")).toBeTruthy();
  });

  test("R3-1c a rejected fetch on create: announced, the typed values are kept", async () => {
    stub(
      { method: "GET", path: SEARCH_PATH, body: searchBody({ results: [] }) },
      { method: "POST", path: COST_BASE, reject: new TypeError("Failed to fetch") },
    );
    renderCost();
    await waitFor(() => expect(screen.getByTestId("cost-profile-reload-button")).toBeTruthy());
    fireEvent.change(screen.getByTestId("cost-profile-label-input"), {
      target: { value: "My rate" },
    });
    fireEvent.change(screen.getByTestId("cost-profile-rate-amount-input"), {
      target: { value: "0.5" },
    });
    fireEvent.change(screen.getByTestId("cost-profile-source-input"), {
      target: { value: "https://x" },
    });
    fireEvent.click(screen.getByTestId("cost-profile-submit-button"));
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.getByTestId("cost-profile-form-error").getAttribute("role")).toBe("alert");
    expect(screen.getByTestId("cost-profile-form-error").textContent).toBe(
      "network error: Failed to fetch",
    );
    expect((screen.getByTestId("cost-profile-label-input") as HTMLInputElement).value).toBe(
      "My rate",
    );
    expect((screen.getByTestId("cost-profile-submit-button") as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  test("R3-1d a rejected fetch on verify: announced, the list stays", async () => {
    stub(
      { method: "GET", path: SEARCH_PATH, body: searchBody() },
      {
        method: "POST",
        path: `${COST_BASE}/${RATE_ID}/verify`,
        reject: new TypeError("Failed to fetch"),
      },
    );
    renderCost();
    await waitFor(() => expect(screen.getByTestId(`cost-profile-verify-${RATE_ID}`)).toBeTruthy());
    fireEvent.click(screen.getByTestId(`cost-profile-verify-${RATE_ID}`));
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.getByTestId("cost-profile-form-error").getAttribute("role")).toBe("alert");
    expect(screen.getByTestId(`cost-profile-result-${RATE_ID}`)).toBeTruthy();
  });

  test("R3-1e an unreadable create response is announced, not treated as success", async () => {
    stub(
      { method: "GET", path: SEARCH_PATH, body: searchBody({ results: [] }) },
      { method: "POST", path: COST_BASE, status: 201, body: { nope: true } },
    );
    renderCost();
    await waitFor(() => expect(screen.getByTestId("cost-profile-reload-button")).toBeTruthy());
    fireEvent.change(screen.getByTestId("cost-profile-label-input"), {
      target: { value: "My rate" },
    });
    fireEvent.change(screen.getByTestId("cost-profile-rate-amount-input"), {
      target: { value: "0.5" },
    });
    fireEvent.change(screen.getByTestId("cost-profile-source-input"), {
      target: { value: "https://x" },
    });
    fireEvent.click(screen.getByTestId("cost-profile-submit-button"));
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect((screen.getByTestId("cost-profile-label-input") as HTMLInputElement).value).toBe(
      "My rate",
    );
  });

  test("R3-1f N25 a malformed current_cheapest_certified record is rejected, never rendered as the cheapest rate", async () => {
    stub({
      method: "GET",
      path: SEARCH_PATH,
      body: searchBody({ current_cheapest_certified: { label: 5 } }),
    });
    renderCost();
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.queryByText(/Cheapest certified rate:/)).toBeNull();
    expect(screen.getByTestId("cheapest-rate-unknown")).toBeTruthy();
  });

  test("R3-1g a refresh that fails after a verify clears the previous result instead of leaving it as current", async () => {
    stub({ method: "GET", path: SEARCH_PATH, body: searchBody() });
    renderCost();
    await waitFor(() => expect(screen.getByTestId(`cost-profile-result-${RATE_ID}`)).toBeTruthy());
    stub(
      { method: "POST", path: `${COST_BASE}/${RATE_ID}/verify`, body: {} },
      { method: "GET", path: SEARCH_PATH, status: 500, body: {} },
    );
    fireEvent.click(screen.getByTestId(`cost-profile-verify-${RATE_ID}`));
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.queryByTestId(`cost-profile-result-${RATE_ID}`)).toBeNull();
    expect(screen.getByTestId("cheapest-rate-unknown")).toBeTruthy();
    expect(screen.queryByTestId("no-certified-cheapest-rate")).toBeNull();
  });
});

describe("1301 stale results", () => {
  test("R3-1h a refresh that REJECTS after a verify also clears the previous result", async () => {
    stub({ method: "GET", path: SEARCH_PATH, body: searchBody() });
    renderCost();
    await waitFor(() => expect(screen.getByTestId(`cost-profile-result-${RATE_ID}`)).toBeTruthy());
    stub(
      { method: "POST", path: `${COST_BASE}/${RATE_ID}/verify`, body: {} },
      { method: "GET", path: SEARCH_PATH, reject: new TypeError("Failed to fetch") },
    );
    fireEvent.click(screen.getByTestId(`cost-profile-verify-${RATE_ID}`));
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.queryByTestId(`cost-profile-result-${RATE_ID}`)).toBeNull();
    expect(screen.getByTestId("cheapest-rate-unknown")).toBeTruthy();
  });

  test("R3-1i a refresh that returns a malformed shape clears the previous result", async () => {
    stub({ method: "GET", path: SEARCH_PATH, body: searchBody() });
    renderCost();
    await waitFor(() => expect(screen.getByTestId(`cost-profile-result-${RATE_ID}`)).toBeTruthy());
    stub(
      { method: "POST", path: `${COST_BASE}/${RATE_ID}/verify`, body: {} },
      { method: "GET", path: SEARCH_PATH, body: { results: "nope" } },
    );
    fireEvent.click(screen.getByTestId(`cost-profile-verify-${RATE_ID}`));
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.queryByTestId(`cost-profile-result-${RATE_ID}`)).toBeNull();
  });
});

describe("1301 bilingual error text (F26 / N28)", () => {
  test("R3-F26 a 500 with a non-JSON body under ar: the localized request-failed text", async () => {
    stub({ method: "GET", path: SEARCH_PATH, status: 500, malformedJson: true });
    renderCost("ar");
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.getByTestId("cost-profile-form-error").textContent).toBe("فشل الطلب (500)");
    expect(screen.getByTestId("cost-profile-form-error-recovery").textContent).toBe(
      "تحقق من اتصالك ثم حاول مرة أخرى. إذا استمر الفشل، تواصل مع المسؤول.",
    );
  });

  test("R3-N28 a `{}` 200 under ar: the localized malformed-search message", async () => {
    stub({ method: "GET", path: SEARCH_PATH, body: {} });
    renderCost("ar");
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.getByTestId("cost-profile-form-error").textContent).toBe(
      "استجابة بحث تكلفة لكل نجاح غير صحيحة",
    );
    expect(screen.getByTestId("cheapest-rate-unknown").textContent).toBe(
      "السعر المعتمد الأرخص غير معروف: لم يكتمل البحث.",
    );
  });

  test("R3-4 a non-JSON 200 under ar: the localized malformed-response message, no English parser text or body", async () => {
    stub({ method: "GET", path: SEARCH_PATH, malformedJson: true });
    renderCost("ar");
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    const text = screen.getByTestId("cost-profile-form-error").textContent ?? "";
    expect(text).toBe("أرسل الخادم استجابة لا تستطيع هذه الصفحة قراءتها. لم يتم تغيير أي شيء.");
    expect(text).not.toMatch(/Unexpected|html|JSON/);
  });

  test("R3-4 an error that carries no message under ar: the localized 'unknown error'", async () => {
    stub({ method: "GET", path: SEARCH_PATH, reject: "boom" });
    renderCost("ar");
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.getByTestId("cost-profile-form-error").textContent).toBe(
      "خطأ في الشبكة: خطأ غير معروف",
    );
  });

  test("R3-4 an error that carries no message under en: 'unknown error' (the existing text)", async () => {
    stub({ method: "GET", path: SEARCH_PATH, reject: new Error("") });
    renderCost("en");
    await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
    expect(screen.getByTestId("cost-profile-form-error").textContent).toBe(
      "network error: unknown error",
    );
  });

  test("R3-F09 the 1301 select options render in Arabic under ar", async () => {
    stub({ method: "GET", path: SEARCH_PATH, body: searchBody({ results: [] }) });
    renderCost("ar");
    await waitFor(() => expect(screen.getByTestId("cost-profile-reload-button")).toBeTruthy());
    const type = screen.getByTestId("cost-profile-type-select") as HTMLSelectElement;
    expect(Array.from(type.options).map((o) => o.textContent)).toEqual(["مزوّد", "عتاد محلي"]);
    const unit = screen.getByTestId("cost-profile-rate-unit-select") as HTMLSelectElement;
    expect(Array.from(unit.options).map((o) => o.textContent)).toEqual([
      "لكل رمز إدخال",
      "لكل رمز إخراج",
      "لكل ساعة معالج رسومي",
      "لكل ساعة معالج",
      "لكل جيجابايت ذاكرة في الساعة",
      "لكل ساعة",
      "لكل طلب",
    ]);
  });
});

describe("1301 inputs are on the UX01 target size", () => {
  test("R3-8 the four text inputs carry the --target-min block size, like the selects", async () => {
    stub({ method: "GET", path: SEARCH_PATH, body: searchBody({ results: [] }) });
    renderCost();
    await waitFor(() => expect(screen.getByTestId("cost-profile-reload-button")).toBeTruthy());
    for (const id of [
      "cost-profile-label-input",
      "cost-profile-rate-amount-input",
      "cost-profile-currency-input",
      "cost-profile-source-input",
      "cost-profile-type-select",
      "cost-profile-rate-unit-select",
    ]) {
      expect((screen.getByTestId(id) as HTMLElement).style.minBlockSize, id).toBe(
        "var(--target-min)",
      );
    }
  });
});

describe("1301 loading / wrapping", () => {
  test("R3-N29 the cheapest-certified paragraph wraps a long unbroken label", async () => {
    const long = rate({ label: "L".repeat(300), verified_at: "2026-10-02T00:00:00+00:00" });
    stub({
      method: "GET",
      path: SEARCH_PATH,
      body: searchBody({ results: [], current_cheapest_certified: long }),
    });
    renderCost();
    await waitFor(() => expect(screen.getByTestId("current-cheapest-certified")).toBeTruthy());
    const para = screen.getByTestId("current-cheapest-certified").querySelector("p") as HTMLElement;
    expect(para.textContent).toContain("L".repeat(300));
    expect(para.style.overflowWrap).toBe("anywhere");
  });

  test("R3-8 the panel root wraps (a long value anywhere inside cannot widen the page)", async () => {
    stub({ method: "GET", path: SEARCH_PATH, body: searchBody() });
    renderCost();
    await waitFor(() => expect(screen.getByTestId("cost-profile-panel")).toBeTruthy());
    expect((screen.getByTestId("cost-profile-panel") as HTMLElement).style.overflowWrap).toBe(
      "anywhere",
    );
  });
});

// ---------------------------------------------------------------------------------------------
// Point 3 -- MISSING-STATES
// ---------------------------------------------------------------------------------------------
describe("0214 empty / loading states", () => {
  test("R3-3a an empty project list (200 []) is an EmptyState with a reload action, en", async () => {
    stub({ method: "GET", path: PROJECTS_PATH, body: [] });
    renderCatalog();
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId("projects-reload-button")).toBeTruthy());
    expect(screen.getByText("No projects found")).toBeTruthy();
    expect(screen.getByTestId("projects-reload-button").textContent).toBe("Load projects");
  });

  test("R3-3a the same empty state, localized, under ar", async () => {
    stub({ method: "GET", path: PROJECTS_PATH, body: [] });
    renderCatalog("ar");
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId("projects-reload-button")).toBeTruthy());
    expect(screen.getByText("لم يتم العثور على مشاريع")).toBeTruthy();
    expect(screen.getByTestId("projects-reload-button").textContent).toBe("تحميل المشاريع");
  });

  test("R3-3a no empty state before the first load, and none after a failed load (a failure is not 'zero projects')", async () => {
    stub({ method: "GET", path: PROJECTS_PATH, status: 503, body: {} });
    renderCatalog();
    expect(screen.queryByTestId("projects-reload-button")).toBeNull();
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy());
    expect(screen.queryByTestId("projects-reload-button")).toBeNull();
    expect(screen.queryByText("No projects found")).toBeNull();
  });

  test("R3-N05 loading projects shows a Pending status once the load passes 1 s (not before), gone after", async () => {
    vi.useFakeTimers();
    stub({ method: "GET", path: PROJECTS_PATH, pending: true });
    renderCatalog();
    fireEvent.click(screen.getByTestId("load-projects-button"));
    expect(screen.queryByRole("status")).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(screen.getByRole("status").textContent).toContain("Load projects");
    expect(screen.getByTestId("load-projects-button").getAttribute("aria-busy")).toBe("true");
  });

  test("R3-N06 loading the catalog shows a Pending status once the load passes 1 s", async () => {
    stub(
      { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
      { method: "GET", path: CATALOG_PATH, pending: true },
    );
    renderCatalog();
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT}`)).toBeTruthy());
    fireEvent.click(screen.getByTestId(`project-select-button-${PROJECT}`));
    vi.useFakeTimers();
    fireEvent.click(screen.getByTestId("load-catalog-button"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(screen.getByRole("status").textContent).toContain("Load catalog");
  });

  test("R3-N30 an empty catalog under ar shows the Arabic empty-state title", async () => {
    await loadCatalogReady("ar", catalogBody([]));
    expect(screen.getByText("لا توجد عناصر في الكتالوج")).toBeTruthy();
    expect(screen.queryByText("No catalog entries")).toBeNull();
  });
});

describe("0308 empty / loading states", () => {
  test("R3-3b an empty artifact list (200 []) is an EmptyState with a reload action, en and ar", async () => {
    stub({ method: "GET", path: ARTIFACTS_PATH, body: [] });
    renderMapping();
    fireEvent.click(screen.getByTestId("load-artifacts-button"));
    await waitFor(() => expect(screen.getByTestId("artifacts-reload-button")).toBeTruthy());
    expect(screen.getByText("No trainable artifacts")).toBeTruthy();
    cleanup();
    stub({ method: "GET", path: ARTIFACTS_PATH, body: [] });
    renderMapping("ar");
    fireEvent.click(screen.getByTestId("load-artifacts-button"));
    await waitFor(() => expect(screen.getByTestId("artifacts-reload-button")).toBeTruthy());
    expect(screen.getByText("لا توجد مصنفات قابلة للتدريب")).toBeTruthy();
    expect(screen.getByTestId("artifacts-reload-button").textContent).toBe("تحميل المصنفات");
  });

  test("R3-3b no artifacts empty state after a failed load", async () => {
    stub({ method: "GET", path: ARTIFACTS_PATH, status: 500, body: {} });
    renderMapping();
    fireEvent.click(screen.getByTestId("load-artifacts-button"));
    await waitFor(() => expect(screen.getByTestId("artifacts-error")).toBeTruthy());
    expect(screen.queryByTestId("artifacts-reload-button")).toBeNull();
  });

  test.each([
    ["mapping", "load-mapping-button", MAP_PATH, "Load mapping"],
    ["artifact", "load-artifacts-button", ARTIFACTS_PATH, "Load artifacts"],
    ["change history", "load-changes-button", `${MAP_PATH}/changes`, "Load change history"],
  ])(
    "R3-3b loading the %s sets aria-busy and shows Pending past 1 s",
    async (_name, testId, path, label) => {
      vi.useFakeTimers();
      stub({ method: "GET", path, pending: true });
      renderMapping();
      fireEvent.click(screen.getByTestId(testId));
      expect(screen.getByTestId(testId).getAttribute("aria-busy")).toBe("true");
      expect((screen.getByTestId(testId) as HTMLButtonElement).disabled).toBe(true);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1300);
      });
      expect(screen.getByRole("status").textContent).toContain(label);
    },
  );
});

// ---------------------------------------------------------------------------------------------
// Point 4 -- bilingual errors in the other three components
// ---------------------------------------------------------------------------------------------
describe("0214 / 0308 / 0309 share the localized network-error text", () => {
  test("a non-JSON 200 under ar announces the Arabic malformed-response message in all three", async () => {
    const arMalformed = "أرسل الخادم استجابة لا تستطيع هذه الصفحة قراءتها. لم يتم تغيير أي شيء.";
    stub({ method: "GET", path: PROJECTS_PATH, malformedJson: true });
    renderCatalog("ar");
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy());
    expect(screen.getByTestId("projects-error").textContent).toBe(arMalformed);
    cleanup();

    stub({ method: "GET", path: MAP_PATH, malformedJson: true });
    renderMapping("ar");
    fireEvent.click(screen.getByTestId("load-mapping-button"));
    await waitFor(() => expect(screen.getByTestId("mapping-error")).toBeTruthy());
    expect(screen.getByTestId("mapping-error").textContent).toBe(arMalformed);
    cleanup();

    stub({ method: "GET", path: CAP_PATH, malformedJson: true });
    renderCompat("ar");
    fireEvent.click(screen.getByRole("button", { name: "فحص التوافق" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toBe(arMalformed);
  });

  test("a rejection with no message announces 'unknown error' localized in all three", async () => {
    stub({ method: "GET", path: PROJECTS_PATH, reject: undefined });
    renderCatalog("ar");
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy());
    expect(screen.getByTestId("projects-error").textContent).toBe("خطأ في الشبكة: خطأ غير معروف");
    cleanup();

    stub({ method: "GET", path: MAP_PATH, reject: undefined });
    renderMapping("ar");
    fireEvent.click(screen.getByTestId("load-mapping-button"));
    await waitFor(() => expect(screen.getByTestId("mapping-error")).toBeTruthy());
    expect(screen.getByTestId("mapping-error").textContent).toBe("خطأ في الشبكة: خطأ غير معروف");
    cleanup();

    stub({ method: "GET", path: CAP_PATH, reject: undefined });
    renderCompat("ar");
    fireEvent.click(screen.getByRole("button", { name: "فحص التوافق" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toBe("خطأ في الشبكة: خطأ غير معروف");
  });

  test("every request-level alert in the four components has a recovery step beside it", async () => {
    const recovery =
      "Check your connection and try again. If it keeps failing, contact an administrator.";
    // 0214: projects, catalog, probe
    stub({ method: "GET", path: PROJECTS_PATH, status: 500, body: {} });
    renderCatalog();
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId("projects-error-recovery")).toBeTruthy());
    expect(screen.getByTestId("projects-error-recovery").textContent).toBe(recovery);
    cleanup();

    stub(
      { method: "GET", path: PROJECTS_PATH, body: PROJECT_ROWS },
      { method: "GET", path: CATALOG_PATH, status: 500, body: {} },
    );
    renderCatalog();
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT}`)).toBeTruthy());
    fireEvent.click(screen.getByTestId(`project-select-button-${PROJECT}`));
    fireEvent.click(screen.getByTestId("load-catalog-button"));
    await waitFor(() => expect(screen.getByTestId("catalog-error-recovery")).toBeTruthy());
    expect(screen.getByTestId("catalog-error-recovery").textContent).toBe(recovery);
    cleanup();

    await loadCatalogReady();
    stub({ method: "POST", path: PROBE_PATH, status: 500, body: {} });
    fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
    fireEvent.click(screen.getByTestId(`confirm-probe-button-${MODEL_A}`));
    await waitFor(() => expect(screen.getByTestId("probe-error-recovery")).toBeTruthy());
    expect(screen.getByTestId("probe-error-recovery").textContent).toBe(recovery);
    cleanup();

    // 0308: mapping, artifacts, changes, write
    stub(
      { method: "GET", path: MAP_PATH, status: 500, body: {} },
      { method: "GET", path: ARTIFACTS_PATH, status: 500, body: {} },
      { method: "GET", path: `${MAP_PATH}/changes`, status: 500, body: {} },
    );
    renderMapping();
    fireEvent.click(screen.getByTestId("load-mapping-button"));
    fireEvent.click(screen.getByTestId("load-artifacts-button"));
    fireEvent.click(screen.getByTestId("load-changes-button"));
    for (const id of ["mapping-error", "artifacts-error", "changes-error"]) {
      await waitFor(() => expect(screen.getByTestId(`${id}-recovery`)).toBeTruthy());
      expect(screen.getByTestId(`${id}-recovery`).textContent).toBe(recovery);
    }
    cleanup();

    await loadMappingAndArtifacts();
    stub({ method: "POST", path: MAP_PATH, status: 503, body: { detail: "gateway_unavailable" } });
    fireEvent.click(screen.getByTestId("confirm-change-button"));
    await waitFor(() => expect(screen.getByTestId("write-error-recovery")).toBeTruthy());
    expect(screen.getByTestId("write-error-recovery").textContent).toBe(recovery);
    cleanup();

    // 0309
    stub({ method: "GET", path: CAP_PATH, status: 500, body: {} });
    renderCompat();
    fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
    await waitFor(() => expect(screen.getByTestId("compat-error-recovery")).toBeTruthy());
    expect(screen.getByTestId("compat-error-recovery").textContent).toBe(recovery);
    cleanup();

    // 1301 (form error)
    stub({ method: "GET", path: SEARCH_PATH, status: 500, body: {} });
    renderCost();
    await waitFor(() =>
      expect(screen.getByTestId("cost-profile-form-error-recovery")).toBeTruthy(),
    );
  });

  test("the alert's own text is the message only; the recovery step is its aria-describedby", async () => {
    stub({ method: "GET", path: CAP_PATH, status: 500, body: {} });
    renderCompat();
    fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("request failed (500)");
    const describedBy = alert.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy as string)?.textContent).toContain("try again");
  });
});

// ---------------------------------------------------------------------------------------------
// Point 2 -- overflow wrap sites (Chromium measures them in the browser spec; these pin the code)
// ---------------------------------------------------------------------------------------------
describe("overflow wrap sites", () => {
  test("R3-2 0214: a project row and the root wrap, and a model group row wraps (N32)", async () => {
    const longName = `proj_${"p".repeat(200)}`;
    stub(
      {
        method: "GET",
        path: PROJECTS_PATH,
        body: [{ id: PROJECT, name: longName, status: "active" }],
      },
      {
        method: "GET",
        path: CATALOG_PATH,
        body: catalogBody([catalogEntry()], {
          model_groups: { state: "enriched", entries: { "grp-x": { note: "g".repeat(300) } } },
        }),
      },
    );
    renderCatalog();
    fireEvent.click(screen.getByTestId("load-projects-button"));
    await waitFor(() => expect(screen.getByTestId(`project-row-${PROJECT}`)).toBeTruthy());
    expect((screen.getByTestId(`project-row-${PROJECT}`) as HTMLElement).style.overflowWrap).toBe(
      "anywhere",
    );
    expect((screen.getByTestId("model-catalog-ui") as HTMLElement).style.overflowWrap).toBe(
      "anywhere",
    );
    fireEvent.click(screen.getByTestId(`project-select-button-${PROJECT}`));
    fireEvent.click(screen.getByTestId("load-catalog-button"));
    await waitFor(() => expect(screen.getByTestId("model-group-grp-x")).toBeTruthy());
    expect((screen.getByTestId("model-group-grp-x") as HTMLElement).style.overflowWrap).toBe(
      "anywhere",
    );
  });

  test("R3-2 0308: the heading, the root and every detail row wrap", async () => {
    stub({ method: "GET", path: MAP_PATH, body: mappingBody(2, ART_A) });
    renderMapping("en");
    expect((screen.getByTestId("mapping-model-name") as HTMLElement).style.overflowWrap).toBe(
      "anywhere",
    );
    expect(
      (screen.getByTestId("model-to-artifact-mapping-ui") as HTMLElement).style.overflowWrap,
    ).toBe("anywhere");
    fireEvent.click(screen.getByTestId("load-mapping-button"));
    await waitFor(() => expect(screen.getByTestId("mapped-artifact-details")).toBeTruthy());
    expect((screen.getByTestId("mapped-artifact-details") as HTMLElement).style.overflowWrap).toBe(
      "anywhere",
    );
  });

  test("R3-2 0309: the section wraps and the dimension table is fixed-layout at full width", async () => {
    stub({ method: "GET", path: CAP_PATH, body: capabilityBody() });
    renderCompat();
    fireEvent.click(screen.getByRole("button", { name: "Check compatibility" }));
    await waitFor(() => expect(document.querySelector("table")).toBeTruthy());
    const table = document.querySelector("table") as HTMLElement;
    expect(table.style.tableLayout).toBe("fixed");
    expect(table.style.inlineSize).toBe("100%");
    expect(table.style.overflowWrap).toBe("anywhere");
    expect(
      (screen.getByLabelText("Capability compatibility") as HTMLElement).style.overflowWrap,
    ).toBe("anywhere");
  });
});

function capabilityBody() {
  const unknown = (reason: string) => ({ state: "unknown", reason });
  return {
    litellm_connection_id: CONN,
    project_id: PROJECT,
    public_model_name: ALIAS,
    connection_status: "active",
    access: "granted",
    capability: "trainable_open_weight",
    trainable: true,
    capability_detail: "mapped artifact",
    capability_reason: null,
    role: "primary",
    artifact: {
      artifact_id: ART_A,
      availability: "ready",
      license_approved: true,
      weights_digest: `sha256:${"a".repeat(64)}`,
    },
    catalog_version: 3,
    method_support: unknown("no_backing_evidence"),
    min_gpu_vram_gb: {
      ...unknown("not_tested"),
      declared_minimum_gpu_profile: { gpu: "A100" },
      declared_evidence: "declared",
    },
    chat_template: unknown("no_backing_evidence"),
    evidence: {
      discovery_checked_at: "2026-10-09T08:00:00+00:00",
      tool_call_probe_checked_at: null,
      tool_call_probe_present: false,
    },
  };
}

// ---------------------------------------------------------------------------------------------
// Round-1/2 faults that stayed GREEN (point 5)
// ---------------------------------------------------------------------------------------------
describe("named faults now pinned", () => {
  test("R3-F06 0214: Confirm moves focus to the entry row; Cancel returns it to the trigger; opening focuses Cancel", async () => {
    await loadCatalogReady();
    fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
    await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId(`cancel-probe-button-${MODEL_A}`)),
    );
    fireEvent.click(screen.getByTestId(`cancel-probe-button-${MODEL_A}`));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId(`probe-button-${MODEL_A}`)),
    );
    fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
    await waitFor(() => expect(screen.getByTestId(`confirm-probe-button-${MODEL_A}`)).toBeTruthy());
    fireEvent.click(screen.getByTestId(`confirm-probe-button-${MODEL_A}`));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId(`catalog-entry-${MODEL_A}`)),
    );
    expect(document.activeElement).not.toBe(document.body);
  });

  test("R3-F06 0214: Escape inside the confirm closes it and returns focus to the trigger", async () => {
    await loadCatalogReady();
    fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
    await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
    fireEvent.keyDown(screen.getByTestId(`cancel-probe-button-${MODEL_A}`), { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId(`confirm-probe-${MODEL_A}`)).toBeNull());
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId(`probe-button-${MODEL_A}`)),
    );
  });

  test("R3-F09 0214: filter and probe-kind option texts are Arabic under ar", async () => {
    await loadCatalogReady("ar");
    const texts = (id: string) =>
      Array.from((screen.getByTestId(id) as HTMLSelectElement).options).map((o) => o.textContent);
    expect(texts("status-filter")).toEqual(["الكل", "قابل للاستدعاء", "قديم", "مُزال"]);
    expect(texts("metadata-filter")).toEqual(["الكل", "مُفصَّل", "محدود"]);
    expect(texts("probe-kind")).toEqual(["محادثة", "بث", "استدعاء أداة", "ناتج منظم"]);
  });

  test("R3-F10 0214: every server-supplied value is isolated in <bdi>", async () => {
    await loadCatalogReady();
    const inBdi = (testId: string) => {
      const el = screen.getByTestId(testId);
      const bdi = el.tagName === "BDI" ? el : el.querySelector("bdi");
      expect(bdi, testId).toBeTruthy();
      expect((bdi as HTMLElement).textContent).toBeTruthy();
    };
    expect(screen.getByTestId(`project-row-${PROJECT}`).querySelector("bdi")?.textContent).toBe(
      "Acme Tuning Project",
    );
    for (const id of [
      "catalog-connection-status",
      "catalog-access-state",
      "catalog-freshness",
      `entry-status-${MODEL_A}`,
      `entry-metadata-status-${MODEL_A}`,
      `entry-fields-${MODEL_A}`,
    ]) {
      inBdi(id);
    }
  });

  test("R3-F10 1301: label, amount, currency and unit are isolated in <bdi>", async () => {
    stub({ method: "GET", path: SEARCH_PATH, body: searchBody() });
    renderCost();
    await waitFor(() => expect(screen.getByTestId(`cost-profile-result-${RATE_ID}`)).toBeTruthy());
    const bdis = Array.from(
      screen.getByTestId(`cost-profile-result-${RATE_ID}`).querySelectorAll("bdi"),
    ).map((b) => b.textContent);
    expect(bdis).toEqual(["OpenAI list price", "0.0000025", "USD", "per_input_token"]);
  });

  test("R3-F15 0308: the mapping notice is a role=status live region", async () => {
    await loadMappingAndArtifacts();
    fireEvent.click(screen.getByTestId("confirm-change-button"));
    await waitFor(() => expect(screen.getByTestId("mapping-notice")).toBeTruthy());
    expect(screen.getByTestId("mapping-notice").getAttribute("role")).toBe("status");
    expect(screen.getByRole("status").textContent).toContain("2 -> 3");
  });

  test("R3-focus 0308: a failed write puts focus on the announced write-error (the Confirm button disables while writing); success lands on the changed state", async () => {
    await loadMappingAndArtifacts();
    stub({
      method: "POST",
      path: MAP_PATH,
      status: 503,
      body: { detail: "gateway_unavailable" },
    });
    fireEvent.click(screen.getByTestId("confirm-change-button"));
    await waitFor(() => expect(screen.getByTestId("write-error")).toBeTruthy());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("write-error")));
    expect(screen.getByTestId("write-error").getAttribute("tabindex")).toBe("-1");
    cleanup();

    await loadMappingAndArtifacts();
    fireEvent.click(screen.getByTestId("confirm-change-button"));
    await waitFor(() => expect(screen.getByTestId("mapping-notice")).toBeTruthy());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("mapping-state")));
  });

  test("R3-F16 0308: the confirm is a role=alertdialog (found by role, with its name)", async () => {
    await loadMappingAndArtifacts();
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.getAttribute("data-testid")).toBe("confirm-change");
  });

  test("R3-N14 0308: the alertdialog's accessible name comes from aria-labelledby and is the visible confirm text", async () => {
    await loadMappingAndArtifacts();
    const dialog = screen.getByTestId("confirm-change");
    const labelledBy = dialog.getAttribute("aria-labelledby");
    expect(labelledBy).toBe("confirm-change-text");
    const target = document.getElementById(labelledBy as string);
    expect(target?.textContent).toContain("Changing this mapping");
    expect(screen.getByRole("alertdialog", { name: /Changing this mapping/ })).toBeTruthy();
  });

  test("R3-N19 0308: the confirm carries its own lang/dir under ar", async () => {
    await loadMappingAndArtifacts("ar");
    const dialog = screen.getByTestId("confirm-change");
    expect(dialog.getAttribute("lang")).toBe("ar");
    expect(dialog.getAttribute("dir")).toBe("rtl");
  });

  test("R3-N19 0214: the probe confirm carries its own lang/dir under ar", async () => {
    await loadCatalogReady("ar");
    fireEvent.click(screen.getByTestId(`probe-button-${MODEL_A}`));
    await waitFor(() => expect(screen.getByTestId(`confirm-probe-${MODEL_A}`)).toBeTruthy());
    const dialog = screen.getByTestId(`confirm-probe-${MODEL_A}`);
    expect(dialog.getAttribute("lang")).toBe("ar");
    expect(dialog.getAttribute("dir")).toBe("rtl");
    expect(dialog.getAttribute("aria-labelledby")).toBe(`confirm-probe-title-${MODEL_A}`);
  });

  test("R3-F31 phrase(): a variable only inherited through the prototype is NOT substituted", () => {
    const inherited = Object.create({ status: 999 }) as Record<string, number>;
    expect(phrase("requestFailedTemplate", "en", inherited)).toBe("request failed ({status})");
    expect(phrase("requestFailedTemplate", "en", { status: 500 })).toBe("request failed (500)");
    expect(phrase("requestFailedTemplate", "ar", { status: 500 })).toBe("فشل الطلب (500)");
  });
});
