// @vitest-environment jsdom
//
// Wiring: every journey page that mounts a certified UI component must actually RENDER it, hand it
// each journey value in the right place, and reach the real /api/v1 route. wiring.spec.ts only proves
// a page imports a component; this proves the mount. Each case renders the real page and the real
// component (only `fetch` and the Next router are stubbed). Every query key has its own distinct
// value and a non-default API host, so swapping two values, hardcoding the host, dropping a value or
// pointing at another route changes the asserted request path and reddens the case. The front-door
// test proves "/" offers a form with every parameter for every journey (Law 6).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { JOURNEYS } from "../lib/journeys";

const API_BASE = "http://api.test.invalid:8123";
const V: Record<string, string> = {
  organizationId: "aaaaaaaa-0000-4000-8000-000000000001",
  projectId: "bbbbbbbb-0000-4000-8000-000000000002",
  manifestId: "cccccccc-0000-4000-8000-000000000003",
  datasetId: "dddddddd-0000-4000-8000-000000000004",
  suiteId: "eeeeeeee-0000-4000-8000-000000000005",
  connectionId: "ffffffff-0000-4000-8000-000000000006",
  modelVersionId: "abababab-0000-4000-8000-000000000007",
  modelName: "model-x",
  securityProfile: "strict",
};
const ORG = V.organizationId;
const PRJ = V.projectId;
const proj = `/api/v1/organizations/${ORG}/projects/${PRJ}`;

vi.mock("next/router", () => ({
  useRouter: () => ({
    isReady: true,
    query: new Proxy({}, { get: (_t, key) => V[String(key)] }),
  }),
}));
vi.mock("next/head", () => ({ default: () => null }));

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", API_BASE);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

type Case = {
  slug: string;
  marker: string; // data-testid of the component's own element
  click?: () => void; // control that triggers the first request, when it is not made on mount
  paths: string[]; // every path the first requests must hit, exactly
};

const CASES: Case[] = [
  {
    slug: "artifact-mapping",
    marker: "model-to-artifact-mapping-ui",
    click: () => fireEvent.click(screen.getByTestId("load-mapping-button")),
    paths: [
      `/api/v1/organizations/${ORG}/connections/litellm/${V.connectionId}/models/${V.modelName}/training-artifact`,
    ],
  },
  { slug: "requirements", marker: "requirements-panel", paths: [`${proj}/requirements`] },
  {
    slug: "manifest-review",
    marker: "manifest-review-ui",
    paths: [`${proj}/manifests/${V.manifestId}`],
  },
  {
    slug: "mandate-editor",
    marker: "mandate-editor-ui",
    paths: [`${proj}/manifests/${V.manifestId}`, `${proj}/requirements`],
  },
  { slug: "dataset-source", marker: "dataset-source-selector", paths: [`${proj}/dataset-sources`] },
  {
    slug: "dataset-issues",
    marker: "dataset-issue-resolution-ui",
    click: () => fireEvent.click(screen.getByRole("button", { name: "Load quality report" })),
    paths: [`${proj}/datasets/${V.datasetId}/quality-report`],
  },
  {
    // FTF-H011-P4: the mapping wizard lists the dataset's saved versions on mount.
    slug: "dataset-mapping",
    marker: "dataset-mapping-wizard",
    paths: [`${proj}/datasets/${V.datasetId}/mappings?limit=200`],
  },
  {
    slug: "evaluation-cases",
    marker: "load-cases-button",
    click: () => fireEvent.click(screen.getByTestId("load-cases-button")),
    paths: [`${proj}/evaluation-suites/${V.suiteId}/cases`],
  },
  {
    // FTF-H010-P6: the panel loads the FTF-1001 item and the FTF-H010-P4 roles and actions on mount.
    slug: "model-version",
    marker: "model-version-panel",
    paths: [
      `${proj}/model-versions/${V.modelVersionId}`,
      `${proj}/model-versions/${V.modelVersionId}/roles`,
      `${proj}/model-versions/${V.modelVersionId}/actions`,
    ],
  },
];

for (const c of CASES) {
  test(`[WIRE-PAGE] ${c.slug} renders its component with each value in place`, async () => {
    const mod = await import(`../pages/${c.slug}.tsx`);
    const { container } = render(createElement(mod.default));
    const el = c.marker.startsWith("dataset-issue")
      ? container.querySelector("section[aria-label='Dataset issue resolution']")
      : screen.getByTestId(c.marker);
    expect(el, `${c.slug}: component not rendered`).toBeTruthy();
    c.click?.();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(1));
    const urls = fetchMock.mock.calls.map((call) => String((call as unknown[])[0]));
    for (const path of c.paths) {
      expect(urls, `${c.slug}: expected ${API_BASE}${path}`).toContain(`${API_BASE}${path}`);
    }
  });
}

test("[WIRE-PAGE] requirements shows the security profile it was given", async () => {
  const mod = await import("../pages/requirements");
  render(createElement(mod.default));
  expect(screen.getByTestId("security-profile-value").textContent).toBe(V.securityProfile);
});

test("[WIRE-PAGE] github-repositories renders the selector and lists from the real route", async () => {
  const mod = await import("../pages/github-repositories");
  render(createElement(mod.default));
  expect(screen.getByTestId("github-repository-selector")).toBeTruthy();
  fireEvent.change(screen.getByTestId("github-credential-input"), {
    target: { value: "ghp_pagewiring" },
  });
  fireEvent.click(screen.getByTestId("github-load-repositories-button"));
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(1));
  const url = String((fetchMock.mock.calls[0] as unknown[])[0]);
  expect(url.startsWith(`${API_BASE}${proj}/`), url).toBe(true);
  expect(url).toContain("github");
});

test("[WIRE-FRONT-DOOR] / offers a form with every parameter for every journey", async () => {
  const mod = await import("../pages/index");
  const { container } = render(createElement(mod.default));
  for (const j of JOURNEYS) {
    const form = container.querySelector(`form[action="/${j.slug}"]`) as HTMLFormElement | null;
    expect(form, `front door has no form for /${j.slug}`).toBeTruthy();
    for (const p of j.params) {
      expect(
        within(form as HTMLElement).getByLabelText(p.label),
        `/${j.slug}: no input for ${p.label}`,
      ).toBeTruthy();
    }
  }
});
