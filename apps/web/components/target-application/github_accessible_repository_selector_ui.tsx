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

/*
 * FTF-UX03 round 2: rebuilt on the UX01 components (Button / Field / ErrorSummary / Banner /
 * EmptyState / Pending). Every system string is in both languages (strings_04a03.ts), `lang` and
 * `dir` are set on every state, repository names are rendered in <bdi>, a failed or malformed
 * request is announced with a recovery step and never blanks the screen, and a 2xx only counts when
 * its body binds to what was asked (`bindListing`).
 */

import { useRef, useState } from "react";

import {
  Banner,
  Button,
  EmptyState,
  ErrorSummary,
  Field,
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
  failureMessage,
  isRecord,
  type Failure,
} from "./common";
import { t04a03 } from "./strings_04a03";

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

function parseRepository(value: unknown): GitHubRepositorySummary | null {
  if (!isRecord(value)) return null;
  const { repository_identity, private: isPrivate, default_branch } = value;
  if (typeof repository_identity !== "string" || repository_identity.trim() === "") return null;
  if (typeof isPrivate !== "boolean" || typeof default_branch !== "string") return null;
  return { repository_identity, private: isPrivate, default_branch };
}

/**
 * The one place a listing response is bound to the request that asked for it: a 2xx whose body is
 * not a well-typed listing, or that answers a different page than the one requested, is not a
 * success (UI CHECKLIST a/b/d). Returns `null` for anything else so nothing is rendered from it.
 */
export function bindListing(
  requestedPage: number,
  body: unknown,
): GitHubRepositoryListingResponse | null {
  if (!isRecord(body)) return null;
  const { repositories, page, per_page, has_next_page } = body;
  if (!Array.isArray(repositories)) return null;
  if (!Number.isInteger(page) || page !== requestedPage) return null;
  if (!Number.isInteger(per_page) || typeof has_next_page !== "boolean") return null;
  const parsed: GitHubRepositorySummary[] = [];
  for (const entry of repositories) {
    const repository = parseRepository(entry);
    if (!repository) return null;
    parsed.push(repository);
  }
  return {
    repositories: parsed,
    page: page as number,
    per_page: per_page as number,
    has_next_page,
  };
}

/**
 * Identity (UI CHECKLIST c): everything below the key is remounted when the organization, project
 * or API base changes, so a token, a list or a selection never carries over to another project.
 */
export function GitHubAccessibleRepositorySelector(props: GitHubAccessibleRepositorySelectorProps) {
  const identity = JSON.stringify([props.apiBaseUrl, props.organizationId, props.projectId]);
  return <RepositorySelector key={identity} {...props} />;
}

function RepositorySelector({
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
  const [failure, setFailure] = useState<Failure | null>(null);
  // True only after a real search returned a page successfully (empty state must not
  // show before searching, while loading, or after an error).
  const [searched, setSearched] = useState(false);
  const [submitCount, setSubmitCount] = useState(0);
  const [tokenMissing, setTokenMissing] = useState(false);
  // Explicit selection state -- deliberately starts `null` and is set only
  // inside `handleSelect`, itself only ever invoked from a real click
  // handler below. No other code path in this component assigns it.
  const [selectedRepositoryIdentity, setSelectedRepositoryIdentity] = useState<string | null>(null);
  // A reply for anything but the newest request is ignored, never merged into the list.
  const latestRequest = useRef(0);

  const lang = resolveLocale(locale);

  async function fetchPage(requestedPage: number, append: boolean) {
    const requestId = ++latestRequest.current;
    setLoading(true);
    setFailure(null);
    try {
      const response = await fetch(githubRepositoriesUrl(apiBaseUrl, organizationId, projectId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ credential, page: requestedPage }),
      });
      if (!response.ok) {
        const refused = await failureFromResponse(response);
        if (requestId === latestRequest.current) setFailure(refused);
        return;
      }
      let raw: unknown;
      try {
        raw = await response.json();
      } catch {
        raw = null;
      }
      if (requestId !== latestRequest.current) return;
      const body = bindListing(requestedPage, raw);
      if (!body) {
        // Never a fabricated or half-rendered list: keep what is shown, say what happened.
        setFailure(MALFORMED_FAILURE);
        return;
      }
      setRepositories((previous) =>
        append ? [...previous, ...body.repositories] : body.repositories,
      );
      setPage(body.page);
      setHasNextPage(body.has_next_page);
      setSearched(true);
    } catch {
      if (requestId === latestRequest.current) setFailure(NETWORK_FAILURE);
    } finally {
      if (requestId === latestRequest.current) setLoading(false);
    }
  }

  async function handleLoadRepositories(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitCount((count) => count + 1);
    if (credential.trim() === "") {
      setTokenMissing(true);
      return;
    }
    setTokenMissing(false);
    setSelectedRepositoryIdentity(null);
    await fetchPage(1, false);
  }

  async function handleLoadMore() {
    await fetchPage(page + 1, true);
  }

  function handleSelect(repository: GitHubRepositorySummary) {
    setSelectedRepositoryIdentity(repository.repository_identity);
    onSelect?.(repository);
  }

  function handleDifferentToken() {
    setCredential("");
    setRepositories([]);
    setSearched(false);
    setSelectedRepositoryIdentity(null);
    document.getElementById("github-credential")?.focus();
  }

  const tokenError = tokenMissing ? t04a03("tokenRequired", locale) : undefined;
  // Only a finished, successful search that found nothing and has no further page is "empty".
  const showEmpty =
    searched && !loading && failure === null && !hasNextPage && repositories.length === 0;

  return (
    <div data-testid="github-repository-selector" lang={lang} dir={dirFor(locale)}>
      <ErrorSummary
        errors={tokenError ? [{ fieldId: "github-credential", message: tokenError }] : []}
        locale={locale}
        submitCount={submitCount}
      />
      <form onSubmit={handleLoadRepositories} data-testid="github-credential-form" noValidate>
        <Field
          style={{ minBlockSize: "var(--target-min)" }}
          id="github-credential"
          data-testid="github-credential-input"
          type="password"
          label={t04a03("tokenLabel", locale)}
          helper={phrase("githubTokenHelp", locale)}
          error={tokenError}
          requirement="required"
          locale={locale}
          value={credential}
          onChange={(event) => setCredential(event.target.value)}
        />
        <Button
          type="submit"
          variant="primary"
          data-testid="github-load-repositories-button"
          disabled={loading}
        >
          {t04a03("loadRepositories", locale)}
        </Button>
      </form>

      {failure && (
        <div data-testid="github-repository-error">
          <Banner kind="error" locale={locale}>
            {failureMessage(failure, locale)}
          </Banner>
        </div>
      )}

      <div aria-busy={loading}>
        {loading && <Pending label={t04a03("loadingRepositories", locale)} locale={locale} />}

        {showEmpty && (
          <EmptyState
            title={phrase("noRepositoriesTitle", locale)}
            why={phrase("noRepositoriesWhy", locale)}
            action={
              <Button
                variant="secondary"
                data-testid="github-different-token-button"
                onClick={handleDifferentToken}
              >
                {t04a03("differentToken", locale)}
              </Button>
            }
          />
        )}

        {repositories.length > 0 && (
          <p data-testid="github-repository-list-note">
            {t04a03("listNote", locale, { count: repositories.length })}
          </p>
        )}

        <ul data-testid="github-repository-list" aria-label={t04a03("listLabel", locale)}>
          {repositories.map((repository, index) => {
            const selected = selectedRepositoryIdentity === repository.repository_identity;
            return (
              <li key={`${index}:${repository.repository_identity}`}>
                <Button
                  variant={selected ? "primary" : "secondary"}
                  data-testid={`github-repository-option-${repository.repository_identity}`}
                  aria-pressed={selected}
                  onClick={() => handleSelect(repository)}
                >
                  {selected && <span aria-hidden="true">✓ </span>}
                  <bdi>{repository.repository_identity}</bdi>
                  {repository.private ? ` (${t04a03("privateMark", locale)})` : ""}
                </Button>
              </li>
            );
          })}
        </ul>

        {hasNextPage && (
          <Button data-testid="github-load-more-button" disabled={loading} onClick={handleLoadMore}>
            {t04a03("loadMore", locale)}
          </Button>
        )}
      </div>

      {selectedRepositoryIdentity && (
        <div role="status" data-testid="github-repository-selected-notice">
          <span aria-hidden="true">✓</span> {t04a03("selectedNotice", locale)}{" "}
          <bdi>{selectedRepositoryIdentity}</bdi>
        </div>
      )}
    </div>
  );
}
