/**
 * Project create/edit form (FTF-0401), rebuilt for FTF-UX04 on the UX01 design system.
 *
 * A real form driven entirely by the live FTF-0401 API
 * (`POST/PATCH /api/v1/organizations/{organization_id}/projects[/{id}]`
 * and `POST .../{id}/ui-state`) -- every submit is a real `fetch` against
 * the actual backend, never a fake local state update (Law 9: "Every UI
 * action used in the happy path invokes real backend behavior").
 *
 * Certified behaviour kept: `project_type` is only settable at create time (the control is locked
 * once editing, now WITH the reason shown, NN/g H5); `agent_objective` is only rendered for an
 * `agentic_product`; "Save draft" is a real `POST .../ui-state`, only available once a project exists.
 *
 * What UX04 adds (docs/design/audit-2026-10.md section 10-11, docs/design/UX_STANDARD.md):
 *  - labels above fields with required/optional marked, helper text, inline errors after blur and a
 *    focusable error summary after a failed submit (H5/H9); the security profile is the closed set
 *    the backend enforces, not free text;
 *  - Create/Save changes is the primary action, Save draft the secondary (H4/H8);
 *  - a request is a success only for a 2xx whose body is a well-formed project that MATCHES what was
 *    sent (UI CHECKLIST a/b/d); a network failure, 5xx, non-JSON or malformed body is a typed,
 *    retryable failure that keeps every entry (Law 17), shown as a localized sentence, never the
 *    backend's own `detail` (internal codes and English developer sentences);
 *  - the saved record, not the stale prop, supplies the next `expected_row_version`; a saved draft is
 *    offered back (H3) but never applied silently (H5);
 *  - English and Arabic (RTL) through `locale`, `lang`/`dir` on the root, user data in `<bdi>`.
 *
 * Scope disclosed, not hidden (see docs/certifications/queue.md): this repository has no Next.js app
 * shell yet (FTF-H029) -- FTF-0401's own file list names only this component and its test. This is a
 * real, DOM-level tested component (Vitest + React Testing Library + jsdom with a mocked `fetch`),
 * plus a real-Chromium evidence harness and a real-API job against the real router.
 */

import { useEffect, useId, useRef, useState } from "react";

import { isRecord, type Failure } from "../target-application/common";
import {
  Banner,
  Button,
  cleanText,
  dirFor,
  ErrorSummary,
  Field,
  Pending,
  resolveLocale,
  type ErrorSummaryItem,
  type Locale,
} from "../ui";
import { SelectField, StatusLine, TextAreaField, type SelectOption } from "./form_controls";
import { isOneOf, isString, isStringOrNull } from "./guards";
import { jsonEqual, requestJson } from "./request";
import { mandateFailureMessage, tm } from "./strings_project_mandate";

export const PROJECT_TYPES = ["agentic_product", "generic_model_tuning"] as const;
export type ProjectType = (typeof PROJECT_TYPES)[number];
export const PROJECT_STATUSES = ["setup_incomplete", "active", "archived"] as const;
export type ProjectStatus = (typeof PROJECT_STATUSES)[number];
/** The closed, ordered set the backend enforces (`SECURITY_PROFILE_LEVELS`), least to most strict. */
export const SECURITY_PROFILES = ["standard", "zero_egress", "air_gapped"] as const;

/** The backend's own length limits (project_schema.py / organization_workspace_schema.py). */
export const NAME_MAX = 200;
export const TEXT_MAX = 4000;

export interface ProjectRecord {
  id: string;
  organization_id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  project_type: ProjectType;
  agent_objective: string | null;
  security_profile: string;
  budget_policy_id: string | null;
  status: ProjectStatus;
  ui_state: Record<string, unknown>;
  row_version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface ProjectSetupFormProps {
  apiBaseUrl: string;
  organizationId: string;
  workspaceId: string;
  project?: ProjectRecord | null;
  onSaved?: (project: ProjectRecord) => void;
  locale?: Locale;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function projectsCollectionUrl(apiBaseUrl: string, organizationId: string): string {
  return `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}/projects`;
}

export function projectItemUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return `${projectsCollectionUrl(apiBaseUrl, organizationId)}/${projectId}`;
}

export function projectUiStateUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return `${projectItemUrl(apiBaseUrl, organizationId, projectId)}/ui-state`;
}

// ---------------------------------------------------------------------------------------------
// Strict parsing and binding (UI CHECKLIST a, b): a reply only counts when it is a well-formed
// project that is the one this request was about.
// ---------------------------------------------------------------------------------------------

/** Every field of the FTF-0401 `_serialize` shape, type- and enum-checked; null when anything is off. */
export function parseProjectRecord(body: unknown): ProjectRecord | null {
  if (!isRecord(body)) return null;
  const b = body;
  if (
    !isString(b.id) ||
    b.id === "" ||
    !isString(b.organization_id) ||
    b.organization_id === "" ||
    !isString(b.workspace_id) ||
    b.workspace_id === "" ||
    !isString(b.name) ||
    !isStringOrNull(b.description) ||
    !isOneOf(PROJECT_TYPES, b.project_type) ||
    !isStringOrNull(b.agent_objective) ||
    !isString(b.security_profile) ||
    !isStringOrNull(b.budget_policy_id) ||
    !isOneOf(PROJECT_STATUSES, b.status) ||
    !isRecord(b.ui_state) ||
    typeof b.row_version !== "number" ||
    !Number.isSafeInteger(b.row_version) ||
    b.row_version < 1 ||
    !isString(b.created_by) ||
    !isString(b.created_at) ||
    !isString(b.updated_at) ||
    !isStringOrNull(b.archived_at)
  ) {
    return null;
  }
  return {
    id: b.id,
    organization_id: b.organization_id,
    workspace_id: b.workspace_id,
    name: b.name,
    description: b.description,
    project_type: b.project_type,
    agent_objective: b.agent_objective,
    security_profile: b.security_profile,
    budget_policy_id: b.budget_policy_id,
    status: b.status,
    ui_state: b.ui_state,
    row_version: b.row_version,
    created_by: b.created_by,
    created_at: b.created_at,
    updated_at: b.updated_at,
    archived_at: b.archived_at,
  };
}

export interface ProjectSentFields {
  name: string;
  project_type: ProjectType;
  description: string | null;
  agent_objective: string | null;
  security_profile: string;
}

/**
 * The ONE place a create or edit reply is compared with what was sent (UI CHECKLIST b). `current` is
 * the record being edited (null on create). A mismatch on any field means the reply is not this
 * request's result, so it is shown as a failure, never as a save.
 */
export function bindProjectSave(
  sent: ProjectSentFields,
  reply: ProjectRecord,
  scope: { organizationId: string; workspaceId: string },
  current: ProjectRecord | null,
): boolean {
  if (reply.organization_id !== scope.organizationId) return false;
  if (reply.status === "archived" || reply.archived_at !== null) return false;
  if (reply.name !== sent.name) return false;
  if (reply.project_type !== sent.project_type) return false;
  if (reply.security_profile !== sent.security_profile) return false;
  if (current === null) {
    return (
      reply.workspace_id === scope.workspaceId &&
      reply.description === sent.description &&
      reply.agent_objective === sent.agent_objective &&
      reply.row_version === 1
    );
  }
  // An edit sends `null` for "leave unchanged" (the API skips a null field), so the reply keeps the
  // record's own value there.
  return (
    reply.id === current.id &&
    reply.workspace_id === current.workspace_id &&
    reply.description === (sent.description === null ? current.description : sent.description) &&
    reply.agent_objective ===
      (sent.agent_objective === null ? current.agent_objective : sent.agent_objective) &&
    reply.row_version === current.row_version + 1
  );
}

/** A draft save changes `ui_state` only: same record, same version, the ui_state that was sent. */
export function bindDraftSave(
  sentUiState: Record<string, unknown>,
  reply: ProjectRecord,
  organizationId: string,
  current: ProjectRecord,
): boolean {
  return (
    reply.id === current.id &&
    reply.organization_id === organizationId &&
    reply.row_version === current.row_version &&
    jsonEqual(reply.ui_state, sentUiState)
  );
}

interface Draft {
  name: string;
  description: string;
  agentObjective: string;
  securityProfile: string;
}

/** A saved draft that can be offered back; null for anything that is not exactly the shape this form
 * writes, or that belongs to another project type. */
export function parseDraft(uiState: Record<string, unknown>, record: ProjectRecord): Draft | null {
  const { name, projectType, description, agentObjective, securityProfile } = uiState;
  if (
    !isString(name) ||
    projectType !== record.project_type ||
    !isString(description) ||
    !isString(agentObjective) ||
    !isString(securityProfile)
  ) {
    return null;
  }
  return { name, description, agentObjective, securityProfile };
}

function newerOf(prop: ProjectRecord | null, saved: ProjectRecord | null): ProjectRecord | null {
  if (prop === null) return saved;
  // `>=`: a draft save keeps the version but stores a newer `ui_state`.
  if (saved !== null && saved.id === prop.id && saved.row_version >= prop.row_version) return saved;
  return prop;
}

// ---------------------------------------------------------------------------------------------

type Busy = "submit" | "draft" | null;
type Notice = "created" | "saved" | "draft" | "restored" | null;

export function ProjectSetupForm(props: ProjectSetupFormProps) {
  // A different organization, workspace or project is a different form (UI CHECKLIST c): nothing
  // typed for one is ever sent for another.
  const identity = `${props.organizationId}|${props.workspaceId}|${props.project?.id ?? "new"}`;
  return <ProjectSetupFormInner key={identity} {...props} />;
}

function ProjectSetupFormInner({
  apiBaseUrl,
  organizationId,
  workspaceId,
  project = null,
  onSaved,
  locale = "en",
}: ProjectSetupFormProps) {
  const lang = resolveLocale(locale);
  const titleId = useId();
  const [saved, setSaved] = useState<ProjectRecord | null>(null);
  const record = newerOf(project, saved);
  const isEdit = record !== null;

  const [name, setName] = useState(record?.name ?? "");
  const [projectType, setProjectType] = useState<ProjectType>(
    record?.project_type ?? "generic_model_tuning",
  );
  const [description, setDescription] = useState(record?.description ?? "");
  const [agentObjective, setAgentObjective] = useState(record?.agent_objective ?? "");
  const [securityProfile, setSecurityProfile] = useState(record?.security_profile ?? "standard");

  const [touched, setTouched] = useState({ name: false, objective: false });
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [submitCount, setSubmitCount] = useState(0);
  const [busy, setBusy] = useState<Busy>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [draftDismissed, setDraftDismissed] = useState(false);

  // An external refresh (the caller hands over a newer version than our own last save produced) is
  // the "reload" the conflict message asks for: the form takes the fresh record.
  const ownVersion = useRef<number | null>(null);
  const seenVersion = useRef<number | null>(project?.row_version ?? null);
  useEffect(() => {
    if (!project || project.row_version === seenVersion.current) return;
    seenVersion.current = project.row_version;
    if (project.row_version === ownVersion.current) return;
    setName(project.name);
    setProjectType(project.project_type);
    setDescription(project.description ?? "");
    setAgentObjective(project.agent_objective ?? "");
    setSecurityProfile(project.security_profile);
    setFailure(null);
    setNotice(null);
    setSubmitAttempted(false);
    setTouched({ name: false, objective: false });
    setDraftDismissed(false);
  }, [project]);

  // A control that disables itself while its request is in flight drops keyboard focus; hand it back
  // to the action the user took once the request has settled.
  const focusAfter = useRef<string | null>(null);
  useEffect(() => {
    if (busy === null && focusAfter.current !== null) {
      document.getElementById(focusAfter.current)?.focus();
      focusAfter.current = null;
    }
  }, [busy]);

  const agentic = projectType === "agentic_product";
  const archived = record?.status === "archived";
  const nameBlank = cleanText(name) === "";
  const objectiveBlank = agentic && cleanText(agentObjective) === "";
  const nameError =
    (touched.name || submitAttempted) && nameBlank ? tm("nameRequired", locale) : undefined;
  const objectiveError =
    (touched.objective || submitAttempted) && objectiveBlank
      ? tm("objectiveRequired", locale)
      : undefined;
  const summary: ErrorSummaryItem[] = [];
  if (submitAttempted && nameBlank) {
    summary.push({ fieldId: "project-name", message: tm("nameRequired", locale) });
  }
  if (submitAttempted && objectiveBlank) {
    summary.push({ fieldId: "agent-objective", message: tm("objectiveRequired", locale) });
  }

  const draft = record ? parseDraft(record.ui_state, record) : null;
  const draftDiffers =
    draft !== null &&
    record !== null &&
    (draft.name !== name ||
      draft.description !== description ||
      draft.agentObjective !== agentObjective ||
      draft.securityProfile !== securityProfile) &&
    (draft.name !== record.name ||
      draft.description !== (record.description ?? "") ||
      draft.agentObjective !== (record.agent_objective ?? "") ||
      draft.securityProfile !== record.security_profile);

  function edited(): void {
    setNotice(null);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (busy !== null || archived) return;
    if (nameBlank || objectiveBlank) {
      setSubmitAttempted(true);
      setSubmitCount((count) => count + 1);
      return;
    }
    setBusy("submit");
    focusAfter.current = "project-submit";
    setFailure(null);
    setNotice(null);
    const objective = agentic ? agentObjective : null;
    const sent: ProjectSentFields = {
      name,
      project_type: projectType,
      // The API skips a null field, so clearing an existing description has to be sent as "".
      description:
        description === "" ? ((record?.description ?? "") === "" ? null : "") : description,
      agent_objective: objective,
      security_profile: securityProfile,
    };
    const url = record
      ? projectItemUrl(apiBaseUrl, organizationId, record.id)
      : projectsCollectionUrl(apiBaseUrl, organizationId);
    const body = record
      ? {
          name: sent.name,
          description: sent.description,
          agent_objective: sent.agent_objective,
          security_profile: sent.security_profile,
          expected_row_version: record.row_version,
        }
      : { workspace_id: workspaceId, ...sent };
    const outcome = await requestJson(url, {
      method: record ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });
    if (!outcome.ok) {
      setFailure(outcome.failure);
      setBusy(null);
      return;
    }
    const reply = parseProjectRecord(outcome.body);
    if (reply === null || !bindProjectSave(sent, reply, { organizationId, workspaceId }, record)) {
      setFailure({ kind: "malformed" });
      setBusy(null);
      return;
    }
    ownVersion.current = reply.row_version;
    setSaved(reply);
    setNotice(record ? "saved" : "created");
    setBusy(null);
    onSaved?.(reply);
  }

  async function handleSaveDraft(): Promise<void> {
    if (record === null || archived) return;
    setBusy("draft");
    focusAfter.current = "project-save-draft";
    setFailure(null);
    setNotice(null);
    const uiState = { name, projectType, description, agentObjective, securityProfile };
    const outcome = await requestJson(projectUiStateUrl(apiBaseUrl, organizationId, record.id), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ ui_state: uiState }),
    });
    if (!outcome.ok) {
      setFailure(outcome.failure);
      setBusy(null);
      return;
    }
    const reply = parseProjectRecord(outcome.body);
    if (reply === null || !bindDraftSave(uiState, reply, organizationId, record)) {
      setFailure({ kind: "malformed" });
      setBusy(null);
      return;
    }
    // Only the draft changed: keep the version, take the stored ui_state.
    setSaved(reply);
    setNotice("draft");
    setBusy(null);
  }

  function restoreDraft(): void {
    if (!draft) return;
    setName(draft.name);
    setDescription(draft.description);
    setAgentObjective(draft.agentObjective);
    setSecurityProfile(draft.securityProfile);
    setDraftDismissed(true);
    setNotice("restored");
  }

  const profileOptions: SelectOption[] = SECURITY_PROFILES.map((value) => ({
    value,
    label: tm(
      value === "standard"
        ? "profileStandard"
        : value === "zero_egress"
          ? "profileZeroEgress"
          : "profileAirGapped",
      locale,
    ),
  }));
  if (!(SECURITY_PROFILES as readonly string[]).includes(securityProfile)) {
    // A profile the server holds that this form does not offer stays visible and selected rather
    // than being silently swapped for another one.
    profileOptions.push({
      value: securityProfile,
      label: tm("profileOther", locale, { value: securityProfile }),
    });
  }

  const submitLabel = isEdit ? tm("submitSave", locale) : tm("submitCreate", locale);
  const noticeText =
    notice === "created"
      ? tm("projectCreated", locale)
      : notice === "saved"
        ? tm("projectSaved", locale)
        : notice === "draft"
          ? tm("draftSaved", locale)
          : notice === "restored"
            ? tm("draftRestored", locale)
            : null;

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      data-testid="project-setup-form"
      aria-labelledby={titleId}
      aria-busy={busy !== null}
      lang={lang}
      dir={dirFor(locale)}
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr)",
        gap: "var(--space-4)",
        maxInlineSize: "100%",
      }}
    >
      <h2 id={titleId}>{isEdit ? tm("formTitleEdit", locale) : tm("formTitleCreate", locale)}</h2>

      <ErrorSummary errors={summary} locale={locale} submitCount={submitCount} />

      {archived && (
        <div data-testid="project-archived-notice">
          <Banner kind="warning" locale={locale}>
            {tm("archivedNotice", locale)}
          </Banner>
        </div>
      )}

      {isEdit &&
        record.project_type === "agentic_product" &&
        record.status === "setup_incomplete" && (
          <div data-testid="setup-incomplete-notice">
            <Banner kind="info" locale={locale}>
              {tm("setupIncomplete", locale)}
            </Banner>
          </div>
        )}

      {draftDiffers && !draftDismissed && (
        <div data-testid="project-draft-offer">
          <Banner kind="info" locale={locale}>
            {tm("draftFound", locale)}
          </Banner>
          <Button
            id="project-restore-draft"
            variant="secondary"
            data-testid="project-restore-draft-button"
            onClick={restoreDraft}
          >
            {tm("draftRestore", locale)}
          </Button>
        </div>
      )}

      <Field
        id="project-name"
        data-testid="project-name-input"
        label={tm("nameLabel", locale)}
        helper={tm("nameHelper", locale)}
        error={nameError}
        requirement="required"
        locale={locale}
        value={name}
        maxLength={NAME_MAX}
        readOnly={busy !== null}
        onChange={(event) => {
          setName(event.target.value);
          edited();
        }}
        onBlur={() => setTouched((t) => ({ ...t, name: true }))}
      />

      <SelectField
        id="project-type"
        data-testid="project-type-select"
        label={tm("typeLabel", locale)}
        helper={tm("typeHelper", locale)}
        lockedReason={isEdit ? tm("typeLocked", locale) : undefined}
        requirement="required"
        locale={locale}
        value={projectType}
        options={[
          { value: "generic_model_tuning", label: tm("typeGeneric", locale) },
          { value: "agentic_product", label: tm("typeAgentic", locale) },
        ]}
        onChange={(event) => {
          if (busy !== null) return;
          setProjectType(event.target.value as ProjectType);
          edited();
        }}
      />

      {agentic && (
        <TextAreaField
          id="agent-objective"
          data-testid="agent-objective-input"
          label={tm("objectiveLabel", locale)}
          helper={tm("objectiveHelper", locale)}
          error={objectiveError}
          requirement="required"
          locale={locale}
          value={agentObjective}
          maxLength={TEXT_MAX}
          readOnly={busy !== null}
          onChange={(event) => {
            setAgentObjective(event.target.value);
            edited();
          }}
          onBlur={() => setTouched((t) => ({ ...t, objective: true }))}
        />
      )}

      <TextAreaField
        id="project-description"
        data-testid="project-description-input"
        label={tm("descriptionLabel", locale)}
        helper={tm("descriptionHelper", locale)}
        requirement="optional"
        locale={locale}
        value={description}
        maxLength={TEXT_MAX}
        readOnly={busy !== null}
        onChange={(event) => {
          setDescription(event.target.value);
          edited();
        }}
      />

      <SelectField
        id="security-profile"
        data-testid="security-profile-input"
        label={tm("profileLabel", locale)}
        helper={tm("profileHelper", locale)}
        requirement="required"
        locale={locale}
        value={securityProfile}
        options={profileOptions}
        onChange={(event) => {
          if (busy !== null) return;
          setSecurityProfile(event.target.value);
          edited();
        }}
      />

      {failure && (
        <div data-testid="project-form-error">
          <Banner kind="error" locale={locale}>
            {`${mandateFailureMessage(failure, "project", locale)} ${tm("inputKept", locale)}`}
          </Banner>
        </div>
      )}

      {busy !== null && (
        <Pending
          label={busy === "draft" ? tm("savingDraft", locale) : tm("saving", locale)}
          locale={locale}
        />
      )}

      {noticeText !== null && (
        <StatusLine
          locale={locale}
          testId={
            notice === "draft"
              ? "project-draft-saved-notice"
              : notice === "restored"
                ? "project-draft-restored-notice"
                : "project-saved-notice"
          }
        >
          {noticeText}
        </StatusLine>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
        <Button
          id="project-submit"
          type="submit"
          variant="primary"
          data-testid="project-submit-button"
          disabled={busy !== null || archived}
        >
          {submitLabel}
        </Button>
        <Button
          id="project-save-draft"
          variant="secondary"
          data-testid="project-save-draft-button"
          disabled={busy !== null || archived}
          disabledReason={isEdit ? undefined : tm("saveDraftNeedsProject", locale)}
          onClick={() => void handleSaveDraft()}
        >
          {tm("saveDraft", locale)}
        </Button>
      </div>
    </form>
  );
}
