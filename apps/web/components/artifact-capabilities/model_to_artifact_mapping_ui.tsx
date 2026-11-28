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
 *
 * FTF-UX05 round 2 (2026-10-22, second L2 return, PR #972): fixes four defects an independent
 * review of the merged round-1+round-2 code found by reproducing each directly.
 *
 * - CONFIRM-LOSES-FOCUS (severity 3): the `confirm-change` `role="alertdialog"` never moved focus
 *   anywhere on open and never returned it anywhere on Cancel (confirmed in jsdom:
 *   `document.activeElement` stayed on the trigger throughout, which is wrong for a dialog whose
 *   own trigger can become `disabled`/unmounted underneath it). It now follows this repo's own
 *   established Dialog convention (`mandate_editor_and_conflict_resolution_ui.tsx`'s own
 *   discard-confirm, already copied once in this same file's sibling `model_catalog_ui.tsx`):
 *   opening the confirm moves focus to its own Cancel button; Cancel returns focus to the trigger
 *   that opened it (`map-button` or `unmap-button`, whichever is still present); Confirm's own
 *   `confirm-change-button` keeps focus naturally while `writing` (it only becomes `disabled`,
 *   never unmounted, so no explicit focus move is needed there).
 * - MALFORMED-ITEM-CRASH: `handleLoadArtifacts`/`handleLoadChanges` accepted any array as the
 *   artifact/change list with no per-item validation. A malformed artifact (e.g.
 *   `required_python_packages` sent as a string instead of an array) crashed `ArtifactDetails`'s
 *   `packages.join(", ")` with no error boundary, unmounting this whole component. Both lists now
 *   validate every item (`isMappedArtifact`/`isMappingChange`) before accepting the response,
 *   same discipline `isCapabilities` already uses in the sibling FTF-0309 component; a failed
 *   parse is the same kind of typed error this component already shows for other shape failures.
 * - NO-TARGET-SIZE: every native `<button>` had no minimum target size (WCAG 2.2 §2.5.8) -- all
 *   now render through the shared `Button` component (the §72.1-fixed component, not a new style).
 * - OVERFLOW: a long source URI/digest/revision value is not wrapped -- each now carries
 *   `style={{ overflowWrap: "anywhere" }}` in `ArtifactDetails`.
 *
 * FTF-UX05 round 3 (2026-10-23, QUEUE 0e): the mapping, artifact and change-history loads each set
 * `aria-busy`, disable their trigger and show `Pending`; an empty artifact list (a validated 200
 * `[]`) is an `EmptyState` with a reload action; the heading, the root and the detail list wrap
 * unbroken values; every request-level error is an `AlertMessage` (announced, localized, with a
 * recovery step); and a FAILED write puts keyboard focus on the announced `write-error` (the Confirm
 * button disables while writing and would otherwise drop focus to `<body>`).
 */

import { useEffect, useRef, useState } from "react";

import {
  AlertMessage,
  Button,
  EmptyState,
  Pending,
  kindForStatus,
  networkError,
  uiError,
  useRefocusAfter,
  useSingleFlight,
  type UiError,
} from "../ui";
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

async function readErrorDetail(response: Response, locale: Locale): Promise<UiError> {
  const kind = kindForStatus(response.status);
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      const key = ERROR_PHRASE_KEYS[body.detail as keyof typeof ERROR_PHRASE_KEYS];
      // An unrecognized code is raw backend data (its language is the backend's choice, not this
      // component's) and is returned verbatim, never mistranslated.
      return uiError(key ? phrase(key, locale) : body.detail, kind);
    }
  } catch {
    // fall through to the generic message below
  }
  return uiError(phrase("requestFailedTemplate", locale, { status: response.status }), kind);
}

function shown(value: unknown, locale: Locale): string {
  if (value === null || value === undefined || value === "") {
    return phrase("notRecordedText", locale);
  }
  return String(value);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

const ARTIFACT_STATUSES = ["registered", "ready", "archived"];

/** A malformed artifact (e.g. `required_python_packages` sent as a non-array) previously crashed
 * `ArtifactDetails`'s `packages.join(", ")` with no error boundary, unmounting this whole
 * component -- validated up front instead, same discipline `isCapabilities` already uses in the
 * sibling FTF-0309 component. */
function isMappedArtifact(value: unknown): value is MappedArtifact {
  if (!isPlainObject(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.display_name === "string" &&
    typeof value.source_type === "string" &&
    typeof value.source_uri === "string" &&
    typeof value.model_family === "string" &&
    typeof value.base_model_revision === "string" &&
    typeof value.license_id === "string" &&
    ARTIFACT_STATUSES.includes(value.status as string) &&
    (value.weights_digest === undefined ||
      value.weights_digest === null ||
      typeof value.weights_digest === "string") &&
    (value.required_python_packages === undefined ||
      Array.isArray(value.required_python_packages)) &&
    (value.minimum_gpu_profile === undefined || isPlainObject(value.minimum_gpu_profile))
  );
}

function isMappingChange(value: unknown): value is MappingChange {
  if (!isPlainObject(value)) return false;
  return (
    typeof value.id === "string" &&
    (value.previous_trainable_artifact_id === null ||
      typeof value.previous_trainable_artifact_id === "string") &&
    (value.new_trainable_artifact_id === null ||
      typeof value.new_trainable_artifact_id === "string") &&
    typeof value.previous_catalog_version === "number" &&
    typeof value.new_catalog_version === "number" &&
    typeof value.actor === "string" &&
    typeof value.created_at === "string"
  );
}

function isMapping(body: unknown): body is TrainingArtifactMapping {
  if (!isPlainObject(body)) return false;
  return (
    (body.mapping_state === "mapped" || body.mapping_state === "unmapped") &&
    typeof body.catalog_version === "number" &&
    (body.artifact === null || body.artifact === undefined || isMappedArtifact(body.artifact))
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
  const [mappingError, setMappingError] = useState<UiError | null>(null);
  const [loadingMapping, setLoadingMapping] = useState(false);

  const [artifacts, setArtifacts] = useState<MappedArtifact[]>([]);
  const [artifactsError, setArtifactsError] = useState<UiError | null>(null);
  const [loadingArtifacts, setLoadingArtifacts] = useState(false);
  // True only after a validated, successful listing (an empty list is then a real empty state).
  const [artifactsLoaded, setArtifactsLoaded] = useState(false);
  // Deliberately starts null: never pre-selected, never derived from the alias.
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);

  const [pending, setPending] = useState<"map" | "unmap" | null>(null);
  const [writing, setWriting] = useState(false);
  const [writeError, setWriteError] = useState<UiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [changes, setChanges] = useState<MappingChange[] | null>(null);
  const [changesError, setChangesError] = useState<UiError | null>(null);
  const [loadingChanges, setLoadingChanges] = useState(false);

  // Load triggers disable themselves while busy and would drop keyboard focus to <body>.
  const armMappingFocus = useRefocusAfter(loadingMapping);
  const armArtifactsFocus = useRefocusAfter(loadingArtifacts);
  const armChangesFocus = useRefocusAfter(loadingChanges);

  const mappingFlight = useSingleFlight();
  const artifactsFlight = useSingleFlight();
  const changesFlight = useSingleFlight();

  async function handleLoadMapping() {
    if (!mappingFlight.begin()) return;
    armMappingFocus(() => triggerOf("load-mapping-button"));
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
        setMapping(null);
        setMappingError(await readErrorDetail(response, locale));
        return;
      }
      const body: unknown = await response.json();
      if (!isMapping(body)) {
        setMapping(null);
        setMappingError(uiError(phrase("mappingMalformedMapping", locale)));
        return;
      }
      setMapping(body);
      setPending(null);
    } catch (caught) {
      setMapping(null);
      setMappingError(networkError(caught, locale));
    } finally {
      mappingFlight.end();
      setLoadingMapping(false);
    }
  }

  // A failed reload must not leave the previous list (and a selection made from it) beside the error.
  function clearArtifacts() {
    setArtifacts([]);
    setSelectedArtifactId(null);
  }

  async function handleLoadArtifacts() {
    if (!artifactsFlight.begin()) return;
    armArtifactsFocus(() => triggerOf("load-artifacts-button"));
    setArtifactsError(null);
    setArtifactsLoaded(false);
    setLoadingArtifacts(true);
    try {
      const response = await fetch(artifactsUrl(apiBaseUrl, organizationId), {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        clearArtifacts();
        setArtifactsError(await readErrorDetail(response, locale));
        return;
      }
      const body: unknown = await response.json();
      if (!Array.isArray(body) || !body.every(isMappedArtifact)) {
        clearArtifacts();
        setArtifactsError(uiError(phrase("mappingMalformedArtifactListing", locale)));
        return;
      }
      setArtifacts(body);
      setArtifactsLoaded(true);
    } catch (caught) {
      clearArtifacts();
      setArtifactsError(networkError(caught, locale));
    } finally {
      artifactsFlight.end();
      setLoadingArtifacts(false);
    }
  }

  async function handleLoadChanges() {
    if (!changesFlight.begin()) return;
    armChangesFocus(() => triggerOf("load-changes-button"));
    // Cleared up front: the previous history (or its "no changes" empty state) is not the answer to
    // this request, and must not sit beside a failure or a spinner.
    setChanges(null);
    setChangesError(null);
    setLoadingChanges(true);
    try {
      const response = await fetch(
        mappingChangesUrl(apiBaseUrl, organizationId, connectionId, modelName),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        setChangesError(await readErrorDetail(response, locale));
        return;
      }
      const body: unknown = await response.json();
      if (
        !isPlainObject(body) ||
        !Array.isArray(body.changes) ||
        !body.changes.every(isMappingChange)
      ) {
        setChangesError(uiError(phrase("mappingMalformedChangeHistory", locale)));
        return;
      }
      setChanges(body.changes);
    } catch (caught) {
      setChangesError(networkError(caught, locale));
    } finally {
      changesFlight.end();
      setLoadingChanges(false);
    }
  }

  // A failed write leaves the confirm dialog open and the (disabled-while-writing) Confirm button
  // has just lost focus to <body>: put keyboard focus on the announced error instead, next to the
  // retry (the same recovery as a successful write, which moves focus to the changed state).
  const focusWriteError = useRef(false);
  function failWrite(error: UiError) {
    focusWriteError.current = true;
    setWriteError(error);
  }

  async function submit(artifactId: string | null) {
    if (!mapping) {
      failWrite(uiError(phrase("mappingLoadFirst", locale), "input"));
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
        failWrite(await readErrorDetail(response, locale));
        return;
      }
      const body: unknown = await response.json();
      if (!isMapping(body) || typeof (body as MappingResult).changed !== "boolean") {
        failWrite(uiError(phrase("mappingMalformedMapping", locale), "verify-state"));
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
      // The confirm dialog (and its own confirm/cancel buttons) just unmounted -- keep the
      // keyboard user on the state that just changed, rather than dropping focus to <body>.
      queueMicrotask(() =>
        rootRef.current?.querySelector<HTMLElement>('[data-testid="mapping-state"]')?.focus(),
      );
    } catch (caught) {
      // Nothing is shown as mapped unless the route genuinely answered.
      failWrite(networkError(caught, locale, { write: true }));
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

  const rootRef = useRef<HTMLDivElement>(null);
  function triggerOf(testId: string): HTMLElement | null {
    return rootRef.current?.querySelector<HTMLElement>(`[data-testid="${testId}"]`) ?? null;
  }

  useEffect(() => {
    if (writeError && focusWriteError.current) {
      focusWriteError.current = false;
      rootRef.current?.querySelector<HTMLElement>('[data-testid="write-error"]')?.focus();
    }
  }, [writeError]);

  // Focus management for the confirm dialog (L2 return round 2, same convention
  // `mandate_editor_and_conflict_resolution_ui.tsx`'s own discard-confirm already established):
  // opening it moves focus to its own Cancel button; Cancel returns focus to whichever trigger
  // opened it.
  function openConfirm(which: "map" | "unmap") {
    setPending(which);
    queueMicrotask(() =>
      rootRef.current?.querySelector<HTMLElement>('[data-testid="cancel-change-button"]')?.focus(),
    );
  }

  function closeConfirm() {
    const opener = pending === "map" ? "map-button" : "unmap-button";
    setPending(null);
    queueMicrotask(() =>
      rootRef.current?.querySelector<HTMLElement>(`[data-testid="${opener}"]`)?.focus(),
    );
  }

  function requestMap() {
    if (isMapped) {
      openConfirm("map");
      return;
    }
    if (selectedArtifactId) void submit(selectedArtifactId);
  }

  return (
    <div
      ref={rootRef}
      data-testid="model-to-artifact-mapping-ui"
      aria-label={phrase("mappingAriaLabel", locale, { model: modelName })}
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
      style={{ overflowWrap: "anywhere" }}
    >
      <h2 data-testid="mapping-model-name" style={{ overflowWrap: "anywhere" }}>
        <bdi>{modelName}</bdi>
      </h2>
      <p data-testid="mapping-three-steps-notice">{phrase("mappingThreeStepsNotice", locale)}</p>

      <section data-testid="mapping-current">
        <Button
          data-testid="load-mapping-button"
          disabled={loadingMapping}
          aria-busy={loadingMapping}
          onClick={handleLoadMapping}
        >
          {phrase("mappingLoadMapping", locale)}
        </Button>
        {loadingMapping && <Pending label={phrase("mappingLoadMapping", locale)} locale={locale} />}
        {mappingError && (
          <AlertMessage error={mappingError} locale={locale} testId="mapping-error" />
        )}
        {mapping && (
          <div data-testid="mapping-state" tabIndex={-1}>
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
        <Button
          data-testid="load-artifacts-button"
          disabled={loadingArtifacts}
          aria-busy={loadingArtifacts}
          onClick={handleLoadArtifacts}
        >
          {phrase("mappingLoadArtifacts", locale)}
        </Button>
        {loadingArtifacts && (
          <Pending label={phrase("mappingLoadArtifacts", locale)} locale={locale} />
        )}
        {artifactsError && (
          <AlertMessage error={artifactsError} locale={locale} testId="artifacts-error" />
        )}
        {artifactsLoaded && artifacts.length === 0 && (
          <EmptyState
            title={phrase("mappingNoArtifactsTitle", locale)}
            why={phrase("mappingNoArtifactsWhy", locale)}
            action={
              <Button data-testid="artifacts-reload-button" onClick={handleLoadArtifacts}>
                {phrase("mappingLoadArtifacts", locale)}
              </Button>
            }
          />
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
              <Button
                data-testid={`artifact-select-button-${artifact.id}`}
                aria-pressed={selectedArtifactId === artifact.id}
                disabled={artifact.status !== "ready"}
                onClick={() => setSelectedArtifactId(artifact.id)}
              >
                {selectedArtifactId === artifact.id
                  ? phrase("selected", locale)
                  : phrase("select", locale)}
              </Button>
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

        <Button data-testid="map-button" disabled={!canMap} onClick={requestMap}>
          {phrase("mappingMapButton", locale)}
        </Button>
        <Button
          data-testid="unmap-button"
          disabled={!isMapped || writing}
          onClick={() => openConfirm("unmap")}
        >
          {phrase("mappingRemoveButton", locale)}
        </Button>

        {pending && mapping && (
          <div
            role="alertdialog"
            aria-labelledby="confirm-change-text"
            data-testid="confirm-change"
            lang={resolveLocale(locale)}
            dir={dirFor(locale)}
            onKeyDown={(event) => {
              if (event.key === "Escape") closeConfirm();
            }}
          >
            <p id="confirm-change-text" data-testid="confirm-change-text">
              {pending === "unmap"
                ? phrase("mappingConfirmRemoving", locale)
                : phrase("mappingConfirmChanging", locale)}{" "}
              {phrase("mappingConfirmInvalidatesSuffix", locale, {
                version: mapping.catalog_version,
              })}
            </p>
            <Button
              variant="danger"
              data-testid="confirm-change-button"
              disabled={writing}
              onClick={() => void submit(pending === "unmap" ? null : selectedArtifactId)}
            >
              {phrase("mappingConfirmButton", locale)}
            </Button>
            <Button data-testid="cancel-change-button" onClick={closeConfirm}>
              {phrase("cancel", locale)}
            </Button>
          </div>
        )}

        {writeError && (
          <AlertMessage error={writeError} locale={locale} testId="write-error" focusable />
        )}
        {notice && (
          <p role="status" data-testid="mapping-notice">
            {notice}
          </p>
        )}
      </section>

      <section data-testid="mapping-history">
        <Button
          data-testid="load-changes-button"
          disabled={loadingChanges}
          aria-busy={loadingChanges}
          onClick={handleLoadChanges}
        >
          {phrase("mappingLoadChangeHistory", locale)}
        </Button>
        {loadingChanges && (
          <Pending label={phrase("mappingLoadChangeHistory", locale)} locale={locale} />
        )}
        {changesError && (
          <AlertMessage error={changesError} locale={locale} testId="changes-error" />
        )}
        {changes && changes.length === 0 && (
          <div data-testid="changes-empty">
            <EmptyState
              title={phrase("mappingNoChangesTitle", locale)}
              why={phrase("mappingNoChangesWhy", locale)}
              action={
                <Button data-testid="changes-reload-button" onClick={handleLoadChanges}>
                  {phrase("mappingLoadChangeHistory", locale)}
                </Button>
              }
            />
          </div>
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
    <dl data-testid={`${prefix}-artifact-details`} style={{ overflowWrap: "anywhere" }}>
      <dt>{phrase("artifactDetailArtifact", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-name`}>
        <bdi>{artifact.display_name}</bdi>
      </dd>
      <dt>{phrase("artifactDetailSource", locale)}</dt>
      <dd data-testid={`${prefix}-artifact-source`} style={{ overflowWrap: "anywhere" }}>
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
      <dd data-testid={`${prefix}-artifact-digest`} style={{ overflowWrap: "anywhere" }}>
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
