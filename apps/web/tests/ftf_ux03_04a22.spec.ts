// @vitest-environment jsdom
//
// FTF-UX03 round 2 -- FTF-04A22 mandate editor, rebuilt on the UX01 components (QUEUE 0g).
// Pins the return points that apply to this component: bilingual render with lang/dir on every
// state, announced + recoverable errors, loading and empty states with a real action, status shown
// by words + icon (never a dot or a raw code), the discard-edits path, the generated strict-parse
// table over every response field (UI CHECKLIST a), one binding function with a fault per field
// (b), identity reset (c), no simulated success (d) and typed retryable errors (e).
// Each pin was run RED against a mutated component first (see the submission's fault table).
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, test, vi } from "vitest";

import { failureMessage } from "../components/target-application/common";
import { manifestUrl } from "../components/target-application/manifest_review_edit_ui_with_per_field_evidence";
import {
  MANDATE_LIST_FIELDS,
  MANDATE_NUMBER_FIELDS,
  MANDATE_TEXT_FIELDS,
  MandateEditorAndConflictResolutionUI,
  bindManifest,
  bindSavedManifest,
  conflictSentence,
  parseRequirements,
  sameJson,
} from "../components/target-application/mandate_editor_and_conflict_resolution_ui";
import {
  MANDATE_STRINGS,
  MANDATE_STRING_KEYS,
  mandateT,
} from "../components/target-application/strings_04a22";
import { requirementsUrl } from "../components/project-mandate/requirements_ui";

const API_BASE = "http://localhost:8000";
const ORG = "11111111-1111-1111-1111-111111111111";
const PROJECT = "22222222-2222-2222-2222-222222222222";
const MANIFEST = "33333333-3333-3333-3333-333333333333";
const OTHER_MANIFEST = "55555555-5555-5555-5555-555555555555";
const ALL_FIELDS = [...MANDATE_TEXT_FIELDS, ...MANDATE_LIST_FIELDS, ...MANDATE_NUMBER_FIELDS];
const ARABIC = /[؀-ۿ]/;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Json = Record<string, unknown>;

function manifestBody(overrides: Json = {}): Json {
  return {
    id: MANIFEST,
    organization_id: ORG,
    project_id: PROJECT,
    version: 1,
    status: "draft",
    agent_name: "support-agent",
    mandate_json: null,
    forbidden_actions_json: null,
    manifest_sha256: null,
    ...overrides,
  };
}

function requirementRow(overrides: Json = {}): Json {
  return {
    tool_definition_id: "tool-1",
    name: "refund",
    side_effect_class: "critical",
    is_critical: true,
    requires_confirmation: true,
    measurable: true,
    runtime_enforced: true,
    ...overrides,
  };
}

function reply(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

interface Serving {
  manifests?: Record<string, Json>;
  requirements?: unknown;
  patch?: (body: Json, id: string) => ReturnType<typeof reply> | Promise<ReturnType<typeof reply>>;
  requirementsReply?: () => ReturnType<typeof reply> | Promise<ReturnType<typeof reply>>;
}

/** A URL-routed fake of the two real routes; `calls` records every request. */
function serve(config: Serving = {}) {
  const manifests = config.manifests ?? { [MANIFEST]: manifestBody() };
  const calls: { method: string; path: string; body?: Json }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(url);
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(init.body as string) as Json) : undefined;
      calls.push({ method, path: parsed.pathname, body });
      if (parsed.pathname === new URL(requirementsUrl(API_BASE, ORG, PROJECT)).pathname) {
        return config.requirementsReply
          ? config.requirementsReply()
          : reply(200, "requirements" in config ? config.requirements : []);
      }
      const id = parsed.pathname.split("/").pop() as string;
      if (method === "PATCH") {
        if (config.patch) return config.patch(body as Json, id);
        const next = { ...manifests[id], ...(body as Json) };
        manifests[id] = next;
        return reply(200, next);
      }
      const found = manifests[id];
      return found ? reply(200, found) : reply(404, { detail: "manifest not found" });
    }),
  );
  return { calls, manifests };
}

function mount(locale?: "en" | "ar", manifestId = MANIFEST) {
  return render(
    createElement(MandateEditorAndConflictResolutionUI, {
      apiBaseUrl: API_BASE,
      organizationId: ORG,
      projectId: PROJECT,
      manifestId,
      locale,
    }),
  );
}

async function loaded(locale?: "en" | "ar") {
  const view = mount(locale);
  await waitFor(() => expect(screen.getByTestId("mandate-fields-section")).toBeTruthy());
  return view;
}

function type(testId: string, value: string) {
  fireEvent.change(screen.getByTestId(testId), { target: { value } });
}

/** What a reader sees: the component's text with user data (inside <bdi>) and the technical tokens "JSON", "en" and "ar" (language codes) taken out. */
function systemText(): string {
  const root = screen.getByTestId("mandate-editor-ui").cloneNode(true) as HTMLElement;
  root.querySelectorAll("bdi").forEach((node) => node.remove());
  return (root.textContent ?? "").replace(/JSON|\ben\b|\bar\b/g, "");
}

function conflictFixture() {
  return {
    manifests: {
      [MANIFEST]: manifestBody({
        forbidden_actions_json: {
          actions: [
            { name: "refund", reason: "finance policy" },
            { name: "export_data", reason: "" },
            { name: "ghost_tool", reason: "unknown" },
            { name: "delete_user", reason: "irreversible" },
          ],
        },
      }),
    },
    requirements: [
      requirementRow({ name: "refund", runtime_enforced: false }),
      requirementRow({
        name: "export_data",
        is_critical: false,
        requires_confirmation: false,
      }),
      requirementRow({ name: "delete_user", runtime_enforced: true }),
    ],
  };
}

// -- catalogue ---------------------------------------------------------------------------------

describe("[UX03-04A22-CATALOGUE] the en/ar catalogue is complete", () => {
  test("every string has English and Arabic text with the same placeholders", () => {
    for (const key of MANDATE_STRING_KEYS) {
      const entry = MANDATE_STRINGS[key];
      expect(entry.en.trim().length, `${key} en`).toBeGreaterThan(0);
      expect(ARABIC.test(entry.ar), `${key} ar has no Arabic`).toBe(true);
      const holes = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join(",");
      expect(holes(entry.ar), `${key} placeholders`).toBe(holes(entry.en));
    }
  });

  test("every field label is a human phrase, never the snake_case key", () => {
    for (const field of ALL_FIELDS) {
      expect(mandateT(field, "en")).not.toContain("_");
      expect(mandateT(field, "ar")).not.toContain("_");
    }
  });
});

// -- bilingual, lang/dir on every state --------------------------------------------------------

describe("[UX03-04A22-AR] bilingual render", () => {
  test("populated, Arabic: exact strings, no English system text, lang/dir set", async () => {
    serve(conflictFixture());
    await loaded("ar");
    const root = screen.getByTestId("mandate-editor-ui");
    expect(root.getAttribute("lang")).toBe("ar");
    expect(root.getAttribute("dir")).toBe("rtl");
    expect(screen.getByRole("heading", { name: "التفويض" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "الإجراءات المحظورة والتعارضات" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "جاهزية القفل" })).toBeTruthy();
    expect(screen.getByTestId("refresh-enforcement-button").textContent).toBe("تحديث حالة التطبيق");
    expect(screen.getByTestId("forbidden-action-add-button").textContent).toBe("إضافة إجراء محظور");
    expect(systemText().match(/[A-Za-z]{2,}/g)).toBeNull();
  });

  test("every field has its exact Arabic label above the input", async () => {
    serve();
    await loaded("ar");
    for (const field of ALL_FIELDS) {
      const label = document.querySelector(`label[for="mandate-${field}"]`);
      expect(label, field).toBeTruthy();
      expect(label!.textContent).toContain(mandateT(field, "ar"));
      expect(label!.textContent).toContain("(اختياري)");
    }
  });

  test("the remove button's accessible name is the exact Arabic phrase with the action name", async () => {
    serve(conflictFixture());
    await loaded("ar");
    expect(screen.getByTestId("forbidden-action-remove-refund").getAttribute("aria-label")).toBe(
      mandateT("removeActionNamed", "ar", { name: "refund" }),
    );
  });

  test("loading, load error, read-only and the add-form error carry lang + dir too", async () => {
    // loading
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    const loading = mount("ar");
    expect(screen.getByTestId("mandate-editor-ui").getAttribute("lang")).toBe("ar");
    expect(screen.getByTestId("mandate-editor-ui").getAttribute("dir")).toBe("rtl");
    expect(screen.getByTestId("mandate-loading").textContent).toBe("جارٍ تحميل التفويض…");
    expect(systemText().match(/[A-Za-z]{2,}/g)).toBeNull();
    loading.unmount();

    // load error
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new Error("down"))),
    );
    const failed = mount("ar");
    await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
    expect(screen.getByTestId("mandate-editor-ui").getAttribute("lang")).toBe("ar");
    expect(screen.getByTestId("mandate-editor-ui").getAttribute("dir")).toBe("rtl");
    expect(screen.getByTestId("mandate-load-error").textContent).toContain(
      failureMessage({ kind: "network" }, "ar"),
    );
    expect(screen.getByTestId("mandate-load-retry").textContent).toBe("إعادة المحاولة");
    expect(systemText().match(/[A-Za-z]{2,}/g)).toBeNull();
    failed.unmount();

    // read-only
    serve({ manifests: { [MANIFEST]: manifestBody({ status: "locked" }) } });
    mount("ar");
    await waitFor(() => expect(screen.getByTestId("mandate-readonly-notice")).toBeTruthy());
    expect(screen.getByTestId("mandate-readonly-notice").textContent).toContain(
      mandateT("readOnly", "ar", { status: mandateT("statusLocked", "ar") }),
    );
    expect(systemText().match(/[A-Za-z]{2,}/g)).toBeNull();
  });

  test("a locale we do not ship renders English with lang=en, never lang=fr", async () => {
    serve();
    const view = render(
      createElement(MandateEditorAndConflictResolutionUI, {
        apiBaseUrl: API_BASE,
        organizationId: ORG,
        projectId: PROJECT,
        manifestId: MANIFEST,
        locale: "fr" as unknown as "en",
      }),
    );
    await waitFor(() => expect(screen.getByTestId("mandate-fields-section")).toBeTruthy());
    const root = view.getByTestId("mandate-editor-ui");
    expect(root.getAttribute("lang")).toBe("en");
    expect(root.getAttribute("dir")).toBe("ltr");
    expect(ARABIC.test(systemText())).toBe(false);
  });

  test("user data stays in <bdi> so an English action name does not reorder an Arabic line", async () => {
    serve(conflictFixture());
    await loaded("ar");
    const name = screen.getByTestId("forbidden-action-name-refund");
    expect(name.querySelector("bdi")?.textContent).toBe("refund");
    const blockers = screen.getByTestId("mandate-lock-blockers");
    expect(Array.from(blockers.querySelectorAll("bdi")).map((n) => n.textContent)).toEqual([
      "refund",
      "export_data",
    ]);
  });
});

// -- human words, text + icon ------------------------------------------------------------------

describe("[UX03-04A22-WORDS] status is words + icon, never a raw code or a dot alone", () => {
  for (const locale of ["en", "ar"] as const) {
    test(`no snake_case reason code or label is visible (${locale})`, async () => {
      serve(conflictFixture());
      await loaded(locale);
      const text = systemText();
      for (const code of [
        "not_runtime_enforced",
        "not_declared_critical",
        "not_found",
        "review_required",
      ]) {
        expect(text, code).not.toContain(code);
      }
      expect(text).not.toMatch(/[a-z]+_[a-z_]+/);
      for (const label of Array.from(document.querySelectorAll("label"))) {
        expect(label.textContent).not.toContain("_");
      }
    });
  }

  test("each of the four conflict states shows readable text and an aria-hidden icon", async () => {
    serve(conflictFixture());
    await loaded();
    for (const name of ["refund", "export_data", "ghost_tool", "delete_user"]) {
      const status = screen.getByTestId(`forbidden-action-status-${name}`);
      const words = (status.textContent ?? "").replace(/[^A-Za-z ]/g, "").trim();
      expect(words.length, `${name} status words`).toBeGreaterThan(5);
      const icon = status.querySelector('[aria-hidden="true"]');
      expect(icon, `${name} icon`).toBeTruthy();
      expect((icon!.textContent ?? "").trim().length).toBeGreaterThan(0);
    }
  });

  test("the four states say different things, so none is colour-only", async () => {
    serve(conflictFixture());
    await loaded();
    const texts = ["refund", "export_data", "ghost_tool", "delete_user"].map(
      (n) => screen.getByTestId(`forbidden-action-status-${n}`).textContent,
    );
    expect(new Set(texts).size).toBe(4);
  });

  test("the blocking sentences are the localized catalogue text", () => {
    expect(conflictSentence("not_runtime_enforced", "ar")).toBe(
      mandateT("blockerUnenforced", "ar"),
    );
    expect(conflictSentence("not_declared_critical", "en")).toBe(
      mandateT("blockerUndeclared", "en"),
    );
    expect(conflictSentence("enforced")).toBe("");
    expect(conflictSentence("not_found")).toBe("");
  });

  test("the reason entered with a forbidden action is shown, and a missing one says so", async () => {
    serve(conflictFixture());
    await loaded();
    const row = screen.getByTestId("forbidden-action-row-refund");
    const bdis = Array.from(row.querySelectorAll("bdi")).map((n) => n.textContent);
    expect(bdis).toEqual(["refund", "finance policy"]);
    expect(screen.getByTestId("forbidden-action-row-export_data").textContent).toContain(
      mandateT("noReason", "en"),
    );
  });
});

// -- empty state --------------------------------------------------------------------------------

describe("[UX03-04A22-EMPTY] empty state with a real action", () => {
  test("Arabic empty state: exact title and explanation, and a Button that moves focus to the name field", async () => {
    serve();
    await loaded("ar");
    expect(
      screen.getByRole("heading", { name: mandateT("forbiddenEmptyTitle", "ar") }),
    ).toBeTruthy();
    expect(screen.getByText(mandateT("forbiddenEmptyWhy", "ar"))).toBeTruthy();
    const action = screen.getByTestId("forbidden-empty-add-action");
    expect(action.tagName).toBe("BUTTON");
    expect(action.textContent).toBe(mandateT("forbiddenEmptyAction", "ar"));
    fireEvent.click(action);
    expect(document.activeElement).toBe(screen.getByTestId("new-forbidden-action-name"));
  });

  test("English empty state uses the English title and has the action", async () => {
    serve();
    await loaded();
    expect(screen.getByRole("heading", { name: "No forbidden actions declared" })).toBeTruthy();
    expect(screen.getByTestId("forbidden-empty-add-action")).toBeTruthy();
  });

  test("never shown while loading", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    mount();
    expect(screen.queryByTestId("forbidden-empty-add-action")).toBeNull();
    expect(screen.queryByText(mandateT("forbiddenEmptyTitle", "en"))).toBeNull();
    expect(screen.getByTestId("mandate-editor-ui").getAttribute("aria-busy")).toBe("true");
  });

  test("never shown after a load error (a failed load is not 'nothing declared')", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => reply(503, { detail: "down" })),
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
    expect(screen.queryByText(mandateT("forbiddenEmptyTitle", "en"))).toBeNull();
    expect(screen.queryByTestId("mandate-lock-ready")).toBeNull();
  });

  test("not shown when there are actions; read-only shows a plain note and no action button", async () => {
    serve(conflictFixture());
    await loaded();
    expect(screen.queryByTestId("forbidden-empty-add-action")).toBeNull();
    cleanup();
    serve({ manifests: { [MANIFEST]: manifestBody({ status: "locked" }) } });
    await loaded();
    expect(screen.queryByTestId("forbidden-empty-add-action")).toBeNull();
    expect(screen.getByTestId("forbidden-empty-readonly").textContent).toBe(
      mandateT("forbiddenEmptyReadOnly", "en"),
    );
  });
});

// -- loading ------------------------------------------------------------------------------------

describe("[UX03-04A22-LOADING] busy states are announced", () => {
  test("saving: aria-busy, a 'Saving…' button that is disabled, and the editor stays mounted", async () => {
    let release: (value: ReturnType<typeof reply>) => void = () => undefined;
    let sentBody: Json = {};
    serve({
      patch: (body) =>
        new Promise((resolve) => {
          sentBody = body;
          release = resolve;
        }),
    });
    await loaded();
    type("mandate-field-business_purpose", "x");
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    await waitFor(() =>
      expect(screen.getByTestId("mandate-editor-ui").getAttribute("aria-busy")).toBe("true"),
    );
    const save = screen.getByTestId("mandate-save-button") as HTMLButtonElement;
    expect(save.textContent).toBe(mandateT("saving", "en"));
    expect(save.disabled).toBe(true);
    release(reply(200, manifestBody(sentBody)));
    await waitFor(() => expect(screen.getByTestId("mandate-saved-notice")).toBeTruthy());
    expect(screen.getByTestId("mandate-editor-ui").getAttribute("aria-busy")).toBeNull();
  });

  test("refreshing enforcement: the button says so and the page is aria-busy", async () => {
    let release: (value: ReturnType<typeof reply>) => void = () => undefined;
    let first = true;
    serve({
      requirementsReply: () => {
        if (first) {
          first = false;
          return reply(200, []);
        }
        return new Promise((resolve) => {
          release = resolve;
        });
      },
    });
    await loaded();
    fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
    await waitFor(() =>
      expect(screen.getByTestId("refresh-enforcement-button").textContent).toBe(
        mandateT("refreshing", "en"),
      ),
    );
    expect(screen.getByTestId("mandate-editor-ui").getAttribute("aria-busy")).toBe("true");
    release(reply(200, []));
    await waitFor(() =>
      expect(screen.getByTestId("refresh-enforcement-button").textContent).toBe(
        mandateT("refresh", "en"),
      ),
    );
  });
});

// -- targets and labels -------------------------------------------------------------------------

describe("[UX03-04A22-TARGET] every control is a UX01 control", () => {
  test("every button has the 24 px minimum target from the shared Button", async () => {
    serve(conflictFixture());
    await loaded();
    fireEvent.click(screen.getByTestId("forbidden-action-remove-refund"));
    const buttons = Array.from(document.querySelectorAll("button"));
    expect(buttons.length).toBeGreaterThan(4);
    for (const button of buttons) {
      expect(button.getAttribute("data-variant"), button.textContent ?? "").toBeTruthy();
      expect((button as HTMLElement).style.minBlockSize).toBe("var(--target-min)");
    }
  });

  test("every field has a visible label above it and its help wired by aria-describedby", async () => {
    serve();
    await loaded();
    for (const field of ALL_FIELDS) {
      const input = screen.getByTestId(`mandate-field-${field}`);
      const label = document.querySelector(`label[for="mandate-${field}"]`);
      expect(label, field).toBeTruthy();
      expect(label!.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      const described = (input.getAttribute("aria-describedby") ?? "").split(" ");
      expect(
        described.some((id) => document.getElementById(id)?.textContent),
        field,
      ).toBe(true);
    }
  });

  test("a save that is impossible says why instead of silently disabling", async () => {
    serve();
    await loaded();
    const save = screen.getByTestId("mandate-save-button") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByTestId("disabled-reason").textContent).toBe(mandateT("saveNothing", "en"));
    type("mandate-field-business_purpose", "x");
    expect(save.disabled).toBe(false);
    expect(screen.queryByTestId("disabled-reason")).toBeNull();
  });
});

// -- add-form errors ----------------------------------------------------------------------------

describe("[UX03-04A22-ADD] adding a forbidden action reports what is wrong", () => {
  test("an empty name shows an error summary (focused), a field error and aria wiring", async () => {
    serve();
    await loaded();
    fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
    const message = mandateT("addNameEmpty", "en");
    const summary = screen.getByRole("alert", { name: /problem/i });
    await waitFor(() => expect(document.activeElement).toBe(summary));
    expect(summary.textContent).toContain(message);
    const input = screen.getByTestId("new-forbidden-action-name");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const described = (input.getAttribute("aria-describedby") ?? "").split(" ");
    expect(described.map((id) => document.getElementById(id)?.textContent)).toContain(message);
    // The summary's link goes to the field.
    fireEvent.click(summary.querySelector("a")!);
    expect(document.activeElement).toBe(input);
  });

  test("a duplicate name says which name, in Arabic too", async () => {
    serve(conflictFixture());
    await loaded("ar");
    type("new-forbidden-action-name", "refund");
    fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
    expect(screen.getAllByText(mandateT("addNameDuplicate", "ar", { name: "refund" })).length).toBe(
      2,
    );
  });

  test("typing clears the error; a valid add clears it too and never leaves a stale summary", async () => {
    serve();
    await loaded();
    fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
    expect(screen.getByTestId("new-forbidden-action-name").getAttribute("aria-invalid")).toBe(
      "true",
    );
    type("new-forbidden-action-name", "refund");
    expect(screen.getByTestId("new-forbidden-action-name").getAttribute("aria-invalid")).toBeNull();
    fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
    expect(screen.queryByRole("alert", { name: /problem/i })).toBeNull();
    expect(screen.getByTestId("forbidden-action-row-refund")).toBeTruthy();
  });

  test("remove buttons are told apart by name", async () => {
    serve(conflictFixture());
    await loaded();
    const names = ["refund", "export_data", "ghost_tool", "delete_user"].map((n) =>
      screen.getByTestId(`forbidden-action-remove-${n}`).getAttribute("aria-label")!,
    );
    expect(new Set(names).size).toBe(4);
    names.forEach((label, i) =>
      expect(label).toBe(
        mandateT("removeActionNamed", "en", {
          name: ["refund", "export_data", "ghost_tool", "delete_user"][i]!,
        }),
      ),
    );
  });
});

// -- discard edits ------------------------------------------------------------------------------

describe("[UX03-04A22-DISCARD] unsaved edits can be thrown away (H3)", () => {
  test("offered only once something changed, confirms in words, and keeps focus sensible", async () => {
    serve({
      manifests: {
        [MANIFEST]: manifestBody({
          mandate_json: { business_purpose: "Saved purpose", supported_languages: ["en"] },
          forbidden_actions_json: { actions: [{ name: "refund", reason: "policy" }] },
        }),
      },
      requirements: [requirementRow()],
    });
    await loaded();
    expect(screen.queryByTestId("mandate-discard-button")).toBeNull();
    type("mandate-field-business_purpose", "Edited");
    type("mandate-field-supported_languages", "en, ar");
    fireEvent.click(screen.getByTestId("forbidden-action-remove-refund"));
    expect(screen.getByTestId("mandate-dirty-notice").textContent).toContain(
      mandateT("unsaved", "en"),
    );

    const trigger = screen.getByTestId("mandate-discard-button");
    fireEvent.click(trigger);
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain(mandateT("discardConfirm", "en"));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId("mandate-discard-keep")),
    );

    // Escape keeps the edits and puts focus back on the trigger.
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(
      (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
    ).toBe("Edited");

    // "Keep editing" also keeps them.
    fireEvent.click(trigger);
    fireEvent.click(screen.getByTestId("mandate-discard-keep"));
    expect(
      (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
    ).toBe("Edited");

    // Confirming returns every field to the saved version.
    fireEvent.click(trigger);
    fireEvent.click(screen.getByTestId("mandate-discard-confirm-button"));
    expect(
      (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
    ).toBe("Saved purpose");
    expect(
      (screen.getByTestId("mandate-field-supported_languages") as HTMLInputElement).value,
    ).toBe("en");
    expect(screen.getByTestId("forbidden-action-row-refund")).toBeTruthy();
    expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId("mandate-discard-button")).toBeNull();
    expect(screen.queryByTestId("mandate-dirty-notice")).toBeNull();
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId("mandate-field-business_purpose")),
    );
  });

  test("discarding sends nothing to the server", async () => {
    const { calls } = serve();
    await loaded();
    type("mandate-field-business_purpose", "x");
    fireEvent.click(screen.getByTestId("mandate-discard-button"));
    fireEvent.click(screen.getByTestId("mandate-discard-confirm-button"));
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  test("the confirmation is Arabic exactly in Arabic", async () => {
    serve();
    await loaded("ar");
    type("mandate-field-business_purpose", "x");
    fireEvent.click(screen.getByTestId("mandate-discard-button"));
    expect(screen.getByRole("alertdialog").textContent).toContain(mandateT("discardConfirm", "ar"));
    expect(screen.getByTestId("mandate-discard-keep").textContent).toBe("متابعة التعديل");
  });

  test("it is not offered on a read-only manifest", async () => {
    serve({ manifests: { [MANIFEST]: manifestBody({ status: "superseded" }) } });
    await loaded();
    expect(screen.queryByTestId("mandate-discard-button")).toBeNull();
  });
});

// -- errors: announced, recoverable, state kept ------------------------------------------------

describe("[UX03-04A22-ERRORS] failures are announced, specific and recoverable", () => {
  const BAD_LOADS: [string, () => unknown][] = [
    ["a rejected fetch", () => Promise.reject(new Error("boom"))],
    [
      "a 500 with a non-JSON body",
      () => ({
        ok: false,
        status: 500,
        json: async () => {
          throw new SyntaxError("x");
        },
      }),
    ],
    ["a 503 with a detail", () => reply(503, { detail: "upstream_down" })],
    ["a 429 with no body", () => reply(429, null)],
    ["a 200 with {nope:true}", () => reply(200, { nope: true })],
    ["a 200 with null", () => reply(200, null)],
    [
      "a 200 whose body cannot be parsed",
      () => ({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError("x");
        },
      }),
    ],
  ];
  for (const [name, make] of BAD_LOADS) {
    test(`load: ${name} -> announced error with a retry, editor not unmounted, retry recovers`, async () => {
      let broken = true;
      const good = serve();
      const real = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<unknown>;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (u: string, i?: RequestInit) => (broken ? make() : real(u, i))),
      );
      mount();
      await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
      expect(screen.getByTestId("mandate-load-error").querySelector('[role="alert"]')).toBeTruthy();
      expect(screen.getByTestId("mandate-editor-ui")).toBeTruthy();
      expect(screen.queryByTestId("mandate-save-button")).toBeNull();
      expect(screen.queryByTestId("mandate-lock-ready")).toBeNull();
      expect(screen.getByTestId("mandate-load-error").textContent!.length).toBeGreaterThan(20);
      broken = false;
      fireEvent.click(screen.getByTestId("mandate-load-retry"));
      await waitFor(() => expect(screen.getByTestId("mandate-fields-section")).toBeTruthy());
      expect(screen.queryByTestId("mandate-load-error")).toBeNull();
      expect(good.calls.length).toBeGreaterThan(0);
    });
  }

  test("a 5xx says to wait and retry; a 4xx with a detail shows the server's reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => reply(502, { detail: "x" })),
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
    expect(screen.getByTestId("mandate-load-error").textContent).toContain(
      failureMessage({ kind: "http", status: 502, detail: "x" }),
    );
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => reply(404, { detail: "manifest not found" })),
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
    expect(screen.getByTestId("mandate-load-error").textContent).toContain("manifest not found");
  });

  const BAD_SAVES: [string, () => unknown][] = [
    ["a rejected fetch", () => Promise.reject(new Error("down"))],
    [
      "a 500 with a non-JSON body",
      () => ({
        ok: false,
        status: 500,
        json: async () => {
          throw new SyntaxError("x");
        },
      }),
    ],
    ["a 409 locked", () => reply(409, { detail: "manifest is locked" })],
    ["a 200 with {nope:true}", () => reply(200, { nope: true })],
    [
      "a 200 with an unreadable body",
      () => ({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError("x");
        },
      }),
    ],
  ];
  for (const [name, make] of BAD_SAVES) {
    test(`save: ${name} -> announced, edits kept, never shown as saved, retry succeeds`, async () => {
      let broken = true;
      serve({
        patch: (body, id) => {
          if (broken) return make() as ReturnType<typeof reply>;
          return reply(200, manifestBody({ id, ...body }));
        },
      });
      await loaded();
      type("mandate-field-business_purpose", "keep me");
      fireEvent.click(screen.getByTestId("mandate-save-button"));
      await waitFor(() => expect(screen.getByTestId("mandate-save-error")).toBeTruthy());
      expect(screen.getByTestId("mandate-save-error").querySelector('[role="alert"]')).toBeTruthy();
      expect(screen.getByTestId("mandate-save-error").textContent).toContain(
        mandateT("saveFailedKept", "en"),
      );
      expect(
        (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
      ).toBe("keep me");
      expect(screen.queryByTestId("mandate-saved-notice")).toBeNull();
      expect(screen.getByTestId("mandate-dirty-notice")).toBeTruthy();
      expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(false);
      broken = false;
      fireEvent.click(screen.getByTestId("mandate-save-button"));
      await waitFor(() => expect(screen.getByTestId("mandate-saved-notice")).toBeTruthy());
      expect(screen.queryByTestId("mandate-save-error")).toBeNull();
    });
  }

  const BAD_REFRESHES: [string, () => unknown][] = [
    ["a rejected fetch", () => Promise.reject(new Error("offline"))],
    [
      "a 500 with a non-JSON body",
      () => ({
        ok: false,
        status: 500,
        json: async () => {
          throw new SyntaxError("x");
        },
      }),
    ],
    ["a 429 with no body", () => reply(429, null)],
    ["a 200 with {nope:true}", () => reply(200, { nope: true })],
    [
      "a 200 list with a mistyped row",
      () => reply(200, [requirementRow({ runtime_enforced: "yes" })]),
    ],
  ];
  for (const [name, make] of BAD_REFRESHES) {
    test(`refresh: ${name} -> announced, the last known conflict stays, retry can clear it`, async () => {
      let broken = false;
      serve({
        manifests: {
          [MANIFEST]: manifestBody({
            forbidden_actions_json: { actions: [{ name: "refund", reason: "" }] },
          }),
        },
        requirementsReply: () =>
          broken
            ? (make() as ReturnType<typeof reply>)
            : reply(200, [requirementRow({ runtime_enforced: true })]),
      });
      await loaded();
      expect(screen.getByTestId("mandate-lock-ready")).toBeTruthy();
      broken = true;
      fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
      await waitFor(() => expect(screen.getByTestId("refresh-enforcement-error")).toBeTruthy());
      expect(
        screen.getByTestId("refresh-enforcement-error").querySelector('[role="alert"]'),
      ).toBeTruthy();
      expect(screen.getByTestId("refresh-enforcement-error").textContent).toContain(
        mandateT("refreshStale", "en"),
      );
      // The error never clears or invents a conflict.
      expect(screen.getByTestId("mandate-lock-ready")).toBeTruthy();
      broken = false;
      fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
      await waitFor(() => expect(screen.queryByTestId("refresh-enforcement-error")).toBeNull());
    });
  }
});

// -- strict parse table (UI CHECKLIST a) --------------------------------------------------------

describe("[UX03-04A22-STRICT] every response field is parsed strictly", () => {
  const WRONG: [string, unknown][] = [
    ["a number", 7],
    ["a string", "x"],
    ["a boolean", true],
    ["an array", []],
    ["an object", {}],
    ["null", null],
    ["missing", undefined],
  ];
  /** Per manifest field: the values the contract forbids. `mandate_json`/`forbidden_actions_json`
   * may be an object or null; `status` is one of four words. */
  const MANIFEST_FAULTS: Record<string, [string, unknown][]> = {
    id: [...WRONG, ["another manifest's id", OTHER_MANIFEST]],
    project_id: [...WRONG, ["another project's id", "99999999-9999-9999-9999-999999999999"]],
    version: [
      ["a string", "1"],
      ["a float", 1.5],
      ["null", null],
      ["an object", {}],
      ["missing", undefined],
    ],
    status: [
      ["a number", 3],
      ["null", null],
      ["an object", {}],
      ["missing", undefined],
      ["a word outside the enum", "approved"],
      ["the wrong case", "Draft"],
      ["empty", ""],
    ],
    mandate_json: [
      ["a string", "x"],
      ["a number", 3],
      ["an array", []],
      ["missing", undefined],
    ],
    forbidden_actions_json: [
      ["a string", "x"],
      ["a number", 3],
      ["an array", [{ name: "refund" }]],
      ["missing", undefined],
    ],
  };

  function withField(field: string, value: unknown): Json {
    const body = manifestBody();
    if (value === undefined) delete body[field];
    else body[field] = value;
    return body;
  }

  for (const [field, faults] of Object.entries(MANIFEST_FAULTS)) {
    for (const [label, value] of faults) {
      test(`load: manifest.${field} = ${label} -> malformed, no fields, no save, no add form`, async () => {
        serve({ manifests: { [MANIFEST]: withField(field, value) } });
        mount();
        await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
        expect(screen.getByTestId("mandate-load-error").textContent).toContain(
          failureMessage({ kind: "malformed" }),
        );
        expect(screen.queryByTestId("mandate-fields-section")).toBeNull();
        expect(screen.queryByTestId("mandate-save-button")).toBeNull();
        expect(screen.queryByTestId("forbidden-action-add-form")).toBeNull();
        expect(screen.queryByTestId("mandate-lock-ready")).toBeNull();
      });

      test(`save: PATCH 200 with ${field} = ${label} -> malformed, edits kept, never 'saved'`, async () => {
        serve({ patch: () => reply(200, withField(field, value)) });
        await loaded();
        type("mandate-field-business_purpose", "kept");
        fireEvent.click(screen.getByTestId("mandate-save-button"));
        await waitFor(() => expect(screen.getByTestId("mandate-save-error")).toBeTruthy());
        expect(screen.getByTestId("mandate-save-error").textContent).toContain(
          failureMessage({ kind: "malformed" }),
        );
        expect(screen.queryByTestId("mandate-saved-notice")).toBeNull();
        expect(
          (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
        ).toBe("kept");
      });
    }
  }

  const ROW_FIELDS: [string, "string" | "boolean"][] = [
    ["tool_definition_id", "string"],
    ["name", "string"],
    ["side_effect_class", "string"],
    ["is_critical", "boolean"],
    ["requires_confirmation", "boolean"],
    ["measurable", "boolean"],
    ["runtime_enforced", "boolean"],
  ];
  for (const [field, kind] of ROW_FIELDS) {
    const bad: [string, unknown][] =
      kind === "string"
        ? [
            ["a number", 5],
            ["null", null],
            ["an object", {}],
            ["missing", undefined],
          ]
        : [
            ["a string", "true"],
            ["a number", 1],
            ["null", null],
            ["missing", undefined],
          ];
    for (const [label, value] of bad) {
      test(`load: requirements[].${field} = ${label} -> whole list refused, no false 'no conflicts'`, async () => {
        const row = requirementRow();
        if (value === undefined) delete row[field];
        else row[field] = value;
        serve({
          manifests: {
            [MANIFEST]: manifestBody({
              forbidden_actions_json: { actions: [{ name: "refund", reason: "" }] },
            }),
          },
          requirements: [requirementRow({ name: "other" }), row],
        });
        mount();
        await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
        expect(screen.queryByTestId("mandate-lock-ready")).toBeNull();
        expect(screen.queryByTestId("mandate-save-button")).toBeNull();
      });
    }
  }

  for (const [label, value] of [
    ["an object", {}],
    ["null", null],
    ["a string", "[]"],
    ["a row that is not an object", [3]],
    ["a row that is null", [null]],
    ["a row that is an array", [[]]],
  ] as [string, unknown][]) {
    test(`load: requirements = ${label} -> malformed`, async () => {
      serve({ requirements: value });
      mount();
      await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
      expect(screen.queryByTestId("forbidden-actions-section")).toBeNull();
    });
  }

  test("pure: parseRequirements accepts a typed list and refuses anything else", () => {
    expect(parseRequirements([requirementRow()])).toHaveLength(1);
    expect(parseRequirements([])).toEqual([]);
    expect(parseRequirements({})).toBeNull();
    expect(parseRequirements([{ name: "x" }])).toBeNull();
  });

  test("an unknown manifest status the API might add later is refused, not shown as editable", () => {
    expect(
      bindManifest(manifestBody({ status: "archived" }), {
        manifestId: MANIFEST,
        projectId: PROJECT,
      }),
    ).toBeNull();
  });
});

// -- binding (UI CHECKLIST b): one function, a fault per field ----------------------------------

describe("[UX03-04A22-BIND] bindManifest compares every field with what was asked for", () => {
  const ids = { manifestId: MANIFEST, projectId: PROJECT };
  test("the happy path binds", () => {
    expect(bindManifest(manifestBody(), ids)).not.toBeNull();
    expect(
      bindManifest(
        manifestBody({ mandate_json: { a: 1 }, forbidden_actions_json: { actions: [] } }),
        ids,
      ),
    ).not.toBeNull();
  });
  const FAULTS: [string, Json][] = [
    ["id", manifestBody({ id: OTHER_MANIFEST })],
    ["project_id", manifestBody({ project_id: "9" })],
    ["version", manifestBody({ version: "1" })],
    ["status", manifestBody({ status: "approved" })],
    ["mandate_json", manifestBody({ mandate_json: [] })],
    ["forbidden_actions_json", manifestBody({ forbidden_actions_json: "x" })],
  ];
  for (const [field, body] of FAULTS) {
    test(`a body whose ${field} differs from the request is refused`, () => {
      expect(bindManifest(body, ids)).toBeNull();
    });
  }
  test("a non-object body is refused", () => {
    for (const body of [null, undefined, 3, "x", []]) {
      expect(bindManifest(body, ids)).toBeNull();
    }
  });
});

// -- identity (UI CHECKLIST c) -------------------------------------------------------------------

describe("[UX03-04A22-IDENTITY] a different manifest never shows the previous one's data or edits", () => {
  test("changing manifestId drops drafts, errors and the previous manifest", async () => {
    serve({
      manifests: {
        [MANIFEST]: manifestBody({ mandate_json: { business_purpose: "FIRST" } }),
        [OTHER_MANIFEST]: manifestBody({
          id: OTHER_MANIFEST,
          mandate_json: { business_purpose: "SECOND" },
        }),
      },
      patch: () => reply(409, { detail: "manifest is locked" }),
    });
    const view = await loaded();
    type("mandate-field-business_purpose", "unsaved edit");
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    await waitFor(() => expect(screen.getByTestId("mandate-save-error")).toBeTruthy());
    view.rerender(
      createElement(MandateEditorAndConflictResolutionUI, {
        apiBaseUrl: API_BASE,
        organizationId: ORG,
        projectId: PROJECT,
        manifestId: OTHER_MANIFEST,
      }),
    );
    await waitFor(() =>
      expect(
        (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
      ).toBe("SECOND"),
    );
    expect(screen.queryByTestId("mandate-save-error")).toBeNull();
    expect(screen.queryByTestId("mandate-dirty-notice")).toBeNull();
    expect((screen.getByTestId("mandate-save-button") as HTMLButtonElement).disabled).toBe(true);
  });

  test("a save that finishes after the manifest changed does not touch the new manifest", async () => {
    let release: (value: ReturnType<typeof reply>) => void = () => undefined;
    serve({
      manifests: {
        [MANIFEST]: manifestBody({ mandate_json: { business_purpose: "FIRST" } }),
        [OTHER_MANIFEST]: manifestBody({
          id: OTHER_MANIFEST,
          mandate_json: { business_purpose: "SECOND" },
        }),
      },
      patch: () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    });
    const view = await loaded();
    type("mandate-field-business_purpose", "edit");
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    await waitFor(() =>
      expect(screen.getByTestId("mandate-save-button").textContent).toBe("Saving…"),
    );
    view.rerender(
      createElement(MandateEditorAndConflictResolutionUI, {
        apiBaseUrl: API_BASE,
        organizationId: ORG,
        projectId: PROJECT,
        manifestId: OTHER_MANIFEST,
      }),
    );
    await waitFor(() =>
      expect(
        (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
      ).toBe("SECOND"),
    );
    release(reply(200, manifestBody({ mandate_json: { business_purpose: "edit" } })));
    await new Promise((r) => setTimeout(r, 20));
    expect(
      (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
    ).toBe("SECOND");
    expect(screen.queryByTestId("mandate-saved-notice")).toBeNull();
  });

  test("the manifest URL used is the one for the current props", () => {
    expect(manifestUrl(API_BASE, ORG, PROJECT, OTHER_MANIFEST)).toContain(OTHER_MANIFEST);
  });
});

// -- (d) no simulated success -------------------------------------------------------------------

describe("[UX03-04A22-NOFAKE] success is a typed 2xx with a bound body", () => {
  test("a 204 or an empty 200 is not 'saved'", async () => {
    for (const make of [
      () => ({
        ok: true,
        status: 204,
        json: async () => {
          throw new SyntaxError("empty");
        },
      }),
      () => reply(200, {}),
    ]) {
      serve({ patch: make as never });
      await loaded();
      type("mandate-field-business_purpose", "x");
      fireEvent.click(screen.getByTestId("mandate-save-button"));
      await waitFor(() => expect(screen.getByTestId("mandate-save-error")).toBeTruthy());
      expect(screen.queryByTestId("mandate-saved-notice")).toBeNull();
      cleanup();
    }
  });

  test("a good save shows the server's manifest and says so in words", async () => {
    serve();
    await loaded("ar");
    type("mandate-field-business_purpose", "x");
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    await waitFor(() => expect(screen.getByTestId("mandate-saved-notice")).toBeTruthy());
    expect(screen.getByTestId("mandate-saved-notice").textContent).toContain(
      mandateT("saved", "ar"),
    );
  });
});

// -- structural accessibility (jsdom cannot measure contrast or target size: the real-browser run does)

describe("[UX03-04A22-AXE] structural a11y rules hold in every state", () => {
  async function violations(): Promise<string[]> {
    const result = await axe.run(document.body, {
      // "region" is a page-shell rule (landmarks belong to FTF-H029); contrast and target size need a
      // real layout engine and run in the real-browser harness.
      rules: {
        "color-contrast": { enabled: false },
        "target-size": { enabled: false },
        region: { enabled: false },
      },
    });
    return result.violations.map(
      (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
    );
  }

  for (const locale of ["en", "ar"] as const) {
    test(`populated with conflicts, an add-form error and the discard confirmation (${locale})`, async () => {
      serve(conflictFixture());
      await loaded(locale);
      fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
      type("mandate-field-business_purpose", "x");
      fireEvent.click(screen.getByTestId("mandate-discard-button"));
      expect(await violations()).toEqual([]);
    });

    test(`empty, read-only and load-error states (${locale})`, async () => {
      serve();
      mount(locale);
      await waitFor(() => expect(screen.getByTestId("mandate-fields-section")).toBeTruthy());
      expect(await violations()).toEqual([]);
      cleanup();
      serve({ manifests: { [MANIFEST]: manifestBody({ status: "locked" }) } });
      mount(locale);
      await waitFor(() => expect(screen.getByTestId("mandate-readonly-notice")).toBeTruthy());
      expect(await violations()).toEqual([]);
      cleanup();
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => Promise.reject(new Error("x"))),
      );
      mount(locale);
      await waitFor(() => expect(screen.getByTestId("mandate-load-error")).toBeTruthy());
      expect(await violations()).toEqual([]);
    });
  }
});

// -- UX03 r2 review: a save is bound to what was sent; stale refresh; unreadable bodies; locked while saving

describe("[UX03-04A22-R2-SAVEBIND] a save reply must say what was sent", () => {
  const ids = { manifestId: MANIFEST, projectId: PROJECT };
  const sent = {
    mandate_json: { business_purpose: "new", tags: ["a", { k: 1, j: 2 }] },
    forbidden_actions_json: { actions: [{ name: "refund", reason: "policy" }] },
  };
  const echo = () => manifestBody({ ...sent });

  test("an exact echo binds, regardless of object key order", () => {
    expect(bindSavedManifest(echo(), ids, sent, "draft")).not.toBeNull();
    expect(
      bindSavedManifest(
        manifestBody({
          mandate_json: { tags: ["a", { j: 2, k: 1 }], business_purpose: "new" },
          forbidden_actions_json: { actions: [{ reason: "policy", name: "refund" }] },
        }),
        ids,
        sent,
        "draft",
      ),
    ).not.toBeNull();
  });

  const FAULTS: [string, Json][] = [
    [
      "mandate_json (old value kept)",
      manifestBody({ ...sent, mandate_json: { business_purpose: "old" } }),
    ],
    ["mandate_json (null)", manifestBody({ ...sent, mandate_json: null })],
    [
      "mandate_json (array order)",
      manifestBody({
        ...sent,
        mandate_json: { business_purpose: "new", tags: [{ k: 1, j: 2 }, "a"] },
      }),
    ],
    [
      "mandate_json (key dropped)",
      manifestBody({ ...sent, mandate_json: { business_purpose: "new" } }),
    ],
    [
      "mandate_json (extra key)",
      manifestBody({ ...sent, mandate_json: { ...sent.mandate_json, extra: 1 } }),
    ],
    [
      "forbidden_actions_json (old value kept)",
      manifestBody({ ...sent, forbidden_actions_json: null }),
    ],
    [
      "forbidden_actions_json (reason changed)",
      manifestBody({
        ...sent,
        forbidden_actions_json: { actions: [{ name: "refund", reason: "other" }] },
      }),
    ],
    ["status (flipped to locked)", manifestBody({ ...sent, status: "locked" })],
    ["status (flipped to review_required)", manifestBody({ ...sent, status: "review_required" })],
    ["id", manifestBody({ ...sent, id: OTHER_MANIFEST })],
    ["project_id", manifestBody({ ...sent, project_id: "9" })],
  ];
  for (const [field, body] of FAULTS) {
    test(`a reply whose ${field} differs from the request is refused`, () => {
      expect(bindSavedManifest(body, ids, sent, "draft")).toBeNull();
    });
  }
  test("sameJson distinguishes types, lengths and keys", () => {
    expect(sameJson({ a: 1 }, { a: "1" })).toBe(false);
    expect(sameJson([1], [1, 2])).toBe(false);
    expect(sameJson({ a: 1 }, { b: 1 })).toBe(false);
    expect(sameJson({ a: 1 }, { a: 1, b: 2 })).toBe(false);
    expect(sameJson({ a: 1, b: 2 }, { a: 1 })).toBe(false);
    expect(sameJson([], {})).toBe(false);
    expect(sameJson(null, {})).toBe(false);
    expect(sameJson({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toBe(true);
  });

  test("UI: a 200 that kept the OLD mandate is never 'saved' and the draft stays", async () => {
    serve({
      manifests: { [MANIFEST]: manifestBody({ mandate_json: { business_purpose: "old" } }) },
      patch: (body) =>
        reply(200, manifestBody({ ...body, mandate_json: { business_purpose: "old" } })),
    });
    await loaded();
    type("mandate-field-business_purpose", "edited");
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    const error = await screen.findByTestId("mandate-save-error");
    expect(error.textContent).toContain("cannot read");
    expect(screen.queryByTestId("mandate-saved-notice")).toBeNull();
    expect(
      (screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement).value,
    ).toBe("edited");
    expect(screen.getByTestId("mandate-dirty-notice")).toBeTruthy();
  });

  test("UI: a 200 that kept the OLD forbidden actions is never 'saved' and the new entry stays", async () => {
    serve({ patch: (body) => reply(200, manifestBody({ ...body, forbidden_actions_json: null })) });
    await loaded();
    type("new-forbidden-action-name", "wire_transfer");
    fireEvent.click(screen.getByTestId("forbidden-action-add-button"));
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    await screen.findByTestId("mandate-save-error");
    expect(screen.queryByTestId("mandate-saved-notice")).toBeNull();
    expect(screen.getByTestId("forbidden-action-name-wire_transfer")).toBeTruthy();
  });

  test("UI: a 200 that flips the status to locked is refused and the editor stays editable", async () => {
    serve({ patch: (body) => reply(200, manifestBody({ ...body, status: "locked" })) });
    await loaded();
    type("mandate-field-business_purpose", "edited");
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    await screen.findByTestId("mandate-save-error");
    expect(screen.queryByTestId("mandate-saved-notice")).toBeNull();
    expect(screen.queryByTestId("mandate-readonly-notice")).toBeNull();
  });
});

describe("[UX03-04A22-R2-REFRESH-IDENTITY] a stale enforcement refresh never applies to another manifest", () => {
  test("a refresh reply that arrives after the manifest changed is ignored", async () => {
    const forbidden = { actions: [{ name: "refund", reason: "policy" }] };
    let release: (value: ReturnType<typeof reply>) => void = () => undefined;
    let n = 0;
    serve({
      manifests: {
        [MANIFEST]: manifestBody({ forbidden_actions_json: forbidden }),
        [OTHER_MANIFEST]: manifestBody({ id: OTHER_MANIFEST, forbidden_actions_json: forbidden }),
      },
      requirementsReply: () => {
        n += 1;
        if (n === 2) {
          return new Promise((resolve) => {
            release = resolve;
          });
        }
        return reply(200, []);
      },
    });
    const view = await loaded();
    fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
    await waitFor(() => expect(n).toBe(2));
    view.rerender(
      createElement(MandateEditorAndConflictResolutionUI, {
        apiBaseUrl: API_BASE,
        organizationId: ORG,
        projectId: PROJECT,
        manifestId: OTHER_MANIFEST,
      }),
    );
    await waitFor(() => expect(screen.getByTestId("forbidden-action-name-refund")).toBeTruthy());
    await act(async () => {
      release(reply(200, [requirementRow({ name: "refund", runtime_enforced: true })]));
    });
    // The stale reply (enforced refund) must not have been applied to the new manifest.
    expect(screen.queryByTestId("forbidden-action-enforced-refund")).toBeNull();
  });
});

describe("[UX03-04A22-R2-REFRESH-FAILURE-IDENTITY] a stale refresh failure is not shown on another manifest", () => {
  test("a 503 for the old manifest's refresh does not appear after the manifest changed", async () => {
    let release: (value: ReturnType<typeof reply>) => void = () => undefined;
    let n = 0;
    serve({
      manifests: {
        [MANIFEST]: manifestBody(),
        [OTHER_MANIFEST]: manifestBody({ id: OTHER_MANIFEST }),
      },
      requirementsReply: () => {
        n += 1;
        if (n === 2) {
          return new Promise((resolve) => {
            release = resolve;
          });
        }
        return reply(200, []);
      },
    });
    const view = await loaded();
    fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
    await waitFor(() => expect(n).toBe(2));
    view.rerender(
      createElement(MandateEditorAndConflictResolutionUI, {
        apiBaseUrl: API_BASE,
        organizationId: ORG,
        projectId: PROJECT,
        manifestId: OTHER_MANIFEST,
      }),
    );
    await waitFor(() => expect(n).toBe(3));
    await waitFor(() => expect(screen.getByTestId("mandate-fields-section")).toBeTruthy());
    await act(async () => {
      release(reply(503, { detail: "down" }));
    });
    expect(screen.queryByTestId("refresh-enforcement-error")).toBeNull();
  });
});

describe("[UX03-04A22-R2-UNREADABLE] a non-JSON 200 is 'cannot read', not a network failure", () => {
  const unreadable = () => ({
    ok: true,
    status: 200,
    json: async (): Promise<unknown> => {
      throw new SyntaxError("Unexpected token <");
    },
  });

  test("load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => unreadable()),
    );
    mount();
    const error = await screen.findByTestId("mandate-load-error");
    expect(error.textContent).toContain("cannot read");
    expect(error.textContent).not.toContain("Could not reach");
  });

  test("save", async () => {
    const { calls } = serve({ patch: () => unreadable() as unknown as ReturnType<typeof reply> });
    await loaded();
    type("mandate-field-business_purpose", "edited");
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    const error = await screen.findByTestId("mandate-save-error");
    expect(calls.some((c) => c.method === "PATCH")).toBe(true);
    expect(error.textContent).toContain("cannot read");
    expect(error.textContent).not.toContain("Could not reach");
  });

  test("refresh", async () => {
    let n = 0;
    serve({
      requirementsReply: () => {
        n += 1;
        return (n === 1 ? reply(200, []) : unreadable()) as unknown as ReturnType<typeof reply>;
      },
    });
    await loaded();
    fireEvent.click(screen.getByTestId("refresh-enforcement-button"));
    const error = await screen.findByTestId("refresh-enforcement-error");
    expect(error.textContent).toContain("cannot read");
    expect(error.textContent).not.toContain("Could not reach");
  });
});

describe("[UX03-04A22-R2-SAVELOCK] edits typed while a save is in flight are not lost or shown as saved", () => {
  test("the form is read-only during the save, typing is ignored, and the reply matches the request", async () => {
    let release: (value: ReturnType<typeof reply>) => void = () => undefined;
    let sentBody: Json = {};
    serve({
      manifests: {
        [MANIFEST]: manifestBody({
          forbidden_actions_json: { actions: [{ name: "refund", reason: "policy" }] },
        }),
      },
      patch: (body) =>
        new Promise((resolve) => {
          sentBody = body;
          release = resolve;
        }),
    });
    await loaded();
    type("mandate-field-business_purpose", "first");
    fireEvent.click(screen.getByTestId("mandate-save-button"));
    await waitFor(() =>
      expect(screen.getByTestId("mandate-save-button").textContent).toBe("Saving…"),
    );
    const purpose = screen.getByTestId("mandate-field-business_purpose") as HTMLTextAreaElement;
    expect(purpose.readOnly).toBe(true);
    for (const field of [...MANDATE_LIST_FIELDS, ...MANDATE_NUMBER_FIELDS]) {
      expect(
        (screen.getByTestId(`mandate-field-${field}`) as HTMLInputElement).readOnly,
        field,
      ).toBe(true);
    }
    expect((screen.getByTestId("new-forbidden-action-name") as HTMLInputElement).readOnly).toBe(
      true,
    );
    expect((screen.getByTestId("forbidden-action-add-button") as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(
      (screen.getByTestId("forbidden-action-remove-refund") as HTMLButtonElement).disabled,
    ).toBe(true);
    // Edits attempted anyway (a programmatic change event) do nothing.
    type("mandate-field-business_purpose", "second");
    type("mandate-field-latency_slo_ms", "900");
    fireEvent.click(screen.getByTestId("forbidden-action-remove-refund"));
    expect(purpose.value).toBe("first");
    expect((screen.getByTestId("mandate-field-latency_slo_ms") as HTMLInputElement).value).toBe("");
    release(reply(200, manifestBody({ ...sentBody })));
    await screen.findByTestId("mandate-saved-notice");
    expect(purpose.value).toBe("first");
    expect(screen.queryByTestId("mandate-dirty-notice")).toBeNull();
    expect(screen.getByTestId("forbidden-action-name-refund")).toBeTruthy();
    expect(purpose.readOnly).toBe(false);
  });
});
