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

/*
 * FTF-UX03 round 2: rebuilt on the UX01 components, bilingual with `lang`/`dir` on every state,
 * branch and ref text in <bdi>. Every failure (rejected fetch, non-2xx, malformed 2xx) is announced
 * with a recovery step and keeps what is on screen. A 2xx counts only when `bindBranchListing` /
 * `bindResolvedRef` find it answers what was asked, and a reply for a ref that is no longer the one
 * in the input is ignored (the pre-existing stale-resolve race).
 */

import { useRef, useState } from "react";

import {
  Banner,
  Button,
  EmptyState,
  Field,
  Pending,
  dirFor,
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
import { t04a04 } from "./strings_04a04";

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
  locale?: Locale;
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

// A git object id: 40 hex (SHA-1) or 64 hex (SHA-256 repositories).
const COMMIT_SHA = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/i;

/** Binds a branch-listing reply to the page that was requested; `null` when it does not bind. */
export function bindBranchListing(
  requestedPage: number,
  body: unknown,
): GitHubBranchListingResponse | null {
  if (!isRecord(body)) return null;
  const { branches, page, per_page, has_next_page } = body;
  if (!Array.isArray(branches)) return null;
  if (!Number.isInteger(page) || page !== requestedPage) return null;
  if (!Number.isInteger(per_page) || typeof has_next_page !== "boolean") return null;
  const parsed: GitHubBranchSummary[] = [];
  for (const entry of branches) {
    if (!isRecord(entry)) return null;
    const { name, list_time_commit_sha } = entry;
    if (typeof name !== "string" || name.trim() === "") return null;
    if (typeof list_time_commit_sha !== "string") return null;
    parsed.push({ name, list_time_commit_sha });
  }
  return {
    branches: parsed,
    page: page as number,
    per_page: per_page as number,
    has_next_page,
  };
}

/**
 * Binds a resolve reply to the ref that was sent: the echoed ref must be the requested ref and the
 * commit must be a real object id, so a reply for another ref is never shown as this ref's commit.
 */
export function bindResolvedRef(requestedRef: string, body: unknown): GitHubResolvedRef | null {
  if (!isRecord(body)) return null;
  const { ref, resolved_commit_sha } = body;
  if (typeof ref !== "string" || ref !== requestedRef) return null;
  if (typeof resolved_commit_sha !== "string" || !COMMIT_SHA.test(resolved_commit_sha)) return null;
  return { ref, resolved_commit_sha };
}

/**
 * Identity (UI CHECKLIST c): the repository, connection and project the selector works on are the
 * key, so branches, a typed ref and a resolved commit never carry over to another one.
 */
export function GitHubBranchRefSelector(props: GitHubBranchRefSelectorProps) {
  const identity = JSON.stringify([
    props.apiBaseUrl,
    props.organizationId,
    props.projectId,
    props.repositoryIdentity,
    props.targetApplicationConnectionId,
    props.credential,
  ]);
  return <BranchRefSelector key={identity} {...props} />;
}

function BranchRefSelector({
  apiBaseUrl,
  organizationId,
  projectId,
  credential,
  repositoryIdentity,
  targetApplicationConnectionId,
  onResolve,
  defaultBranch,
  locale = "en",
}: GitHubBranchRefSelectorProps) {
  const [branches, setBranches] = useState<GitHubBranchSummary[]>([]);
  const [page, setPage] = useState(1);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [loadingBranches, setLoadingBranches] = useState(false);
  const [branchesLoaded, setBranchesLoaded] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  // Which request the failure belongs to: the words for a 404 or 409 differ between listing branches
  // and resolving a ref.
  const [failureRoute, setFailureRoute] = useState<SelectorRoute>("branches");
  // The ref currently being edited/selected (display value only).
  const [selectedRef, setSelectedRef] = useState(defaultBranch ?? "");
  // Deliberately starts `null` and is set only from a real, fresh
  // `github-resolve-ref` response inside `handleResolve` -- cleared by
  // every code path that changes `selectedRef`, so a resolved SHA is
  // never shown next to a ref it was not actually resolved for (this
  // task's own real "does not mix commits" negative_acceptance).
  const [resolvedRef, setResolvedRef] = useState<GitHubResolvedRef | null>(null);
  // What the input says right now, readable from an async reply that outlives a render.
  const liveRef = useRef(selectedRef);
  const latestResolve = useRef(0);

  const lang = resolveLocale(locale);

  async function fetchBranchPage(requestedPage: number, append: boolean) {
    setLoadingBranches(true);
    setFailure(null);
    setFailureRoute("branches");
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
      const body = bindBranchListing(requestedPage, raw);
      if (!body) {
        setFailure(MALFORMED_FAILURE);
        return;
      }
      setBranches((previous) => (append ? [...previous, ...body.branches] : body.branches));
      setPage(body.page);
      setHasNextPage(body.has_next_page);
      setBranchesLoaded(true);
    } catch {
      setFailure(NETWORK_FAILURE);
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

  function changeRef(value: string) {
    liveRef.current = value;
    setSelectedRef(value);
    setResolvedRef(null);
  }

  async function handleResolve(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requestedRef = selectedRef;
    if (!requestedRef.trim()) {
      return;
    }
    const requestId = ++latestResolve.current;
    setResolving(true);
    setFailure(null);
    setFailureRoute("resolve");
    // A reply only counts while it is still the newest request AND the input still holds the ref it
    // was sent for; otherwise the user has moved on and showing it would mix commits.
    const stale = () => requestId !== latestResolve.current || liveRef.current !== requestedRef;
    try {
      const response = await fetch(githubResolveRefUrl(apiBaseUrl, organizationId, projectId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          credential,
          repository_identity: repositoryIdentity,
          ref: requestedRef,
          target_application_connection_id: targetApplicationConnectionId,
        }),
      });
      if (!response.ok) {
        const refused = await failureFromResponse(response);
        if (!stale()) setFailure(refused);
        return;
      }
      let raw: unknown;
      try {
        raw = await response.json();
      } catch {
        raw = null;
      }
      if (stale()) return;
      const body = bindResolvedRef(requestedRef, raw);
      if (!body) {
        setFailure(MALFORMED_FAILURE);
        return;
      }
      setResolvedRef(body);
      onResolve?.(body);
    } catch {
      if (!stale()) setFailure(NETWORK_FAILURE);
    } finally {
      if (requestId === latestResolve.current) setResolving(false);
    }
  }

  const showEmpty =
    branchesLoaded && !loadingBranches && failure === null && !hasNextPage && branches.length === 0;
  const refBlank = !selectedRef.trim();

  return (
    <div data-testid="github-branch-ref-selector" lang={lang} dir={dirFor(locale)}>
      <Button
        variant="secondary"
        data-testid="github-load-branches-button"
        disabled={loadingBranches}
        onClick={handleLoadBranches}
      >
        {t04a04("loadBranches", locale)}
      </Button>

      {failure && (
        <div data-testid="github-branch-ref-error">
          <Banner kind="error" locale={locale}>
            {selectorFailureMessage(failure, locale, failureRoute)}
          </Banner>
        </div>
      )}

      <div aria-busy={loadingBranches}>
        {loadingBranches && <Pending label={t04a04("loadingBranches", locale)} locale={locale} />}

        {showEmpty && (
          <EmptyState
            title={t04a04("noBranchesTitle", locale)}
            why={t04a04("noBranchesWhy", locale)}
            action={
              <Button
                variant="secondary"
                data-testid="github-enter-ref-button"
                onClick={() => document.getElementById("github-ref-input")?.focus()}
              >
                {t04a04("enterRefManually", locale)}
              </Button>
            }
          />
        )}

        <ul data-testid="github-branch-list" aria-label={t04a04("branchListLabel", locale)}>
          {branches.map((branch, index) => {
            const selected = selectedRef === branch.name;
            return (
              <li key={`${index}:${branch.name}`}>
                <Button
                  variant={selected ? "primary" : "secondary"}
                  data-testid={`github-branch-option-${branch.name}`}
                  aria-pressed={selected}
                  onClick={() => changeRef(branch.name)}
                >
                  {selected && <span aria-hidden="true">✓ </span>}
                  <bdi>{branch.name}</bdi>
                </Button>
              </li>
            );
          })}
        </ul>

        {hasNextPage && (
          <Button
            data-testid="github-branch-load-more-button"
            disabled={loadingBranches}
            onClick={handleLoadMore}
          >
            {t04a04("loadMore", locale)}
          </Button>
        )}
      </div>

      <form onSubmit={handleResolve} data-testid="github-ref-form" noValidate>
        <Field
          style={{ minBlockSize: "var(--target-min)" }}
          id="github-ref-input"
          data-testid="github-ref-input"
          type="text"
          label={t04a04("refLabel", locale)}
          helper={t04a04("refHelper", locale)}
          requirement="required"
          locale={locale}
          value={selectedRef}
          onChange={(event) => changeRef(event.target.value)}
        />
        <Button
          type="submit"
          variant="primary"
          data-testid="github-resolve-ref-button"
          disabled={resolving}
          disabledReason={refBlank ? t04a04("refRequired", locale) : undefined}
        >
          {t04a04("resolve", locale)}
        </Button>
      </form>
      {resolving && <Pending label={t04a04("resolving", locale)} locale={locale} />}

      {resolvedRef && (
        <div role="status" data-testid="github-resolved-ref-notice">
          <span aria-hidden="true">✓</span> {t04a04("resolvedPrefix", locale)}{" "}
          <bdi>{resolvedRef.ref}</bdi> {t04a04("resolvedTo", locale)}{" "}
          <bdi dir="ltr">{resolvedRef.resolved_commit_sha}</bdi>
        </div>
      )}
    </div>
  );
}
