/**
 * Requirements review panel (FTF-0408).
 *
 * Assembles three already-live, already-CERTIFIED surfaces for one
 * project into a single review screen, exactly as FTF-0407's own module
 * docstring named this task: "FTF-0408's own later 'Requirements UI'
 * page that assembles this alongside project privacy/budget policy."
 *
 *  - FTF-0407's real, read-only `GET .../requirements` critical-behavior
 *    view (`derive_critical_behavior_requirement_view`) -- every tool's
 *    declared (`is_critical`/`requires_confirmation`) state next to its
 *    own `runtime_enforced` state. This view is derived, never re-decided
 *    here (see `critical_behavior_requirement_routes.py`'s own module
 *    docstring) -- a tool declared critical/requires-confirmation but not
 *    runtime-enforced is a real, surfaced conflict, never silently
 *    resolved (this task's own negative_acceptance: "show observed/
 *    desired/enforced conflicts").
 *  - FTF-0403's real budget-policy `POST`/`GET`/`PATCH` surface --
 *    `total_amount`/`scopes` are genuinely editable here (this task's
 *    own acceptance: "User edits ... scope ... with validation, version
 *    preview"); `currency` is only settable at creation, matching the
 *    backend's own `revise_budget_policy` contract, which does not
 *    accept a currency change. "Version preview" is the real
 *    `expected_row_version` sent with every revision -- shown to the
 *    user as the budget policy's own `row_version` before they submit,
 *    the same optimistic-concurrency discipline `project_schema_api_ui.tsx`
 *    (FTF-0401) already established, so a stale concurrent edit is
 *    refused by the backend (409) rather than silently overwritten.
 *  - The project's own `security_profile` (FTF-0401/FTF-0402), rendered
 *    read-only with a pointer to where it is actually edited --
 *    `ProjectSetupForm` already owns that one real write path; this
 *    panel does not open a second, competing surface for the same field.
 *
 * Scope disclosed, not hidden (see docs/certifications/queue.md): the
 * task packet's own acceptance clause additionally names "mandate/
 * languages/outputs/SLO" editing and "explicit lock after model/runtime
 * resolution". Section 6.8D's own `AgentManifest` (`mandate_json`,
 * `forbidden_actions_json`, locking) is not real storage anywhere in
 * this repository yet -- `critical_behavior_requirement_routes.py`'s own
 * module docstring says so explicitly, and no later task has built it.
 * A UI control that appeared to save a mandate/language/output/SLO field
 * or "lock" anything with no real backing table would be exactly the
 * "production placeholder path" Law 11 forbids. Those fields are out of
 * scope here, for the future AgentManifest-authoring task Section 7A.6/
 * 7A.7 describe.
 *
 * A real, DOM-level tested component (Vitest + React Testing Library +
 * jsdom with a mocked `fetch`), not a full browser E2E -- the same
 * precedent `project_schema_api_ui.tsx`/`costprofile_schema_ui.tsx`
 * already established (no Next.js app shell exists yet in this
 * repository).
 */

import { useEffect, useState } from "react";

export interface CriticalBehaviorRequirementView {
  tool_definition_id: string;
  name: string;
  side_effect_class: string;
  is_critical: boolean;
  requires_confirmation: boolean;
  measurable: boolean;
  runtime_enforced: boolean;
}

export interface BudgetPolicyRecord {
  id: string;
  organization_id: string;
  project_id: string;
  total_amount: string;
  currency: string;
  scopes: string[];
  reserved_amount: string;
  available_amount: string;
  row_version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

// Section 41's own named Temporal workflow list -- the same closed
// vocabulary `budget_policy_schema.py::BUDGET_SCOPES` already defines on
// the backend. Redeclared here rather than imported: this repository has
// no shared TS/Python contract package for domain enums yet, the same
// precedent `project_schema_api_ui.tsx` already established for
// `ProjectType`/`ProjectStatus`.
export const BUDGET_SCOPES = ["training", "evaluation", "serving"] as const;
export type BudgetScope = (typeof BUDGET_SCOPES)[number];

export interface RequirementsPanelProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  securityProfile: string;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function requirementsUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/requirements`
  );
}

export function budgetPolicyUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/budget-policy`
  );
}

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

export function RequirementsPanel({
  apiBaseUrl,
  organizationId,
  projectId,
  securityProfile,
}: RequirementsPanelProps) {
  const [requirements, setRequirements] = useState<CriticalBehaviorRequirementView[] | null>(null);
  const [requirementsError, setRequirementsError] = useState<string | null>(null);

  const [budgetPolicy, setBudgetPolicy] = useState<BudgetPolicyRecord | null>(null);
  const [budgetPolicyMissing, setBudgetPolicyMissing] = useState(false);
  const [budgetError, setBudgetError] = useState<string | null>(null);

  const [totalAmount, setTotalAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [scopes, setScopes] = useState<Set<BudgetScope>>(new Set());
  const [saving, setSaving] = useState(false);

  async function refreshRequirements() {
    setRequirementsError(null);
    const response = await fetch(requirementsUrl(apiBaseUrl, organizationId, projectId), {
      credentials: "include",
    });
    if (!response.ok) {
      setRequirementsError(await readErrorDetail(response));
      return;
    }
    setRequirements(await response.json());
  }

  async function refreshBudgetPolicy() {
    setBudgetError(null);
    const response = await fetch(budgetPolicyUrl(apiBaseUrl, organizationId, projectId), {
      credentials: "include",
    });
    if (response.status === 404) {
      setBudgetPolicyMissing(true);
      setBudgetPolicy(null);
      return;
    }
    if (!response.ok) {
      setBudgetError(await readErrorDetail(response));
      return;
    }
    const policy: BudgetPolicyRecord = await response.json();
    setBudgetPolicyMissing(false);
    setBudgetPolicy(policy);
    setTotalAmount(policy.total_amount);
    setCurrency(policy.currency);
    setScopes(new Set(policy.scopes as BudgetScope[]));
  }

  // Deliberately runs once per identity triple, not on every render --
  // re-fetching on every keystroke would repeatedly clobber the user's
  // own in-progress budget-policy edit below (the draft-preservation
  // guarantee this task's own negative_acceptance requires).
  useEffect(() => {
    void refreshRequirements();
    void refreshBudgetPolicy();
  }, [apiBaseUrl, organizationId, projectId]);

  function toggleScope(scope: BudgetScope) {
    setScopes((previous) => {
      const next = new Set(previous);
      if (next.has(scope)) {
        next.delete(scope);
      } else {
        next.add(scope);
      }
      return next;
    });
  }

  async function handleBudgetSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setBudgetError(null);
    try {
      const scopesList = Array.from(scopes);
      if (budgetPolicyMissing) {
        const response = await fetch(budgetPolicyUrl(apiBaseUrl, organizationId, projectId), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ total_amount: totalAmount, currency, scopes: scopesList }),
        });
        if (!response.ok) {
          // Preserve the draft on a real failure -- never revert the
          // user's own in-progress entry back to empty fields.
          setBudgetError(await readErrorDetail(response));
          return;
        }
        const created: BudgetPolicyRecord = await response.json();
        setBudgetPolicy(created);
        setBudgetPolicyMissing(false);
      } else {
        const response = await fetch(budgetPolicyUrl(apiBaseUrl, organizationId, projectId), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            total_amount: totalAmount,
            scopes: scopesList,
            expected_row_version: budgetPolicy!.row_version,
          }),
        });
        if (!response.ok) {
          setBudgetError(await readErrorDetail(response));
          return;
        }
        const revised: BudgetPolicyRecord = await response.json();
        setBudgetPolicy(revised);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div data-testid="requirements-panel">
      <section data-testid="security-profile-summary">
        <h2>Security profile</h2>
        <p data-testid="security-profile-value">{securityProfile}</p>
        <p data-testid="security-profile-edit-notice">
          Edited from the project setup form, not here.
        </p>
      </section>

      <section data-testid="critical-behavior-requirements">
        <h2>Critical behavior requirements</h2>
        {requirementsError && <p data-testid="requirements-error">{requirementsError}</p>}
        <ul data-testid="requirement-list">
          {(requirements ?? []).map((requirement) => {
            const desiredCritical = requirement.is_critical || requirement.requires_confirmation;
            const conflict = desiredCritical && !requirement.runtime_enforced;
            return (
              <li
                key={requirement.tool_definition_id}
                data-testid={`requirement-row-${requirement.tool_definition_id}`}
              >
                <span>
                  {requirement.name} ({requirement.side_effect_class})
                </span>
                <span data-testid={`requirement-desired-${requirement.tool_definition_id}`}>
                  {desiredCritical ? "critical" : "non-critical"}
                </span>
                <span data-testid={`requirement-enforced-${requirement.tool_definition_id}`}>
                  {requirement.runtime_enforced ? "enforced" : "not enforced"}
                </span>
                {conflict && (
                  <span data-testid={`requirement-conflict-${requirement.tool_definition_id}`}>
                    Conflict: declared critical/requires-confirmation but not runtime-enforced.
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section data-testid="budget-policy-section">
        <h2>Budget policy</h2>
        {budgetError && <p data-testid="budget-policy-error">{budgetError}</p>}

        {budgetPolicyMissing ? (
          <p data-testid="budget-policy-missing-notice">No budget policy configured yet.</p>
        ) : (
          budgetPolicy && (
            <dl data-testid="budget-policy-summary">
              <dt>Row version</dt>
              <dd data-testid="budget-row-version">{budgetPolicy.row_version}</dd>
              <dt>Reserved</dt>
              <dd data-testid="budget-reserved">{budgetPolicy.reserved_amount}</dd>
              <dt>Available</dt>
              <dd data-testid="budget-available">{budgetPolicy.available_amount}</dd>
            </dl>
          )
        )}

        <form onSubmit={handleBudgetSubmit} data-testid="budget-policy-form">
          <label htmlFor="budget-total-amount">Total amount</label>
          <input
            id="budget-total-amount"
            data-testid="budget-total-amount-input"
            value={totalAmount}
            onChange={(event) => setTotalAmount(event.target.value)}
            required
          />

          <label htmlFor="budget-currency">Currency</label>
          <input
            id="budget-currency"
            data-testid="budget-currency-input"
            value={currency}
            disabled={!budgetPolicyMissing}
            onChange={(event) => setCurrency(event.target.value.toUpperCase())}
            maxLength={3}
            required
          />

          <fieldset data-testid="budget-scopes-fieldset">
            <legend>Scopes</legend>
            {BUDGET_SCOPES.map((scope) => (
              <label key={scope}>
                <input
                  type="checkbox"
                  data-testid={`budget-scope-${scope}`}
                  checked={scopes.has(scope)}
                  onChange={() => toggleScope(scope)}
                />
                {scope}
              </label>
            ))}
          </fieldset>

          <button type="submit" data-testid="budget-policy-submit-button" disabled={saving}>
            {budgetPolicyMissing ? "Create budget policy" : "Save changes"}
          </button>
        </form>
      </section>
    </div>
  );
}
