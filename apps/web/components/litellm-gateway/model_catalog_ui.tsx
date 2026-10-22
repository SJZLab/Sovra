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
 *
 * FTF-UX05 (2026-10-22): "Run probe" fired a real, paid, external-provider
 * call with no confirmation step (audit finding #9, severity 3 -- NN/g
 * "Confirmation Dialogs: confirm before an irreversible action"). Clicking
 * "Run probe" now opens an inline confirm (`role="alertdialog"`) naming the
 * real consequence; only its own "Run probe" button calls the live route.
 * No cost figure is shown here: this component has no wiring to a per-route
 * rate (FTF-1301 cost profiles are keyed by profile, not by this catalog's
 * route/model), and Law 11 forbids a fabricated number -- the honest
 * consequence is stated in words instead. Error text now carries
 * `role="alert"` (finding X2) and the busy buttons carry `aria-busy`
 * (finding X7); an optional `locale` prop renders the new confirm text and
 * the container's own `lang`/`dir` in Arabic (finding X4).
 *
 * FTF-UX05 round 2 (2026-10-22, L2 return): round 1 left four real behaviors
 * (the two `aria-busy` flags, the two `role="alert"` paragraphs and the
 * confirm's own button text) with no test pinning them -- removing any one
 * of them left the suite green. Round 2 adds the missing assertions (see
 * `ftf_0214.spec.ts`, the "round 2 pins" block) and independently
 * re-verified each one RED-then-GREEN. It also fixes two real defects the
 * L2 review found by reproducing them directly:
 *
 * - WCAG 3.1.2 (lang of parts): the container's `lang`/`dir` covered every
 *   piece of UI copy -- labels, buttons, filter options, status prefixes --
 *   that stayed hardcoded English regardless of `locale`. Every string this
 *   component itself authors now routes through `phrase()` with a real `ar`
 *   translation (see `ui/phrases.ts`'s FTF-0214 block), so `lang="ar"` is
 *   now honest for that copy. Values this component does not author --
 *   project/model names, the server's own `status`/`access_state`/
 *   `metadata_status` enum literals, raw JSON field dumps, probe
 *   identifiers and timestamps -- are data, not UI copy: they are wrapped in
 *   `<bdi>` (the same convention `candidate_selection_ui_no_auto_selection.tsx`
 *   already established) rather than given a language tag, since their
 *   actual language is not something this component controls. A `detail`
 *   string surfaced raw from the live backend (`catalogError`/`probeError`/
 *   `projectsError`) is the same kind of data -- it is wrapped in `<bdi>`
 *   inside its `role="alert"` paragraph, and the paragraph itself no longer
 *   claims a `lang` for text whose language is the backend's choice, not
 *   this component's. Default `locale="en"` rendering is byte-identical to
 *   round 1 -- every phrase's `en` value is the exact pre-existing text.
 * - Focus management: the confirm `role="alertdialog"` never received focus
 *   on open (confirmed in jsdom: `document.activeElement` stayed the
 *   now-`disabled` trigger). It now follows this repo's own established
 *   Dialog convention (`mandate_editor_and_conflict_resolution_ui.tsx`'s
 *   discard-confirm: focus the least-destructive action on open, return
 *   focus to the trigger on Cancel/Escape): opening the confirm moves focus
 *   to its own Cancel button; Cancel (or Escape) returns focus to the
 *   `probe-button-<id>` trigger; Confirm, whose trigger becomes disabled for
 *   the live request, moves focus to the entry row itself (a stable,
 *   non-disabled ancestor) rather than leaving it on a disabled element. The
 *   dialog's `aria-labelledby` now points at a real, visible `<h2>` title
 *   using the `runProbeConfirmTitle` phrase round 1 defined but never wired
 *   up.
 */

import { useRef, useState } from "react";

import { dirFor, phrase, resolveLocale, type Locale } from "../ui/phrases";

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
  locale?: Locale;
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
    `/connections/litellm/${connectionId}/models/${encodeURIComponent(modelId)}/probe`;
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

function networkErrorMessage(caught: unknown, locale: Locale): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `${phrase("networkErrorPrefix", locale)} ${reason}`;
}

export function ModelCatalogUI({
  apiBaseUrl,
  organizationId,
  connectionId,
  locale = "en",
}: ModelCatalogUIProps) {
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
  // FTF-UX05 finding #9 (severity 3): a real, paid, irreversible call is never fired from the
  // first click. `confirmProbeModelId` names the entry a confirm is open for; nothing calls the
  // live route until its own "Run probe" button inside the confirm is clicked.
  const [confirmProbeModelId, setConfirmProbeModelId] = useState<string | null>(null);

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
        setProjectsError(phrase("catalogMalformedProjectListing", locale));
        return;
      }
      setProjects(body);
    } catch (caught) {
      setProjectsError(networkErrorMessage(caught, locale));
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
      setCatalogError(phrase("catalogSelectProjectFirst", locale));
      return;
    }
    setLoadingCatalog(true);
    setCatalogError(null);
    setConfirmProbeModelId(null);
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
        setCatalogError(phrase("catalogMalformedCatalog", locale));
        return;
      }
      setCatalog(body);
    } catch (caught) {
      setCatalogError(networkErrorMessage(caught, locale));
    } finally {
      setLoadingCatalog(false);
    }
  }

  async function handleProbe(modelId: string) {
    if (!selectedProjectId) {
      setProbeError(phrase("catalogSelectProjectFirst", locale));
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
        setProbeError(phrase("catalogMalformedProbe", locale));
        return;
      }
      setProbeResults((prev) => ({ ...prev, [modelId]: body }));
    } catch (caught) {
      // Nothing is recorded as a result unless the probe genuinely
      // returned -- a network failure never fabricates a probe outcome.
      setProbeError(networkErrorMessage(caught, locale));
    } finally {
      setProbingModelId(null);
    }
  }

  // Focus management for the confirm dialog (finding 3, L2 return round 2): the WAI-ARIA Alert
  // Dialog Pattern and this repo's own established Dialog convention
  // (`mandate_editor_and_conflict_resolution_ui.tsx`'s discard-confirm) both move focus to the
  // least-destructive action on open and return it to the trigger on cancel.
  const rootRef = useRef<HTMLDivElement>(null);

  function openProbeConfirm(modelId: string) {
    setConfirmProbeModelId(modelId);
    queueMicrotask(() =>
      rootRef.current
        ?.querySelector<HTMLElement>(`[data-testid="cancel-probe-button-${modelId}"]`)
        ?.focus(),
    );
  }

  function cancelProbeConfirm(modelId: string) {
    setConfirmProbeModelId(null);
    // The trigger is re-enabled again by now (the probe never ran) -- keep the keyboard user there.
    queueMicrotask(() =>
      rootRef.current
        ?.querySelector<HTMLElement>(`[data-testid="probe-button-${modelId}"]`)
        ?.focus(),
    );
  }

  function confirmProbe(modelId: string) {
    setConfirmProbeModelId(null);
    // The trigger becomes `disabled` for the live request, so focus cannot stay there; the entry
    // row itself is a stable, non-disabled ancestor (same precedent as moving focus to the first
    // field after a destructive confirm elsewhere in this repo).
    queueMicrotask(() =>
      rootRef.current
        ?.querySelector<HTMLElement>(`[data-testid="catalog-entry-${modelId}"]`)
        ?.focus(),
    );
    void handleProbe(modelId);
  }

  return (
    <div
      ref={rootRef}
      data-testid="model-catalog-ui"
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
    >
      <section data-testid="project-selection">
        <button
          type="button"
          data-testid="load-projects-button"
          disabled={loadingProjects}
          aria-busy={loadingProjects}
          onClick={handleLoadProjects}
        >
          {phrase("catalogLoadProjects", locale)}
        </button>
        {projectsError && (
          <p role="alert" data-testid="projects-error" dir={dirFor(locale)}>
            <bdi>{projectsError}</bdi>
          </p>
        )}
        <ul data-testid="project-list">
          {projects.map((project) => (
            <li key={project.id} data-testid={`project-row-${project.id}`}>
              <span>
                <bdi>{project.name}</bdi>
              </span>
              <button
                type="button"
                data-testid={`project-select-button-${project.id}`}
                aria-pressed={selectedProjectId === project.id}
                onClick={() => setSelectedProjectId(project.id)}
              >
                {selectedProjectId === project.id
                  ? phrase("selected", locale)
                  : phrase("select", locale)}
              </button>
            </li>
          ))}
        </ul>
        {!selectedProjectId && (
          <p data-testid="no-project-selected">{phrase("catalogNoProjectSelected", locale)}</p>
        )}
      </section>

      <section data-testid="catalog-filters">
        <label>
          {phrase("catalogStatusFilterLabel", locale)}
          <select
            data-testid="status-filter"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="">{phrase("catalogFilterAll", locale)}</option>
            <option value="callable">{phrase("catalogStatusCallable", locale)}</option>
            <option value="stale">{phrase("catalogStatusStale", locale)}</option>
            <option value="removed">{phrase("catalogStatusRemoved", locale)}</option>
          </select>
        </label>
        <label>
          {phrase("catalogMetadataFilterLabel", locale)}
          <select
            data-testid="metadata-filter"
            value={metadataFilter}
            onChange={(event) => setMetadataFilter(event.target.value)}
          >
            <option value="">{phrase("catalogFilterAll", locale)}</option>
            <option value="enriched">{phrase("catalogMetadataEnriched", locale)}</option>
            <option value="limited">{phrase("catalogMetadataLimited", locale)}</option>
          </select>
        </label>
        <button
          type="button"
          data-testid="load-catalog-button"
          disabled={!selectedProjectId || loadingCatalog}
          aria-busy={loadingCatalog}
          onClick={handleLoadCatalog}
        >
          {phrase("catalogLoadCatalog", locale)}
        </button>
      </section>

      {catalogError && (
        <p role="alert" data-testid="catalog-error" dir={dirFor(locale)}>
          <bdi>{catalogError}</bdi>
        </p>
      )}

      {catalog && (
        <section data-testid="catalog-results">
          <p data-testid="catalog-connection-status">
            {phrase("catalogConnectionPrefix", locale)} <bdi>{catalog.connection_status}</bdi>
          </p>
          <p data-testid="catalog-access-state">
            {phrase("catalogAccessPrefix", locale)} <bdi>{catalog.access_state}</bdi>
          </p>
          <p data-testid="catalog-freshness">
            {phrase("catalogLastCheckPrefix", locale)}{" "}
            <bdi>{catalog.last_successful_check_at ?? phrase("catalogNever", locale)}</bdi>
          </p>
          {catalog.alert && (
            <p data-testid="catalog-alert">
              <bdi>{catalog.alert}</bdi>
            </p>
          )}
          {catalog.anomaly && (
            <p data-testid="catalog-anomaly">
              {phrase("catalogAnomalyPrefix", locale)} {catalog.anomaly.previous_count} -&gt;{" "}
              {catalog.anomaly.new_count} (<bdi>{catalog.anomaly.status}</bdi>)
            </p>
          )}

          {catalog.model_groups && (
            <div data-testid="catalog-model-groups">
              <p data-testid="model-groups-state">
                {phrase("catalogModelGroupsPrefix", locale)} <bdi>{catalog.model_groups.state}</bdi>
              </p>
              <ul>
                {Object.entries(catalog.model_groups.entries).map(([groupId, groupFields]) => (
                  <li key={groupId} data-testid={`model-group-${groupId}`}>
                    <bdi>
                      {groupId}: {JSON.stringify(groupFields)}
                    </bdi>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <label>
            {phrase("catalogProbeKindLabel", locale)}
            <select
              data-testid="probe-kind"
              value={probeKind}
              onChange={(event) => setProbeKind(event.target.value as ProbeKind)}
            >
              <option value="chat">{phrase("probeKindChat", locale)}</option>
              <option value="streaming">{phrase("probeKindStreaming", locale)}</option>
              <option value="tool_call">{phrase("probeKindToolCall", locale)}</option>
              <option value="structured_output">
                {phrase("probeKindStructuredOutput", locale)}
              </option>
            </select>
          </label>
          {probeError && (
            <p role="alert" data-testid="probe-error" dir={dirFor(locale)}>
              <bdi>{probeError}</bdi>
            </p>
          )}

          <ul data-testid="catalog-entry-list">
            {catalog.entries.map((entry) => {
              const result = probeResults[entry.id];
              return (
                <li key={entry.id} data-testid={`catalog-entry-${entry.id}`} tabIndex={-1}>
                  <span data-testid={`entry-status-${entry.id}`}>
                    <bdi>{entry.status}</bdi>
                  </span>
                  <span data-testid={`entry-metadata-status-${entry.id}`}>
                    <bdi>{entry.metadata_status}</bdi>
                  </span>
                  <span data-testid={`entry-fields-${entry.id}`}>
                    <bdi>{JSON.stringify(entry.fields)}</bdi>
                  </span>
                  <button
                    type="button"
                    data-testid={`probe-button-${entry.id}`}
                    disabled={
                      entry.status !== "callable" ||
                      catalog.connection_status === "revoked" ||
                      catalog.access_state === "revoked" ||
                      !selectedProjectId ||
                      probingModelId !== null ||
                      confirmProbeModelId !== null
                    }
                    onClick={() => openProbeConfirm(entry.id)}
                  >
                    {phrase("confirmRunProbe", locale)}
                  </button>
                  {confirmProbeModelId === entry.id && (
                    <div
                      role="alertdialog"
                      aria-labelledby={`confirm-probe-title-${entry.id}`}
                      aria-describedby={`confirm-probe-text-${entry.id}`}
                      data-testid={`confirm-probe-${entry.id}`}
                      lang={resolveLocale(locale)}
                      dir={dirFor(locale)}
                      onKeyDown={(event) => {
                        if (event.key === "Escape") cancelProbeConfirm(entry.id);
                      }}
                    >
                      <h2
                        id={`confirm-probe-title-${entry.id}`}
                        data-testid={`confirm-probe-title-${entry.id}`}
                      >
                        {phrase("runProbeConfirmTitle", locale)}
                      </h2>
                      <p
                        id={`confirm-probe-text-${entry.id}`}
                        data-testid={`confirm-probe-text-${entry.id}`}
                      >
                        {phrase("runProbeConfirmBody", locale)}
                      </p>
                      <button
                        type="button"
                        data-testid={`confirm-probe-button-${entry.id}`}
                        onClick={() => confirmProbe(entry.id)}
                      >
                        {phrase("confirmRunProbe", locale)}
                      </button>
                      <button
                        type="button"
                        data-testid={`cancel-probe-button-${entry.id}`}
                        onClick={() => cancelProbeConfirm(entry.id)}
                      >
                        {phrase("cancel", locale)}
                      </button>
                    </div>
                  )}
                  {result &&
                    (() => {
                      const costText = result.usage
                        ? `${result.usage.total_tokens} ${phrase("tokensSuffix", locale)}`
                        : phrase("probeResultCostUnknown", locale);
                      return (
                        <span data-testid={`probe-result-${entry.id}`}>
                          <bdi>{result.state}</bdi> ({costText})
                          <span data-testid={`probe-type-${entry.id}`}>
                            <bdi>{result.probe_type}</bdi>
                          </span>
                          <span data-testid={`probe-route-identity-${entry.id}`}>
                            <bdi>{result.route_identity_state}</bdi>
                          </span>
                          <span data-testid={`probe-probed-at-${entry.id}`}>
                            <bdi>{result.probed_at}</bdi>
                          </span>
                        </span>
                      );
                    })()}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
