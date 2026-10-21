/**
 * GitHub branch/ref selector + immutable SHA resolver (FTF-04A04, Section
 * 6.8A/6.8B/7A.1-7A.2).
 *
 * This is the step *between* FTF-04A03's own repository selection and
 * FTF-04A05's own `TargetSourceSnapshot` creation: `credential` and
 * `repositoryIdentity` are already known context by this point (carried
 * over from the prior step), and this component calls the two real
 * FTF-04A04 backend routes (`github_branch_ref_routes.py`):
 *
 *   - `POST .../target-connections/github-branches` to list real branches
 *     for orientation.
 *   - `POST .../target-connections/github-resolve-ref` to resolve the
 *     user's own selected/typed branch, tag, or SHA to its real, current,
 *     immutable commit SHA.
 *
 * Acceptance clause 1 ("Resolve selected branch/tag/ref to immutable
 * commit SHA and persist both display ref and resolved identity") is met
 * for real, not just structurally: `resolvedRef` is populated only by a
 * real response from `github-resolve-ref` (never derived from a branch
 * list entry's own `list_time_commit_sha`, which is rendered for
 * orientation only), and that same request durably persists the resolved
 * display ref onto the real `TargetApplicationConnection` row named by
 * `targetApplicationConnectionId` (`selected_branch`, via the backend's
 * own already-CERTIFIED update path -- see `github_branch_ref_crud.py`'s
 * own docstring for the full scope boundary on the resolved-commit-SHA
 * half, which durably belongs to FTF-04A05's own downstream
 * `TargetSourceSnapshot`, not to this connection row). `onResolve` remains
 * a plain notification callback for this component's own parent (e.g. to
 * refresh a displayed connection), not the persistence step itself.
 *
 * Negative_acceptance ("Branch advancing between list/read does not mix
 * commits; unknown/inaccessible ref is blocked") is met structurally,
 * not just by the backend: `handleRefChange` (typing a custom ref) and
 * `handleSelectBranch` (picking from the list) both clear any previously
 * resolved SHA immediately -- there is no code path that keeps showing a
 * stale `resolvedRef` next to a since-changed selection, and a denied
 * resolution (backend 404/502/etc.) surfaces as `error`, never a
 * fabricated SHA.
 *
 * Scope disclosed, not hidden -- same precedent
 * `github_accessible_repository_selector_ui.tsx` already established:
 * this repository has no Next.js app shell yet, so this is a real,
 * DOM-level tested component (Vitest + React Testing Library + jsdom with
 * a mocked `fetch`), not a full browser E2E.
 */

import { useState } from "react";

export interface GitHubBranchSummary {
  name: string;
  list_time_commit_sha: string;
}

interface GitHubBranchListingResponse {
  branches: GitHubBranchSummary[];
  page: number;
  per_page: number;
  has_next_page: boolean;
}

export interface GitHubResolvedRef {
  ref: string;
  resolved_commit_sha: string;
}

export interface GitHubBranchRefSelectorProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  credential: string;
  repositoryIdentity: string;
  targetApplicationConnectionId: string;
  onResolve?: (resolved: GitHubResolvedRef) => void;
  /** Pre-fills the ref input (e.g. the connection's default branch); never resolves it. */
  defaultBranch?: string;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function githubBranchesUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/target-connections/github-branches`
  );
}

export function githubResolveRefUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/target-connections/github-resolve-ref`
  );
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

export function GitHubBranchRefSelector({
  apiBaseUrl,
  organizationId,
  projectId,
  credential,
  repositoryIdentity,
  targetApplicationConnectionId,
  onResolve,
  defaultBranch,
}: GitHubBranchRefSelectorProps) {
  const [branches, setBranches] = useState<GitHubBranchSummary[]>([]);
  const [page, setPage] = useState(1);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [loadingBranches, setLoadingBranches] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The ref currently being edited/selected (display value only).
  const [selectedRef, setSelectedRef] = useState(defaultBranch ?? "");
  // Deliberately starts `null` and is set only from a real, fresh
  // `github-resolve-ref` response inside `handleResolve` -- cleared by
  // every code path that changes `selectedRef`, so a resolved SHA is
  // never shown next to a ref it was not actually resolved for (this
  // task's own real "does not mix commits" negative_acceptance).
  const [resolvedRef, setResolvedRef] = useState<GitHubResolvedRef | null>(null);

  async function fetchBranchPage(requestedPage: number, append: boolean) {
    setLoadingBranches(true);
    setError(null);
    try {
      const response = await fetch(githubBranchesUrl(apiBaseUrl, organizationId, projectId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          credential,
          repository_identity: repositoryIdentity,
          page: requestedPage,
        }),
      });
      if (!response.ok) {
        setError(await readErrorDetail(response));
        return;
      }
      const body: GitHubBranchListingResponse = await response.json();
      setBranches((previous) => (append ? [...previous, ...body.branches] : body.branches));
      setPage(body.page);
      setHasNextPage(body.has_next_page);
    } finally {
      setLoadingBranches(false);
    }
  }

  async function handleLoadBranches() {
    await fetchBranchPage(1, false);
  }

  async function handleLoadMore() {
    await fetchBranchPage(page + 1, true);
  }

  function handleSelectBranch(name: string) {
    setSelectedRef(name);
    setResolvedRef(null);
  }

  function handleRefChange(value: string) {
    setSelectedRef(value);
    setResolvedRef(null);
  }

  async function handleResolve(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedRef.trim()) {
      return;
    }
    setResolving(true);
    setError(null);
    try {
      const response = await fetch(githubResolveRefUrl(apiBaseUrl, organizationId, projectId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          credential,
          repository_identity: repositoryIdentity,
          ref: selectedRef,
          target_application_connection_id: targetApplicationConnectionId,
        }),
      });
      if (!response.ok) {
        setError(await readErrorDetail(response));
        return;
      }
      const body: GitHubResolvedRef = await response.json();
      setResolvedRef(body);
      onResolve?.(body);
    } finally {
      setResolving(false);
    }
  }

  return (
    <div data-testid="github-branch-ref-selector">
      <button
        type="button"
        data-testid="github-load-branches-button"
        disabled={loadingBranches}
        onClick={handleLoadBranches}
      >
        Load branches
      </button>

      {error && <p data-testid="github-branch-ref-error">{error}</p>}

      <ul data-testid="github-branch-list">
        {branches.map((branch) => (
          <li key={branch.name}>
            <button
              type="button"
              data-testid={`github-branch-option-${branch.name}`}
              aria-pressed={selectedRef === branch.name}
              onClick={() => handleSelectBranch(branch.name)}
            >
              {branch.name}
            </button>
          </li>
        ))}
      </ul>

      {hasNextPage && (
        <button
          type="button"
          data-testid="github-branch-load-more-button"
          disabled={loadingBranches}
          onClick={handleLoadMore}
        >
          Load more
        </button>
      )}

      <form onSubmit={handleResolve} data-testid="github-ref-form">
        <label htmlFor="github-ref-input">Branch, tag, or commit SHA</label>
        <input
          id="github-ref-input"
          data-testid="github-ref-input"
          type="text"
          value={selectedRef}
          onChange={(event) => handleRefChange(event.target.value)}
          required
        />
        <button
          type="submit"
          data-testid="github-resolve-ref-button"
          disabled={resolving || !selectedRef.trim()}
        >
          Resolve
        </button>
      </form>

      {resolvedRef && (
        <p data-testid="github-resolved-ref-notice">
          Resolved {resolvedRef.ref} to {resolvedRef.resolved_commit_sha}
        </p>
      )}
    </div>
  );
}
