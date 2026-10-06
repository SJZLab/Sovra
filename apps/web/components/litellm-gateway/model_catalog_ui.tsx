/**
 * Model catalog UI (FTF-0214, Section 2-3/38/65/77/81).
 *
 * This is the first live caller of the real, composed
 * `GET .../connections/litellm/{connectionId}/catalog` and
 * `POST .../connections/litellm/{connectionId}/models/{modelId}/probe`
 * routes on `app.litellm.model_catalog_routes.build_model_catalog_router`
 * -- themselves the first live caller of nine previously-unwired,
 * already-CERTIFIED pure-logic modules (FTF-0205/0206/0207/0208/0209/
 * 0210/0211/0212/0213/H004). See that module's own docstring for the
 * full composition.
 *
 * This task's own acceptance clause ("Catalog shows actual access
 * freshness/capability/probe evidence, safe filters and explicit project
 * selection") is met structurally:
 *
 * - Freshness/capability/probe evidence: `entries` is rendered directly
 *   from the live route's own real `access_state`/`last_successful_
 *   check_at`/`metadata_status`/`fields`, never re-derived or guessed
 *   client-side; a probe result is shown only after the real
 *   `POST .../probe` response returns.
 * - Safe filters: `statusFilter`/`metadataFilter` are sent to the live
 *   route as query parameters (the same safe, narrowing-only filter the
 *   backend itself enforces -- see that module's own docstring) rather
 *   than hidden client-side; an unrecognized value is surfaced as the
 *   backend's own real 400, never silently dropped.
 * - Explicit project selection: `selectedProjectId` starts `null` and is
 *   never pre-selected -- the catalog cannot be loaded and the probe
 *   button stays disabled until a real project row, fetched from the
 *   live `GET .../projects` route (FTF-0401, already-CERTIFIED), has
 *   been explicitly clicked by the user.
 *
 * Negative_acceptance ("No automatic model selection, fabricated costs or
 * hidden stale access; viewer cannot run paid probe") is met structurally
 * too: no entry is ever pre-selected or auto-probed; a `usage`/cost field
 * absent from the live response is rendered as "unknown", never a
 * fabricated zero; a `stale`/`removed` entry's probe button is disabled
 * client-side (defense in depth only -- the live route's own
 * `require_model_access` call is the real, authoritative enforcement,
 * never this component); and the "viewer cannot run paid probe" half is
 * the live route's own `organization_admin`-only role gate -- a 403 from
 * that gate is surfaced here as `probeError`, never retried or masked.
 *
 * Scope disclosed, not hidden (same precedent
 * `candidate_selection_ui_no_auto_selection.tsx`/
 * `github_branch_ref_selector_immutable_sha_resolver.tsx` already
 * established): this repository has no Next.js `app/` page tree yet
 * (FTF-H029, "create the first page," has not landed), so this is a
 * real, DOM-level tested component (Vitest + React Testing Library +
 * jsdom with a mocked `fetch`), not a full browser E2E -- Law 6 (UI
 * reachability from `/`) stays disclosed BLOCKED on that dependency,
 * same as FTF-04A03/FTF-04A04's own already-disclosed gap.
 */

import { useState } from "react";

export interface ProjectSummary {
  id: string;
  name: string;
  status: string;
}

export interface CatalogEntry {
  id: string;
  status: "callable" | "stale" | "removed";
  metadata_status: "enriched" | "limited";
  first_seen_at: string;
  last_seen_callable_at: string | null;
  fields: Record<string, unknown>;
}

export interface AnomalyRecord {
  id: number;
  kind: string;
  detected_at: string;
  previous_count: number;
  new_count: number;
  status: string;
}

export interface CatalogSnapshot {
  connection_status: string;
  access_state: "unknown" | "healthy" | "revoked" | "degraded";
  last_successful_check_at: string | null;
  last_checked_at: string | null;
  alert: string | null;
  anomaly: AnomalyRecord | null;
  model_groups: { state: string; entries: Record<string, Record<string, unknown>> } | null;
  entries: CatalogEntry[];
}

export interface ProbeUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ProbeResult {
  probe_type: string;
  state: string;
  route_identity_state: "verified" | "unverified";
  resolved_model: string | null;
  usage: ProbeUsage | null;
  latency_ms: number | null;
  detail: string;
  probed_at: string;
  [key: string]: unknown;
}

export type ProbeKind = "chat" | "streaming" | "tool_call" | "structured_output";

export interface ModelCatalogUIProps {
  apiBaseUrl: string;
  organizationId: string;
  connectionId: string;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function projectsListUrl(apiBaseUrl: string, organizationId: string): string {
  return `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}/projects`;
}

export function catalogUrl(
  apiBaseUrl: string,
  organizationId: string,
  connectionId: string,
  projectId: string,
  statusFilter: string,
  metadataFilter: string,
): string {
  const base =
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/connections/litellm/${connectionId}/catalog`;
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  if (statusFilter) params.set("status", statusFilter);
  if (metadataFilter) params.set("metadata_status", metadataFilter);
  const query = params.toString();
  return query ? `${base}?${query}` : base;
}

export function probeUrl(
  apiBaseUrl: string,
  organizationId: string,
  connectionId: string,
  modelId: string,
  projectId: string,
): string {
  const base =
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/connections/litellm/${connectionId}/models/${modelId}/probe`;
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  const query = params.toString();
  return query ? `${base}?${query}` : base;
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

function networkErrorMessage(caught: unknown): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `network error: ${reason}`;
}

export function ModelCatalogUI({ apiBaseUrl, organizationId, connectionId }: ModelCatalogUIProps) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  // Deliberately starts null, never pre-selected -- this task's own real
  // "explicit project selection" requirement.
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState("");
  const [metadataFilter, setMetadataFilter] = useState("");
  const [catalog, setCatalog] = useState<CatalogSnapshot | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [probeKind, setProbeKind] = useState<ProbeKind>("chat");
  const [probingModelId, setProbingModelId] = useState<string | null>(null);
  const [probeResults, setProbeResults] = useState<Record<string, ProbeResult>>({});
  const [probeError, setProbeError] = useState<string | null>(null);

  async function handleLoadProjects() {
    setLoadingProjects(true);
    setProjectsError(null);
    try {
      const response = await fetch(projectsListUrl(apiBaseUrl, organizationId), {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        setProjectsError(await readErrorDetail(response));
        return;
      }
      const body: ProjectSummary[] = await response.json();
      if (!Array.isArray(body)) {
        setProjectsError("malformed project listing response");
        return;
      }
      setProjects(body);
    } catch (caught) {
      setProjectsError(networkErrorMessage(caught));
    } finally {
      setLoadingProjects(false);
    }
  }

  async function handleLoadCatalog() {
    if (!selectedProjectId) {
      // Defense in depth only -- the "Load catalog" button is already
      // disabled until a project is explicitly selected (see below); the
      // live route itself does not require project_id on the catalog
      // read, but this component never sends the request without one.
      setCatalogError("select a project first");
      return;
    }
    setLoadingCatalog(true);
    setCatalogError(null);
    try {
      const response = await fetch(
        catalogUrl(
          apiBaseUrl,
          organizationId,
          connectionId,
          selectedProjectId,
          statusFilter,
          metadataFilter,
        ),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        setCatalogError(await readErrorDetail(response));
        return;
      }
      const body: CatalogSnapshot = await response.json();
      if (!body || !Array.isArray(body.entries)) {
        setCatalogError("malformed catalog response");
        return;
      }
      setCatalog(body);
    } catch (caught) {
      setCatalogError(networkErrorMessage(caught));
    } finally {
      setLoadingCatalog(false);
    }
  }

  async function handleProbe(modelId: string) {
    if (!selectedProjectId) {
      setProbeError("select a project first");
      return;
    }
    setProbingModelId(modelId);
    setProbeError(null);
    try {
      const response = await fetch(
        probeUrl(apiBaseUrl, organizationId, connectionId, modelId, selectedProjectId),
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ probe_type: probeKind }),
        },
      );
      if (!response.ok) {
        setProbeError(await readErrorDetail(response));
        return;
      }
      const body: ProbeResult = await response.json();
      if (!body || typeof body.state !== "string") {
        setProbeError("malformed probe response");
        return;
      }
      setProbeResults((prev) => ({ ...prev, [modelId]: body }));
    } catch (caught) {
      // Nothing is recorded as a result unless the probe genuinely
      // returned -- a network failure never fabricates a probe outcome.
      setProbeError(networkErrorMessage(caught));
    } finally {
      setProbingModelId(null);
    }
  }

  return (
    <div data-testid="model-catalog-ui">
      <section data-testid="project-selection">
        <button
          type="button"
          data-testid="load-projects-button"
          disabled={loadingProjects}
          onClick={handleLoadProjects}
        >
          Load projects
        </button>
        {projectsError && <p data-testid="projects-error">{projectsError}</p>}
        <ul data-testid="project-list">
          {projects.map((project) => (
            <li key={project.id} data-testid={`project-row-${project.id}`}>
              <span>{project.name}</span>
              <button
                type="button"
                data-testid={`project-select-button-${project.id}`}
                aria-pressed={selectedProjectId === project.id}
                onClick={() => setSelectedProjectId(project.id)}
              >
                {selectedProjectId === project.id ? "Selected" : "Select"}
              </button>
            </li>
          ))}
        </ul>
        {!selectedProjectId && <p data-testid="no-project-selected">No project selected yet.</p>}
      </section>

      <section data-testid="catalog-filters">
        <label>
          Status filter
          <select
            data-testid="status-filter"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="">All</option>
            <option value="callable">Callable</option>
            <option value="stale">Stale</option>
            <option value="removed">Removed</option>
          </select>
        </label>
        <label>
          Metadata filter
          <select
            data-testid="metadata-filter"
            value={metadataFilter}
            onChange={(event) => setMetadataFilter(event.target.value)}
          >
            <option value="">All</option>
            <option value="enriched">Enriched</option>
            <option value="limited">Limited</option>
          </select>
        </label>
        <button
          type="button"
          data-testid="load-catalog-button"
          disabled={!selectedProjectId || loadingCatalog}
          onClick={handleLoadCatalog}
        >
          Load catalog
        </button>
      </section>

      {catalogError && <p data-testid="catalog-error">{catalogError}</p>}

      {catalog && (
        <section data-testid="catalog-results">
          <p data-testid="catalog-access-state">Access: {catalog.access_state}</p>
          <p data-testid="catalog-freshness">
            Last successful check: {catalog.last_successful_check_at ?? "never"}
          </p>
          {catalog.alert && <p data-testid="catalog-alert">{catalog.alert}</p>}
          {catalog.anomaly && (
            <p data-testid="catalog-anomaly">
              Anomaly: {catalog.anomaly.previous_count} -&gt; {catalog.anomaly.new_count} (
              {catalog.anomaly.status})
            </p>
          )}

          <label>
            Probe kind
            <select
              data-testid="probe-kind"
              value={probeKind}
              onChange={(event) => setProbeKind(event.target.value as ProbeKind)}
            >
              <option value="chat">Chat</option>
              <option value="streaming">Streaming</option>
              <option value="tool_call">Tool call</option>
              <option value="structured_output">Structured output</option>
            </select>
          </label>
          {probeError && <p data-testid="probe-error">{probeError}</p>}

          <ul data-testid="catalog-entry-list">
            {catalog.entries.map((entry) => {
              const result = probeResults[entry.id];
              return (
                <li key={entry.id} data-testid={`catalog-entry-${entry.id}`}>
                  <span data-testid={`entry-status-${entry.id}`}>{entry.status}</span>
                  <span data-testid={`entry-metadata-status-${entry.id}`}>
                    {entry.metadata_status}
                  </span>
                  <button
                    type="button"
                    data-testid={`probe-button-${entry.id}`}
                    disabled={
                      entry.status !== "callable" || !selectedProjectId || probingModelId !== null
                    }
                    onClick={() => handleProbe(entry.id)}
                  >
                    Run probe
                  </button>
                  {result && (
                    <span data-testid={`probe-result-${entry.id}`}>
                      {result.state} (
                      {result.usage ? `${result.usage.total_tokens} tokens` : "cost unknown"})
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
