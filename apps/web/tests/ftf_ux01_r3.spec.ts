// @vitest-environment jsdom
//
// FTF-UX01 round 3 (Control-plane return 2026-10-21T12:49Z, QUEUE item 0n). Each test is bound to
// one return point and was run RED against the round-2 code before the fix (see the submission).
// Component-level DOM tests (Vitest + React Testing Library + user-event + jsdom).
import { createElement, useState, type ReactElement } from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import {
  Breadcrumbs,
  Button,
  DataTable,
  ErrorSummary,
  Field,
  PendingView,
  StatusBadge,
  type StatusState,
} from "../components/ui";
import CSS from "../styles/tokens.css?raw";

afterEach(() => cleanup());

function block(selector: string): Record<string, string> {
  const start = CSS.indexOf(selector);
  expect(start, `block ${selector}`).toBeGreaterThanOrEqual(0);
  const body = CSS.slice(CSS.indexOf("{", start) + 1, CSS.indexOf("}", start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/([\w-]+):\s*([^;]+);/g))
    out[m[1] as string] = (m[2] as string).trim();
  return out;
}
function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const lin = (v: number): number => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
}
function ratio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
function need(tokens: Record<string, string>, name: string): string {
  const v = tokens[name];
  if (v === undefined) throw new Error(`missing token ${name}`);
  return v;
}

const LIGHT = block(":root {");
const DARK = block(':root[data-theme="dark"]');
const DARK_MEDIA = block(':root:not([data-theme="light"])');

// --- Point 1: ERRORSUMMARY-FOCUS-STEAL ---------------------------------------------------------

/** A caller that builds the error list DURING RENDER (a fresh array every render) -- the exact
 * pattern the round-2 identity-keyed effect broke on. */
function Form({ submitCount }: { submitCount?: number }): ReactElement {
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

test("[UX01-R3-T1] typing after following a summary link keeps focus in the field and keeps every keystroke", async () => {
  const user = userEvent.setup();
  render(createElement(Form, { submitCount: 1 }));
  await user.click(screen.getByRole("link", { name: "Name is too short" }));
  const input = screen.getByLabelText("Name") as HTMLInputElement;
  expect(document.activeElement).toBe(input);
  await user.keyboard("abcdef");
  expect(document.activeElement, "focus must stay in the field while typing").toBe(input);
  expect(input.value).toBe("abcdef");
});

test("[UX01-R3-T1b] typing keeps focus even when the caller passes no submitCount at all", async () => {
  const user = userEvent.setup();
  render(createElement(Form));
  const input = screen.getByLabelText("Name") as HTMLInputElement;
  input.focus();
  await user.keyboard("xyz");
  expect(document.activeElement).toBe(input);
  expect(input.value).toBe("xyz");
});

test("[UX01-R3-T2] a bumped submitCount re-takes focus; a fresh array with the same submitCount does not", () => {
  const errs = () => [{ fieldId: "f1", message: "Enter a name" }];
  const { rerender } = render(
    createElement(
      "div",
      null,
      createElement(ErrorSummary, { errors: errs(), submitCount: 1 }),
      createElement("input", { id: "f1" }),
    ),
  );
  expect(document.activeElement).toBe(screen.getByRole("alert"));
  (document.getElementById("f1") as HTMLInputElement).focus();
  rerender(
    createElement(
      "div",
      null,
      createElement(ErrorSummary, { errors: errs(), submitCount: 1 }),
      createElement("input", { id: "f1" }),
    ),
  );
  expect(document.activeElement, "same submitCount: no steal").toBe(document.getElementById("f1"));
  rerender(
    createElement(
      "div",
      null,
      createElement(ErrorSummary, { errors: errs(), submitCount: 2 }),
      createElement("input", { id: "f1" }),
    ),
  );
  expect(document.activeElement, "second submit: re-take").toBe(screen.getByRole("alert"));
});

// --- Point 2: DARK-LINK-CONTRAST ---------------------------------------------------------------

test("[UX01-R3-T3] link tokens: light = the action token, dark >= 4.5:1 on both dark surfaces, both dark blocks agree", () => {
  expect(need(LIGHT, "--color-link")).toBe(need(LIGHT, "--color-action"));
  expect(need(LIGHT, "--color-link")).toBe("#1d4ed8");
  for (const surface of ["--color-surface", "--color-surface-2"]) {
    expect(
      ratio(need(DARK, "--color-link"), need(DARK, surface)),
      `dark link on ${surface}`,
    ).toBeGreaterThanOrEqual(4.5);
    expect(
      ratio(need(LIGHT, "--color-link"), need(LIGHT, surface)),
      `light link on ${surface}`,
    ).toBeGreaterThanOrEqual(4.5);
  }
  expect(need(DARK_MEDIA, "--color-link")).toBe(need(DARK, "--color-link"));
});

test("[UX01-R3-T3b] the stylesheet colours every link from the token and declares color-scheme for light and dark", () => {
  const anchor = block("a {");
  expect(anchor["color"]).toBe("var(--color-link)");
  expect(block(":root {")["color-scheme"]).toBe("light dark");
  expect(block(':root[data-theme="dark"]')["color-scheme"]).toBe("dark");
  expect(block(':root[data-theme="light"]')["color-scheme"]).toBe("light");
});

// --- Point 4: UNPINNED P6 / F15b / F15c --------------------------------------------------------

test("[UX01-R3-T4] two ErrorSummaries on one page have distinct ids and distinct accessible names (P6)", () => {
  render(
    createElement(
      "div",
      null,
      createElement(ErrorSummary, {
        errors: [{ fieldId: "a", message: "A is bad" }],
        title: "Upload problems",
      }),
      createElement(ErrorSummary, {
        errors: [{ fieldId: "b", message: "B is bad" }],
        title: "Mapping problems",
      }),
    ),
  );
  const alerts = screen.getAllByRole("alert");
  expect(alerts).toHaveLength(2);
  const ids = alerts.map((a) => a.getAttribute("aria-labelledby"));
  expect(ids[0]).not.toBe(ids[1]);
  expect(screen.getByRole("alert", { name: "Upload problems" })).toBeTruthy();
  expect(screen.getByRole("alert", { name: "Mapping problems" })).toBeTruthy();
});

const ALL_STATES: StatusState[] = [
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

test("[UX01-R3-T5] all ten section 72.2 states have pairwise distinct icons, blocked and qualified included", () => {
  expect(ALL_STATES).toHaveLength(10);
  const icons = new Map<string, string>();
  for (const s of ALL_STATES) {
    const { container, unmount } = render(
      createElement(StatusBadge, {
        state: s,
        detail: s === "blocked" ? "budget" : s === "qualified" ? "support/ar" : undefined,
      }),
    );
    const icon = container.querySelector("[aria-hidden=true]")?.textContent ?? "";
    expect(icon, `${s} has an icon`).not.toBe("");
    for (const [other, otherIcon] of icons)
      expect(icon, `${s} vs ${other} share an icon`).not.toBe(otherIcon);
    icons.set(s, icon);
    unmount();
  }
});

// --- Severity-2 probes (decided and fixed; see the submission) ---------------------------------

test("[UX01-R3-S2a] a disabled Button looks different from an enabled one without relying on colour alone", () => {
  render(
    createElement(
      "div",
      null,
      createElement(Button, { variant: "primary" }, "Go"),
      createElement(Button, { variant: "primary", disabledReason: "Upload first" }, "Stop"),
    ),
  );
  const on = screen.getByRole("button", { name: "Go" });
  const off = screen.getByRole("button", { name: "Stop" });
  expect(off.style.borderStyle).toBe("dashed");
  expect(on.style.borderStyle).not.toBe("dashed");
  expect(off.style.cursor).toBe("not-allowed");
  expect(off.style.background).not.toBe(on.style.background);
});

test("[UX01-R3-S2b] StatusBadge qualified with no scope reads 'Qualified', never a dangling 'Qualified for'", () => {
  const { container } = render(createElement(StatusBadge, { state: "qualified" }));
  expect(container.textContent?.trim().endsWith("Qualified")).toBe(true);
  expect(container.textContent).not.toMatch(/Qualified for/);
  cleanup();
  const ar = render(createElement(StatusBadge, { state: "qualified", locale: "ar" }));
  expect(ar.container.textContent).not.toMatch(/لـ\s*$/);
});

test("[UX01-R3-S2c] DataTable shows the sort direction visibly, not only in aria-sort", () => {
  render(
    createElement(DataTable<{ n: string }>, {
      caption: "T",
      rows: [{ n: "b" }, { n: "a" }],
      rowKey: (r) => r.n,
      empty: createElement("p", null, "none"),
      columns: [{ key: "n", header: "Name", render: (r) => r.n, sortValue: (r) => r.n }],
    }),
  );
  const btn = screen.getByRole("button", { name: /Name/ });
  expect(btn.textContent).not.toMatch(/[▲▼]/);
  fireEvent.click(btn);
  expect(screen.getByRole("button", { name: /Name/ }).textContent).toContain("▲");
  fireEvent.click(screen.getByRole("button", { name: /Name/ }));
  expect(screen.getByRole("button", { name: /Name/ }).textContent).toContain("▼");
});

test("[UX01-R3-S2d] an indeterminate Pending does not draw a full solid bar that reads as 100%", () => {
  const { rerender } = render(
    createElement(PendingView, { phase: "progress", label: "Importing" }),
  );
  const indeterminate = screen.getByTestId("progress-fill") as HTMLElement;
  expect(indeterminate.getAttribute("data-indeterminate")).toBe("true");
  expect(indeterminate.style.background).toContain("repeating-linear-gradient");
  rerender(createElement(PendingView, { phase: "progress", label: "Importing", value: 100 }));
  const full = screen.getByTestId("progress-fill") as HTMLElement;
  expect(full.getAttribute("data-indeterminate")).toBeNull();
  expect(full.style.background).not.toContain("repeating-linear-gradient");
});

test("[UX01-R3-S2e] an unknown locale at runtime falls back to English; an unknown API state renders a neutral badge and never throws", () => {
  const fr = render(createElement(StatusBadge, { state: "running", locale: "fr" as never }));
  expect(fr.container.textContent).toContain("Running");
  cleanup();
  const unknown = render(createElement(StatusBadge, { state: "weird_new_state" as never }));
  expect(unknown.container.textContent).toContain("Unknown state");
  expect(unknown.container.querySelector("[data-tone=muted]")).not.toBeNull();
  expect(unknown.container.querySelector("[aria-hidden=true]")?.textContent).toBeTruthy();
  cleanup();
  const bc = render(
    createElement(Breadcrumbs, {
      items: [{ label: "A", href: "/a" }, { label: "B" }],
      locale: "fr" as never,
    }),
  );
  expect(bc.container.querySelector("nav")?.getAttribute("aria-label")).toBe("Breadcrumb");
  expect(bc.container.querySelector("nav")?.getAttribute("dir")).toBe("ltr");
});

test("[UX01-R3-S2f] zero-width-only reasons, errors and details count as blank", () => {
  render(createElement(Button, { disabledReason: "​​" }, "Go"));
  expect((screen.getByRole("button", { name: "Go" }) as HTMLButtonElement).disabled).toBe(false);
  render(createElement(Field, { id: "zw", label: "Zw", requirement: "optional", error: "​" }));
  expect(screen.queryByRole("alert")).toBeNull();
  const b = render(createElement(StatusBadge, { state: "blocked", detail: "​" }));
  expect(b.container.textContent).toContain("Blocked — reason unavailable");
});
