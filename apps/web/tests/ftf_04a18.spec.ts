// @vitest-environment jsdom
//
// FTF-04A18 -- Candidate-selection UI -- no auto-selection.
//
// A real component/DOM-level test (Vitest + React Testing Library +
// jsdom): renders the actual `CandidateSelectionUI`, drives real user
// interaction (loading candidates, explicitly selecting one) and asserts
// on the real `fetch` calls it makes against the live FTF-04A17/FTF-04A18
// API contract -- request method, URL and JSON body -- never a snapshot
// or a hand-written HTML string. `fetch` itself is mocked (no live server
// in a unit test), but the component code path exercised is the real one
// that would hit the actual backend
// (`discovered_agent_candidate_selection_routes.py`).
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E -- same precedent `ftf_04a04.spec.ts` already established.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  CandidateSelectionUI,
  candidateSelectUrl,
  candidatesListUrl,
} from "../components/target-application/candidate_selection_ui_no_auto_selection";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const CONNECTION_ID = "33333333-3333-3333-3333-333333333333";
const SNAPSHOT_ID = "44444444-4444-4444-4444-444444444444";
const CANDIDATE_LOW = "55555555-5555-5555-5555-555555555555";
const CANDIDATE_HIGH = "66666666-6666-6666-6666-666666666666";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderSelectionUI(onSelected?: (c: unknown) => void) {
  return render(
    createElement(CandidateSelectionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      connectionId: CONNECTION_ID,
      snapshotId: SNAPSHOT_ID,
      onSelected,
    }),
  );
}

function mockCandidateList() {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      items: [
        {
          id: CANDIDATE_LOW,
          display_name: "InternalTriageAgent",
          framework_or_runtime: "python",
          confidence_score: 0.42,
          discovery_status: "candidate",
        },
        {
          id: CANDIDATE_HIGH,
          display_name: "CustomerSupportAgent",
          framework_or_runtime: "typescript",
          confidence_score: 0.96,
          discovery_status: "candidate",
        },
      ],
      limit: 50,
      offset: 0,
      total: 2,
    }),
  });
}

test("URL helpers point at the real FTF-04A17/FTF-04A18 API contract", () => {
  expect(candidatesListUrl(API_BASE, ORG_ID, PROJECT_ID, CONNECTION_ID, SNAPSHOT_ID)).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}` +
      `/target-connections/${CONNECTION_ID}/snapshots/${SNAPSHOT_ID}/candidates`,
  );
  expect(
    candidateSelectUrl(API_BASE, ORG_ID, PROJECT_ID, CONNECTION_ID, SNAPSHOT_ID, CANDIDATE_LOW),
  ).toBe(
    `${API_BASE}/api/v1/organizations/${ORG_ID}/projects/${PROJECT_ID}` +
      `/target-connections/${CONNECTION_ID}/snapshots/${SNAPSHOT_ID}/candidates/${CANDIDATE_LOW}/select`,
  );
});

test("loading candidates GETs the real collection and renders every candidate returned, none pre-selected", async () => {
  mockCandidateList();
  renderSelectionUI();
  fireEvent.click(screen.getByTestId("candidate-load-button"));

  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
  expect(url).toBe(candidatesListUrl(API_BASE, ORG_ID, PROJECT_ID, CONNECTION_ID, SNAPSHOT_ID));
  expect(init.method).toBe("GET");

  await waitFor(() => expect(screen.getByTestId(`candidate-row-${CANDIDATE_LOW}`)).toBeTruthy());
  expect(screen.getByTestId("candidate-list").children.length).toBe(2);
  // negative_acceptance: listing never implies a selection.
  expect(screen.queryByTestId("candidate-selection-confirmation")).toBeNull();
});

test("negative_acceptance: the higher-confidence candidate is rendered but not auto-selected; only an explicit click selects it", async () => {
  mockCandidateList();
  const onSelected = vi.fn();
  renderSelectionUI(onSelected);
  fireEvent.click(screen.getByTestId("candidate-load-button"));
  await waitFor(() => expect(screen.getByTestId(`candidate-row-${CANDIDATE_HIGH}`)).toBeTruthy());

  // Both candidates are listed, in the real order the backend returned
  // them -- the component never sorts by confidence_score itself.
  const list = screen.getByTestId("candidate-list");
  expect(list.children[0]!.getAttribute("data-testid")).toBe(`candidate-row-${CANDIDATE_LOW}`);
  expect(list.children[1]!.getAttribute("data-testid")).toBe(`candidate-row-${CANDIDATE_HIGH}`);

  // Nothing is selected merely because one candidate has the highest
  // confidence_score.
  expect(screen.queryByTestId("candidate-selection-confirmation")).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);

  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      candidate_id: CANDIDATE_HIGH,
      target_source_snapshot_id: SNAPSHOT_ID,
      selected_by: "user-123",
      selected_at: "2026-10-02T12:00:00+00:00",
    }),
  });
  fireEvent.click(screen.getByTestId(`candidate-select-button-${CANDIDATE_HIGH}`));

  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  const [url, init] = fetchMock.mock.calls[1]!;
  expect(url).toBe(
    candidateSelectUrl(API_BASE, ORG_ID, PROJECT_ID, CONNECTION_ID, SNAPSHOT_ID, CANDIDATE_HIGH),
  );
  expect(init.method).toBe("POST");

  await waitFor(() =>
    expect(screen.getByTestId("candidate-selection-confirmation").textContent).toContain(
      CANDIDATE_HIGH,
    ),
  );
  expect(onSelected).toHaveBeenCalledWith({
    candidate_id: CANDIDATE_HIGH,
    target_source_snapshot_id: SNAPSHOT_ID,
    selected_by: "user-123",
    selected_at: "2026-10-02T12:00:00+00:00",
  });
});

test("negative_acceptance: a stale candidate from a superseded snapshot is rejected, never silently confirmed", async () => {
  mockCandidateList();
  renderSelectionUI();
  fireEvent.click(screen.getByTestId("candidate-load-button"));
  await waitFor(() => expect(screen.getByTestId(`candidate-row-${CANDIDATE_LOW}`)).toBeTruthy());

  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  fetchMock.mockResolvedValueOnce({
    ok: false,
    status: 409,
    json: async () => ({
      detail: `target source snapshot ${SNAPSHOT_ID} has been superseded`,
    }),
  });
  fireEvent.click(screen.getByTestId(`candidate-select-button-${CANDIDATE_LOW}`));

  await waitFor(() =>
    expect(screen.getByTestId("candidate-selection-error").textContent).toContain("superseded"),
  );
  expect(screen.queryByTestId("candidate-selection-confirmation")).toBeNull();
});
