/**
 * Mandate editor and conflict-resolution UI (FTF-04A22, Section
 * 6.8D/7A.6/7A.7/66.4/72.3/77/81).
 *
 * A dedicated, structured editor for `AgentManifest.mandate_json` and
 * `AgentManifest.forbidden_actions_json` (Section 7A.6's own named
 * fields: business purpose, allowed responsibilities, critical
 * behaviors, required tools, tool side-effect/confirmation policy,
 * required context/data classes, supported languages, expected
 * outputs/schemas, escalation/abstention behavior, privacy/security
 * profile, latency SLO, throughput/concurrency target, cost-per-success
 * ceiling, critical safety/reliability gates) -- not a second raw-JSON
 * editor for those two fields (FTF-04A21's own generic per-field editor
 * already covers every `AgentManifest` content field, mandate fields
 * included, as opaque JSON; this component instead gives them real
 * structure and, per this task's own distinct acceptance clause, the
 * desired/observed/enforced conflict view FTF-04A21 does not build).
 *
 * Desired/observed/enforced (Section 7A.7/66.4): "Mandate fields split
 * into desired/observed/enforced values. User saying 'refunds forbidden'
 * does not prove runtime denial." This component reads real data for
 * every one of the three, never invents one:
 * - desired: `forbidden_actions_json.actions[].name` -- the user's own
 *   declared forbidden-action list, edited here.
 * - observed (repository contract): the already-CERTIFIED,
 *   project-scoped `/requirements` view (FTF-0407,
 *   `derive_critical_behavior_requirement_view`) -- a tool's own
 *   declared `side_effect_class`/`is_critical`/`requires_confirmation`,
 *   as the repository's tool schema actually states it, matched to a
 *   forbidden-action name by `ToolDefinition.name`.
 * - enforced (observed runtime contract): the same view's own
 *   `runtime_enforced` field -- whether the real authorization backend
 *   has actually been observed to reject the attempt, not merely
 *   whether the mandate says so.
 *
 * `evaluateForbiddenAction` is a pure function of those two already-live
 * data sources. `mandateLockBlockers` reports exactly the forbidden
 * actions that are not genuinely enforced yet. Both are exported so a
 * later manifest-wide lock gate (FTF-04A23's own "Manifest validation
 * engine", which lists this task as its own dependency) can consume the
 * same real computation rather than re-deriving it.
 *
 * Negative acceptance ("User override cannot declare a dangerous runtime
 * safe; unresolved policy conflicts block lock"): there is no control
 * anywhere in this component that writes a "resolved"/"acknowledged"/
 * "safe" flag for a forbidden action. The conflict banner is recomputed
 * from `requirements` on every render; the only way it changes is a
 * fresh `/requirements` fetch (via "Refresh enforcement status", a
 * read-only GET) returning different real data, or the action being
 * removed from the mandate's own forbidden list (a real edit to what is
 * forbidden, not a claim that an unchanged forbidden action is safe).
 * Saving a mandate edit never re-derives or clears a conflict by itself
 * -- `handleSave`'s own PATCH response is applied only to `manifest`,
 * never to `requirements`.
 *
 * Scope disclosed, not hidden (same precedent as every sibling
 * target-application component): no Next.js `app/` page tree exists yet
 * (FTF-H029), so this is a DOM-level component test (Vitest + React
 * Testing Library + jsdom with a mocked `fetch`), not a full browser
 * E2E. `database_changes: No independent schema change` -- this reuses
 * FTF-04A19's own `mandate_json`/`forbidden_actions_json` columns and
 * FTF-04A19/FTF-0407's own live routes; nothing new is added to either.
 */

import { useEffect, useMemo, useState } from "react";

import {
  manifestUrl,
  type ManifestRecord,
} from "./manifest_review_edit_ui_with_per_field_evidence";
import {
  requirementsUrl,
  type CriticalBehaviorRequirementView,
} from "../project-mandate/requirements_ui";

export const MANDATE_TEXT_FIELDS = [
  "business_purpose",
  "tool_side_effect_confirmation_policy",
  "expected_outputs_and_schemas",
  "escalation_abstention_behavior",
  "privacy_security_profile",
] as const;
export type MandateTextField = (typeof MANDATE_TEXT_FIELDS)[number];

export const MANDATE_LIST_FIELDS = [
  "allowed_responsibilities",
  "critical_behaviors",
  "required_tools",
  "required_context_data_classes",
  "supported_languages",
  "critical_safety_reliability_gates",
] as const;
export type MandateListField = (typeof MANDATE_LIST_FIELDS)[number];

export const MANDATE_NUMBER_FIELDS = [
  "latency_slo_ms",
  "throughput_concurrency_target",
  "cost_per_success_ceiling",
] as const;
export type MandateNumberField = (typeof MANDATE_NUMBER_FIELDS)[number];

export type MandateContent = {
  [K in MandateTextField]: string | null;
} & {
  [K in MandateListField]: string[];
} & {
  [K in MandateNumberField]: number | null;
};

export function emptyMandateContent(): MandateContent {
  const content = {} as MandateContent;
  for (const field of MANDATE_TEXT_FIELDS) content[field] = null;
  for (const field of MANDATE_LIST_FIELDS) content[field] = [];
  for (const field of MANDATE_NUMBER_FIELDS) content[field] = null;
  return content;
}

/** Coerces an unknown `mandate_json` value into a well-typed, defaulted
 * `MandateContent` -- a malformed/missing field is never fatal, it is
 * dropped back to its own empty default (never silently fabricated
 * content). */
export function parseMandateContent(raw: unknown): MandateContent {
  const content = emptyMandateContent();
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return content;
  const source = raw as Record<string, unknown>;
  for (const field of MANDATE_TEXT_FIELDS) {
    const value = source[field];
    content[field] = typeof value === "string" ? value : null;
  }
  for (const field of MANDATE_LIST_FIELDS) {
    const value = source[field];
    content[field] = Array.isArray(value)
      ? value.filter((v): v is string => typeof v === "string")
      : [];
  }
  for (const field of MANDATE_NUMBER_FIELDS) {
    const value = source[field];
    content[field] = typeof value === "number" && Number.isFinite(value) ? value : null;
  }
  return content;
}

/** The raw stored `mandate_json` object (or `{}`), kept so a save never drops a key this
 * editor does not model. */
export function mandateBaseOf(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return { ...(raw as Record<string, unknown>) };
}

/** Known fields come from the editor; every other stored key survives unchanged. */
export function serializeMandateContent(
  content: MandateContent,
  base: Record<string, unknown> = {},
): Record<string, unknown> {
  return { ...base, ...content };
}

export interface ForbiddenActionEntry {
  name: string;
  reason: string;
  /** Any other keys the stored entry carried; written back unchanged on save. */
  extra?: Record<string, unknown>;
}

/** Coerces an unknown `forbidden_actions_json` value (a JSON object, per
 * `AgentManifest`'s own `_validate_json_field_or_none`, never a bare
 * array) into a well-typed list under its own `actions` key. */
export function parseForbiddenActions(raw: unknown): ForbiddenActionEntry[] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  const actions = (raw as Record<string, unknown>).actions;
  if (!Array.isArray(actions)) return [];
  const out: ForbiddenActionEntry[] = [];
  for (const entry of actions) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
    const name = (entry as Record<string, unknown>).name;
    const reason = (entry as Record<string, unknown>).reason;
    if (typeof name === "string" && name.length > 0) {
      const extra: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(entry as Record<string, unknown>)) {
        if (key !== "name" && key !== "reason") extra[key] = value;
      }
      out.push({
        name,
        reason: typeof reason === "string" ? reason : "",
        ...(Object.keys(extra).length > 0 ? { extra } : {}),
      });
    }
  }
  return out;
}

/** The raw stored `forbidden_actions_json` object minus `actions` (or `{}`), kept so a save
 * never drops a top-level key this editor does not model. */
export function forbiddenBaseOf(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const { actions: _actions, ...rest } = raw as Record<string, unknown>;
  void _actions;
  return rest;
}

export function serializeForbiddenActions(
  entries: ForbiddenActionEntry[],
  base: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    ...base,
    actions: entries.map((entry) => ({
      ...(entry.extra ?? {}),
      name: entry.name,
      reason: entry.reason,
    })),
  };
}

export type ForbiddenActionConflictReason =
  "not_found" | "not_declared_critical" | "not_runtime_enforced" | "enforced";

/** Reasons that keep a forbidden action from being genuinely enforced --
 * Section 66.4's own "does not prove runtime denial." `not_found` is
 * deliberately excluded: there is no discovered tool to disprove
 * enforcement against yet, so it is disclosed, not treated as a false
 * claim of safety. */
export const BLOCKING_FORBIDDEN_ACTION_REASONS: readonly ForbiddenActionConflictReason[] = [
  "not_declared_critical",
  "not_runtime_enforced",
];

export interface ForbiddenActionStatus {
  name: string;
  reason: ForbiddenActionConflictReason;
  tool: CriticalBehaviorRequirementView | null;
}

/** Pure desired-vs-observed-vs-enforced evaluation for one forbidden
 * action name -- Section 7A.7's own "repository tool schema permits
 * refund, while user mandate says refunds are forbidden" example, plus
 * Section 66.4's own "does not prove runtime denial" half. Never reads
 * any local/override state -- only the live `requirements` (observed +
 * enforced) list passed in. */
export function evaluateForbiddenAction(
  name: string,
  requirements: CriticalBehaviorRequirementView[],
): ForbiddenActionStatus {
  const tool = requirements.find((row) => row.name === name) ?? null;
  if (!tool) return { name, reason: "not_found", tool: null };
  const declaredCritical = tool.is_critical || tool.requires_confirmation;
  if (!declaredCritical) return { name, reason: "not_declared_critical", tool };
  if (!tool.runtime_enforced) return { name, reason: "not_runtime_enforced", tool };
  return { name, reason: "enforced", tool };
}

export function evaluateForbiddenActions(
  entries: ForbiddenActionEntry[],
  requirements: CriticalBehaviorRequirementView[],
): ForbiddenActionStatus[] {
  return entries.map((entry) => evaluateForbiddenAction(entry.name, requirements));
}

function blockerMessage(status: ForbiddenActionStatus): string {
  if (status.reason === "not_declared_critical") {
    return `${status.name}: repository tool schema does not require confirmation -- a dangerous runtime cannot be declared safe by the mandate alone`;
  }
  return `${status.name}: not observed to be enforced at runtime yet`;
}

/** Every reason "unresolved policy conflicts block lock" (this task's
 * own negative acceptance) refers to -- real, derived, never settable by
 * any control in this component. A later manifest-wide lock gate
 * (FTF-04A23) is expected to call this with the same live data rather
 * than re-deriving it. */
export function mandateLockBlockers(statuses: ForbiddenActionStatus[]): string[] {
  return statuses
    .filter((s) => BLOCKING_FORBIDDEN_ACTION_REASONS.includes(s.reason))
    .map(blockerMessage);
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export interface MandateEditorAndConflictResolutionUIProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  manifestId: string;
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    // fall through to the generic message below
  }
  return `request failed (${response.status})`;
}

function networkErrorMessage(caught: unknown): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `network error: ${reason}`;
}

export function MandateEditorAndConflictResolutionUI({
  apiBaseUrl,
  organizationId,
  projectId,
  manifestId,
}: MandateEditorAndConflictResolutionUIProps) {
  const base = trimBase(apiBaseUrl);

  const [manifest, setManifest] = useState<ManifestRecord | null>(null);
  const [requirements, setRequirements] = useState<CriticalBehaviorRequirementView[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [mandate, setMandate] = useState<MandateContent>(emptyMandateContent());
  const [forbiddenActions, setForbiddenActions] = useState<ForbiddenActionEntry[]>([]);
  const [mandateBase, setMandateBase] = useState<Record<string, unknown>>({});
  const [forbiddenBase, setForbiddenBase] = useState<Record<string, unknown>>({});
  // Raw text of a list input while the user is typing, so "en," is not collapsed to "en".
  const [listDrafts, setListDrafts] = useState<Partial<Record<MandateListField, string>>>({});
  const [dirty, setDirty] = useState(false);

  const [newActionName, setNewActionName] = useState("");
  const [newActionReason, setNewActionReason] = useState("");

  const [saveError, setSaveError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [manifestResponse, requirementsResponse] = await Promise.all([
          fetch(manifestUrl(base, organizationId, projectId, manifestId), {
            method: "GET",
            credentials: "include",
          }),
          fetch(requirementsUrl(base, organizationId, projectId), {
            method: "GET",
            credentials: "include",
          }),
        ]);
        if (!manifestResponse.ok) {
          if (!cancelled) setLoadError(await readErrorDetail(manifestResponse));
          return;
        }
        if (!requirementsResponse.ok) {
          if (!cancelled) setLoadError(await readErrorDetail(requirementsResponse));
          return;
        }
        const manifestBody: ManifestRecord = await manifestResponse.json();
        const requirementsBody: CriticalBehaviorRequirementView[] =
          await requirementsResponse.json();
        if (!manifestBody || typeof manifestBody.status !== "string") {
          if (!cancelled) setLoadError("malformed manifest response");
          return;
        }
        if (!Array.isArray(requirementsBody)) {
          if (!cancelled) setLoadError("malformed requirements response");
          return;
        }
        if (cancelled) return;
        setManifest(manifestBody);
        setRequirements(requirementsBody);
        setMandate(parseMandateContent(manifestBody.mandate_json));
        setForbiddenActions(parseForbiddenActions(manifestBody.forbidden_actions_json));
        setMandateBase(mandateBaseOf(manifestBody.mandate_json));
        setForbiddenBase(forbiddenBaseOf(manifestBody.forbidden_actions_json));
        setListDrafts({});
        setDirty(false);
      } catch (caught) {
        if (!cancelled) setLoadError(networkErrorMessage(caught));
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [base, organizationId, projectId, manifestId]);

  const editable = manifest?.status === "draft" || manifest?.status === "review_required";

  const forbiddenActionStatuses = useMemo(
    () => evaluateForbiddenActions(forbiddenActions, requirements),
    [forbiddenActions, requirements],
  );
  const blockers = useMemo(
    () => mandateLockBlockers(forbiddenActionStatuses),
    [forbiddenActionStatuses],
  );

  function setTextField(field: MandateTextField, value: string) {
    if (!editable) return;
    setMandate((prev) => ({ ...prev, [field]: value === "" ? null : value }));
    setDirty(true);
  }

  function setNumberField(field: MandateNumberField, value: string) {
    if (!editable) return;
    const parsed = value === "" ? null : Number(value);
    setMandate((prev) => ({
      ...prev,
      [field]: parsed !== null && Number.isFinite(parsed) ? parsed : null,
    }));
    setDirty(true);
  }

  function setListField(field: MandateListField, value: string) {
    if (!editable) return;
    const items = value
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
    setListDrafts((prev) => ({ ...prev, [field]: value }));
    setMandate((prev) => ({ ...prev, [field]: items }));
    setDirty(true);
  }

  function addForbiddenAction() {
    if (!editable) return;
    const name = newActionName.trim();
    if (!name || forbiddenActions.some((entry) => entry.name === name)) return;
    setForbiddenActions((prev) => [...prev, { name, reason: newActionReason.trim() }]);
    setNewActionName("");
    setNewActionReason("");
    setDirty(true);
  }

  function removeForbiddenAction(name: string) {
    if (!editable) return;
    setForbiddenActions((prev) => prev.filter((entry) => entry.name !== name));
    setDirty(true);
  }

  async function handleSave() {
    if (!manifest || !editable || !dirty) return;
    setBusy(true);
    setSaveError(null);
    try {
      const response = await fetch(manifestUrl(base, organizationId, projectId, manifestId), {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mandate_json: serializeMandateContent(mandate, mandateBase),
          forbidden_actions_json: serializeForbiddenActions(forbiddenActions, forbiddenBase),
        }),
      });
      if (!response.ok) {
        // The draft stays exactly as the user left it -- a refused save
        // never discards or "resolves" anything.
        setSaveError(await readErrorDetail(response));
        return;
      }
      const updated: ManifestRecord = await response.json();
      if (!updated || typeof updated.status !== "string") {
        setSaveError("malformed manifest response");
        return;
      }
      // Only `manifest` is replaced by the save response. `requirements`
      // (observed/enforced) is untouched here -- see this module's own
      // docstring for why a save must never double as a conflict
      // resolution.
      setManifest(updated);
      setMandateBase(
        mandateBaseOf(updated.mandate_json ?? serializeMandateContent(mandate, mandateBase)),
      );
      setForbiddenBase(
        forbiddenBaseOf(
          updated.forbidden_actions_json ??
            serializeForbiddenActions(forbiddenActions, forbiddenBase),
        ),
      );
      setListDrafts({});
      setDirty(false);
    } catch (caught) {
      setSaveError(networkErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshEnforcement() {
    setBusy(true);
    setRefreshError(null);
    try {
      const response = await fetch(requirementsUrl(base, organizationId, projectId), {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        setRefreshError(await readErrorDetail(response));
        return;
      }
      const body: CriticalBehaviorRequirementView[] = await response.json();
      if (!Array.isArray(body)) {
        setRefreshError("malformed requirements response");
        return;
      }
      setRequirements(body);
    } catch (caught) {
      setRefreshError(networkErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <div data-testid="mandate-editor-ui">
        <p role="alert" data-testid="mandate-load-error">
          {loadError}
        </p>
      </div>
    );
  }
  if (!manifest) {
    return (
      <div data-testid="mandate-editor-ui">
        <p data-testid="mandate-loading">Loading mandate…</p>
      </div>
    );
  }

  return (
    <div data-testid="mandate-editor-ui">
      <section data-testid="mandate-fields-section">
        <h2>Mandate</h2>
        {!editable && (
          <p data-testid="mandate-readonly-notice">
            Manifest is {manifest.status}; mandate fields are read-only.
          </p>
        )}
        {MANDATE_TEXT_FIELDS.map((field) => (
          <div key={field}>
            <label htmlFor={`mandate-${field}`}>{field}</label>
            <textarea
              id={`mandate-${field}`}
              data-testid={`mandate-field-${field}`}
              value={mandate[field] ?? ""}
              disabled={!editable}
              onChange={(event) => setTextField(field, event.target.value)}
            />
          </div>
        ))}
        {MANDATE_LIST_FIELDS.map((field) => (
          <div key={field}>
            <label htmlFor={`mandate-${field}`}>{field} (comma-separated)</label>
            <input
              id={`mandate-${field}`}
              data-testid={`mandate-field-${field}`}
              value={listDrafts[field] ?? mandate[field].join(", ")}
              disabled={!editable}
              onChange={(event) => setListField(field, event.target.value)}
            />
          </div>
        ))}
        {MANDATE_NUMBER_FIELDS.map((field) => (
          <div key={field}>
            <label htmlFor={`mandate-${field}`}>{field}</label>
            <input
              id={`mandate-${field}`}
              data-testid={`mandate-field-${field}`}
              type="number"
              value={mandate[field] ?? ""}
              disabled={!editable}
              onChange={(event) => setNumberField(field, event.target.value)}
            />
          </div>
        ))}
      </section>

      <section data-testid="forbidden-actions-section">
        <h2>Forbidden actions and conflict resolution</h2>
        <p>
          Desired (mandate) vs. observed (repository tool schema) vs. enforced (runtime, Section
          66.4) -- a declared forbidden action is never shown as safe unless the real backend has
          actually been observed to enforce it.
        </p>
        <ul data-testid="forbidden-action-list">
          {forbiddenActionStatuses.map((status) => (
            <li key={status.name} data-testid={`forbidden-action-row-${status.name}`}>
              <span data-testid={`forbidden-action-name-${status.name}`}>{status.name}</span>
              <span data-testid={`forbidden-action-status-${status.name}`}>{status.reason}</span>
              {BLOCKING_FORBIDDEN_ACTION_REASONS.includes(status.reason) && (
                <span role="alert" data-testid={`forbidden-action-conflict-${status.name}`}>
                  {blockerMessage(status)}
                </span>
              )}
              {status.reason === "not_found" && (
                <span data-testid={`forbidden-action-not-found-${status.name}`}>
                  no matching tool discovered in this project yet
                </span>
              )}
              {status.reason === "enforced" && (
                <span data-testid={`forbidden-action-enforced-${status.name}`}>
                  observed enforced at runtime
                </span>
              )}
              {editable && (
                <button
                  type="button"
                  data-testid={`forbidden-action-remove-${status.name}`}
                  onClick={() => removeForbiddenAction(status.name)}
                >
                  Remove from mandate
                </button>
              )}
            </li>
          ))}
        </ul>

        {editable && (
          <div data-testid="forbidden-action-add-form">
            <label htmlFor="new-forbidden-action-name">Action/tool name</label>
            <input
              id="new-forbidden-action-name"
              data-testid="new-forbidden-action-name"
              value={newActionName}
              onChange={(event) => setNewActionName(event.target.value)}
            />
            <label htmlFor="new-forbidden-action-reason">Reason</label>
            <input
              id="new-forbidden-action-reason"
              data-testid="new-forbidden-action-reason"
              value={newActionReason}
              onChange={(event) => setNewActionReason(event.target.value)}
            />
            <button
              type="button"
              data-testid="forbidden-action-add-button"
              onClick={addForbiddenAction}
            >
              Add forbidden action
            </button>
          </div>
        )}

        <button
          type="button"
          data-testid="refresh-enforcement-button"
          disabled={busy}
          onClick={() => void handleRefreshEnforcement()}
        >
          Refresh enforcement status
        </button>
        {refreshError && (
          <p role="alert" data-testid="refresh-enforcement-error">
            {refreshError}
          </p>
        )}
      </section>

      <section data-testid="mandate-lock-readiness-section">
        <h2>Lock readiness</h2>
        {blockers.length === 0 ? (
          <p data-testid="mandate-lock-ready">No unresolved mandate conflicts.</p>
        ) : (
          <div data-testid="mandate-lock-blocked">
            <p>Lock is blocked by {blockers.length} unresolved mandate conflict(s):</p>
            <ul data-testid="mandate-lock-blockers">
              {blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {editable && (
        <button
          type="button"
          data-testid="mandate-save-button"
          disabled={!dirty || busy}
          onClick={() => void handleSave()}
        >
          Save mandate
        </button>
      )}
      {saveError && (
        <p role="alert" data-testid="mandate-save-error">
          {saveError}
        </p>
      )}
    </div>
  );
}
