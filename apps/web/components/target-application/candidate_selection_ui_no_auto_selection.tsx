/**
 * Candidate-selection UI -- no auto-selection (FTF-04A18, Section 6.8C/
 * 7A.5/45/66/77/81).
 *
 * This is the step *after* FTF-04A17's own source-analysis pipeline has
 * already produced one or more `DiscoveredAgentCandidate` rows for a
 * given snapshot: this component calls the two real FTF-04A17/FTF-04A18
 * backend routes --
 *
 *   - `GET .../target-connections/{connectionId}/snapshots/{snapshotId}/
 *     candidates` (FTF-04A17, already-CERTIFIED) to list the real
 *     candidates for this snapshot.
 *   - `POST .../candidates/{candidateId}/select` (FTF-04A18, this task's
 *     own real route, `discovered_agent_candidate_selection_routes.py`)
 *     to record one explicit selection as a real, persisted `AuditEvent`
 *     row.
 *
 * Section 7A.5's own closing sentence ("The system must never silently
 * select the first/highest-confidence candidate") is this component's
 * own real negative_acceptance half, enforced structurally: `candidates`
 * is rendered in the exact order the backend returns it (already-
 * CERTIFIED insertion order, never re-sorted by `confidenceScore` in
 * this component), no option is ever pre-checked/default-selected on
 * load or after a fetch, and `handleSelect` always takes an explicit
 * `candidateId` argument from a real user click on that candidate's own
 * button -- there is no "select highest score" or "select first" code
 * path anywhere in this file.
 *
 * Acceptance clause 1 ("UI lists multiple detected agents and requires
 * explicit selected ID with persisted audit") is met for real: `confirmed`
 * is populated only from the real `POST .../select` response body (never
 * synthesized client-side before the request succeeds), and that response
 * is itself backed by a real, durable `AuditEvent` row written in the same
 * database transaction as the request (see the backend route/module's own
 * docstring for why an audit row, not a new mutable column, is this
 * task's own correct reading of "persisted" under its
 * `database_changes: "No independent schema change"` contract).
 *
 * Negative_acceptance ("stale candidate from prior snapshot is rejected")
 * is enforced by the backend (`StaleSnapshotCandidateError` -> HTTP 409),
 * surfaced here as `error`, never silently retried against a different
 * snapshot or masked as success.
 *
 * Network failures (a rejected `fetch`, an unreadable or malformed body)
 * are surfaced as `error` on both the listing and the select path --
 * never swallowed into an empty list or a confirmation.
 *
 * Scope disclosed, not hidden -- same precedent
 * `github_branch_ref_selector_immutable_sha_resolver.tsx` already
 * established: this repository has no Next.js app shell yet, so this is
 * a real, DOM-level tested component (Vitest + React Testing Library +
 * jsdom with a mocked `fetch`), not a full browser E2E.
 */

/*
 * FTF-UX03 round 2: rebuilt on the UX01 components, bilingual with `lang`/`dir` on every state
 * (the old forced `dir` is now the real locale direction), names and runtimes in <bdi>. Row buttons
 * carry the candidate's name in their accessible name, the confirmation names the candidate and
 * shows a localized time (never a UUID or an ISO string), and a 2xx counts only when its body binds
 * to the candidate and snapshot that were asked for (`bindSelection`, `bindListing`).
 */

import { useState } from "react";

import {
  Banner,
  Button,
  EmptyState,
  Pending,
  dirFor,
  phrase,
  resolveLocale,
  type Locale,
} from "../ui";
import {
  MALFORMED_FAILURE,
  NETWORK_FAILURE,
  failureFromResponse,
  selectorFailureMessage,
  type SelectorRoute,
  isRecord,
  type Failure,
} from "./common";
import { t04a18 } from "./strings_04a18";

export interface DiscoveredAgentCandidateSummary {
  id: string;
  display_name: string;
  framework_or_runtime: string | null;
  confidence_score: number;
  discovery_status: string;
}

interface CandidateListingResponse {
  items: DiscoveredAgentCandidateSummary[];
  limit: number;
  offset: number;
  total: number;
}

export interface CandidateSelectionConfirmation {
  candidate_id: string;
  target_source_snapshot_id: string;
  selected_by: string;
  selected_at: string;
}

export interface CandidateSelectionUIProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  connectionId: string;
  snapshotId: string;
  onSelected?: (confirmation: CandidateSelectionConfirmation) => void;
  locale?: Locale;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function candidatesListUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  connectionId: string,
  snapshotId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/target-connections/${connectionId}` +
    `/snapshots/${snapshotId}/candidates`
  );
}

export function candidateSelectUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  connectionId: string,
  snapshotId: string,
  candidateId: string,
): string {
  return `${candidatesListUrl(apiBaseUrl, organizationId, projectId, connectionId, snapshotId)}/${candidateId}/select`;
}

// An ISO-8601 instant with an explicit offset, as the backend's `isoformat()` writes it; a bare "1"
// parses as a date in JavaScript but is not a time this page may present.
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;

// The backend's closed set (discovered_agent_candidate_schema.ALLOWED_DISCOVERY_STATUSES).
const DISCOVERY_STATUSES: readonly string[] = ["candidate"];

interface ListingContext {
  organizationId: string;
  snapshotId: string;
}

function parseCandidate(
  value: unknown,
  context: ListingContext,
): DiscoveredAgentCandidateSummary | null {
  if (!isRecord(value)) return null;
  // Each row says which organization and snapshot it belongs to: a row for another one is not
  // this snapshot's candidate and is never listed or selectable.
  if (value.organization_id !== context.organizationId) return null;
  if (value.target_source_snapshot_id !== context.snapshotId) return null;
  const { id, display_name, framework_or_runtime, confidence_score, discovery_status } = value;
  if (typeof id !== "string" || id === "") return null;
  if (typeof display_name !== "string" || display_name.trim() === "") return null;
  if (framework_or_runtime !== null && typeof framework_or_runtime !== "string") return null;
  if (typeof confidence_score !== "number" || !Number.isFinite(confidence_score)) return null;
  if (confidence_score < 0 || confidence_score > 1) return null;
  if (typeof discovery_status !== "string" || !DISCOVERY_STATUSES.includes(discovery_status)) {
    return null;
  }
  return { id, display_name, framework_or_runtime, confidence_score, discovery_status };
}

/** A candidate listing is only a listing when every field has its documented type; else `null`. */
export function bindListing(
  body: unknown,
  context: ListingContext,
): CandidateListingResponse | null {
  if (!isRecord(body)) return null;
  const { items, limit, offset, total } = body;
  if (!Array.isArray(items)) return null;
  if (!Number.isInteger(limit) || !Number.isInteger(offset) || !Number.isInteger(total)) {
    return null;
  }
  const parsed: DiscoveredAgentCandidateSummary[] = [];
  for (const entry of items) {
    const candidate = parseCandidate(entry, context);
    if (!candidate) return null;
    parsed.push(candidate);
  }
  return {
    items: parsed,
    limit: limit as number,
    offset: offset as number,
    total: total as number,
  };
}

/**
 * Binds a select reply to the request: the confirmed candidate and snapshot must be the ones that
 * were asked for, the actor must be present and the time must parse. A reply for another candidate
 * is never shown as this candidate's confirmation.
 */
export function bindSelection(
  requestedCandidateId: string,
  requestedSnapshotId: string,
  body: unknown,
): CandidateSelectionConfirmation | null {
  if (!isRecord(body)) return null;
  const { candidate_id, target_source_snapshot_id, selected_by, selected_at } = body;
  if (candidate_id !== requestedCandidateId) return null;
  if (target_source_snapshot_id !== requestedSnapshotId) return null;
  if (typeof selected_by !== "string" || selected_by.trim() === "") return null;
  if (typeof selected_at !== "string" || !ISO_INSTANT.test(selected_at)) return null;
  if (Number.isNaN(Date.parse(selected_at))) return null;
  return {
    candidate_id: requestedCandidateId,
    target_source_snapshot_id: requestedSnapshotId,
    selected_by,
    selected_at,
  };
}

/** A person-readable time in the viewer's locale and zone (never the raw ISO string). */
export function formatSelectedAt(iso: string, locale: Locale | string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(resolveLocale(locale) === "ar" ? "ar" : "en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

/**
 * Identity (UI CHECKLIST c): organization, project, connection and snapshot are the key, so a
 * candidate list or a confirmation never carries over to another snapshot.
 */
export function CandidateSelectionUI(props: CandidateSelectionUIProps) {
  const identity = JSON.stringify([
    props.apiBaseUrl,
    props.organizationId,
    props.projectId,
    props.connectionId,
    props.snapshotId,
  ]);
  return <CandidateSelection key={identity} {...props} />;
}

function CandidateSelection({
  apiBaseUrl,
  organizationId,
  projectId,
  connectionId,
  snapshotId,
  onSelected,
  locale = "en",
}: CandidateSelectionUIProps) {
  const [candidates, setCandidates] = useState<DiscoveredAgentCandidateSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selectingId, setSelectingId] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [failureRoute, setFailureRoute] = useState<SelectorRoute>("candidates");
  // Deliberately starts `null` and is set only from a real, fresh
  // `.../select` response inside `handleSelect` -- this task's own real
  // "no auto-selection" guarantee: nothing in this component's own state
  // ever names a confirmed candidate before the user explicitly acts.
  const [confirmed, setConfirmed] = useState<CandidateSelectionConfirmation | null>(null);

  const lang = resolveLocale(locale);

  async function handleLoadCandidates() {
    setLoading(true);
    setFailure(null);
    setFailureRoute("candidates");
    try {
      const response = await fetch(
        candidatesListUrl(apiBaseUrl, organizationId, projectId, connectionId, snapshotId),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        const refused = await failureFromResponse(response);
        setFailure(refused);
        return;
      }
      let raw: unknown;
      try {
        raw = await response.json();
      } catch {
        raw = null;
      }
      const body = bindListing(raw, { organizationId, snapshotId });
      if (!body) {
        // Never swallowed into an empty list: the previous list stays and the problem is shown.
        setFailure(MALFORMED_FAILURE);
        return;
      }
      setCandidates(body.items);
      setLoaded(true);
    } catch {
      // A network failure is surfaced, never turned into an empty or "successful" listing.
      setFailure(NETWORK_FAILURE);
    } finally {
      setLoading(false);
    }
  }

  async function handleSelect(candidateId: string) {
    setSelectingId(candidateId);
    setFailure(null);
    setFailureRoute("select");
    try {
      const response = await fetch(
        candidateSelectUrl(
          apiBaseUrl,
          organizationId,
          projectId,
          connectionId,
          snapshotId,
          candidateId,
        ),
        { method: "POST", credentials: "include" },
      );
      if (!response.ok) {
        const refused = await failureFromResponse(response);
        setFailure(refused);
        return;
      }
      let raw: unknown;
      try {
        raw = await response.json();
      } catch {
        raw = null;
      }
      const body = bindSelection(candidateId, snapshotId, raw);
      if (!body) {
        // Nothing is confirmed unless the POST really succeeded with a bound body.
        setFailure(MALFORMED_FAILURE);
        return;
      }
      setConfirmed(body);
      onSelected?.(body);
    } catch {
      setFailure(NETWORK_FAILURE);
    } finally {
      setSelectingId(null);
    }
  }

  const showEmpty = loaded && !loading && failure === null && candidates.length === 0;
  const confirmedName =
    confirmed &&
    candidates.find((candidate) => candidate.id === confirmed.candidate_id)?.display_name;

  return (
    <div data-testid="candidate-selection-ui" lang={lang} dir={dirFor(locale)}>
      <Button
        variant="secondary"
        data-testid="candidate-load-button"
        disabled={loading}
        onClick={handleLoadCandidates}
      >
        {t04a18("loadCandidates", locale)}
      </Button>

      {failure && (
        <div data-testid="candidate-selection-error">
          <Banner kind="error" locale={locale}>
            {selectorFailureMessage(failure, locale, failureRoute)}
          </Banner>
        </div>
      )}

      <div aria-busy={loading || selectingId !== null}>
        {loading && <Pending label={t04a18("loadingCandidates", locale)} locale={locale} />}
        {selectingId !== null && (
          <Pending label={t04a18("selectingCandidate", locale)} locale={locale} />
        )}

        {showEmpty && (
          <EmptyState
            title={t04a18("noCandidatesTitle", locale)}
            why={t04a18("noCandidatesWhy", locale)}
            action={
              <Button
                variant="secondary"
                data-testid="candidate-reload-button"
                onClick={handleLoadCandidates}
              >
                {t04a18("reloadCandidates", locale)}
              </Button>
            }
          />
        )}

        <ul data-testid="candidate-list" aria-label={t04a18("listLabel", locale)}>
          {candidates.map((candidate) => {
            const isConfirmed = confirmed?.candidate_id === candidate.id;
            const label = t04a18("selectCandidate", locale);
            return (
              <li
                key={candidate.id}
                data-testid={`candidate-row-${candidate.id}`}
                style={{ overflowWrap: "anywhere" }}
              >
                <bdi>{candidate.display_name}</bdi>{" "}
                {candidate.framework_or_runtime && (
                  <span>
                    {t04a18("runtime", locale)}: <bdi>{candidate.framework_or_runtime}</bdi>{" "}
                  </span>
                )}
                <span data-testid={`candidate-confidence-${candidate.id}`}>
                  {phrase("confidenceScore", locale)}: {candidate.confidence_score}
                </span>{" "}
                {isConfirmed && (
                  <span>
                    <span aria-hidden="true">✓</span> {t04a18("selectedMark", locale)}{" "}
                  </span>
                )}
                <Button
                  variant={isConfirmed ? "primary" : "secondary"}
                  data-testid={`candidate-select-button-${candidate.id}`}
                  aria-label={`${label}: ${candidate.display_name}`}
                  disabled={selectingId !== null}
                  onClick={() => handleSelect(candidate.id)}
                >
                  {label}
                </Button>
              </li>
            );
          })}
        </ul>
      </div>

      {confirmed && (
        <div
          role="status"
          data-testid="candidate-selection-confirmation"
          style={{ overflowWrap: "anywhere" }}
        >
          <span aria-hidden="true">✓</span> {t04a18("selectedPrefix", locale)}{" "}
          <bdi>{confirmedName ?? t04a18("unknownCandidate", locale)}</bdi>{" "}
          {t04a18("selectedAt", locale)} {formatSelectedAt(confirmed.selected_at, locale)}
        </div>
      )}
    </div>
  );
}
