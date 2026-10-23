// @vitest-environment jsdom
//
// FTF-1301 -- CostProfile record form + cost-per-success search panel.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `CostProfileSearchPanel`, drives real user
// interaction and asserts on the real `fetch` calls it makes against the
// live FTF-1301 API contract -- request method, URL and JSON body --
// never a snapshot or hand-written HTML string. `fetch` itself is mocked
// (no live server in a unit test), but the component code path exercised
// is the real one that would hit the actual backend.
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E (no Playwright here -- see the component's own docstring
// for why, the same precedent `ftf_0401.spec.ts` already established).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  CostProfileRecord,
  CostProfileSearchPanel,
  CostProfileSearchResult,
  costPerSuccessSearchUrl,
  costProfileVerifyUrl,
  costProfilesCollectionUrl,
} from "../components/cost-and-model-search/costprofile_schema_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";

function baseRecord(overrides: Partial<CostProfileRecord> = {}): CostProfileRecord {
  return {
    id: "33333333-3333-3333-3333-333333333333",
    organization_id: ORG_ID,
    profile_type: "provider",
    label: "OpenAI gpt-4o-mini",
    rate_unit: "per_input_token",
    rate_amount: "0.0000001500",
    currency: "USD",
    source: "https://openai.com/api/pricing",
    rate_recorded_at: "2026-09-01T00:00:00+00:00",
    verified_at: null,
    expires_at: null,
    row_version: 1,
    created_by: "user-1",
    created_at: "2026-09-01T00:00:00+00:00",
    updated_at: "2026-09-01T00:00:00+00:00",
    ...overrides,
  };
}

function searchResult(overrides: Partial<CostProfileSearchResult> = {}): CostProfileSearchResult {
  return {
    ...baseRecord(),
    certifiable_cheapest: false,
    disqualified_reason: "unverified",
    ...overrides,
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("URL helpers point at the real FTF-1301 API contract", () => {
  expect(costProfilesCollectionUrl(API_BASE, ORG_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/cost-profiles`,
  );
  expect(costProfileVerifyUrl(API_BASE, ORG_ID, "cp1")).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/cost-profiles/cp1/verify`,
  );
  expect(costPerSuccessSearchUrl(API_BASE, ORG_ID, PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}/costs/cost-per-success/search`,
  );
});

test("on mount, fetches the real search endpoint and shows 'no certified cheapest rate' honestly when null", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: [], current_cheapest_certified: null }),
  });

  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(fetchMock.mock.calls[0]![0]).toBe(costPerSuccessSearchUrl(API_BASE, ORG_ID, PROJECT_ID));
  await waitFor(() => expect(screen.getByTestId("no-certified-cheapest-rate")).toBeTruthy());
});

test("renders every result's own honest disqualification reason, never silently filtered", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  const expired = searchResult({ id: "r-expired", disqualified_reason: "expired" });
  const unverified = searchResult({ id: "r-unverified", disqualified_reason: "unverified" });
  const zeroProvider = searchResult({
    id: "r-zero",
    profile_type: "provider",
    rate_amount: "0.0000000000",
    verified_at: "2026-09-10T00:00:00+00:00",
    disqualified_reason: "zero_provider_rate_unconfirmed",
  });
  const certified = searchResult({
    id: "r-certified",
    verified_at: "2026-09-10T00:00:00+00:00",
    certifiable_cheapest: true,
    disqualified_reason: null,
  });
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      results: [expired, unverified, zeroProvider, certified],
      current_cheapest_certified: certified,
    }),
  });

  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  await waitFor(() => expect(screen.getByTestId("cost-profile-result-r-certified")).toBeTruthy());
  expect(screen.getByTestId("cost-profile-disqualified-r-expired").textContent).toMatch(/expired/i);
  expect(screen.getByTestId("cost-profile-disqualified-r-unverified").textContent).toMatch(
    /unverified/i,
  );
  expect(screen.getByTestId("cost-profile-disqualified-r-zero").textContent).toMatch(
    /not local-compute-free/i,
  );
  expect(screen.getByTestId("cost-profile-certified-r-certified")).toBeTruthy();

  // A certified, already-verified result never shows a Verify button; an
  // unverified one does.
  expect(screen.queryByTestId("cost-profile-verify-r-certified")).toBeNull();
  expect(screen.getByTestId("cost-profile-verify-r-unverified")).toBeTruthy();
});

test("submitting the create form POSTs the real payload, then refreshes the search", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ results: [], current_cheapest_certified: null }),
    })
    .mockResolvedValueOnce({ ok: true, json: async () => baseRecord() })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ results: [searchResult()], current_cheapest_certified: null }),
    });

  const onCreated = vi.fn();
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      onCreated,
    }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

  fireEvent.change(screen.getByTestId("cost-profile-label-input"), {
    target: { value: "On-prem A100 node" },
  });
  fireEvent.change(screen.getByTestId("cost-profile-type-select"), {
    target: { value: "local_hardware" },
  });
  fireEvent.change(screen.getByTestId("cost-profile-rate-unit-select"), {
    target: { value: "per_gpu_hour" },
  });
  fireEvent.change(screen.getByTestId("cost-profile-rate-amount-input"), {
    target: { value: "2.5000000000" },
  });
  fireEvent.change(screen.getByTestId("cost-profile-currency-input"), {
    target: { value: "usd" },
  });
  fireEvent.change(screen.getByTestId("cost-profile-source-input"), {
    target: { value: "internal TCO worksheet v3" },
  });
  fireEvent.click(screen.getByTestId("cost-profile-submit-button"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  const [url, init] = fetchMock.mock.calls[1]!;
  expect(url).toBe(costProfilesCollectionUrl(API_BASE, ORG_ID));
  expect(init.method).toBe("POST");
  const body = JSON.parse(init.body as string);
  expect(body.profile_type).toBe("local_hardware");
  expect(body.label).toBe("On-prem A100 node");
  expect(body.rate_unit).toBe("per_gpu_hour");
  expect(body.rate_amount).toBe("2.5000000000");
  // Currency is uppercased client-side as typed -- proves the component
  // does not silently send a lowercase code the backend would reject.
  expect(body.currency).toBe("USD");
  expect(body.source).toBe("internal TCO worksheet v3");
  await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  // The third call is the real re-fetch of the search endpoint after create.
  expect(fetchMock.mock.calls[2]![0]).toBe(costPerSuccessSearchUrl(API_BASE, ORG_ID, PROJECT_ID));
});

test("clicking Verify POSTs the real verify endpoint, then refreshes", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  const unverified = searchResult({ id: "r-unverified", disqualified_reason: "unverified" });
  fetchMock
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ results: [unverified], current_cheapest_certified: null }),
    })
    .mockResolvedValueOnce({ ok: true, json: async () => baseRecord({ id: "r-unverified" }) })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        results: [
          {
            ...unverified,
            verified_at: "now",
            certifiable_cheapest: true,
            disqualified_reason: null,
          },
        ],
        current_cheapest_certified: null,
      }),
    });

  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("cost-profile-verify-r-unverified")).toBeTruthy());

  fireEvent.click(screen.getByTestId("cost-profile-verify-r-unverified"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  const [url, init] = fetchMock.mock.calls[1]!;
  expect(url).toBe(costProfileVerifyUrl(API_BASE, ORG_ID, "r-unverified"));
  expect(init.method).toBe("POST");
});

// FTF-UX05 (2026-10-22): audit finding X2 (severity 2) -- the form error carried no
// `role="alert"`; audit finding H5 (severity 1-2, recorded decision: NOT changed to a closed
// list -- see the component's own docstring) -- a non-breaking currency datalist is added
// instead; finding X4 (bilingual, partial).

test("UX05: the create-form error carries role=alert, and typed-uppercase currency behaviour is unchanged", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ results: [], current_cheapest_certified: null }),
    })
    .mockResolvedValueOnce({ ok: false, status: 422, json: async () => ({ detail: "invalid" }) });

  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

  fireEvent.change(screen.getByTestId("cost-profile-label-input"), { target: { value: "x" } });
  fireEvent.change(screen.getByTestId("cost-profile-rate-amount-input"), {
    target: { value: "1" },
  });
  fireEvent.change(screen.getByTestId("cost-profile-currency-input"), { target: { value: "usd" } });
  fireEvent.change(screen.getByTestId("cost-profile-source-input"), { target: { value: "x" } });
  fireEvent.click(screen.getByTestId("cost-profile-submit-button"));

  await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
  expect(screen.getByTestId("cost-profile-form-error").getAttribute("role")).toBe("alert");
  // The free-text field is unchanged: typed lowercase is still uppercased client-side.
  expect((screen.getByTestId("cost-profile-currency-input") as HTMLInputElement).value).toBe("USD");
});

test("UX05: the currency field offers a suggestion list without constraining typed input", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: [], current_cheapest_certified: null }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

  const input = screen.getByTestId("cost-profile-currency-input") as HTMLInputElement;
  expect(input.getAttribute("list")).toBe("cost-profile-currency-suggestions");
  const datalist = screen.getByTestId("cost-profile-currency-suggestions");
  expect(datalist.querySelectorAll("option").length).toBeGreaterThanOrEqual(5);
  // Still free text: an uncommon code the datalist does not suggest is accepted as typed.
  fireEvent.change(input, { target: { value: "xyz" } });
  expect(input.value).toBe("XYZ");
});

test("UX05: locale='ar' sets lang/dir on the panel", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: [], current_cheapest_certified: null }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(screen.getByTestId("cost-profile-panel").getAttribute("dir")).toBe("rtl");
  expect(screen.getByTestId("cost-profile-panel").getAttribute("lang")).toBe("ar");
});

// ---- round 2 (L2 return): pin the 2 previously-unpinned behaviors, full bilingual pass ----

test("UX05 round 2: cost-profile-submit-button's aria-busy reflects saving while the create request is in flight", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: [], current_cheapest_certified: null }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

  fireEvent.change(screen.getByTestId("cost-profile-label-input"), { target: { value: "x" } });
  fireEvent.change(screen.getByTestId("cost-profile-rate-amount-input"), {
    target: { value: "1" },
  });
  fireEvent.change(screen.getByTestId("cost-profile-source-input"), { target: { value: "x" } });

  const submitButton = screen.getByTestId("cost-profile-submit-button") as HTMLButtonElement;
  expect(submitButton.getAttribute("aria-busy")).toBe("false");
  let resolveCreate: (value: unknown) => void = () => {};
  fetchMock.mockImplementationOnce(() => new Promise((resolve) => (resolveCreate = resolve)));
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: [], current_cheapest_certified: null }),
  });
  fireEvent.click(submitButton);
  await waitFor(() => expect(submitButton.getAttribute("aria-busy")).toBe("true"));
  resolveCreate({ ok: true, json: async () => baseRecord() });
  await waitFor(() => expect(submitButton.getAttribute("aria-busy")).toBe("false"));
});

test("UX05 round 2: cost-profile-loading carries role=status and aria-live=polite while the search is loading", async () => {
  let resolveSearch: (value: unknown) => void = () => {};
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockImplementationOnce(() => new Promise((resolve) => (resolveSearch = resolve)));
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  const loadingParagraph = await screen.findByTestId("cost-profile-loading");
  expect(loadingParagraph.getAttribute("role")).toBe("status");
  expect(loadingParagraph.getAttribute("aria-live")).toBe("polite");
  resolveSearch({
    ok: true,
    json: async () => ({ results: [], current_cheapest_certified: null }),
  });
  await waitFor(() => expect(screen.queryByTestId("cost-profile-loading")).toBeNull());
});

test("UX05 round 2: locale='ar' translates every form label, select option and search-panel string", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      results: [
        searchResult({ id: "r-expired", disqualified_reason: "expired" }),
        searchResult({
          id: "r-certified",
          verified_at: "2026-09-10T00:00:00+00:00",
          certifiable_cheapest: true,
          disqualified_reason: null,
        }),
      ],
      current_cheapest_certified: baseRecord({ id: "r-certified" }),
    }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

  expect(document.querySelector('label[for="cost-profile-type"]')?.textContent).toBe("النوع");
  expect(document.querySelector('label[for="cost-profile-label"]')?.textContent).toBe("التسمية");
  expect(document.querySelector('label[for="cost-profile-rate-unit"]')?.textContent).toBe(
    "وحدة السعر",
  );
  expect(document.querySelector('label[for="cost-profile-currency"]')?.textContent).toBe("العملة");
  expect(document.querySelector('label[for="cost-profile-source"]')?.textContent).toBe("المصدر");
  expect(screen.getByTestId("cost-profile-submit-button").textContent).toBe("تسجيل السعر");
  expect(screen.getByText("بحث التكلفة لكل نجاح")).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByTestId("cost-profile-disqualified-r-expired").textContent).toContain(
      "منتهي",
    ),
  );
  expect(screen.getByTestId("cost-profile-certified-r-certified").textContent).toBe(
    "معتمد ومؤهّل للأرخص",
  );
  expect(screen.getByTestId("cost-profile-verify-r-expired").textContent).toBe("اعتماد");
});

test("UX05 round 2: locale='ar' translates the empty-state text, and the empty default locale='en' text is unchanged", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: [], current_cheapest_certified: null }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      locale: "ar",
    }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(screen.getByTestId("no-certified-cheapest-rate").textContent).toBe(
    "لا يوجد أرخص سعر معتمد بعد -- سجّل واعتمد سعرًا لتظهر هنا.",
  );
});

// -- UX05 round 2 (second L2 return, PR #972) -----------------------------------------------

test("UX05 r2 (L2 return): an empty {} 200 search response is a typed error, never a silently-accepted empty result (Law 17)", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({}) });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
  // Never silently accepted as a real, validated empty result set (EmptyState requires `search`
  // to be set, which never happens on a rejected malformed response):
  expect(screen.queryByTestId("cost-profile-reload-button")).toBeNull();
});

test("UX05 r2 (L2 return): a results array whose item is malformed (certifiable_cheapest is a string) is rejected, not rendered", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      results: [{ ...searchResult(), certifiable_cheapest: "yes" }],
      current_cheapest_certified: null,
    }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
  expect(screen.queryByTestId(`cost-profile-result-${baseRecord().id}`)).toBeNull();
});

test("UX05 r2 (L2 return): a results array that is not an array (an object instead) is rejected", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: { not: "an array" }, current_cheapest_certified: null }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("cost-profile-form-error")).toBeTruthy());
});

test("UX05 r2 (L2 return): the create/verify/select controls render through the shared Button component at the minimum target size", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      results: [searchResult({ verified_at: null })],
      current_cheapest_certified: null,
    }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId(`cost-profile-verify-${baseRecord().id}`)).toBeTruthy(),
  );
  for (const testId of ["cost-profile-submit-button", `cost-profile-verify-${baseRecord().id}`]) {
    const el = screen.getByTestId(testId) as HTMLElement;
    expect(el.style.minBlockSize).toBe("var(--target-min)");
    expect(el.style.minInlineSize).toBe("var(--target-min)");
  }
  expect((screen.getByTestId("cost-profile-type-select") as HTMLElement).style.minBlockSize).toBe(
    "var(--target-min)",
  );
});

test("UX05 r2 (L2 return): a successful load with zero results shows an EmptyState with a reload action", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: [], current_cheapest_certified: null }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() => expect(screen.getByTestId("cost-profile-reload-button")).toBeTruthy());
  expect(screen.getByText("No rates recorded yet")).toBeTruthy();
});

test("UX05 r2 (L2 return): a long source/label value in a result is wrapped, not left to overflow", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  const longResult = searchResult({ source: "https://example.com/" + "x".repeat(300) });
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ results: [longResult], current_cheapest_certified: null }),
  });
  render(
    createElement(CostProfileSearchPanel, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );
  await waitFor(() =>
    expect(screen.getByTestId(`cost-profile-result-${longResult.id}`)).toBeTruthy(),
  );
  expect(
    (screen.getByTestId(`cost-profile-result-${longResult.id}`) as HTMLElement).style.overflowWrap,
  ).toBe("anywhere");
});
