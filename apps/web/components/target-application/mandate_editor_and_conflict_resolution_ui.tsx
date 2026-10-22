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

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";

import {
  manifestUrl,
  type ManifestRecord,
} from "./manifest_review_edit_ui_with_per_field_evidence";
import {
  requirementsUrl,
  type CriticalBehaviorRequirementView,
} from "../project-mandate/requirements_ui";
import {
  Banner,
  Button,
  EmptyState,
  ErrorSummary,
  Field,
  Pending,
  StatusBadge,
  dirFor,
  phrase,
  resolveLocale,
  type ErrorSummaryItem,
  type Locale,
} from "../ui";
import {
  MALFORMED_FAILURE,
  NETWORK_FAILURE,
  failureFromResponse,
  safeFailureMessage,
  isRecord,
  type Failure,
} from "./common";
import { helpFor } from "./mandate_field_help";
import { mandateT, type MandateStringKey } from "./strings_04a22";

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

/** The English reason a blocking conflict blocks lock, for callers that want a plain string (the
 * editor itself renders the localized form via `conflictSentence`). */
function blockerMessage(status: ForbiddenActionStatus): string {
  return `${status.name}: ${conflictSentence(status.reason, "en")}`;
}

/** The sentence for one conflict reason in the given language; "" for a non-blocking reason. */
export function conflictSentence(
  reason: ForbiddenActionConflictReason,
  locale: Locale | string = "en",
): string {
  if (reason === "not_declared_critical") return mandateT("blockerUndeclared", locale);
  if (reason === "not_runtime_enforced") return mandateT("blockerUnenforced", locale);
  return "";
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

const MANIFEST_STATUSES = ["draft", "review_required", "locked", "superseded"] as const;
const STATUS_WORD = {
  draft: "statusDraft",
  review_required: "statusReviewRequired",
  locked: "statusLocked",
  superseded: "statusSuperseded",
} as const;

/** `null` or a JSON object: the only shapes either mandate column may hold. */
function isObjectOrNull(value: unknown): boolean {
  return value === null || isRecord(value);
}

export interface ManifestIdentity {
  manifestId: string;
  projectId: string;
  organizationId: string;
  /** Known once the manifest has loaded; the snapshot is fixed at creation, so a later reply must
   * name the same one. Absent on the first load. */
  snapshotId?: string;
}

/**
 * The ONE binding function for a manifest body (load AND save response): a typed 2xx that is for
 * this manifest and this project, in a known status, with both mandate columns present as an
 * object or null. Anything else is `null` (malformed); nothing is ever defaulted into success.
 */
export function bindManifest(body: unknown, expected: ManifestIdentity): ManifestRecord | null {
  if (!isRecord(body)) return null;
  if (body.id !== expected.manifestId) return null;
  if (body.project_id !== expected.projectId) return null;
  // The tenant is part of the identity: a manifest of another organization (or one that does not
  // say) is never shown or saved as this organization's.
  if (body.organization_id !== expected.organizationId) return null;
  if (typeof body.target_source_snapshot_id !== "string" || body.target_source_snapshot_id === "") {
    return null;
  }
  if (expected.snapshotId !== undefined && body.target_source_snapshot_id !== expected.snapshotId) {
    return null;
  }
  if (typeof body.version !== "number" || !Number.isInteger(body.version)) return null;
  if (
    typeof body.status !== "string" ||
    !(MANIFEST_STATUSES as readonly string[]).includes(body.status)
  ) {
    return null;
  }
  for (const column of ["mandate_json", "forbidden_actions_json"] as const) {
    // `undefined` (a missing column) is neither an object nor null, so absence is refused too.
    if (!isObjectOrNull(body[column])) return null;
  }
  return body as ManifestRecord;
}

/** `.json()` that reports an unreadable body as `null` instead of throwing, so a non-JSON 200
 * reaches the binding functions (which refuse `null`) and reads "cannot read", never "could not
 * reach the server". */
async function readBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/** Structural equality that ignores object key order (a jsonb column does not keep it). */
export function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((item, index) => sameJson(item, b[index]))
    );
  }
  if (isRecord(a) && isRecord(b)) {
    const keys = Object.keys(a);
    return (
      keys.length === Object.keys(b).length &&
      keys.every((key) => Object.hasOwn(b, key) && sameJson(a[key], b[key]))
    );
  }
  return false;
}

export interface SentMandate {
  mandate_json: unknown;
  forbidden_actions_json: unknown;
}

/**
 * Binds a SAVE reply to the request that produced it, on top of `bindManifest`: the stored
 * `mandate_json` and `forbidden_actions_json` must be what was sent (a 200 that kept the old
 * columns is a dropped edit, not a save) and the lifecycle `status` must be unchanged (a save never
 * locks or supersedes). `null` when anything differs.
 */
export function bindSavedManifest(
  body: unknown,
  expected: ManifestIdentity,
  sent: SentMandate,
  statusBefore: string,
): ManifestRecord | null {
  const bound = bindManifest(body, expected);
  if (!bound) return null;
  if (bound.status !== statusBefore) return null;
  if (!sameJson(bound.mandate_json, sent.mandate_json)) return null;
  if (!sameJson(bound.forbidden_actions_json, sent.forbidden_actions_json)) return null;
  return bound;
}

const REQUIREMENT_STRINGS = ["tool_definition_id", "name", "side_effect_class"] as const;
const REQUIREMENT_BOOLEANS = [
  "is_critical",
  "requires_confirmation",
  "measurable",
  "runtime_enforced",
] as const;

/** The `/requirements` list, every row fully typed. One bad row refuses the whole list: dropping
 * it would turn an unknown tool into "not found", which reads as "nothing to enforce". */
export function parseRequirements(body: unknown): CriticalBehaviorRequirementView[] | null {
  if (!Array.isArray(body)) return null;
  for (const row of body) {
    if (!isRecord(row)) return null;
    for (const key of REQUIREMENT_STRINGS) if (typeof row[key] !== "string") return null;
    for (const key of REQUIREMENT_BOOLEANS) if (typeof row[key] !== "boolean") return null;
  }
  return body as CriticalBehaviorRequirementView[];
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export interface MandateEditorAndConflictResolutionUIProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  manifestId: string;
  locale?: Locale;
}

function Shell({
  locale,
  busy,
  children,
}: {
  locale: Locale;
  busy?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      data-testid="mandate-editor-ui"
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
      aria-busy={busy ? true : undefined}
      style={{ display: "grid", gap: "var(--space-4)" }}
    >
      {children}
    </div>
  );
}

/** Field has no multi-line variant, so the long-text mandate fields use this one with the same
 * contract (label above, requirement marker, helper and error wired by aria-describedby). */
function TextAreaField({
  id,
  label,
  helper,
  locale,
  ...rest
}: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> & {
  id: string;
  label: string;
  helper?: string;
  locale: Locale;
}) {
  const helperId = helper ? `${id}-helper` : undefined;
  return (
    <div>
      <label htmlFor={id}>
        {label} <span>{phrase("fieldOptional", locale)}</span>
      </label>
      {helper ? <div id={helperId}>{helper}</div> : null}
      <textarea {...rest} id={id} aria-describedby={helperId} />
    </div>
  );
}

function ConflictBadge({
  icon,
  tone,
  label,
  locale,
}: {
  icon: string;
  tone: "success" | "muted";
  label: string;
  locale: Locale;
}) {
  return (
    <span
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
      data-tone={tone}
      style={{ color: `var(--color-${tone})` }}
    >
      <span aria-hidden="true">{icon}</span> {label}
    </span>
  );
}

export function MandateEditorAndConflictResolutionUI({
  apiBaseUrl,
  organizationId,
  projectId,
  manifestId,
  locale: localeProp = "en",
}: MandateEditorAndConflictResolutionUIProps) {
  const base = trimBase(apiBaseUrl);
  const locale = resolveLocale(localeProp);
  const t = (key: MandateStringKey, vars?: Record<string, string | number>) =>
    mandateT(key, locale, vars);
  const identity = `${base}|${organizationId}|${projectId}|${manifestId}`;
  const identityRef = useRef(identity);
  const rootRef = useRef<HTMLDivElement>(null);

  const [manifest, setManifest] = useState<ManifestRecord | null>(null);
  const [requirements, setRequirements] = useState<CriticalBehaviorRequirementView[]>([]);
  const [loadFailure, setLoadFailure] = useState<Failure | null>(null);
  const [loadNonce, setLoadNonce] = useState(0);

  const [mandate, setMandate] = useState<MandateContent>(emptyMandateContent());
  const [forbiddenActions, setForbiddenActions] = useState<ForbiddenActionEntry[]>([]);
  const [mandateBase, setMandateBase] = useState<Record<string, unknown>>({});
  const [forbiddenBase, setForbiddenBase] = useState<Record<string, unknown>>({});
  // Raw text of a list input while the user is typing, so "en," is not collapsed to "en".
  const [listDrafts, setListDrafts] = useState<Partial<Record<MandateListField, string>>>({});
  const [dirty, setDirty] = useState(false);

  const [newActionName, setNewActionName] = useState("");
  const [newActionReason, setNewActionReason] = useState("");
  const [addErrors, setAddErrors] = useState<ErrorSummaryItem[]>([]);
  const [addSubmitCount, setAddSubmitCount] = useState(0);

  const [saveFailure, setSaveFailure] = useState<Failure | null>(null);
  const [saved, setSaved] = useState(false);
  const [refreshFailure, setRefreshFailure] = useState<Failure | null>(null);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);

  useEffect(() => {
    identityRef.current = identity;
  }, [identity]);

  useEffect(() => {
    let cancelled = false;
    // A different organization, project or manifest never shows the previous one's data or edits.
    setManifest(null);
    setRequirements([]);
    setLoadFailure(null);
    setMandate(emptyMandateContent());
    setForbiddenActions([]);
    setMandateBase({});
    setForbiddenBase({});
    setListDrafts({});
    setDirty(false);
    setNewActionName("");
    setNewActionReason("");
    setAddErrors([]);
    setSaveFailure(null);
    setSaved(false);
    setRefreshFailure(null);
    setSaving(false);
    setRefreshing(false);
    setConfirmingDiscard(false);
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
          const failure = await failureFromResponse(manifestResponse);
          if (!cancelled) setLoadFailure(failure);
          return;
        }
        if (!requirementsResponse.ok) {
          const failure = await failureFromResponse(requirementsResponse);
          if (!cancelled) setLoadFailure(failure);
          return;
        }
        const manifestRaw = await readBody(manifestResponse);
        const requirementsRaw = await readBody(requirementsResponse);
        if (cancelled) return;
        const manifestBody = bindManifest(manifestRaw, { manifestId, projectId, organizationId });
        const requirementsBody = parseRequirements(requirementsRaw);
        if (!manifestBody || !requirementsBody) {
          setLoadFailure(MALFORMED_FAILURE);
          return;
        }
        setManifest(manifestBody);
        setRequirements(requirementsBody);
        setMandate(parseMandateContent(manifestBody.mandate_json));
        setForbiddenActions(parseForbiddenActions(manifestBody.forbidden_actions_json));
        setMandateBase(mandateBaseOf(manifestBody.mandate_json));
        setForbiddenBase(forbiddenBaseOf(manifestBody.forbidden_actions_json));
      } catch {
        // A rejected fetch is a visible, retryable failure, never a blank
        // page and never an empty "no conflicts" state (Law 17).
        if (!cancelled) setLoadFailure(NETWORK_FAILURE);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [base, organizationId, projectId, manifestId, loadNonce]);

  const statusEditable = manifest?.status === "draft" || manifest?.status === "review_required";
  // While a save is in flight the form is read-only: an edit typed now would be neither in the
  // request nor in the reply, and the reply would overwrite it while the screen read "saved".
  const editable = statusEditable && !saving;
  const busy = saving || refreshing;

  const forbiddenActionStatuses = useMemo(
    () => evaluateForbiddenActions(forbiddenActions, requirements),
    [forbiddenActions, requirements],
  );
  const blockers = useMemo(
    () =>
      forbiddenActionStatuses.filter((s) => BLOCKING_FORBIDDEN_ACTION_REASONS.includes(s.reason)),
    [forbiddenActionStatuses],
  );

  function markEdited() {
    setDirty(true);
    setSaved(false);
  }

  function setTextField(field: MandateTextField, value: string) {
    if (!editable) return;
    setMandate((prev) => ({ ...prev, [field]: value === "" ? null : value }));
    markEdited();
  }

  function setNumberField(field: MandateNumberField, value: string) {
    if (!editable) return;
    const parsed = value === "" ? null : Number(value);
    setMandate((prev) => ({
      ...prev,
      [field]: parsed !== null && Number.isFinite(parsed) ? parsed : null,
    }));
    markEdited();
  }

  function setListField(field: MandateListField, value: string) {
    if (!editable) return;
    const items = value
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
    setListDrafts((prev) => ({ ...prev, [field]: value }));
    setMandate((prev) => ({ ...prev, [field]: items }));
    markEdited();
  }

  function addForbiddenAction() {
    if (!editable) return;
    const name = newActionName.trim();
    const problems: ErrorSummaryItem[] = [];
    if (!name) {
      problems.push({ fieldId: "new-forbidden-action-name", message: t("addNameEmpty") });
    } else if (forbiddenActions.some((entry) => entry.name === name)) {
      problems.push({
        fieldId: "new-forbidden-action-name",
        message: t("addNameDuplicate", { name }),
      });
    }
    if (problems.length > 0) {
      setAddErrors(problems);
      setAddSubmitCount((count) => count + 1);
      return;
    }
    setAddErrors([]);
    setForbiddenActions((prev) => [...prev, { name, reason: newActionReason.trim() }]);
    setNewActionName("");
    setNewActionReason("");
    markEdited();
  }

  function removeForbiddenAction(name: string) {
    if (!editable) return;
    setForbiddenActions((prev) => prev.filter((entry) => entry.name !== name));
    markEdited();
  }

  function focusById(id: string) {
    document.getElementById(id)?.focus();
  }

  function discardEdits() {
    if (!manifest) return;
    setMandate(parseMandateContent(manifest.mandate_json));
    setForbiddenActions(parseForbiddenActions(manifest.forbidden_actions_json));
    setMandateBase(mandateBaseOf(manifest.mandate_json));
    setForbiddenBase(forbiddenBaseOf(manifest.forbidden_actions_json));
    setListDrafts({});
    setNewActionName("");
    setNewActionReason("");
    setAddErrors([]);
    setSaveFailure(null);
    setDirty(false);
    setConfirmingDiscard(false);
    // The trigger just disappeared; keep the keyboard user where the work is.
    queueMicrotask(() => focusById(`mandate-${MANDATE_TEXT_FIELDS[0]}`));
  }

  function closeDiscard() {
    setConfirmingDiscard(false);
    queueMicrotask(() =>
      rootRef.current
        ?.querySelector<HTMLElement>('[data-testid="mandate-discard-button"]')
        ?.focus(),
    );
  }

  async function handleSave() {
    if (!manifest || !editable || !dirty) return;
    const started = identityRef.current;
    setSaving(true);
    setSaveFailure(null);
    setSaved(false);
    try {
      const sent: SentMandate = {
        mandate_json: serializeMandateContent(mandate, mandateBase),
        forbidden_actions_json: serializeForbiddenActions(forbiddenActions, forbiddenBase),
      };
      const response = await fetch(manifestUrl(base, organizationId, projectId, manifestId), {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sent),
      });
      if (!response.ok) {
        // The draft stays exactly as the user left it -- a refused save
        // never discards or "resolves" anything.
        const failure = await failureFromResponse(response);
        if (identityRef.current === started) setSaveFailure(failure);
        return;
      }
      const raw = await readBody(response);
      if (identityRef.current !== started) return;
      // A reply that does not say what was sent (unreadable, another manifest, old columns, a
      // status change) is not a save: the drafts stay and nothing reads "saved".
      const updated = bindSavedManifest(
        raw,
        {
          manifestId,
          projectId,
          organizationId,
          snapshotId: manifest.target_source_snapshot_id,
        },
        sent,
        manifest.status,
      );
      if (!updated) {
        setSaveFailure(MALFORMED_FAILURE);
        return;
      }
      // Only `manifest` is replaced by the save response. `requirements`
      // (observed/enforced) is untouched here -- see this module's own
      // docstring for why a save must never double as a conflict
      // resolution.
      setManifest(updated);
      // The server's returned manifest is what is shown after a save.
      setMandate(parseMandateContent(updated.mandate_json));
      setForbiddenActions(parseForbiddenActions(updated.forbidden_actions_json));
      setMandateBase(mandateBaseOf(updated.mandate_json));
      setForbiddenBase(forbiddenBaseOf(updated.forbidden_actions_json));
      setListDrafts({});
      setDirty(false);
      setSaved(true);
    } catch {
      if (identityRef.current === started) setSaveFailure(NETWORK_FAILURE);
    } finally {
      if (identityRef.current === started) setSaving(false);
    }
  }

  async function handleRefreshEnforcement() {
    const started = identityRef.current;
    setRefreshing(true);
    setRefreshFailure(null);
    try {
      const response = await fetch(requirementsUrl(base, organizationId, projectId), {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        const failure = await failureFromResponse(response);
        if (identityRef.current === started) setRefreshFailure(failure);
        return;
      }
      const raw = await readBody(response);
      if (identityRef.current !== started) return;
      const body = parseRequirements(raw);
      if (!body) {
        setRefreshFailure(MALFORMED_FAILURE);
        return;
      }
      setRequirements(body);
    } catch {
      if (identityRef.current === started) setRefreshFailure(NETWORK_FAILURE);
    } finally {
      if (identityRef.current === started) setRefreshing(false);
    }
  }

  if (loadFailure) {
    return (
      <Shell locale={locale}>
        <div data-testid="mandate-load-error">
          <Banner kind="error" locale={locale}>
            {safeFailureMessage(loadFailure, locale)}
          </Banner>
        </div>
        <div>
          <Button
            variant="primary"
            data-testid="mandate-load-retry"
            onClick={() => setLoadNonce((n) => n + 1)}
          >
            {t("retry")}
          </Button>
        </div>
      </Shell>
    );
  }
  if (!manifest) {
    return (
      <Shell locale={locale} busy>
        <p data-testid="mandate-loading">{t("loading")}</p>
        <Pending label={t("loading")} locale={locale} />
      </Shell>
    );
  }

  const statusWord = t(STATUS_WORD[manifest.status as (typeof MANIFEST_STATUSES)[number]]);
  const nameError = addErrors.find((e) => e.fieldId === "new-forbidden-action-name")?.message;

  return (
    <Shell locale={locale} busy={busy}>
      <div ref={rootRef} style={{ display: "grid", gap: "var(--space-4)" }}>
        <section
          data-testid="mandate-fields-section"
          style={{ display: "grid", gap: "var(--space-3)" }}
        >
          <h2>{t("title")}</h2>
          {!statusEditable && (
            <div data-testid="mandate-readonly-notice">
              <Banner kind="info" locale={locale}>
                {t("readOnly", { status: statusWord })}
              </Banner>
            </div>
          )}
          {MANDATE_TEXT_FIELDS.map((field) => (
            <TextAreaField
              key={field}
              id={`mandate-${field}`}
              data-testid={`mandate-field-${field}`}
              label={t(field)}
              helper={helpFor(field, locale)}
              locale={locale}
              value={mandate[field] ?? ""}
              disabled={!statusEditable}
              readOnly={saving}
              onChange={(event) => setTextField(field, event.target.value)}
            />
          ))}
          {MANDATE_LIST_FIELDS.map((field) => (
            <Field
              style={{ minBlockSize: "var(--target-min)" }}
              key={field}
              id={`mandate-${field}`}
              data-testid={`mandate-field-${field}`}
              label={t(field)}
              helper={helpFor(field, locale)}
              requirement="optional"
              locale={locale}
              value={listDrafts[field] ?? mandate[field].join(", ")}
              disabled={!statusEditable}
              readOnly={saving}
              onChange={(event) => setListField(field, event.target.value)}
            />
          ))}
          {MANDATE_NUMBER_FIELDS.map((field) => (
            <Field
              style={{ minBlockSize: "var(--target-min)" }}
              key={field}
              id={`mandate-${field}`}
              data-testid={`mandate-field-${field}`}
              type="number"
              label={t(field)}
              helper={helpFor(field, locale)}
              requirement="optional"
              locale={locale}
              value={mandate[field] ?? ""}
              disabled={!statusEditable}
              readOnly={saving}
              onChange={(event) => setNumberField(field, event.target.value)}
            />
          ))}
        </section>

        <section
          data-testid="forbidden-actions-section"
          style={{ display: "grid", gap: "var(--space-3)" }}
        >
          <h2>{t("forbiddenTitle")}</h2>
          <p>{t("forbiddenExplainer")}</p>
          {forbiddenActionStatuses.length === 0 ? (
            statusEditable ? (
              <EmptyState
                title={t("forbiddenEmptyTitle")}
                why={t("forbiddenEmptyWhy")}
                action={
                  <Button
                    variant="primary"
                    data-testid="forbidden-empty-add-action"
                    onClick={() => focusById("new-forbidden-action-name")}
                  >
                    {t("forbiddenEmptyAction")}
                  </Button>
                }
              />
            ) : (
              <p data-testid="forbidden-empty-readonly">{t("forbiddenEmptyReadOnly")}</p>
            )
          ) : (
            <ul
              data-testid="forbidden-action-list"
              style={{ display: "grid", gap: "var(--space-3)" }}
            >
              {forbiddenActionStatuses.map((status) => {
                const entry = forbiddenActions.find((a) => a.name === status.name);
                const blocking = BLOCKING_FORBIDDEN_ACTION_REASONS.includes(status.reason);
                return (
                  <li key={status.name} data-testid={`forbidden-action-row-${status.name}`}>
                    <span data-testid={`forbidden-action-name-${status.name}`}>
                      <bdi>{status.name}</bdi>
                    </span>{" "}
                    <span data-testid={`forbidden-action-status-${status.name}`}>
                      {status.reason === "not_declared_critical" && (
                        <StatusBadge
                          state="blocked"
                          detail={t("badgeUndeclared")}
                          locale={locale}
                        />
                      )}
                      {status.reason === "not_runtime_enforced" && (
                        <StatusBadge
                          state="blocked"
                          detail={t("badgeUnenforced")}
                          locale={locale}
                        />
                      )}
                      {status.reason === "not_found" && (
                        <ConflictBadge
                          icon="?"
                          tone="muted"
                          label={t("badgeNotFound")}
                          locale={locale}
                        />
                      )}
                      {status.reason === "enforced" && (
                        <ConflictBadge
                          icon="✓"
                          tone="success"
                          label={t("badgeEnforced")}
                          locale={locale}
                        />
                      )}
                    </span>
                    <div>
                      {t("reasonLabel")}:{" "}
                      <bdi>{entry && entry.reason.trim() ? entry.reason : t("noReason")}</bdi>
                    </div>
                    {blocking && (
                      <div data-testid={`forbidden-action-conflict-${status.name}`}>
                        {conflictSentence(status.reason, locale)}
                      </div>
                    )}
                    {status.reason === "not_found" && (
                      <div data-testid={`forbidden-action-not-found-${status.name}`}>
                        {t("noteNotFound")}
                      </div>
                    )}
                    {status.reason === "enforced" && (
                      <div data-testid={`forbidden-action-enforced-${status.name}`}>
                        {t("noteEnforced")}
                      </div>
                    )}
                    {statusEditable && (
                      <Button
                        data-testid={`forbidden-action-remove-${status.name}`}
                        aria-label={t("removeActionNamed", { name: status.name })}
                        disabled={saving}
                        onClick={() => removeForbiddenAction(status.name)}
                      >
                        {t("removeAction")}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {statusEditable && (
            <div
              data-testid="forbidden-action-add-form"
              style={{ display: "grid", gap: "var(--space-3)" }}
            >
              <ErrorSummary errors={addErrors} locale={locale} submitCount={addSubmitCount} />
              <Field
                style={{ minBlockSize: "var(--target-min)" }}
                id="new-forbidden-action-name"
                data-testid="new-forbidden-action-name"
                label={t("addName")}
                requirement="required"
                locale={locale}
                error={nameError}
                readOnly={saving}
                value={newActionName}
                onChange={(event) => {
                  setNewActionName(event.target.value);
                  if (addErrors.length > 0) setAddErrors([]);
                }}
              />
              <Field
                style={{ minBlockSize: "var(--target-min)" }}
                id="new-forbidden-action-reason"
                data-testid="new-forbidden-action-reason"
                label={t("addReason")}
                requirement="optional"
                locale={locale}
                readOnly={saving}
                value={newActionReason}
                onChange={(event) => setNewActionReason(event.target.value)}
              />
              <div>
                <Button
                  variant="primary"
                  data-testid="forbidden-action-add-button"
                  disabled={saving}
                  onClick={addForbiddenAction}
                >
                  {t("addButton")}
                </Button>
              </div>
            </div>
          )}

          <div>
            <Button
              data-testid="refresh-enforcement-button"
              disabled={busy}
              onClick={() => void handleRefreshEnforcement()}
            >
              {refreshing ? t("refreshing") : t("refresh")}
            </Button>
          </div>
          {refreshing && <Pending label={t("refreshing")} locale={locale} />}
          {refreshFailure && (
            <div data-testid="refresh-enforcement-error">
              <Banner kind="error" locale={locale}>
                {`${safeFailureMessage(refreshFailure, locale)} ${t("refreshStale")}`}
              </Banner>
            </div>
          )}
        </section>

        <section
          data-testid="mandate-lock-readiness-section"
          aria-live="polite"
          style={{ display: "grid", gap: "var(--space-2)" }}
        >
          <h2>{t("lockTitle")}</h2>
          {blockers.length === 0 ? (
            <p data-testid="mandate-lock-ready">
              <span aria-hidden="true">✓</span> {t("lockReady")}
            </p>
          ) : (
            <div data-testid="mandate-lock-blocked">
              <p>
                <span aria-hidden="true">⊗</span> {t("lockBlocked", { count: blockers.length })}
              </p>
              <ul data-testid="mandate-lock-blockers">
                {blockers.map((blocker) => (
                  <li key={blocker.name}>
                    <bdi>{blocker.name}</bdi>: {conflictSentence(blocker.reason, locale)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        {statusEditable && (
          <section style={{ display: "grid", gap: "var(--space-3)" }}>
            {dirty && (
              <p data-testid="mandate-dirty-notice">
                <span aria-hidden="true">●</span> {t("unsaved")}
              </p>
            )}
            {saved && !dirty && (
              <div data-testid="mandate-saved-notice">
                <Banner kind="success" locale={locale}>
                  {t("saved")}
                </Banner>
              </div>
            )}
            {saveFailure && (
              <div data-testid="mandate-save-error">
                <Banner kind="error" locale={locale}>
                  {`${safeFailureMessage(saveFailure, locale)} ${t("saveFailedKept")}`}
                </Banner>
              </div>
            )}
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
              <Button
                variant="primary"
                data-testid="mandate-save-button"
                disabled={!dirty || busy}
                disabledReason={!dirty && !busy ? t("saveNothing") : undefined}
                onClick={() => void handleSave()}
              >
                {saving ? t("saving") : t("save")}
              </Button>
              {dirty && (
                <Button
                  data-testid="mandate-discard-button"
                  disabled={busy}
                  onClick={() => {
                    setConfirmingDiscard(true);
                    queueMicrotask(() =>
                      rootRef.current
                        ?.querySelector<HTMLElement>('[data-testid="mandate-discard-keep"]')
                        ?.focus(),
                    );
                  }}
                >
                  {t("discard")}
                </Button>
              )}
            </div>
            {saving && <Pending label={t("saving")} locale={locale} />}
            {dirty && confirmingDiscard && (
              <div
                role="alertdialog"
                aria-label={t("discard")}
                aria-describedby="mandate-discard-body"
                data-testid="mandate-discard-confirm"
                onKeyDown={(event) => {
                  if (event.key === "Escape") closeDiscard();
                }}
                style={{ display: "grid", gap: "var(--space-3)" }}
              >
                <p id="mandate-discard-body">{t("discardConfirm")}</p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
                  <Button data-testid="mandate-discard-keep" onClick={closeDiscard}>
                    {t("discardKeep")}
                  </Button>
                  <Button
                    variant="danger"
                    data-testid="mandate-discard-confirm-button"
                    onClick={discardEdits}
                  >
                    {t("discard")}
                  </Button>
                </div>
              </div>
            )}
          </section>
        )}
      </div>
    </Shell>
  );
}
