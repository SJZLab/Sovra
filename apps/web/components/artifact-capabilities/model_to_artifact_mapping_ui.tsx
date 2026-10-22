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
 * Arabic (finding X4).
 *
 * FTF-UX05 round 2 (2026-10-22, L2 return): the L2 review found the container's `lang`/`dir`
 * covered large amounts of hardcoded-English UI copy -- every button, every status line, every
 * `dt` label in `ArtifactDetails`, every authored error message -- that stayed English regardless
 * of `locale`. Every string this component itself authors now routes through `phrase()` with a
 * real `ar` translation (see `ui/phrases.ts`'s FTF-0308 block), so `lang="ar"` is now honest for
 * that copy. Values this component does not author -- the alias/model name, artifact names, source
 * URIs, digests, actor ids, timestamps, the server's own enum literals (`status`, `availability`)
 * -- are data, not UI copy, and are wrapped in `<bdi>` rather than given a language tag (same
 * convention `candidate_selection_ui_no_auto_selection.tsx` already established). A `detail` string
 * surfaced raw from the live backend for an unrecognized error code is the same kind of data.
 * Default `locale="en"` rendering is byte-identical to round 1.
 */

import { useState } from "react";

import { dirFor, phrase, PHRASES, resolveLocale, type Locale } from "../ui/phrases";

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

const ERROR_PHRASE_KEYS = {
  not_found: "mappingErrorNotFound",
  connection_revoked: "mappingErrorConnectionRevoked",
  model_not_accessible: "mappingErrorModelNotAccessible",
  artifact_not_ready: "mappingErrorArtifactNotReady",
  catalog_version_conflict: "mappingErrorCatalogVersionConflict",
  gateway_unavailable: "mappingErrorGatewayUnavailable",
  invalid_model_name: "mappingErrorInvalidModelName",
} as const satisfies Record<string, keyof typeof PHRASES>;

async function readErrorDetail(response: Response, locale: Locale): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      const key = ERROR_PHRASE_KEYS[body.detail as keyof typeof ERROR_PHRASE_KEYS];
      // An unrecognized code is raw backend data (its language is the backend's choice, not this
      // component's) and is returned verbatim, never mistranslated.
      return key ? phrase(key, locale) : body.detail;
    }
  } catch {
    // fall through to the generic message below
  }
  return phrase("requestFailedTemplate", locale, { status: response.status });
}

function networkErrorMessage(caught: unknown, locale: Locale): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `${phrase("networkErrorPrefix", locale)} ${reason}`;
}

function shown(value: unknown, locale: Locale): string {
  if (value === null || value === undefined || value === "") {
    return phrase("notRecordedText", locale);
  }
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
        setMappingError(await readErrorDetail(response, locale));
        return;
      }
      const body: unknown = await response.json();
      if (!isMapping(body)) {
        setMappingError(phrase("mappingMalformedMapping", locale));
        return;
      }
      setMapping(body);
      setPending(null);
    } catch (caught) {
      setMappingError(networkErrorMessage(caught, locale));
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
        setArtifactsError(await readErrorDetail(response, locale));
        return;
      }
      const body: unknown = await response.json();
      if (!Array.isArray(body)) {
        setArtifactsError(phrase("mappingMalformedArtifactListing", locale));
        return;
      }
      setArtifacts(body as MappedArtifact[]);
    } catch (caught) {
      setArtifactsError(networkErrorMessage(caught, locale));
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
        setChangesError(await readErrorDetail(response, locale));
        return;
      }
      const body = await response.json();
      if (!body || !Array.isArray(body.changes)) {
        setChangesError(phrase("mappingMalformedChangeHistory", locale));
        return;
      }
      setChanges(body.changes as MappingChange[]);
    } catch (caught) {
      setChangesError(networkErrorMessage(caught, locale));
    }
  }

  async function submit(artifactId: string | null) {
    if (!mapping) {
      setWriteError(phrase("mappingLoadFirst", locale));
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
        setWriteError(await readErrorDetail(response, locale));
        return;
      }
      const body: unknown = await response.json();
      if (!isMapping(body) || typeof (body as MappingResult).changed !== "boolean") {
        setWriteError(phrase("mappingMalformedMapping", locale));
        return;
      }
      const result = body as MappingResult;
      const previousVersion = mapping.catalog_version;
      setMapping(result);
      setPending(null);
      setSelectedArtifactId(null);
      setNotice(
        result.changed
          ? `${phrase("mappingNoticeChangedPrefix", locale)} ${previousVersion} -> ${result.catalog_version}. ` +
              phrase("mappingNoticeChangedSuffix", locale, { prev: previousVersion })
          : phrase("mappingNoticeNoChange", locale),
      );
    } catch (caught) {
      // Nothing is shown as mapped unless the route genuinely answered.
      setWriteError(networkErrorMessage(caught, locale));
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
      aria-label={phrase("mappingAriaLabel", locale, { model: modelName })}
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
    >
      <h2 data-testid="mapping-model-name">
        <bdi>{modelName}</bdi>
      </h2>
      <p data-testid="mapping-three-steps-notice">{phrase("mappingThreeStepsNotice", locale)}</p>

      <section data-testid="mapping-current">
        <button
          type="button"
          data-testid="load-mapping-button"
          disabled={loadingMapping}
          onClick={handleLoadMapping}
        >
          {phrase("mappingLoadMapping", locale)}
        </button>
        {mappingError && (
          <p role="alert" data-testid="mapping-error">
            <bdi>{mappingError}</bdi>
          </p>
        )}
        {mapping && (
          <div data-testid="mapping-state">
            <p data-testid="mapping-state-label">
              {mapping.mapping_state === "mapped"
                ? phrase("mappingStateMapped", locale)
                : phrase("mappingStateUnmapped", locale)}
            </p>
            <p data-testid="mapping-catalog-version">
              {phrase("mappingCatalogVersionPrefix", locale)} {mapping.catalog_version}
            </p>
            <p data-testid="mapping-last-verified">
              {phrase("mappingLastVerifiedPrefix", locale)}{" "}
              <bdi>{shown(mapping.last_verified_accessible_at, locale)}</bdi>
            </p>
            <p data-testid="mapping-last-changed">
              {phrase("mappingLastChangedPrefix", locale)}{" "}
              <bdi>{shown(mapping.last_changed_at, locale)}</bdi>
            </p>
            {mapping.artifact && (
              <ArtifactDetails artifact={mapping.artifact} prefix="mapped" locale={locale} />
            )}
          </div>
        )}
      </section>

      <section data-testid="mapping-picker">
        <button type="button" data-testid="load-artifacts-button" onClick={handleLoadArtifacts}>
          {phrase("mappingLoadArtifacts", locale)}
        </button>
        {artifactsError && (
          <p role="alert" data-testid="artifacts-error">
            <bdi>{artifactsError}</bdi>
          </p>
        )}
        <ul data-testid="artifact-list">
          {artifacts.map((artifact) => (
            <li key={artifact.id} data-testid={`artifact-row-${artifact.id}`}>
              <span>
                <bdi>{artifact.display_name}</bdi>
              </span>{" "}
              <span>
                (<bdi>{artifact.status}</bdi>)
              </span>
              <button
                type="button"
                data-testid={`artifact-select-button-${artifact.id}`}
                aria-pressed={selectedArtifactId === artifact.id}
                disabled={artifact.status !== "ready"}
                onClick={() => setSelectedArtifactId(artifact.id)}
              >
                {selectedArtifactId === artifact.id
                  ? phrase("selected", locale)
                  : phrase("select", locale)}
              </button>
              {artifact.status !== "ready" && (
                <span data-testid={`artifact-not-ready-${artifact.id}`}>
                  {phrase("mappingArtifactNotReady", locale)}
                </span>
              )}
            </li>
          ))}
        </ul>
        {!selectedArtifactId && (
          <p data-testid="no-artifact-selected">{phrase("mappingNoArtifactSelected", locale)}</p>
        )}
        {selectedArtifact && (
          <ArtifactDetails artifact={selectedArtifact} prefix="selected" locale={locale} />
        )}

        <button type="button" data-testid="map-button" disabled={!canMap} onClick={requestMap}>
          {phrase("mappingMapButton", locale)}
        </button>
        <button
          type="button"
          data-testid="unmap-button"
          disabled={!isMapped || writing}
          onClick={() => setPending("unmap")}
        >
          {phrase("mappingRemoveButton", locale)}
        </button>

        {pending && mapping && (
          <div role="alertdialog" data-testid="confirm-change">
            <p data-testid="confirm-change-text">
              {pending === "unmap"
                ? phrase("mappingConfirmRemoving", locale)
                : phrase("mappingConfirmChanging", locale)}{" "}
              {phrase("mappingConfirmInvalidatesSuffix", locale, {
                version: mapping.catalog_version,
              })}
            </p>
            <button
              type="button"
              data-testid="confirm-change-button"
              disabled={writing}
              onClick={() => void submit(pending === "unmap" ? null : selectedArtifactId)}
            >
              {phrase("mappingConfirmButton", locale)}
            </button>
            <button
              type="button"
              data-testid="cancel-change-button"
              onClick={() => setPending(null)}
            >
              {phrase("cancel", locale)}
            </button>
          </div>
        )}

        {writeError && (
          <p role="alert" data-testid="write-error">
            <bdi>{writeError}</bdi>
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
          {phrase("mappingLoadChangeHistory", locale)}
        </button>
        {changesError && (
          <p role="alert" data-testid="changes-error">
            <bdi>{changesError}</bdi>
          </p>
        )}
        {changes && changes.length === 0 && (
          <p data-testid="changes-empty">{phrase("mappingNoChangesRecorded", locale)}</p>
        )}
        {changes && changes.length > 0 && (
          <ul data-testid="changes-list">
            {changes.map((change) => (
              <li key={change.id} data-testid={`change-row-${change.id}`}>
                v{change.previous_catalog_version} -&gt; v{change.new_catalog_version}:{" "}
                <bdi>{shown(change.previous_trainable_artifact_id, locale)}</bdi> -&gt;{" "}
                <bdi>{shown(change.new_trainable_artifact_id, locale)}</bdi>{" "}
                {phrase("byPrefix", locale)} <bdi>{change.actor}</bdi> {phrase("atPrefix", locale)}{" "}
                <bdi>{change.created_at}</bdi>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ArtifactDetails({
  artifact,
  prefix,
  locale,
}: {
  artifact: MappedArtifact;
  prefix: string;
  locale: Locale;
}) {
  const packages = artifact.required_python_packages ?? [];
  return (
    <dl data-testid={`${prefix}-artifact-details`}>
      <dt>{phrase("artifactDetailArtifact", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-name`}>
        <bdi>{artifact.display_name}</bdi>
      </dd>
      <dt>{phrase("artifactDetailSource", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-source`}>
        <bdi>
          {artifact.source_type}: {artifact.source_uri}
        </bdi>
      </dd>
      <dt>{phrase("artifactDetailModelFamily", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-family`}>
        <bdi>{shown(artifact.model_family, locale)}</bdi>
      </dd>
      <dt>{phrase("artifactDetailPinnedRevision", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-revision`}>
        <bdi>{shown(artifact.base_model_revision, locale)}</bdi>
      </dd>
      <dt>{phrase("artifactDetailWeightsDigest", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-digest`}>
        <bdi>{shown(artifact.weights_digest, locale)}</bdi>
      </dd>
      <dt>{phrase("artifactDetailLicense", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-license`}>
        <bdi>{shown(artifact.license_id, locale)}</bdi>;{" "}
        {phrase("artifactLicenseAcceptedByPrefix", locale)}{" "}
        <bdi>{shown(artifact.license_accepted_by, locale)}</bdi> {phrase("atPrefix", locale)}{" "}
        <bdi>{shown(artifact.license_accepted_at, locale)}</bdi>
      </dd>
      <dt>{phrase("artifactDetailTrustRemoteCode", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-trust-remote-code`}>
        {artifact.trust_remote_code_allowed === undefined
          ? phrase("notRecordedText", locale)
          : artifact.trust_remote_code_allowed
            ? phrase("allowedText", locale)
            : phrase("notAllowedText", locale)}
      </dd>
      <dt>{phrase("artifactDetailRequiredPackages", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-packages`}>
        {packages.length > 0 ? (
          <bdi>{packages.join(", ")}</bdi>
        ) : (
          phrase("noneRecordedText", locale)
        )}
      </dd>
      <dt>{phrase("artifactDetailMinimumGpuProfile", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-gpu`}>
        {artifact.minimum_gpu_profile && Object.keys(artifact.minimum_gpu_profile).length > 0 ? (
          <bdi>{JSON.stringify(artifact.minimum_gpu_profile)}</bdi>
        ) : (
          phrase("notRecordedText", locale)
        )}
      </dd>
      <dt>{phrase("artifactDetailStatus", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-status`}>
        <bdi>{artifact.status}</bdi>
      </dd>
    </dl>
  );
}
