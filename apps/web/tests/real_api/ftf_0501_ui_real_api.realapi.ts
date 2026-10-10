// @vitest-environment jsdom
//
// FTF-0501-UI round 2 -- the real resumable-upload component against the REAL
// FTF-0501 router (build_direct_upload_router) on PostgreSQL 16 as sovra_app and
// a real S3 API (SeaweedFS in CI). Nothing about the server is mocked: the only
// test double is the transport wrapper below, which (1) adds the test principal
// header the router's principal_resolver reads, in place of the OIDC session
// cookie the product will use, (2) hands jsdom's Blob bytes to Node's fetch,
// and (3) can drop one part PUT to model a connection loss.
//
// Run by tests/integration/test_ftf_0501_ui_real_api.py (which seeds the
// organization / project / connection, passes them in the environment and then
// verifies the database); not part of the default vitest include.
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  draftStorageKey,
  ResumableDatasetUploadUI,
} from "../../components/dataset-factory/resumable_dataset_upload_ui";

// No @types/node in this workspace (a new dependency is an owner ask): read the
// environment the Python harness passes in through the runtime global.
const ENV = (globalThis as unknown as { process: { env: Record<string, string | undefined> } })
  .process.env;

function required(name: string): string {
  const value = ENV[name];
  if (!value)
    throw new Error(`${name} is required (run via tests/integration/test_ftf_0501_ui_real_api.py)`);
  return value;
}

const API_BASE = required("FTF_0501_UI_API_BASE");
const ORG_ID = required("FTF_0501_UI_ORG");
const OTHER_ORG_ID = required("FTF_0501_UI_OTHER_ORG");
const PROJECT_ID = required("FTF_0501_UI_PROJECT");
const CONNECTION_ID = required("FTF_0501_UI_CONNECTION");
const PRINCIPAL = required("FTF_0501_UI_PRINCIPAL");
const FILE_BYTES = Number(required("FTF_0501_UI_FILE_BYTES"));

/** Deterministic JSONL (every line starts with `{`, 1024 bytes); the harness
 * regenerates the same bytes to check what the server stored. */
export function jsonlBytes(seed: number, size: number): Uint8Array {
  const line = new TextEncoder().encode(`{"k":"${String.fromCharCode(97 + seed).repeat(1015)}"}\n`);
  const out = new Uint8Array(size);
  for (let offset = 0; offset < size; offset += line.length) out.set(line, offset);
  return out;
}

interface Call {
  method: string;
  url: string;
  status: number | "dropped";
  partNumber: string | null;
  bytes?: number;
  body?: unknown;
}
let records: Call[] = [];
let dropPartNumber: string | null = null;
const realFetch = globalThis.fetch;

function readBlob(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("file read failed"));
    reader.readAsArrayBuffer(blob);
  });
}

beforeEach(() => {
  records = [];
  dropPartNumber = null;
  window.localStorage.clear();
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const isApi = url.startsWith(API_BASE);
    const partNumber = new URL(url).searchParams.get("partNumber");
    const headers = new Headers(init.headers);
    if (isApi) headers.set("X-Test-Principal", PRINCIPAL);
    let body: BodyInit | undefined = init.body ?? undefined;
    let bytes: number | undefined;
    let parsedBody: unknown;
    if (typeof body === "string") parsedBody = JSON.parse(body);
    if (body instanceof Blob) {
      const buffer = new Uint8Array(await readBlob(body));
      bytes = buffer.length;
      body = buffer;
    }
    if (!isApi && method === "PUT" && partNumber !== null && partNumber === dropPartNumber) {
      dropPartNumber = null;
      records.push({ method, url, status: "dropped", partNumber, bytes });
      throw new TypeError("fetch failed: injected connection drop");
    }
    const response = await realFetch(url, { method, headers, body });
    records.push({ method, url, status: response.status, partNumber, bytes, body: parsedBody });
    return response;
  });
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

function pickAndStart(file: File) {
  fireEvent.change(screen.getByTestId("upload-content-type-select"), {
    target: { value: "application/jsonl" },
  });
  fireEvent.change(screen.getByTestId("upload-file-input"), { target: { files: [file] } });
  fireEvent.click(screen.getByTestId("upload-start-button"));
}

function jsonlFile(seed: number): File {
  return new File([jsonlBytes(seed, FILE_BYTES) as BlobPart], `seed-${seed}.jsonl`, {
    type: "application/jsonl",
  });
}

function partPuts(): Call[] {
  return records.filter((r) => r.method === "PUT" && r.status !== "dropped");
}

function apiCalls(method: string, suffix: string): Call[] {
  return records.filter(
    (r) => r.url.startsWith(API_BASE) && r.method === method && r.url.endsWith(suffix),
  );
}

async function finalizeAs(name: string) {
  fireEvent.change(screen.getByTestId("upload-dataset-name-input"), { target: { value: name } });
  fireEvent.click(screen.getByTestId("upload-finalize-button"));
  await waitFor(() => expect(screen.getByTestId("upload-accepted")).toBeTruthy(), {
    timeout: 60_000,
  });
  expect(screen.getByTestId("upload-accepted-text").textContent).toBe("Dataset created.");
}

test("R1 real router + S3: multi-part upload, finalize, bound Dataset", async () => {
  renderUI();
  pickAndStart(jsonlFile(0));
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy(), {
    timeout: 120_000,
  });
  const partCount = partPuts().length;
  expect(partCount).toBeGreaterThanOrEqual(3);
  expect(new Set(partPuts().map((r) => r.partNumber)).size).toBe(partCount);
  expect(partPuts().every((r) => r.status === 200)).toBe(true);
  await finalizeAs("ui-real-r1");
  expect(
    window.localStorage.getItem(draftStorageKey(ORG_ID, PROJECT_ID, CONNECTION_ID)),
  ).toBeNull();
});

test("R2 real router + S3: interrupt, reload, resume uploads ONLY the missing parts, accepted Dataset", async () => {
  const first = renderUI();
  dropPartNumber = "2";
  pickAndStart(jsonlFile(1));
  await waitFor(() => expect(screen.getByTestId("upload-interrupted")).toBeTruthy(), {
    timeout: 120_000,
  });
  // part 1 reached storage, part 2 was dropped on the wire, part 3 was never attempted
  expect(partPuts().map((r) => r.partNumber)).toEqual(["1"]);
  expect(records.some((r) => r.status === "dropped" && r.partNumber === "2")).toBe(true);

  // reload: a fresh component instance, same localStorage
  first.unmount();
  records.length = 0;
  renderUI();
  expect(screen.getByTestId("upload-resume-banner")).toBeTruthy();
  fireEvent.change(screen.getByTestId("upload-resume-file-input"), {
    target: { files: [jsonlFile(1)] },
  });
  await waitFor(() => expect(screen.getByTestId("upload-all-parts-uploaded")).toBeTruthy(), {
    timeout: 120_000,
  });
  const grantBodies = apiCalls("POST", "/part-urls").map(
    (r) => r.body as { part_numbers: number[] },
  );
  expect(grantBodies.length).toBe(1);
  expect(grantBodies[0]?.part_numbers[0]).toBe(2);
  expect(grantBodies[0]?.part_numbers).not.toContain(1);
  expect(partPuts().map((r) => r.partNumber)).not.toContain("1");
  expect(partPuts().every((r) => r.status === 200)).toBe(true);
  await finalizeAs("ui-real-r2");
});

test("R3 real router: another organization's path is refused by the server; nothing is created", async () => {
  renderUI({ organizationId: OTHER_ORG_ID });
  pickAndStart(jsonlFile(2));
  await waitFor(() => expect(screen.getByTestId("upload-error-text")).toBeTruthy(), {
    timeout: 60_000,
  });
  expect(screen.queryByText("Dataset created.")).toBeNull();
  expect(partPuts().length).toBe(0);
  expect(apiCalls("POST", "/upload-sessions").every((r) => r.status !== 201)).toBe(true);
  expect(
    window.localStorage.getItem(draftStorageKey(OTHER_ORG_ID, PROJECT_ID, CONNECTION_ID)),
  ).toBeNull();
});
