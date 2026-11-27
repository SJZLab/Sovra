// FTF-H011-P4 (UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md §72.5): a client-side entry
// bundled in memory by `ftf_h011_browser.spec.ts` and injected into a real Chromium page. It
// MOUNTS the real mapping wizard and replaces `fetch` with a route table, so every state exists in a real
// layout engine (same technique as `ux06_entry.tsx`).
import { createElement } from "react";
import { createRoot } from "react-dom/client";

import { DatasetMappingWizard } from "../../components/dataset-factory/dataset_mapping_wizard_ui";

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
  /** Resolves `ok: true` with a body that is not valid JSON (a malformed 200). */
  malformedJson?: boolean;
}

function stubFetch(routes: Route[]): void {
  window.fetch = ((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = new URL(url);
    const pathname = u.pathname + u.search;
    for (const route of routes) {
      if (route.method !== method || !pathname.endsWith(route.path)) continue;
      if (route.pending) return new Promise(() => undefined);
      if (route.reject) return Promise.reject(new TypeError("Failed to fetch"));
      const status = route.status ?? 200;
      if (route.malformedJson) {
        return Promise.resolve({
          ok: status >= 200 && status < 300,
          status,
          json: async () => {
            throw new SyntaxError("Unexpected token < in JSON at position 0");
          },
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: async () => route.body,
      } as Response);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      json: async () => ({ detail: "not stubbed" }),
    } as Response);
  }) as typeof window.fetch;
}

function mount(props: Record<string, unknown>) {
  createRoot(document.getElementById("root")!).render(
    createElement(DatasetMappingWizard, props as never),
  );
}

(window as unknown as { H011P4: unknown }).H011P4 = { stubFetch, mount };
