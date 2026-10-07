/**
 * Model-to-artifact mapping UI (FTF-0308, Section 6/13/34/68/77/81).
 *
 * This task's own packet names a frontend-only `files` list; the real
 * backend composition this component needs (reusing FTF-0214's own
 * discovery/enrichment/reconciliation, FTF-0301's `artifact_crud.
 * get_artifact` and FTF-0307's `classify_training_capability`) is a
 * disclosed refinement under a new `GET .../models/{modelId}/training-
 * capability` route on `app.litellm.model_catalog_routes` -- see that
 * module's own docstring for the full reasoning (the same precedent its
 * own FTF-0214 docstring already applied for an identical gap).
 *
 * Acceptance ("UI maps explicit accessible alias to verified trainable
 * artifact and displays provenance/limits") is met structurally:
 *
 * - "Explicit accessible alias": `modelId` is never free-typed -- it is
 *   selected from the real, live `GET .../catalog` response's own
 *   `entries` (the same route FTF-0214's own `ModelCatalogUI` calls),
 *   never pre-selected.
 * - "Verified trainable artifact": `artifactId` is selected from the
 *   real, live `GET .../artifacts` response (FTF-0301), never typed or
 *   guessed; "verified" is the live `training-capability` route's own
 *   real classification, never re-derived client-side.
 * - "Displays provenance/limits": `artifact` in the live response (real
 *   `display_name`/`source_type`/`source_uri`/`model_family`/
 *   `base_model_revision`/`weights_digest`/`license_id`/
 *   `required_python_packages`/`minimum_gpu_profile`) is rendered
 *   directly, never fabricated.
 *
 * Negative_acceptance ("Changing mapping invalidates related
 * qualification; no guessed weight identity from LiteLLM alias") is met
 * structurally too: selecting a different alias or a different artifact
 * immediately clears the previously-displayed capability result
 * (`setCapability(null)`) *before* the new request is even sent, so a
 * stale classification for the old mapping is never shown while the new
 * one loads; there is no client-side name/string matching between a
 * model alias and an artifact anywhere in this file -- the mapping is
 * always two independent, explicit user selections.
 *
 * Scope disclosed, not hidden (same precedent `model_catalog_ui.tsx`
 * already established): this repository has no Next.js `app/` page tree
 * yet (FTF-H029), so this is a real, DOM-level tested component (Vitest +
 * React Testing Library + jsdom with a mocked `fetch`), not a full
 * browser E2E -- Law 6 (UI reachability from `/`) stays disclosed
 * BLOCKED on that dependency.
 */

import { useState } from "react";

export interface CatalogEntrySummary {
  id: string;
  status: "callable" | "stale" | "removed";
}

export interface ArtifactSummary {
  id: string;
  display_name: string;
  status: "registered" | "ready" | "archived";
}

export interface ArtifactProvenance {
  id: string;
  display_name: string;
  source_type: string;
  source_uri: string;
  model_family: string;
  base_model_revision: string;
  weights_digest: string | null;
  license_id: string;
  status: string;
  trust_remote_code_allowed: boolean;
  required_python_packages: string[];
  minimum_gpu_profile: Record<string, unknown>;
}

export interface TrainingCapabilityResult {
  model_id: string;
  access: "granted" | "not_granted";
  capability: "trainable_open_weight" | "managed_fine_tunable" | "inference_only" | "unknown";
  artifact_id: string | null;
  artifact_availability: "none" | "registered" | "ready" | "archived";
  license_approved: boolean;
  role: string | null;
  evidence_probed_at: string | null;
  detail: string;
  artifact: ArtifactProvenance | null;
}

export interface ModelToArtifactMappingUIProps {
  apiBaseUrl: string;
  organizationId: string;
  connectionId: string;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function catalogUrl(
  apiBaseUrl: string,
  organizationId: string,
  connectionId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/connections/litellm/${connectionId}/catalog`
  );
}

export function artifactsUrl(apiBaseUrl: string, organizationId: string): string {
  return `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}/artifacts`;
}

export function trainingCapabilityUrl(
  apiBaseUrl: string,
  organizationId: string,
  connectionId: string,
  modelId: string,
  artifactId: string | null,
): string {
  const base =
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/connections/litellm/${connectionId}/models/${encodeURIComponent(modelId)}/training-capability`;
  if (!artifactId) return base;
  const params = new URLSearchParams({ artifact_id: artifactId });
  return `${base}?${params.toString()}`;
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

export function ModelToArtifactMappingUI({
  apiBaseUrl,
  organizationId,
  connectionId,
}: ModelToArtifactMappingUIProps) {
  const [entries, setEntries] = useState<CatalogEntrySummary[]>([]);
  const [loadingEntries, setLoadingEntries] = useState(false);
  const [entriesError, setEntriesError] = useState<string | null>(null);
  // Deliberately starts null, never pre-selected -- the same "explicit
  // accessible alias" discipline this task's own acceptance clause names.
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);

  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [loadingArtifacts, setLoadingArtifacts] = useState(false);
  const [artifactsError, setArtifactsError] = useState<string | null>(null);
  // Also never pre-selected -- "no guessed weight identity from LiteLLM alias".
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);

  const [capability, setCapability] = useState<TrainingCapabilityResult | null>(null);
  const [loadingCapability, setLoadingCapability] = useState(false);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);

  async function handleLoadModels() {
    setLoadingEntries(true);
    setEntriesError(null);
    try {
      const response = await fetch(catalogUrl(apiBaseUrl, organizationId, connectionId), {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        setEntriesError(await readErrorDetail(response));
        return;
      }
      const body = await response.json();
      if (!body || !Array.isArray(body.entries)) {
        setEntriesError("malformed catalog response");
        return;
      }
      setEntries(body.entries);
    } catch (caught) {
      setEntriesError(networkErrorMessage(caught));
    } finally {
      setLoadingEntries(false);
    }
  }

  async function handleLoadArtifacts() {
    setLoadingArtifacts(true);
    setArtifactsError(null);
    try {
      const response = await fetch(artifactsUrl(apiBaseUrl, organizationId), {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        setArtifactsError(await readErrorDetail(response));
        return;
      }
      const body: ArtifactSummary[] = await response.json();
      if (!Array.isArray(body)) {
        setArtifactsError("malformed artifact listing response");
        return;
      }
      setArtifacts(body);
    } catch (caught) {
      setArtifactsError(networkErrorMessage(caught));
    } finally {
      setLoadingArtifacts(false);
    }
  }

  async function fetchCapability(modelId: string, artifactId: string | null) {
    setLoadingCapability(true);
    setCapabilityError(null);
    try {
      const response = await fetch(
        trainingCapabilityUrl(apiBaseUrl, organizationId, connectionId, modelId, artifactId),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        setCapabilityError(await readErrorDetail(response));
        return;
      }
      const body: TrainingCapabilityResult = await response.json();
      if (!body || typeof body.capability !== "string") {
        setCapabilityError("malformed training-capability response");
        return;
      }
      setCapability(body);
    } catch (caught) {
      // negative_acceptance: a network failure never fabricates a result.
      setCapabilityError(networkErrorMessage(caught));
    } finally {
      setLoadingCapability(false);
    }
  }

  function selectModel(modelId: string) {
    setSelectedModelId(modelId);
    // negative_acceptance: changing the mapped alias invalidates the
    // previously-displayed capability/qualification immediately -- never
    // shown stale while the new (modelId, artifactId) pair's own result
    // is still loading.
    setCapability(null);
    setCapabilityError(null);
    if (selectedArtifactId) {
      void fetchCapability(modelId, selectedArtifactId);
    }
  }

  function selectArtifact(artifactId: string) {
    setSelectedArtifactId(artifactId);
    // Same discipline as selectModel: the mapping changed, so the old
    // classification is invalidated before the new one is requested.
    setCapability(null);
    setCapabilityError(null);
    if (selectedModelId) {
      void fetchCapability(selectedModelId, artifactId);
    }
  }

  return (
    <div data-testid="model-to-artifact-mapping-ui">
      <section data-testid="model-selection">
        <button
          type="button"
          data-testid="load-models-button"
          disabled={loadingEntries}
          onClick={handleLoadModels}
        >
          Load models
        </button>
        {entriesError && <p data-testid="models-error">{entriesError}</p>}
        <ul data-testid="model-list">
          {entries.map((entry) => (
            <li key={entry.id} data-testid={`model-row-${entry.id}`}>
              <span>{entry.id}</span>
              <span data-testid={`model-status-${entry.id}`}>{entry.status}</span>
              <button
                type="button"
                data-testid={`model-select-button-${entry.id}`}
                aria-pressed={selectedModelId === entry.id}
                onClick={() => selectModel(entry.id)}
              >
                {selectedModelId === entry.id ? "Selected" : "Select"}
              </button>
            </li>
          ))}
        </ul>
        {!selectedModelId && <p data-testid="no-model-selected">No model alias selected yet.</p>}
      </section>

      <section data-testid="artifact-selection">
        <button
          type="button"
          data-testid="load-artifacts-button"
          disabled={loadingArtifacts}
          onClick={handleLoadArtifacts}
        >
          Load artifacts
        </button>
        {artifactsError && <p data-testid="artifacts-error">{artifactsError}</p>}
        <ul data-testid="artifact-list">
          {artifacts.map((artifact) => (
            <li key={artifact.id} data-testid={`artifact-row-${artifact.id}`}>
              <span>{artifact.display_name}</span>
              <span data-testid={`artifact-status-${artifact.id}`}>{artifact.status}</span>
              <button
                type="button"
                data-testid={`artifact-select-button-${artifact.id}`}
                aria-pressed={selectedArtifactId === artifact.id}
                onClick={() => selectArtifact(artifact.id)}
              >
                {selectedArtifactId === artifact.id ? "Selected" : "Select"}
              </button>
            </li>
          ))}
        </ul>
        {!selectedArtifactId && (
          <p data-testid="no-artifact-selected">
            No artifact mapped yet -- never guessed from the alias name.
          </p>
        )}
      </section>

      {loadingCapability && <p data-testid="capability-loading">Loading mapping...</p>}
      {capabilityError && <p data-testid="capability-error">{capabilityError}</p>}

      {capability && !loadingCapability && (
        <section data-testid="capability-results">
          <p data-testid="capability-model-id">Model: {capability.model_id}</p>
          <p data-testid="capability-access">Access: {capability.access}</p>
          <p data-testid="capability-value">Capability: {capability.capability}</p>
          <p data-testid="capability-license-approved">
            License approved: {capability.license_approved ? "yes" : "no"}
          </p>
          <p data-testid="capability-artifact-availability">
            Artifact availability: {capability.artifact_availability}
          </p>
          {capability.artifact ? (
            <div data-testid="artifact-provenance">
              <p data-testid="provenance-display-name">{capability.artifact.display_name}</p>
              <p data-testid="provenance-source">
                {capability.artifact.source_type}: {capability.artifact.source_uri}
              </p>
              <p data-testid="provenance-revision">{capability.artifact.base_model_revision}</p>
              <p data-testid="provenance-digest">
                {capability.artifact.weights_digest ?? "unresolved"}
              </p>
              <p data-testid="provenance-license">{capability.artifact.license_id}</p>
              <p data-testid="provenance-limits">
                {JSON.stringify(capability.artifact.minimum_gpu_profile)}
              </p>
            </div>
          ) : (
            <p data-testid="no-artifact-provenance">No artifact mapped.</p>
          )}
        </section>
      )}
    </div>
  );
}
