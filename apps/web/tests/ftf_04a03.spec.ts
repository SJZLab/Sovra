// @vitest-environment jsdom
//
// FTF-04A03 -- GitHub accessible-repository selector UI.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `GitHubAccessibleRepositorySelector`, drives
// real user interaction (typing a token, submitting, clicking "Load
// more", clicking a repository) and asserts on the real `fetch` calls it
// makes against the live FTF-04A03 API contract -- request method, URL
// and JSON body -- never a snapshot or a hand-written HTML string.
// `fetch` itself is mocked (no live server in a unit test), but the
// component code path exercised is the real one that would hit the
// actual backend (`github_repository_listing_routes.py`).
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E (no Playwright here -- see the component's own docstring
// for why: no Next.js app shell exists yet to navigate within, the same
// precedent `ftf_0401.spec.ts` already established).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  GitHubAccessibleRepositorySelector,
  githubRepositoriesUrl,
} from "../components/target-application/github_accessible_repository_selector_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function typeToken(value: string) {
  fireEvent.change(screen.getByTestId("github-credential-input"), { target: { value } });
}

test("URL helper points at the real FTF-04A03 API contract", () => {
  expect(githubRepositoriesUrl(API_BASE, ORG_ID, PROJECT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}` +
      "/target-connections/github-repositories",
  );
});

test("submitting the token form POSTs the real payload and renders the real repositories returned", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      repositories: [
        { repository_identity: "acme/repo-one", private: false, default_branch: "main" },
        { repository_identity: "acme/repo-two", private: true, default_branch: "trunk" },
      ],
      page: 1,
      per_page: 30,
      has_next_page: false,
    }),
  });

  render(
    createElement(GitHubAccessibleRepositorySelector, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  typeToken("ghp_realtoken");
  fireEvent.click(screen.getByTestId("github-load-repositories-button"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const [url, init] = fetchMock.mock.calls[0]!;
  expect(url).toBe(githubRepositoriesUrl(API_BASE, ORG_ID, PROJECT_ID));
  expect(init.method).toBe("POST");
  const body = JSON.parse(init.body as string);
  expect(body).toEqual({ credential: "ghp_realtoken", page: 1 });

  await waitFor(() =>
    expect(screen.getByTestId("github-repository-option-acme/repo-one")).toBeTruthy(),
  );
  expect(screen.getByTestId("github-repository-option-acme/repo-two").textContent).toContain(
    "(private)",
  );
  // acceptance: only the real credential-accessible repositories the
  // backend returned are rendered -- nothing else, no client-side default
  // list, no synthesized/mock entries.
  expect(screen.getByTestId("github-repository-list").children.length).toBe(2);
  // negative_acceptance: no result is auto-selected merely by loading.
  expect(screen.queryByTestId("github-repository-selected-notice")).toBeNull();
  expect(
    screen.getByTestId("github-repository-option-acme/repo-one").getAttribute("aria-pressed"),
  ).toBe("false");
});

test("negative_acceptance: no repository is ever auto-selected -- selection requires a real explicit click", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      repositories: [
        { repository_identity: "acme/only-repo", private: false, default_branch: "main" },
      ],
      page: 1,
      per_page: 30,
      has_next_page: false,
    }),
  });

  const onSelect = vi.fn();
  render(
    createElement(GitHubAccessibleRepositorySelector, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      onSelect,
    }),
  );

  typeToken("ghp_realtoken");
  fireEvent.click(screen.getByTestId("github-load-repositories-button"));
  await waitFor(() =>
    expect(screen.getByTestId("github-repository-option-acme/only-repo")).toBeTruthy(),
  );

  // Even with exactly one real result, nothing is selected until a real click.
  expect(onSelect).not.toHaveBeenCalled();
  expect(screen.queryByTestId("github-repository-selected-notice")).toBeNull();

  fireEvent.click(screen.getByTestId("github-repository-option-acme/only-repo"));
  expect(onSelect).toHaveBeenCalledTimes(1);
  expect(onSelect).toHaveBeenCalledWith({
    repository_identity: "acme/only-repo",
    private: false,
    default_branch: "main",
  });
  await waitFor(() =>
    expect(screen.getByTestId("github-repository-selected-notice").textContent).toContain(
      "acme/only-repo",
    ),
  );
  expect(
    screen.getByTestId("github-repository-option-acme/only-repo").getAttribute("aria-pressed"),
  ).toBe("true");
});

test("Load more fetches the real next page and appends to the existing real list", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      repositories: [
        { repository_identity: "acme/page1-repo", private: false, default_branch: "main" },
      ],
      page: 1,
      per_page: 1,
      has_next_page: true,
    }),
  });

  render(
    createElement(GitHubAccessibleRepositorySelector, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  typeToken("ghp_realtoken");
  fireEvent.click(screen.getByTestId("github-load-repositories-button"));
  await waitFor(() =>
    expect(screen.getByTestId("github-repository-option-acme/page1-repo")).toBeTruthy(),
  );
  expect(screen.getByTestId("github-load-more-button")).toBeTruthy();

  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      repositories: [
        { repository_identity: "acme/page2-repo", private: true, default_branch: "trunk" },
      ],
      page: 2,
      per_page: 1,
      has_next_page: false,
    }),
  });
  fireEvent.click(screen.getByTestId("github-load-more-button"));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const [, secondInit] = fetchMock.mock.calls[1]!;
  const secondBody = JSON.parse(secondInit.body as string);
  expect(secondBody).toEqual({ credential: "ghp_realtoken", page: 2 });

  await waitFor(() =>
    expect(screen.getByTestId("github-repository-option-acme/page2-repo")).toBeTruthy(),
  );
  // acceptance: pagination appends to, never replaces, the already-shown real page.
  expect(screen.getByTestId("github-repository-option-acme/page1-repo")).toBeTruthy();
  expect(screen.getByTestId("github-repository-list").children.length).toBe(2);
  // the real has_next_page: false on the final page removes the control.
  expect(screen.queryByTestId("github-load-more-button")).toBeNull();
});

test("a failed listing surfaces the real server-provided error detail, never a generic swallow", async () => {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status: 401,
    json: async () => ({ detail: "GitHub rejected the credential (HTTP 401)" }),
  });

  render(
    createElement(GitHubAccessibleRepositorySelector, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
    }),
  );

  typeToken("revoked-token");
  fireEvent.click(screen.getByTestId("github-load-repositories-button"));

  await waitFor(() =>
    expect(screen.getByTestId("github-repository-error").textContent).toBe(
      "GitHub rejected the credential (HTTP 401)",
    ),
  );
  expect(screen.getByTestId("github-repository-list").children.length).toBe(0);
});
