// @vitest-environment jsdom
//
// FTF-UX02 round 3, finding 2 (MALFORMED-200, Law 17; QUEUE 0k): FTF-0500A shows a connection only
// when the reply is a real one, and "Authorized" only for `{authorized: true, purpose: <sent>}`.
// One test per case from the verifier's reproduction; each was RED against the round-2 component.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DatasetSourceSelectorPurposeAuthorizationUi } from "../components/dataset-factory/dataset_source_selector_purpose_authorization_ui";

const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const GOOD = {
  id: "33333333-3333-3333-3333-333333333333",
  organization_id: ORG,
  project_id: PROJECT,
  source_type: "s3",
  display_name: "Support tickets export",
  has_credential: true,
  has_connection_config: false,
  allowed_purposes: ["training"],
  security_classification: "confidential",
  connection_status: "active",
  last_validated_at: null,
  created_by: "user-1",
  created_at: "2026-09-01T00:00:00+00:00",
  updated_at: "2026-09-01T00:00:00+00:00",
  revoked_at: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Reply = { ok: boolean; status?: number; json: () => Promise<unknown> };

function stub(handlers: { list?: unknown; create?: Reply; authorize?: Reply }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/authorize$/.test(url)) return handlers.authorize!;
      if (init?.method === "POST") return handlers.create!;
      return { ok: true, status: 200, json: async () => handlers.list ?? [] };
    }),
  );
}

function mountUi(locale: "en" | "ar" = "en") {
  return render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: PROJECT,
      locale,
    }),
  );
}

const MALFORMED_EN = "The server's reply could not be read. You can retry.";

test.each([
  ["a null item", [GOOD, null]],
  ["an empty object", [{}]],
  ["a blank display name", [{ ...GOOD, display_name: "  " }]],
  ["a wrongly typed id", [{ ...GOOD, id: 7 }]],
  ["an array item", [[GOOD]]],
])(
  "[UX02-R3-M1] a list with %s is one retryable alert, never a crash or a blank item",
  async (_n, list) => {
    stub({ list });
    mountUi();
    await waitFor(() => expect(screen.getByTestId("dataset-source-list-error")).toBeTruthy());
    expect(screen.getByTestId("dataset-source-list-error").textContent).toBe(MALFORMED_EN);
    expect(screen.getByTestId("dataset-source-list-retry-button")).toBeTruthy();
    expect(screen.queryByTestId(`dataset-source-option-${GOOD.id}`)).toBeNull();
    expect(screen.getByTestId("dataset-source-selector")).toBeTruthy();
  },
);

test("[UX02-R3-M1b] the malformed-list alert is in Arabic under locale=ar", async () => {
  stub({ list: [null] });
  mountUi("ar");
  await waitFor(() => expect(screen.getByTestId("dataset-source-list-error")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-list-error").textContent).toBe(
    "تعذّرت قراءة رد الخادم. يمكنك إعادة المحاولة.",
  );
});

async function createConnection(reply: Reply) {
  stub({ list: [], create: reply });
  mountUi();
  await waitFor(() => expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy());
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "New one" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "internal" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));
}

test.each([
  ["a 201 null", null],
  ["a 201 empty object", {}],
  ["a 201 with a wrongly typed id", { ...GOOD, id: 3 }],
])(
  "[UX02-R3-M2] %s from create is one alert; no connection is appended or selected",
  async (_n, body) => {
    await createConnection({ ok: true, status: 201, json: async () => body });
    await waitFor(() => expect(screen.getByTestId("dataset-source-create-error")).toBeTruthy());
    expect(screen.getByTestId("dataset-source-create-error").textContent).toBe(MALFORMED_EN);
    expect(screen.queryByTestId("dataset-source-connection-list")?.children.length).toBe(0);
    expect(screen.queryByTestId("dataset-source-authorization-section")).toBeNull();
    // The draft is preserved so the user can retry.
    expect(screen.getByTestId("dataset-source-preview")).toBeTruthy();
  },
);

test("[UX02-R3-M2b] a 201 non-JSON create reply is one alert, not an unhandled rejection", async () => {
  await createConnection({
    ok: true,
    status: 201,
    json: async () => {
      throw new SyntaxError("Unexpected token <");
    },
  });
  await waitFor(() => expect(screen.getByTestId("dataset-source-create-error")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-create-error").textContent).toBe(MALFORMED_EN);
});

async function authorizeWith(reply: Reply) {
  stub({ list: [GOOD], authorize: reply });
  mountUi();
  await waitFor(() => expect(screen.getByTestId(`dataset-source-option-${GOOD.id}`)).toBeTruthy());
  fireEvent.click(screen.getByTestId(`dataset-source-option-${GOOD.id}`));
  fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
    target: { value: "training" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-check-authorization-button"));
}

test.each([
  ["an empty object", { ok: true, status: 200, json: async () => ({}) }],
  ["authorized:false", { ok: true, status: 200, json: async () => ({ authorized: false }) }],
  [
    "authorized:false with the matching purpose",
    {
      ok: true,
      status: 200,
      json: async () => ({ authorized: false, purpose: "training" }),
    },
  ],
  [
    "authorized as a string",
    {
      ok: true,
      status: 200,
      json: async () => ({ authorized: "true", purpose: "training" }),
    },
  ],
  [
    "a different purpose",
    { ok: true, status: 200, json: async () => ({ authorized: true, purpose: "validation" }) },
  ],
  [
    "authorized without a purpose",
    { ok: true, status: 200, json: async () => ({ authorized: true }) },
  ],
  [
    "a non-JSON body",
    {
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    },
  ],
  ["a null body", { ok: true, status: 200, json: async () => null }],
])("[UX02-R3-M3] authorize reply %s is never shown as Authorized", async (_n, reply) => {
  await authorizeWith(reply);
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-authorization-error")).toBeTruthy(),
  );
  expect(screen.getByTestId("dataset-source-authorization-error").textContent).toBe(MALFORMED_EN);
  expect(screen.queryByTestId("dataset-source-authorization-result-authorized")).toBeNull();
  // Not stuck: the button is usable again.
  expect(
    (screen.getByTestId("dataset-source-check-authorization-button") as HTMLButtonElement).disabled,
  ).toBe(false);
});

test("[UX02-R3-M3b] NEGATIVE: the real exact grant is still shown as Authorized", async () => {
  await authorizeWith({
    ok: true,
    status: 200,
    json: async () => ({ authorized: true, purpose: "training" }),
  });
  await waitFor(() =>
    expect(screen.getByTestId("dataset-source-authorization-result-authorized")).toBeTruthy(),
  );
  expect(screen.queryByTestId("dataset-source-authorization-error")).toBeNull();
});

test("[UX02-R3-A9] the empty state's 'New connection' action really moves focus to the display-name field", async () => {
  stub({ list: [] });
  mountUi();
  await waitFor(() => expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy());
  fireEvent.click(screen.getByTestId("dataset-source-empty-state-action"));
  expect(document.activeElement).toBe(screen.getByTestId("dataset-source-display-name-input"));
});

test("[UX02-R3-N1] a successful create focuses the new connection's option and announces it (role=status)", async () => {
  await createConnection({ ok: true, status: 201, json: async () => GOOD });
  await waitFor(() => expect(screen.getByTestId("dataset-source-created-status")).toBeTruthy());
  expect(screen.getByTestId("dataset-source-created-status").getAttribute("role")).toBe("status");
  expect(document.activeElement).toBe(screen.getByTestId(`dataset-source-option-${GOOD.id}`));
  // choosing another connection clears the announcement
  fireEvent.click(screen.getByTestId(`dataset-source-option-${GOOD.id}`));
  expect(screen.queryByTestId("dataset-source-created-status")).toBeNull();
});
