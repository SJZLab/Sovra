// @vitest-environment jsdom
//
// FTF-04A04 -- GitHub branch/ref selector + immutable SHA resolver UI.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `GitHubBranchRefSelector`, drives real user
// interaction (loading branches, selecting one, typing a custom ref,
// resolving) and asserts on the real `fetch` calls it makes against the
// live FTF-04A04 API contract -- request method, URL and JSON body --
// never a snapshot or a hand-written HTML string. `fetch` itself is
// mocked (no live server in a unit test), but the component code path
// exercised is the real one that would hit the actual backend
// (`github_branch_ref_routes.py`).
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E -- same precedent `ftf_04a03.spec.ts` already established.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  GitHubBranchRefSelector,
  githubBranchesUrl,
  githubResolveRefUrl,
} from "../components/target-application/github_branch_ref_selector_immutable_sha_resolver";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const CONNECTION_ID = "33333333-3333-3333-3333-333333333333";
const REPO = "acme/agent-repo";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderSelector(onResolve?: (r: unknown) => void) {
  return render(
    createElement(GitHubBranchRefSelector, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      credential: "ghp_realtoken",
      repositoryIdentity: REPO,
      targetApplicationConnectionId: CONNECTION_ID,
      onResolve,
    }),
  );
}

test("URL helpers point at the real FTF-04A04 API contract", () => {
  expect(githubBranchesUrl(API_BASE, ORG_ID, PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}` +
      "/target-connections/github-branches",
  );
  expect(githubResolveRefUrl(API_BASE, ORG_ID, PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}` +
      "/target-connections/github-resolve-ref",
  );
});

test("loading branches POSTs the real payload and renders the real branches returned", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      branches: [
        { name: "main", list_time_commit_sha: "a".repeat(40) },
        { name: "release/1.0", list_time_commit_sha: "b".repeat(40) },
      ],
      page: 1,
      per_page: 30,
      has_next_page: false,
    }),
  });

  renderSelector();
  fireEvent.click(screen.getByTestId("github-load-branches-button"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [url, init] = fetchMock.mock.calls[0]!;
  expect(url).toBe(githubBranchesUrl(API_BASE, ORG_ID, PROJECT_ID));
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body as string)).toEqual({
    credential: "ghp_realtoken",
    repository_identity: REPO,
    page: 1,
  });

  await waitFor(() => expect(screen.getByTestId("github-branch-option-main")).toBeTruthy());
  expect(screen.getByTestId("github-branch-list").children.length).toBe(2);
  // negative_acceptance: no branch is auto-selected merely by loading.
  expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
  expect(screen.getByTestId("github-branch-option-main").getAttribute("aria-pressed")).toBe(
    "false",
  );
});

test("selecting a branch then resolving calls github-resolve-ref fresh, never reusing the list-time SHA", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      branches: [{ name: "main", list_time_commit_sha: "a".repeat(40) }],
      page: 1,
      per_page: 30,
      has_next_page: false,
    }),
  });

  const onResolve = vi.fn();
  renderSelector(onResolve);
  fireEvent.click(screen.getByTestId("github-load-branches-button"));
  await waitFor(() => expect(screen.getByTestId("github-branch-option-main")).toBeTruthy());

  fireEvent.click(screen.getByTestId("github-branch-option-main"));
  expect(screen.getByTestId("github-branch-option-main").getAttribute("aria-pressed")).toBe("true");
  expect((screen.getByTestId("github-ref-input") as HTMLInputElement).value).toBe("main");

  // The real, current commit SHA differs from the stale list-time SHA --
  // proving the component never trusts `list_time_commit_sha` as the
  // resolved identity.
  const freshSha = "c".repeat(40);
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ ref: "main", resolved_commit_sha: freshSha }),
  });

  fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const [url, init] = fetchMock.mock.calls[1]!;
  expect(url).toBe(githubResolveRefUrl(API_BASE, ORG_ID, PROJECT_ID));
  expect(JSON.parse(init.body as string)).toEqual({
    credential: "ghp_realtoken",
    repository_identity: REPO,
    ref: "main",
    target_application_connection_id: CONNECTION_ID,
  });

  await waitFor(() =>
    expect(screen.getByTestId("github-resolved-ref-notice").textContent).toContain(freshSha),
  );
  expect(screen.getByTestId("github-resolved-ref-notice").textContent).not.toContain(
    "a".repeat(40),
  );
  expect(onResolve).toHaveBeenCalledWith({ ref: "main", resolved_commit_sha: freshSha });
});

test("negative_acceptance: changing the selected ref after a resolution clears the stale resolved SHA immediately", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      branches: [
        { name: "main", list_time_commit_sha: "a".repeat(40) },
        { name: "develop", list_time_commit_sha: "d".repeat(40) },
      ],
      page: 1,
      per_page: 30,
      has_next_page: false,
    }),
  });
  renderSelector();
  fireEvent.click(screen.getByTestId("github-load-branches-button"));
  await waitFor(() => expect(screen.getByTestId("github-branch-option-main")).toBeTruthy());

  fireEvent.click(screen.getByTestId("github-branch-option-main"));
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ ref: "main", resolved_commit_sha: "c".repeat(40) }),
  });
  fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
  await waitFor(() => expect(screen.getByTestId("github-resolved-ref-notice")).toBeTruthy());

  // Selecting a *different* branch must never leave the old branch's own
  // resolved SHA visible next to the new selection -- this is the real
  // UI-level half of "does not mix commits".
  fireEvent.click(screen.getByTestId("github-branch-option-develop"));
  expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();

  // Typing a free-form ref must clear it too.
  fireEvent.click(screen.getByTestId("github-branch-option-main"));
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ ref: "main", resolved_commit_sha: "c".repeat(40) }),
  });
  fireEvent.click(screen.getByTestId("github-resolve-ref-button"));
  await waitFor(() => expect(screen.getByTestId("github-resolved-ref-notice")).toBeTruthy());
  fireEvent.change(screen.getByTestId("github-ref-input"), { target: { value: "v1.2.3" } });
  expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
});

test("negative_acceptance: an unknown/inaccessible ref surfaces the real server-provided error, never a fabricated SHA", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  renderSelector();
  fireEvent.change(screen.getByTestId("github-ref-input"), {
    target: { value: "does-not-exist" },
  });
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status: 404,
    json: async () => ({ detail: "GitHub could not find the requested resource (HTTP 404)" }),
  });
  fireEvent.click(screen.getByTestId("github-resolve-ref-button"));

  await waitFor(() =>
    expect(screen.getByTestId("github-branch-ref-error").textContent).toBe(
      "GitHub could not find the requested resource (HTTP 404)",
    ),
  );
  expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
});

test("the Resolve button is disabled until a ref is present, and typing a ref never auto-resolves", async () => {
  renderSelector();
  expect((screen.getByTestId("github-resolve-ref-button") as HTMLButtonElement).disabled).toBe(
    true,
  );
  fireEvent.change(screen.getByTestId("github-ref-input"), { target: { value: "main" } });
  expect((screen.getByTestId("github-resolve-ref-button") as HTMLButtonElement).disabled).toBe(
    false,
  );
  expect(fetch).not.toHaveBeenCalled();
});

// FTF-UX03 (audit #14): the connection's default branch pre-fills the ref, never resolves it.
test("UX03: defaultBranch pre-fills the ref input but nothing is resolved until the user submits", () => {
  render(
    createElement(GitHubBranchRefSelector, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      credential: "ghp_realtoken",
      repositoryIdentity: REPO,
      targetApplicationConnectionId: CONNECTION_ID,
      defaultBranch: "trunk",
    }),
  );
  expect((screen.getByTestId("github-ref-input") as HTMLInputElement).value).toBe("trunk");
  expect(screen.queryByTestId("github-resolved-ref-notice")).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

test("UX03: without defaultBranch the ref input starts empty", () => {
  renderSelector();
  expect((screen.getByTestId("github-ref-input") as HTMLInputElement).value).toBe("");
});
