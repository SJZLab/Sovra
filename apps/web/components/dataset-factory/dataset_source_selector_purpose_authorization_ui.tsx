/**
 * Dataset source selector + purpose-authorization UI (FTF-0500A, Section
 * 6.8F/7A.10/9/45/65-67/77/81).
 *
 * Assembles FTF-0500's real, already-CERTIFIED `DatasetSourceConnection`
 * surface (`apps/api/app/datasets/dataset_source_connection_routes.py`)
 * into the one screen this task's own acceptance clause names: "UI
 * selects explicit source, credentials scope, purpose and privacy
 * boundary with preview and approval."
 *
 *  - **Source / credentials scope / purpose / privacy boundary selection:**
 *    the "New connection" form's `source_type`/`credential`/
 *    `allowed_purposes`/`security_classification` fields map exactly onto
 *    FTF-0500's own closed vocabularies (redeclared below, not imported --
 *    this repository has no shared TS/Python contract package for domain
 *    enums yet, the same precedent `requirements_ui.tsx`'s own
 *    `BUDGET_SCOPES` already established). The raw credential value is
 *    never re-displayed once entered; only `has_credential` (a boolean)
 *    ever comes back from the real backend (Section 46).
 *  - **Preview:** a real, local review-before-submit step -- "Review"
 *    moves the form into a `preview` step that echoes back exactly the
 *    fields about to be sent (never the raw credential, only whether one
 *    was provided) and requires a separate, explicit "Confirm and create"
 *    click before anything is actually submitted. This is deliberately
 *    *not* Section 7A.10's connector-based external source-metadata
 *    preview (estimated record/byte counts scraped from the real S3/
 *    Postgres/REST source) -- no source-connector validation call exists
 *    anywhere in this repository yet (`dataset_source_connection_routes.py`'s
 *    own module docstring discloses this explicitly: "does not implement
 *    any actual source-connector call ... FTF-0501-and-later's own job").
 *    Building that preview against a connector that does not exist would
 *    be exactly the "production placeholder path" Law 11 forbids; the
 *    disclosed local review-before-submit step is the real capability
 *    this repository can honestly ship today.
 *  - **Approval:** two real, backend-enforced approval points, not a
 *    client-side illusion of one -- (1) the explicit "Confirm and create"
 *    click is the only code path that ever calls the real `POST
 *    .../dataset-sources`, and (2) "Check authorization" calls the real
 *    `POST .../dataset-sources/{id}/authorize` (FTF-0500's own "purpose
 *    authorization before import" primitive), surfacing the real 200
 *    `authorized`/403 `denied` outcome rather than a UI-only guess. The
 *    authorization panel offers every purpose in the closed vocabulary,
 *    not only the ones the selected connection already grants -- so a
 *    denial for an out-of-grant purpose is the real backend saying so
 *    (`DatasetSourceAuthorizationError`), never a client-side filter
 *    quietly hiding the negative case this task's own negative_acceptance
 *    requires be provable.
 *
 * negative_acceptance ("No automatic repo/log harvesting or hidden broad
 * source permissions") is met structurally: every field reaching the real
 * backend is a value the user explicitly typed, checked or selected --
 * `allowedPurposes`/`securityClassification` start empty and the "Review"
 * step is disabled until the user has made an explicit choice for each;
 * there is no code path that defaults `security_classification` to a
 * value the user did not pick, and no bulk-import or source-listing
 * capability exists in this component at all.
 *
 * A real, DOM-level tested component (Vitest + React Testing Library +
 * jsdom with a mocked `fetch`), not a full browser E2E -- the same
 * precedent `requirements_ui.tsx`/`github_accessible_repository_selector_ui.tsx`
 * already established (no Next.js app shell exists yet in this
 * repository).
 */

import { useEffect, useState } from "react";

import { Button, EmptyState, Field, PendingView } from "../ui";
import { dirFor, phrase, resolveLocale, type Locale } from "../ui/phrases";

// Section 6.8F's own literal enumeration -- redeclared to match
// `dataset_source_connection_schema.py::ALLOWED_SOURCE_TYPES` exactly.
export const DATASET_SOURCE_TYPES = [
  "upload",
  "s3",
  "minio",
  "database",
  "agent_traces",
  "production_conversations",
  "api",
] as const;
export type DatasetSourceType = (typeof DATASET_SOURCE_TYPES)[number];

// `dataset_source_connection_schema.py::ALLOWED_PURPOSES`.
export const DATASET_SOURCE_PURPOSES = [
  "training",
  "validation",
  "held_out_evaluation",
  "candidate_development",
  "baseline_only",
] as const;
export type DatasetSourcePurpose = (typeof DATASET_SOURCE_PURPOSES)[number];

// `dataset_source_connection_schema.py::ALLOWED_SECURITY_CLASSIFICATIONS`.
export const DATASET_SOURCE_SECURITY_CLASSIFICATIONS = [
  "public",
  "internal",
  "confidential",
  "restricted",
] as const;
export type DatasetSourceSecurityClassification =
  (typeof DATASET_SOURCE_SECURITY_CLASSIFICATIONS)[number];

export interface DatasetSourceConnectionSummary {
  id: string;
  organization_id: string;
  project_id: string;
  source_type: string;
  display_name: string;
  has_credential: boolean;
  has_connection_config: boolean;
  allowed_purposes: string[];
  security_classification: string;
  connection_status: string;
  last_validated_at: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  revoked_at: string | null;
}

export interface DatasetSourceSelectorPurposeAuthorizationProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  /** Standard-phrase language; "ar" renders right-to-left. */
  locale?: Locale;
}

type FormStep = "form" | "preview";
type AuthorizeState = "idle" | "checking" | "authorized" | "denied";

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function datasetSourceConnectionsUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/dataset-sources`
  );
}

export function datasetSourceAuthorizeUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  connectionId: string,
): string {
  return `${datasetSourceConnectionsUrl(apiBaseUrl, organizationId, projectId)}/${connectionId}/authorize`;
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

export function DatasetSourceSelectorPurposeAuthorizationUi({
  apiBaseUrl,
  organizationId,
  projectId,
  locale = "en",
}: DatasetSourceSelectorPurposeAuthorizationProps) {
  const [connections, setConnections] = useState<DatasetSourceConnectionSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const [formStep, setFormStep] = useState<FormStep>("form");
  const [sourceType, setSourceType] = useState<DatasetSourceType>(DATASET_SOURCE_TYPES[0]);
  const [displayName, setDisplayName] = useState("");
  const [purposes, setPurposes] = useState<Set<DatasetSourcePurpose>>(new Set());
  const [securityClassification, setSecurityClassification] = useState<
    DatasetSourceSecurityClassification | ""
  >("");
  const [credential, setCredential] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [selectedConnectionId, setSelectedConnectionId] = useState<string | null>(null);
  const [purposeToCheck, setPurposeToCheck] = useState<DatasetSourcePurpose | "">("");
  const [authorizeState, setAuthorizeState] = useState<AuthorizeState>("idle");
  const [authorizeMessage, setAuthorizeMessage] = useState<string | null>(null);

  async function refreshConnections() {
    setListError(null);
    const response = await fetch(
      datasetSourceConnectionsUrl(apiBaseUrl, organizationId, projectId),
      {
        credentials: "include",
      },
    );
    if (!response.ok) {
      setListError(await readErrorDetail(response));
      return;
    }
    setConnections(await response.json());
  }

  // Deliberately runs once per identity triple, not on every keystroke of
  // the in-progress "New connection" form below.
  useEffect(() => {
    void refreshConnections();
  }, [apiBaseUrl, organizationId, projectId]);

  function togglePurpose(purpose: DatasetSourcePurpose) {
    setPurposes((previous) => {
      const next = new Set(previous);
      if (next.has(purpose)) {
        next.delete(purpose);
      } else {
        next.add(purpose);
      }
      return next;
    });
  }

  const formIsReadyForReview =
    displayName.trim().length > 0 && purposes.size > 0 && securityClassification !== "";

  function handleReview(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!formIsReadyForReview) {
      return;
    }
    setCreateError(null);
    setFormStep("preview");
  }

  function handleBackToEdit() {
    setFormStep("form");
  }

  function resetForm() {
    setSourceType(DATASET_SOURCE_TYPES[0]);
    setDisplayName("");
    setPurposes(new Set());
    setSecurityClassification("");
    setCredential("");
    setFormStep("form");
  }

  async function handleConfirmCreate() {
    setCreating(true);
    setCreateError(null);
    try {
      const response = await fetch(
        datasetSourceConnectionsUrl(apiBaseUrl, organizationId, projectId),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            source_type: sourceType,
            display_name: displayName,
            allowed_purposes: Array.from(purposes),
            security_classification: securityClassification,
            ...(credential ? { credential } : {}),
          }),
        },
      );
      if (!response.ok) {
        // Preserve the draft on a real failure -- never revert the user's
        // own in-progress entries back to empty fields.
        setCreateError(await readErrorDetail(response));
        return;
      }
      const created: DatasetSourceConnectionSummary = await response.json();
      setConnections((previous) => (previous ? [...previous, created] : [created]));
      setSelectedConnectionId(created.id);
      setAuthorizeState("idle");
      setAuthorizeMessage(null);
      resetForm();
    } finally {
      setCreating(false);
    }
  }

  function handleSelectConnection(connection: DatasetSourceConnectionSummary) {
    setSelectedConnectionId(connection.id);
    setPurposeToCheck("");
    setAuthorizeState("idle");
    setAuthorizeMessage(null);
  }

  async function handleCheckAuthorization() {
    if (!selectedConnectionId || !purposeToCheck) {
      return;
    }
    setAuthorizeState("checking");
    setAuthorizeMessage(null);
    try {
      const response = await fetch(
        datasetSourceAuthorizeUrl(apiBaseUrl, organizationId, projectId, selectedConnectionId),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ purpose: purposeToCheck }),
        },
      );
      if (response.status === 403) {
        setAuthorizeState("denied");
        setAuthorizeMessage(await readErrorDetail(response));
        return;
      }
      if (!response.ok) {
        setAuthorizeState("idle");
        setAuthorizeMessage(await readErrorDetail(response));
        return;
      }
      setAuthorizeState("authorized");
      setAuthorizeMessage(null);
    } finally {
      // state already set in each branch above
    }
  }

  const selectedConnection =
    connections?.find((connection) => connection.id === selectedConnectionId) ?? null;

  function connectionStatusLabel(status: string): string {
    if (status === "active") return phrase("connectionStatusActive", locale);
    if (status === "revoked") return phrase("connectionStatusRevoked", locale);
    return status;
  }

  return (
    <div data-testid="dataset-source-selector" lang={resolveLocale(locale)} dir={dirFor(locale)}>
      <section data-testid="dataset-source-list-section">
        <h2>{phrase("datasetSourceHeading", locale)}</h2>
        {listError && (
          <p role="alert" data-testid="dataset-source-list-error">
            {listError}
          </p>
        )}
        {connections === null && !listError && (
          <PendingView
            phase="busy"
            label={phrase("datasetSourceLoading", locale)}
            locale={locale}
          />
        )}
        {connections !== null && connections.length === 0 && !listError && (
          <div data-testid="dataset-source-empty-state">
            <EmptyState
              title={phrase("noConnectionsTitle", locale)}
              why={phrase("noConnectionsWhy", locale)}
              action={
                <Button
                  data-testid="dataset-source-empty-state-action"
                  onClick={() => document.getElementById("dataset-source-display-name")?.focus()}
                >
                  {phrase("newConnectionHeading", locale)}
                </Button>
              }
            />
          </div>
        )}
        <ul data-testid="dataset-source-connection-list">
          {(connections ?? []).map((connection) => (
            <li key={connection.id}>
              <Button
                variant="secondary"
                data-testid={`dataset-source-option-${connection.id}`}
                aria-pressed={selectedConnectionId === connection.id}
                onClick={() => handleSelectConnection(connection)}
              >
                {connection.display_name} ({connection.source_type},{" "}
                {connection.security_classification},{" "}
                {connectionStatusLabel(connection.connection_status)})
              </Button>
            </li>
          ))}
        </ul>
      </section>

      <section data-testid="dataset-source-new-connection-section">
        <h2>{phrase("newConnectionHeading", locale)}</h2>
        {createError && (
          <p role="alert" data-testid="dataset-source-create-error">
            {createError}
          </p>
        )}

        {formStep === "form" && (
          <form onSubmit={handleReview} data-testid="dataset-source-form">
            <label htmlFor="dataset-source-type">{phrase("sourceTypeLabel", locale)}</label>
            <select
              id="dataset-source-type"
              data-testid="dataset-source-type-select"
              value={sourceType}
              onChange={(event) => setSourceType(event.target.value as DatasetSourceType)}
            >
              {DATASET_SOURCE_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>

            <Field
              id="dataset-source-display-name"
              data-testid="dataset-source-display-name-input"
              label={phrase("displayNameLabel", locale)}
              requirement="required"
              locale={locale}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              required
            />

            <Field
              id="dataset-source-credential"
              data-testid="dataset-source-credential-input"
              label={phrase("credentialLabel", locale)}
              requirement="optional"
              locale={locale}
              type="password"
              value={credential}
              onChange={(event) => setCredential(event.target.value)}
            />

            <fieldset data-testid="dataset-source-purposes-fieldset">
              <legend>{phrase("allowedPurposesLegend", locale)}</legend>
              {DATASET_SOURCE_PURPOSES.map((purpose) => (
                <label key={purpose}>
                  <input
                    type="checkbox"
                    data-testid={`dataset-source-purpose-${purpose}`}
                    checked={purposes.has(purpose)}
                    onChange={() => togglePurpose(purpose)}
                  />
                  {purpose}
                </label>
              ))}
            </fieldset>

            <label htmlFor="dataset-source-security-classification">
              {phrase("securityClassificationLabel", locale)}
            </label>
            <select
              id="dataset-source-security-classification"
              data-testid="dataset-source-security-classification-select"
              value={securityClassification}
              onChange={(event) =>
                setSecurityClassification(
                  event.target.value as DatasetSourceSecurityClassification | "",
                )
              }
            >
              <option value="">{phrase("selectPlaceholder", locale)}</option>
              {DATASET_SOURCE_SECURITY_CLASSIFICATIONS.map((classification) => (
                <option key={classification} value={classification}>
                  {classification}
                </option>
              ))}
            </select>

            <Button
              type="submit"
              data-testid="dataset-source-review-button"
              disabled={!formIsReadyForReview}
            >
              {phrase("reviewButton", locale)}
            </Button>
          </form>
        )}

        {formStep === "preview" && (
          <div data-testid="dataset-source-preview">
            <p data-testid="dataset-source-preview-notice">{phrase("previewNotice", locale)}</p>
            <dl>
              <dt>{phrase("previewSourceTypeLabel", locale)}</dt>
              <dd data-testid="dataset-source-preview-source-type">{sourceType}</dd>
              <dt>{phrase("previewDisplayNameLabel", locale)}</dt>
              <dd data-testid="dataset-source-preview-display-name">
                <bdi>{displayName}</bdi>
              </dd>
              <dt>{phrase("previewCredentialScopeLabel", locale)}</dt>
              <dd data-testid="dataset-source-preview-credential">
                {credential
                  ? phrase("credentialProvided", locale)
                  : phrase("credentialNotProvided", locale)}
              </dd>
              <dt>{phrase("previewPurposesLabel", locale)}</dt>
              <dd data-testid="dataset-source-preview-purposes">
                {Array.from(purposes).join(", ")}
              </dd>
              <dt>{phrase("previewPrivacyBoundaryLabel", locale)}</dt>
              <dd data-testid="dataset-source-preview-security-classification">
                {securityClassification}
              </dd>
            </dl>
            <Button
              variant="secondary"
              data-testid="dataset-source-back-button"
              onClick={handleBackToEdit}
              disabled={creating}
            >
              {phrase("backButton", locale)}
            </Button>
            <Button
              data-testid="dataset-source-confirm-button"
              onClick={handleConfirmCreate}
              disabled={creating}
            >
              {phrase("confirmCreateButton", locale)}
            </Button>
          </div>
        )}
      </section>

      {selectedConnection && (
        <section data-testid="dataset-source-authorization-section">
          <h2>{phrase("authorizationHeading", locale)}</h2>
          <p data-testid="dataset-source-authorization-selected-connection">
            <bdi>{selectedConnection.display_name}</bdi>
          </p>
          <label htmlFor="dataset-source-purpose-to-check">
            {phrase("purposeToCheckLabel", locale)}
          </label>
          <select
            id="dataset-source-purpose-to-check"
            data-testid="dataset-source-purpose-to-check-select"
            value={purposeToCheck}
            onChange={(event) => setPurposeToCheck(event.target.value as DatasetSourcePurpose | "")}
          >
            <option value="">{phrase("selectPlaceholder", locale)}</option>
            {DATASET_SOURCE_PURPOSES.map((purpose) => (
              <option key={purpose} value={purpose}>
                {purpose}
              </option>
            ))}
          </select>
          <Button
            data-testid="dataset-source-check-authorization-button"
            onClick={handleCheckAuthorization}
            disabled={!purposeToCheck || authorizeState === "checking"}
          >
            {phrase("checkAuthorizationButton", locale)}
          </Button>
          {authorizeState === "authorized" && (
            <p data-testid="dataset-source-authorization-result-authorized">
              {phrase("authorizedResult", locale)}
            </p>
          )}
          {authorizeState === "denied" && (
            <p role="alert" data-testid="dataset-source-authorization-result-denied">
              {authorizeMessage}
            </p>
          )}
          {authorizeState === "idle" && authorizeMessage && (
            <p role="alert" data-testid="dataset-source-authorization-error">
              {authorizeMessage}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
