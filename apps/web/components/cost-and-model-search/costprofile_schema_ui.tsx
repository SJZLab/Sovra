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
 * (finding X4, partial).
 */

import { useEffect, useState } from "react";

import { dirFor, resolveLocale, type Locale } from "../ui/phrases";

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

const DISQUALIFIED_REASON_TEXT: Record<Exclude<DisqualifiedReason, null>, string> = {
  expired: "Expired -- this rate is no longer current.",
  unverified: "Unverified -- nobody has independently confirmed this rate yet.",
  zero_provider_rate_unconfirmed:
    "A provider price of exactly zero is never treated as certified-free (a $0 token price is not local-compute-free).",
};

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // fall through to the generic message below
  }
  return `request failed (${response.status})`;
}

export function CostProfileSearchPanel({
  apiBaseUrl,
  organizationId,
  projectId,
  onCreated,
  locale = "en",
}: CostProfilePanelProps) {
  const [search, setSearch] = useState<CostPerSuccessSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [profileType, setProfileType] = useState<ProfileType>("provider");
  const [label, setLabel] = useState("");
  const [rateUnit, setRateUnit] = useState<RateUnit>("per_input_token");
  const [rateAmount, setRateAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [source, setSource] = useState("");
  const [saving, setSaving] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(costPerSuccessSearchUrl(apiBaseUrl, organizationId, projectId), {
        credentials: "include",
      });
      if (!response.ok) {
        setError(await readErrorDetail(response));
        return;
      }
      setSearch(await response.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [apiBaseUrl, organizationId, projectId]);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
        setError(await readErrorDetail(response));
        return;
      }
      const created: CostProfileRecord = await response.json();
      setLabel("");
      setRateAmount("");
      setSource("");
      onCreated?.(created);
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  async function handleVerify(costProfileId: string) {
    setError(null);
    const response = await fetch(costProfileVerifyUrl(apiBaseUrl, organizationId, costProfileId), {
      method: "POST",
      credentials: "include",
    });
    if (!response.ok) {
      setError(await readErrorDetail(response));
      return;
    }
    await refresh();
  }

  return (
    <div data-testid="cost-profile-panel" lang={resolveLocale(locale)} dir={dirFor(locale)}>
      <form onSubmit={handleCreate} data-testid="cost-profile-create-form">
        <label htmlFor="cost-profile-type">Type</label>
        <select
          id="cost-profile-type"
          data-testid="cost-profile-type-select"
          value={profileType}
          onChange={(event) => setProfileType(event.target.value as ProfileType)}
        >
          <option value="provider">Provider</option>
          <option value="local_hardware">Local hardware</option>
        </select>

        <label htmlFor="cost-profile-label">Label</label>
        <input
          id="cost-profile-label"
          data-testid="cost-profile-label-input"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          required
        />

        <label htmlFor="cost-profile-rate-unit">Rate unit</label>
        <select
          id="cost-profile-rate-unit"
          data-testid="cost-profile-rate-unit-select"
          value={rateUnit}
          onChange={(event) => setRateUnit(event.target.value as RateUnit)}
        >
          <option value="per_input_token">Per input token</option>
          <option value="per_output_token">Per output token</option>
          <option value="per_gpu_hour">Per GPU hour</option>
          <option value="per_cpu_hour">Per CPU hour</option>
          <option value="per_ram_gb_hour">Per RAM GB-hour</option>
          <option value="per_hour">Per hour</option>
          <option value="per_request">Per request</option>
        </select>

        <label htmlFor="cost-profile-rate-amount">Rate amount</label>
        <input
          id="cost-profile-rate-amount"
          data-testid="cost-profile-rate-amount-input"
          value={rateAmount}
          onChange={(event) => setRateAmount(event.target.value)}
          required
        />

        <label htmlFor="cost-profile-currency">Currency</label>
        <input
          id="cost-profile-currency"
          data-testid="cost-profile-currency-input"
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

        <label htmlFor="cost-profile-source">Source</label>
        <input
          id="cost-profile-source"
          data-testid="cost-profile-source-input"
          value={source}
          onChange={(event) => setSource(event.target.value)}
          required
        />

        {error && (
          <p
            role="alert"
            data-testid="cost-profile-form-error"
            lang={resolveLocale(locale)}
            dir={dirFor(locale)}
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          data-testid="cost-profile-submit-button"
          disabled={saving}
          aria-busy={saving}
        >
          Record rate
        </button>
      </form>

      <section data-testid="cost-per-success-search-results">
        <h2>Cost-per-success search</h2>
        {loading && (
          <p role="status" aria-live="polite" data-testid="cost-profile-loading">
            Loading...
          </p>
        )}

        <div data-testid="current-cheapest-certified">
          {search?.current_cheapest_certified ? (
            <p>
              Cheapest certified rate: {search.current_cheapest_certified.label} --{" "}
              {search.current_cheapest_certified.rate_amount}{" "}
              {search.current_cheapest_certified.currency} /{" "}
              {search.current_cheapest_certified.rate_unit}
            </p>
          ) : (
            <p data-testid="no-certified-cheapest-rate">
              No certified cheapest rate yet -- record and verify a rate to see one here.
            </p>
          )}
        </div>

        <ul data-testid="cost-profile-result-list">
          {(search?.results ?? []).map((result) => (
            <li key={result.id} data-testid={`cost-profile-result-${result.id}`}>
              <span>
                {result.label} -- {result.rate_amount} {result.currency} / {result.rate_unit}
              </span>
              {result.certifiable_cheapest ? (
                <span data-testid={`cost-profile-certified-${result.id}`}>
                  Certified cheapest-eligible
                </span>
              ) : (
                <span data-testid={`cost-profile-disqualified-${result.id}`}>
                  {result.disqualified_reason
                    ? DISQUALIFIED_REASON_TEXT[result.disqualified_reason]
                    : null}
                </span>
              )}
              {result.verified_at === null && (
                <button
                  type="button"
                  data-testid={`cost-profile-verify-${result.id}`}
                  onClick={() => handleVerify(result.id)}
                >
                  Verify
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
