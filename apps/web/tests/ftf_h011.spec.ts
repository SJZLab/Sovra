// @vitest-environment jsdom
//
// FTF-H011-P4 -- the dataset mapping wizard (apps/web/components/dataset-factory/
// dataset_mapping_wizard_ui.tsx) in jsdom with a route-table `fetch`. Covers the UI CHECKLIST:
// (a) a generated strict-parse table over EVERY field of every response the wizard reads,
// (b) one binding check with a fault per bound field, (c) identity reset, (d) no simulated
// success, (e) typed errors keep state and are retryable; plus the flow itself (format -> probe ->
// map -> preview -> propose -> approve / reject), masking, text-only rendering, Arabic, the
// Idempotency-Key reuse on a lost write and the read-only purpose. (f) is the real-API suite
// (apps/web/tests/real_api/ftf_h011_ui_real_api.realapi.ts).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  CSV_DELIMITERS,
  CSV_ENCODINGS,
  DatasetMappingWizard,
  ERROR_MESSAGES,
  bindsMapping,
  buildDocument,
  canonicalJson,
  emptyDraft,
  ignoredMessageKeys,
  parseMapping,
  parseMappingPage,
  parsePreview,
  parseProbe,
  type Draft,
  type MappingRow,
} from "../components/dataset-factory/dataset_mapping_wizard_ui";

const API = "http://api.h011.test:8011";
const ORG = "aaaaaaaa-0000-4000-8000-0000000000a1";
const PRJ = "bbbbbbbb-0000-4000-8000-0000000000b2";
const DS = "dddddddd-0000-4000-8000-0000000000d4";
const BASE = `${API}/api/v1/organizations/${ORG}/projects/${PRJ}/datasets/${DS}`;
const sha = (c: string) => c.repeat(64);

// ------------------------------------------------------------------ fixtures (contract-shaped)
function mappingBody(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    dataset_id: DS,
    dataset_name: "support-chats",
    dataset_purpose: "training",
    dataset_version: 1,
    dataset_sha256: sha("a"),
    mapping_version: 1,
    parent_mapping_version: null,
    mapping_sha256: sha("b"),
    importer: "jsonl",
    importer_version: "1",
    transform_version: "1",
    source_schema_sha256: null,
    tool_definitions_sha256: sha("c"),
    preview_sha256: sha("d"),
    preview_counts: { seen: 2, accepted: 2, rejected: 0 },
    status: "proposed",
    allowed_purposes_snapshot: ["training", "validation"],
    approved_purpose: null,
    approved_by: null,
    approver_role: null,
    approved_at: null,
    created_by: "user-1",
    created_at: "2026-11-27T10:00:00+00:00",
    updated_at: "2026-11-27T10:00:00+00:00",
    masked: false,
    mapping_document: DOC,
    decision: null,
    ...extra,
  };
}
const APPROVED = {
  status: "approved",
  approved_purpose: "training",
  approved_by: "user-admin",
  approver_role: "workspace_admin",
  approved_at: "2026-11-27T11:00:00+00:00",
  decision: { action: "approve", rationale: "looks right", decided_by: "user-admin" },
};
const DOC = {
  shape: "messages",
  messages_pointer: "/conversation",
  role_key: "role",
  content_key: "content",
  role_map: { human: "user", bot: "assistant" },
  on_unmapped_message_keys: "reject",
};
const RAW = (i: number) => ({
  conversation: [
    { role: "human", content: `مرحبا ${i}` },
    { role: "bot", content: `أهلا ${i}` },
  ],
});
const PROBE = {
  state: "completed",
  failure_code: null,
  complete: true,
  masked: false,
  headers: null,
  rows: [RAW(1), RAW(2)],
};
const CANDIDATE = (i: number) => ({
  messages: [
    { role: "user", content: `مرحبا ${i}` },
    { role: "assistant", content: `أهلا ${i}` },
  ],
});
const PREVIEW = {
  state: "completed",
  failure_code: null,
  complete: true,
  masked: false,
  counts: { seen: 2, accepted: 2, rejected: 0 },
  source_schema_sha256: null,
  tool_definitions_sha256: sha("c"),
  preview_sha256: sha("d"),
  observed_role_values: ["human", "bot"],
  rows: [1, 2].map((i) => ({
    locator: `line:${i}`,
    outcome: "accepted",
    code: null,
    slot: null,
    raw_row: RAW(i),
    not_mapped: [],
    candidate: CANDIDATE(i),
    comparison: [{ slot: "content", raw_type: "str", canonical_type: "str", identical: true }],
  })),
};
const PAGE = (items: unknown[] = []) => ({ items, next_after: null });

// ------------------------------------------------------------------ route-table fetch
interface Route {
  method: string;
  path: string; // matched against the end of the pathname (+ search)
  status?: number;
  body?: unknown;
  reject?: boolean;
  nonJson?: boolean;
  once?: boolean;
}
interface Sent {
  method: string;
  url: string;
  headers: Headers;
  body: unknown;
}
let routes: Route[] = [];
let sent: Sent[] = [];

function install() {
  sent = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const u = new URL(url);
      const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
      sent.push({ method, url, headers: new Headers(init.headers), body });
      const i = routes.findIndex(
        (r) => r.method === method && (u.pathname + u.search).endsWith(r.path),
      );
      if (i < 0) return { ok: false, status: 404, json: async () => ({ detail: "not_found" }) };
      const r = routes[i]!;
      if (r.once) routes.splice(i, 1);
      if (r.reject) throw new TypeError("Failed to fetch");
      const status = r.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => {
          if (r.nonJson) throw new SyntaxError("Unexpected token < in JSON");
          return JSON.parse(JSON.stringify(r.body));
        },
      };
    }),
  );
}

const LIST = (items: unknown[] = []): Route => ({
  method: "GET",
  path: "/mappings?limit=200",
  body: PAGE(items),
});

beforeEach(() => {
  routes = [];
  install();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount(locale: "en" | "ar" = "en", datasetId = DS) {
  return render(
    createElement(DatasetMappingWizard, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: PRJ,
      datasetId,
      locale,
    }),
  );
}

async function toProbe(format: RegExp = /JSON Lines/) {
  fireEvent.click(screen.getByRole("radio", { name: format }));
  fireEvent.click(screen.getByTestId("probe-button"));
}
function type(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
async function fillMessages() {
  await screen.findByTestId("mapping-step-map");
  fireEvent.click(screen.getByRole("radio", { name: /A list of messages/ }));
  type(/Messages location/, "/conversation");
  type(/^Role key/, "role");
  type(/^Content key/, "content");
  type(/Raw role value 1/, "human");
  fireEvent.change(screen.getByLabelText(/Maps to \(role 1\)/), { target: { value: "user" } });
  fireEvent.click(screen.getByTestId("add-role"));
  type(/Raw role value 2/, "bot");
  fireEvent.change(screen.getByLabelText(/Maps to \(role 2\)/), { target: { value: "assistant" } });
}
async function toPreview() {
  routes.push(LIST(), { method: "POST", path: "/mapping-probe", body: PROBE });
  mount();
  await screen.findByTestId("mapping-versions-empty");
  await toProbe();
  await fillMessages();
  routes.push({ method: "POST", path: "/mapping-preview", body: PREVIEW });
  fireEvent.click(screen.getByTestId("preview-button"));
  await screen.findByTestId("preview-result");
}
async function toApprove(
  proposeReply: Route = {
    method: "POST",
    path: "/mappings",
    status: 201,
    body: { ...mappingBody(), replayed: false, created: true },
  },
) {
  await toPreview();
  fireEvent.click(screen.getByTestId("to-propose"));
  routes.push(proposeReply);
  fireEvent.click(screen.getByTestId("save-mapping"));
}

// =============================================================================================
describe("[H011-P4] flow", () => {
  test("empty dataset: Draft state, empty state with the first action, nothing in storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    routes.push(LIST());
    mount();
    await screen.findByTestId("mapping-versions-empty");
    expect(screen.getByTestId("mapping-state").textContent).toContain("Draft");
    expect(screen.getByText("No mapping yet")).toBeTruthy();
    expect(sent.map((s) => s.url)).toEqual([`${BASE}/mappings?limit=200`]);
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  test("format -> probe -> map -> preview -> propose -> approve sends exactly the contract bodies", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    await toApprove();
    await screen.findByTestId("mapping-step-approve");
    // probe body: no options block for JSONL
    const probe = sent.find((s) => s.url.endsWith("/mapping-probe"))!;
    expect(probe.body).toEqual({});
    const preview = sent.find((s) => s.url.endsWith("/mapping-preview"))!;
    expect(preview.body).toEqual({ mapping: DOC });
    const propose = sent.find((s) => s.method === "POST" && s.url.endsWith("/mappings"))!;
    expect(propose.body).toEqual({ mapping: DOC });
    expect(propose.headers.get("Idempotency-Key")).toMatch(/^[A-Za-z0-9._:-]{8,200}$/);
    expect(screen.getByTestId("mapping-notice").textContent).toContain("version 1 saved");
    // purpose is READ-ONLY text from the dataset: there is no purpose input at all
    expect(screen.getByTestId("mapping-purpose").textContent).toContain("training");
    expect(screen.queryByLabelText(/purpose/i, { selector: "input,select,textarea" })).toBeNull();
    routes.push({
      method: "POST",
      path: "/approve",
      body: { ...mappingBody(APPROVED), replayed: false },
    });
    // the preview the user saw is the one approved
    fireEvent.change(screen.getByLabelText(/Reason for the decision/), {
      target: { value: "Matches the source" },
    });
    fireEvent.click(screen.getByTestId("approve-confirm"));
    fireEvent.click(screen.getByTestId("approve-button"));
    await waitFor(() =>
      expect(screen.getByTestId("mapping-state").textContent).toContain("Approved"),
    );
    const approve = sent.find((s) => s.url.endsWith("/approve"))!;
    expect(approve.url).toBe(`${BASE}/mappings/11111111-1111-4111-8111-111111111111/approve`);
    expect(approve.body).toEqual({
      expected_preview_sha256: sha("d"),
      purpose: "training",
      acknowledged_ignored_keys: [],
      rationale: "Matches the source",
    });
    expect(approve.headers.get("Idempotency-Key")).not.toBe(propose.headers.get("Idempotency-Key"));
    expect(screen.getByTestId("mapping-notice").textContent).toContain("approved for training");
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  test("approval needs a reason and the explicit confirmation; nothing is sent without them", async () => {
    await toApprove();
    await screen.findByTestId("mapping-step-approve");
    const before = sent.length;
    fireEvent.click(screen.getByTestId("approve-button"));
    const summary = await screen
      .findByRole("alert", { name: /problem|There is a problem|fix/i })
      .catch(() => null);
    expect(summary ?? screen.getAllByRole("alert").length).toBeTruthy();
    expect(screen.getByText("Enter the reason for the decision.", { selector: "a" })).toBeTruthy();
    expect(
      screen.getByText("Confirm that you reviewed the preview.", { selector: "a" }),
    ).toBeTruthy();
    expect(sent.length).toBe(before);
  });

  test("raw beside canonical, the comparison table and Arabic values are shown; data cells are dir=auto", async () => {
    await toPreview();
    const row = screen.getByTestId("preview-row-0");
    const cells = within(row).getAllByText("مرحبا 1", { selector: "span" });
    expect(cells.length).toBe(2); // raw and canonical
    for (const cell of cells) expect(cell.getAttribute("dir")).toBe("auto");
    expect(within(row).getAllByText("Raw").length).toBeGreaterThan(0);
    expect(within(row).getAllByText("Canonical").length).toBeGreaterThan(0);
    expect(within(row).getByText("Type comparison")).toBeTruthy();
    expect(screen.getByTestId("preview-counts").textContent).toContain("2");
  });

  test("rejected rows are shown with their machine-readable code, and the state is Validation required", async () => {
    routes.push(LIST(), { method: "POST", path: "/mapping-probe", body: PROBE });
    mount();
    await screen.findByTestId("mapping-versions-empty");
    await toProbe();
    await fillMessages();
    const rejected = {
      ...PREVIEW,
      counts: { seen: 2, accepted: 1, rejected: 1, "rejected:unknown_tool": 1 },
      rows: [
        PREVIEW.rows[0],
        {
          ...PREVIEW.rows[1],
          outcome: "rejected",
          code: "unknown_tool",
          slot: "tool_calls",
          candidate: null,
        },
      ],
    };
    routes.push({ method: "POST", path: "/mapping-preview", body: rejected });
    fireEvent.click(screen.getByTestId("preview-button"));
    await screen.findByTestId("preview-result");
    expect(screen.getByTestId("preview-row-1-code").textContent).toBe("unknown_tool");
    expect(screen.getByTestId("preview-row-1").textContent).toContain("does not define");
    expect(screen.getByTestId("mapping-state").textContent).toContain("Validation required");
    expect(screen.getByTestId("preview-rejections").textContent).toContain("1 rows are rejected");
  });

  test("an HTML payload in a cell renders as text, never as markup", async () => {
    const evil = '<img src=x onerror="window.__pwned=1"><b>bold</b>';
    routes.push(LIST(), {
      method: "POST",
      path: "/mapping-probe",
      body: { ...PROBE, rows: [{ conversation: [{ role: evil, content: evil }] }] },
    });
    mount();
    await screen.findByTestId("mapping-versions-empty");
    await toProbe();
    await screen.findByTestId("probe-result");
    const root = screen.getByTestId("dataset-mapping-wizard");
    expect(root.querySelector("img")).toBeNull();
    expect(root.querySelector("b")).toBeNull();
    expect(root.textContent).toContain(evil);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  test("masked values (ml_engineer on a confidential source) show Hidden / مخفي, never a value", async () => {
    const masked = { masked: true, chars: 7 };
    routes.push(LIST(), {
      method: "POST",
      path: "/mapping-probe",
      body: { ...PROBE, masked: true, rows: [{ [`k`]: masked }] },
    });
    mount("ar");
    await screen.findByTestId("mapping-versions-empty");
    fireEvent.click(screen.getByRole("radio", { name: /JSON Lines/ }));
    fireEvent.click(screen.getByTestId("probe-button"));
    await screen.findByTestId("masked-notice");
    const result = screen.getByTestId("probe-result");
    const hidden = result.querySelector('[data-masked="true"]');
    expect(hidden?.textContent).toBe("مخفي (7 حرفًا)");
    // the marker itself is never rendered as data
    expect(result.textContent).not.toContain("chars");
    expect(result.textContent).not.toContain("true");
  });

  test("a CSV probe sends the options block (windows-1256, ';', 2 rows before the header)", async () => {
    routes.push(LIST(), {
      method: "POST",
      path: "/mapping-probe",
      body: { ...PROBE, headers: ["السؤال", "الجواب"], rows: [["س", "ج"]] },
    });
    mount();
    await screen.findByTestId("mapping-versions-empty");
    fireEvent.click(screen.getByRole("radio", { name: /CSV/ }));
    fireEvent.change(screen.getByLabelText(/Text encoding/), { target: { value: "windows-1256" } });
    fireEvent.change(screen.getByLabelText(/Delimiter/), { target: { value: ";" } });
    type(/Rows before the header/, "2");
    fireEvent.click(screen.getByTestId("probe-button"));
    await screen.findByTestId("mapping-step-map");
    expect(sent.find((s) => s.url.endsWith("/mapping-probe"))!.body).toEqual({
      csv: { encoding: "windows-1256", delimiter: ";", has_header: true, header_row: 2 },
    });
    // a tabular source cannot use a JSON pointer: only the column shapes are offered
    expect(screen.queryByRole("radio", { name: /A list of messages in each record/ })).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /A prompt and a completion/ }));
    fireEvent.change(screen.getByLabelText(/^Prompt/), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText(/^Completion/), { target: { value: "1" } });
    routes.push({ method: "POST", path: "/mapping-preview", body: PREVIEW });
    fireEvent.click(screen.getByTestId("preview-button"));
    await screen.findByTestId("preview-result");
    expect(sent.find((s) => s.url.endsWith("/mapping-preview"))!.body).toEqual({
      mapping: {
        shape: "prompt_completion",
        prompt: { header: "السؤال" },
        completion: { header: "الجواب" },
        csv: { encoding: "windows-1256", delimiter: ";", has_header: true, header_row: 2 },
      },
    });
  });

  test("invalid header row is prevented client-side with an inline error and nothing is sent", async () => {
    routes.push(LIST());
    mount();
    await screen.findByTestId("mapping-versions-empty");
    fireEvent.click(screen.getByRole("radio", { name: /CSV/ }));
    type(/Rows before the header/, "-1");
    const before = sent.length;
    fireEvent.click(screen.getByTestId("probe-button"));
    expect(screen.getAllByText("Enter a whole number from 0 to 1000000.").length).toBeGreaterThan(
      0,
    );
    expect(sent.length).toBe(before);
  });

  test("XLSX: the server's importer_unavailable is shown as Blocked with its reason (no fallback)", async () => {
    routes.push(LIST(), {
      method: "POST",
      path: "/mapping-probe",
      status: 409,
      body: { detail: "importer_unavailable" },
    });
    mount();
    await screen.findByTestId("mapping-versions-empty");
    await toProbe(/Excel workbook/);
    await screen.findByTestId("probe-error");
    expect(screen.getByTestId("mapping-state").textContent).toContain(
      "Blocked — This file type cannot be mapped yet.",
    );
    expect(screen.queryByTestId("mapping-step-map")).toBeNull();
  });

  test("no assistant in role_map: warned, still sent, and the server's 400 is shown (R3)", async () => {
    routes.push(LIST(), { method: "POST", path: "/mapping-probe", body: PROBE });
    mount();
    await screen.findByTestId("mapping-versions-empty");
    await toProbe();
    await screen.findByTestId("mapping-step-map");
    fireEvent.click(screen.getByRole("radio", { name: /A list of messages/ }));
    type(/Messages location/, "/conversation");
    type(/^Role key/, "role");
    type(/^Content key/, "content");
    type(/Raw role value 1/, "human");
    fireEvent.change(screen.getByLabelText(/Maps to \(role 1\)/), { target: { value: "user" } });
    expect(screen.getByTestId("no-assistant-warning")).toBeTruthy();
    routes.push({
      method: "POST",
      path: "/mapping-preview",
      status: 400,
      body: { detail: "critical_role_unmapped" },
    });
    fireEvent.click(screen.getByTestId("preview-button"));
    await screen.findByTestId("preview-error");
    expect(screen.getByTestId("preview-alert").textContent).toContain("assistant");
    expect(screen.getByTestId("mapping-state").textContent).toContain("Validation required");
  });

  test("approve 409 critical_rows_unmapped and 403 purpose_not_authorized are shown, not success (R4/R5)", async () => {
    for (const [status, detail, word] of [
      [409, "critical_rows_unmapped", "Validation required"],
      [403, "purpose_not_authorized", "Blocked"],
    ] as const) {
      cleanup();
      routes = [];
      install();
      await toApprove();
      await screen.findByTestId("mapping-step-approve");
      routes.push({ method: "POST", path: "/approve", status, body: { detail } });
      fireEvent.change(screen.getByLabelText(/Reason for the decision/), {
        target: { value: "ok" },
      });
      fireEvent.click(screen.getByTestId("approve-confirm"));
      fireEvent.click(screen.getByTestId("approve-button"));
      await screen.findByTestId("decision-error");
      expect(screen.getByTestId("decision-error").textContent).toBe(ERROR_MESSAGES[detail]!.en);
      expect(screen.getByTestId("mapping-state").textContent).toContain(word);
      expect(screen.getByTestId("mapping-state").textContent).not.toContain("Approved");
    }
  });

  test("ignored message keys must each be acknowledged and are sent sorted", async () => {
    const doc = { ...DOC, on_unmapped_message_keys: "ignore" };
    const raw = {
      conversation: [
        { role: "human", content: "a", zeta: 1, meta: 2 },
        { role: "bot", content: "b" },
      ],
    };
    const preview = { ...PREVIEW, rows: [{ ...PREVIEW.rows[0], raw_row: raw }] };
    routes.push(LIST([mappingBody({ mapping_document: doc })]), {
      method: "POST",
      path: "/mapping-preview",
      body: preview,
    });
    mount();
    fireEvent.click(await screen.findByTestId("review-version-1"));
    fireEvent.click(screen.getByTestId("preview-version"));
    await screen.findByTestId("ignored-keys");
    fireEvent.change(screen.getByLabelText(/Reason for the decision/), { target: { value: "ok" } });
    fireEvent.click(screen.getByTestId("approve-confirm"));
    fireEvent.click(screen.getByTestId("approve-button"));
    expect(screen.getByText("Confirm every ignored key.", { selector: "a" })).toBeTruthy();
    const boxes = within(screen.getByTestId("ignored-keys")).getAllByRole("checkbox");
    expect(boxes.length).toBe(2);
    boxes.forEach((b) => fireEvent.click(b));
    routes.push({
      method: "POST",
      path: "/approve",
      body: { ...mappingBody({ ...APPROVED, mapping_document: doc }), replayed: false },
    });
    fireEvent.click(screen.getByTestId("approve-button"));
    await waitFor(() => expect(sent.some((s) => s.url.endsWith("/approve"))).toBe(true));
    expect(
      (
        sent.find((s) => s.url.endsWith("/approve"))!.body as {
          acknowledged_ignored_keys: string[];
        }
      ).acknowledged_ignored_keys,
    ).toEqual(["meta", "zeta"]);
  });

  test("resume after reload: a saved proposal is listed and must be previewed again before approval", async () => {
    routes.push(LIST([mappingBody()]));
    mount();
    fireEvent.click(await screen.findByTestId("review-version-1"));
    expect(screen.getByTestId("approve-button").hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("decision-needs-preview")).toBeTruthy();
    routes.push({
      method: "POST",
      path: "/mapping-preview",
      body: { ...PREVIEW, preview_sha256: sha("e") },
    });
    fireEvent.click(screen.getByTestId("preview-version"));
    // a preview whose digest differs from the stored one never enables approval
    await screen.findByText(/stored preview differs/);
    expect(screen.getByTestId("approve-button").hasAttribute("disabled")).toBe(true);
  });

  test("reject asks for confirmation in a dialog and records Rejected only on a bound 2xx", async () => {
    await toApprove();
    await screen.findByTestId("mapping-step-approve");
    fireEvent.change(screen.getByLabelText(/Reason for the decision/), {
      target: { value: "wrong roles" },
    });
    fireEvent.click(screen.getByTestId("reject-button"));
    const dialog = await screen.findByTestId("reject-dialog");
    expect(dialog.textContent).toContain("cannot be approved later");
    routes.push({
      method: "POST",
      path: "/reject",
      body: {
        ...mappingBody({
          status: "rejected",
          decision: { action: "reject", rationale: "wrong roles", decided_by: "u" },
        }),
        replayed: false,
      },
    });
    fireEvent.click(screen.getByTestId("confirm-reject"));
    await waitFor(() =>
      expect(screen.getByTestId("mapping-state").textContent).toContain("Rejected"),
    );
    expect(sent.find((s) => s.url.endsWith("/reject"))!.body).toEqual({ rationale: "wrong roles" });
  });

  test("Arabic: root is lang=ar dir=rtl and the standard phrases are Arabic", async () => {
    routes.push(LIST());
    mount("ar");
    await screen.findByTestId("mapping-versions-empty");
    const root = screen.getByTestId("dataset-mapping-wizard");
    expect(root.getAttribute("lang")).toBe("ar");
    expect(root.getAttribute("dir")).toBe("rtl");
    expect(root.textContent).toContain("مسودة");
    expect(root.textContent).toContain("قراءة الملف");
    expect(root.textContent).toContain("لا يوجد تعيين بعد");
  });
});

// =============================================================================================
describe("[H011-P4] (e) typed errors keep state and are retryable; (d) no simulated success", () => {
  for (const [label, route] of [
    ["503 storage_unavailable", { status: 503, body: { detail: "storage_unavailable" } }],
    ["500 no body", { status: 500, body: {} }],
    ["non-JSON 502", { status: 502, nonJson: true }],
    ["an unknown 409 code", { status: 409, body: { detail: "a_code_from_the_future" } }],
    ["network failure", { reject: true }],
    ["2xx non-JSON", { status: 200, nonJson: true }],
    ["2xx malformed", { status: 200, body: { state: "done" } }],
  ] as const) {
    test(`probe ${label}: an error, never success, and the options are kept`, async () => {
      routes.push(LIST(), { method: "POST", path: "/mapping-probe", ...route } as Route);
      mount();
      await screen.findByTestId("mapping-versions-empty");
      fireEvent.click(screen.getByRole("radio", { name: /CSV/ }));
      fireEvent.change(screen.getByLabelText(/Delimiter/), { target: { value: ";" } });
      fireEvent.click(screen.getByTestId("probe-button"));
      await screen.findByTestId("probe-error");
      expect(screen.queryByTestId("probe-result")).toBeNull();
      expect(screen.queryByTestId("mapping-step-map")).toBeNull();
      expect((screen.getByLabelText(/Delimiter/) as HTMLSelectElement).value).toBe(";");
      const recovery = screen.getByTestId("probe-alert-recovery").textContent ?? "";
      if (label.includes("unknown"))
        expect(screen.getByTestId("probe-alert").textContent).toContain("a_code_from_the_future");
      expect(recovery.length).toBeGreaterThan(0);
      // retry works with the same options
      routes = [
        {
          method: "POST",
          path: "/mapping-probe",
          body: { ...PROBE, headers: ["a"], rows: [["x"]] },
        },
      ];
      fireEvent.click(screen.getByTestId("probe-button"));
      await screen.findByTestId("mapping-step-map");
    });
  }

  test("a lost propose response is retried with the SAME Idempotency-Key (server replays)", async () => {
    await toPreview();
    fireEvent.click(screen.getByTestId("to-propose"));
    routes.push({ method: "POST", path: "/mappings", reject: true, once: true });
    fireEvent.click(screen.getByTestId("save-mapping"));
    await screen.findByTestId("save-error");
    expect(screen.queryByTestId("mapping-step-approve")).toBeNull();
    routes.push({
      method: "POST",
      path: "/mappings",
      body: { ...mappingBody(), replayed: true, created: false },
    });
    fireEvent.click(screen.getByTestId("save-mapping"));
    await screen.findByTestId("mapping-step-approve");
    const keys = sent
      .filter((s) => s.method === "POST" && s.url.endsWith("/mappings"))
      .map((s) => s.headers.get("Idempotency-Key"));
    expect(keys.length).toBe(2);
    expect(keys[0]).toBe(keys[1]);
  });

  test("a 2xx propose whose document differs from the one sent is never shown as saved", async () => {
    await toApprove({
      method: "POST",
      path: "/mappings",
      status: 201,
      body: {
        ...mappingBody({ mapping_document: { ...DOC, role_map: { human: "assistant" } } }),
        created: true,
        replayed: false,
      },
    });
    await screen.findByTestId("save-error");
    expect(screen.getByTestId("save-error").textContent).toContain("does not match");
    expect(screen.queryByTestId("mapping-step-approve")).toBeNull();
  });
});

// =============================================================================================
describe("[H011-P4] (c) identity", () => {
  test("a new dataset id remounts: the draft and the probe are gone, the new list is requested", async () => {
    routes.push(LIST(), { method: "POST", path: "/mapping-probe", body: PROBE });
    const view = mount();
    await screen.findByTestId("mapping-versions-empty");
    await toProbe();
    await screen.findByTestId("mapping-step-map");
    const other = "eeeeeeee-0000-4000-8000-0000000000e5";
    routes.push({ method: "GET", path: `/datasets/${other}/mappings?limit=200`, body: PAGE() });
    view.rerender(
      createElement(DatasetMappingWizard, {
        apiBaseUrl: API,
        organizationId: ORG,
        projectId: PRJ,
        datasetId: other,
      }),
    );
    await screen.findByTestId("mapping-step-format");
    expect(screen.queryByTestId("mapping-step-map")).toBeNull();
    expect((screen.getByRole("radio", { name: /JSON Lines/ }) as HTMLInputElement).checked).toBe(
      false,
    );
    expect(sent.at(-1)!.url).toBe(
      `${API}/api/v1/organizations/${ORG}/projects/${PRJ}/datasets/${other}/mappings?limit=200`,
    );
  });

  test("a version list naming another dataset is malformed, never shown", async () => {
    routes.push(LIST([mappingBody({ dataset_id: "ffffffff-0000-4000-8000-0000000000f6" })]));
    mount();
    await screen.findByTestId("mapping-versions-error");
    expect(screen.queryByTestId("review-version-1")).toBeNull();
  });
});

// =============================================================================================
// (a) generated strict-parse table over EVERY response field.
type Mutation = { name: string; apply: (o: Record<string, unknown>) => void };
function mutationsFor(
  sample: Record<string, unknown>,
  enums: Record<string, string[]>,
  nullable: string[],
): Mutation[] {
  const out: Mutation[] = [];
  for (const [key, value] of Object.entries(sample)) {
    out.push({ name: `${key} missing`, apply: (o) => void delete o[key] });
    const wrong =
      typeof value === "string"
        ? 7
        : typeof value === "number"
          ? "7"
          : typeof value === "boolean"
            ? "true"
            : Array.isArray(value)
              ? "x"
              : value === null
                ? nullable.includes(key)
                  ? undefined
                  : 7
                : "x";
    if (wrong !== undefined)
      out.push({ name: `${key} wrong type`, apply: (o) => void (o[key] = wrong) });
    if (!nullable.includes(key) && value !== null)
      out.push({ name: `${key} null`, apply: (o) => void (o[key] = null) });
    if (enums[key])
      out.push({ name: `${key} outside the enum`, apply: (o) => void (o[key] = "not_a_value") });
  }
  return out;
}
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

describe("[H011-P4] (a) strict parse: every field of every response", () => {
  const probeMut = mutationsFor(PROBE, { state: ["completed"] }, ["failure_code", "headers"]);
  test.each(probeMut.map((m) => [m.name, m]))("probe: %s -> malformed", (_n, m) => {
    const body = clone(PROBE) as Record<string, unknown>;
    (m as Mutation).apply(body);
    expect(parseProbe(body)).toBeNull();
  });
  test("probe: a header that is neither a string nor a masked marker is malformed", () => {
    expect(parseProbe({ ...PROBE, headers: [{ masked: true, chars: 1, extra: 1 }] })).toBeNull();
    expect(parseProbe({ ...PROBE, rows: ["a string row"] })).toBeNull();
    expect(parseProbe(PROBE)).not.toBeNull();
  });

  const previewMut = mutationsFor(PREVIEW, { state: ["completed"] }, [
    "failure_code",
    "source_schema_sha256",
  ]);
  test.each(previewMut.map((m) => [m.name, m]))("preview: %s -> malformed", (_n, m) => {
    const body = clone(PREVIEW) as Record<string, unknown>;
    (m as Mutation).apply(body);
    expect(parsePreview(body)).toBeNull();
  });
  const rowMut = mutationsFor(
    PREVIEW.rows[0] as Record<string, unknown>,
    { outcome: ["accepted"] },
    ["code", "slot", "raw_row"],
  );
  test.each(rowMut.map((m) => [m.name, m]))("preview row: %s -> malformed", (_n, m) => {
    const body = clone(PREVIEW) as { rows: Record<string, unknown>[] };
    (m as Mutation).apply(body.rows[0]!);
    // an accepted row with a null candidate, or a row with no outcome, is malformed
    expect(parsePreview(body)).toBeNull();
  });
  const cmpMut = mutationsFor(PREVIEW.rows[0]!.comparison[0] as Record<string, unknown>, {}, []);
  test.each(cmpMut.map((m) => [m.name, m]))("preview comparison: %s -> malformed", (_n, m) => {
    const body = clone(PREVIEW) as { rows: { comparison: Record<string, unknown>[] }[] };
    (m as Mutation).apply(body.rows[0]!.comparison[0]!);
    expect(parsePreview(body)).toBeNull();
  });
  test("preview: a hash that is not 64 lowercase hex, a negative count, a rejected row without a code", () => {
    expect(parsePreview({ ...PREVIEW, preview_sha256: "D".repeat(64) })).toBeNull();
    expect(parsePreview({ ...PREVIEW, counts: { seen: -1 } })).toBeNull();
    expect(
      parsePreview({ ...PREVIEW, rows: [{ ...PREVIEW.rows[0], outcome: "rejected", code: null }] }),
    ).toBeNull();
    expect(parsePreview(PREVIEW)).not.toBeNull();
  });

  const mappingSample = mappingBody();
  const mappingMut = mutationsFor(mappingSample, { status: ["proposed"] }, [
    "parent_mapping_version",
    "source_schema_sha256",
    "approved_purpose",
    "approved_by",
    "approver_role",
    "approved_at",
    "mapping_document",
    "decision",
  ]).filter((m) => !m.name.startsWith("replayed") && !m.name.startsWith("created"));
  test.each(mappingMut.map((m) => [m.name, m]))("mapping: %s -> malformed", (_n, m) => {
    const body = clone(mappingSample);
    (m as Mutation).apply(body);
    expect(parseMapping(body)).toBeNull();
  });
  test("mapping: approval columns must be all set exactly when approved, with the dataset's purpose", () => {
    expect(parseMapping(mappingBody({ approved_purpose: "training" }))).toBeNull();
    expect(parseMapping(mappingBody({ ...APPROVED, approved_at: null }))).toBeNull();
    expect(parseMapping(mappingBody({ ...APPROVED, approved_purpose: "validation" }))).toBeNull();
    expect(parseMapping(mappingBody({ masked: true }))).toBeNull(); // masked yet a document
    expect(
      parseMapping(mappingBody({ decision: { action: "approve", rationale: null } })),
    ).toBeNull();
    expect(
      parseMapping(
        mappingBody({ decision: { action: "merge", rationale: null, decided_by: "u" } }),
      ),
    ).toBeNull();
    expect(parseMapping(mappingBody(APPROVED))).not.toBeNull();
    expect(parseMapping(mappingBody({ masked: true, mapping_document: null }))).not.toBeNull();
  });
  test("page: items and next_after are strict", () => {
    expect(parseMappingPage({ items: [] })).toBeNull();
    expect(parseMappingPage({ items: "x", next_after: null })).toBeNull();
    expect(parseMappingPage({ items: [], next_after: 0 })).toBeNull();
    expect(parseMappingPage({ items: [{ id: 1 }], next_after: null })).toBeNull();
    expect(parseMappingPage(PAGE([mappingBody()]))).not.toBeNull();
  });

  // Component level: each malformed propose reply shows the malformed-write message and NO
  // approval control, for every field.
  test.each(mappingMut.slice(0, 200).map((m) => [m.name, m]))(
    "component: a propose reply with %s shows malformed and no approve control",
    async (_n, m) => {
      const body = { ...clone(mappingSample), created: true, replayed: false };
      (m as Mutation).apply(body);
      await toApprove({ method: "POST", path: "/mappings", status: 201, body });
      await screen.findByTestId("save-error");
      expect(screen.queryByTestId("approve-button")).toBeNull();
      expect(screen.getByTestId("mapping-state").textContent).not.toContain("Approved");
    },
  );
  test.each(previewMut.map((m) => [m.name, m]))(
    "component: a preview reply with %s shows malformed and no way to save",
    async (_n, m) => {
      const body = clone(PREVIEW) as Record<string, unknown>;
      (m as Mutation).apply(body);
      routes.push(LIST(), { method: "POST", path: "/mapping-probe", body: PROBE });
      mount();
      await screen.findByTestId("mapping-versions-empty");
      await toProbe();
      await fillMessages();
      routes.push({ method: "POST", path: "/mapping-preview", body });
      fireEvent.click(screen.getByTestId("preview-button"));
      await screen.findByTestId("preview-error");
      expect(screen.queryByTestId("preview-result")).toBeNull();
      expect(screen.getByTestId("to-propose").hasAttribute("disabled")).toBe(true);
    },
  );
});

// =============================================================================================
describe("[H011-P4] (b) one binding check, a fault per bound field", () => {
  const row = parseMapping(mappingBody())!;
  test("binds when everything matches", () => {
    expect(bindsMapping(row, { datasetId: DS, document: DOC })).toBe(true);
    expect(
      bindsMapping(parseMapping(mappingBody(APPROVED))!, {
        datasetId: DS,
        mappingId: row.id,
        status: "approved",
        purpose: "training",
      }),
    ).toBe(true);
  });
  const faults: [string, Partial<MappingRow>, Parameters<typeof bindsMapping>[1]][] = [
    ["dataset id", { datasetId: "other" }, { datasetId: DS }],
    ["mapping id", { id: "other" }, { datasetId: DS, mappingId: row.id }],
    ["status", { status: "rejected" }, { datasetId: DS, status: "approved" }],
    ["approved purpose", { approvedPurpose: "validation" }, { datasetId: DS, purpose: "training" }],
    ...Object.keys(DOC).map(
      (k) =>
        [
          `document.${k}`,
          { mappingDocument: { ...DOC, [k]: k === "role_map" ? { human: "user" } : "changed" } },
          { datasetId: DS, document: DOC },
        ] as [string, Partial<MappingRow>, Parameters<typeof bindsMapping>[1]],
    ),
    [
      "document extra key",
      { mappingDocument: { ...DOC, name_key: "name" } },
      { datasetId: DS, document: DOC },
    ],
  ];
  test.each(faults)("a different %s does not bind", (_n, patch, expectation) => {
    expect(bindsMapping({ ...row, ...patch } as MappingRow, expectation)).toBe(false);
  });
});

// =============================================================================================
describe("[H011-P4] pure helpers mirror the backend", () => {
  test("the CSV vocabularies are the importer's", () => {
    expect([...CSV_ENCODINGS]).toEqual([
      "utf-8",
      "utf-8-sig",
      "utf-16-le",
      "utf-16-be",
      "windows-1256",
    ]);
    expect([...CSV_DELIMITERS]).toEqual([",", ";", "\t", "|"]);
  });
  test("buildDocument never defaults an optional field and keeps the user's exact values", () => {
    const d: Draft = {
      ...emptyDraft(),
      format: "jsonl",
      shape: "messages",
      messagesPointer: "/m",
      roleKey: "r",
      contentKey: "c",
      roles: [{ raw: "Human ", target: "user" }],
    };
    expect(buildDocument(d, null)).toEqual({
      shape: "messages",
      messages_pointer: "/m",
      role_key: "r",
      content_key: "c",
      role_map: { "Human ": "user" },
      on_unmapped_message_keys: "reject",
    });
  });
  test("a masked header is never guessed: the slot becomes the column index", () => {
    const d: Draft = {
      ...emptyDraft(),
      format: "csv",
      shape: "prompt_completion",
      prompt: "0",
      completion: "1",
    };
    const doc = buildDocument(d, [{ masked: true, chars: 3 } as never, "b"]);
    expect(doc!.prompt).toEqual({ index: 0 });
    expect(doc!.completion).toEqual({ header: "b" });
  });
  test("ignoredMessageKeys is the store's rule (only for ignore, outside the mapped keys)", () => {
    const preview = parsePreview({
      ...PREVIEW,
      rows: [
        {
          ...PREVIEW.rows[0],
          raw_row: { conversation: [{ role: "h", content: "c", x: 1, name: 2 }] },
        },
      ],
    })!;
    expect(
      ignoredMessageKeys(
        { ...DOC, on_unmapped_message_keys: "ignore", name_key: "name" },
        preview,
        null,
      ),
    ).toEqual(["x"]);
    expect(ignoredMessageKeys(DOC, preview, null)).toEqual([]);
  });
  test("canonicalJson is key-order independent", () => {
    expect(canonicalJson({ a: 1, b: [1, { d: 1, c: 2 }] })).toBe(
      canonicalJson({ b: [1, { c: 2, d: 1 }], a: 1 }),
    );
  });
});
