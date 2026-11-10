// @vitest-environment jsdom
//
// Wiring: every journey page that mounts a certified UI component must actually RENDER it with the
// journey's query values and reach the real /api/v1 route. wiring.spec.ts only proves a page imports
// a component; this proves the mount. Each case renders the real page and the real component (only
// `fetch` and the Next router are stubbed) and fails if the page stops rendering the component, drops
// the organization / project value, or points at a different route. Fault: replacing a page's
// component with a placeholder `<p>` reddens its case.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const API_BASE = "http://localhost:8000";
const ID = "11111111-1111-1111-1111-111111111111";

vi.mock("next/router", () => ({
  useRouter: () => ({
    isReady: true,
    query: new Proxy(
      {},
      {
        get: (_t, key) => (key === "securityProfile" ? "standard" : key === "modelName" ? "m" : ID),
      },
    ),
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
  marker: string; // data-testid of the component's own root or first element
  // How the first request is triggered: on mount, or by clicking the named control.
  trigger: { onMount: true } | { click: string };
  route: string; // path the first request must target
};

const org = `/api/v1/organizations/${ID}`;
const CASES: Case[] = [
  {
    slug: "artifact-mapping",
    marker: "model-to-artifact-mapping-ui",
    trigger: { click: "load-mapping-button" },
    route: `${org}/`,
  },
  {
    slug: "requirements",
    marker: "requirements-panel",
    trigger: { onMount: true },
    route: `${org}/projects/${ID}/requirements`,
  },
  {
    slug: "manifest-review",
    marker: "manifest-review-ui",
    trigger: { onMount: true },
    route: `${org}/projects/${ID}/manifests/${ID}`,
  },
  {
    slug: "mandate-editor",
    marker: "mandate-editor-ui",
    trigger: { onMount: true },
    route: `${org}/projects/${ID}/manifests/${ID}`,
  },
  {
    slug: "dataset-source",
    marker: "dataset-source-selector",
    trigger: { onMount: true },
    route: `${org}/projects/${ID}/dataset-sources`,
  },
  {
    slug: "evaluation-cases",
    marker: "load-cases-button",
    trigger: { click: "load-cases-button" },
    route: `${org}/projects/${ID}/`,
  },
];

for (const c of CASES) {
  test(`[WIRE-PAGE] ${c.slug} renders its component and calls the real route`, async () => {
    const mod = await import(`../pages/${c.slug}.tsx`);
    render(createElement(mod.default));
    expect(screen.getByTestId(c.marker), `${c.slug}: component not rendered`).toBeTruthy();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    if ("click" in c.trigger) {
      fireEvent.click(screen.getByTestId(c.trigger.click));
    }
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const url = String((fetchMock.mock.calls[0] as unknown[])[0]);
    expect(url.startsWith(`${API_BASE}${c.route}`), `${c.slug}: first request ${url}`).toBe(true);
  });
}

// dataset-issues has no test ids on first paint; its first control is the labelled load button.
test("[WIRE-PAGE] dataset-issues renders its component and calls the real route", async () => {
  const mod = await import("../pages/dataset-issues");
  render(createElement(mod.default));
  expect(screen.getByRole("heading", { name: "Dataset quality issues" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Load quality report" }));
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const url = String((fetchMock.mock.calls[0] as unknown[])[0]);
  expect(url.startsWith(`${API_BASE}${org}/projects/${ID}/`), url).toBe(true);
});

test("[WIRE-PAGE] github-repositories renders the repository selector", async () => {
  const mod = await import("../pages/github-repositories");
  render(createElement(mod.default));
  expect(screen.getByTestId("github-repository-selector")).toBeTruthy();
  expect(screen.getByTestId("github-load-repositories-button")).toBeTruthy();
});
