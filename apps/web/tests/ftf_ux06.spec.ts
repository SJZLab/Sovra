// @vitest-environment jsdom
//
// FTF-UX06 round 1 -- evaluation-factory redesign (docs/design/audit-2026-10.md section 8,
// cross-cutting X1/X3/X4/X7). Proves the behaviours `ftf_0602.spec.ts` does not: bilingual en/ar
// rendering, per-field aria-describedby association, aria-busy during in-flight requests, on-blur
// JSON validation, and the per-row case_key hint on a returned field error. Each test has a
// matching NEGATIVE counterpart proving the new behaviour does not fire when it should not
// (CLAUDE.md "Relay is execution-only" step 4 / routines.md STEP 5b).
import { createElement } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { EvaluationCaseEditorImport } from "../components/evaluation-factory/evaluation_case_editor_import";
import {
  CASE_STRINGS,
  CASE_STRING_KEYS,
  type CaseStringKey,
} from "../components/evaluation-factory/strings_0602";

const ARABIC = /[؀-ۿ]/;

const API_BASE = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const SUITE = "33333333-3333-3333-3333-333333333333";
const CASE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const LIST_URL = `${API_BASE}/api/v1/organizations/${ORG}/projects/${PROJECT}/evaluation-suites/${SUITE}/cases`;

function evalCase(id: string, key: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    case_key: key,
    category: "refunds",
    severity: "high",
    critical: false,
    language: "en",
    group_key: `group-${key}`,
    input_json: { prompt: "p" },
    expected_json: { state: "refunded" },
    grader_type: "exact_match",
    grader_config_json: {},
    weight: 1,
    ...extra,
  };
}

function importRow(key: string, extra: Record<string, unknown> = {}) {
  return {
    case_key: key,
    category: "refunds",
    severity: "high",
    language: "en",
    group_key: `group-${key}`,
    input_json: { prompt: "p" },
    expected_json: { state: "refunded" },
    grader_type: "exact_match",
    ...extra,
  };
}

type Reply = { ok: boolean; status?: number; body: unknown };
let replies: Record<string, Reply[]>;

function enqueue(method: string, url: string, ...queued: Reply[]) {
  const key = `${method} ${url}`;
  replies[key] = [...(replies[key] ?? []), ...queued];
}
const ok = (body: unknown): Reply => ({ ok: true, status: 200, body });
const fail = (status: number, body: unknown): Reply => ({ ok: false, status, body });

/** A deferred fetch: the caller controls exactly when the request "arrives", so the in-flight
 * (aria-busy) window can be observed deterministically, the same technique
 * `ftf_ux03_selectors.spec.ts`'s own `inFlight()` uses. */
function inFlight() {
  let release: (value: unknown) => void = () => undefined;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

beforeEach(() => {
  replies = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: { method?: string }) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const next = replies[key]?.shift();
      if (!next) throw new Error(`unexpected request ${key}`);
      return { ok: next.ok, status: next.status ?? 200, json: async () => next.body };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderUI(locale?: "en" | "ar") {
  return render(
    createElement(EvaluationCaseEditorImport, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      suiteId: SUITE,
      locale,
    }),
  );
}

async function loadCases(items: unknown[], nextAfter: string | null = null) {
  enqueue("GET", LIST_URL, ok({ items, next_after: nextAfter }));
  fireEvent.click(screen.getByTestId("load-cases-button"));
  await waitFor(() => expect(screen.getByTestId("case-list")).toBeTruthy());
}

async function openEditor(id: string) {
  fireEvent.click(screen.getByTestId(`edit-case-${id}`));
  await waitFor(() => expect(screen.getByTestId("case-form")).toBeTruthy());
}
function setField(testId: string, value: string) {
  fireEvent.change(screen.getByTestId(testId), { target: { value } });
}
function blur(testId: string) {
  fireEvent.blur(screen.getByTestId(testId));
}

// ---------------------------------------------------------------------------------------------
// X4: bilingual en/ar + RTL
// ---------------------------------------------------------------------------------------------
describe("[UX06-X4] bilingual en/ar", () => {
  test("locale='ar' renders Arabic labels, dir=rtl and lang=ar", () => {
    renderUI("ar");
    const section = screen.getByRole("region", { name: "حالات التقييم" });
    expect(section.getAttribute("dir")).toBe("rtl");
    expect(section.getAttribute("lang")).toBe("ar");
    expect(screen.getByRole("heading", { name: "استيراد الحالات" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "تحميل الحالات" })).toBeTruthy();
  });

  test("NEGATIVE: the default locale (no prop) stays English/LTR, not Arabic", () => {
    renderUI();
    const section = screen.getByRole("region", { name: "Evaluation cases" });
    expect(section.getAttribute("dir")).toBe("ltr");
    expect(section.getAttribute("lang")).toBe("en");
    expect(screen.queryByText("استيراد الحالات")).toBeNull();
  });

  test("an unshipped locale value falls back to English, not a blank render", () => {
    render(
      createElement(EvaluationCaseEditorImport, {
        apiBaseUrl: API_BASE,
        organizationId: ORG,
        projectId: PROJECT,
        suiteId: SUITE,
        // @ts-expect-error -- deliberately an unshipped locale, same as a stored preference
        // value this build does not know; resolveLocale must still render real text.
        locale: "fr",
      }),
    );
    expect(screen.getByRole("heading", { name: "Import cases" })).toBeTruthy();
  });

  test("a locale change at runtime re-renders every string, not just the ones touched so far", () => {
    const { rerender } = renderUI("en");
    expect(screen.getByText("Load cases")).toBeTruthy();
    rerender(
      createElement(EvaluationCaseEditorImport, {
        apiBaseUrl: API_BASE,
        organizationId: ORG,
        projectId: PROJECT,
        suiteId: SUITE,
        locale: "ar",
      }),
    );
    expect(screen.getByText("تحميل الحالات")).toBeTruthy();
    expect(screen.queryByText("Load cases")).toBeNull();
  });

  // Round 2 (L2 reviewer finding, Probe A): the earlier tests above only ever render the EMPTY
  // list under locale="ar" -- none of them loaded a populated case, so three literal English
  // words baked into the row ("critical", "group ", "Edit") went unnoticed. This is the row test.
  test("a populated case row under locale='ar' has no stray English word", async () => {
    renderUI("ar");
    await loadCases([evalCase(CASE_A, "a-case", { critical: true })]);
    const row = screen.getByTestId(`case-row-${CASE_A}`);
    expect(row.textContent).toContain("حرِج"); // critical tag
    expect(row.textContent).toContain("مجموعة"); // group prefix
    expect(screen.getByTestId(`edit-case-${CASE_A}`).textContent).toBe("تعديل"); // Edit button
    expect(row.textContent).not.toContain("critical");
    expect(row.textContent).not.toContain("group ");
    expect(screen.getByTestId(`edit-case-${CASE_A}`).textContent).not.toBe("Edit");
  });

  test("NEGATIVE: the default locale keeps the row's critical tag, group prefix and Edit button in English", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case", { critical: true })]);
    const row = screen.getByTestId(`case-row-${CASE_A}`);
    expect(row.textContent).toContain("critical");
    expect(row.textContent).toContain("group group-a-case");
    expect(screen.getByTestId(`edit-case-${CASE_A}`).textContent).toBe("Edit");
  });

  // Round 3 (second independent reviewer, Finding B, sev 1): a non-Error rejection's fallback
  // "unknown error" word was a bare literal, never routed through t0602 -- the one word that could
  // survive untranslated inside an otherwise-Arabic network-error sentence.
  test("a non-Error fetch rejection under locale='ar' shows no stray 'unknown error' English", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      // Deliberately not an Error instance -- the exact shape networkFailure's fallback exists for.
      throw "a plain string rejection, not an Error instance";
    });
    renderUI("ar");
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    const text = screen.getByTestId("list-error").textContent ?? "";
    expect(text).toContain("خطأ غير معروف");
    expect(text).not.toContain("unknown error");
  });

  test("NEGATIVE: the same non-Error rejection at the default locale still says 'unknown error' in English", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw "a plain string rejection, not an Error instance";
    });
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    expect(screen.getByTestId("list-error").textContent).toContain("unknown error");
  });
});

// ---------------------------------------------------------------------------------------------
// X3: field-level errors connected to their own input via aria-describedby
// ---------------------------------------------------------------------------------------------
describe("[UX06-X3] error <-> field association", () => {
  test("a severity field error is referenced by the severity select's aria-describedby", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    const url = `${LIST_URL}/${CASE_A}`;
    enqueue(
      "PATCH",
      url,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: "severity", code: "invalid_value" }],
      }),
    );
    setField("field-severity", "low");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-field-errors")).toBeTruthy());

    const select = screen.getByTestId("field-severity");
    const describedBy = select.getAttribute("aria-describedby") ?? "";
    expect(describedBy).toContain("field-severity-error");
    const errorNode = document.getElementById("field-severity-error");
    expect(errorNode?.getAttribute("role")).toBe("alert");
    expect(errorNode?.textContent).toContain("invalid_value");
  });

  test("NEGATIVE: with no field error, the severity select's aria-describedby names no error id", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    const select = screen.getByTestId("field-severity");
    expect(select.getAttribute("aria-describedby") ?? "").not.toContain("field-severity-error");
    expect(document.getElementById("field-severity-error")).toBeNull();
  });

  test("a category field error does not leak onto the unrelated weight field", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    const url = `${LIST_URL}/${CASE_A}`;
    enqueue(
      "PATCH",
      url,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: "category", code: "too_long" }],
      }),
    );
    setField("field-category", "x".repeat(300));
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(document.getElementById("field-category-error")).toBeTruthy());
    const weight = screen.getByTestId("field-weight");
    expect(weight.getAttribute("aria-describedby") ?? "").not.toContain("error");
  });

  // Round 2 (L2 reviewer finding, Probe B): `buildPatch` treats category/severity/language/
  // group_key/grader_type identically as server-patchable string fields, so a field error naming
  // "critical" or "language" is exactly as plausible as one naming "category" -- round 1 wired
  // aria-describedby for 8 of the 10 patchable fields and silently left these two out.
  test("a language field error is referenced by the language input's aria-describedby", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    const url = `${LIST_URL}/${CASE_A}`;
    enqueue(
      "PATCH",
      url,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: "language", code: "unsupported" }],
      }),
    );
    setField("field-language", "zz");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-field-errors")).toBeTruthy());

    const input = screen.getByTestId("field-language");
    expect(input.getAttribute("aria-describedby") ?? "").toContain("field-language-error");
    const errorNode = document.getElementById("field-language-error");
    expect(errorNode?.getAttribute("role")).toBe("alert");
    expect(errorNode?.textContent).toContain("unsupported");
  });

  test("a critical field error is referenced by the critical checkbox's aria-describedby", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case", { grader_type: "tool_name" })]);
    await openEditor(CASE_A);
    const url = `${LIST_URL}/${CASE_A}`;
    enqueue(
      "PATCH",
      url,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: "critical", code: "requires_independent_grader" }],
      }),
    );
    fireEvent.click(screen.getByTestId("field-critical"));
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-field-errors")).toBeTruthy());

    const checkbox = screen.getByTestId("field-critical");
    expect(checkbox.getAttribute("aria-describedby") ?? "").toContain("field-critical-error");
    const errorNode = document.getElementById("field-critical-error");
    expect(errorNode?.getAttribute("role")).toBe("alert");
    expect(errorNode?.textContent).toContain("requires_independent_grader");
  });

  test("NEGATIVE: with no field error, the language input and critical checkbox name no error id", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    expect(
      (screen.getByTestId("field-language").getAttribute("aria-describedby") ?? "").includes(
        "error",
      ),
    ).toBe(false);
    expect(
      (screen.getByTestId("field-critical").getAttribute("aria-describedby") ?? "").includes(
        "error",
      ),
    ).toBe(false);
    expect(document.getElementById("field-language-error")).toBeNull();
    expect(document.getElementById("field-critical-error")).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// X7: aria-busy during in-flight requests, with the shared Pending indicator past 1 s
// ---------------------------------------------------------------------------------------------
describe("[UX06-X7] busy state", () => {
  test("the list region is aria-busy while loading, and the trigger is disabled", async () => {
    const pending = inFlight();
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => pending.promise);
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeTruthy());
    expect((screen.getByTestId("load-cases-button") as HTMLButtonElement).disabled).toBe(true);
    pending.release({ ok: true, status: 200, json: async () => ({ items: [], next_after: null }) });
    await waitFor(() =>
      expect((screen.getByTestId("load-cases-button") as HTMLButtonElement).disabled).toBe(false),
    );
  });

  test("NEGATIVE: once loading resolves, aria-busy is not left stuck true", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    expect(document.querySelector('[aria-busy="true"]')).toBeNull();
  });

  test("importing sets aria-busy on the import region and disables the import button", async () => {
    const pending = inFlight();
    renderUI();
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify([importRow("c1")]) },
    });
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => pending.promise);
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeTruthy());
    expect((screen.getByTestId("import-button") as HTMLButtonElement).disabled).toBe(true);
    pending.release({
      ok: true,
      status: 201,
      json: async () => ({ items: [evalCase(CASE_A, "c1")] }),
    });
    await waitFor(() => expect(screen.getByTestId("notice")).toBeTruthy());
  });
});

// ---------------------------------------------------------------------------------------------
// Per-component H5: JSON fields get a schema hint and validate on blur
// ---------------------------------------------------------------------------------------------
describe("[UX06-H5] JSON field hint and on-blur validation", () => {
  test("each JSON field carries a visible schema hint linked via aria-describedby", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    const field = screen.getByTestId("field-input-json");
    expect(field.getAttribute("aria-describedby")).toContain("field-input-json-hint");
    expect(screen.getByTestId("field-input-json-hint").textContent).toContain("JSON object");
  });

  test("invalid JSON on blur shows an inline error before any submit", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    setField("field-input-json", "{not json");
    blur("field-input-json");
    expect(screen.getByTestId("field-input-json-blur-error")).toBeTruthy();
    expect(screen.getByTestId("field-input-json").getAttribute("aria-describedby")).toContain(
      "field-input-json-blur-error",
    );
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1); // only the initial listing
  });

  test("NEGATIVE: valid JSON on blur shows no inline error", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    setField("field-input-json", '{"prompt": "ok"}');
    blur("field-input-json");
    expect(screen.queryByTestId("field-input-json-blur-error")).toBeNull();
  });

  test("NEGATIVE: fixing the field after a blur error clears it on the next blur", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    setField("field-input-json", "{broken");
    blur("field-input-json");
    expect(screen.getByTestId("field-input-json-blur-error")).toBeTruthy();
    setField("field-input-json", '{"prompt": "fixed"}');
    blur("field-input-json");
    expect(screen.queryByTestId("field-input-json-blur-error")).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// Per-component H9: an import field error names the affected row's own case_key
// ---------------------------------------------------------------------------------------------
describe("[UX06-H9] row hint on import field errors", () => {
  test("a field error naming row 2 shows that row's own case_key, not just its number", async () => {
    renderUI();
    const rows = [importRow("alpha"), importRow("bravo")];
    enqueue(
      "POST",
      LIST_URL,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: 1, field: "group_key", code: "required" }],
      }),
    );
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify(rows) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
    const text = screen.getByTestId("import-field-errors").textContent ?? "";
    expect(text).toContain("Row 2 / group_key: required");
    expect(text).toContain("bravo");
    expect(text).not.toContain("alpha");
  });

  test("NEGATIVE: a request-level error (index null) names no row and fabricates no case_key", async () => {
    renderUI();
    const rows = [importRow("alpha")];
    enqueue(
      "POST",
      LIST_URL,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: null, code: "body_malformed" }],
      }),
    );
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify(rows) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
    const text = screen.getByTestId("import-field-errors").textContent ?? "";
    expect(text).toContain("Request: body_malformed");
    expect(text).not.toContain("alpha");
    expect(text).not.toContain("case_key");
  });

  test("NEGATIVE: an out-of-range row index shows the row/field/code line with no case_key suffix", async () => {
    renderUI();
    const rows = [importRow("alpha")];
    enqueue(
      "POST",
      LIST_URL,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: 5, field: "group_key", code: "required" }],
      }),
    );
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify(rows) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
    const text = screen.getByTestId("import-field-errors").textContent ?? "";
    expect(text).toContain("Row 6 / group_key: required");
    expect(text).not.toContain("case_key");
  });
});

// ---------------------------------------------------------------------------------------------
// X1: rebuilt on the shared UX01 library (Button/EmptyState), not hand-rolled controls
// ---------------------------------------------------------------------------------------------
describe("[UX06-X1] shared component library", () => {
  test("an empty, loaded case list shows the shared EmptyState with a title and explanation", async () => {
    renderUI();
    await loadCases([]);
    expect(screen.getByRole("region", { name: "No cases yet" })).toBeTruthy();
    expect(screen.getByText("Import cases below, or ask an evaluator to add some.")).toBeTruthy();
  });

  test("NEGATIVE: a non-empty list never shows the empty state", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    expect(screen.queryByRole("region", { name: "No cases yet" })).toBeNull();
  });

  test("the disabled import button carries the disabled styling hook from the shared Button", async () => {
    renderUI();
    const button = screen.getByTestId("import-button") as HTMLButtonElement;
    expect(button.getAttribute("data-variant")).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// FTF-UX06 round 2 (QUEUE 0f; PR #975's return of #964)
// ---------------------------------------------------------------------------------------------
describe("[UX06-R2 FOCUS-LOST-TO-BODY] a failed load/import/save takes focus on its own alert", () => {
  test("a failed load takes focus on the list-error alert, not <body>", async () => {
    enqueue("GET", LIST_URL, fail(503, {}));
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    expect(document.activeElement).toBe(screen.getByTestId("list-error"));
  });

  test("NEGATIVE: with no failure, there is no list-error alert to take focus", async () => {
    renderUI();
    expect(screen.queryByTestId("list-error")).toBeNull();
  });

  test("a failed import takes focus on the import-error alert, not <body>", async () => {
    enqueue("POST", LIST_URL, fail(503, {}));
    renderUI();
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify([importRow("c1")]) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-error")).toBeTruthy());
    expect(document.activeElement).toBe(screen.getByTestId("import-error"));
  });

  test("a failed save takes focus on the edit-error alert, not <body>", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    enqueue("PATCH", `${LIST_URL}/${CASE_A}`, fail(503, {}));
    setField("field-language", "fr");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-error")).toBeTruthy());
    expect(document.activeElement).toBe(screen.getByTestId("edit-error"));
  });

  test("a second failure with the identical message re-focuses the alert even though the text did not change", async () => {
    enqueue("GET", LIST_URL, fail(503, {}));
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    const alert = screen.getByTestId("list-error");
    // Simulate the user tabbing away before retrying.
    screen.getByTestId("load-cases-button").focus();
    expect(document.activeElement).not.toBe(alert);
    enqueue("GET", LIST_URL, fail(503, {}));
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("list-error")));
  });
});

describe("[UX06-R2 ERRORS-NO-RECOVERY] every top-level alert names a concrete recovery step", () => {
  test("the list-load alert tells the user to press Load cases again", async () => {
    enqueue("GET", LIST_URL, fail(503, {}));
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    expect(screen.getByTestId("list-error").textContent).toContain("Press Load cases again.");
  });

  test("the import alert tells the user to fix the file and import again", async () => {
    enqueue("POST", LIST_URL, fail(503, {}));
    renderUI();
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify([importRow("c1")]) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-error")).toBeTruthy());
    expect(screen.getByTestId("import-error").textContent).toContain("import again");
  });

  test("the save alert tells the user to fix the field and save again", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    enqueue("PATCH", `${LIST_URL}/${CASE_A}`, fail(503, {}));
    setField("field-language", "fr");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-error")).toBeTruthy());
    expect(screen.getByTestId("edit-error").textContent).toContain("save again");
  });
});

describe("[UX06-R2 X1] the empty case list's EmptyState has a real action", () => {
  test("clicking the empty-state action focuses the import textarea", async () => {
    renderUI();
    await loadCases([]);
    fireEvent.click(screen.getByTestId("empty-state-import-action"));
    expect(document.activeElement).toBe(screen.getByTestId("import-text"));
  });
});

describe("[UX06-R2 NOT-BILINGUAL] non-JSON 200 responses never leak a raw browser parser message", () => {
  test("a non-JSON 200 on list load shows the localized malformed-listing message, English default", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON at position 0");
      },
    }));
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    const text = screen.getByTestId("list-error").textContent ?? "";
    expect(text).toContain("malformed case listing response");
    expect(text).not.toContain("Unexpected token");
  });

  test("the same non-JSON 200 under locale='ar' shows the localized Arabic message, no English leak", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON at position 0");
      },
    }));
    renderUI("ar");
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    const text = screen.getByTestId("list-error").textContent ?? "";
    expect(text).toContain("استجابة قائمة الحالات غير سليمة");
    expect(text).not.toContain("Unexpected token");
    expect(text).not.toContain("SyntaxError");
  });

  test("a non-JSON 200 on import shows the localized malformed-import message, no parser leak", async () => {
    renderUI();
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify([importRow("c1")]) },
    });
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => ({
      ok: true,
      status: 201,
      json: async () => {
        throw new SyntaxError("Unexpected end of JSON input");
      },
    }));
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-error")).toBeTruthy());
    const text = screen.getByTestId("import-error").textContent ?? "";
    expect(text).toContain("malformed import response");
    expect(text).not.toContain("Unexpected end");
  });

  test("an unreachable-server fetch rejection under locale='ar' never shows the raw English 'Failed to fetch'", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw new TypeError("Failed to fetch");
    });
    renderUI("ar");
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    const text = screen.getByTestId("list-error").textContent ?? "";
    expect(text).toContain("تعذّر الوصول إلى الخادم");
    expect(text).not.toContain("Failed to fetch");
  });

  test("NEGATIVE: the same unreachable-server rejection at the default English locale keeps the raw message", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw new TypeError("Failed to fetch");
    });
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    expect(screen.getByTestId("list-error").textContent).toContain("Failed to fetch");
  });
});

describe("[UX06-R2 NOT-BILINGUAL] a server-returned field name and validation code are translated under locale='ar'", () => {
  test("an import field error's field label and code are both translated", async () => {
    renderUI("ar");
    const rows = [importRow("alpha")];
    enqueue(
      "POST",
      LIST_URL,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: 0, field: "group_key", code: "required" }],
      }),
    );
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify(rows) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
    const text = screen.getByTestId("import-field-errors").textContent ?? "";
    expect(text).toContain("مفتاح المجموعة"); // translated field label
    expect(text).toContain("مطلوب"); // translated code
    expect(text).not.toContain("group_key");
    expect(text).not.toContain("required");
  });

  test("NEGATIVE: the identical field error keeps the exact raw 'Row 1 / group_key: required' text at the default locale", async () => {
    renderUI();
    const rows = [importRow("alpha")];
    enqueue(
      "POST",
      LIST_URL,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: 0, field: "group_key", code: "required" }],
      }),
    );
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify(rows) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
    expect(screen.getByTestId("import-field-errors").textContent).toContain(
      "Row 1 / group_key: required",
    );
  });

  test("an unmapped field/code combination falls back to the raw value under 'ar' too (disclosed, not hidden)", async () => {
    renderUI("ar");
    const rows = [importRow("alpha")];
    enqueue(
      "POST",
      LIST_URL,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: 0, field: "a_future_field", code: "a_future_code" }],
      }),
    );
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify(rows) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
    const text = screen.getByTestId("import-field-errors").textContent ?? "";
    expect(text).toContain("a_future_field");
    expect(text).toContain("a_future_code");
  });
});

describe("[UX06-R2 NOT-ON-UX01] category/language/group_key/weight are built on the shared Field component", () => {
  test("a category field error renders through Field's own error association, same id as round 1", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    enqueue(
      "PATCH",
      `${LIST_URL}/${CASE_A}`,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: "category", code: "too_long" }],
      }),
    );
    setField("field-category", "x".repeat(300));
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(document.getElementById("field-category-error")).toBeTruthy());
    expect(screen.getByTestId("field-category").getAttribute("aria-describedby") ?? "").toContain(
      "field-category-error",
    );
    expect(screen.getByTestId("field-category").getAttribute("aria-invalid")).toBe("true");
  });

  test("the category/language/group_key/weight fields each carry a visible required marker", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    expect(screen.getAllByText("(required)").length).toBeGreaterThanOrEqual(4);
  });
});

// ---------------------------------------------------------------------------------------------
// FTF-UX06 round 2, second L2 review (independent reviewer return) -- the specific faults that
// stayed GREEN on the first round-2 submission (point 7's named list) now each have their own
// RED-on-removal test, a translated severity/case_key/unknown-detail pass, the wrong-recovery-step
// fix, and a catalogue-wide stray-English sweep (same pattern `strings_04a22.ts`'s
// `MANDATE_STRING_KEYS`/`MANDATE_STRINGS` already uses).
// ---------------------------------------------------------------------------------------------
// `noticeImported`'s English counts a literal "case"/"cases" plural with `{plural}`; Arabic "حالة"
// needs no such suffix (round-1 content, a real grammar difference, not an untranslated hole).
const HOLES_EXCEPTION: Partial<Record<CaseStringKey, true>> = { noticeImported: true };

describe("[UX06-R2b CATALOGUE] every en/ar string is complete, no silent English-under-ar", () => {
  test("every entry has non-empty English and genuinely Arabic text, with matching placeholders", () => {
    for (const key of CASE_STRING_KEYS) {
      const entry = CASE_STRINGS[key];
      expect(entry.en.trim().length, `${key} en`).toBeGreaterThan(0);
      expect(ARABIC.test(entry.ar), `${key} ar has no Arabic text`).toBe(true);
      if (HOLES_EXCEPTION[key]) continue;
      const holes = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join(",");
      expect(holes(entry.ar), `${key} placeholders differ`).toBe(holes(entry.en));
    }
  });
});

describe("[UX06-R2b UNPINNED] faults the second L2 review reproduced as GREEN", () => {
  test("F10: the import notice carries role=status", async () => {
    renderUI();
    enqueue("POST", LIST_URL, ok({ items: [evalCase(CASE_A, "c1")] }));
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify([importRow("c1")]) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("notice")).toBeTruthy());
    expect(screen.getByTestId("notice").getAttribute("role")).toBe("status");
  });

  test("F11: an input_json server field error carries role=alert", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    enqueue(
      "PATCH",
      `${LIST_URL}/${CASE_A}`,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: "input_json", code: "schema_mismatch" }],
      }),
    );
    setField("field-input-json", '{"a":1}');
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(document.getElementById("field-input-json-error")).toBeTruthy());
    expect(document.getElementById("field-input-json-error")?.getAttribute("role")).toBe("alert");
  });

  test("F11: a grader_config_json server field error carries role=alert", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    enqueue(
      "PATCH",
      `${LIST_URL}/${CASE_A}`,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: "grader_config_json", code: "schema_mismatch" }],
      }),
    );
    setField("field-grader-config-json", '{"a":1}');
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() =>
      expect(document.getElementById("field-grader-config-json-error")).toBeTruthy(),
    );
    expect(document.getElementById("field-grader-config-json-error")?.getAttribute("role")).toBe(
      "alert",
    );
  });

  test("F12: the load-more button is disabled while a further page is loading", async () => {
    const pending = inFlight();
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")], "cursor-1");
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => pending.promise);
    fireEvent.click(screen.getByTestId("load-more-button"));
    await waitFor(() =>
      expect((screen.getByTestId("load-more-button") as HTMLButtonElement).disabled).toBe(true),
    );
    pending.release({ ok: true, status: 200, json: async () => ({ items: [], next_after: null }) });
  });

  test("F13/F14: the save button is disabled and the form is aria-busy while saving", async () => {
    const pending = inFlight();
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    setField("field-language", "fr");
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => pending.promise);
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() =>
      expect((screen.getByTestId("save-case-button") as HTMLButtonElement).disabled).toBe(true),
    );
    expect(screen.getByTestId("case-form").getAttribute("aria-busy")).toBe("true");
    pending.release({
      ok: true,
      status: 200,
      json: async () => evalCase(CASE_A, "a-case", { language: "fr" }),
    });
  });

  test("F15: a Pending indicator (role=status) appears during a save that takes over a second", async () => {
    const pending = inFlight();
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    setField("field-language", "fr");
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => pending.promise);
    fireEvent.click(screen.getByTestId("save-case-button"));
    await new Promise((resolve) => setTimeout(resolve, 1100));
    expect(document.querySelector('[role="status"]')).toBeTruthy();
    pending.release({
      ok: true,
      status: 200,
      json: async () => evalCase(CASE_A, "a-case", { language: "fr" }),
    });
  }, 10_000);

  test("F28: a Pending indicator (role=status) appears during an import that takes over a second", async () => {
    const pending = inFlight();
    renderUI();
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify([importRow("c1")]) },
    });
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => pending.promise);
    fireEvent.click(screen.getByTestId("import-button"));
    await new Promise((resolve) => setTimeout(resolve, 1100));
    expect(document.querySelector('[role="status"]')).toBeTruthy();
    pending.release({
      ok: true,
      status: 201,
      json: async () => ({ items: [evalCase(CASE_A, "c1")] }),
    });
  }, 10_000);

  test("F16: the JSON field hint is translated under locale='ar'", async () => {
    renderUI("ar");
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    expect(screen.getByTestId("field-input-json-hint").textContent).toContain(
      "يجب أن يكون كائن JSON",
    );
  });

  test("F17: the empty state's title is translated under locale='ar'", async () => {
    renderUI("ar");
    await loadCases([]);
    expect(screen.getByText("لا توجد حالات بعد")).toBeTruthy();
  });

  test("F18: a 500 with no detail field shows the translated generic message under locale='ar'", async () => {
    enqueue("GET", LIST_URL, fail(500, {}));
    renderUI("ar");
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    expect(screen.getByTestId("list-error").textContent).toContain("فشل الطلب (500)");
  });

  test("F5/F6: the 'too_long' code and 'weight' field name are both translated under locale='ar'", async () => {
    renderUI("ar");
    enqueue(
      "POST",
      LIST_URL,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: 0, field: "weight", code: "too_long" }],
      }),
    );
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify([importRow("alpha")]) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
    const text = screen.getByTestId("import-field-errors").textContent ?? "";
    expect(text).toContain("الوزن");
    expect(text).toContain("طويل جدًا");
    expect(text).not.toContain("weight");
    expect(text).not.toContain("too_long");
  });
});

describe("[UX06-R2b NOT-BILINGUAL residue] severity labels, case_key, local JSON field names, unknown detail", () => {
  test("severity option labels are translated under locale='ar'", async () => {
    renderUI("ar");
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    const select = screen.getByTestId("field-severity") as HTMLSelectElement;
    const texts = Array.from(select.options).map((o) => o.textContent);
    expect(texts).toContain("عالية");
    expect(texts).not.toContain("high");
  });

  test("NEGATIVE: severity option labels stay English at the default locale", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    const select = screen.getByTestId("field-severity") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toContain("high");
  });

  test("the severity badge shown in a case row is translated under locale='ar'", async () => {
    renderUI("ar");
    await loadCases([evalCase(CASE_A, "a-case", { severity: "high" })]);
    const row = screen.getByTestId(`case-row-${CASE_A}`);
    expect(row.textContent).toContain("عالية");
    expect(row.textContent).not.toContain("high");
  });

  test("a case_key field error's field label is translated under locale='ar'", async () => {
    renderUI("ar");
    enqueue(
      "POST",
      LIST_URL,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: 0, field: "case_key", code: "duplicate_case_key" }],
      }),
    );
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: JSON.stringify([importRow("alpha")]) },
    });
    fireEvent.click(screen.getByTestId("import-button"));
    await waitFor(() => expect(screen.getByTestId("import-field-errors")).toBeTruthy());
    const text = screen.getByTestId("import-field-errors").textContent ?? "";
    expect(text).toContain("مفتاح الحالة");
    expect(text).not.toContain("case_key");
  });

  test("a local JSON validation error names the translated field under locale='ar'", async () => {
    renderUI("ar");
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    setField("field-input-json", "{not json");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-error")).toBeTruthy());
    const text = screen.getByTestId("edit-error").textContent ?? "";
    expect(text).toContain("المدخل (JSON)");
    expect(text).not.toContain("input_json");
  });

  test("NEGATIVE: the same local JSON validation error keeps the raw field name at the default locale", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    setField("field-input-json", "{not json");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-error")).toBeTruthy());
    expect(screen.getByTestId("edit-error").textContent).toContain(
      "input_json must be a JSON object",
    );
  });

  test("an unmapped server detail code degrades to the generic translated message under locale='ar', never the raw code", async () => {
    enqueue("GET", LIST_URL, fail(500, { detail: "some_new_server_code" }));
    renderUI("ar");
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    const text = screen.getByTestId("list-error").textContent ?? "";
    expect(text).toContain("فشل الطلب (500)");
    expect(text).not.toContain("some_new_server_code");
  });

  test("NEGATIVE: the same unmapped detail code stays raw at the default English locale", async () => {
    enqueue("GET", LIST_URL, fail(500, { detail: "some_new_server_code" }));
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    expect(screen.getByTestId("list-error").textContent).toContain("some_new_server_code");
  });

  test("case_key/category/group_key are isolated in <bdi> for bidi-safe rendering", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    const row = screen.getByTestId(`case-row-${CASE_A}`);
    expect(row.querySelectorAll("bdi").length).toBeGreaterThanOrEqual(3);
  });
});

describe("[UX06-R2b WRONG-RECOVERY-STEPS] the recovery step fits what actually failed", () => {
  test("a 403-restricted list load never advises retrying the same request", async () => {
    enqueue("GET", LIST_URL, fail(403, { detail: "final_suite_content_restricted" }));
    renderUI();
    fireEvent.click(screen.getByTestId("load-cases-button"));
    await waitFor(() => expect(screen.getByTestId("list-error")).toBeTruthy());
    const text = screen.getByTestId("list-error").textContent ?? "";
    expect(text).toContain("Ask an evaluator or administrator for access.");
    expect(text).not.toContain("Press Load cases again.");
  });

  test("a save-time network failure never claims a field needs fixing", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw new TypeError("Failed to fetch");
    });
    setField("field-language", "fr");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-error")).toBeTruthy());
    const text = screen.getByTestId("edit-error").textContent ?? "";
    expect(text).toContain("Try again.");
    expect(text).not.toContain("Fix the field shown above");
  });

  test("a malformed-200 save response never claims a field needs fixing", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    }));
    setField("field-language", "fr");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-error")).toBeTruthy());
    const text = screen.getByTestId("edit-error").textContent ?? "";
    expect(text).toContain("Try again.");
    expect(text).not.toContain("Fix the field shown above");
  });

  test("NEGATIVE: a genuine field-validation save failure still advises fixing the field", async () => {
    renderUI();
    await loadCases([evalCase(CASE_A, "a-case")]);
    await openEditor(CASE_A);
    enqueue(
      "PATCH",
      `${LIST_URL}/${CASE_A}`,
      fail(400, {
        detail: "validation_failed",
        errors: [{ index: null, field: "language", code: "unsupported" }],
      }),
    );
    setField("field-language", "zz");
    fireEvent.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(screen.getByTestId("edit-error")).toBeTruthy());
    expect(screen.getByTestId("edit-error").textContent).toContain(
      "Fix the field shown above, then save again.",
    );
  });
});
