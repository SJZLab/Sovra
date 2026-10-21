/**
 * GitHub accessible-repository selector (FTF-04A03, Section 6.8A/7A.1-7A.2).
 *
 * This is the "browse before connecting" step Section 7A.2 requires: the
 * user pastes an already-resolved GitHub App installation token or
 * organization-approved token, and this component calls the real FTF-04A03
 * backend (`POST /api/v1/organizations/{organization_id}/projects/
 * {project_id}/target-connections/github-repositories`,
 * `github_repository_listing_routes.py`) to render the real, credential-
 * scoped, paginated list of repositories GitHub itself says that
 * credential can see -- never a client-side mock list (Law 9).
 *
 * Acceptance clause 1 ("Render only credential-accessible repositories
 * with pagination and explicit selection") is met structurally: the
 * rendered list is exactly `repositories` from the real backend response
 * (no client-side filtering that could hide or add entries), "Load more"
 * fetches the next real page via the response's own `has_next_page`/`page`
 * fields, and selection state starts at `null` and only ever changes on a
 * real user click (`handleSelect`) -- there is no code path that assigns
 * `selectedRepositoryIdentity` on mount, on a successful fetch, or from
 * any other implicit trigger, so the first (or any) result is never
 * auto-selected (this task's own negative_acceptance).
 *
 * Scope disclosed, not hidden -- same precedent `project_schema_api_ui.tsx`/
 * `oidc_login.tsx` already established: this repository has no Next.js
 * app shell yet (no `next.config`, no `app/page.tsx`), so this is a real,
 * DOM-level tested component (Vitest + React Testing Library + jsdom with
 * a mocked `fetch`), not a full browser E2E.
 */

import { useState } from "react";

import { EmptyState, dirFor, phrase, type Locale } from "../ui";

export interface GitHubRepositorySummary {
  repository_identity: string;
  private: boolean;
  default_branch: string;
}

interface GitHubRepositoryListingResponse {
  repositories: GitHubRepositorySummary[];
  page: number;
  per_page: number;
  has_next_page: boolean;
}

export interface GitHubAccessibleRepositorySelectorProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  onSelect?: (repository: GitHubRepositorySummary) => void;
  locale?: Locale;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function githubRepositoriesUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/target-connections/github-repositories`
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

export function GitHubAccessibleRepositorySelector({
  apiBaseUrl,
  organizationId,
  projectId,
  onSelect,
  locale = "en",
}: GitHubAccessibleRepositorySelectorProps) {
  const [credential, setCredential] = useState("");
  const [repositories, setRepositories] = useState<GitHubRepositorySummary[]>([]);
  const [page, setPage] = useState(1);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // True only after a real search returned a page successfully (empty state must not
  // show before searching or after an error).
  const [searched, setSearched] = useState(false);
  // Explicit selection state -- deliberately starts `null` and is set only
  // inside `handleSelect`, itself only ever invoked from a real click
  // handler below. No other code path in this component assigns it.
  const [selectedRepositoryIdentity, setSelectedRepositoryIdentity] = useState<string | null>(null);

  async function fetchPage(requestedPage: number, append: boolean) {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(githubRepositoriesUrl(apiBaseUrl, organizationId, projectId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ credential, page: requestedPage }),
      });
      if (!response.ok) {
        setError(await readErrorDetail(response));
        return;
      }
      const body: GitHubRepositoryListingResponse = await response.json();
      setRepositories((previous) =>
        append ? [...previous, ...body.repositories] : body.repositories,
      );
      setPage(body.page);
      setHasNextPage(body.has_next_page);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  }

  async function handleLoadRepositories(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSelectedRepositoryIdentity(null);
    setSearched(false);
    await fetchPage(1, false);
  }

  async function handleLoadMore() {
    await fetchPage(page + 1, true);
  }

  function handleSelect(repository: GitHubRepositorySummary) {
    setSelectedRepositoryIdentity(repository.repository_identity);
    onSelect?.(repository);
  }

  return (
    <div data-testid="github-repository-selector" dir={dirFor(locale)}>
      <form onSubmit={handleLoadRepositories} data-testid="github-credential-form">
        <label htmlFor="github-credential">GitHub token</label>
        <input
          id="github-credential"
          data-testid="github-credential-input"
          type="password"
          value={credential}
          onChange={(event) => setCredential(event.target.value)}
          aria-describedby="github-credential-help"
          required
        />
        <p id="github-credential-help">{phrase("githubTokenHelp", locale)}</p>
        <button type="submit" data-testid="github-load-repositories-button" disabled={loading}>
          Load repositories
        </button>
      </form>

      {error && <p data-testid="github-repository-error">{error}</p>}

      {searched && repositories.length === 0 && (
        <EmptyState
          title={phrase("noRepositoriesTitle", locale)}
          why={phrase("noRepositoriesWhy", locale)}
          action={<span>{phrase("noRepositoriesAction", locale)}</span>}
        />
      )}

      <ul data-testid="github-repository-list">
        {repositories.map((repository) => (
          <li key={repository.repository_identity}>
            <button
              type="button"
              data-testid={`github-repository-option-${repository.repository_identity}`}
              aria-pressed={selectedRepositoryIdentity === repository.repository_identity}
              onClick={() => handleSelect(repository)}
            >
              {repository.repository_identity}
              {repository.private ? " (private)" : ""}
            </button>
          </li>
        ))}
      </ul>

      {hasNextPage && (
        <button
          type="button"
          data-testid="github-load-more-button"
          disabled={loading}
          onClick={handleLoadMore}
        >
          Load more
        </button>
      )}

      {selectedRepositoryIdentity && (
        <p data-testid="github-repository-selected-notice">
          Selected: {selectedRepositoryIdentity}
        </p>
      )}
    </div>
  );
}
