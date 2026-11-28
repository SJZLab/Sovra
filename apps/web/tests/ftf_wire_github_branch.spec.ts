// @vitest-environment jsdom
//
// Wiring: the "github-branch" journey page. Step 1 (repository selector) hands its credential to
// step 2 (branch selector) through component state only. This test renders the real page, drives
// the real components, and asserts that the credential reaches the branch request body while it
// never appears in a URL. `fetch` is mocked (no server in a unit test); the component path is real.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const CONNECTION_ID = "33333333-3333-3333-3333-333333333333";
const CARRIED_VALUE = "ghp_realtoken_flow";

vi.mock("next/router", () => ({
  useRouter: () => ({
    isReady: true,
    query: {
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      targetApplicationConnectionId: CONNECTION_ID,
    },
  }),
}));
vi.mock("next/head", () => ({ default: () => null }));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", API_BASE);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

test("[WIRE-GH-1] the branch step appears only after an explicit repository click, and receives the credential", async () => {
  const { default: GitHubBranchPage } = await import("../pages/github-branch");
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        repositories: [
          { repository_identity: "acme/service", private: true, default_branch: "trunk" },
        ],
        page: 1,
        per_page: 30,
        has_next_page: false,
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        branches: [{ name: "trunk", list_time_commit_sha: "a".repeat(40) }],
        page: 1,
        per_page: 30,
        has_next_page: false,
      }),
    });

  render(createElement(GitHubBranchPage));

  // Step 2 is not shown before a repository is explicitly chosen.
  expect(screen.queryByTestId("github-branch-ref-selector")).toBeNull();

  fireEvent.change(screen.getByTestId("github-credential-input"), {
    target: { value: CARRIED_VALUE },
  });
  fireEvent.click(screen.getByTestId("github-load-repositories-button"));
  await waitFor(() =>
    expect(screen.getByTestId("github-repository-option-acme/service")).toBeTruthy(),
  );
  expect(screen.queryByTestId("github-branch-ref-selector")).toBeNull();

  fireEvent.click(screen.getByTestId("github-repository-option-acme/service"));
  await waitFor(() => expect(screen.getByTestId("github-branch-ref-selector")).toBeTruthy());

  fireEvent.click(screen.getByTestId("github-load-branches-button"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

  const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
  expect(url).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}` +
      "/target-connections/github-branches",
  );
  const body = JSON.parse(String(init.body));
  expect(body.credential).toBe(CARRIED_VALUE);
  expect(body.repository_identity).toBe("acme/service");
});

test("[WIRE-GH-2] the credential never appears in any URL", async () => {
  const { default: GitHubBranchPage } = await import("../pages/github-branch");
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        repositories: [
          { repository_identity: "acme/service", private: false, default_branch: "main" },
        ],
        page: 1,
        per_page: 30,
        has_next_page: false,
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ branches: [], page: 1, per_page: 30, has_next_page: false }),
    });

  render(createElement(GitHubBranchPage));
  fireEvent.change(screen.getByTestId("github-credential-input"), {
    target: { value: CARRIED_VALUE },
  });
  fireEvent.click(screen.getByTestId("github-load-repositories-button"));
  await waitFor(() =>
    expect(screen.getByTestId("github-repository-option-acme/service")).toBeTruthy(),
  );
  fireEvent.click(screen.getByTestId("github-repository-option-acme/service"));
  await waitFor(() => expect(screen.getByTestId("github-branch-ref-selector")).toBeTruthy());
  fireEvent.click(screen.getByTestId("github-load-branches-button"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

  for (const call of fetchMock.mock.calls as [string, RequestInit][]) {
    expect(call[0]).not.toContain(CARRIED_VALUE);
  }
  expect(window.location.href).not.toContain(CARRIED_VALUE);
  expect(window.location.search).toBe("");
});
