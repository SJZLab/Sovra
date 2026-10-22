/**
 * Requirements review panel (FTF-0408), rebuilt for FTF-UX04 on the UX01 design system.
 *
 * Assembles three already-live, already-CERTIFIED surfaces for one project into a single review
 * screen, exactly as FTF-0407's own module docstring named this task:
 *
 *  - FTF-0407's real, read-only `GET .../requirements` critical-behavior view -- every tool's
 *    declared (`is_critical`/`requires_confirmation`) state next to its own `runtime_enforced` state.
 *    The view is derived, never re-decided here: a tool declared critical/requires-confirmation but
 *    not runtime-enforced is a real, surfaced conflict (this task's negative_acceptance: "show
 *    observed/desired/enforced conflicts"), computed fresh on every render, never a stored flag.
 *  - FTF-0403's real budget-policy `POST`/`GET`/`PATCH` surface -- `total_amount`/`scopes` are
 *    editable (acceptance: "User edits ... scope ... with validation, version preview"); `currency` is
 *    only settable at creation. The "version preview" is the real `expected_row_version` sent with
 *    every revision, shown before the user submits, so a stale concurrent edit is refused (409).
 *  - The project's own `security_profile`, read-only with a pointer to where it is edited.
 *
 * What UX04 adds (docs/design/audit-2026-10.md section 11; docs/design/UX_STANDARD.md):
 *  - audit H5 (sev 2): the total is checked against the already-reserved amount BEFORE submit, in
 *    exact decimal arithmetic (never floats), with the minimum named in the message;
 *  - the requirement list is a real table (header per column, state as words + icon, never colour),
 *    with a loading state, an empty state that says what to do, and a load error with a retry;
 *  - the budget form: labels above fields, required/optional marked, currency a closed list (the
 *    backend's recognized codes), scopes validated (at least one), inline errors after blur and a
 *    focusable summary after a failed submit;
 *  - a reply is a success only for a 2xx whose body is a well-formed record that MATCHES what was
 *    sent (UI CHECKLIST a/b/d); a network failure, 5xx, non-JSON or malformed body is a typed,
 *    retryable failure that keeps every entry and the last good data (Law 17), shown as a localized
 *    sentence, never the backend's own `detail`;
 *  - everything is keyed by organization and project: a different one is a different panel
 *    (UI CHECKLIST c); a stale response for the previous project is dropped;
 *  - English and Arabic (RTL) through `locale`, `lang`/`dir` on the root, user data in `<bdi>`.
 *
 * Scope disclosed, not hidden (see docs/certifications/queue.md): the packet also names
 * "mandate/languages/outputs/SLO" editing and "explicit lock after model/runtime resolution". Section
 * 6.8D's `AgentManifest` is not real storage for those here; a control that appeared to save them
 * with no backing table would be the "production placeholder path" Law 11 forbids. They stay out of
 * scope, for the AgentManifest-authoring task (the 04A21/04A22 components own that surface).
 */

import { useEffect, useRef, useState } from "react";

import { isRecord, type Failure } from "../target-application/common";
import {
  Banner,
  Button,
  DataTable,
  dirFor,
  EmptyState,
  ErrorSummary,
  Field,
  Pending,
  phrase,
  resolveLocale,
  type ErrorSummaryItem,
  type Locale,
} from "../ui";
import { SelectField, StatusLine } from "./form_controls";
import { isOneOf, isString } from "./guards";
import { requestJson } from "./request";
import { mandateFailureMessage, tm, type MandateWord } from "./strings_project_mandate";

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

// Section 41's own named Temporal workflow list -- the same closed vocabulary
// `budget_policy_schema.py::BUDGET_SCOPES` defines on the backend. Redeclared here rather than
// imported: this repository has no shared TS/Python contract package for domain enums yet.
export const BUDGET_SCOPES = ["training", "evaluation", "serving"] as const;
export type BudgetScope = (typeof BUDGET_SCOPES)[number];

/** `budget_policy_schema.py::ALLOWED_CURRENCIES` (the recognized ISO-4217 codes), redeclared. */
export const BUDGET_CURRENCIES = [
  "USD",
  "EUR",
  "GBP",
  "SAR",
  "AED",
  "JPY",
  "CHF",
  "CAD",
  "AUD",
  "SGD",
] as const;

/** NUMERIC(18,4): at most 4 decimal places and 14 integer digits. */
export const AMOUNT_DECIMALS = 4;
export const AMOUNT_INTEGER_DIGITS = 14;

export interface RequirementsPanelProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  securityProfile: string;
  locale?: Locale;
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

// ---------------------------------------------------------------------------------------------
// Exact decimal handling (money is never a float)
// ---------------------------------------------------------------------------------------------

const DECIMAL = /^(\d+)(?:\.(\d+))?$/;

/** An amount as an integer count of 1/10^4 units, or null when it is not a plain non-negative
 * decimal with at most 4 places and 14 integer digits. No sign, exponent, separator or whitespace. */
export function toUnits(text: string): bigint | null {
  const match = DECIMAL.exec(text);
  if (!match) return null;
  const whole = match[1]!;
  const fraction = match[2] ?? "";
  if (fraction.length > AMOUNT_DECIMALS) return null;
  if (whole.replace(/^0+(?=\d)/, "").length > AMOUNT_INTEGER_DIGITS) return null;
  return BigInt(whole + fraction.padEnd(AMOUNT_DECIMALS, "0"));
}

export type TotalProblem = "required" | "invalid" | "belowReserved" | null;

/**
 * Pre-submit check of the total (NN/g H5, audit finding H5 sev 2): required, a positive decimal that
 * fits the column, and not below the amount already reserved. `reserved` is the policy's own
 * server-formatted string (absent on create).
 */
export function totalProblem(total: string, reserved: string | null): TotalProblem {
  const typed = total.trim();
  if (typed === "") return "required";
  const units = toUnits(typed);
  if (units === null || units <= 0n) return "invalid";
  if (reserved !== null) {
    const held = toUnits(reserved);
    if (held !== null && units < held) return "belowReserved";
  }
  return null;
}

// ---------------------------------------------------------------------------------------------
// Strict parsing and binding (UI CHECKLIST a, b)
// ---------------------------------------------------------------------------------------------

/** Every field of the FTF-0407 view, type-checked; null when the body is not exactly a list of them. */
export function parseRequirements(body: unknown): CriticalBehaviorRequirementView[] | null {
  if (!Array.isArray(body)) return null;
  const rows: CriticalBehaviorRequirementView[] = [];
  const seen = new Set<string>();
  for (const item of body as unknown[]) {
    if (
      !isRecord(item) ||
      !isString(item.tool_definition_id) ||
      item.tool_definition_id === "" ||
      !isString(item.name) ||
      !isString(item.side_effect_class) ||
      typeof item.is_critical !== "boolean" ||
      typeof item.requires_confirmation !== "boolean" ||
      typeof item.measurable !== "boolean" ||
      typeof item.runtime_enforced !== "boolean" ||
      seen.has(item.tool_definition_id)
    ) {
      return null;
    }
    seen.add(item.tool_definition_id);
    rows.push({
      tool_definition_id: item.tool_definition_id,
      name: item.name,
      side_effect_class: item.side_effect_class,
      is_critical: item.is_critical,
      requires_confirmation: item.requires_confirmation,
      measurable: item.measurable,
      runtime_enforced: item.runtime_enforced,
    });
  }
  return rows;
}

/** Every field of the FTF-0403 `_serialize` shape, type- and enum-checked; null when anything is off. */
export function parseBudgetPolicy(body: unknown): BudgetPolicyRecord | null {
  if (!isRecord(body)) return null;
  const b = body;
  if (
    !isString(b.id) ||
    b.id === "" ||
    !isString(b.organization_id) ||
    b.organization_id === "" ||
    !isString(b.project_id) ||
    b.project_id === "" ||
    !isString(b.total_amount) ||
    toUnits(b.total_amount) === null ||
    !isOneOf(BUDGET_CURRENCIES, b.currency) ||
    !Array.isArray(b.scopes) ||
    b.scopes.length === 0 ||
    !b.scopes.every((s: unknown) => isOneOf(BUDGET_SCOPES, s)) ||
    new Set(b.scopes as string[]).size !== b.scopes.length ||
    !isString(b.reserved_amount) ||
    toUnits(b.reserved_amount) === null ||
    !isString(b.available_amount) ||
    toUnits(b.available_amount) === null ||
    typeof b.row_version !== "number" ||
    !Number.isSafeInteger(b.row_version) ||
    b.row_version < 1 ||
    !isString(b.created_by) ||
    !isString(b.created_at) ||
    !isString(b.updated_at)
  ) {
    return null;
  }
  return {
    id: b.id,
    organization_id: b.organization_id,
    project_id: b.project_id,
    total_amount: b.total_amount,
    currency: b.currency,
    scopes: [...(b.scopes as string[])],
    reserved_amount: b.reserved_amount,
    available_amount: b.available_amount,
    row_version: b.row_version,
    created_by: b.created_by,
    created_at: b.created_at,
    updated_at: b.updated_at,
  };
}

export interface BudgetSent {
  total_amount: string;
  /** Present on create only; a revision never sends a currency. */
  currency: string | null;
  scopes: BudgetScope[];
}

/**
 * The ONE place a budget reply is compared with what was sent (UI CHECKLIST b). The server returns
 * the amount re-formatted to four places and the scopes sorted, so amounts compare as exact units
 * and scopes as sets. `current` is the policy being revised (null on create). Available must equal
 * total minus reserved, so a reply that does not add up is not accepted.
 */
export function bindBudgetSave(
  sent: BudgetSent,
  reply: BudgetPolicyRecord,
  scope: { organizationId: string; projectId: string },
  current: BudgetPolicyRecord | null,
): boolean {
  if (reply.organization_id !== scope.organizationId || reply.project_id !== scope.projectId) {
    return false;
  }
  const total = toUnits(reply.total_amount);
  const reserved = toUnits(reply.reserved_amount);
  const available = toUnits(reply.available_amount);
  const sentUnits = toUnits(sent.total_amount.trim());
  if (total === null || reserved === null || available === null || sentUnits === null) return false;
  if (total !== sentUnits || available !== total - reserved) return false;
  const sentScopes = [...sent.scopes].sort();
  const replyScopes = [...reply.scopes].sort();
  if (sentScopes.length !== replyScopes.length) return false;
  if (!sentScopes.every((s, i) => s === replyScopes[i])) return false;
  if (current === null) {
    return reply.currency === sent.currency && reply.row_version === 1;
  }
  // A revision never sends a currency (it is fixed at creation), and never changes it.
  return (
    sent.currency === null &&
    reply.id === current.id &&
    reply.currency === current.currency &&
    reply.row_version === current.row_version + 1
  );
}

// ---------------------------------------------------------------------------------------------

type Load<T> =
  { state: "loading" } | { state: "ready"; data: T } | { state: "failed"; failure: Failure };

type BudgetLoad =
  | { state: "loading" }
  | { state: "missing" }
  | { state: "ready"; policy: BudgetPolicyRecord }
  | { state: "failed"; failure: Failure };

const SCOPE_WORD: Record<BudgetScope, MandateWord> = {
  training: "scopeTraining",
  evaluation: "scopeEvaluation",
  serving: "scopeServing",
};

export function RequirementsPanel(props: RequirementsPanelProps) {
  // A different organization or project is a different panel (UI CHECKLIST c): nothing typed or
  // loaded for one is ever shown or sent for another.
  return <RequirementsPanelInner key={`${props.organizationId}|${props.projectId}`} {...props} />;
}

function RequirementsPanelInner({
  apiBaseUrl,
  organizationId,
  projectId,
  securityProfile,
  locale = "en",
}: RequirementsPanelProps) {
  const lang = resolveLocale(locale);
  const [requirements, setRequirements] = useState<Load<CriticalBehaviorRequirementView[]>>({
    state: "loading",
  });
  const [budget, setBudget] = useState<BudgetLoad>({ state: "loading" });

  const [totalAmount, setTotalAmount] = useState("");
  const [currency, setCurrency] = useState<string>("USD");
  const [scopes, setScopes] = useState<Set<BudgetScope>>(new Set());
  const [touched, setTouched] = useState({ total: false });
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [submitCount, setSubmitCount] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveFailure, setSaveFailure] = useState<Failure | null>(null);
  const [saveNotice, setSaveNotice] = useState<"created" | "saved" | null>(null);

  // The submit button disables itself while its request is in flight, which drops keyboard focus;
  // hand it back once the request has settled.
  const refocusSubmit = useRef(false);
  useEffect(() => {
    if (!saving && refocusSubmit.current) {
      refocusSubmit.current = false;
      document.getElementById("budget-policy-submit")?.focus();
    }
  }, [saving]);

  async function refreshRequirements(): Promise<void> {
    setRequirements({ state: "loading" });
    const outcome = await requestJson(requirementsUrl(apiBaseUrl, organizationId, projectId), {
      credentials: "include",
    });
    if (!outcome.ok) {
      setRequirements({ state: "failed", failure: outcome.failure });
      return;
    }
    const rows = parseRequirements(outcome.body);
    if (rows === null) {
      setRequirements({ state: "failed", failure: { kind: "malformed" } });
      return;
    }
    setRequirements({ state: "ready", data: rows });
  }

  function seedForm(policy: BudgetPolicyRecord): void {
    setTotalAmount(policy.total_amount);
    setCurrency(policy.currency);
    setScopes(new Set(policy.scopes as BudgetScope[]));
  }

  async function refreshBudgetPolicy(): Promise<void> {
    setBudget({ state: "loading" });
    const outcome = await requestJson(budgetPolicyUrl(apiBaseUrl, organizationId, projectId), {
      credentials: "include",
    });
    if (!outcome.ok) {
      // A 404 is "no policy yet" (the route answers `not_found` for a missing policy and for a
      // project it cannot show; creating one then answers 404 for the latter, which is shown).
      if (outcome.failure.kind === "http" && outcome.failure.status === 404) {
        setBudget({ state: "missing" });
        return;
      }
      setBudget({ state: "failed", failure: outcome.failure });
      return;
    }
    const policy = parseBudgetPolicy(outcome.body);
    if (
      policy === null ||
      policy.organization_id !== organizationId ||
      policy.project_id !== projectId
    ) {
      setBudget({ state: "failed", failure: { kind: "malformed" } });
      return;
    }
    setBudget({ state: "ready", policy });
    seedForm(policy);
  }

  // Runs once per identity (the component is re-keyed on a prop change), not on every render:
  // re-fetching on every keystroke would clobber the user's in-progress budget edit.
  useEffect(() => {
    void refreshRequirements();
    void refreshBudgetPolicy();
  }, []);

  const policy = budget.state === "ready" ? budget.policy : null;
  const creating = budget.state === "missing";
  const formAvailable = creating || policy !== null;
  const problem = totalProblem(totalAmount, creating ? null : (policy?.reserved_amount ?? null));
  const scopesProblem = scopes.size === 0;

  const totalError =
    (touched.total || submitAttempted) && problem !== null
      ? tm(
          problem === "required"
            ? "totalRequired"
            : problem === "invalid"
              ? "totalInvalid"
              : "totalBelowReserved",
          locale,
          { reserved: policy?.reserved_amount ?? "", currency: policy?.currency ?? currency },
        )
      : undefined;
  const scopesError = submitAttempted && scopesProblem ? tm("scopesRequired", locale) : undefined;
  const summary: ErrorSummaryItem[] = [];
  if (submitAttempted && totalError) {
    summary.push({ fieldId: "budget-total-amount", message: totalError });
  }
  if (submitAttempted && scopesProblem) {
    summary.push({
      fieldId: `budget-scope-${BUDGET_SCOPES[0]}`,
      message: tm("scopesRequired", locale),
    });
  }

  function toggleScope(scope: BudgetScope): void {
    setSaveNotice(null);
    setScopes((previous) => {
      const next = new Set(previous);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  }

  async function handleBudgetSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (saving || !formAvailable) return;
    if (problem !== null || scopesProblem) {
      setSubmitAttempted(true);
      setSubmitCount((count) => count + 1);
      return;
    }
    setSaving(true);
    refocusSubmit.current = true;
    setSaveFailure(null);
    setSaveNotice(null);
    const sent: BudgetSent = {
      total_amount: totalAmount.trim(),
      currency: creating ? currency : null,
      // Same order the form lists them in, so the request is stable.
      scopes: BUDGET_SCOPES.filter((s) => scopes.has(s)),
    };
    const url = budgetPolicyUrl(apiBaseUrl, organizationId, projectId);
    const init: RequestInit = creating
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            total_amount: sent.total_amount,
            currency: sent.currency,
            scopes: sent.scopes,
          }),
        }
      : {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            total_amount: sent.total_amount,
            scopes: sent.scopes,
            expected_row_version: policy!.row_version,
          }),
        };
    const outcome = await requestJson(url, init);
    if (!outcome.ok) {
      // The draft stays exactly as typed (this task's negative_acceptance).
      setSaveFailure(outcome.failure);
      setSaving(false);
      return;
    }
    const reply = parseBudgetPolicy(outcome.body);
    if (
      reply === null ||
      !bindBudgetSave(sent, reply, { organizationId, projectId }, creating ? null : policy)
    ) {
      setSaveFailure({ kind: "malformed" });
      setSaving(false);
      return;
    }
    setBudget({ state: "ready", policy: reply });
    seedForm(reply);
    setSubmitAttempted(false);
    setTouched({ total: false });
    setSaveNotice(creating ? "created" : "saved");
    setSaving(false);
  }

  const desiredOf = (r: CriticalBehaviorRequirementView) =>
    r.is_critical || r.requires_confirmation;
  /** Computed fresh on every render from the live view; never a stored or overridable flag. */
  const isConflict = (r: CriticalBehaviorRequirementView) => desiredOf(r) && !r.runtime_enforced;
  const reqRows = requirements.state === "ready" ? requirements.data : [];

  return (
    <div
      data-testid="requirements-panel"
      lang={lang}
      dir={dirFor(locale)}
      style={{
        display: "grid",
        gap: "var(--space-5)",
        maxInlineSize: "100%",
      }}
    >
      <h2 style={{ fontSize: "var(--font-title)" }}>{tm("panelTitle", locale)}</h2>

      <section data-testid="security-profile-summary" aria-labelledby="req-security-heading">
        <h3 id="req-security-heading">{tm("securityHeading", locale)}</h3>
        <p data-testid="security-profile-value" style={{ overflowWrap: "anywhere" }}>
          <bdi>{securityProfile}</bdi>
        </p>
        <p data-testid="security-profile-edit-notice">{tm("securityEditNotice", locale)}</p>
      </section>

      <section data-testid="critical-behavior-requirements" aria-labelledby="req-critical-heading">
        <h3 id="req-critical-heading">{tm("criticalHeading", locale)}</h3>
        {requirements.state === "loading" && (
          <div data-testid="requirements-loading">
            <Pending label={tm("requirementsLoading", locale)} locale={locale} />
          </div>
        )}
        {requirements.state === "failed" && (
          <div data-testid="requirements-error">
            <Banner kind="error" locale={locale}>
              {`${tm("requirementsLoadFailed", locale)} ${mandateFailureMessage(requirements.failure, "project", locale)}`}
            </Banner>
            <Button
              variant="secondary"
              data-testid="requirements-retry-button"
              onClick={() => void refreshRequirements()}
            >
              {tm("retry", locale)}
            </Button>
          </div>
        )}
        {requirements.state === "ready" && reqRows.length === 0 && (
          <div data-testid="requirements-empty">
            <EmptyState
              title={tm("requirementsEmptyTitle", locale)}
              why={tm("requirementsEmptyWhy", locale)}
              action={
                <Button
                  variant="secondary"
                  data-testid="requirements-reload-button"
                  onClick={() => void refreshRequirements()}
                >
                  {tm("requirementsEmptyAction", locale)}
                </Button>
              }
            />
          </div>
        )}
        {reqRows.length > 0 && (
          <div data-testid="requirement-list">
            <DataTable<CriticalBehaviorRequirementView>
              caption={tm("criticalCaption", locale)}
              rows={reqRows}
              rowKey={(requirement) => requirement.tool_definition_id}
              empty={null}
              columns={[
                {
                  key: "tool",
                  header: tm("colTool", locale),
                  sortValue: (requirement) => requirement.name,
                  render: (requirement) => (
                    <span
                      data-testid={`requirement-row-${requirement.tool_definition_id}`}
                      style={{ overflowWrap: "anywhere" }}
                    >
                      <bdi>{requirement.name}</bdi> (<bdi>{requirement.side_effect_class}</bdi>)
                    </span>
                  ),
                },
                {
                  key: "desired",
                  header: tm("colDesired", locale),
                  sortValue: (requirement) => (desiredOf(requirement) ? 1 : 0),
                  render: (requirement) => (
                    <span data-testid={`requirement-desired-${requirement.tool_definition_id}`}>
                      {desiredOf(requirement)
                        ? tm("desiredCritical", locale)
                        : tm("desiredNonCritical", locale)}
                    </span>
                  ),
                },
                {
                  key: "enforced",
                  header: tm("colEnforced", locale),
                  sortValue: (requirement) => (requirement.runtime_enforced ? 1 : 0),
                  render: (requirement) => (
                    <span data-testid={`requirement-enforced-${requirement.tool_definition_id}`}>
                      {requirement.runtime_enforced
                        ? tm("enforced", locale)
                        : tm("notEnforced", locale)}
                    </span>
                  ),
                },
                {
                  key: "check",
                  header: tm("colConflict", locale),
                  // Conflicts first when ascending: the rows that need attention come to the top.
                  sortValue: (requirement) => (isConflict(requirement) ? 0 : 1),
                  render: (requirement) =>
                    isConflict(requirement) ? (
                      <span
                        data-testid={`requirement-conflict-${requirement.tool_definition_id}`}
                        style={{ color: "var(--color-danger)", overflowWrap: "anywhere" }}
                      >
                        <span aria-hidden="true">✕</span> {tm("conflict", locale)}
                      </span>
                    ) : (
                      <span style={{ color: "var(--color-success)" }}>
                        <span aria-hidden="true">✓</span> {tm("consistent", locale)}
                      </span>
                    ),
                },
              ]}
            />
          </div>
        )}
      </section>

      <section data-testid="budget-policy-section" aria-labelledby="req-budget-heading">
        <h3 id="req-budget-heading">{tm("budgetHeading", locale)}</h3>
        {budget.state === "loading" && (
          <div data-testid="budget-policy-loading">
            <Pending label={tm("budgetLoading", locale)} locale={locale} />
          </div>
        )}
        {budget.state === "failed" && (
          <div data-testid="budget-policy-load-error">
            <Banner kind="error" locale={locale}>
              {`${tm("budgetLoadFailed", locale)} ${mandateFailureMessage(budget.failure, "budget", locale)}`}
            </Banner>
            <Button
              variant="secondary"
              data-testid="budget-policy-retry-button"
              onClick={() => void refreshBudgetPolicy()}
            >
              {tm("retry", locale)}
            </Button>
          </div>
        )}
        {creating && (
          <div data-testid="budget-policy-missing-notice">
            <Banner kind="info" locale={locale}>
              {tm("budgetMissing", locale)}
            </Banner>
          </div>
        )}
        {policy && (
          <dl data-testid="budget-policy-summary">
            <dt>{tm("rowVersion", locale)}</dt>
            <dd data-testid="budget-row-version">{policy.row_version}</dd>
            <dt>{tm("reserved", locale)}</dt>
            <dd>
              <span data-testid="budget-reserved">{policy.reserved_amount}</span>{" "}
              <bdi>{policy.currency}</bdi>
            </dd>
            <dt>{tm("available", locale)}</dt>
            <dd>
              <span data-testid="budget-available">{policy.available_amount}</span>{" "}
              <bdi>{policy.currency}</bdi>
            </dd>
          </dl>
        )}

        {formAvailable && (
          <form
            onSubmit={(event) => void handleBudgetSubmit(event)}
            noValidate
            data-testid="budget-policy-form"
            aria-label={tm("budgetHeading", locale)}
            aria-busy={saving}
            style={{
              display: "grid",
              gap: "var(--space-4)",
              maxInlineSize: "100%",
            }}
          >
            <ErrorSummary errors={summary} locale={locale} submitCount={submitCount} />

            <Field
              id="budget-total-amount"
              data-testid="budget-total-amount-input"
              label={tm("totalLabel", locale)}
              helper={tm("totalHelper", locale)}
              error={totalError}
              requirement="required"
              locale={locale}
              inputMode="decimal"
              value={totalAmount}
              readOnly={saving}
              onChange={(event) => {
                setTotalAmount(event.target.value);
                setSaveNotice(null);
              }}
              onBlur={() => setTouched({ total: true })}
            />

            <SelectField
              id="budget-currency"
              data-testid="budget-currency-input"
              label={tm("currencyLabel", locale)}
              lockedReason={creating ? undefined : tm("currencyLocked", locale)}
              requirement="required"
              locale={locale}
              value={currency}
              options={BUDGET_CURRENCIES.map((code) => ({ value: code, label: code }))}
              onChange={(event) => {
                if (saving) return;
                setCurrency(event.target.value);
                setSaveNotice(null);
              }}
            />

            <fieldset
              data-testid="budget-scopes-fieldset"
              aria-describedby={
                scopesError ? "budget-scopes-helper budget-scopes-error" : "budget-scopes-helper"
              }
            >
              <legend>
                {tm("scopesLegend", locale)} <span>{phrase("fieldRequired", locale)}</span>
              </legend>
              <div id="budget-scopes-helper">{tm("scopesHelper", locale)}</div>
              {BUDGET_SCOPES.map((scope) => (
                <label key={scope} style={{ display: "block", minBlockSize: "var(--target-min)" }}>
                  <input
                    id={`budget-scope-${scope}`}
                    type="checkbox"
                    data-testid={`budget-scope-${scope}`}
                    checked={scopes.has(scope)}
                    disabled={saving}
                    onChange={() => toggleScope(scope)}
                  />{" "}
                  {tm(SCOPE_WORD[scope], locale)}
                </label>
              ))}
              {scopesError ? (
                <div id="budget-scopes-error" role="alert" data-testid="budget-scopes-error">
                  {scopesError}
                </div>
              ) : null}
            </fieldset>

            {saveFailure && (
              <div data-testid="budget-policy-error">
                <Banner kind="error" locale={locale}>
                  {`${mandateFailureMessage(saveFailure, "budget", locale)} ${tm("inputKept", locale)}`}
                </Banner>
              </div>
            )}
            {saving && <Pending label={tm("budgetSaving", locale)} locale={locale} />}
            {saveNotice && (
              <StatusLine locale={locale} testId="budget-policy-saved-notice">
                {saveNotice === "created" ? tm("budgetCreated", locale) : tm("budgetSaved", locale)}
              </StatusLine>
            )}

            <div>
              <Button
                id="budget-policy-submit"
                type="submit"
                variant="primary"
                data-testid="budget-policy-submit-button"
                disabled={saving}
              >
                {creating ? tm("budgetCreate", locale) : tm("budgetSave", locale)}
              </Button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
