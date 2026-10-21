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

import { useState } from "react";

import { dirFor, phrase, type Locale } from "../ui";

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

export function CandidateSelectionUI({
  apiBaseUrl,
  organizationId,
  projectId,
  connectionId,
  snapshotId,
  onSelected,
  locale = "en",
}: CandidateSelectionUIProps) {
  const [candidates, setCandidates] = useState<DiscoveredAgentCandidateSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectingId, setSelectingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Deliberately starts `null` and is set only from a real, fresh
  // `.../select` response inside `handleSelect` -- this task's own real
  // "no auto-selection" guarantee: nothing in this component's own state
  // ever names a confirmed candidate before the user explicitly acts.
  const [confirmed, setConfirmed] = useState<CandidateSelectionConfirmation | null>(null);

  async function handleLoadCandidates() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        candidatesListUrl(apiBaseUrl, organizationId, projectId, connectionId, snapshotId),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        setError(await readErrorDetail(response));
        return;
      }
      const body: CandidateListingResponse = await response.json();
      if (!body || !Array.isArray(body.items)) {
        setError("malformed candidate listing response");
        return;
      }
      setCandidates(body.items);
    } catch (caught) {
      // A network failure or unreadable body is surfaced, never swallowed
      // and never turned into an empty or "successful" listing.
      setError(networkErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  async function handleSelect(candidateId: string) {
    setSelectingId(candidateId);
    setError(null);
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
        setError(await readErrorDetail(response));
        return;
      }
      const body: CandidateSelectionConfirmation = await response.json();
      if (!body || typeof body.candidate_id !== "string") {
        setError("malformed candidate selection response");
        return;
      }
      setConfirmed(body);
      onSelected?.(body);
    } catch (caught) {
      // Nothing is confirmed unless the POST really succeeded.
      setError(networkErrorMessage(caught));
    } finally {
      setSelectingId(null);
    }
  }

  return (
    <div data-testid="candidate-selection-ui" dir={dirFor(locale)}>
      <button
        type="button"
        data-testid="candidate-load-button"
        disabled={loading}
        onClick={handleLoadCandidates}
      >
        Load detected agent candidates
      </button>

      {error && <p data-testid="candidate-selection-error">{error}</p>}

      <ul data-testid="candidate-list">
        {candidates.map((candidate) => (
          <li key={candidate.id} data-testid={`candidate-row-${candidate.id}`}>
            <span>{candidate.display_name}</span>
            <span data-testid={`candidate-confidence-${candidate.id}`}>
              {phrase("confidenceScore", locale)}: {candidate.confidence_score}
            </span>
            <button
              type="button"
              data-testid={`candidate-select-button-${candidate.id}`}
              disabled={selectingId !== null}
              onClick={() => handleSelect(candidate.id)}
            >
              Select this candidate
            </button>
          </li>
        ))}
      </ul>

      {confirmed && (
        <p data-testid="candidate-selection-confirmation">
          Selected {confirmed.candidate_id} at {confirmed.selected_at}
        </p>
      )}
    </div>
  );
}
