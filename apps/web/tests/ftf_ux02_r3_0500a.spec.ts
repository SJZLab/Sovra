// @vitest-environment jsdom
//
// FTF-UX02 round 3, finding 2 (MALFORMED-200, Law 17; QUEUE 0k): FTF-0500A shows a connection only
// when the reply is a real one, and "Authorized" only for `{authorized: true, purpose: <sent>}`.
// One test per case from the verifier's reproduction; each was RED against the round-2 component.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

test("[UX02-R3-N2] a Retry that succeeds hands focus to the first connection, never <body>", async () => {
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      calls += 1;
      return calls === 1
        ? { ok: false, status: 500, json: async () => ({}) }
        : { ok: true, status: 200, json: async () => [GOOD] };
    }),
  );
  mountUi();
  await waitFor(() => expect(screen.getByTestId("dataset-source-list-retry-button")).toBeTruthy());
  fireEvent.click(screen.getByTestId("dataset-source-list-retry-button"));
  await waitFor(() => expect(screen.getByTestId(`dataset-source-option-${GOOD.id}`)).toBeTruthy());
  expect(document.activeElement).toBe(screen.getByTestId(`dataset-source-option-${GOOD.id}`));
});

// Round 4, finding 3 (NN/g H5, checklist I4): an impossible action stays visible but disabled WITH
// the reason shown (the UX01 Button `disabledReason`), localized, and the reason leaves with the cause.
test.each([
  ["en", "Complete the display name, an allowed purpose and a security classification to review."],
  ["ar", "أكمل الاسم المعروض وغرضًا مسموحًا وتصنيف الأمان للمراجعة."],
] as const)(
  "[UX02-R4-D1] [%s] Review is disabled WITH a reason that is wired by aria-describedby and goes away when the form is ready",
  async (locale, reason) => {
    stub({ list: [] });
    mountUi(locale);
    await waitFor(() => expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy());
    const button = screen.getByTestId("dataset-source-review-button") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    const shown = screen.getByTestId("disabled-reason");
    expect(shown.textContent).toBe(reason);
    expect(button.getAttribute("aria-describedby")).toContain(shown.id);
    fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
      target: { value: "New one" },
    });
    fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
    fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
      target: { value: "internal" },
    });
    expect(button.disabled).toBe(false);
    expect(screen.queryByTestId("disabled-reason")).toBeNull();
  },
);

test.each([
  ["en", "Choose a purpose to authorize."],
  ["ar", "اختر الغرض المطلوب تفويضه."],
] as const)(
  "[UX02-R4-D2] [%s] Check authorization is disabled WITH a reason until a purpose is chosen",
  async (locale, reason) => {
    stub({ list: [GOOD] });
    mountUi(locale);
    await waitFor(() =>
      expect(screen.getByTestId(`dataset-source-option-${GOOD.id}`)).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId(`dataset-source-option-${GOOD.id}`));
    const button = screen.getByTestId(
      "dataset-source-check-authorization-button",
    ) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    const section = screen.getByTestId("dataset-source-authorization-section");
    const shown = within(section).getByTestId("disabled-reason");
    expect(shown.textContent).toBe(reason);
    expect(button.getAttribute("aria-describedby")).toContain(shown.id);
    fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
      target: { value: "training" },
    });
    expect(button.disabled).toBe(false);
    expect(within(section).queryByTestId("disabled-reason")).toBeNull();
  },
);

// Round 4, finding 4 (response-time rule): nothing before 1 s, then a polite busy indicator.
test.each([
  ["en", "Creating the connection…"],
  ["ar", "جارٍ إنشاء الاتصال…"],
] as const)(
  "[UX02-R4-B1] [%s] a create in flight shows a busy indicator only after one second",
  async (locale, label) => {
    stub({ list: [], create: new Promise(() => undefined) as unknown as Reply });
    mountUi(locale);
    await waitFor(() => expect(screen.getByTestId("dataset-source-empty-state")).toBeTruthy());
    fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
      target: { value: "New one" },
    });
    fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
    fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
      target: { value: "internal" },
    });
    fireEvent.click(screen.getByTestId("dataset-source-review-button"));
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.queryByText(label)).toBeNull();
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      const status = screen.getByText(label).closest('[role="status"]')!;
      expect(status.getAttribute("aria-live")).toBe("polite");
    } finally {
      vi.useRealTimers();
    }
  },
);

test.each([
  ["en", "Checking authorization…"],
  ["ar", "جارٍ فحص التفويض…"],
] as const)(
  "[UX02-R4-B2] [%s] an authorization check in flight shows a busy indicator only after one second",
  async (locale, label) => {
    stub({ list: [GOOD], authorize: new Promise(() => undefined) as unknown as Reply });
    mountUi(locale);
    await waitFor(() =>
      expect(screen.getByTestId(`dataset-source-option-${GOOD.id}`)).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId(`dataset-source-option-${GOOD.id}`));
    fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
      target: { value: "training" },
    });
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByTestId("dataset-source-check-authorization-button"));
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.queryByText(label)).toBeNull();
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      const status = screen.getByText(label).closest('[role="status"]')!;
      expect(status.getAttribute("aria-live")).toBe("polite");
    } finally {
      vi.useRealTimers();
    }
  },
);
