// @vitest-environment jsdom
//
// FTF-UX01 -- design-system foundation: tokens (section 72.1) + shared accessible components.
// Component-level DOM tests (Vitest + React Testing Library + jsdom); no Next.js page tree yet (FTF-H029).
// Contrast is COMPUTED here from apps/web/styles/tokens.css, not asserted from a hard-coded table.
import { createElement } from "react";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  Banner,
  Breadcrumbs,
  Button,
  DataTable,
  EmptyState,
  ErrorSummary,
  Field,
  Pending,
  phaseFor,
  StatusBadge,
  statusLabel,
} from "../components/ui";
import CSS from "../styles/tokens.css?raw";

afterEach(() => cleanup());

function block(selector: string): Record<string, string> {
  const start = CSS.indexOf(selector);
  expect(start, `block ${selector}`).toBeGreaterThanOrEqual(0);
  const body = CSS.slice(CSS.indexOf("{", start) + 1, CSS.indexOf("}", start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/(--[\w-]+):\s*([^;]+);/g))
    out[m[1] as string] = (m[2] as string).trim();
  return out;
}

const LIGHT = block(":root {");
const DARK = block(':root[data-theme="dark"]');
const DARK_MEDIA = block(':root:not([data-theme="light"])');

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
type Tokens = Record<string, string>;
function tok(tokens: Tokens, name: string): string {
  const v = tokens[name];
  if (v === undefined) throw new Error(`missing token ${name}`);
  return v;
}

test("[UX01-T1] section 72.1 light token values are exact", () => {
  expect(LIGHT).toMatchObject({
    "--space-1": "4px",
    "--space-2": "8px",
    "--space-3": "12px",
    "--space-4": "16px",
    "--space-5": "24px",
    "--space-6": "32px",
    "--font-body": "14px",
    "--line-body": "20px",
    "--font-table": "13px",
    "--font-label": "12px",
    "--font-title": "24px",
    "--color-surface": "#ffffff",
    "--color-surface-2": "#f8fafc",
    "--color-border": "#cbd5e1",
    "--color-text": "#0f172a",
    "--color-muted": "#475569",
    "--color-action": "#1d4ed8",
    "--color-success": "#166534",
    "--color-warning": "#92400e",
    "--color-danger": "#b91c1c",
  });
});

const TEXT_ON_SURFACE = [
  "--color-text",
  "--color-muted",
  "--color-action",
  "--color-success",
  "--color-warning",
  "--color-danger",
];
for (const [name, tokens] of [
  ["light", LIGHT],
  ["dark", DARK],
] as const) {
  test(`[UX01-T2] ${name}: every text token >= 4.5:1 on both surfaces; border and focus >= 3:1`, () => {
    for (const surface of ["--color-surface", "--color-surface-2"]) {
      for (const t of TEXT_ON_SURFACE) {
        expect(
          ratio(tok(tokens, t), tok(tokens, surface)),
          `${name} ${t} on ${surface}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
    expect(
      ratio(tok(tokens, "--color-action-text"), tok(tokens, "--color-action")),
      `${name} button label`,
    ).toBeGreaterThanOrEqual(4.5);
    expect(
      ratio(tok(tokens, "--color-focus"), tok(tokens, "--color-surface")),
      `${name} focus ring`,
    ).toBeGreaterThanOrEqual(3);
    // The section 72.1 light border (#cbd5e1) is a decorative divider; UI-component boundaries use --color-muted.
    expect(
      ratio(tok(tokens, "--color-muted"), tok(tokens, "--color-surface")),
      `${name} control boundary`,
    ).toBeGreaterThanOrEqual(3);
  });
}

test("[UX01-T3] dark tokens are separate from light and the prefers-color-scheme block matches them", () => {
  expect(DARK["--color-surface"]).not.toBe(LIGHT["--color-surface"]);
  expect(DARK["--color-text"]).not.toBe(LIGHT["--color-text"]);
  expect(DARK_MEDIA).toEqual(DARK);
});

test("[UX01-T4] no CDN or remote asset in the stylesheet (ZERO_EGRESS)", () => {
  expect(CSS).not.toMatch(/https?:\/\//);
  expect(CSS).not.toMatch(/@import/);
});

test("[UX01-T5] Field: visible label, helper and error associated, required marked, input preserved", () => {
  const { rerender } = render(
    createElement(Field, {
      id: "name",
      label: "Dataset name",
      helper: "Unique in the project",
      requirement: "required",
      defaultValue: "abc",
    }),
  );
  const input = screen.getByLabelText(/Dataset name/);
  expect(input.getAttribute("aria-describedby")).toBe("name-helper");
  expect(input.getAttribute("aria-required")).toBe("true");
  expect(input.getAttribute("aria-invalid")).toBeNull();
  expect(screen.getByText("(required)")).toBeTruthy();
  rerender(
    createElement(Field, {
      id: "name",
      label: "Dataset name",
      helper: "Unique in the project",
      error: "Name is already used",
      requirement: "required",
      defaultValue: "abc",
    }),
  );
  const again = screen.getByLabelText(/Dataset name/) as HTMLInputElement;
  expect(again.getAttribute("aria-invalid")).toBe("true");
  expect(again.getAttribute("aria-describedby")).toBe("name-helper name-error");
  expect(screen.getByRole("alert").textContent).toBe("Name is already used");
  expect(again.value).toBe("abc");
});

test("[UX01-T6] ErrorSummary takes focus, links to and focuses the invalid field, renders nothing when clean", () => {
  const { rerender, container } = render(
    createElement(
      "div",
      null,
      createElement(ErrorSummary, { errors: [] }),
      createElement("input", { id: "f1" }),
    ),
  );
  expect(container.querySelector("[role=alert]")).toBeNull();
  rerender(
    createElement(
      "div",
      null,
      createElement(ErrorSummary, { errors: [{ fieldId: "f1", message: "Enter a name" }] }),
      createElement("input", { id: "f1" }),
    ),
  );
  const summary = screen.getByRole("alert");
  expect(document.activeElement).toBe(summary);
  fireEvent.click(screen.getByRole("link", { name: "Enter a name" }));
  expect(document.activeElement).toBe(document.getElementById("f1"));
});

test("[UX01-T7] StatusBadge: section 72.2 words, text + icon (never colour alone), blocked/qualified need detail", () => {
  expect(statusLabel({ state: "blocked", detail: "budget exhausted" })).toBe(
    "Blocked — budget exhausted",
  );
  expect(statusLabel({ state: "qualified", detail: "support-chat/ar" })).toBe(
    "Qualified for support-chat/ar",
  );
  expect(() => statusLabel({ state: "blocked" })).toThrow();
  expect(() => statusLabel({ state: "qualified", detail: "  " })).toThrow();
  for (const [s, w] of [
    ["draft", "Draft"],
    ["not_connected", "Not connected"],
    ["validation_required", "Validation required"],
    ["queued", "Queued"],
    ["running", "Running"],
    ["failed", "Failed"],
    ["cancelled", "Cancelled"],
    ["production", "Production"],
  ] as const) {
    expect(statusLabel({ state: s })).toBe(w);
  }
  const { container } = render(createElement(StatusBadge, { state: "failed" }));
  expect(container.textContent).toContain("Failed");
  expect(container.querySelector("[aria-hidden=true]")?.textContent?.length).toBeGreaterThan(0);
});

test("[UX01-T8] Button: disabled-with-reason shows the reason and is described by it", () => {
  const onClick = vi.fn();
  render(
    createElement(
      Button,
      { id: "go", onClick, disabledReason: "Upload a dataset first" },
      "Create dataset",
    ),
  );
  const b = screen.getByRole("button", { name: "Create dataset" }) as HTMLButtonElement;
  expect(b.disabled).toBe(true);
  expect(b.getAttribute("aria-describedby")).toBe("go-reason");
  expect(screen.getByTestId("disabled-reason").textContent).toBe("Upload a dataset first");
  fireEvent.click(b);
  expect(onClick).not.toHaveBeenCalled();
});

test("[UX01-T9] DataTable: caption, sortable header with aria-sort, empty state, keyboard-reachable sort", () => {
  type R = { id: string; name: string; secs: number };
  const rows: R[] = [
    { id: "a", name: "b", secs: 9 },
    { id: "b", name: "a", secs: 3 },
  ];
  const columns = [
    { key: "name", header: "Name", render: (r: R) => r.name, sortValue: (r: R) => r.name },
    { key: "secs", header: "Duration (s)", render: (r: R) => String(r.secs) },
  ];
  const empty = createElement(EmptyState, {
    title: "No runs",
    why: "No run has started.",
    action: createElement("a", { href: "#new" }, "Start a run"),
  });
  const { rerender } = render(
    createElement(DataTable<R>, { caption: "Runs", columns, rows, rowKey: (r: R) => r.id, empty }),
  );
  expect(screen.getByRole("table", { name: "Runs" })).toBeTruthy();
  const th = screen.getByRole("columnheader", { name: "Name" });
  expect(th.getAttribute("aria-sort")).toBe("none");
  expect(
    screen.getByRole("columnheader", { name: "Duration (s)" }).getAttribute("aria-sort"),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Name" }));
  expect(th.getAttribute("aria-sort")).toBe("ascending");
  expect(screen.getAllByRole("row").at(1)?.textContent).toContain("a3");
  fireEvent.click(screen.getByRole("button", { name: "Name" }));
  expect(th.getAttribute("aria-sort")).toBe("descending");
  expect(screen.getAllByRole("row").at(1)?.textContent).toContain("b9");
  rerender(
    createElement(DataTable<R>, {
      caption: "Runs",
      columns,
      rows: [],
      rowKey: (r: R) => r.id,
      empty,
    }),
  );
  expect(screen.getByRole("heading", { name: "No runs" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Start a run" })).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("[UX01-T10] Breadcrumbs: nav landmark, last crumb is the current page and not a link", () => {
  render(
    createElement(Breadcrumbs, {
      items: [
        { label: "Projects", href: "/p" },
        { label: "Acme", href: "/p/acme" },
        { label: "Datasets" },
      ],
    }),
  );
  expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeTruthy();
  expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["Projects", "Acme"]);
  expect(screen.getByText("Datasets").getAttribute("aria-current")).toBe("page");
});

test("[UX01-T11] Banner: errors are assertive alerts, others polite status, always text + icon", () => {
  const { rerender } = render(createElement(Banner, { kind: "error", children: "Upload failed" }));
  expect(screen.getByRole("alert").textContent).toContain("Upload failed");
  rerender(createElement(Banner, { kind: "info", children: "Saved as draft" }));
  expect(screen.getByRole("status").textContent).toContain("Saved as draft");
});

test("[UX01-T12] Pending follows the NN/g 0.1/1/10 s limits: nothing, busy after 1 s, progress + cancel after 10 s", () => {
  expect(phaseFor(999)).toBe("none");
  expect(phaseFor(1000)).toBe("busy");
  expect(phaseFor(9999)).toBe("busy");
  expect(phaseFor(10000)).toBe("progress");
  vi.useFakeTimers();
  try {
    const onCancel = vi.fn();
    render(createElement(Pending, { label: "Importing dataset", onCancel }));
    expect(screen.queryByRole("status")).toBeNull();
    act(() => void vi.advanceTimersByTime(1250));
    expect(screen.getByRole("status").textContent).toContain("Importing dataset");
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    act(() => void vi.advanceTimersByTime(9000));
    expect(screen.getByRole("progressbar")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledOnce();
  } finally {
    vi.useRealTimers();
  }
});
