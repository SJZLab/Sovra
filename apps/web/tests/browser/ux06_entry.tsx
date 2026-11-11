// FTF-UX06 round 2, point 1 (QUEUE 0f; UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md
// §72.5): a client-side entry bundled in memory by `ftf_ux06_browser.spec.ts` and injected into a
// real Chromium page. The component fetches data in effects and on click, so this MOUNTS the real
// component and replaces `fetch` with a route table -- loading, populated, empty, error and
// edit-form states all exist in a real layout engine, same technique as `ux03_entry.tsx`.
import { createElement } from "react";
import { createRoot } from "react-dom/client";

import { EvaluationCaseEditorImport } from "../../components/evaluation-factory/evaluation_case_editor_import";

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
    const pathname = new URL(url).pathname;
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
    createElement(EvaluationCaseEditorImport, props as never),
  );
}

(window as unknown as { UX06: unknown }).UX06 = { stubFetch, mount };
