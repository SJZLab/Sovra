// @vitest-environment jsdom
//
// FTF-UX02 r4, pre-review round 2 (reviewer verdict RETURN). Every test below was written BEFORE the
// fix / fault and run RED against the round-1 head (e7801742) or against the named fault, then green.
// Ids: [UX02-R4R2-*]. Mirrors in real Chromium: ftf_ux02_r3_browser.spec.ts "[UX02-R4R2]" rows.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DatasetSourceSelectorPurposeAuthorizationUi } from "../components/dataset-factory/dataset_source_selector_purpose_authorization_ui";
import { ResumableDatasetUploadUI } from "../components/dataset-factory/resumable_dataset_upload_ui";

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
const CONN_B = { ...GOOD, id: "44444444-4444-4444-4444-444444444444", display_name: "Archive" };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.localStorage.clear();
});

const flush = () =>
  act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });

/** A reply whose headers are here but whose body arrives only when `release()` is called. */
function slowBody(status: number, body: unknown) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return {
    reply: { ok: status < 300, status, json: async () => (await gate, body) },
    release,
  };
}

// =================================================================================================
// FTF-0500A
// =================================================================================================
function mount500(locale: "en" | "ar" = "en") {
  return render(
    createElement(DatasetSourceSelectorPurposeAuthorizationUi, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: PROJECT,
      locale,
    }),
  );
}

function stub500(opts: {
  list: unknown[] | (() => Promise<unknown[]>);
  authorize?: () => unknown;
  create?: () => unknown;
}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (/\/authorize$/.test(url)) return opts.authorize!();
    if (init?.method === "POST") return opts.create!();
    const list = typeof opts.list === "function" ? await opts.list() : opts.list;
    return { ok: true, status: 200, json: async () => list };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function selectAndCheck(connectionId = GOOD.id) {
  await waitFor(() =>
    expect(screen.getByTestId(`dataset-source-option-${connectionId}`)).toBeTruthy(),
  );
  fireEvent.click(screen.getByTestId(`dataset-source-option-${connectionId}`));
  fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
    target: { value: "training" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-check-authorization-button"));
  await flush(); // headers have landed and the component is reading the body
}

const authorizeOutput = () => ({
  authorized: screen.queryByTestId("dataset-source-authorization-result-authorized") !== null,
  denied: screen.queryByTestId("dataset-source-authorization-result-denied") !== null,
  error: screen.queryByTestId("dataset-source-authorization-error")?.textContent ?? null,
});
const NOTHING = { authorized: false, denied: false, error: null };

// Finding 1 (STALE-AUTHORIZE-BODY, severity 3): the stale() check ran BEFORE the body read, so a
// 403 / non-ok body that lands after the pair changed painted the OLD pair's failure on the new one.
// Fd: the 200 grant body read has the same shape and is pinned by the same table.
test.each([
  ["a 403 body", 403, { detail: "purpose 'training' is not allowed for this connection" }],
  ["a 500 body", 500, { detail: "boom" }],
  ["a 200 grant body", 200, { authorized: true, purpose: "training" }],
] as const)(
  "[UX02-R4R2-A1] %s that arrives after the PURPOSE changed paints nothing on the new purpose",
  async (_n, status, body) => {
    const slow = slowBody(status, body);
    stub500({ list: [GOOD], authorize: () => slow.reply });
    mount500();
    await selectAndCheck();
    fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
      target: { value: "validation" },
    });
    await act(async () => {
      slow.release();
      await flush();
    });
    expect(authorizeOutput()).toEqual(NOTHING);
    expect(
      (screen.getByTestId("dataset-source-check-authorization-button") as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  },
);

test.each([
  ["a 403 body", 403, { detail: "denied for the first connection" }],
  ["a 500 body", 500, { detail: "boom" }],
  ["a 200 grant body", 200, { authorized: true, purpose: "training" }],
] as const)(
  "[UX02-R4R2-A2] %s that arrives after switching to ANOTHER CONNECTION paints nothing on it",
  async (_n, status, body) => {
    const slow = slowBody(status, body);
    stub500({ list: [GOOD, CONN_B], authorize: () => slow.reply });
    mount500();
    await selectAndCheck();
    fireEvent.click(screen.getByTestId(`dataset-source-option-${CONN_B.id}`));
    await act(async () => {
      slow.release();
      await flush();
    });
    expect(authorizeOutput()).toEqual(NOTHING);
  },
);

test("[UX02-R4R2-A3] NEGATIVE: with the pair unchanged a slow 403 body still shows its own denial", async () => {
  const slow = slowBody(403, { detail: "purpose 'training' is not allowed for this connection" });
  stub500({ list: [GOOD], authorize: () => slow.reply });
  mount500();
  await selectAndCheck();
  await act(async () => {
    slow.release();
    await flush();
  });
  expect(screen.getByTestId("dataset-source-authorization-result-denied")).toBeTruthy();
});

// Fe: a rejected fetch (network error) that lands after the pair changed.
test.each(["connection", "purpose"] as const)(
  "[UX02-R4R2-E1] a rejected authorize fetch that lands after the %s changed paints no error",
  async (change) => {
    let fail!: (error: Error) => void;
    const rejected = new Promise<never>((_resolve, reject) => {
      fail = reject;
    });
    stub500({ list: [GOOD, CONN_B], authorize: () => rejected });
    mount500();
    await selectAndCheck();
    if (change === "connection")
      fireEvent.click(screen.getByTestId(`dataset-source-option-${CONN_B.id}`));
    else
      fireEvent.change(screen.getByTestId("dataset-source-purpose-to-check-select"), {
        target: { value: "validation" },
      });
    await act(async () => {
      fail(new TypeError("Failed to fetch"));
      await flush();
    });
    expect(authorizeOutput()).toEqual(NOTHING);
  },
);

// Fc: a create that lands while an authorize for the previously selected connection is in flight
// selects the NEW connection; that late reply is for the old one and must not paint on it.
test("[UX02-R4R2-C1] an authorize reply for connection B that lands after a create selected another connection paints nothing", async () => {
  let settle!: (reply: unknown) => void;
  const pendingAuthorize = new Promise((resolve) => {
    settle = resolve;
  });
  const created = { ...GOOD, id: "77777777-7777-7777-7777-777777777777", display_name: "New one" };
  stub500({
    list: [GOOD],
    authorize: () => pendingAuthorize,
    create: () => ({ ok: true, status: 201, json: async () => created }),
  });
  mount500();
  await selectAndCheck();
  fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
    target: { value: "New one" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
  fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
    target: { value: "internal" },
  });
  fireEvent.click(screen.getByTestId("dataset-source-review-button"));
  fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));
  await waitFor(() =>
    expect(screen.getByTestId(`dataset-source-option-${created.id}`)).toBeTruthy(),
  );
  await act(async () => {
    settle({
      ok: true,
      status: 200,
      json: async () => ({ authorized: true, purpose: "training" }),
    });
    await flush();
  });
  expect(authorizeOutput()).toEqual(NOTHING);
  expect(screen.getByTestId("dataset-source-authorization-selected-connection").textContent).toBe(
    "New one",
  );
});

// Finding 5 (CREATE-WIPED-BY-LIST, severity 2): a create that succeeds while the FIRST list load is
// still in flight must survive that (older) list reply: the list is merged with it, selection and
// focus stay.
test.each([
  ["an empty list", []],
  ["a list with another connection", [GOOD]],
] as const)(
  "[UX02-R4R2-L1] a create that succeeds during the first list load survives %s arriving afterwards",
  async (_n, list) => {
    let releaseList!: () => void;
    const gate = new Promise<void>((resolve) => {
      releaseList = resolve;
    });
    const created = {
      ...GOOD,
      id: "77777777-7777-7777-7777-777777777777",
      display_name: "New one",
    };
    stub500({
      list: async () => {
        await gate;
        return [...list];
      },
      create: () => ({ ok: true, status: 201, json: async () => created }),
    });
    mount500();
    fireEvent.change(screen.getByTestId("dataset-source-display-name-input"), {
      target: { value: "New one" },
    });
    fireEvent.click(screen.getByTestId("dataset-source-purpose-training"));
    fireEvent.change(screen.getByTestId("dataset-source-security-classification-select"), {
      target: { value: "internal" },
    });
    fireEvent.click(screen.getByTestId("dataset-source-review-button"));
    fireEvent.click(screen.getByTestId("dataset-source-confirm-button"));
    await waitFor(() =>
      expect(screen.getByTestId(`dataset-source-option-${created.id}`)).toBeTruthy(),
    );
    const option = screen.getByTestId(`dataset-source-option-${created.id}`);
    await waitFor(() => expect(document.activeElement).toBe(option));
    await act(async () => {
      releaseList();
      await flush();
    });
    // Same DOM node (not re-created), still focused and selected; the list's own items joined it.
    expect(screen.getByTestId(`dataset-source-option-${created.id}`)).toBe(option);
    expect(document.activeElement).toBe(option);
    expect(screen.getByTestId("dataset-source-authorization-selected-connection").textContent).toBe(
      "New one",
    );
    expect(screen.queryByTestId(`dataset-source-option-${GOOD.id}`) !== null).toBe(
      list.length === 1,
    );
    expect(screen.getAllByTestId(`dataset-source-option-${created.id}`)).toHaveLength(1);
  },
);

// =================================================================================================
// FTF-0501-UI
// =================================================================================================
const CONNECTION = "55555555-5555-5555-5555-555555555555";
const SESSION = {
  id: "66666666-6666-6666-6666-666666666666",
  project_id: PROJECT,
  dataset_source_connection_id: CONNECTION,
  purpose: "training",
  status: "open",
  declared_size_bytes: 3,
  declared_sha256: "039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81",
  declared_content_type: "application/json",
  rejection_reason: null,
  created_at: "2026-10-09T00:00:00+00:00",
  expires_at: "2026-10-09T01:00:00+00:00",
  finalized_at: null,
  part_size_bytes: 3,
  part_count: 1,
};

function mount501(locale: "en" | "ar" = "en") {
  return render(
    createElement(ResumableDatasetUploadUI, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: PROJECT,
      datasetSourceConnectionId: CONNECTION,
      locale,
    }),
  );
}

type Reply = { ok: boolean; status: number; json: () => Promise<unknown> };
const reply = (status: number, body: unknown): Reply => ({
  ok: status < 300,
  status,
  json: async () => body,
});

/** Scripted 0501 API: finalize and "GET session" answer from per-call queues. */
function stubUpload(opts: {
  finalize: Array<[number, unknown]>;
  status: Array<() => Promise<Reply> | Reply>;
}) {
  const calls = { finalize: 0, status: 0, sessions: 0 };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      if (u.hostname === "storage.example") return reply(200, {});
      if (u.pathname.endsWith("/finalize")) {
        const [status, body] = opts.finalize[Math.min(calls.finalize, opts.finalize.length - 1)]!;
        calls.finalize += 1;
        return reply(status, body);
      }
      if (u.pathname.endsWith("/part-urls")) {
        return reply(200, {
          parts: [
            {
              part_number: 1,
              method: "PUT",
              url: "https://storage.example/p1",
              content_length: 3,
              expires_at: "2026-10-09T00:05:00+00:00",
            },
          ],
        });
      }
      if (u.pathname.endsWith("/parts"))
        return reply(200, { part_size_bytes: 3, part_count: 1, parts: [] });
      if (init?.method === "POST") {
        calls.sessions += 1;
        return reply(201, SESSION);
      }
      const step = opts.status[Math.min(calls.status, opts.status.length - 1)]!;
      calls.status += 1;
      return step();
    }),
  );
  return calls;
}

async function startAndFinalize() {
  fireEvent.change(screen.getByTestId("upload-file-input"), {
    target: { files: [new File([new Uint8Array([1, 2, 3])], "data.jsonl")] },
  });
  fireEvent.click(screen.getByTestId("upload-start-button"));
  await waitFor(() => expect(screen.getByTestId("upload-dataset-name-input")).toBeTruthy());
  fireEvent.change(screen.getByTestId("upload-dataset-name-input"), { target: { value: "ds" } });
  fireEvent.click(screen.getByTestId("upload-finalize-button"));
}

/** First upload finalizes to the malformed screen, "Check upload status" starts and the user leaves
 * with Back. Returns with the draft banner showing and the old status request STILL in flight. */
async function malformedCheckThenBack(locale: "en" | "ar" = "en") {
  mount501(locale);
  await startAndFinalize();
  await waitFor(() => expect(screen.getByTestId("upload-malformed")).toBeTruthy());
  fireEvent.click(screen.getByTestId("upload-check-status-button"));
  await flush();
  fireEvent.click(screen.getByTestId("upload-malformed-back-button"));
  expect(screen.getByTestId("upload-resume-banner")).toBeTruthy();
}

const stillBanner = () => {
  expect(screen.getByTestId("upload-resume-banner")).toBeTruthy();
  expect(screen.queryByTestId("upload-malformed")).toBeNull();
  expect(screen.queryByTestId("upload-rejected-text")).toBeNull();
  expect(screen.queryByTestId("upload-expired-text")).toBeNull();
  expect(window.localStorage.length, "the draft is kept").toBeGreaterThan(0);
};

// Fo: a late NETWORK error after Back must not restore the malformed screen.
test("[UX02-R4R2-O1] a network error from 'Check upload status' that lands after Back does not restore the malformed screen", async () => {
  let fail!: (error: Error) => void;
  const rejected = new Promise<Reply>((_resolve, reject) => {
    fail = reject;
  });
  stubUpload({ finalize: [[201, {}]], status: [() => rejected] });
  await malformedCheckThenBack();
  await act(async () => {
    fail(new TypeError("Failed to fetch"));
    await flush();
  });
  stillBanner();
});

test("[UX02-R4R2-O2] NEGATIVE: with no Back the same network error returns to the malformed screen with an alert", async () => {
  let fail!: (error: Error) => void;
  const rejected = new Promise<Reply>((_resolve, reject) => {
    fail = reject;
  });
  stubUpload({ finalize: [[201, {}]], status: [() => rejected] });
  mount501();
  await startAndFinalize();
  await waitFor(() => expect(screen.getByTestId("upload-malformed")).toBeTruthy());
  fireEvent.click(screen.getByTestId("upload-check-status-button"));
  await flush();
  await act(async () => {
    fail(new TypeError("Failed to fetch"));
    await flush();
  });
  expect(screen.getByTestId("upload-malformed")).toBeTruthy();
  expect(screen.getByTestId("upload-malformed-text")).toBeTruthy();
});

// Fv: the session JSON arrives after Back. Fw: the error detail arrives after Back.
test("[UX02-R4R2-V1] a session body that arrives after Back is ignored (nothing rejected, draft kept)", async () => {
  const slow = slowBody(200, { ...SESSION, status: "rejected", rejection_reason: "size_mismatch" });
  stubUpload({ finalize: [[201, {}]], status: [() => slow.reply as Reply] });
  await malformedCheckThenBack();
  await act(async () => {
    slow.release();
    await flush();
  });
  stillBanner();
});

test("[UX02-R4R2-W1] an error detail that arrives after Back is ignored (no expiry screen, draft kept)", async () => {
  const slow = slowBody(410, { detail: "upload_expired" });
  stubUpload({ finalize: [[201, {}]], status: [() => slow.reply as Reply] });
  await malformedCheckThenBack();
  await act(async () => {
    slow.release();
    await flush();
  });
  stillBanner();
});

test("[UX02-R4R2-W2] NEGATIVE: without Back the same slow 410 detail ends on the expiry screen", async () => {
  const slow = slowBody(410, { detail: "upload_expired" });
  stubUpload({ finalize: [[201, {}]], status: [() => slow.reply as Reply] });
  mount501();
  await startAndFinalize();
  await waitFor(() => expect(screen.getByTestId("upload-malformed")).toBeTruthy());
  fireEvent.click(screen.getByTestId("upload-check-status-button"));
  await flush();
  await act(async () => {
    slow.release();
    await flush();
  });
  expect(screen.getByTestId("upload-expired-text")).toBeTruthy();
});

/** Continue from `malformedCheckThenBack`: abandon the draft and drive a SECOND upload to its end.
 * The first status request (A) is still in flight. */
async function secondUpload(finalizeSecond: boolean) {
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  await screen.findByTestId("upload-abandon-confirm");
  fireEvent.click(screen.getByTestId("upload-abandon-confirm-button"));
  await waitFor(() => expect(screen.getByTestId("upload-form")).toBeTruthy());
  fireEvent.change(screen.getByTestId("upload-file-input"), {
    target: { files: [new File([new Uint8Array([1, 2, 3])], "data.jsonl")] },
  });
  fireEvent.click(screen.getByTestId("upload-start-button"));
  await waitFor(() => expect(screen.getByTestId("upload-dataset-name-input")).toBeTruthy());
  if (finalizeSecond) {
    fireEvent.change(screen.getByTestId("upload-dataset-name-input"), { target: { value: "ds" } });
    fireEvent.click(screen.getByTestId("upload-finalize-button"));
  }
}

function lateSettle() {
  let settle!: (value: Reply) => void;
  const promise = new Promise<Reply>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

// Fx: superseding a status check must also clear its busy flag.
test("[UX02-R4R2-X1] after Back and a fresh upload, Start over is usable although the old status request is still in flight", async () => {
  const a = lateSettle();
  stubUpload({ finalize: [[201, {}]], status: [() => a.promise] });
  await malformedCheckThenBack();
  await secondUpload(false);
  const start = screen.getByTestId("upload-new-upload-button") as HTMLButtonElement;
  expect(start.disabled).toBe(false);
  expect(
    screen.getByTestId("upload-finalize").querySelector('[data-testid="disabled-reason"]')
      ?.textContent ?? "",
  ).not.toMatch(/status check/);
});

// Fn: the OLD request's `finally` must not clear the NEW request's busy flag.
test("[UX02-R4R2-N1] an old status request that settles while a NEWER one is in flight keeps Start over disabled", async () => {
  const a = lateSettle();
  stubUpload({
    finalize: [
      [201, {}],
      [500, {}],
    ],
    status: [() => a.promise, () => new Promise<Reply>(() => undefined)],
  });
  await malformedCheckThenBack();
  await secondUpload(true);
  await waitFor(() => expect(screen.getByTestId("upload-finalize-retry-text")).toBeTruthy());
  fireEvent.click(screen.getByTestId("upload-check-status-button")); // B
  await flush();
  const start = screen.getByTestId("upload-new-upload-button") as HTMLButtonElement;
  expect(start.disabled, "B in flight").toBe(true);
  await act(async () => {
    a.settle(reply(500, {})); // A settles late
    await flush();
  });
  expect(start.disabled, "A's late settle must not release B's busy state").toBe(true);
});

// Fm: the OLD request's lock release must not free the NEWER request's lock.
test("[UX02-R4R2-M1] an old status request that settles while a NEWER one is in flight does not allow a duplicate request", async () => {
  const a = lateSettle();
  const calls = stubUpload({
    finalize: [[201, {}]],
    status: [() => a.promise, () => new Promise<Reply>(() => undefined)],
  });
  await malformedCheckThenBack();
  await secondUpload(true);
  await waitFor(() => expect(screen.getByTestId("upload-malformed")).toBeTruthy());
  fireEvent.click(screen.getByTestId("upload-check-status-button")); // B
  await flush();
  expect(calls.status).toBe(2);
  await act(async () => {
    a.settle(reply(500, {})); // A settles late
    await flush();
  });
  fireEvent.click(screen.getByTestId("upload-check-status-button")); // a second click on B's screen
  await flush();
  expect(calls.status, "B is still in flight: no third request").toBe(2);
});

// Ft: the malformed screen's own busy indicator.
test.each([
  ["en", "Checking upload status…"],
  ["ar", "جارٍ فحص حالة الرفع…"],
] as const)(
  "[UX02-R4R2-T1] [%s] 'Check upload status' on the malformed screen shows a busy indicator after one second",
  async (locale, label) => {
    stubUpload({ finalize: [[201, {}]], status: [() => new Promise<Reply>(() => undefined)] });
    mount501(locale);
    await startAndFinalize();
    await waitFor(() => expect(screen.getByTestId("upload-malformed")).toBeTruthy());
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByTestId("upload-check-status-button"));
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.queryByText(label)).toBeNull();
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      expect(screen.getByText(label).closest('[role="status"]')!.getAttribute("aria-live")).toBe(
        "polite",
      );
    } finally {
      vi.useRealTimers();
    }
  },
);

// Finding 4 (CLICKS-SILENTLY-DROPPED, severity 2): on finalize_retry, while a status check is in
// flight, Finalize and Check upload status were enabled but their click was dropped by the lock.
test.each([
  ["en", "Wait for the status check to finish."],
  ["ar", "انتظر حتى ينتهي فحص الحالة."],
] as const)(
  "[UX02-R4R2-D1] [%s] on finalize_retry Finalize and Check upload status are disabled WITH the reason while a check is in flight, and usable after",
  async (locale, reason) => {
    const a = lateSettle();
    const calls = stubUpload({ finalize: [[500, {}]], status: [() => a.promise] });
    mount501(locale);
    await startAndFinalize();
    await waitFor(() => expect(screen.getByTestId("upload-finalize-retry-text")).toBeTruthy());
    const finalize = screen.getByTestId("upload-finalize-button") as HTMLButtonElement;
    const check = screen.getByTestId("upload-check-status-button") as HTMLButtonElement;
    expect(finalize.disabled, "idle: usable").toBe(false);
    expect(check.disabled, "idle: usable").toBe(false);
    fireEvent.click(check);
    await flush();
    for (const button of [finalize, check]) {
      expect(button.disabled).toBe(true);
      const described = (button.getAttribute("aria-describedby") ?? "")
        .split(" ")
        .map((id) => document.getElementById(id)?.textContent)
        .filter(Boolean);
      expect(described).toContain(reason);
    }
    fireEvent.click(finalize);
    fireEvent.click(check);
    await flush();
    expect(calls.finalize, "no second finalize").toBe(1);
    expect(calls.status, "no second status request").toBe(1);
    await act(async () => {
      a.settle(reply(500, {}));
      await flush();
    });
    expect(finalize.disabled).toBe(false);
    expect((screen.getByTestId("upload-check-status-button") as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(document.querySelector('[data-testid="disabled-reason"]')).toBeNull();
  },
);
