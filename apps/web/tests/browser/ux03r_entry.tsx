// FTF-0603 UX evidence entry: bundled in memory by `ftf_0603_browser.spec.ts` and injected into a
// real Chromium page. Mounts the real SuiteValidationLocking and replaces `fetch` with a route
// table, the same technique as `ux06_entry.tsx`.
import { createElement } from "react";
import { createRoot } from "react-dom/client";

import { SuiteValidationLocking } from "../../components/evaluation-factory/suite_validation_locking";

export interface Route {
  method: string;
  /** Matched against the END of the request's pathname. */
  path: string;
  status?: number;
  body?: unknown;
  pending?: boolean;
  reject?: boolean;
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
    createElement(SuiteValidationLocking, props as never),
  );
}

(window as unknown as { UX0603: unknown }).UX0603 = { stubFetch, mount };
