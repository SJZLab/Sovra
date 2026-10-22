/**
 * Model-to-artifact mapping UI (FTF-0308, Section 2.2/3.3/6.6/39/68/77/81).
 *
 * Consumes only the frozen FTF-0308-P1 routes in
 * `packages/contracts/openapi/03-interface.yaml`:
 *
 *   GET  .../connections/litellm/{id}/models/{name}/training-artifact
 *   POST .../connections/litellm/{id}/models/{name}/training-artifact
 *   GET  .../connections/litellm/{id}/models/{name}/training-artifact/changes
 *   GET  /api/v1/organizations/{org}/artifacts          (artifact picker)
 *
 * Acceptance ("UI maps explicit accessible alias to verified trainable
 * artifact and displays provenance/limits"):
 *
 * - The user names the artifact. The picker starts empty, is never ordered,
 *   filtered or pre-selected by the alias text, and a mapping is only sent
 *   by an explicit click on "Map artifact".
 * - Only `ready` artifacts are selectable (the select button is disabled
 *   otherwise; the route's `artifact_not_ready` 409 is the real enforcement).
 * - Provenance and limits are rendered from the server's own artifact
 *   record: source, pinned revision, weights digest, license acceptance,
 *   trust_remote_code, required packages and minimum GPU profile. A missing
 *   field shows "not recorded", never a guessed value.
 *
 * Negative acceptance ("Changing mapping invalidates related qualification;
 * no guessed weight identity from LiteLLM alias"):
 *
 * - Every write carries `expected_catalog_version` read from the last GET,
 *   so a stale view is the route's `catalog_version_conflict` 409, not a
 *   silent overwrite. Changing or removing an existing mapping needs a
 *   second, explicit confirmation that names the invalidation.
 * - After `changed: true` the UI reports the version move and that evidence
 *   recorded against the previous catalog version no longer applies (the
 *   store's `require_current_mapping` is what enforces that server side).
 * - No weight identity is derived from the alias anywhere in this file.
 * - Provider outage (`gateway_unavailable`, 503) and every other failure
 *   are shown as errors and never as a successful mapping (Law 17).
 *
 * Authorization is the route's role gate (403 is surfaced as-is); a UI
 * control never authorizes the effect.
 *
 * Scope disclosed, not hidden (same precedent as `model_catalog_ui.tsx`):
 * this repository has no Next.js `app/` page tree yet (FTF-H029), so this
 * component is DOM-tested with Vitest + React Testing Library + jsdom and a
 * mocked `fetch`; it is UNWIRED to a page until that mount exists.
 *
 * FTF-UX05 (2026-10-22): audit finding (H1/H7, severity 2) -- the mapping,
 * the available artifacts and the change history are three independently
 * triggered loads with no indication up front that three separate actions
 * are needed to see the full picture. A visible notice now says so; it is
 * purely informational, changes no existing control, and the certified
 * three-button load flow is unchanged. An optional `locale` prop renders
 * the new notice (and the existing confirm text's own `lang`/`dir`) in
 * Arabic (finding X4, partial: same precedent as UX02/UX03 round 1 -- only
 * the copy this round adds or touches is bilingual).
 */

import { useState } from "react";

import { dirFor, phrase, resolveLocale, type Locale } from "../ui/phrases";

export interface MappedArtifact {
  id: string;
  display_name: string;
  source_type: string;
  source_uri: string;
  model_family: string;
  base_model_revision: string;
  weights_digest?: string | null;
  license_id: string;
  license_accepted_by?: string | null;
  license_accepted_at?: string | null;
  trust_remote_code_allowed?: boolean;
  required_python_packages?: string[];
  minimum_gpu_profile?: Record<string, unknown>;
  status: "registered" | "ready" | "archived";
}

export interface TrainingArtifactMapping {
  mapping_state: "unmapped" | "mapped";
  model_catalog_entry_id: string | null;
  litellm_connection_id: string;
  public_model_name: string;
  trainable_artifact_id: string | null;
  catalog_version: number;
  last_verified_accessible_at: string | null;
  last_changed_at: string | null;
  artifact: MappedArtifact | null;
}

export interface MappingResult extends TrainingArtifactMapping {
  changed: boolean;
}

export interface MappingChange {
  id: string;
  previous_trainable_artifact_id: string | null;
  new_trainable_artifact_id: string | null;
  previous_catalog_version: number;
  new_catalog_version: number;
  actor: string;
  request_id: string;
  created_at: string;
}

export interface ModelToArtifactMappingUIProps {
  apiBaseUrl: string;
  organizationId: string;
  connectionId: string;
  modelName: string;
  locale?: Locale;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

/** Encode each path segment but keep '/', the route's model_name is a path. */
function encodeModelName(modelName: string): string {
  return modelName.split("/").map(encodeURIComponent).join("/");
}

export function mappingUrl(
  apiBaseUrl: string,
  organizationId: string,
  connectionId: string,
  modelName: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/connections/litellm/${connectionId}/models/${encodeModelName(modelName)}/training-artifact`
  );
}

export function mappingChangesUrl(
  apiBaseUrl: string,
  organizationId: string,
  connectionId: string,
  modelName: string,
): string {
  return `${mappingUrl(apiBaseUrl, organizationId, connectionId, modelName)}/changes`;
}

export function artifactsUrl(apiBaseUrl: string, organizationId: string): string {
  return `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}/artifacts`;
}

const ERROR_MESSAGES: Record<string, string> = {
  not_found: "The connection or artifact was not found.",
  connection_revoked: "The gateway connection is revoked; nothing was changed.",
  model_not_accessible:
    "The gateway does not currently list this model alias; nothing was changed.",
  artifact_not_ready: "The artifact is not ready; nothing was changed.",
  catalog_version_conflict:
    "The mapping changed since it was loaded; reload it and review before retrying.",
  gateway_unavailable:
    "The gateway could not be reached, so the alias could not be verified; nothing was changed.",
  invalid_model_name: "The model name is not valid.",
};

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      return ERROR_MESSAGES[body.detail] ?? body.detail;
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

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "not recorded";
  return String(value);
}

function isMapping(body: unknown): body is TrainingArtifactMapping {
  if (!body || typeof body !== "object") return false;
  const candidate = body as Partial<TrainingArtifactMapping>;
  return (
    (candidate.mapping_state === "mapped" || candidate.mapping_state === "unmapped") &&
    typeof candidate.catalog_version === "number"
  );
}

export function ModelToArtifactMappingUI({
  apiBaseUrl,
  organizationId,
  connectionId,
  modelName,
  locale = "en",
}: ModelToArtifactMappingUIProps) {
  const [mapping, setMapping] = useState<TrainingArtifactMapping | null>(null);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [loadingMapping, setLoadingMapping] = useState(false);

  const [artifacts, setArtifacts] = useState<MappedArtifact[]>([]);
  const [artifactsError, setArtifactsError] = useState<string | null>(null);
  // Deliberately starts null: never pre-selected, never derived from the alias.
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);

  const [pending, setPending] = useState<"map" | "unmap" | null>(null);
  const [writing, setWriting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [changes, setChanges] = useState<MappingChange[] | null>(null);
  const [changesError, setChangesError] = useState<string | null>(null);

  async function handleLoadMapping() {
    setLoadingMapping(true);
    setMappingError(null);
    try {
      const response = await fetch(
        mappingUrl(apiBaseUrl, organizationId, connectionId, modelName),
        {
          method: "GET",
          credentials: "include",
        },
      );
      if (!response.ok) {
        setMappingError(await readErrorDetail(response));
        return;
      }
      const body: unknown = await response.json();
      if (!isMapping(body)) {
        setMappingError("malformed mapping response");
        return;
      }
      setMapping(body);
      setPending(null);
    } catch (caught) {
      setMappingError(networkErrorMessage(caught));
    } finally {
      setLoadingMapping(false);
    }
  }

  async function handleLoadArtifacts() {
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
      const body: unknown = await response.json();
      if (!Array.isArray(body)) {
        setArtifactsError("malformed artifact listing response");
        return;
      }
      setArtifacts(body as MappedArtifact[]);
    } catch (caught) {
      setArtifactsError(networkErrorMessage(caught));
    }
  }

  async function handleLoadChanges() {
    setChangesError(null);
    try {
      const response = await fetch(
        mappingChangesUrl(apiBaseUrl, organizationId, connectionId, modelName),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        setChangesError(await readErrorDetail(response));
        return;
      }
      const body = await response.json();
      if (!body || !Array.isArray(body.changes)) {
        setChangesError("malformed change history response");
        return;
      }
      setChanges(body.changes as MappingChange[]);
    } catch (caught) {
      setChangesError(networkErrorMessage(caught));
    }
  }

  async function submit(artifactId: string | null) {
    if (!mapping) {
      setWriteError("load the current mapping first");
      return;
    }
    setWriting(true);
    setWriteError(null);
    setNotice(null);
    try {
      const response = await fetch(
        mappingUrl(apiBaseUrl, organizationId, connectionId, modelName),
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            trainable_artifact_id: artifactId,
            expected_catalog_version: mapping.catalog_version,
          }),
        },
      );
      if (!response.ok) {
        // The view is left exactly as it was: a failed write changes nothing.
        setWriteError(await readErrorDetail(response));
        return;
      }
      const body: unknown = await response.json();
      if (!isMapping(body) || typeof (body as MappingResult).changed !== "boolean") {
        setWriteError("malformed mapping response");
        return;
      }
      const result = body as MappingResult;
      const previousVersion = mapping.catalog_version;
      setMapping(result);
      setPending(null);
      setSelectedArtifactId(null);
      setNotice(
        result.changed
          ? `Mapping changed: catalog version ${previousVersion} -> ${result.catalog_version}. ` +
              `Qualification evidence recorded against version ${previousVersion} no longer applies.`
          : "No change: this alias already has that mapping.",
      );
    } catch (caught) {
      // Nothing is shown as mapped unless the route genuinely answered.
      setWriteError(networkErrorMessage(caught));
    } finally {
      setWriting(false);
    }
  }

  const selectedArtifact = artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? null;
  const isMapped = mapping?.mapping_state === "mapped";
  const canMap =
    mapping !== null &&
    !writing &&
    selectedArtifact !== null &&
    selectedArtifact.id !== mapping.trainable_artifact_id;

  function requestMap() {
    if (isMapped) {
      setPending("map");
      return;
    }
    if (selectedArtifactId) void submit(selectedArtifactId);
  }

  return (
    <div
      data-testid="model-to-artifact-mapping-ui"
      aria-label={`Training artifact for ${modelName}`}
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
    >
      <h2 data-testid="mapping-model-name">{modelName}</h2>
      <p data-testid="mapping-three-steps-notice">{phrase("mappingThreeStepsNotice", locale)}</p>

      <section data-testid="mapping-current">
        <button
          type="button"
          data-testid="load-mapping-button"
          disabled={loadingMapping}
          onClick={handleLoadMapping}
        >
          Load mapping
        </button>
        {mappingError && (
          <p role="alert" data-testid="mapping-error">
            {mappingError}
          </p>
        )}
        {mapping && (
          <div data-testid="mapping-state">
            <p data-testid="mapping-state-label">
              {mapping.mapping_state === "mapped"
                ? "Mapped to a trainable artifact"
                : "Not mapped: this alias has no explicit training artifact"}
            </p>
            <p data-testid="mapping-catalog-version">Catalog version: {mapping.catalog_version}</p>
            <p data-testid="mapping-last-verified">
              Last verified accessible: {shown(mapping.last_verified_accessible_at)}
            </p>
            <p data-testid="mapping-last-changed">Last changed: {shown(mapping.last_changed_at)}</p>
            {mapping.artifact && <ArtifactDetails artifact={mapping.artifact} prefix="mapped" />}
          </div>
        )}
      </section>

      <section data-testid="mapping-picker">
        <button type="button" data-testid="load-artifacts-button" onClick={handleLoadArtifacts}>
          Load artifacts
        </button>
        {artifactsError && (
          <p role="alert" data-testid="artifacts-error">
            {artifactsError}
          </p>
        )}
        <ul data-testid="artifact-list">
          {artifacts.map((artifact) => (
            <li key={artifact.id} data-testid={`artifact-row-${artifact.id}`}>
              <span>{artifact.display_name}</span> <span>({artifact.status})</span>
              <button
                type="button"
                data-testid={`artifact-select-button-${artifact.id}`}
                aria-pressed={selectedArtifactId === artifact.id}
                disabled={artifact.status !== "ready"}
                onClick={() => setSelectedArtifactId(artifact.id)}
              >
                {selectedArtifactId === artifact.id ? "Selected" : "Select"}
              </button>
              {artifact.status !== "ready" && (
                <span data-testid={`artifact-not-ready-${artifact.id}`}>
                  not ready: cannot be mapped
                </span>
              )}
            </li>
          ))}
        </ul>
        {!selectedArtifactId && <p data-testid="no-artifact-selected">No artifact selected yet.</p>}
        {selectedArtifact && <ArtifactDetails artifact={selectedArtifact} prefix="selected" />}

        <button type="button" data-testid="map-button" disabled={!canMap} onClick={requestMap}>
          Map artifact
        </button>
        <button
          type="button"
          data-testid="unmap-button"
          disabled={!isMapped || writing}
          onClick={() => setPending("unmap")}
        >
          Remove mapping
        </button>

        {pending && mapping && (
          <div role="alertdialog" data-testid="confirm-change">
            <p data-testid="confirm-change-text">
              {pending === "unmap" ? "Removing this mapping" : "Changing this mapping"} invalidates
              related qualification: evidence recorded against catalog version{" "}
              {mapping.catalog_version} will no longer apply.
            </p>
            <button
              type="button"
              data-testid="confirm-change-button"
              disabled={writing}
              onClick={() => void submit(pending === "unmap" ? null : selectedArtifactId)}
            >
              Confirm change
            </button>
            <button
              type="button"
              data-testid="cancel-change-button"
              onClick={() => setPending(null)}
            >
              Cancel
            </button>
          </div>
        )}

        {writeError && (
          <p role="alert" data-testid="write-error">
            {writeError}
          </p>
        )}
        {notice && (
          <p role="status" data-testid="mapping-notice">
            {notice}
          </p>
        )}
      </section>

      <section data-testid="mapping-history">
        <button type="button" data-testid="load-changes-button" onClick={handleLoadChanges}>
          Load change history
        </button>
        {changesError && (
          <p role="alert" data-testid="changes-error">
            {changesError}
          </p>
        )}
        {changes && changes.length === 0 && <p data-testid="changes-empty">No changes recorded.</p>}
        {changes && changes.length > 0 && (
          <ul data-testid="changes-list">
            {changes.map((change) => (
              <li key={change.id} data-testid={`change-row-${change.id}`}>
                v{change.previous_catalog_version} -&gt; v{change.new_catalog_version}:{" "}
                {shown(change.previous_trainable_artifact_id)} -&gt;{" "}
                {shown(change.new_trainable_artifact_id)} by {change.actor} at {change.created_at}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ArtifactDetails({ artifact, prefix }: { artifact: MappedArtifact; prefix: string }) {
  const packages = artifact.required_python_packages ?? [];
  return (
    <dl data-testid={`${prefix}-artifact-details`}>
      <dt>Artifact</dt>
      <dd data-testid={`${prefix}-artifact-name`}>{artifact.display_name}</dd>
      <dt>Source</dt>
      <dd data-testid={`${prefix}-artifact-source`}>
        {artifact.source_type}: {artifact.source_uri}
      </dd>
      <dt>Model family</dt>
      <dd data-testid={`${prefix}-artifact-family`}>{shown(artifact.model_family)}</dd>
      <dt>Pinned revision</dt>
      <dd data-testid={`${prefix}-artifact-revision`}>{shown(artifact.base_model_revision)}</dd>
      <dt>Weights digest</dt>
      <dd data-testid={`${prefix}-artifact-digest`}>{shown(artifact.weights_digest)}</dd>
      <dt>License</dt>
      <dd data-testid={`${prefix}-artifact-license`}>
        {shown(artifact.license_id)}; accepted by {shown(artifact.license_accepted_by)} at{" "}
        {shown(artifact.license_accepted_at)}
      </dd>
      <dt>Trust remote code</dt>
      <dd data-testid={`${prefix}-artifact-trust-remote-code`}>
        {artifact.trust_remote_code_allowed === undefined
          ? "not recorded"
          : artifact.trust_remote_code_allowed
            ? "allowed"
            : "not allowed"}
      </dd>
      <dt>Required packages</dt>
      <dd data-testid={`${prefix}-artifact-packages`}>
        {packages.length > 0 ? packages.join(", ") : "none recorded"}
      </dd>
      <dt>Minimum GPU profile</dt>
      <dd data-testid={`${prefix}-artifact-gpu`}>
        {artifact.minimum_gpu_profile && Object.keys(artifact.minimum_gpu_profile).length > 0
          ? JSON.stringify(artifact.minimum_gpu_profile)
          : "not recorded"}
      </dd>
      <dt>Status</dt>
      <dd data-testid={`${prefix}-artifact-status`}>{artifact.status}</dd>
    </dl>
  );
}
