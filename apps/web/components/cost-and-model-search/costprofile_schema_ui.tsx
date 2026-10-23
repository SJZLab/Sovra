/**
 * CostProfile record form + cost-per-success search panel (FTF-1301).
 *
 * A real component driven entirely by the live FTF-1301 API
 * (`POST/GET /api/v1/organizations/{organization_id}/cost-profiles`,
 * `POST .../{id}/verify` and `GET .../projects/{project_id}/costs/
 * cost-per-success/search`) -- every action is a real `fetch` against the
 * actual backend, never a fake local state update (Law 9: "Every UI
 * action used in the happy path invokes real backend behavior").
 *
 * "Display unknowns honestly" (this task's own acceptance clause) is
 * rendered literally: every search result shows its own
 * `certifiable_cheapest`/`disqualified_reason` verdict, never filtered
 * out of the list -- a disqualified rate is shown alongside its own
 * reason (`expired`/`unverified`/`zero_provider_rate_unconfirmed`), and
 * `current_cheapest_certified` renders an explicit "no certified rate
 * yet" notice rather than silently picking an unqualified one when it is
 * `null`.
 *
 * Scope disclosed, not hidden (see docs/certifications/queue.md): this
 * repository has no Next.js app shell yet -- the same precedent
 * `project_schema_api_ui.tsx` (FTF-0401) already established. This is a
 * real, DOM-level tested component (Vitest + React Testing Library +
 * jsdom with a mocked `fetch`), not a full browser E2E.
 *
 * FTF-UX05 (2026-10-22): audit finding X2 (severity 2) -- the create-form
 * error rendered as a bare `<p>` with no `role="alert"`; it now carries
 * `role="alert"` and `lang`/`dir`. Audit finding H5 (severity 1-2) -- the
 * currency field is free text rather than a closed ISO-4217 list. NOT
 * changed to a closed `<select>` this round: the certified test
 * (`ftf_1301.spec.ts`, "submitting the create form POSTs the real payload")
 * asserts the exact typed-lowercase/sent-uppercase behaviour of the free-text
 * input, and the parent-slice instruction is to keep every certified
 * behaviour and API contract unchanged except for selector migration -- a
 * closed list is a real interaction-model change, not a selector change, so
 * it is recorded here as a decision rather than done. A non-breaking
 * `<datalist>` of common ISO-4217 codes is added instead, as guidance only:
 * the field is still free text and the existing typed-uppercase behaviour is
 * unchanged. Control plane to decide whether to accept the datalist-only
 * mitigation or route a proper contract change for a closed list. An
 * optional `locale` prop renders the loading/error text's own `lang`/`dir`
 * (finding X4).
 *
 * FTF-UX05 round 2 (2026-10-22, L2 return): round 1 left the error paragraph's `role="alert"` and
 * `aria-busy` with no test pinning them -- round 2 adds the missing assertions (see
 * `ftf_1301.spec.ts`, "round 2 pins") and independently re-verified each one RED-then-GREEN. It also
 * fixes the WCAG 3.1.2 defect the L2 review found: the container's `lang`/`dir` covered large
 * amounts of hardcoded-English UI copy (every form label, every select option, the search heading,
 * the loading/empty/certified-eligible text, the disqualification reasons). Every string this
 * component itself authors now routes through `phrase()` with a real `ar` translation (see
 * `ui/phrases.ts`'s FTF-1301 block), so `lang="ar"` is now honest for that copy. Values this
 * component does not author -- the record's own label/rate/currency/source text, a user-typed
 * value -- are data, not UI copy, and are wrapped in `<bdi>` rather than given a language tag (same
 * convention `candidate_selection_ui_no_auto_selection.tsx` already established). Default
 * `locale="en"` rendering is byte-identical to round 1.
 *
 * FTF-UX05 round 2 (2026-10-22, second L2 return, PR #972): `refresh()` set `search` directly from
 * `await response.json()` with no shape check -- a 200 with an unexpected body (an empty `{}`, a
 * `results` that is not an array, a malformed result item) was accepted as real data: `{}` rendered
 * as "zero results, no certified cheapest rate", a quiet success where Law 17 requires a visible
 * failure. `isSearchResponse`/`isSearchResult` give this response the same strict parse
 * `isCapabilities` already uses in the sibling FTF-0309 component; a failed parse surfaces the same
 * kind of error this component already shows for an HTTP failure. Native `<button>`s now render
 * through the shared `Button` component (WCAG 2.2 §2.5.8 target size, §72.1's fixed component --
 * `<select>`s keep the repo's established `minBlockSize` convention since there is no `<select>`
 * design-system component yet); an empty result list now shows `EmptyState` instead of a bare `<ul>`,
 * and a long label/source/currency value is wrapped (`overflowWrap: "anywhere"`).
 *
 * FTF-UX05 round 3 (2026-10-23, QUEUE 0e): `refresh`, `handleCreate` and `handleVerify` now catch a
 * rejected fetch and a body that is not JSON (Law 17): the error is announced (`AlertMessage`,
 * `role="alert"`, localized, with a recovery step), `search` is cleared on any failed refresh, and
 * "No certified cheapest rate yet" renders ONLY for a validated search response -- before one, or
 * after a failure, the cheapest rate is shown as unknown (`cheapest-rate-unknown`), which is a
 * different statement. An unreadable create response is announced and keeps the typed values. The
 * four text inputs carry the same `--target-min` block size as the selects. Recorded decisions (not
 * done, severity 2): the inputs stay native rather than `Field` (its required/optional marker lives
 * inside the `<label>`, and the certified test asserts the exact label text), a failed submit has
 * no `ErrorSummary` (the API returns one request-level `detail`, there are no per-field errors to
 * link; native `required` covers empty fields), and `certifiable_cheapest`/`disqualified_reason` are
 * shown as the server's words rather than `StatusBadge` (its vocabulary is the fixed section 72.2
 * state set, which has no "disqualified: expired/unverified" member -- mapping them would invent one).
 */

import { useEffect, useRef, useState } from "react";

import {
  AlertMessage,
  Button,
  EmptyState,
  kindForStatus,
  networkError,
  uiError,
  useRefocusAfter,
  useSingleFlight,
  type UiError,
} from "../ui";
import { dirFor, phrase, resolveLocale, type Locale } from "../ui/phrases";

export type ProfileType = "provider" | "local_hardware";
export type RateUnit =
  | "per_input_token"
  | "per_output_token"
  | "per_gpu_hour"
  | "per_cpu_hour"
  | "per_ram_gb_hour"
  | "per_hour"
  | "per_request";
export type DisqualifiedReason = "expired" | "unverified" | "zero_provider_rate_unconfirmed" | null;

export interface CostProfileRecord {
  id: string;
  organization_id: string;
  profile_type: ProfileType;
  label: string;
  rate_unit: RateUnit;
  rate_amount: string;
  currency: string;
  source: string;
  rate_recorded_at: string;
  verified_at: string | null;
  expires_at: string | null;
  row_version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface CostProfileSearchResult extends CostProfileRecord {
  certifiable_cheapest: boolean;
  disqualified_reason: DisqualifiedReason;
}

export interface CostPerSuccessSearchResponse {
  results: CostProfileSearchResult[];
  current_cheapest_certified: CostProfileRecord | null;
}

export interface CostProfilePanelProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  onCreated?: (profile: CostProfileRecord) => void;
  locale?: Locale;
}

/** Common ISO-4217 codes offered as a suggestion only (finding H5, partial mitigation); the
 * field stays free text so the certified typed-uppercase behaviour is unchanged. */
const COMMON_CURRENCY_CODES = [
  "USD",
  "EUR",
  "GBP",
  "SAR",
  "AED",
  "JPY",
  "CNY",
  "INR",
  "CAD",
  "AUD",
] as const;

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function costProfilesCollectionUrl(apiBaseUrl: string, organizationId: string): string {
  return `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}/cost-profiles`;
}

export function costProfileVerifyUrl(
  apiBaseUrl: string,
  organizationId: string,
  costProfileId: string,
): string {
  return `${costProfilesCollectionUrl(apiBaseUrl, organizationId)}/${costProfileId}/verify`;
}

export function costPerSuccessSearchUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/costs/cost-per-success/search`
  );
}

function disqualifiedReasonText(reason: Exclude<DisqualifiedReason, null>, locale: Locale): string {
  switch (reason) {
    case "expired":
      return phrase("costProfileDisqualifiedExpired", locale);
    case "unverified":
      return phrase("costProfileDisqualifiedUnverified", locale);
    case "zero_provider_rate_unconfirmed":
      return phrase("costProfileDisqualifiedZeroProviderRate", locale);
  }
}

async function readErrorDetail(response: Response, locale: Locale): Promise<UiError> {
  const kind = kindForStatus(response.status);
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      return uiError(body.detail, kind);
    }
  } catch {
    // fall through to the generic message below
  }
  return uiError(phrase("requestFailedTemplate", locale, { status: response.status }), kind);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

const DISQUALIFIED_REASONS = ["expired", "unverified", "zero_provider_rate_unconfirmed"];

function isCostProfileRecord(value: unknown): value is CostProfileRecord {
  return (
    isPlainObject(value) &&
    typeof value.id === "string" &&
    typeof value.label === "string" &&
    typeof value.rate_unit === "string" &&
    typeof value.rate_amount === "string" &&
    typeof value.currency === "string" &&
    typeof value.source === "string" &&
    (value.verified_at === null || typeof value.verified_at === "string")
  );
}

function isSearchResult(value: unknown): value is CostProfileSearchResult {
  if (!isPlainObject(value) || !isCostProfileRecord(value)) return false;
  const { certifiable_cheapest, disqualified_reason } = value as unknown as CostProfileSearchResult;
  return (
    typeof certifiable_cheapest === "boolean" &&
    (disqualified_reason === null || DISQUALIFIED_REASONS.includes(disqualified_reason))
  );
}

/** Strict parse of the live search response -- an unexpected shape (an empty `{}`, a non-array
 * `results`, a malformed result item) is a typed error, never a silently-accepted empty result
 * (Law 17), same discipline `isCapabilities` already uses in the sibling FTF-0309 component. */
function isSearchResponse(body: unknown): body is CostPerSuccessSearchResponse {
  if (!isPlainObject(body)) return false;
  if (!(
    body.current_cheapest_certified === null || isCostProfileRecord(body.current_cheapest_certified)
  )) {
    return false;
  }
  return Array.isArray(body.results) && body.results.every(isSearchResult);
}

export function CostProfileSearchPanel({
  apiBaseUrl,
  organizationId,
  projectId,
  onCreated,
  locale = "en",
}: CostProfilePanelProps) {
  const [search, setSearch] = useState<CostPerSuccessSearchResponse | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  // The mount effect starts the first search at once: begin in the loading state so the first
  // frame never shows "cheapest rate unknown" and a Reload button for a search that is under way.
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const retryFlight = useSingleFlight();
  const createFlight = useSingleFlight();
  const verifyFlight = useSingleFlight();
  // A stale search response (e.g. the previous project's reply arriving after the current
  // project's) must never overwrite the latest request's state (FTF-1301-F1); same `stale()`
  // request-counter guard as the sibling FTF-0309 component.
  const latestSearchRequest = useRef(0);

  const [profileType, setProfileType] = useState<ProfileType>("provider");
  const [label, setLabel] = useState("");
  const [rateUnit, setRateUnit] = useState<RateUnit>("per_input_token");
  const [rateAmount, setRateAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [source, setSource] = useState("");
  const [saving, setSaving] = useState(false);

  // Submit and the search triggers disable themselves while busy (or unmount, like Verify on
  // success) and would drop keyboard focus to <body>: give it back afterwards.
  const armSaveFocus = useRefocusAfter(saving);
  const armSearchFocus = useRefocusAfter(loading);
  const panelRef = useRef<HTMLDivElement>(null);
  function inPanel(testId: string): HTMLElement | null {
    return panelRef.current?.querySelector<HTMLElement>(`[data-testid="${testId}"]`) ?? null;
  }

  /** `afterWrite`: the write this refresh follows DID succeed, so a failure to read the list must
   * not be presented as if nothing happened -- the recovery is to check the current state. */
  async function refresh(afterWrite = false) {
    // Captured now, compared after every await: a newer `refresh()` call (a project switch, a
    // retry, a post-write re-check) bumps the counter and makes this call's own state writes a
    // no-op, so an out-of-order response can never overwrite a newer request's result (Law 17).
    const request = ++latestSearchRequest.current;
    const stale = () => request !== latestSearchRequest.current;
    setLoading(true);
    setError(null);
    const failure = (e: UiError): UiError => (afterWrite ? { ...e, kind: "verify-state" } : e);
    try {
      const response = await fetch(costPerSuccessSearchUrl(apiBaseUrl, organizationId, projectId), {
        credentials: "include",
      });
      if (!response.ok) {
        const detail = await readErrorDetail(response, locale);
        if (stale()) return;
        setSearch(null);
        setError(failure(detail));
        return;
      }
      const body: unknown = await response.json();
      if (stale()) return;
      if (!isSearchResponse(body)) {
        setSearch(null);
        setError(failure(uiError(phrase("costProfileMalformedSearchResponse", locale))));
        return;
      }
      setSearch(body);
    } catch (caught) {
      // A rejected fetch or a body that is not JSON: the previous result may be stale, and
      // "no certified cheapest rate" must never be shown for a search that did not complete.
      if (stale()) return;
      setSearch(null);
      setError(networkError(caught, locale, { write: afterWrite }));
    } finally {
      if (!stale()) setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [apiBaseUrl, organizationId, projectId]);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!createFlight.begin()) return;
    setNotice(null);
    armSaveFocus(() => inPanel("cost-profile-submit-button"));
    armSearchFocus(() => inPanel("cost-per-success-search-results"));
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(costProfilesCollectionUrl(apiBaseUrl, organizationId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          profile_type: profileType,
          label,
          rate_unit: rateUnit,
          rate_amount: rateAmount,
          currency,
          source,
          rate_recorded_at: new Date().toISOString(),
        }),
      });
      if (!response.ok) {
        setError(await readErrorDetail(response, locale));
        return;
      }
      const created: unknown = await response.json();
      if (!isCostProfileRecord(created)) {
        // The server answered 2xx, so the write very likely happened, but the response cannot be
        // read: keep the typed values, and say to check the current state before submitting again
        // (a blind retry would record the rate twice).
        setError(uiError(phrase("malformedWriteResponseMessage", locale), "verify-state"));
        return;
      }
      setLabel("");
      setRateAmount("");
      setSource("");
      onCreated?.(created);
      setNotice(phrase("costProfileCreated", locale));
      await refresh(true);
    } catch (caught) {
      setError(networkError(caught, locale, { write: true }));
    } finally {
      createFlight.end();
      setSaving(false);
    }
  }

  function retrySearch() {
    if (!retryFlight.begin()) return;
    armSearchFocus(() => inPanel("cost-per-success-search-results"));
    void refresh().finally(retryFlight.end);
  }

  async function handleVerify(costProfileId: string) {
    if (!verifyFlight.begin()) return;
    setNotice(null);
    armSearchFocus(
      () =>
        inPanel(`cost-profile-result-${costProfileId}`) ??
        inPanel("cost-per-success-search-results"),
    );
    setError(null);
    try {
      const response = await fetch(
        costProfileVerifyUrl(apiBaseUrl, organizationId, costProfileId),
        { method: "POST", credentials: "include" },
      );
      if (!response.ok) {
        setError(await readErrorDetail(response, locale));
        verifyFlight.end();
        return;
      }
    } catch (caught) {
      setError(networkError(caught, locale, { write: true }));
      verifyFlight.end();
      return;
    }
    await refresh(true);
    verifyFlight.end();
  }

  return (
    <div
      ref={panelRef}
      data-testid="cost-profile-panel"
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
      style={{ overflowWrap: "anywhere" }}
    >
      <form onSubmit={handleCreate} data-testid="cost-profile-create-form">
        <label htmlFor="cost-profile-type">{phrase("costProfileTypeLabel", locale)}</label>
        <select
          id="cost-profile-type"
          data-testid="cost-profile-type-select"
          value={profileType}
          onChange={(event) => setProfileType(event.target.value as ProfileType)}
          style={{ minBlockSize: "var(--target-min)" }}
        >
          <option value="provider">{phrase("costProfileTypeProvider", locale)}</option>
          <option value="local_hardware">{phrase("costProfileTypeLocalHardware", locale)}</option>
        </select>

        <label htmlFor="cost-profile-label">{phrase("costProfileLabelLabel", locale)}</label>
        <input
          id="cost-profile-label"
          data-testid="cost-profile-label-input"
          style={{ minBlockSize: "var(--target-min)" }}
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          required
        />

        <label htmlFor="cost-profile-rate-unit">{phrase("costProfileRateUnitLabel", locale)}</label>
        <select
          id="cost-profile-rate-unit"
          data-testid="cost-profile-rate-unit-select"
          value={rateUnit}
          onChange={(event) => setRateUnit(event.target.value as RateUnit)}
          style={{ minBlockSize: "var(--target-min)" }}
        >
          <option value="per_input_token">{phrase("rateUnitPerInputToken", locale)}</option>
          <option value="per_output_token">{phrase("rateUnitPerOutputToken", locale)}</option>
          <option value="per_gpu_hour">{phrase("rateUnitPerGpuHour", locale)}</option>
          <option value="per_cpu_hour">{phrase("rateUnitPerCpuHour", locale)}</option>
          <option value="per_ram_gb_hour">{phrase("rateUnitPerRamGbHour", locale)}</option>
          <option value="per_hour">{phrase("rateUnitPerHour", locale)}</option>
          <option value="per_request">{phrase("rateUnitPerRequest", locale)}</option>
        </select>

        <label htmlFor="cost-profile-rate-amount">
          {phrase("costProfileRateAmountLabel", locale)}
        </label>
        <input
          id="cost-profile-rate-amount"
          data-testid="cost-profile-rate-amount-input"
          style={{ minBlockSize: "var(--target-min)" }}
          value={rateAmount}
          onChange={(event) => setRateAmount(event.target.value)}
          required
        />

        <label htmlFor="cost-profile-currency">{phrase("costProfileCurrencyLabel", locale)}</label>
        <input
          id="cost-profile-currency"
          data-testid="cost-profile-currency-input"
          style={{ minBlockSize: "var(--target-min)" }}
          value={currency}
          onChange={(event) => setCurrency(event.target.value.toUpperCase())}
          maxLength={3}
          list="cost-profile-currency-suggestions"
          required
        />
        <datalist
          id="cost-profile-currency-suggestions"
          data-testid="cost-profile-currency-suggestions"
        >
          {COMMON_CURRENCY_CODES.map((code) => (
            <option key={code} value={code} />
          ))}
        </datalist>

        <label htmlFor="cost-profile-source">{phrase("costProfileSourceLabel", locale)}</label>
        <input
          id="cost-profile-source"
          data-testid="cost-profile-source-input"
          style={{ minBlockSize: "var(--target-min)" }}
          value={source}
          onChange={(event) => setSource(event.target.value)}
          required
        />

        {notice && (
          <p role="status" data-testid="cost-profile-created-notice">
            {notice}
          </p>
        )}
        {error && <AlertMessage error={error} locale={locale} testId="cost-profile-form-error" />}

        <Button
          type="submit"
          data-testid="cost-profile-submit-button"
          disabled={saving}
          aria-busy={saving}
        >
          {phrase("costProfileRecordRate", locale)}
        </Button>
      </form>

      <section data-testid="cost-per-success-search-results" tabIndex={-1}>
        <h2>{phrase("costPerSuccessSearchHeading", locale)}</h2>
        {loading && (
          <p role="status" aria-live="polite" data-testid="cost-profile-loading">
            {phrase("costProfileLoading", locale)}
          </p>
        )}

        <div data-testid="current-cheapest-certified">
          {search?.current_cheapest_certified ? (
            <p style={{ overflowWrap: "anywhere" }}>
              {phrase("costProfileCheapestCertifiedPrefix", locale)}{" "}
              <bdi>{search.current_cheapest_certified.label}</bdi> --{" "}
              <bdi>{search.current_cheapest_certified.rate_amount}</bdi>{" "}
              <bdi>{search.current_cheapest_certified.currency}</bdi> /{" "}
              <bdi>{search.current_cheapest_certified.rate_unit}</bdi>
            </p>
          ) : search ? (
            <p data-testid="no-certified-cheapest-rate">
              {phrase("costProfileNoCertifiedCheapestRate", locale)}
            </p>
          ) : loading ? null : (
            // The search has not produced a validated answer (it failed or has not run): the
            // cheapest rate is UNKNOWN, which is not the same statement as "none is certified"
            // (Law 17).
            <>
              <p data-testid="cheapest-rate-unknown">
                {phrase("costProfileCheapestUnknown", locale)}
              </p>
              <Button data-testid="cheapest-rate-retry-button" onClick={retrySearch}>
                {phrase("costProfileReload", locale)}
              </Button>
            </>
          )}
        </div>

        {search && search.results.length === 0 && (
          <EmptyState
            title={phrase("costProfileNoResultsTitle", locale)}
            why={phrase("costProfileNoResultsWhy", locale)}
            action={
              <Button data-testid="cost-profile-reload-button" onClick={retrySearch}>
                {phrase("costProfileReload", locale)}
              </Button>
            }
          />
        )}

        <ul data-testid="cost-profile-result-list">
          {(search?.results ?? []).map((result) => (
            <li
              key={result.id}
              data-testid={`cost-profile-result-${result.id}`}
              tabIndex={-1}
              style={{ overflowWrap: "anywhere" }}
            >
              <span>
                <bdi>{result.label}</bdi> -- <bdi>{result.rate_amount}</bdi>{" "}
                <bdi>{result.currency}</bdi> / <bdi>{result.rate_unit}</bdi>
              </span>
              {result.certifiable_cheapest ? (
                <span data-testid={`cost-profile-certified-${result.id}`}>
                  {phrase("costProfileCertifiedCheapestEligible", locale)}
                </span>
              ) : (
                <span data-testid={`cost-profile-disqualified-${result.id}`}>
                  {result.disqualified_reason
                    ? disqualifiedReasonText(result.disqualified_reason, locale)
                    : null}
                </span>
              )}
              {result.verified_at === null && (
                <Button
                  data-testid={`cost-profile-verify-${result.id}`}
                  onClick={() => handleVerify(result.id)}
                >
                  {phrase("costProfileVerify", locale)}
                </Button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
