// @vitest-environment jsdom
//
// FTF-0501-UI -- Resumable dataset upload UI.
//
// A real component/DOM-level test (Vitest + React Testing Library + jsdom):
// renders the actual `ResumableDatasetUploadUI`, drives real user
// interaction (including real `File`/`Blob` slicing and a real
// `crypto.subtle.digest` SHA-256 hash of the test file's bytes -- not a
// mocked hash), and asserts on the exact `fetch` calls (method, URL, JSON
// body, and the raw part bytes PUT to the presigned URLs) made against the
// frozen FTF-0501 routes. `fetch` is mocked (no live server in a unit
// test); every other code path -- hashing, part slicing, missing-part
// computation, localStorage draft handling -- is the real one.
//
// Disclosed: component-level, not a browser E2E -- the repository has no
// Next.js `app/` page tree yet (FTF-H029), same precedent as
// ftf_0308.spec.ts/ftf_0309.spec.ts. Law 6 (reachability from `/`) stays
// disclosed BLOCKED on FTF-H029.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  finalizeUrl,
  missingPartNumbers,
  partByteRange,
  partsUrl,
  partUrlsUrl,
  ResumableDatasetUploadUI,
  sessionsUrl,
  sessionUrl,
  sha256Hex,
} from "../components/dataset-factory/resumable_dataset_upload_ui";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const CONNECTION_ID = "33333333-3333-3333-3333-333333333333";
const SESSION_ID = "44444444-4444-4444-4444-444444444444";

const SESSIONS_URL = sessionsUrl(API_BASE, ORG_ID, PROJECT_ID);
const SESSION_URL = sessionUrl(API_BASE, ORG_ID, PROJECT_ID, SESSION_ID);
const PARTS_URL = partsUrl(API_BASE, ORG_ID, PROJECT_ID, SESSION_ID);
const PART_URLS_URL = partUrlsUrl(API_BASE, ORG_ID, PROJECT_ID, SESSION_ID);
const FINALIZE_URL = finalizeUrl(API_BASE, ORG_ID, PROJECT_ID, SESSION_ID);

const DRAFT_KEY = `sovra.ftf0501.upload-draft.${PROJECT_ID}.${CONNECTION_ID}`;

// 5 bytes, split as part_size_bytes=3/part_count=2 -> parts of 3 and 2 bytes.
const FILE_BYTES = new Uint8Array([10, 20, 30, 40, 50]);
const OTHER_BYTES = new Uint8Array([9, 9, 9, 9, 9]);

function testFile(bytes: Uint8Array, name = "data.jsonl"): File {
  return new File([bytes as BlobPart], name, { type: "application/jsonl" });
}

async function expectedSha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function session(overrides: Record<string, unknown> = {}) {
  return {
    id: SESSION_ID,
    project_id: PROJECT_ID,
    dataset_source_connection_id: CONNECTION_ID,
    purpose: "training",
    status: "open",
    declared_size_bytes: 5,
    declared_sha256: "placeholder",
    declared_content_type: "application/jsonl",
    rejection_reason: null,
    created_at: "2026-10-09T00:00:00+00:00",
    expires_at: "2026-10-09T01:00:00+00:00",
    finalized_at: null,
    part_size_bytes: 3,
    part_count: 2,
    ...overrides,
  };
}

type Reply = { ok: boolean; status?: number; body?: unknown } | "throw";
let replies: Record<string, Reply[]>;

function enqueue(method: string, url: string, ...queued: Reply[]) {
  const key = `${method} ${url}`;
  replies[key] = [...(replies[key] ?? []), ...queued];
}

function ok(body: unknown, status = 200): Reply {
  return { ok: true, status, body };
}

function fail(status: number, detail: string | null): Reply {
  return { ok: false, status, body: detail === null ? {} : { detail } };
}

function fetchCalls() {
  return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
}

function calls(method: string, url: string) {
  return fetchCalls().filter(
    ([calledUrl, init]) => calledUrl === url && (init?.method ?? "GET") === method,
  );
}

function readBlobAsArrayBuffer(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("file read failed"));
    reader.readAsArrayBuffer(blob);
  });
}

async function bodyOf(call: [string, { body?: unknown }]): Promise<unknown> {
  const raw = call[1]?.body;
  if (typeof raw === "string") return JSON.parse(raw);
  // A part PUT sends a Blob (the real File slice), not a JSON string.
  // jsdom's Blob has no `.arrayBuffer()`, only FileReader, same as the
  // component's own real browser-compatible read path.
  const blob = raw as Blob;
  return new Uint8Array(await readBlobAsArrayBuffer(blob));
}

beforeEach(() => {
  replies = {};
  window.localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string }) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const next = replies[key]?.shift();
      if (!next) throw new Error(`unexpected request ${key}`);
      if (next === "throw") throw new Error("network drop");
      return {
        ok: next.ok,
        status: next.status ?? 200,
        json: async () => next.body,
      };
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function renderUI() {
  return render(
    createElement(ResumableDatasetUploadUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      datasetSourceConnectionId: CONNECTION_ID,
    }),
  );
}

async function selectFileAndStart(file: File) {
  fireEvent.change(screen.getByTestId("upload-file-input"), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByTestId("upload-start-button"));
}

// ---- pure helpers (no DOM) ----

test("missingPartNumbers returns only the numbers the listing lacks", () => {
  expect(missingPartNumbers(4, [])).toEqual([1, 2, 3, 4]);
  expect(missingPartNumbers(4, [{ part_number: 2, size_bytes: 1 }])).toEqual([1, 3, 4]);
  expect(
    missingPartNumbers(
      4,
      [1, 2, 3, 4].map((n) => ({ part_number: n, size_bytes: 1 })),
    ),
  ).toEqual([]);
});

test("fault: treating an empty listing as 'nothing missing' -> RED", () => {
  // The real bug this guards against: if resume ever read the listing
  // backwards (uploaded-so-far IS the missing set), a brand-new session
  // with 0 parts stored would wrongly compute 0 parts missing.
  const wrong = (partCount: number, uploaded: { part_number: number }[]) =>
    uploaded.map((p) => p.part_number); // the faulty inversion
  expect(wrong(4, [])).toEqual([]); // the fault
  expect(missingPartNumbers(4, [])).toEqual([1, 2, 3, 4]); // the real function disagrees
});

test("partByteRange: every part but the last is part_size_bytes; the last holds the remainder", () => {
  expect(partByteRange(1, 3, 2, 5)).toEqual({ start: 0, end: 3 });
  expect(partByteRange(2, 3, 2, 5)).toEqual({ start: 3, end: 5 });
});

test("sha256Hex computes the real digest of the file's bytes", async () => {
  const expected = await expectedSha256(FILE_BYTES);
  const actual = await sha256Hex(testFile(FILE_BYTES));
  expect(actual).toBe(expected);
  expect(actual).toMatch(/^[0-9a-f]{64}$/);
});

// ---- component ----

test("nothing is requested on render; Start is disabled until a file is chosen", () => {
  renderUI();
  expect(fetchCalls().length).toBe(0);
  expect((screen.getByTestId("upload-start-button") as HTMLButtonElement).disabled).toBe(true);
});

test("acceptance: full upload, exact part bytes and finalize body, ends accepted", async () => {
  renderUI();
  const expectedHash = await expectedSha256(FILE_BYTES);
  const file = testFile(FILE_BYTES);

  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok({ part_size_bytes: 3, part_count: 2, parts: [] }));
  enqueue(
    "POST",
    PART_URLS_URL,
    ok({
      parts: [
        {
          part_number: 1,
          method: "PUT",
          url: "https://storage.example/part1",
          content_length: 3,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
        {
          part_number: 2,
          method: "PUT",
          url: "https://storage.example/part2",
          content_length: 2,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
      ],
    }),
  );
  enqueue("PUT", "https://storage.example/part1", ok({}));
  enqueue("PUT", "https://storage.example/part2", ok({}));

  await selectFileAndStart(file);
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());

  // The create request declares the real SHA-256 and size -- never guessed.
  const createBody = await bodyOf(calls("POST", SESSIONS_URL)[0] as [string, { body?: unknown }]);
  expect(createBody).toEqual({
    dataset_source_connection_id: CONNECTION_ID,
    purpose: "training",
    declared_size_bytes: 5,
    declared_sha256: expectedHash,
    declared_content_type: "application/json",
  });

  // part-urls requested both missing part numbers in one call.
  const grantBody = await bodyOf(calls("POST", PART_URLS_URL)[0] as [string, { body?: unknown }]);
  expect(grantBody).toEqual({ part_numbers: [1, 2] });

  // Each part PUT carried the exact byte slice, not the whole file.
  const part1Body = (await bodyOf(
    calls("PUT", "https://storage.example/part1")[0] as [string, { body?: unknown }],
  )) as Uint8Array;
  const part2Body = (await bodyOf(
    calls("PUT", "https://storage.example/part2")[0] as [string, { body?: unknown }],
  )) as Uint8Array;
  expect(Array.from(part1Body)).toEqual([10, 20, 30]);
  expect(Array.from(part2Body)).toEqual([40, 50]);
  // The presigned PUT never carries app credentials.
  expect(calls("PUT", "https://storage.example/part1")[0]?.[1]).not.toHaveProperty("credentials");

  // Finalize.
  fireEvent.change(screen.getByTestId("upload-dataset-name-input"), {
    target: { value: "my-dataset" },
  });
  enqueue("POST", FINALIZE_URL, ok({ id: "dataset-1", name: "my-dataset" }, 201));
  fireEvent.click(screen.getByTestId("upload-finalize-button"));
  await waitFor(() => expect(screen.getByTestId("upload-accepted")).toBeTruthy());
  expect(screen.getByTestId("upload-accepted-dataset-id").textContent).toBe("dataset-1");
  const finalizeBody = await bodyOf(calls("POST", FINALIZE_URL)[0] as [string, { body?: unknown }]);
  expect(finalizeBody).toEqual({ dataset_name: "my-dataset" });

  // Terminal success clears the draft.
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
});

test("negative_acceptance: content type is the explicit selection, never inferred from the file name", async () => {
  renderUI();
  // File named like a CSV; content-type select is left at its default
  // (application/json). The create request must still say json.
  const file = testFile(FILE_BYTES, "looks-like-a.csv");
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok({ part_size_bytes: 3, part_count: 2, parts: [] }));
  await selectFileAndStart(file);
  await waitFor(() => expect(calls("POST", SESSIONS_URL).length).toBe(1));
  const body = await bodyOf(calls("POST", SESSIONS_URL)[0] as [string, { body?: unknown }]);
  expect((body as { declared_content_type: string }).declared_content_type).toBe(
    "application/json",
  );
});

test("fault: a dropped connection mid-upload is shown as interrupted, not success -> RED", async () => {
  renderUI();
  const file = testFile(FILE_BYTES);
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok({ part_size_bytes: 3, part_count: 2, parts: [] }));
  enqueue(
    "POST",
    PART_URLS_URL,
    ok({
      parts: [
        {
          part_number: 1,
          method: "PUT",
          url: "https://storage.example/part1",
          content_length: 3,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
        {
          part_number: 2,
          method: "PUT",
          url: "https://storage.example/part2",
          content_length: 2,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
      ],
    }),
  );
  enqueue("PUT", "https://storage.example/part1", ok({}));
  enqueue("PUT", "https://storage.example/part2", "throw"); // the network drop

  await selectFileAndStart(file);
  await waitFor(() => expect(screen.getByTestId("upload-interrupted")).toBeTruthy());
  expect(screen.queryByTestId("upload-accepted")).toBeNull();
  expect(screen.queryByTestId("upload-all-parts-uploaded")).toBeNull();
  // The draft survives an interruption -- resuming must still be possible.
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test(
  "acceptance (resume after a drop): only the still-missing part is re-requested, " +
    "never every part again",
  async () => {
    renderUI();
    const file = testFile(FILE_BYTES);
    enqueue("POST", SESSIONS_URL, ok(session(), 201));
    enqueue("GET", PARTS_URL, ok({ part_size_bytes: 3, part_count: 2, parts: [] }));
    enqueue(
      "POST",
      PART_URLS_URL,
      ok({
        parts: [
          {
            part_number: 1,
            method: "PUT",
            url: "https://storage.example/part1",
            content_length: 3,
            expires_at: "2026-10-09T00:05:00+00:00",
          },
          {
            part_number: 2,
            method: "PUT",
            url: "https://storage.example/part2",
            content_length: 2,
            expires_at: "2026-10-09T00:05:00+00:00",
          },
        ],
      }),
    );
    enqueue("PUT", "https://storage.example/part1", ok({}));
    enqueue("PUT", "https://storage.example/part2", "throw");
    await selectFileAndStart(file);
    await waitFor(() => expect(screen.getByTestId("upload-interrupted")).toBeTruthy());

    // Resume: storage now genuinely has part 1 (the real source of truth).
    enqueue(
      "GET",
      PARTS_URL,
      ok({ part_size_bytes: 3, part_count: 2, parts: [{ part_number: 1, size_bytes: 3 }] }),
    );
    enqueue(
      "POST",
      PART_URLS_URL,
      ok({
        parts: [
          {
            part_number: 2,
            method: "PUT",
            url: "https://storage.example/part2-retry",
            content_length: 2,
            expires_at: "2026-10-09T00:06:00+00:00",
          },
        ],
      }),
    );
    enqueue("PUT", "https://storage.example/part2-retry", ok({}));
    fireEvent.click(screen.getByTestId("upload-resume-button"));
    await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());

    // The fault this guards against: resume re-uploading every part.
    const secondGrantCall = calls("POST", PART_URLS_URL)[1] as [string, { body?: unknown }];
    const secondGrantBody = await bodyOf(secondGrantCall);
    expect(secondGrantBody).toEqual({ part_numbers: [2] }); // not [1, 2]
    expect(calls("PUT", "https://storage.example/part1").length).toBe(1); // part 1 never re-sent
  },
);

test("fault: a 409 upload_incomplete finalize reply shown as success -> RED", async () => {
  renderUI();
  const file = testFile(FILE_BYTES);
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok({ part_size_bytes: 3, part_count: 2, parts: [] }));
  enqueue(
    "POST",
    PART_URLS_URL,
    ok({
      parts: [
        {
          part_number: 1,
          method: "PUT",
          url: "https://storage.example/part1",
          content_length: 3,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
        {
          part_number: 2,
          method: "PUT",
          url: "https://storage.example/part2",
          content_length: 2,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
      ],
    }),
  );
  enqueue("PUT", "https://storage.example/part1", ok({}));
  enqueue("PUT", "https://storage.example/part2", ok({}));
  await selectFileAndStart(file);
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());

  fireEvent.change(screen.getByTestId("upload-dataset-name-input"), {
    target: { value: "my-dataset" },
  });
  enqueue("POST", FINALIZE_URL, fail(409, "upload_incomplete"));
  fireEvent.click(screen.getByTestId("upload-finalize-button"));
  await waitFor(() => expect(screen.getByTestId("upload-incomplete-text")).toBeTruthy());
  expect(screen.queryByTestId("upload-accepted")).toBeNull();
  expect(screen.getByTestId("upload-incomplete-text").textContent).toContain(
    "Not every part has been uploaded",
  );
  // 409 incomplete keeps the session resumable: the draft is not cleared.
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test.each([
  [410, "upload_expired", "upload-expired-text", "expired before it was finalized"],
  [422, "sha256_mismatch", "upload-rejected-text", "do not match the declared SHA-256"],
  [422, "size_mismatch", "upload-rejected-text", "do not match the declared size"],
  [422, "content_type_mismatch", "upload-rejected-text", "does not match the declared type"],
  [409, "dataset_version_conflict", "upload-rejected-text", "already exists"],
])(
  "a %i %s finalize reply is shown as that typed outcome, never as accepted",
  async (status, detail, testId, expectedText) => {
    renderUI();
    const file = testFile(FILE_BYTES);
    enqueue("POST", SESSIONS_URL, ok(session(), 201));
    enqueue("GET", PARTS_URL, ok({ part_size_bytes: 3, part_count: 2, parts: [] }));
    enqueue(
      "POST",
      PART_URLS_URL,
      ok({
        parts: [
          {
            part_number: 1,
            method: "PUT",
            url: "https://storage.example/part1",
            content_length: 3,
            expires_at: "2026-10-09T00:05:00+00:00",
          },
          {
            part_number: 2,
            method: "PUT",
            url: "https://storage.example/part2",
            content_length: 2,
            expires_at: "2026-10-09T00:05:00+00:00",
          },
        ],
      }),
    );
    enqueue("PUT", "https://storage.example/part1", ok({}));
    enqueue("PUT", "https://storage.example/part2", ok({}));
    await selectFileAndStart(file);
    await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());

    fireEvent.change(screen.getByTestId("upload-dataset-name-input"), {
      target: { value: "my-dataset" },
    });
    enqueue("POST", FINALIZE_URL, fail(status, detail));
    fireEvent.click(screen.getByTestId("upload-finalize-button"));
    await waitFor(() => expect(screen.getByTestId(testId)).toBeTruthy());
    expect(screen.getByTestId(testId).textContent).toContain(expectedText);
    expect(screen.queryByTestId("upload-accepted")).toBeNull();
  },
);

test("a 503 storage outage on finalize is shown as an outage, never as accepted, and stays resumable", async () => {
  renderUI();
  const file = testFile(FILE_BYTES);
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok({ part_size_bytes: 3, part_count: 2, parts: [] }));
  enqueue(
    "POST",
    PART_URLS_URL,
    ok({
      parts: [
        {
          part_number: 1,
          method: "PUT",
          url: "https://storage.example/part1",
          content_length: 3,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
        {
          part_number: 2,
          method: "PUT",
          url: "https://storage.example/part2",
          content_length: 2,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
      ],
    }),
  );
  enqueue("PUT", "https://storage.example/part1", ok({}));
  enqueue("PUT", "https://storage.example/part2", ok({}));
  await selectFileAndStart(file);
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());

  fireEvent.change(screen.getByTestId("upload-dataset-name-input"), {
    target: { value: "my-dataset" },
  });
  enqueue("POST", FINALIZE_URL, fail(503, "storage_unavailable"));
  fireEvent.click(screen.getByTestId("upload-finalize-button"));
  await waitFor(() => expect(screen.getByTestId("upload-storage-unavailable-text")).toBeTruthy());
  expect(screen.queryByTestId("upload-accepted")).toBeNull();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

// ---- resume after a page reload ----

test("acceptance (resume after reload): shows the resume banner, lists real parts, requests only missing ones", async () => {
  const expectedHash = await expectedSha256(FILE_BYTES);
  // Simulate a reload: a draft was left behind by an earlier mount, but
  // this is a brand-new render with no prior in-memory state.
  window.localStorage.setItem(
    DRAFT_KEY,
    JSON.stringify({
      uploadSessionId: SESSION_ID,
      projectId: PROJECT_ID,
      datasetSourceConnectionId: CONNECTION_ID,
      purpose: "training",
      declaredSizeBytes: 5,
      declaredSha256: expectedHash,
      declaredContentType: "application/jsonl",
      fileName: "data.jsonl",
    }),
  );
  renderUI();
  expect(screen.getByTestId("upload-resume-banner")).toBeTruthy();
  expect(screen.getByTestId("upload-resume-text").textContent).toContain("data.jsonl");
  expect(fetchCalls().length).toBe(0); // nothing requested just from mounting

  enqueue("GET", SESSION_URL, ok(session()));
  // Storage already has part 1 from before the reload.
  enqueue(
    "GET",
    PARTS_URL,
    ok({ part_size_bytes: 3, part_count: 2, parts: [{ part_number: 1, size_bytes: 3 }] }),
  );
  enqueue(
    "POST",
    PART_URLS_URL,
    ok({
      parts: [
        {
          part_number: 2,
          method: "PUT",
          url: "https://storage.example/part2",
          content_length: 2,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
      ],
    }),
  );
  enqueue("PUT", "https://storage.example/part2", ok({}));

  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(FILE_BYTES, "data.jsonl")] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());

  const grantBody = await bodyOf(calls("POST", PART_URLS_URL)[0] as [string, { body?: unknown }]);
  expect(grantBody).toEqual({ part_numbers: [2] }); // not [1, 2]
});

test("negative_acceptance: resuming with a different file is refused before any network call", async () => {
  const expectedHash = await expectedSha256(FILE_BYTES);
  window.localStorage.setItem(
    DRAFT_KEY,
    JSON.stringify({
      uploadSessionId: SESSION_ID,
      projectId: PROJECT_ID,
      datasetSourceConnectionId: CONNECTION_ID,
      purpose: "training",
      declaredSizeBytes: 5,
      declaredSha256: expectedHash,
      declaredContentType: "application/jsonl",
      fileName: "data.jsonl",
    }),
  );
  renderUI();
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(OTHER_BYTES, "data.jsonl")] }, // wrong bytes, same name
  });
  await waitFor(() => expect(screen.getByTestId("upload-resume-text").textContent).toBeTruthy());
  // Refused: no GET session, no GET parts, no POST part-urls.
  expect(fetchCalls().length).toBe(0);
  expect(screen.getByTestId("upload-resume-banner")).toBeTruthy();
});

test("abandoning a draft clears it and returns to a fresh upload form, with no network call", async () => {
  window.localStorage.setItem(
    DRAFT_KEY,
    JSON.stringify({
      uploadSessionId: SESSION_ID,
      projectId: PROJECT_ID,
      datasetSourceConnectionId: CONNECTION_ID,
      purpose: "training",
      declaredSizeBytes: 5,
      declaredSha256: "irrelevant",
      declaredContentType: "application/jsonl",
      fileName: "data.jsonl",
    }),
  );
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  expect(screen.getByTestId("upload-form")).toBeTruthy();
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  expect(fetchCalls().length).toBe(0);
});

test("fault: a secret or file bytes ever written to localStorage -> RED", async () => {
  renderUI();
  const file = testFile(FILE_BYTES);
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok({ part_size_bytes: 3, part_count: 2, parts: [] }));
  enqueue(
    "POST",
    PART_URLS_URL,
    ok({
      parts: [
        {
          part_number: 1,
          method: "PUT",
          url: "https://storage.example/secret-part1-url",
          content_length: 3,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
        {
          part_number: 2,
          method: "PUT",
          url: "https://storage.example/secret-part2-url",
          content_length: 2,
          expires_at: "2026-10-09T00:05:00+00:00",
        },
      ],
    }),
  );
  enqueue("PUT", "https://storage.example/secret-part1-url", ok({}));
  enqueue("PUT", "https://storage.example/secret-part2-url", ok({}));
  await selectFileAndStart(file);
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());

  // Scan every key this origin's localStorage holds, not only the draft
  // key -- a leak into a second, differently-named key must RED too.
  const allStoredValues = Object.keys(window.localStorage)
    .map((k) => window.localStorage.getItem(k))
    .join("\n");
  expect(allStoredValues).not.toContain("secret-part1-url");
  expect(allStoredValues).not.toContain("secret-part2-url");
  expect(allStoredValues).not.toContain("storage.example");

  const stored = window.localStorage.getItem(DRAFT_KEY) ?? "";
  // Only the declared, non-secret fields are present.
  const parsed = JSON.parse(stored);
  expect(Object.keys(parsed).sort()).toEqual(
    [
      "datasetSourceConnectionId",
      "declaredContentType",
      "declaredSha256",
      "declaredSizeBytes",
      "fileName",
      "projectId",
      "purpose",
      "uploadSessionId",
    ].sort(),
  );
});
