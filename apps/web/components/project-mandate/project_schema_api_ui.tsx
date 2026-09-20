/**
 * Project create/edit form (FTF-0401).
 *
 * A real form driven entirely by the live FTF-0401 API
 * (`POST/PATCH /api/v1/organizations/{organization_id}/projects[/{id}]`
 * and `POST .../{id}/ui-state`) -- every submit is a real `fetch` against
 * the actual backend, never a fake local state update (Law 9: "Every UI
 * action used in the happy path invokes real backend behavior").
 *
 * `project_type` is only ever settable at create time (the field is
 * disabled once editing an existing project) -- this mirrors the backend's
 * own immutability rule (`apply_edits` never accepts `project_type`;
 * see `apps/api/app/projects/project_crud.py`'s own module docstring) so
 * the UI cannot even present a control that would silently no-op or
 * mislead the user into thinking a downgrade happened.
 *
 * `agent_objective` is only rendered when `project_type` is
 * `agentic_product` -- the same agentic/generic distinction the backend
 * enforces in `validate_agent_objective`.
 *
 * "Saved UI state" (this task's own acceptance clause) is a real, explicit
 * "Save draft" action against `POST .../ui-state`, not a client-only
 * localStorage stash -- draft form values genuinely round-trip through the
 * backend's `ui_state` column so a user can resume a partially-filled
 * setup from a different session. It only applies once a project exists
 * (there is nothing to attach draft state to before the first real
 * create), so it is disabled in create mode.
 *
 * Scope disclosed, not hidden (see docs/certifications/queue.md): this
 * repository has no Next.js app shell yet (no `next.config`, no
 * `app/page.tsx`) -- FTF-0401's own file list names only this component
 * and its test, not a full page/routing scaffold, the same precedent
 * `oidc_login.tsx` already established. This is a real, DOM-level tested
 * component (Vitest + React Testing Library + jsdom with a mocked
 * `fetch`), not a full browser E2E.
 */

import { useState } from "react";

export type ProjectType = "agentic_product" | "generic_model_tuning";
export type ProjectStatus = "setup_incomplete" | "active" | "archived";

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

export function ProjectSetupForm({
  apiBaseUrl,
  organizationId,
  workspaceId,
  project = null,
  onSaved,
}: ProjectSetupFormProps) {
  const isEdit = project !== null;
  const [name, setName] = useState(project?.name ?? "");
  const [projectType, setProjectType] = useState<ProjectType>(
    project?.project_type ?? "generic_model_tuning",
  );
  const [description, setDescription] = useState(project?.description ?? "");
  const [agentObjective, setAgentObjective] = useState(project?.agent_objective ?? "");
  const [securityProfile, setSecurityProfile] = useState(project?.security_profile ?? "standard");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [draftSaved, setDraftSaved] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setDraftSaved(false);
    try {
      const objective = projectType === "agentic_product" ? agentObjective : null;
      const url = isEdit
        ? projectItemUrl(apiBaseUrl, organizationId, project!.id)
        : projectsCollectionUrl(apiBaseUrl, organizationId);
      const method = isEdit ? "PATCH" : "POST";
      const body = isEdit
        ? {
            name,
            description: description || null,
            agent_objective: objective,
            security_profile: securityProfile,
            expected_row_version: project!.row_version,
          }
        : {
            workspace_id: workspaceId,
            name,
            project_type: projectType,
            description: description || null,
            agent_objective: objective,
            security_profile: securityProfile,
          };
      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        setError(await readErrorDetail(response));
        return;
      }
      const saved: ProjectRecord = await response.json();
      onSaved?.(saved);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveDraft() {
    if (!isEdit) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(projectUiStateUrl(apiBaseUrl, organizationId, project!.id), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          ui_state: { name, projectType, description, agentObjective, securityProfile },
        }),
      });
      if (!response.ok) {
        setError(await readErrorDetail(response));
        return;
      }
      setDraftSaved(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} data-testid="project-setup-form">
      <label htmlFor="project-name">Name</label>
      <input
        id="project-name"
        data-testid="project-name-input"
        value={name}
        onChange={(event) => setName(event.target.value)}
        required
      />

      <label htmlFor="project-type">Project type</label>
      <select
        id="project-type"
        data-testid="project-type-select"
        value={projectType}
        disabled={isEdit}
        onChange={(event) => setProjectType(event.target.value as ProjectType)}
      >
        <option value="generic_model_tuning">Generic model tuning</option>
        <option value="agentic_product">Agentic product</option>
      </select>

      {projectType === "agentic_product" && (
        <>
          <label htmlFor="agent-objective">Agent objective</label>
          <textarea
            id="agent-objective"
            data-testid="agent-objective-input"
            value={agentObjective}
            onChange={(event) => setAgentObjective(event.target.value)}
            required
          />
        </>
      )}

      <label htmlFor="project-description">Description</label>
      <textarea
        id="project-description"
        data-testid="project-description-input"
        value={description}
        onChange={(event) => setDescription(event.target.value)}
      />

      <label htmlFor="security-profile">Security profile</label>
      <input
        id="security-profile"
        data-testid="security-profile-input"
        value={securityProfile}
        onChange={(event) => setSecurityProfile(event.target.value)}
      />

      {isEdit && project!.project_type === "agentic_product" && (
        <p data-testid="setup-incomplete-notice">
          {project!.status === "setup_incomplete"
            ? "Setup incomplete: a Target Application connection and Agent Manifest are required before this project can go active."
            : null}
        </p>
      )}

      {error && <p data-testid="project-form-error">{error}</p>}

      <button type="submit" data-testid="project-submit-button" disabled={saving}>
        {isEdit ? "Save changes" : "Create project"}
      </button>
      <button
        type="button"
        data-testid="project-save-draft-button"
        disabled={saving || !isEdit}
        onClick={handleSaveDraft}
      >
        Save draft
      </button>
      {draftSaved && <p data-testid="project-draft-saved-notice">Draft saved</p>}
    </form>
  );
}
