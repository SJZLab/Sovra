// FTF-UX01 round 2 (§72.5 evidence): a static server-rendered harness page mounting every
// shared component in every state named by the return. Rendered with ReactDOMServer so it
// loads in a REAL browser (Chromium) with no bundler and no CORS-blocked module script --
// native elements (button/a/input) keep their real tab order and focus behaviour without any
// client-side hydration, which is all the §72.5 viewport/keyboard-only/zoom checks and the axe
// scan below need. Interactive STATE CHANGES (onClick firing, DataTable re-sorting, Pending's
// timers) are already exercised for real in apps/web/tests/ftf_ux01.spec.ts (jsdom + RTL +
// fireEvent/user-event) -- this harness is deliberately scoped to what a real layout engine
// proves that jsdom cannot: real computed styles, real reflow, real native focus order.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  Banner,
  Breadcrumbs,
  Button,
  DataTable,
  EmptyState,
  ErrorSummary,
  Field,
  PendingView,
  StatusBadge,
} from "../../components/ui";

type Row = { id: string; name: string; secs: number };
const ROWS: Row[] = [
  { id: "a", name: "import-2026-10-01", secs: 42 },
  { id: "b", name: "import-2026-10-05", secs: 9 },
];

function Harness({ locale }: { locale: "en" | "ar" }) {
  return createElement(
    "main",
    { id: "harness", style: { padding: "16px", display: "grid", gap: "24px" } },
    createElement(
      "section",
      { "aria-label": "breadcrumbs" },
      createElement(Breadcrumbs, {
        locale,
        items: [
          { label: "Projects", href: "/p" },
          { label: "Acme", href: "/p/acme" },
          { label: "Datasets", href: "/p/acme/datasets" },
        ],
      }),
    ),
    createElement(
      "section",
      { "aria-label": "banners" },
      createElement(Banner, { kind: "info", children: "Saved as draft" }),
      createElement(Banner, { kind: "success", children: "Upload complete" }),
      createElement(Banner, { kind: "warning", children: "Connection expires soon" }),
      createElement(Banner, { kind: "error", children: "Upload failed" }),
    ),
    createElement(
      "section",
      { "aria-label": "status-badges" },
      createElement(StatusBadge, { state: "draft", locale }),
      createElement(StatusBadge, { state: "not_connected", locale }),
      createElement(StatusBadge, { state: "validation_required", locale }),
      createElement(StatusBadge, { state: "blocked", detail: "budget exhausted", locale }),
      createElement(StatusBadge, { state: "blocked", locale }),
      createElement(StatusBadge, { state: "queued", locale }),
      createElement(StatusBadge, { state: "running", locale }),
      createElement(StatusBadge, { state: "failed", locale }),
      createElement(StatusBadge, { state: "cancelled", locale }),
      createElement(StatusBadge, { state: "qualified", detail: "support-chat/ar", locale }),
      createElement(StatusBadge, { state: "production", locale }),
    ),
    createElement(
      "section",
      { "aria-label": "buttons" },
      createElement(Button, { variant: "primary" }, "Create dataset"),
      createElement(Button, { variant: "secondary" }, "Cancel"),
      createElement(Button, { variant: "danger" }, "Revoke connection"),
      createElement(Button, { disabledReason: "Upload a dataset first" }, "Start training"),
    ),
    createElement(
      "section",
      { "aria-label": "field" },
      createElement(Field, {
        id: "dataset-name",
        label: "Dataset name",
        helper: "Unique in the project",
        requirement: "required",
        locale,
      }),
      createElement(Field, {
        id: "dataset-desc",
        label: "Description",
        error: "Description is too long",
        requirement: "optional",
        locale,
      }),
    ),
    createElement(ErrorSummary, {
      locale,
      errors: [
        { fieldId: "dataset-name", message: "Enter a dataset name" },
        { fieldId: "dataset-desc", message: "Description is too long" },
      ],
    }),
    createElement(
      "section",
      { "aria-label": "pending" },
      createElement(PendingView, {
        phase: "progress",
        label: "Importing dataset",
        value: 42,
        onCancel: () => {},
        locale,
      }),
      createElement(PendingView, { phase: "progress", label: "Validating schema", locale }),
      createElement(PendingView, { phase: "busy", label: "Checking connection", locale }),
    ),
    createElement(DataTable<Row>, {
      caption: "Recent imports",
      rowKey: (r: Row) => r.id,
      rows: ROWS,
      empty: createElement(EmptyState, {
        title: "No imports yet",
        why: "No dataset has been imported.",
        action: createElement("a", { href: "#new" }, "Start an import"),
      }),
      columns: [
        { key: "name", header: "Name", render: (r: Row) => r.name, sortValue: (r: Row) => r.name },
        { key: "secs", header: "Duration (s)", render: (r: Row) => String(r.secs) },
      ],
    }),
    createElement(DataTable<Row>, {
      caption: "Empty imports",
      rowKey: (r: Row) => r.id,
      rows: [],
      empty: createElement(EmptyState, {
        title: "No imports yet",
        why: "No dataset has been imported.",
        action: createElement("a", { href: "#new" }, "Start an import"),
      }),
      columns: [{ key: "name", header: "Name", render: (r: Row) => r.name }],
    }),
  );
}

/** A full standalone HTML document: tokens.css inlined (no CDN, ZERO_EGRESS), no script tags. */
export function buildHarnessHtml(tokensCss: string, locale: "en" | "ar" = "en"): string {
  const body = renderToStaticMarkup(createElement(Harness, { locale }));
  return `<!doctype html>
<html lang="${locale}" dir="${locale === "ar" ? "rtl" : "ltr"}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>FTF-UX01 evidence harness</title>
<style>${tokensCss}</style>
</head>
<body>${body}</body>
</html>`;
}
