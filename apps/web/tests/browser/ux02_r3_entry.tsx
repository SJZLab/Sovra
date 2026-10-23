// FTF-UX02 round 3 (§72.5 real-browser evidence, QUEUE 0k points 1/3/4/5). Client-side entry bundled
// in memory by apps/web/tests/ftf_ux02_r3_browser.spec.ts and injected into a real Chromium page.
// Mounts the REAL FTF-0500A, FTF-0501-UI and FTF-0516 components and a probe that opens the shared
// UX01 Dialog; `fetch` is replaced by a route table the spec passes in (nothing is a stand-in for a
// component). Same approach as ux02_entry.tsx, extended with the third component and the Dialog.
import { createElement, useState, type ComponentType } from "react";
import { createRoot } from "react-dom/client";

import { DatasetIssueResolutionUI } from "../../components/dataset-factory/dataset_issue_resolution_ui";
import { DatasetSourceSelectorPurposeAuthorizationUi } from "../../components/dataset-factory/dataset_source_selector_purpose_authorization_ui";
import { ResumableDatasetUploadUI } from "../../components/dataset-factory/resumable_dataset_upload_ui";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";

export interface Route {
  method: string;
  /** Matched against the END of the request's pathname. */
  path: string;
  status?: number;
  body?: unknown;
  /** Never settles: the request stays in flight (the loading state). */
  pending?: boolean;
  /** Rejects like a network failure. */
  reject?: boolean;
  /** Settle only after this many ms (a request slow enough for the UI to be observed busy). */
  delayMs?: number;
  /** The JSON body is not JSON (a 200 HTML error page). */
  notJson?: boolean;
}

/** Opens the shared Dialog on mount with two footer actions, so its geometry can be measured. */
function DialogProbe({ locale, longText }: { locale?: "en" | "ar"; longText?: boolean }) {
  const [open, setOpen] = useState(true);
  const body = longText ? "x".repeat(300) : "This cannot be undone.";
  return (
    <div>
      <Button data-testid="probe-opener" onClick={() => setOpen(true)}>
        open
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Abandon this draft?"
        description={body}
        locale={locale}
        data-testid="probe-dialog"
      >
        <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
          <Button data-testid="probe-keep" onClick={() => setOpen(false)}>
            Keep
          </Button>
          <Button data-testid="probe-abandon" variant="danger">
            Abandon
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

const REGISTRY: Record<string, ComponentType<Record<string, unknown>>> = {
  "0500A": DatasetSourceSelectorPurposeAuthorizationUi as unknown as ComponentType<
    Record<string, unknown>
  >,
  "0501-UI": ResumableDatasetUploadUI as unknown as ComponentType<Record<string, unknown>>,
  "0516": DatasetIssueResolutionUI as unknown as ComponentType<Record<string, unknown>>,
  Dialog: DialogProbe as unknown as ComponentType<Record<string, unknown>>,
};

function stubFetch(routes: Route[]): void {
  window.fetch = ((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const pathname = new URL(url).pathname;
    for (const route of routes) {
      if (route.method !== method || !pathname.endsWith(route.path)) continue;
      if (route.pending) return new Promise(() => undefined);
      if (route.reject) return Promise.reject(new TypeError("Failed to fetch"));
      const status = route.status ?? 200;
      const reply = {
        ok: status >= 200 && status < 300,
        status,
        json: async () => {
          if (route.notJson) throw new SyntaxError("Unexpected token < in JSON at position 0");
          return route.body;
        },
      } as Response;
      return route.delayMs
        ? new Promise<Response>((resolve) => setTimeout(() => resolve(reply), route.delayMs))
        : Promise.resolve(reply);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      json: async () => ({ detail: "not stubbed" }),
    } as Response);
  }) as typeof window.fetch;
}

function mount(
  name: string,
  props: Record<string, unknown>,
  storageItems?: Record<string, string>,
) {
  window.localStorage.clear();
  for (const [key, value] of Object.entries(storageItems ?? {})) {
    window.localStorage.setItem(key, value);
  }
  const component = REGISTRY[name];
  if (!component) throw new Error(`unknown component ${name}`);
  createRoot(document.getElementById("root")!).render(createElement(component, props));
}

(window as unknown as { UX02R3: unknown }).UX02R3 = { stubFetch, mount };
