// @vitest-environment jsdom
//
// Wiring: the front door "/" and every journey page that mounts a certified UI component.
// Each case walks the whole path a user takes: it renders the real "/" page, fills the journey's form
// by its visible labels, builds the page's query ONLY from what the form submits (names, method GET),
// renders the real journey page with that query and checks, with only `fetch` and the Next router
// stubbed, that
//   - the page title and the component's own element are rendered,
//   - the component sends exactly the expected requests (full URLs, no extras), and
//   - nothing is hardcoded: two different value sets and two different API hosts are run.
// Faults this reddens: a placeholder for the component, a journey slug or label or parameter name
// changed, a swapped or dropped value, a hardcoded host or value, a wrong or extra route, a form
// whose method, input name or submit button is wrong, a journey missing from "/".
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { JOURNEYS } from "../lib/journeys";
import Home from "../pages/index";

type Query = Record<string, string | string[] | undefined>;
const router = vi.hoisted(() => ({ isReady: true, query: {} as Query }));

vi.mock("next/router", () => ({ useRouter: () => router }));
vi.mock("next/head", () => ({
  default: ({ children }: { children?: unknown }) =>
    createElement("div", { "data-testid": "head" }, children as never),
}));

const SETS = [
  {
    host: "http://api-a.test.invalid:8123",
    v: {
      organizationId: "aaaaaaaa-0000-4000-8000-000000000001",
      projectId: "bbbbbbbb-0000-4000-8000-000000000002",
      manifestId: "cccccccc-0000-4000-8000-000000000003",
      datasetId: "dddddddd-0000-4000-8000-000000000004",
      suiteId: "eeeeeeee-0000-4000-8000-000000000005",
      connectionId: "ffffffff-0000-4000-8000-000000000006",
      modelVersionId: "abababab-0000-4000-8000-000000000007",
      modelName: "model-x",
      securityProfile: "strict",
    } as Record<string, string>,
  },
  {
    host: "http://api-b.test.invalid:9911",
    v: {
      organizationId: "a1a1a1a1-1111-4111-8111-111111111111",
      projectId: "b2b2b2b2-2222-4222-8222-222222222222",
      manifestId: "c3c3c3c3-3333-4333-8333-333333333333",
      datasetId: "d4d4d4d4-4444-4444-8444-444444444444",
      suiteId: "e5e5e5e5-5555-4555-8555-555555555555",
      connectionId: "f6f6f6f6-6666-4666-8666-666666666666",
      modelVersionId: "a7a7a7a7-7777-4777-8777-777777777777",
      modelName: "model-y",
      securityProfile: "standard",
    } as Record<string, string>,
  },
];

type Case = {
  slug: string;
  title: string;
  params: Array<[string, string]>; // [query name, visible label], exactly as the front door shows
  mount: () => HTMLElement; // the component's own rendered element
  trigger?: () => void; // control the user uses to send the first request, when not made on mount
  urls: (v: Record<string, string>) => string[]; // exact request paths (with query string)
};

const org = (v: Record<string, string>) => `/api/v1/organizations/${v.organizationId}`;
const proj = (v: Record<string, string>) => `${org(v)}/projects/${v.projectId}`;
const ORG_PROJECT: Array<[string, string]> = [
  ["organizationId", "Organization ID"],
  ["projectId", "Project ID"],
];

const CASES: Case[] = [
  {
    slug: "artifact-mapping",
    title: "Map a model to a trainable artifact",
    params: [
      ["organizationId", "Organization ID"],
      ["connectionId", "Connection ID"],
      ["modelName", "Model name"],
    ],
    mount: () => screen.getByTestId("model-to-artifact-mapping-ui"),
    trigger: () => fireEvent.click(screen.getByTestId("load-mapping-button")),
    urls: (v) => [
      `${org(v)}/connections/litellm/${v.connectionId}/models/${v.modelName}/training-artifact`,
    ],
  },
  {
    slug: "requirements",
    title: "Project requirements",
    params: [...ORG_PROJECT, ["securityProfile", "Security profile"]],
    mount: () => screen.getByTestId("requirements-panel"),
    urls: (v) => [`${proj(v)}/requirements`, `${proj(v)}/budget-policy`],
  },
  {
    slug: "manifest-review",
    title: "Manifest review",
    params: [...ORG_PROJECT, ["manifestId", "Manifest ID"]],
    mount: () => screen.getByTestId("manifest-review-ui"),
    urls: (v) => [
      `${proj(v)}/manifests/${v.manifestId}`,
      `${proj(v)}/manifests/${v.manifestId}/evidence`,
    ],
  },
  {
    slug: "mandate-editor",
    title: "Mandate editor",
    params: [...ORG_PROJECT, ["manifestId", "Manifest ID"]],
    mount: () => screen.getByTestId("mandate-editor-ui"),
    urls: (v) => [`${proj(v)}/manifests/${v.manifestId}`, `${proj(v)}/requirements`],
  },
  {
    slug: "dataset-source",
    title: "Dataset source and purpose authorization",
    params: ORG_PROJECT,
    mount: () => screen.getByTestId("dataset-source-selector"),
    urls: (v) => [`${proj(v)}/dataset-sources`],
  },
  {
    slug: "dataset-issues",
    title: "Dataset issue resolution",
    params: [...ORG_PROJECT, ["datasetId", "Dataset ID"]],
    mount: () => screen.getByRole("region", { name: "Dataset issue resolution" }),
    trigger: () => fireEvent.click(screen.getByRole("button", { name: "Load quality report" })),
    urls: (v) => [
      `${proj(v)}/datasets/${v.datasetId}/quality-report`,
      `${proj(v)}/datasets/${v.datasetId}/issues?limit=50`,
      `${proj(v)}/datasets/${v.datasetId}/issue-resolutions?limit=50`,
    ],
  },
  {
    // FTF-H011-P4: the mapping wizard lists the dataset's saved versions on mount.
    slug: "dataset-mapping",
    title: "Dataset preview, field mapping and approval",
    params: [...ORG_PROJECT, ["datasetId", "Dataset ID"]],
    mount: () => screen.getByTestId("dataset-mapping-wizard"),
    urls: (v) => [`${proj(v)}/datasets/${v.datasetId}/mappings?limit=200`],
  },
  {
    slug: "evaluation-cases",
    title: "Evaluation case editor and import",
    params: [...ORG_PROJECT, ["suiteId", "Suite ID"]],
    mount: () => screen.getByRole("region", { name: "Evaluation cases" }),
    trigger: () => fireEvent.click(screen.getByTestId("load-cases-button")),
    urls: (v) => [`${proj(v)}/evaluation-suites/${v.suiteId}/cases`],
  },
  {
    // FTF-H010-P6: the panel loads the FTF-1001 item and the FTF-H010-P4 roles and actions on mount.
    slug: "model-version",
    title: "Model version: lineage, roles and actions",
    params: [...ORG_PROJECT, ["modelVersionId", "Model version ID"]],
    mount: () => screen.getByTestId("model-version-panel"),
    urls: (v) => [
      `${proj(v)}/model-versions/${v.modelVersionId}`,
      `${proj(v)}/model-versions/${v.modelVersionId}/roles`,
      `${proj(v)}/model-versions/${v.modelVersionId}/actions`,
    ],
  },
  {
    slug: "github-repositories",
    title: "GitHub repositories",
    params: ORG_PROJECT,
    mount: () => screen.getByTestId("github-repository-selector"),
    trigger: () => {
      fireEvent.change(screen.getByTestId("github-credential-input"), {
        target: { value: "ghp_pagewiring" },
      });
      fireEvent.click(screen.getByTestId("github-load-repositories-button"));
    },
    urls: (v) => [`${proj(v)}/target-connections/github-repositories`],
  },
];

beforeEach(() => {
  router.isReady = true;
  router.query = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const fetchMock = () => fetch as unknown as ReturnType<typeof vi.fn>;
const requested = () => fetchMock().mock.calls.map((call) => String((call as unknown[])[0]));
const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

// Render "/" and submit one journey's form the way a browser does: fill each input by its visible
// label, then read the query string from what the form would actually send.
function submitFrontDoor(c: Case, values: Record<string, string>): Query {
  const { container } = render(createElement(Home));
  const form = container.querySelector(`form[action="/${c.slug}"]`) as HTMLFormElement | null;
  expect(form, `front door has no form for /${c.slug}`).toBeTruthy();
  const f = form as HTMLFormElement;
  expect(f.method, `/${c.slug}: form method`).toBe("get");
  expect(f.querySelector('button[type="submit"]'), `/${c.slug}: no submit button`).toBeTruthy();
  expect(f.querySelectorAll("input").length, `/${c.slug}: input count`).toBe(c.params.length);
  for (const [name, label] of c.params) {
    const input = within(f).getByLabelText(label) as HTMLInputElement;
    expect(input.name, `/${c.slug}: input named for "${label}"`).toBe(name);
    expect(input.required, `/${c.slug}: "${label}" must be required`).toBe(true);
    fireEvent.change(input, { target: { value: values[name] } });
  }
  const query: Query = {};
  new FormData(f).forEach((value, key) => {
    query[key] = String(value);
  });
  cleanup();
  return query;
}

async function openPage(slug: string) {
  const mod = await import(`../pages/${slug}.tsx`);
  return render(createElement(mod.default));
}

for (const c of CASES) {
  for (const set of SETS) {
    test(`[WIRE-PAGE] ${c.slug}: "/" form to page to exact requests (${new URL(set.host).port})`, async () => {
      vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", set.host);
      router.query = submitFrontDoor(c, set.v);
      expect(Object.keys(router.query).sort()).toEqual(c.params.map(([name]) => name).sort());

      await openPage(c.slug);
      expect(screen.getByTestId("head").textContent, `${c.slug}: page title`).toBe(
        `${c.title} · Sovra Factory`,
      );
      expect(c.mount(), `${c.slug}: component not rendered`).toBeTruthy();
      c.trigger?.();

      const expected = c.urls(set.v).map((path) => `${set.host}${path}`);
      await waitFor(() =>
        expect(fetchMock().mock.calls.length).toBeGreaterThanOrEqual(expected.length),
      );
      await settle();
      expect(requested().sort(), `${c.slug}: requests`).toEqual([...expected].sort());
    });
  }
}

test("[WIRE-PAGE] requirements shows the security profile it was given", async () => {
  const c = CASES[1] as Case;
  for (const set of SETS) {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", set.host);
    router.query = submitFrontDoor(c, set.v);
    await openPage(c.slug);
    expect(screen.getByTestId("security-profile-value").textContent).toBe(set.v.securityProfile);
    cleanup();
  }
});

test("[WIRE-FRONT-DOOR] the registry pins every journey's slug, label and parameter list", () => {
  for (const c of CASES) {
    const j = JOURNEYS.find((journey) => journey.slug === c.slug);
    expect(j, `no journey ${c.slug}`).toBeTruthy();
    expect(j?.title).toBe(c.title);
    expect(j?.params.map((p) => [p.name, p.label])).toEqual(c.params);
  }
});

test("[WIRE-FRONT-DOOR] / offers a form with every parameter for every journey", () => {
  const { container } = render(createElement(Home));
  for (const j of JOURNEYS) {
    const form = container.querySelector(`form[action="/${j.slug}"]`) as HTMLFormElement | null;
    expect(form, `front door has no form for /${j.slug}`).toBeTruthy();
    expect((form as HTMLFormElement).method).toBe("get");
    expect((form as HTMLFormElement).querySelector('button[type="submit"]')).toBeTruthy();
    for (const p of j.params) {
      const input = within(form as HTMLElement).getByLabelText(p.label) as HTMLInputElement;
      expect(input.name, `/${j.slug}: ${p.label}`).toBe(p.name);
    }
  }
});

test("[WIRE-SHELL] a missing journey value is shown, never defaulted, and nothing is requested", async () => {
  const set = SETS[0]!;
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", set.host);
  router.query = {
    organizationId: set.v.organizationId,
    projectId: set.v.projectId,
  };
  await openPage("dataset-issues");
  expect(screen.getByRole("alert").textContent).toContain("Missing Dataset ID");
  await settle();
  expect(fetchMock()).not.toHaveBeenCalled();
});

test("[WIRE-SHELL] an unset NEXT_PUBLIC_API_BASE_URL is shown and nothing is requested", async () => {
  const set = SETS[0]!;
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
  router.query = { organizationId: set.v.organizationId, projectId: set.v.projectId };
  await openPage("dataset-source");
  expect(screen.getByRole("alert").textContent).toContain("NEXT_PUBLIC_API_BASE_URL");
  await settle();
  expect(fetchMock()).not.toHaveBeenCalled();
});

test("[WIRE-SHELL] before the router is ready the page loads and requests nothing", async () => {
  const set = SETS[0]!;
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", set.host);
  router.isReady = false;
  router.query = { organizationId: set.v.organizationId, projectId: set.v.projectId };
  await openPage("dataset-source");
  expect(screen.getByText("Loading…")).toBeTruthy();
  await settle();
  expect(fetchMock()).not.toHaveBeenCalled();
});

test("[WIRE-SHELL] a repeated query key uses its first value", async () => {
  const set = SETS[0]!;
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", set.host);
  router.query = {
    organizationId: [set.v.organizationId!, "ffffffff-9999-4999-8999-999999999999"],
    projectId: [set.v.projectId!, "ffffffff-8888-4888-8888-888888888888"],
  };
  await openPage("dataset-source");
  await waitFor(() => expect(fetchMock().mock.calls.length).toBe(1));
  expect(requested()).toEqual([`${set.host}${proj(set.v)}/dataset-sources`]);
});
