// @vitest-environment jsdom
//
// FTF-UX01 round 4 (Control-plane return, QUEUE item 0n): ErrorSummary takes focus ONLY when
// `submitCount` changes (or on mount with errors), never because the error list went from empty to
// non-empty without a submit. Each test was run RED against the round-3 code before the fix.
import { createElement, useState, type ReactElement } from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import {
  Banner,
  Breadcrumbs,
  Button,
  ErrorSummary,
  Field,
  PendingView,
  StatusBadge,
  type StatusState,
} from "../components/ui";

afterEach(() => cleanup());

/** A form that builds `errors` during render and has NOT been submitted (submitCount stays 1 from an
 * earlier failed submit, as in the verifier's R3-T1 caller): the list crosses the empty/non-empty
 * threshold as the user types and deletes. */
function ThresholdForm({ submitCount }: { submitCount?: number }): ReactElement {
  const [name, setName] = useState("");
  const errors = name.length < 20 ? [{ fieldId: "nm", message: "Name is too short" }] : [];
  return createElement(
    "div",
    null,
    createElement(ErrorSummary, { errors, submitCount }),
    createElement("input", {
      id: "nm",
      "aria-label": "Name",
      value: name,
      onChange: (e: { target: { value: string } }) => setName(e.target.value),
    }),
  );
}

test("[UX01-R4-T1] typing past the threshold and Backspace back across it keeps focus in the field and every keystroke", async () => {
  const user = userEvent.setup();
  render(createElement(ThresholdForm, { submitCount: 1 }));
  await user.click(screen.getByRole("link", { name: "Name is too short" }));
  const input = screen.getByLabelText("Name") as HTMLInputElement;
  expect(document.activeElement).toBe(input);
  const twenty = "abcdefghijklmnopqrst";
  await user.keyboard(twenty);
  expect(screen.queryByRole("alert"), "errors cleared at 20 characters").toBeNull();
  expect(input.value).toBe(twenty);
  await user.keyboard("{Backspace}");
  // The list is non-empty again with NO new submit: the summary reappears but must not take focus.
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(document.activeElement, "focus stays in the field after Backspace").toBe(input);
  await user.keyboard("xy");
  expect(input.value).toBe(twenty.slice(0, -1) + "xy");
  expect(document.activeElement).toBe(input);
});

test("[UX01-R4-T1b] the same crossing with no submitCount passed at all", async () => {
  const user = userEvent.setup();
  render(createElement(ThresholdForm));
  const input = screen.getByLabelText("Name") as HTMLInputElement;
  input.focus();
  await user.keyboard("abcdefghijklmnopqrst");
  await user.keyboard("{Backspace}");
  await user.keyboard("z");
  expect(document.activeElement).toBe(input);
  expect(input.value).toBe("abcdefghijklmnopqrsz");
});

/** A blur-validation caller: a field is invalid once the user has left it empty. The summary is
 * rendered unconditionally and `submitCount` never changes (no submit happened). */
function BlurForm(): ReactElement {
  const [touched, setTouched] = useState(false);
  const [a, setA] = useState("");
  const errors = touched && a === "" ? [{ fieldId: "a", message: "Enter a first name" }] : [];
  return createElement(
    "div",
    null,
    createElement(ErrorSummary, { errors }),
    createElement("input", {
      id: "a",
      "aria-label": "First",
      value: a,
      onChange: (e: { target: { value: string } }) => setA(e.target.value),
      onBlur: () => setTouched(true),
    }),
    createElement("input", { id: "b", "aria-label": "Second" }),
  );
}

test("[UX01-R4-T2] tabbing out of an empty field shows the error without moving focus off the next field", async () => {
  const user = userEvent.setup();
  render(createElement(BlurForm));
  await user.click(screen.getByLabelText("First"));
  await user.tab();
  expect(screen.getByRole("alert"), "the error appears on blur").toBeTruthy();
  const second = screen.getByLabelText("Second") as HTMLInputElement;
  expect(document.activeElement, "focus is on the field the user tabbed to").toBe(second);
  await user.keyboard("typed");
  expect(second.value, "later keystrokes are not lost").toBe("typed");
  expect(document.activeElement).toBe(second);
});

test("[UX01-R4-T3] a bumped submitCount still takes focus when the errors arrive in the same update; a submit with no errors focuses nothing", () => {
  const errs = [{ fieldId: "f1", message: "Enter a name" }];
  const view = (errors: typeof errs, submitCount: number) =>
    createElement(
      "div",
      null,
      createElement(ErrorSummary, { errors, submitCount }),
      createElement("input", { id: "f1" }),
    );
  const { rerender } = render(view([], 0));
  (document.getElementById("f1") as HTMLInputElement).focus();
  // A submit that finds no errors: nothing to focus, focus stays where it is.
  rerender(view([], 1));
  expect(document.activeElement).toBe(document.getElementById("f1"));
  // A submit that fails: errors and submitCount arrive together.
  rerender(view(errs, 2));
  expect(document.activeElement).toBe(screen.getByRole("alert"));
  // Mounting with errors already present also takes focus.
  cleanup();
  render(view(errs, 0));
  expect(document.activeElement).toBe(screen.getByRole("alert"));
  fireEvent.click(screen.getByRole("link", { name: "Enter a name" }));
  expect(document.activeElement).toBe(document.getElementById("f1"));
});

// --- Advisories taken (cheap): prototype-key states/locales, unknown-locale lang, Pending clamp,
// --- soft hyphen / Hangul filler as blank.

test("[UX01-R4-A1] a state or locale named after an Object.prototype member renders the neutral badge / English, never throws", () => {
  for (const state of ["toString", "constructor", "__proto__", "hasOwnProperty", "valueOf"]) {
    const { container, unmount } = render(
      createElement(StatusBadge, { state: state as never, locale: "en" }),
    );
    const badge = container.querySelector("[data-status]") as HTMLElement;
    expect(badge.getAttribute("data-status"), state).toBe("unknown");
    expect(badge.textContent, state).toContain("Unknown state");
    unmount();
  }
  for (const locale of ["toString", "constructor", "__proto__"]) {
    const { container, unmount } = render(
      createElement(StatusBadge, { state: "failed", locale: locale as never }),
    );
    const badge = container.querySelector("[data-status]") as HTMLElement;
    expect(badge.textContent, locale).toContain("Failed");
    expect(badge.textContent, `${locale}: never a function body`).not.toContain("function");
    unmount();
  }
});

test("[UX01-R4-A2] an unknown locale renders English and says lang=en, not the unknown value", () => {
  const { container } = render(
    createElement(StatusBadge, { state: "failed", locale: "fr" as never }),
  );
  const badge = container.querySelector("[data-status]") as HTMLElement;
  expect(badge.textContent).toContain("Failed");
  expect(badge.getAttribute("lang")).toBe("en");
  expect(badge.getAttribute("dir")).toBe("ltr");
  cleanup();
  render(
    createElement(Field, {
      id: "f",
      label: "Name",
      requirement: "required",
      locale: "fr" as never,
    }),
  );
  expect(screen.getByLabelText(/Name/).closest("[lang]")?.getAttribute("lang")).toBe("en");
});

test("[UX01-R4-A3] a determinate Pending percentage is clamped in the text and in aria-valuenow", () => {
  for (const [value, want] of [
    [150, 100],
    [-5, 0],
    [42.4, 42],
  ] as const) {
    const { unmount } = render(
      createElement(PendingView, { phase: "progress", label: "Upload", value }),
    );
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow"), String(value)).toBe(
      String(want === 42 ? value : want),
    );
    expect(screen.getByText(`${want}%`), String(value)).toBeTruthy();
    unmount();
  }
});

test("[UX01-R4-A4] a soft hyphen or Hangul filler alone is a blank reason / error", () => {
  render(createElement(Button, { disabledReason: "­ㅤ" }, "Go"));
  expect((screen.getByRole("button", { name: "Go" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.queryByTestId("disabled-reason")).toBeNull();
  render(createElement(Field, { id: "n9", label: "Name", requirement: "optional", error: "ㅤ­" }));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("[UX01-R4-A2b] same-class sweep: every localized component says lang=en for an unknown locale", () => {
  const fr = "fr" as never;
  const cases: [string, ReactElement, string][] = [
    [
      "ErrorSummary",
      createElement(ErrorSummary, { errors: [{ fieldId: "x", message: "m" }], locale: fr }),
      "[role=alert]",
    ],
    ["Breadcrumbs", createElement(Breadcrumbs, { items: [{ label: "Home" }], locale: fr }), "nav"],
    [
      "PendingView",
      createElement(PendingView, { phase: "busy", label: "Wait", locale: fr }),
      "[role=status]",
    ],
  ];
  for (const [name, element, selector] of cases) {
    const { container, unmount } = render(element);
    const node = container.querySelector(selector) as HTMLElement;
    expect(node.getAttribute("lang"), name).toBe("en");
    expect(node.getAttribute("dir"), name).toBe("ltr");
    unmount();
  }
});

test("[UX01-R4-A5] Banner carries lang/dir for its text: Arabic is rtl, an unknown locale is en/ltr, default is en", () => {
  const cases: [string | undefined, string, string][] = [
    ["ar", "ar", "rtl"],
    ["fr", "en", "ltr"],
    [undefined, "en", "ltr"],
  ];
  for (const [locale, lang, dir] of cases) {
    const { container, unmount } = render(
      createElement(Banner, { kind: "info", locale: locale as never, children: "نص" }),
    );
    const node = container.querySelector("[data-kind]") as HTMLElement;
    expect(node.getAttribute("lang"), String(locale)).toBe(lang);
    expect(node.getAttribute("dir"), String(locale)).toBe(dir);
    unmount();
  }
});

test("[UX01-R4-A6] no status or banner icon is an emoji-presentation glyph (colour emoji are font-dependent and ignore the token colours)", () => {
  const states: StatusState[] = [
    "draft",
    "not_connected",
    "validation_required",
    "blocked",
    "queued",
    "running",
    "failed",
    "cancelled",
    "qualified",
    "production",
  ];
  const icons: [string, string][] = [];
  for (const state of states) {
    const { container, unmount } = render(
      createElement(StatusBadge, { state, detail: state === "blocked" ? "why" : "scope" }),
    );
    icons.push([state, (container.querySelector("[aria-hidden]") as HTMLElement).textContent!]);
    unmount();
  }
  const unknown = render(createElement(StatusBadge, { state: "nope" as never }));
  icons.push([
    "unknown",
    (unknown.container.querySelector("[aria-hidden]") as HTMLElement).textContent!,
  ]);
  unknown.unmount();
  for (const kind of ["info", "success", "warning", "error"] as const) {
    const { container, unmount } = render(createElement(Banner, { kind, children: "text" }));
    icons.push([
      `banner ${kind}`,
      (container.querySelector("[aria-hidden]") as HTMLElement).textContent!,
    ]);
    unmount();
  }
  expect(icons.length).toBe(15);
  for (const [name, icon] of icons) {
    expect(icon.length, `${name} has an icon`).toBeGreaterThan(0);
    expect(/\p{Emoji_Presentation}|️/u.test(icon), `${name}: ${icon} must be a text glyph`).toBe(
      false,
    );
  }
});

test("FTF-UX02 r2 (regression, Law 15): Field's own input carries the same --target-min floor as Button (WCAG 2.2 2.5.8) -- a bare <input> is under 24x24 in every real browser, which FTF-UX06's real-Chromium axe check on a Field-built screen caught when the round-1 block-label fix was added here without it", () => {
  render(createElement(Field, { id: "fld", label: "Name", requirement: "required" }));
  expect(screen.getByLabelText(/Name/).style.minBlockSize).toBe("var(--target-min)");
});

test("FTF-UX02 r2 (regression): a caller's own style on Field still wins over the --target-min default", () => {
  render(
    createElement(Field, {
      id: "fld2",
      label: "Name",
      requirement: "required",
      style: { minBlockSize: "48px" },
    }),
  );
  expect(screen.getByLabelText(/Name/).style.minBlockSize).toBe("48px");
});
