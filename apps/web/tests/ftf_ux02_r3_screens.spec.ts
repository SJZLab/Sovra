// @vitest-environment jsdom
//
// FTF-UX02 round 3, finding 4 (QUEUE 0k): FTF-0516 and FTF-0501-UI are rebuilt on UX01 with an en/ar
// catalogue, a `locale` prop, `lang`/`dir`, recovery alerts and focus that never falls to <body>.
// This file pins that in jsdom; the real-layout half (overflow, targets, axe, off-screen) is
// ftf_ux02_r3_browser.spec.ts. Every test was run RED against the round-2 component.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { DatasetIssueResolutionUI } from "../components/dataset-factory/dataset_issue_resolution_ui";
import {
  ResumableDatasetUploadUI,
  classifyFailure,
} from "../components/dataset-factory/resumable_dataset_upload_ui";
import {
  KEYS_0516,
  SERVER_DETAIL_CODES,
  STRINGS_0516,
  WORD_VALUES,
  word,
} from "../components/dataset-factory/strings_0516";
import { ERROR_CODES, KEYS_0501, STRINGS_0501 } from "../components/dataset-factory/strings_0501";
import { Field } from "../components/ui/Field";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

// ---------------------------------------------------------------------------------------------
// catalogues
// ---------------------------------------------------------------------------------------------
const ARABIC = /[؀-ۿ]/;
const placeholders = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join(",");

for (const [name, keys, table] of [
  ["0516", KEYS_0516, STRINGS_0516],
  ["0501", KEYS_0501, STRINGS_0501],
] as const) {
  test(`[UX02-R3-C1] the ${name} catalogue has a real Arabic string for every key, with the same placeholders`, () => {
    const bad: string[] = [];
    for (const key of keys) {
      const entry = (table as Record<string, { en: string; ar: string }>)[key]!;
      if (entry.ar.trim() === "") bad.push(`${key}: empty ar`);
      else if (entry.ar === entry.en && /[A-Za-z]{4,}/.test(entry.en.replace(/\{\w+\}/g, "")))
        bad.push(`${key}: ar === en`);
      else if (!ARABIC.test(entry.ar) && !/^#?[{}\w\s#]*$/.test(entry.ar))
        bad.push(`${key}: no Arabic`);
      if (placeholders(entry.en) !== placeholders(entry.ar))
        bad.push(`${key}: placeholders differ`);
    }
    expect(bad, bad.join(" | ")).toEqual([]);
  });
}

test("[UX02-R3-C2] every closed vocabulary word has an Arabic word and English stays the raw wire value", () => {
  for (const kind of Object.keys(WORD_VALUES) as Array<keyof typeof WORD_VALUES>) {
    for (const value of WORD_VALUES[kind]) {
      expect(word(kind, value, "en")).toBe(value);
      expect(ARABIC.test(word(kind, value, "ar")), `${kind}/${value}`).toBe(true);
    }
  }
  expect(word("severity", "unseen_value", "ar"), "an unknown value is shown as received").toBe(
    "unseen_value",
  );
});

test("[UX02-R3-C3] every server error code the screens know has an Arabic message", () => {
  for (const code of SERVER_DETAIL_CODES)
    expect(ARABIC.test(STRINGS_0516[code].ar), code).toBe(true);
  for (const code of ERROR_CODES) expect(ARABIC.test(STRINGS_0501[code].ar), code).toBe(true);
});

test("[UX02-R3-C4] classifyFailure localizes its message and keeps the English text under the default locale", () => {
  expect(classifyFailure(422, "sha256_mismatch").message).toBe(
    "The uploaded bytes do not match the declared SHA-256; nothing was created.",
  );
  expect(ARABIC.test(classifyFailure(422, "sha256_mismatch", "ar").message)).toBe(true);
  expect(classifyFailure(500, null, "ar").message).toBe("فشل الطلب (500)؛ يمكن إعادة المحاولة");
});

// ---------------------------------------------------------------------------------------------
// Field: the requirement marker can sit outside the label (FTF-0516's pinned exact-label queries)
// ---------------------------------------------------------------------------------------------
test("[UX02-R3-F1] Field requirementPlacement=hint keeps the label text exact and announces the marker by description", () => {
  render(
    createElement(Field, {
      id: "f",
      label: "Successor dataset id",
      requirement: "required",
      requirementPlacement: "hint",
    }),
  );
  const input = screen.getByLabelText("Successor dataset id");
  expect(input.getAttribute("aria-required")).toBe("true");
  const describedBy = input.getAttribute("aria-describedby") ?? "";
  expect(describedBy.split(" ")).toContain("f-requirement");
  expect(document.getElementById("f-requirement")?.textContent).toBe("(required)");
  expect(document.querySelector("label")?.textContent).toBe("Successor dataset id");
});

test("[UX02-R3-F2] NEGATIVE: Field's default placement still puts the marker inside the label", () => {
  render(createElement(Field, { id: "g", label: "Name", requirement: "optional" }));
  expect(document.querySelector("label")?.textContent).toBe("Name (optional)");
});

// ---------------------------------------------------------------------------------------------
// FTF-0516
// ---------------------------------------------------------------------------------------------
const API = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const DATASET = "33333333-3333-3333-3333-333333333333";
const REPORT = "44444444-4444-4444-4444-444444444444";
const BASE = `${API}/api/v1/organizations/${ORG}/projects/${PROJECT}/datasets/${DATASET}`;
const sha = (c: string) => c.repeat(64);
const OPEN = sha("a");
const EVIDENCE = sha("e");

const report = (extra: Record<string, unknown> = {}) => ({
  id: REPORT,
  dataset_id: DATASET,
  dataset_version: 2,
  dataset_sha256: sha("1"),
  report_seq: 1,
  report_sha256: sha("2"),
  detector_versions: { pii: "1" },
  detectors_reported: ["pii"],
  issue_count: 1,
  blocking_count: 1,
  warning_count: 0,
  resolution_version: 3,
  is_clean: false,
  blocks_split: true,
  blocks_training: true,
  residual_risk_disclosure: "A clean report is not proof the data has no personal data.",
  created_at: "2026-10-09T08:00:00+00:00",
  updated_at: "2026-10-09T08:00:00+00:00",
  ...extra,
});
const issue = (id: string, extra: Record<string, unknown> = {}) => ({
  issue_id: id,
  report_id: REPORT,
  detector: "pii_secrets",
  category: "pii_secrets",
  severity: "blocking",
  reason_code: "pii_detected",
  message_sha256: sha("9"),
  batch_positions: [0, 2],
  record_indices: [10, null],
  evidence_digest: null,
  is_detector_failure: false,
  evidence_sha256: EVIDENCE,
  state: "open",
  resolution: null,
  ...extra,
});

type R = { ok: boolean; status: number; body: unknown; jsonThrows?: boolean };
let replies: Record<string, R>;

beforeEach(() => {
  replies = {
    "GET quality-report": { ok: true, status: 200, body: report() },
    "GET issues": { ok: true, status: 200, body: { items: [issue(OPEN)], next_after: null } },
    "GET issue-resolutions": { ok: true, status: 200, body: { items: [] } },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const route = url.replace(BASE, "").replace(/^\//, "").split("?")[0] as string;
      const reply = replies[`${init.method} ${route}`];
      if (!reply) throw new Error(`unmocked ${init.method} ${route}`);
      return {
        ok: reply.ok,
        status: reply.status,
        json: async () => {
          if (reply.jsonThrows) throw new SyntaxError("Unexpected token < in JSON");
          return reply.body;
        },
      };
    }),
  );
});

function mount516(locale?: "en" | "ar") {
  return render(
    createElement(DatasetIssueResolutionUI, {
      apiBaseUrl: API,
      organizationId: ORG,
      projectId: PROJECT,
      datasetId: DATASET,
      locale,
    }),
  );
}
const loadButton = () => screen.getByTestId("load-report-button");
async function load516() {
  fireEvent.click(loadButton());
  await waitFor(() =>
    expect(screen.queryByTestId("load-report-button")?.hasAttribute("disabled")).toBe(false),
  );
}

test("[UX02-R3-S1] 0516 under locale=ar: lang/dir on the root and an Arabic heading, button and intro", () => {
  mount516("ar");
  const root = screen.getByLabelText("معالجة مشكلات مجموعة البيانات");
  expect(root.getAttribute("lang")).toBe("ar");
  expect(root.getAttribute("dir")).toBe("rtl");
  expect(screen.getByText("مشكلات جودة مجموعة البيانات")).toBeTruthy();
  expect(loadButton().textContent).toBe("تحميل تقرير الجودة");
});

test("[UX02-R3-S2] 0516 default locale is English with lang=en dir=ltr", () => {
  mount516();
  const root = screen.getByLabelText("Dataset issue resolution");
  expect(root.getAttribute("lang")).toBe("en");
  expect(root.getAttribute("dir")).toBe("ltr");
});

test("[UX02-R3-S3] 0516 loaded under ar: closed-vocabulary words are Arabic, identifiers stay isolated LTR", async () => {
  mount516("ar");
  await load516();
  const line = screen.getByTestId(`issue-${OPEN}`);
  expect(line.textContent).toContain("حاجبة");
  expect(line.textContent).toContain("مفتوحة");
  expect(line.textContent).toContain("بيانات شخصية وأسرار");
  expect(line.textContent).not.toMatch(/blocking|\[open\]/);
  // the digest fragment on the Review button is data: isolated, not part of an Arabic run
  const button = screen.getByTestId(`review-${OPEN}`);
  expect(button.textContent).toBe("مراجعة aaaaaaaa");
  expect(button.querySelector("bdi")?.getAttribute("dir")).toBe("ltr");
});

test("[UX02-R3-S4] 0516 an unknown server detail is English for en and a localized refusal for ar (never raw English)", async () => {
  replies["GET quality-report"] = { ok: false, status: 500, body: { detail: "boom from server" } };
  const en = mount516();
  fireEvent.click(loadButton());
  await waitFor(() => expect(screen.getByTestId("dir-error").textContent).toBe("boom from server"));
  en.unmount();
  mount516("ar");
  fireEvent.click(loadButton());
  await waitFor(() => expect(screen.getByTestId("dir-error")).toBeTruthy());
  expect(screen.getByTestId("dir-error").textContent).toBe("رفض الخادم هذا الطلب.");
});

test("[UX02-R3-S5] 0516 a failed load announces one alert WITH a recovery step and focuses it, never <body>", async () => {
  replies["GET quality-report"] = {
    ok: false,
    status: 404,
    body: { detail: "quality_report_not_found" },
  };
  mount516();
  fireEvent.click(loadButton());
  await waitFor(() => expect(screen.getByTestId("dir-error")).toBeTruthy());
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(screen.getByTestId("dir-error-recovery").textContent?.length).toBeGreaterThan(10);
  expect(document.activeElement).toBe(screen.getByTestId("dir-error"));
});

test("[UX02-R3-S6] 0516 a non-JSON 200 is a localized read error, not the parser's English message", async () => {
  replies["GET quality-report"] = { ok: true, status: 200, body: null, jsonThrows: true };
  mount516("ar");
  fireEvent.click(loadButton());
  await waitFor(() => expect(screen.getByTestId("dir-error")).toBeTruthy());
  expect(screen.getByTestId("dir-error").textContent).toBe(
    "أرسل الخادم استجابة لا تستطيع هذه الصفحة قراءتها. لم يتم تغيير أي شيء.",
  );
});

test("[UX02-R3-S7] 0516 a null item or {} 200 is one malformed alert and the screen stays mounted", async () => {
  for (const items of [[null], [{}]]) {
    replies["GET issues"] = { ok: true, status: 200, body: { items, next_after: null } };
    const view = mount516();
    fireEvent.click(loadButton());
    await waitFor(() => expect(screen.getByTestId("dir-error")).toBeTruthy());
    expect(screen.getByTestId("dir-error").textContent).toBe("malformed quality response");
    expect(screen.queryByTestId("report")).toBeNull();
    expect(screen.getByLabelText("Dataset issue resolution")).toBeTruthy();
    view.unmount();
  }
});

test("[UX02-R3-S8] 0516 an empty report shows an EmptyState whose action reloads", async () => {
  replies["GET issues"] = { ok: true, status: 200, body: { items: [], next_after: null } };
  replies["GET quality-report"] = {
    ok: true,
    status: 200,
    body: report({ issue_count: 0, blocking_count: 0, is_clean: true }),
  };
  mount516();
  await load516();
  expect(screen.getByText("No issues to resolve")).toBeTruthy();
  const reload = screen.getByTestId("empty-reload-button");
  const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length;
  await act(async () => {
    fireEvent.click(reload);
  });
  await waitFor(() =>
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(calls),
  );
});

test("[UX02-R3-S9] 0516 the required rationale is wired to its hint and problems, and aria-invalid follows the problem", async () => {
  mount516();
  await load516();
  fireEvent.click(screen.getByTestId(`review-${OPEN}`));
  const box = screen.getByTestId("resolution-rationale");
  expect(box.getAttribute("aria-required")).toBe("true");
  expect(box.getAttribute("aria-invalid")).toBe("true");
  const described = (box.getAttribute("aria-describedby") ?? "").split(" ");
  expect(described.length).toBe(2);
  for (const id of described) expect(document.getElementById(id), id).toBeTruthy();
  fireEvent.change(box, { target: { value: "because" } });
  expect(box.getAttribute("aria-invalid")).toBeNull();
});

test("[UX02-R3-S10] 0516 the successor id is a UX01 Field with the exact label and a described requirement", async () => {
  mount516();
  await load516();
  fireEvent.click(screen.getByTestId(`review-${OPEN}`));
  fireEvent.change(screen.getByTestId("resolution-action-select"), {
    target: { value: "correct" },
  });
  const input = screen.getByLabelText("Successor dataset id");
  expect(input.getAttribute("aria-required")).toBe("true");
  expect(input.getAttribute("dir")).toBe("ltr");
  expect(document.getElementById(`${input.id}-requirement`)?.textContent).toBe("(required)");
});

// ---------------------------------------------------------------------------------------------
// FTF-0501-UI
// ---------------------------------------------------------------------------------------------
const CONNECTION = "55555555-5555-5555-5555-555555555555";

function mount501(locale?: "en" | "ar") {
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

test("[UX02-R3-U1] 0501-UI idle under ar: lang/dir, localized label, labelled controls and an Arabic start button", () => {
  mount501("ar");
  const root = screen.getByTestId("resumable-dataset-upload-ui");
  expect(root.getAttribute("lang")).toBe("ar");
  expect(root.getAttribute("dir")).toBe("rtl");
  expect(root.getAttribute("aria-label")).toBe("رفع مجموعة بيانات قابل للاستئناف");
  expect(screen.getByLabelText("الغرض")).toBe(screen.getByTestId("upload-purpose-select"));
  expect(screen.getByLabelText("نوع المحتوى")).toBe(
    screen.getByTestId("upload-content-type-select"),
  );
  expect(screen.getByLabelText("الملف")).toBe(screen.getByTestId("upload-file-input"));
  expect(screen.getByTestId("upload-start-button").textContent).toBe("بدء الرفع");
  const option = screen.getByTestId("upload-purpose-select").querySelector("option");
  expect(option?.textContent).toBe("التدريب");
  expect(option?.getAttribute("value")).toBe("training");
});

test("[UX02-R3-U2] 0501-UI the disabled start button says why (UX01 disabledReason), in both languages", () => {
  const en = mount501();
  expect(screen.getByTestId("upload-start-button").hasAttribute("disabled")).toBe(true);
  expect(screen.getByTestId("disabled-reason").textContent).toBe("Choose a file to start.");
  en.unmount();
  mount501("ar");
  expect(screen.getByTestId("disabled-reason").textContent).toBe("اختر ملفًا للبدء.");
});

test("[UX02-R3-U3] 0501-UI uses the UX01 Button (data-variant) for its actions", () => {
  mount501();
  expect(screen.getByTestId("upload-start-button").getAttribute("data-variant")).toBe("primary");
});

test("[UX02-R3-U4] 0501-UI a failed create is one alert with a recovery step, focused, localized under ar", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
  );
  mount501("ar");
  fireEvent.change(screen.getByTestId("upload-file-input"), {
    target: { files: [new File([new Uint8Array([1, 2, 3])], "data.jsonl")] },
  });
  fireEvent.click(screen.getByTestId("upload-start-button"));
  await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
  const alert = screen.getAllByRole("alert")[0]!;
  expect(alert.textContent).toBe("فشل الطلب (500)؛ يمكن إعادة المحاولة");
  await waitFor(() => expect(document.activeElement).toBe(alert));
});

test("[UX02-R3-U5] 0501-UI an expired session offers a way forward: 'Start a new upload' returns to a fresh form", async () => {
  window.localStorage.setItem(
    `sovra.dataset-upload-draft.${ORG}.${PROJECT}.${CONNECTION}`,
    JSON.stringify({}),
  );
  // Drive the real path: finalize answers 410 upload_expired.
  const session = {
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const ok = (status: number, body: unknown) => ({
        ok: status < 300,
        status,
        json: async () => body,
      });
      if (u.pathname.endsWith("/finalize")) return ok(410, { detail: "upload_expired" });
      if (u.pathname.endsWith("/part-urls")) {
        return ok(200, {
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
        return ok(200, { part_size_bytes: 3, part_count: 1, parts: [] });
      if (u.hostname === "storage.example") return ok(200, {});
      if (init?.method === "POST") return ok(201, session);
      return ok(404, {});
    }),
  );
  mount501();
  fireEvent.change(screen.getByTestId("upload-file-input"), {
    target: { files: [new File([new Uint8Array([1, 2, 3])], "data.jsonl")] },
  });
  fireEvent.click(screen.getByTestId("upload-start-button"));
  await waitFor(() => expect(screen.getByTestId("upload-dataset-name-input")).toBeTruthy());
  fireEvent.change(screen.getByTestId("upload-dataset-name-input"), { target: { value: "ds" } });
  fireEvent.click(screen.getByTestId("upload-finalize-button"));
  await waitFor(() => expect(screen.getByTestId("upload-expired-text")).toBeTruthy());
  fireEvent.click(screen.getByTestId("upload-new-upload-button"));
  expect(screen.getByTestId("upload-form")).toBeTruthy();
});
