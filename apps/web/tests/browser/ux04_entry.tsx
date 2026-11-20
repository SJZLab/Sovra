// FTF-UX04 (UX_STANDARD.md section 4 / section 72.5 real-browser evidence). Client-side entry bundled
// in memory by apps/web/tests/ftf_ux04_browser.spec.ts and injected into a real Chromium page. It
// MOUNTS the two real project-mandate components (FTF-0401 form, FTF-0408 panel) so that loading,
// empty, populated, validation, error, conflict and saved states exist in a real layout engine.
// `fetch` is replaced by a route table the spec passes in; nothing here is a component stand-in.
import { createElement, type ComponentType } from "react";
import { createRoot } from "react-dom/client";

import { ProjectSetupForm } from "../../components/project-mandate/project_schema_api_ui";
import { RequirementsPanel } from "../../components/project-mandate/requirements_ui";

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
}

const REGISTRY: Record<string, ComponentType<Record<string, unknown>>> = {
  "0401": ProjectSetupForm as unknown as ComponentType<Record<string, unknown>>,
  "0408": RequirementsPanel as unknown as ComponentType<Record<string, unknown>>,
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

function mount(name: string, props: Record<string, unknown>) {
  const component = REGISTRY[name];
  if (!component) throw new Error(`unknown component ${name}`);
  createRoot(document.getElementById("root")!).render(createElement(component, props));
}

(window as unknown as { UX04: unknown }).UX04 = { stubFetch, mount };
