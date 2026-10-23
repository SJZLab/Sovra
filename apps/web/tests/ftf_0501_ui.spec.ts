// @vitest-environment jsdom
//
// FTF-0501-UI round 2 -- Resumable dataset upload UI.
//
// A real component/DOM-level test (Vitest + React Testing Library + jsdom):
// renders the actual `ResumableDatasetUploadUI`, drives real user
// interaction (real `File`/`Blob` slicing and a real `crypto.subtle` SHA-256
// of the test file's bytes), and asserts on the exact `fetch` calls made
// against the frozen FTF-0501 routes. `fetch` is mocked here; the sibling
// real-API spec (ftf_0501_ui_real_api.realapi.ts) runs the same component
// against the real router on PostgreSQL 16 and SeaweedFS.
//
// UI CHECKLIST (Control-plane ruling 2026-10-09), each item below:
//  (a) generated strict-parse tables over EVERY field of EVERY reply
//      (wrong JSON type, value outside an enum, missing) -> "malformed" and
//      no action control;
//  (b) ONE binding function per reply, a case per field sent;
//  (c) identity: organization / project / connection reset the component;
//  (d) no simulated progress or success;
//  (e) typed errors: 5xx / 401 / 403 / non-JSON / unknown codes retryable;
//  (f) real-API test: ftf_0501_ui_real_api.realapi.ts + the CI job
//      `resumable-upload-ui-real-api-integration`.
//
// Disclosed: component-level, not a browser E2E -- the repository has no
// Next.js `app/` page tree yet (FTF-H029). Law 6 stays BLOCKED on it.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  ALLOWED_PURPOSES,
  classifyFailure,
  draftStorageKey,
  finalizeUrl,
  missingPartNumbers,
  parsePartsListing,
  parsePartUrlGrants,
  parseUploadedDataset,
  parseUploadSession,
  partByteRange,
  partsUrl,
  partUrlsUrl,
  ResumableDatasetUploadUI,
  SESSION_STATUSES,
  sessionMatches,
  sessionsUrl,
  sessionUrl,
  sha256Hex,
  SUPPORTED_CONTENT_TYPES,
} from "../components/dataset-factory/resumable_dataset_upload_ui";
import routesSource from "../../api/app/datasets/direct_object_storage_upload_routes.py?raw";
import uploadSource from "../../api/app/datasets/direct_object_storage_upload.py?raw";

const API_BASE = "http://localhost:8000";
const ORG_ID = "11111111-1111-1111-1111-111111111111";
const OTHER_ORG_ID = "99999999-9999-9999-9999-999999999999";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const OTHER_PROJECT_ID = "88888888-8888-8888-8888-888888888888";
const CONNECTION_ID = "33333333-3333-3333-3333-333333333333";
const OTHER_CONNECTION_ID = "77777777-7777-7777-7777-777777777777";
const SESSION_ID = "44444444-4444-4444-4444-444444444444";
const DATASET_ID = "55555555-5555-5555-5555-555555555555";
const OTHER_UUID = "66666666-6666-6666-6666-666666666666";

const SESSIONS_URL = sessionsUrl(API_BASE, ORG_ID, PROJECT_ID);
const SESSION_URL = sessionUrl(API_BASE, ORG_ID, PROJECT_ID, SESSION_ID);
const PARTS_URL = partsUrl(API_BASE, ORG_ID, PROJECT_ID, SESSION_ID);
const PART_URLS_URL = partUrlsUrl(API_BASE, ORG_ID, PROJECT_ID, SESSION_ID);
const FINALIZE_URL = finalizeUrl(API_BASE, ORG_ID, PROJECT_ID, SESSION_ID);
const PART1_URL = "https://storage.example/part1";
const PART2_URL = "https://storage.example/part2";

const DRAFT_KEY = draftStorageKey(ORG_ID, PROJECT_ID, CONNECTION_ID);

// 5 bytes, split as part_size_bytes=3/part_count=2 -> parts of 3 and 2 bytes.
const FILE_BYTES = new Uint8Array([10, 20, 30, 40, 50]);
const OTHER_BYTES = new Uint8Array([9, 9, 9, 9, 9]);
// SHA-256 of FILE_BYTES, fixed at module load: the generated case tables below are built
// when this file is collected, before any beforeAll hook could fill a variable in. A test
// further down proves the constant is the real digest of those bytes.
const HASH = "6ef53a6408ec4429791fa808e4fbe7843f936316950a20ba5fbbbdd01cd29899";
const OTHER_HASH = "a".repeat(64);

function testFile(bytes: Uint8Array, name = "data.jsonl"): File {
  return new File([bytes as BlobPart], name, { type: "application/jsonl" });
}

type Json = Record<string, unknown>;

function session(overrides: Json = {}): Json {
  return {
    id: SESSION_ID,
    project_id: PROJECT_ID,
    dataset_source_connection_id: CONNECTION_ID,
    purpose: "training",
    status: "open",
    declared_size_bytes: 5,
    declared_sha256: HASH,
    declared_content_type: "application/json",
    rejection_reason: null,
    created_at: "2026-10-09T00:00:00+00:00",
    expires_at: "2026-10-09T01:00:00+00:00",
    finalized_at: null,
    part_size_bytes: 3,
    part_count: 2,
    ...overrides,
  };
}

function listing(partNumbers: number[] = [], overrides: Json = {}): Json {
  return {
    part_size_bytes: 3,
    part_count: 2,
    parts: partNumbers.map((n) => ({ part_number: n, size_bytes: n === 2 ? 2 : 3 })),
    ...overrides,
  };
}

function grant(partNumber: number, overrides: Json = {}): Json {
  return {
    part_number: partNumber,
    method: "PUT",
    url: partNumber === 1 ? PART1_URL : PART2_URL,
    content_length: partNumber === 1 ? 3 : 2,
    expires_at: "2026-10-09T00:05:00+00:00",
    ...overrides,
  };
}

function grants(numbers: number[]): Json {
  return { parts: numbers.map((n) => grant(n)) };
}

function datasetBody(overrides: Json = {}): Json {
  return {
    id: DATASET_ID,
    organization_id: ORG_ID,
    project_id: PROJECT_ID,
    name: "my-dataset",
    version: 1,
    purpose: "training",
    upload_session_id: SESSION_ID,
    verified_sha256: HASH,
    verified_size_bytes: 5,
    created_by: "user:jane",
    created_at: "2026-10-09T00:02:00+00:00",
    ...overrides,
  };
}

type Plain = { status?: number; body?: unknown; jsonThrows?: boolean; jsonGate?: Promise<void> };
type Reply = Plain | "throw" | ((init?: { body?: unknown }) => Plain | "throw") | Promise<Plain>;
let replies: Record<string, Reply[]>;
let unexpected: string[];

function enqueue(method: string, url: string, ...queued: Reply[]) {
  const key = `${method} ${url}`;
  replies[key] = [...(replies[key] ?? []), ...queued];
}

function ok(body: unknown, status = 200): Plain {
  return { status, body };
}

function fail(status: number, detail: string | null): Plain {
  return { status, body: detail === null ? {} : { detail } };
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
  return new Uint8Array(await readBlobAsArrayBuffer(raw as Blob));
}

beforeEach(() => {
  replies = {};
  unexpected = [];
  window.localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string; body?: unknown }) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      let next: Reply | undefined = replies[key]?.shift();
      if (!next) {
        unexpected.push(key);
        throw new Error(`unexpected request ${key}`);
      }
      if (typeof next === "function") next = next(init);
      const settled: Plain | "throw" = await next;
      if (settled === "throw") throw new Error("network drop");
      const status = settled.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => {
          if (settled.jsonGate) await settled.jsonGate;
          if (settled.jsonThrows) throw new SyntaxError("Unexpected token < in JSON");
          return settled.body;
        },
      };
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

const PROPS = {
  apiBaseUrl: API_BASE,
  organizationId: ORG_ID,
  projectId: PROJECT_ID,
  datasetSourceConnectionId: CONNECTION_ID,
};

function renderUI(props: Partial<typeof PROPS> = {}) {
  return render(createElement(ResumableDatasetUploadUI, { ...PROPS, ...props }));
}

async function selectFileAndStart(file: File) {
  fireEvent.change(screen.getByTestId("upload-file-input"), { target: { files: [file] } });
  fireEvent.click(screen.getByTestId("upload-start-button"));
}

function seedFreshUpload(partsListing: Json = listing()) {
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(partsListing));
  enqueue("POST", PART_URLS_URL, (init) => {
    const requested = (JSON.parse(String(init?.body)) as { part_numbers: number[] }).part_numbers;
    return ok(grants(requested));
  });
  enqueue("PUT", PART1_URL, ok({}));
  enqueue("PUT", PART2_URL, ok({}));
}

async function uploadToReadyToFinalize() {
  const view = renderUI();
  seedFreshUpload();
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());
  return view;
}

async function finalizeWith(name: string, reply: Reply) {
  fireEvent.change(screen.getByTestId("upload-dataset-name-input"), { target: { value: name } });
  enqueue("POST", FINALIZE_URL, reply);
  fireEvent.click(screen.getByTestId("upload-finalize-button"));
}

function seedDraft(overrides: Json = {}) {
  window.localStorage.setItem(
    DRAFT_KEY,
    JSON.stringify({
      organizationId: ORG_ID,
      projectId: PROJECT_ID,
      datasetSourceConnectionId: CONNECTION_ID,
      uploadSessionId: SESSION_ID,
      purpose: "training",
      declaredSizeBytes: 5,
      declaredSha256: HASH,
      declaredContentType: "application/json",
      fileName: "data.jsonl",
      ...overrides,
    }),
  );
}

const ACTION_TEST_IDS = [
  "upload-start-button",
  "upload-file-input",
  "upload-resume-file-input",
  "upload-resume-button",
  "upload-recheck-parts-button",
  "upload-finalize-button",
  "upload-dataset-name-input",
  "upload-accepted",
];

function expectMalformedAndNoActionControl() {
  expect(screen.getByTestId("upload-malformed-text")).toBeTruthy();
  for (const id of ACTION_TEST_IDS) expect(screen.queryByTestId(id), id).toBeNull();
  expect(screen.queryByText("Dataset created.")).toBeNull();
}

// ---------------------------------------------------------------------------
// pure helpers
// ---------------------------------------------------------------------------

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

test("partByteRange: every part but the last is part_size_bytes; the last holds the remainder", () => {
  expect(partByteRange(1, 3, 2, 5)).toEqual({ start: 0, end: 3 });
  expect(partByteRange(2, 3, 2, 5)).toEqual({ start: 3, end: 5 });
});

test("sha256Hex computes the real digest of the file's bytes", async () => {
  const digest = await crypto.subtle.digest("SHA-256", FILE_BYTES as BufferSource);
  const expected = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  expect(await sha256Hex(testFile(FILE_BYTES))).toBe(expected);
  expect(HASH).toBe(expected);
});

// ---------------------------------------------------------------------------
// acceptance
// ---------------------------------------------------------------------------

test("nothing is requested on render; Start is disabled until a file is chosen", () => {
  renderUI();
  expect(fetchCalls().length).toBe(0);
  expect((screen.getByTestId("upload-start-button") as HTMLButtonElement).disabled).toBe(true);
});

test("acceptance: full upload, exact part bytes and finalize body, ends accepted", async () => {
  await uploadToReadyToFinalize();

  const createBody = await bodyOf(calls("POST", SESSIONS_URL)[0] as [string, { body?: unknown }]);
  expect(createBody).toEqual({
    dataset_source_connection_id: CONNECTION_ID,
    purpose: "training",
    declared_size_bytes: 5,
    declared_sha256: HASH,
    declared_content_type: "application/json",
  });
  expect(await bodyOf(calls("POST", PART_URLS_URL)[0] as [string, { body?: unknown }])).toEqual({
    part_numbers: [1, 2],
  });
  const part1 = (await bodyOf(
    calls("PUT", PART1_URL)[0] as [string, { body?: unknown }],
  )) as Uint8Array;
  const part2 = (await bodyOf(
    calls("PUT", PART2_URL)[0] as [string, { body?: unknown }],
  )) as Uint8Array;
  expect(Array.from(part1)).toEqual([10, 20, 30]);
  expect(Array.from(part2)).toEqual([40, 50]);
  expect(calls("PUT", PART1_URL)[0]?.[1]).not.toHaveProperty("credentials");

  await finalizeWith("  my-dataset  ", ok(datasetBody(), 201));
  await waitFor(() => expect(screen.getByTestId("upload-accepted")).toBeTruthy());
  expect(screen.getByTestId("upload-accepted-dataset-id").textContent).toBe(DATASET_ID);
  expect(await bodyOf(calls("POST", FINALIZE_URL)[0] as [string, { body?: unknown }])).toEqual({
    dataset_name: "my-dataset",
  });
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  expect(unexpected).toEqual([]);
});

test("negative_acceptance: content type is the explicit selection, never inferred from the file name", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing()));
  await selectFileAndStart(testFile(FILE_BYTES, "looks-like-a.csv"));
  await waitFor(() => expect(calls("POST", SESSIONS_URL).length).toBe(1));
  const body = await bodyOf(calls("POST", SESSIONS_URL)[0] as [string, { body?: unknown }]);
  expect((body as { declared_content_type: string }).declared_content_type).toBe(
    "application/json",
  );
});

test("a double click on Start creates exactly one session", async () => {
  renderUI();
  seedFreshUpload();
  fireEvent.change(screen.getByTestId("upload-file-input"), {
    target: { files: [testFile(FILE_BYTES)] },
  });
  const button = screen.getByTestId("upload-start-button");
  // Both clicks land before React re-renders, as a fast double click does.
  act(() => {
    button.click();
    button.click();
  });
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());
  expect(calls("POST", SESSIONS_URL).length).toBe(1);
});

test("a double click on Finalize sends exactly one finalize request", async () => {
  await uploadToReadyToFinalize();
  fireEvent.change(screen.getByTestId("upload-dataset-name-input"), {
    target: { value: "my-dataset" },
  });
  enqueue("POST", FINALIZE_URL, ok(datasetBody(), 201));
  const button = screen.getByTestId("upload-finalize-button");
  act(() => {
    button.click();
    button.click();
  });
  await waitFor(() => expect(screen.getByTestId("upload-accepted")).toBeTruthy());
  expect(calls("POST", FINALIZE_URL).length).toBe(1);
});

// ---------------------------------------------------------------------------
// (d) no simulated progress or success
// ---------------------------------------------------------------------------

test("a dropped connection mid-upload is interrupted, never success; resume uploads only the missing part", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing()), ok(listing([1])));
  enqueue("POST", PART_URLS_URL, ok(grants([1, 2])), ok(grants([2])));
  enqueue("PUT", PART1_URL, ok({}));
  enqueue("PUT", PART2_URL, "throw", ok({}));
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId("upload-interrupted")).toBeTruthy());
  expect(screen.queryByTestId("upload-all-parts-uploaded")).toBeNull();
  expect(screen.queryByTestId("upload-finalize-button")).toBeNull();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();

  fireEvent.click(screen.getByTestId("upload-resume-button"));
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());
  expect(await bodyOf(calls("POST", PART_URLS_URL)[1] as [string, { body?: unknown }])).toEqual({
    part_numbers: [2],
  });
  expect(calls("PUT", PART1_URL).length).toBe(1);
  expect(calls("PUT", PART2_URL).length).toBe(2);
});

test("UF4: a 403 on a part PUT is not counted, not 'All parts uploaded.', and keeps the draft", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing()));
  enqueue("POST", PART_URLS_URL, ok(grants([1, 2])));
  enqueue("PUT", PART1_URL, ok({}));
  enqueue("PUT", PART2_URL, { status: 403, body: {} });
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId("upload-interrupted")).toBeTruthy());
  expect(screen.getByTestId("upload-interrupted-text").textContent).toContain("after 1 of 2 parts");
  expect(screen.getByTestId("upload-interrupted-reason").textContent).toContain("403");
  expect(screen.queryByTestId("upload-all-parts-uploaded")).toBeNull();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test("UF8: a thrown finalize is never accepted and never clears the draft", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("my-dataset", "throw");
  await waitFor(() => expect(screen.getByTestId("upload-finalize-retry-text")).toBeTruthy());
  expect(screen.queryByTestId("upload-accepted")).toBeNull();
  expect(screen.queryByText("Dataset created.")).toBeNull();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test.each([200, 202, 204, 203])(
  "a finalize reply with status %i and a valid-looking Dataset is not 'Dataset created.'",
  async (status) => {
    await uploadToReadyToFinalize();
    await finalizeWith("my-dataset", ok(datasetBody(), status));
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expect(screen.queryByText("Dataset created.")).toBeNull();
    expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
  },
);

test.each([201, 200])(
  "the create reply with status %i and an {upload_incomplete}-shaped body is malformed, no draft saved",
  async (status) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, ok({ detail: "upload_incomplete" }, status));
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
    expect(unexpected).toEqual([]);
  },
);

test.each([200, 202, 204])(
  "the create reply with status %i and a fully valid session body is malformed (only 201 creates)",
  async (status) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, ok(session(), status));
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
    expect(unexpected).toEqual([]);
  },
);

test.each([201, 202, 204])(
  "a parts listing with status %i and a fully valid body is malformed (only 200 lists)",
  async (status) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, ok(session(), 201));
    enqueue("GET", PARTS_URL, ok(listing([1]), status));
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expect(calls("POST", PART_URLS_URL).length).toBe(0);
    expect(unexpected).toEqual([]);
  },
);

test.each([201, 202, 204])(
  "part-url grants with status %i and a fully valid body are malformed, no PUT (only 200 grants)",
  async (status) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, ok(session(), 201));
    enqueue("GET", PARTS_URL, ok(listing()));
    enqueue("POST", PART_URLS_URL, ok(grants([1, 2]), status));
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expect(calls("PUT", PART1_URL).length + calls("PUT", PART2_URL).length).toBe(0);
    expect(unexpected).toEqual([]);
  },
);

test.each([201, 202, 204])(
  "a resumed session read with status %i and a fully valid body is malformed (only 200 reads)",
  async (status) => {
    seedDraft();
    renderUI();
    enqueue("GET", SESSION_URL, ok(session(), status));
    fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
      target: { files: [testFile(FILE_BYTES)] },
    });
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expect(calls("GET", PARTS_URL).length).toBe(0);
    expect(unexpected).toEqual([]);
  },
);

// 101 one-byte parts: the component asks for part URLs in batches of 100, and the storage
// origin pinned by the first batch must hold for the second.
async function manyPartsFixture() {
  const bytes = new Uint8Array(101).map((_, i) => i);
  const sha = await sha256Hex(testFile(bytes));
  const many = (overrides: Json = {}) =>
    session({
      declared_size_bytes: 101,
      declared_sha256: sha,
      part_size_bytes: 1,
      part_count: 101,
      ...overrides,
    });
  const manyGrant = (n: number, origin: string) => ({
    part_number: n,
    method: "PUT",
    url: `${origin}/p${n}`,
    content_length: 1,
    expires_at: "2026-10-09T00:05:00+00:00",
  });
  return { bytes, many, manyGrant };
}

test("101 parts go out in two batches of part-url requests, all on one origin, and reach finalize", async () => {
  const { bytes, many, manyGrant } = await manyPartsFixture();
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(many(), 201));
  enqueue("GET", PARTS_URL, ok({ part_size_bytes: 1, part_count: 101, parts: [] }));
  enqueue(
    "POST",
    PART_URLS_URL,
    (init) => {
      const wanted = (JSON.parse(String(init?.body)) as { part_numbers: number[] }).part_numbers;
      return ok({ parts: wanted.map((n) => manyGrant(n, "https://a.example")) });
    },
    (init) => {
      const wanted = (JSON.parse(String(init?.body)) as { part_numbers: number[] }).part_numbers;
      return ok({ parts: wanted.map((n) => manyGrant(n, "https://a.example")) });
    },
  );
  for (let n = 1; n <= 101; n += 1) enqueue("PUT", `https://a.example/p${n}`, ok({}));
  await selectFileAndStart(testFile(bytes));
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());
  expect(calls("POST", PART_URLS_URL).length).toBe(2);
  expect(unexpected).toEqual([]);
});

test("a second batch of grants on another storage origin is malformed; none of its parts is PUT", async () => {
  const { bytes, many, manyGrant } = await manyPartsFixture();
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(many(), 201));
  enqueue("GET", PARTS_URL, ok({ part_size_bytes: 1, part_count: 101, parts: [] }));
  enqueue(
    "POST",
    PART_URLS_URL,
    (init) => {
      const wanted = (JSON.parse(String(init?.body)) as { part_numbers: number[] }).part_numbers;
      return ok({ parts: wanted.map((n) => manyGrant(n, "https://a.example")) });
    },
    ok({ parts: [manyGrant(101, "https://evil.example")] }),
  );
  for (let n = 1; n <= 100; n += 1) enqueue("PUT", `https://a.example/p${n}`, ok({}));
  await selectFileAndStart(testFile(bytes));
  await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
  expect(calls("PUT", "https://evil.example/p101").length).toBe(0);
  expect(unexpected).toEqual([]);
});

test("a configured storageOrigin refuses grants for any other origin", async () => {
  renderUI({ storageOrigin: "https://elsewhere.example" } as Partial<typeof PROPS>);
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing()));
  enqueue("POST", PART_URLS_URL, ok(grants([1, 2])));
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
  expect(calls("PUT", PART1_URL).length).toBe(0);
});

test("UF3: a parts listing that claims a part of the wrong size is malformed, not progress", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue(
    "GET",
    PARTS_URL,
    ok(
      listing([], {
        parts: [
          { part_number: 1, size_bytes: 1 },
          { part_number: 2, size_bytes: 2 },
        ],
      }),
    ),
  );
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
  expect(screen.queryByTestId("upload-all-parts-uploaded")).toBeNull();
  expect(unexpected).toEqual([]);
});

test("a listing that names every part resumes straight to finalize with zero part-url requests", async () => {
  seedDraft();
  renderUI();
  enqueue("GET", SESSION_URL, ok(session()));
  enqueue("GET", PARTS_URL, ok(listing([1, 2])));
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(FILE_BYTES)] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());
  expect(calls("POST", PART_URLS_URL).length).toBe(0);
});

// ---------------------------------------------------------------------------
// (a) + (b) generated strict-parse / binding tables
// ---------------------------------------------------------------------------

type FieldKind = "uuid" | "int" | "str" | "enum" | "ts" | "sha" | "nstr" | "nts" | "const";
interface FieldSpec {
  kind: FieldKind;
  enumValues?: readonly string[];
  /** valid-typed but different values: the reply is "bound" to something else */
  alternates?: unknown[];
}

const NOT_OBJECT = [null, [], "x", 5, true];

function wrongValues(spec: FieldSpec): { label: string; value: unknown }[] {
  const out: unknown[] = [];
  const common = [null, true, false, [], {}, ["x"], { a: 1 }];
  switch (spec.kind) {
    case "uuid":
      out.push(...common, 1, "", "not-a-uuid", `${OTHER_UUID}0`, ` ${OTHER_UUID}`);
      break;
    case "int":
      out.push(...common, "1", "", 1.5, -1, 0, 2 ** 53);
      break;
    case "str":
      out.push(...common, 1, 1.5, "");
      break;
    case "enum":
      out.push(...common, 1, "", "bogus", String(spec.enumValues?.[0]).toUpperCase());
      break;
    case "ts":
      out.push(...common, 1, "", "not-a-date");
      break;
    case "sha":
      out.push(...common, 1, "", "abc", "g".repeat(64), "A".repeat(64), "a".repeat(63));
      break;
    case "nstr":
      out.push(true, false, 1, [], {}, ["x"]);
      break;
    case "nts":
      out.push(true, 1, [], {}, "", "not-a-date");
      break;
    case "const":
      out.push(...common, 1, "", "bogus");
      break;
  }
  return out.map((value) => ({ label: JSON.stringify(value) ?? "undefined", value }));
}

interface Case {
  name: string;
  body: Json;
}

function buildCases(valid: () => Json, fields: Record<string, FieldSpec>): Case[] {
  const cases: Case[] = [];
  for (const [field, spec] of Object.entries(fields)) {
    const missing = valid();
    delete missing[field];
    cases.push({ name: `${field} missing`, body: missing });
    for (const { label, value } of wrongValues(spec)) {
      cases.push({ name: `${field} = ${label}`, body: { ...valid(), [field]: value } });
    }
    for (const alternate of spec.alternates ?? []) {
      cases.push({
        name: `${field} bound to a different value ${JSON.stringify(alternate)}`,
        body: { ...valid(), [field]: alternate },
      });
    }
  }
  return cases;
}

const OTHER_PURPOSE = ALLOWED_PURPOSES[1];
const OTHER_CONTENT_TYPE = SUPPORTED_CONTENT_TYPES[1];

const SESSION_FIELDS: Record<string, FieldSpec> = {
  id: { kind: "uuid" },
  project_id: { kind: "uuid", alternates: [OTHER_PROJECT_ID] },
  dataset_source_connection_id: { kind: "uuid", alternates: [OTHER_CONNECTION_ID] },
  purpose: { kind: "enum", enumValues: ALLOWED_PURPOSES, alternates: [OTHER_PURPOSE] },
  status: { kind: "enum", enumValues: SESSION_STATUSES },
  declared_size_bytes: { kind: "int", alternates: [6, 4] },
  declared_sha256: { kind: "sha", alternates: [OTHER_HASH] },
  declared_content_type: {
    kind: "enum",
    enumValues: SUPPORTED_CONTENT_TYPES,
    alternates: [OTHER_CONTENT_TYPE],
  },
  rejection_reason: { kind: "nstr" },
  created_at: { kind: "ts" },
  expires_at: { kind: "ts" },
  finalized_at: { kind: "nts" },
  part_size_bytes: { kind: "int", alternates: [2, 5] },
  part_count: { kind: "int", alternates: [1, 3] },
};

const CREATE_CASES: Case[] = [
  ...buildCases(() => session(), SESSION_FIELDS),
  // self-consistent but not positive integers: the plan check alone must not admit them
  {
    name: "part_size_bytes = -1 with a consistent part_count = -5",
    body: session({ part_size_bytes: -1, part_count: -5 }),
  },
  {
    name: "part_size_bytes = 1.5 with a consistent part_count = 4",
    body: session({ part_size_bytes: 1.5, part_count: 4 }),
  },
  {
    name: "part_size_bytes = 2 ** 53 (not a safe integer) with a consistent part_count = 1",
    body: session({ part_size_bytes: 2 ** 53, part_count: 1 }),
  },
  // the create reply must be an OPEN session and the new id may be anything
  { name: "status bound to open (verified)", body: session({ status: "verified" }) },
  { name: "status bound to open (rejected)", body: session({ status: "rejected" }) },
  { name: "status bound to open (expired)", body: session({ status: "expired" }) },
  ...NOT_OBJECT.map((value) => ({
    name: `body = ${JSON.stringify(value)}`,
    body: value as unknown as Json,
  })),
].filter((c) => c.name !== "id bound to a different value");

test.each(CREATE_CASES)("create reply strict parse + binding: $name -> malformed", async (c) => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(c.body, 201));
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
  expectMalformedAndNoActionControl();
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  expect(unexpected).toEqual([]);
});

const RESUME_READ_CASES: Case[] = [
  ...buildCases(() => session(), {
    ...SESSION_FIELDS,
    id: { kind: "uuid", alternates: [OTHER_UUID] },
    // any status is a valid read on resume (it routes the terminal states), so no alternates
    status: { kind: "enum", enumValues: SESSION_STATUSES },
  }),
  ...NOT_OBJECT.map((value) => ({
    name: `body = ${JSON.stringify(value)}`,
    body: value as unknown as Json,
  })),
];

test.each(RESUME_READ_CASES)(
  "resume read strict parse + binding to the draft: $name -> malformed",
  async (c) => {
    seedDraft();
    renderUI();
    enqueue("GET", SESSION_URL, ok(c.body));
    fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
      target: { files: [testFile(FILE_BYTES)] },
    });
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expectMalformedAndNoActionControl();
    expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
    expect(unexpected).toEqual([]);
  },
);

const PARTS_FIELDS: Record<string, FieldSpec> = {
  part_size_bytes: { kind: "int", alternates: [4, 2] },
  part_count: { kind: "int", alternates: [3, 1] },
};
const PARTS_CASES: Case[] = [
  ...buildCases(() => listing([1]), PARTS_FIELDS),
  {
    name: "parts missing",
    body: (() => {
      const b = listing([1]);
      delete b.parts;
      return b;
    })(),
  },
  ...[null, true, 1, "x", {}].map((value) => ({
    name: `parts = ${JSON.stringify(value)}`,
    body: listing([1], { parts: value }),
  })),
  ...[null, [], "x", 5, true].map((value) => ({
    name: `parts[0] = ${JSON.stringify(value)}`,
    body: listing([1], { parts: [value] }),
  })),
  ...wrongValues({ kind: "int" }).map(({ label, value }) => ({
    name: `parts[0].part_number = ${label}`,
    body: listing([], { parts: [{ part_number: value, size_bytes: 3 }] }),
  })),
  ...wrongValues({ kind: "int" }).map(({ label, value }) => ({
    name: `parts[0].size_bytes = ${label}`,
    body: listing([], { parts: [{ part_number: 1, size_bytes: value }] }),
  })),
  { name: "parts[0].part_number missing", body: listing([], { parts: [{ size_bytes: 3 }] }) },
  { name: "parts[0].size_bytes missing", body: listing([], { parts: [{ part_number: 1 }] }) },
  {
    name: "parts[0].part_number out of range (3)",
    body: listing([], { parts: [{ part_number: 3, size_bytes: 3 }] }),
  },
  {
    name: "parts[0].part_number out of range (0)",
    body: listing([], { parts: [{ part_number: 0, size_bytes: 3 }] }),
  },
  {
    name: "parts[0].size_bytes is not the plan size",
    body: listing([], { parts: [{ part_number: 1, size_bytes: 2 }] }),
  },
  {
    name: "the last part has the full part size",
    body: listing([], { parts: [{ part_number: 2, size_bytes: 3 }] }),
  },
  {
    name: "duplicate part number",
    body: listing([], {
      parts: [
        { part_number: 1, size_bytes: 3 },
        { part_number: 1, size_bytes: 3 },
      ],
    }),
  },
  ...NOT_OBJECT.map((value) => ({
    name: `body = ${JSON.stringify(value)}`,
    body: value as unknown as Json,
  })),
];

test.each(PARTS_CASES)(
  "parts listing strict parse + binding to the plan: $name -> malformed",
  async (c) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, ok(session(), 201));
    enqueue("GET", PARTS_URL, ok(c.body));
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expectMalformedAndNoActionControl();
    expect(calls("POST", PART_URLS_URL).length).toBe(0);
    expect(unexpected).toEqual([]);
  },
);

function grantReply(overrides: Json = {}): Json {
  return { parts: [grant(1), grant(2)], ...overrides };
}

const GRANT_ITEM_FIELDS: Record<string, FieldSpec> = {
  part_number: { kind: "int", alternates: [3, 2] },
  method: { kind: "const", alternates: ["GET", "POST", "put", "DELETE"] },
  url: { kind: "str" },
  content_length: { kind: "int", alternates: [99, 2] },
  expires_at: { kind: "ts" },
};
const GRANT_CASES: Case[] = [
  ...buildCases(() => ({ x: 1 }), {}),
  ...Object.entries(GRANT_ITEM_FIELDS).flatMap(([field, spec]) => [
    {
      name: `parts[0].${field} missing`,
      body: grantReply({
        parts: [
          (() => {
            const g = grant(1);
            delete g[field];
            return g;
          })(),
          grant(2),
        ],
      }),
    },
    ...wrongValues(spec).map(({ label, value }) => ({
      name: `parts[0].${field} = ${label}`,
      body: grantReply({ parts: [grant(1, { [field]: value }), grant(2)] }),
    })),
    ...(spec.alternates ?? []).map((alternate) => ({
      name: `parts[0].${field} bound to a different value ${JSON.stringify(alternate)}`,
      body: grantReply({ parts: [grant(1, { [field]: alternate }), grant(2)] }),
    })),
  ]),
  ...[
    "not a url",
    "ftp://storage.example/part1",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "https://user:pw@storage.example/part1",
    "/relative/path",
  ].map((value) => ({
    name: `parts[0].url = ${value}`,
    body: grantReply({ parts: [grant(1, { url: value }), grant(2)] }),
  })),
  // every grant carries the same bad URL scheme, so the single-origin rule cannot catch it
  ...[
    "ftp://storage.example/part",
    "file:///tmp/part",
    "javascript:alert(1)",
    "data:text/plain,hello",
    "blob:https://storage.example/0000",
    "ws://storage.example/part",
  ].map((prefix) => ({
    name: `both grants use the scheme of ${prefix}`,
    body: grantReply({
      parts: [grant(1, { url: `${prefix}1` }), grant(2, { url: `${prefix}2` })],
    }),
  })),
  // a one-element array stringifies to a valid URL: the type check must refuse it
  {
    name: "parts[0].url is an array holding a valid URL",
    body: grantReply({ parts: [grant(1, { url: [PART1_URL] }), grant(2)] }),
  },
  {
    name: "grants spread over two storage origins",
    body: grantReply({ parts: [grant(1), grant(2, { url: "https://evil.example/part2" })] }),
  },
  { name: "a requested part has no grant", body: grantReply({ parts: [grant(1)] }) },
  {
    name: "an unrequested extra grant",
    body: grantReply({ parts: [grant(1), grant(2), grant(2)] }),
  },
  { name: "the same part granted twice", body: grantReply({ parts: [grant(1), grant(1)] }) },
  { name: "empty grants", body: grantReply({ parts: [] }) },
  { name: "parts missing", body: {} },
  ...[null, true, 1, "x", {}].map((value) => ({
    name: `parts = ${JSON.stringify(value)}`,
    body: grantReply({ parts: value }),
  })),
  ...[null, [], "x", 5, true].map((value) => ({
    name: `parts[0] = ${JSON.stringify(value)}`,
    body: grantReply({ parts: [value, grant(2)] }),
  })),
  ...NOT_OBJECT.map((value) => ({
    name: `body = ${JSON.stringify(value)}`,
    body: value as unknown as Json,
  })),
].filter((c) => c.name !== "x");

test.each(GRANT_CASES)(
  "part-url grants strict parse + binding to the request: $name -> malformed, no PUT",
  async (c) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, ok(session(), 201));
    enqueue("GET", PARTS_URL, ok(listing()));
    enqueue("POST", PART_URLS_URL, ok(c.body));
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expectMalformedAndNoActionControl();
    expect(calls("PUT", PART1_URL).length + calls("PUT", PART2_URL).length).toBe(0);
    expect(unexpected).toEqual([]);
  },
);

const DATASET_FIELDS: Record<string, FieldSpec> = {
  id: { kind: "uuid" },
  organization_id: { kind: "uuid", alternates: [OTHER_ORG_ID] },
  project_id: { kind: "uuid", alternates: [OTHER_PROJECT_ID] },
  name: { kind: "str", alternates: ["another-name", "MY-DATASET", " my-dataset"] },
  version: { kind: "int" },
  purpose: { kind: "enum", enumValues: ALLOWED_PURPOSES, alternates: [OTHER_PURPOSE] },
  upload_session_id: { kind: "uuid", alternates: [OTHER_UUID] },
  verified_sha256: { kind: "sha", alternates: [OTHER_HASH] },
  verified_size_bytes: { kind: "int", alternates: [6, 4] },
  created_by: { kind: "str" },
  created_at: { kind: "ts" },
};
const DATASET_CASES: Case[] = [
  ...buildCases(() => datasetBody(), DATASET_FIELDS),
  ...NOT_OBJECT.map((value) => ({
    name: `body = ${JSON.stringify(value)}`,
    body: value as unknown as Json,
  })),
  { name: "an upload_incomplete-shaped 2xx", body: { detail: "upload_incomplete" } },
  { name: "an empty object", body: {} },
];

test.each(DATASET_CASES)(
  "finalize Dataset strict parse + binding: $name -> malformed, never 'Dataset created.'",
  async (c) => {
    await uploadToReadyToFinalize();
    await finalizeWith("my-dataset", ok(c.body, 201));
    await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
    expectMalformedAndNoActionControl();
    // the finalize may have taken effect: the draft stays until the status is re-read
    expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
    expect(unexpected).toEqual([]);
  },
);

test.each(
  Object.entries(SESSION_FIELDS).flatMap(([field, spec]) => [
    {
      name: `${field} missing`,
      body: (() => {
        const b = session();
        delete b[field];
        return b;
      })(),
    },
    ...wrongValues(spec).map(({ label, value }) => ({
      name: `${field} = ${label}`,
      body: session({ [field]: value }),
    })),
  ]),
)("parseUploadSession alone refuses: $name", (c) => {
  expect(parseUploadSession(c.body)).toBeNull();
});

test.each([
  ["part_size_bytes -1 / part_count -5", { part_size_bytes: -1, part_count: -5 }],
  ["part_size_bytes 1.5 / part_count 4", { part_size_bytes: 1.5, part_count: 4 }],
  ["declared_size_bytes 0 / part_count 0", { declared_size_bytes: 0, part_count: 0 }],
  ["declared_size_bytes -3 / part_count -1", { declared_size_bytes: -3, part_count: -1 }],
  ["declared_size_bytes 1.5 / part_count 1", { declared_size_bytes: 1.5, part_count: 1 }],
])(
  "parseUploadSession alone refuses a self-consistent non-positive plan: %s",
  (_label, override) => {
    expect(parseUploadSession(session(override))).toBeNull();
  },
);

const FAKE_SESSION = (overrides: Json = {}) => ({
  ...(parseUploadSession(session()) as NonNullable<ReturnType<typeof parseUploadSession>>),
  ...overrides,
});

test.each([
  ["name is empty (expected name also empty)", { name: "" }, {}, ""],
  [
    "purpose outside the enum (session purpose also outside)",
    { purpose: "bogus" },
    { purpose: "bogus" },
    "my-dataset",
  ],
  [
    "verified_sha256 malformed (session sha also malformed)",
    { verified_sha256: "zz" },
    { declared_sha256: "zz" },
    "my-dataset",
  ],
  [
    "verified_size_bytes 0 (session size also 0)",
    { verified_size_bytes: 0 },
    { declared_size_bytes: 0 },
    "my-dataset",
  ],
  [
    "verified_size_bytes 1.5 (session size also 1.5)",
    { verified_size_bytes: 1.5 },
    { declared_size_bytes: 1.5 },
    "my-dataset",
  ],
])(
  "parseUploadedDataset alone refuses when the binding would otherwise agree: %s",
  (_label, bodyOverride, sessionOverride, name) => {
    const bound = FAKE_SESSION(sessionOverride) as Parameters<
      typeof parseUploadedDataset
    >[1]["session"];
    expect(
      parseUploadedDataset(datasetBody({ name: name || "x", ...bodyOverride }), {
        organizationId: ORG_ID,
        session: bound,
        datasetName: name,
      }),
    ).toBeNull();
  },
);

test("parsePartsListing alone refuses a non-positive size even when the plan says the same", () => {
  // part 2 of a 3-byte file in 3-byte parts has a planned size of 0
  const odd = FAKE_SESSION({
    declared_size_bytes: 3,
    part_size_bytes: 3,
    part_count: 2,
  }) as Parameters<typeof parsePartsListing>[1];
  expect(
    parsePartsListing(
      { part_size_bytes: 3, part_count: 2, parts: [{ part_number: 2, size_bytes: 0 }] },
      odd,
    ),
  ).toBeNull();
});

test("parsePartUrlGrants alone refuses a url that is not a string but stringifies to one", () => {
  const parsed = parseUploadSession(session())!;
  expect(
    parsePartUrlGrants({ parts: [grant(1, { url: [PART1_URL] }), grant(2)] }, [1, 2], parsed, null),
  ).toBeNull();
});

test("table bases are valid: every generated case below differs from a reply that is accepted", () => {
  // Guards the tables against going vacuous (a broken base makes every case "malformed").
  const parsed = parseUploadSession(session());
  expect(parsed).not.toBeNull();
  const expectation = { ...EXPECTATION, declaredSha256: HASH };
  expect(sessionMatches(parsed!, expectation)).toBe(true);
  expect(sessionMatches(parsed!, { ...expectation, id: undefined as unknown as string })).toBe(
    true,
  );
  expect(parsePartsListing(listing([1]), parsed!)?.length).toBe(1);
  expect(parsePartUrlGrants(grantReply(), [1, 2], parsed!, null)).not.toBeNull();
  expect(
    parseUploadedDataset(datasetBody(), {
      organizationId: ORG_ID,
      session: parsed!,
      datasetName: "my-dataset",
    }),
  ).not.toBeNull();
  for (const status of SESSION_STATUSES) {
    expect(parseUploadSession(session({ status }))).not.toBeNull();
  }
});

test("a non-JSON 2xx finalize body is malformed, not success", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("my-dataset", { status: 201, jsonThrows: true });
  await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
  expect(screen.queryByText("Dataset created.")).toBeNull();
});

test("malformed finalize: Check upload status finds the verified session and shows a terminal state", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("my-dataset", ok({}, 201));
  await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
  enqueue(
    "GET",
    SESSION_URL,
    ok(session({ status: "verified", finalized_at: "2026-10-09T00:03:00+00:00" })),
  );
  fireEvent.click(screen.getByTestId("upload-check-status-button"));
  await waitFor(() => expect(screen.getByTestId("upload-already-verified-text")).toBeTruthy());
  expect(screen.queryByText("Dataset created.")).toBeNull();
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
});

test("malformed: Back returns to the resume banner when a draft exists", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("my-dataset", ok({}, 201));
  await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
  fireEvent.click(screen.getByTestId("upload-malformed-back-button"));
  expect(screen.getByTestId("upload-resume-banner")).toBeTruthy();
});

// ---------------------------------------------------------------------------
// (b) the ONE binding function: a fault per field sent
// ---------------------------------------------------------------------------

const EXPECTATION = {
  id: SESSION_ID,
  projectId: PROJECT_ID,
  datasetSourceConnectionId: CONNECTION_ID,
  purpose: "training",
  declaredSizeBytes: 5,
  declaredSha256: "",
  declaredContentType: "application/json",
  status: "open" as const,
};

test("sessionMatches accepts the exact reply and refuses each field changed", () => {
  const parsed = parseUploadSession(session());
  expect(parsed).not.toBeNull();
  const expectation = { ...EXPECTATION, declaredSha256: HASH };
  expect(sessionMatches(parsed!, expectation)).toBe(true);
  const flips: [string, Json][] = [
    ["id", { id: OTHER_UUID }],
    ["project_id", { project_id: OTHER_PROJECT_ID }],
    ["dataset_source_connection_id", { dataset_source_connection_id: OTHER_CONNECTION_ID }],
    ["purpose", { purpose: OTHER_PURPOSE }],
    ["declared_size_bytes", { declared_size_bytes: 6 }],
    ["declared_sha256", { declared_sha256: OTHER_HASH }],
    ["declared_content_type", { declared_content_type: OTHER_CONTENT_TYPE }],
    ["status", { status: "verified" }],
  ];
  for (const [field, override] of flips) {
    const changed = parseUploadSession(session(override));
    expect(changed, field).not.toBeNull();
    expect(sessionMatches(changed!, expectation), field).toBe(false);
  }
});

test("parseUploadedDataset accepts the exact Dataset and refuses each bound field changed", () => {
  const parsed = parseUploadSession(session())!;
  const expected = { organizationId: ORG_ID, session: parsed, datasetName: "my-dataset" };
  expect(parseUploadedDataset(datasetBody(), expected)).not.toBeNull();
  const flips: Json[] = [
    { organization_id: OTHER_ORG_ID },
    { project_id: OTHER_PROJECT_ID },
    { name: "other" },
    { purpose: OTHER_PURPOSE },
    { upload_session_id: OTHER_UUID },
    { verified_sha256: OTHER_HASH },
    { verified_size_bytes: 6 },
  ];
  for (const override of flips) {
    expect(
      parseUploadedDataset(datasetBody(override), expected),
      JSON.stringify(override),
    ).toBeNull();
  }
});

test("parsePartsListing and parsePartUrlGrants accept the exact replies", () => {
  const parsed = parseUploadSession(session())!;
  expect(parsePartsListing(listing([1, 2]), parsed)?.length).toBe(2);
  expect(parsePartUrlGrants(grants([1, 2]), [1, 2], parsed, null)?.origin).toBe(
    "https://storage.example",
  );
  expect(parsePartUrlGrants(grants([1, 2]), [1, 2], parsed, "https://other.example")).toBeNull();
});

// ---------------------------------------------------------------------------
// (c) identity
// ---------------------------------------------------------------------------

test("the draft key carries organization, project and connection", () => {
  const keys = new Set([
    draftStorageKey(ORG_ID, PROJECT_ID, CONNECTION_ID),
    draftStorageKey(OTHER_ORG_ID, PROJECT_ID, CONNECTION_ID),
    draftStorageKey(ORG_ID, OTHER_PROJECT_ID, CONNECTION_ID),
    draftStorageKey(ORG_ID, PROJECT_ID, OTHER_CONNECTION_ID),
  ]);
  expect(keys.size).toBe(4);
});

test("an organization-1 draft is not offered or resumed under organization 2", () => {
  seedDraft();
  renderUI({ organizationId: OTHER_ORG_ID });
  expect(screen.queryByTestId("upload-resume-banner")).toBeNull();
  expect(screen.getByTestId("upload-form")).toBeTruthy();
  expect(fetchCalls().length).toBe(0);
});

test.each([
  ["organization", { organizationId: OTHER_ORG_ID }],
  ["project", { projectId: OTHER_PROJECT_ID }],
  ["connection", { datasetSourceConnectionId: OTHER_CONNECTION_ID }],
])("a draft stored under another %s inside the right key is ignored", (_label, drift) => {
  seedDraft(drift);
  renderUI();
  expect(screen.queryByTestId("upload-resume-banner")).toBeNull();
  expect(screen.getByTestId("upload-form")).toBeTruthy();
});

test.each([
  ["organization", { organizationId: OTHER_ORG_ID }],
  ["project", { projectId: OTHER_PROJECT_ID }],
  ["connection", { datasetSourceConnectionId: OTHER_CONNECTION_ID }],
  ["API base", { apiBaseUrl: "http://other.example:9000" }],
])(
  "a changed %s resets the component: no finalize control, no request for the old session",
  async (_label, change) => {
    const view = await uploadToReadyToFinalize();
    const before = fetchCalls().length;
    view.rerender(createElement(ResumableDatasetUploadUI, { ...PROPS, ...change }));
    expect(screen.queryByTestId("upload-finalize-button")).toBeNull();
    expect(screen.queryByTestId("upload-all-parts-uploaded")).toBeNull();
    // The draft slot is keyed on organization/project/connection only, so a changed API base
    // lands on the resume banner (re-pick the file); the others on a fresh form.
    const landing = _label === "API base" ? "upload-resume-banner" : "upload-form";
    expect(screen.getByTestId(landing)).toBeTruthy();
    expect(fetchCalls().length).toBe(before);
  },
);

test("an in-flight create reply that lands after the identity changed does nothing", async () => {
  let release: (value: Plain) => void = () => undefined;
  const pending = new Promise<Plain>((resolve) => {
    release = resolve;
  });
  const view = renderUI();
  enqueue("POST", SESSIONS_URL, pending);
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(calls("POST", SESSIONS_URL).length).toBe(1));
  view.rerender(createElement(ResumableDatasetUploadUI, { ...PROPS, projectId: OTHER_PROJECT_ID }));
  release(ok(session(), 201));
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(calls("GET", PARTS_URL).length).toBe(0);
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  expect(unexpected).toEqual([]);
});

test("resume: a reply with another session id than the draft is malformed", async () => {
  seedDraft();
  renderUI();
  enqueue("GET", SESSION_URL, ok(session({ id: OTHER_UUID })));
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(FILE_BYTES)] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-malformed-text")).toBeTruthy());
  expect(calls("GET", PARTS_URL).length).toBe(0);
});

// ---------------------------------------------------------------------------
// (e) typed errors (Law 17)
// ---------------------------------------------------------------------------

const RETRYABLE_REPLIES: [string, Plain][] = [
  ["500 unknown code", fail(500, "boom")],
  ["500 no detail", { status: 500, body: {} }],
  ["500 non-JSON", { status: 500, jsonThrows: true }],
  ["502 non-JSON", { status: 502, jsonThrows: true }],
  ["504 unknown code", fail(504, "gateway_timeout")],
  ["503 unknown code", fail(503, "overloaded")],
  ["401", fail(401, "unauthenticated")],
  ["401 non-JSON", { status: 401, jsonThrows: true }],
  ["403", fail(403, "forbidden")],
  ["429", fail(429, "rate_limited")],
  ["418 unknown code", fail(418, "teapot")],
  ["422 unknown rejection reason", fail(422, "some_new_reason")],
  ["409 unknown code", fail(409, "some_new_conflict")],
  ["410 unknown code", fail(410, "gone_somehow")],
  ["404 unknown code", fail(404, "no_such_thing")],
  ["known code on the wrong status (500 size_mismatch)", fail(500, "size_mismatch")],
  ["known code on the wrong status (200-class 400 upload_expired)", fail(400, "upload_expired")],
  ["a non-string detail", { status: 500, body: { detail: 5 } }],
  ["a non-string detail array", { status: 422, body: { detail: [{ msg: "x" }] } }],
];

test.each(RETRYABLE_REPLIES)(
  "finalize %s is retryable: nothing terminal, draft kept, Finalize still offered",
  async (_label, reply) => {
    await uploadToReadyToFinalize();
    await finalizeWith("my-dataset", reply);
    await waitFor(() => expect(screen.getByTestId("upload-finalize-retry-text")).toBeTruthy());
    expect(screen.queryByTestId("upload-rejected-text")).toBeNull();
    expect(screen.queryByTestId("upload-expired-text")).toBeNull();
    expect(screen.queryByTestId("upload-accepted")).toBeNull();
    expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
    expect((screen.getByTestId("upload-finalize-button") as HTMLButtonElement).disabled).toBe(
      false,
    );
    // the retry works and ends accepted
    enqueue("POST", FINALIZE_URL, ok(datasetBody(), 201));
    fireEvent.click(screen.getByTestId("upload-finalize-button"));
    await waitFor(() => expect(screen.getByTestId("upload-accepted")).toBeTruthy());
  },
);

const CLOSED_REPLIES: [string, Plain][] = [
  ["422 size_mismatch", fail(422, "size_mismatch")],
  ["422 sha256_mismatch", fail(422, "sha256_mismatch")],
  ["422 content_type_mismatch", fail(422, "content_type_mismatch")],
  ["422 part_size_mismatch", fail(422, "part_size_mismatch")],
  ["422 unexpected_part", fail(422, "unexpected_part")],
  ["422 unsupported_content_type", fail(422, "unsupported_content_type")],
  ["422 source_revoked", fail(422, "source_revoked")],
  ["422 verification_mismatch", fail(422, "verification_mismatch")],
  ["409 upload_session_not_open", fail(409, "upload_session_not_open")],
  ["404 not_found", fail(404, "not_found")],
];

test.each(CLOSED_REPLIES)(
  "finalize %s is the server's known terminal outcome: shown, draft cleared, never accepted",
  async (_label, reply) => {
    await uploadToReadyToFinalize();
    await finalizeWith("my-dataset", reply);
    await waitFor(() => expect(screen.getByTestId("upload-rejected-text")).toBeTruthy());
    expect(screen.queryByTestId("upload-accepted")).toBeNull();
    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  },
);

test("finalize 410 upload_expired is a visible expired state and clears the draft", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("my-dataset", fail(410, "upload_expired"));
  await waitFor(() => expect(screen.getByTestId("upload-expired-text")).toBeTruthy());
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
});

test("UF6/UF10: finalize 409 dataset_version_conflict keeps the session resumable and a second name succeeds", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("taken", fail(409, "dataset_version_conflict"));
  await waitFor(() => expect(screen.getByTestId("upload-conflict-text")).toBeTruthy());
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
  expect(screen.queryByTestId("upload-rejected-text")).toBeNull();
  await finalizeWith("free-name", ok(datasetBody({ name: "free-name" }), 201));
  await waitFor(() => expect(screen.getByTestId("upload-accepted")).toBeTruthy());
  expect(calls("POST", FINALIZE_URL).length).toBe(2);
});

test("finalize 409 upload_incomplete keeps the draft and offers a re-check", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("my-dataset", fail(409, "upload_incomplete"));
  await waitFor(() => expect(screen.getByTestId("upload-incomplete-text")).toBeTruthy());
  expect(screen.getByTestId("upload-recheck-parts-button")).toBeTruthy();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test("finalize 503 storage_unavailable is an outage, never accepted, draft kept", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("my-dataset", fail(503, "storage_unavailable"));
  await waitFor(() => expect(screen.getByTestId("upload-storage-unavailable-text")).toBeTruthy());
  expect(screen.queryByTestId("upload-accepted")).toBeNull();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test("finalize 400 invalid_body keeps everything: nothing about the session is known to have changed", async () => {
  await uploadToReadyToFinalize();
  await finalizeWith("my-dataset", fail(400, "invalid_body"));
  await waitFor(() => expect(screen.getByTestId("upload-finalize-retry-text")).toBeTruthy());
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test.each(RETRYABLE_REPLIES)(
  "part listing %s is retryable: interrupted, draft kept, Resume works",
  async (_label, reply) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, ok(session(), 201));
    enqueue("GET", PARTS_URL, reply, ok(listing([1, 2])));
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-interrupted")).toBeTruthy());
    expect(screen.queryByTestId("upload-rejected-text")).toBeNull();
    expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
    fireEvent.click(screen.getByTestId("upload-resume-button"));
    await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());
  },
);

test.each(RETRYABLE_REPLIES)(
  "part-urls %s is retryable: interrupted, draft kept",
  async (_label, reply) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, ok(session(), 201));
    enqueue("GET", PARTS_URL, ok(listing()));
    enqueue("POST", PART_URLS_URL, reply);
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-interrupted")).toBeTruthy());
    expect(screen.queryByTestId("upload-rejected-text")).toBeNull();
    expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
  },
);

test("part-urls 410 upload_expired is a visible expired state and clears the draft", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing()));
  enqueue("POST", PART_URLS_URL, fail(410, "upload_expired"));
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId("upload-expired-text")).toBeTruthy());
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
});

test("part listing 409 upload_session_not_open is a closed session: shown, draft cleared", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, fail(409, "upload_session_not_open"));
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId("upload-rejected-text")).toBeTruthy());
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
});

test.each([
  ...RETRYABLE_REPLIES,
  ["403 purpose_not_granted", fail(403, "purpose_not_granted")] as [string, Plain],
])(
  "create %s: a visible error, no draft, and the form can be used again",
  async (_label, reply) => {
    renderUI();
    enqueue("POST", SESSIONS_URL, reply, ok(session(), 201));
    enqueue("GET", PARTS_URL, ok(listing([1, 2])));
    await selectFileAndStart(testFile(FILE_BYTES));
    await waitFor(() => expect(screen.getByTestId("upload-error-text")).toBeTruthy());
    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
    fireEvent.change(screen.getByTestId("upload-file-input"), {
      target: { files: [testFile(FILE_BYTES)] },
    });
    fireEvent.click(screen.getByTestId("upload-start-button"));
    await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());
  },
);

test.each(RETRYABLE_REPLIES)(
  "resume read %s keeps the draft and the banner",
  async (_label, reply) => {
    seedDraft();
    renderUI();
    enqueue("GET", SESSION_URL, reply);
    fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
      target: { files: [testFile(FILE_BYTES)] },
    });
    await waitFor(() => expect(screen.getByTestId("upload-resume-error")).toBeTruthy());
    expect(screen.getByTestId("upload-resume-banner")).toBeTruthy();
    expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
  },
);

test("resume read 404 not_found is a closed session: shown, draft cleared", async () => {
  seedDraft();
  renderUI();
  enqueue("GET", SESSION_URL, fail(404, "not_found"));
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(FILE_BYTES)] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-rejected-text")).toBeTruthy());
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
});

test("UF10: resuming a verified session shows a visible terminal state and clears the draft", async () => {
  seedDraft();
  renderUI();
  enqueue(
    "GET",
    SESSION_URL,
    ok(session({ status: "verified", finalized_at: "2026-10-09T00:03:00+00:00" })),
  );
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(FILE_BYTES)] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-already-verified-text")).toBeTruthy());
  expect(screen.queryByText("Dataset created.")).toBeNull();
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  expect(calls("GET", PARTS_URL).length).toBe(0);
});

test.each([
  ["expired", "upload-expired-text"],
  ["rejected", "upload-rejected-text"],
])(
  "resuming a %s session shows a visible terminal state and clears the draft",
  async (status, testId) => {
    seedDraft();
    renderUI();
    enqueue(
      "GET",
      SESSION_URL,
      ok(session({ status, rejection_reason: status === "rejected" ? "sha256_mismatch" : null })),
    );
    fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
      target: { files: [testFile(FILE_BYTES)] },
    });
    await waitFor(() => expect(screen.getByTestId(testId)).toBeTruthy());
    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  },
);

test("classifyFailure: a known code is terminal only on its own status", () => {
  expect(classifyFailure(422, "sha256_mismatch").kind).toBe("closed");
  expect(classifyFailure(500, "sha256_mismatch").kind).toBe("retryable");
  expect(classifyFailure(500, null).kind).toBe("retryable");
  expect(classifyFailure(410, "upload_expired").kind).toBe("expired");
  expect(classifyFailure(503, "storage_unavailable").kind).toBe("storage_unavailable");
  expect(classifyFailure(503, "something_else").kind).toBe("retryable");
});

// ---------------------------------------------------------------------------
// resume + negatives
// ---------------------------------------------------------------------------

test("acceptance (resume after reload): lists real parts and requests only the missing ones", async () => {
  seedDraft();
  renderUI();
  expect(screen.getByTestId("upload-resume-banner")).toBeTruthy();
  expect(fetchCalls().length).toBe(0);
  enqueue("GET", SESSION_URL, ok(session()));
  enqueue("GET", PARTS_URL, ok(listing([1])));
  enqueue("POST", PART_URLS_URL, ok(grants([2])));
  enqueue("PUT", PART2_URL, ok({}));
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(FILE_BYTES)] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy());
  expect(await bodyOf(calls("POST", PART_URLS_URL)[0] as [string, { body?: unknown }])).toEqual({
    part_numbers: [2],
  });
  expect(calls("PUT", PART1_URL).length).toBe(0);
  expect(calls("PUT", PART2_URL).length).toBe(1);
});

test("negative_acceptance: resuming with a different file is refused before any network call", async () => {
  seedDraft();
  renderUI();
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(OTHER_BYTES)] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-resume-error")).toBeTruthy());
  expect(fetchCalls().length).toBe(0);
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test("FTF-UX02 H5 (sev 3): abandoning a draft needs an explicit confirmation; the first click changes nothing", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  expect(screen.getByTestId("upload-abandon-confirm")).toBeTruthy();
  expect(screen.queryByTestId("upload-form")).toBeNull();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
  expect(fetchCalls().length).toBe(0);
});

test("FTF-UX02 H3: cancelling the abandon confirmation keeps the draft resumable", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  fireEvent.click(screen.getByTestId("upload-abandon-cancel-button"));
  expect(screen.queryByTestId("upload-abandon-confirm")).toBeNull();
  expect(screen.getByTestId("upload-resume-file-input")).toBeTruthy();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
});

test("FTF-UX02 X4: the abandon confirmation is in Arabic and right-to-left when locale is ar", () => {
  seedDraft();
  render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale: "ar" }));
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const confirm = screen.getByTestId("upload-abandon-confirm");
  expect(confirm.closest("[dir]")?.getAttribute("dir")).toBe("rtl");
  expect(confirm.textContent).toMatch(/[\u0600-\u06FF]/);
  expect(screen.getByTestId("upload-abandon-confirm-button").textContent).toMatch(
    /[\u0600-\u06FF]/,
  );
  expect(screen.getByTestId("upload-abandon-cancel-button").textContent).toMatch(/[\u0600-\u06FF]/);
});

test("abandoning a draft clears it and returns to a fresh upload form, with no network call", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  fireEvent.click(screen.getByTestId("upload-abandon-confirm-button"));
  expect(screen.getByTestId("upload-form")).toBeTruthy();
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  expect(fetchCalls().length).toBe(0);
});

// ---------------------------------------------------------------------------
// FTF-UX02 round 2, finding 1 (CONFIRM-FOCUS-LOST) and finding 5
// (CONFIRM-PROMISES-DISCARD): the abandon confirmation is now the shared
// UX01 `Dialog` (Radix focus-scope primitives), not a plain unmanaged <div>.
// ---------------------------------------------------------------------------

test("FTF-UX02 r2 finding 1: opening the abandon confirmation moves focus inside it (real focus trap, not a static div)", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const confirm = screen.getByTestId("upload-abandon-confirm");
  expect(confirm.getAttribute("role")).toBe("alertdialog");
  // A plain <div role="alertdialog"> never receives focus on its own; the old
  // implementation left `document.activeElement` on the trigger button that
  // was just hidden. The real focus-trap primitive moves focus inside.
  expect(confirm.contains(document.activeElement)).toBe(true);
});

test("FTF-UX02 r2 finding 1: the confirmation names the trigger via aria-labelledby/aria-describedby, not a hardcoded aria-label", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const confirm = screen.getByTestId("upload-abandon-confirm");
  const labelledBy = confirm.getAttribute("aria-labelledby");
  // `aria-describedby` may legally be a space-separated list of id references
  // (ARIA spec; Radix itself appends its own internal one) -- check that at
  // least one resolves to the real consequence text, not that there is only one.
  const describedByIds = (confirm.getAttribute("aria-describedby") ?? "").split(/\s+/);
  expect(labelledBy).toBeTruthy();
  expect(describedByIds.length).toBeGreaterThan(0);
  expect(document.getElementById(labelledBy!)?.textContent).toBe("Abandon this draft?");
  const describedTexts = describedByIds.map((id) => document.getElementById(id)?.textContent);
  expect(describedTexts.some((t) => t?.includes("This browser forgets your progress"))).toBe(true);
});

test("FTF-UX02 r2 finding 1: Escape keeps the draft (same effect as Keep draft), never the same effect as confirming abandon", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const confirm = screen.getByTestId("upload-abandon-confirm");
  fireEvent.keyDown(confirm, { key: "Escape" });
  expect(screen.queryByTestId("upload-abandon-confirm")).toBeNull();
  // Still resumable: Escape must not have run the destructive `resetToIdle` path.
  expect(screen.getByTestId("upload-resume-file-input")).toBeTruthy();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
  expect(fetchCalls().length).toBe(0);
});

test("FTF-UX02 r2 finding 5: the confirmation no longer claims the upload itself is discarded (no abort route exists for it)", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const body = screen.getByTestId("upload-abandon-confirm").textContent ?? "";
  // The prior wording ("The partly uploaded file is discarded") is a claim this UI cannot
  // keep -- apps/api/app/datasets/direct_object_storage_upload_routes.py exposes no abort
  // route the browser could call, so confirming never contacts the server (asserted below);
  // the honest, rewritten copy describes only the local, client-side effect.
  expect(body).not.toMatch(/discarded/i);
  expect(body).toContain("This browser forgets your progress");
  fireEvent.click(screen.getByTestId("upload-abandon-confirm-button"));
  expect(fetchCalls().length).toBe(0);
});

test("FTF-UX02 r2: the abandon trigger button text is bilingual, not a hardcoded English string", () => {
  seedDraft();
  render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale: "ar" }));
  const trigger = screen.getByTestId("upload-abandon-draft-button");
  expect(trigger.textContent).toMatch(/[؀-ۿ]/);
});

// ---------------------------------------------------------------------------
// FTF-UX02 round 2, pre-review round 1 (independent L2-style review of PR #947):
// findings 1 (CONFIRM-FOCUS-ON-CONFIRM, pinned initial-focus), 2 (UNPINNED-F4),
// 3 (NOT-BILINGUAL-0500A's dialog half) and the lang-attribute gap.
// ---------------------------------------------------------------------------

test("FTF-UX02 r2 pre-review r1 finding 1 (pinned): initial open focus is the Keep-draft button specifically, not merely 'something inside'", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  // Reordering Keep/Abandon so the destructive button gets initial focus must turn this RED --
  // the prior assertion (`confirm.contains(document.activeElement)`) could not catch that.
  expect(document.activeElement).toBe(screen.getByTestId("upload-abandon-cancel-button"));
});

test("FTF-UX02 r2 pre-review r1 finding 1 (pinned): confirming abandon moves focus to the fresh form's first control, never <body>", async () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  fireEvent.click(screen.getByTestId("upload-abandon-confirm-button"));
  await waitFor(() => expect(screen.getByTestId("upload-form")).toBeTruthy());
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByTestId("upload-purpose-select")),
  );
});

test.each([
  ["en", "Keep draft", "Abandon draft"],
  ["ar", "الاحتفاظ بالمسودة", "تجاهل المسودة"],
])(
  "FTF-UX02 r2 pre-review r1 finding 2 (pinned, UNPINNED-F4): Keep/Abandon button labels are exact in %s",
  (locale, keepText, abandonText) => {
    seedDraft();
    render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale: locale as "en" | "ar" }));
    fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
    expect(screen.getByTestId("upload-abandon-cancel-button").textContent).toBe(keepText);
    expect(screen.getByTestId("upload-abandon-confirm-button").textContent).toBe(abandonText);
  },
);

test("FTF-UX02 r2 pre-review r1 finding 2 (pinned, UNPINNED-F4): the English confirm body includes the exact 'cannot be undone' sentence", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  expect(screen.getByTestId("upload-abandon-confirm").textContent).toContain(
    "This cannot be undone.",
  );
});

test("FTF-UX02 r2 pre-review r1 finding 2 (pinned, UNPINNED-F4): the Arabic confirm body is the exact translated sentence, not the English one", () => {
  seedDraft();
  render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale: "ar" }));
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const text = screen.getByTestId("upload-abandon-confirm").textContent ?? "";
  expect(text).toContain(
    "سينسى هذا المتصفح التقدم الذي أحرزته وسيبدأ رفع جديد من البداية. لا يمكن التراجع عن ذلك.",
  );
  expect(text).not.toContain("This browser forgets");
});

test.each(["en", "ar"] as const)(
  "FTF-UX02 r2 pre-review r1 finding 3 (pinned): the abandon dialog's lang attribute is %s, not left unset",
  (locale) => {
    seedDraft();
    render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale }));
    fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
    expect(screen.getByTestId("upload-abandon-confirm").getAttribute("lang")).toBe(locale);
  },
);

test("FTF-UX02 r2 pre-review r1 finding 2 (pinned, UNPINNED-F4): the resume-error paragraph carries role=alert", async () => {
  seedDraft();
  renderUI();
  enqueue("GET", SESSION_URL, fail(500, null));
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [testFile(FILE_BYTES)] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-resume-error")).toBeTruthy());
  expect(screen.getByTestId("upload-resume-error").getAttribute("role")).toBe("alert");
});

// ---------------------------------------------------------------------------
// FTF-UX02 round 2, pre-review round 2 (independent L2-style re-review of PR #947):
// finding 2 (F3a-UNPINNED + the wider Arabic-string surface) and finding 6
// (STICKY-CHECKING-AND-BUTTON-PINS-UNPINNED's Abandon-variant pin).
// ---------------------------------------------------------------------------

test("FTF-UX02 r2 pre-review r2 finding 2 (F3a-UNPINNED): the Arabic abandon-confirmation title is the exact translation, not left English", () => {
  seedDraft();
  render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale: "ar" }));
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const confirm = screen.getByTestId("upload-abandon-confirm");
  const labelledBy = confirm.getAttribute("aria-labelledby");
  expect(document.getElementById(labelledBy!)?.textContent).toBe("هل تريد تجاهل هذه المسودة؟");
});

test("FTF-UX02 r2 pre-review r2 finding 2: the Dialog's close button has an exact, bilingual aria-label", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const confirm = screen.getByTestId("upload-abandon-confirm");
  const closeButton = Array.from(confirm.querySelectorAll("button")).find(
    (b) => b.textContent === "×",
  );
  expect(closeButton?.getAttribute("aria-label")).toBe("Cancel");
  cleanup();

  seedDraft();
  render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale: "ar" }));
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  const confirmAr = screen.getByTestId("upload-abandon-confirm");
  const closeButtonAr = Array.from(confirmAr.querySelectorAll("button")).find(
    (b) => b.textContent === "×",
  );
  expect(closeButtonAr?.getAttribute("aria-label")).toBe("إلغاء");
});

test("FTF-UX02 r2 pre-review r2 finding 2: the resume banner's text and file name are the exact bilingual strings, the name wrapped in <bdi>", () => {
  seedDraft();
  renderUI();
  const banner = screen.getByTestId("upload-resume-banner");
  expect(banner.textContent).toContain("A previous upload of");
  expect(banner.textContent).toContain("is still in progress. Choose that same file to resume it.");
  expect(banner.querySelector("bdi")?.textContent).toBe("data.jsonl");
  cleanup();

  seedDraft();
  render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale: "ar" }));
  const bannerAr = screen.getByTestId("upload-resume-banner");
  expect(bannerAr.textContent).toContain("رفع سابق لملف");
  expect(bannerAr.textContent).toContain("لا يزال قيد التنفيذ. اختر نفس الملف لاستئنافه.");
  expect(bannerAr.querySelector("bdi")?.textContent).toBe("data.jsonl");
});

test("FTF-UX02 r2 pre-review r2 finding 2: the abandon trigger's accessible action text is the exact bilingual sentence", () => {
  seedDraft();
  renderUI();
  expect(screen.getByTestId("upload-abandon-draft-button").textContent).toBe(
    "Abandon this draft and start a new upload",
  );
  cleanup();

  seedDraft();
  render(createElement(ResumableDatasetUploadUI, { ...PROPS, locale: "ar" }));
  expect(screen.getByTestId("upload-abandon-draft-button").textContent).toBe(
    "تجاهل هذه المسودة وابدأ رفعًا جديدًا",
  );
});

test("FTF-UX02 r2 pre-review r2 finding 6 (pinned): the destructive Abandon button is the UX01 Button with variant=danger, not primary", () => {
  seedDraft();
  renderUI();
  fireEvent.click(screen.getByTestId("upload-abandon-draft-button"));
  expect(screen.getByTestId("upload-abandon-confirm-button").getAttribute("data-variant")).toBe(
    "danger",
  );
});

test("fault: a secret, a presigned URL or file bytes ever written to localStorage -> RED", async () => {
  await uploadToReadyToFinalize();
  const dump: string[] = [];
  for (let i = 0; i < window.localStorage.length; i += 1) {
    const key = window.localStorage.key(i) ?? "";
    dump.push(key, window.localStorage.getItem(key) ?? "");
  }
  const everything = dump.join("\n");
  expect(everything).not.toContain("storage.example");
  expect(everything).not.toContain("part1");
  expect(everything).not.toContain("10,20,30");
  expect(everything).toContain(SESSION_ID);
  expect(everything).toContain(ORG_ID);
});

// ---------------------------------------------------------------------------
// Round 3 (Control-plane return of d3bf8ba, #727)
// ---------------------------------------------------------------------------

async function startAndWaitFor(testId: string) {
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(screen.getByTestId(testId)).toBeTruthy());
}

test("UF3-r3: listing [] then a 503 on part-urls shows 'after 0 of 2', never a listed-all count", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing([])));
  enqueue("POST", PART_URLS_URL, fail(503, "storage_unavailable"));
  await startAndWaitFor("upload-interrupted-text");
  expect(screen.getByTestId("upload-interrupted-text").textContent).toContain(
    "interrupted after 0 of 2 parts",
  );
  expect(calls("PUT", PART1_URL).length).toBe(0);
});

test("UF3-r3: listing [1] then a 500 on part-urls shows 'after 1 of 2'", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing([1])));
  enqueue("POST", PART_URLS_URL, fail(500, null));
  await startAndWaitFor("upload-interrupted-text");
  expect(screen.getByTestId("upload-interrupted-text").textContent).toContain(
    "interrupted after 1 of 2 parts",
  );
});

test("UF3-r3: a PUT still in flight shows 'Uploaded 0 of 2', and a 403 PUT is not counted", async () => {
  renderUI();
  let release: (value: Plain) => void = () => undefined;
  const held = new Promise<Plain>((resolve) => {
    release = resolve;
  });
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing([])));
  enqueue("POST", PART_URLS_URL, (init) => {
    const requested = (JSON.parse(String(init?.body)) as { part_numbers: number[] }).part_numbers;
    return ok(grants(requested));
  });
  enqueue("PUT", PART1_URL, held);
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(calls("PUT", PART1_URL).length).toBe(1));
  expect(screen.getByTestId("upload-progress-text").textContent).toContain("Uploaded 0 of 2 parts");
  const bar = screen.getByRole("progressbar");
  expect(bar.getAttribute("aria-valuenow")).toBe("0");
  expect(bar.getAttribute("aria-valuemax")).toBe("100");
  release({ status: 403, body: {} });
  await waitFor(() => expect(screen.getByTestId("upload-interrupted-text")).toBeTruthy());
  expect(screen.getByTestId("upload-interrupted-text").textContent).toContain(
    "interrupted after 0 of 2 parts",
  );
  expect(screen.queryByTestId("upload-all-parts-uploaded")).toBeNull();
});

test("UF3-r3: part 1 listed and part 2 PUT ok then part 3 absent: counts equal listed + 2xx PUTs", async () => {
  renderUI();
  enqueue("POST", SESSIONS_URL, ok(session(), 201));
  enqueue("GET", PARTS_URL, ok(listing([1])));
  enqueue("POST", PART_URLS_URL, (init) => {
    const requested = (JSON.parse(String(init?.body)) as { part_numbers: number[] }).part_numbers;
    return ok(grants(requested));
  });
  enqueue("PUT", PART2_URL, fail(403, null));
  await startAndWaitFor("upload-interrupted-text");
  // listed part 1 counts; the refused PUT of part 2 does not
  expect(screen.getByTestId("upload-interrupted-text").textContent).toContain(
    "interrupted after 1 of 2 parts",
  );
});

test("UNSAFE-ARCHIVE: a 422 unsafe_archive on finalize is terminal: rejected, draft cleared, no Finalize control", async () => {
  await uploadToReadyToFinalize();
  expect(window.localStorage.getItem(DRAFT_KEY)).not.toBeNull();
  await finalizeWith("my-dataset", fail(422, "unsafe_archive"));
  await waitFor(() => expect(screen.getByTestId("upload-rejected-text")).toBeTruthy());
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  expect(screen.queryByTestId("upload-finalize-button")).toBeNull();
  expect(screen.queryByTestId("upload-finalize")).toBeNull();
  expect(screen.getByTestId("upload-rejected-text").textContent).not.toContain("can be retried");
});

test("422 vocabulary cannot drift: every reason the server can raise on finalize is terminal on the client", () => {
  const upload = uploadSource;
  const routes = routesSource;
  const reasons = new Set<string>();
  // `return "code"` (sniff_content / inspect_xlsx verdicts) and `_reject(..., "code")`
  for (const match of upload.matchAll(/return "([a-z][a-z0-9_]*)"/g))
    reasons.add(match[1] as string);
  for (const match of upload.matchAll(/_reject\([^)]*?"([a-z][a-z0-9_]*)"\s*\)/g)) {
    reasons.add(match[1] as string);
  }
  // the route's own 422 details
  for (const match of routes.matchAll(/status_code=422, detail="([a-z][a-z0-9_]*)"/g)) {
    reasons.add(match[1] as string);
  }
  // the extractor must really have found the vocabulary (a silent empty set proves nothing)
  for (const known of [
    "unsafe_archive",
    "content_type_mismatch",
    "sha256_mismatch",
    "size_mismatch",
    "part_size_mismatch",
    "unexpected_part",
    "unsupported_content_type",
    "verification_mismatch",
    "source_revoked",
  ]) {
    expect(reasons.has(known), `extractor missed ${known}`).toBe(true);
  }
  for (const reason of reasons) {
    const failure = classifyFailure(422, reason);
    expect(failure.kind, `422 ${reason}`).toBe("closed");
    expect(failure.message, `422 ${reason}`).not.toContain("can be retried");
  }
});

test("B34: a create whose body resolves after an organization change saves no draft and lists no parts", async () => {
  const view = renderUI();
  let open: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  enqueue("POST", SESSIONS_URL, { status: 201, body: session(), jsonGate: gate });
  await selectFileAndStart(testFile(FILE_BYTES));
  await waitFor(() => expect(calls("POST", SESSIONS_URL).length).toBe(1));
  view.rerender(
    createElement(ResumableDatasetUploadUI, { ...PROPS, organizationId: OTHER_ORG_ID }),
  );
  await act(async () => {
    open();
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
  expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  expect(calls("GET", PARTS_URL).length).toBe(0);
  expect(window.localStorage.length).toBe(0);
  expect(unexpected).toEqual([]);
});
