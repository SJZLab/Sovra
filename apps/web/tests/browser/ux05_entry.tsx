// FTF-UX05 round 3, point 6 (QUEUE 0e; UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md §72.5):
// a client-side entry bundled in memory by `ftf_ux05_browser.spec.ts` and injected into a real
// Chromium page. It mounts the four real components (0214 model catalog, 0308 model-to-artifact
// mapping, 0309 capability compatibility, 1301 cost profile) and replaces `fetch` with a route
// table that answers with REAL `Response` objects -- so a non-JSON 200 produces the browser's own
// SyntaxError from `response.json()`, not a stand-in -- same technique as `ux06_entry.tsx`.
import { createElement } from "react";
import { createRoot } from "react-dom/client";

import { CapabilityCompatibilityUI } from "../../components/artifact-capabilities/capability_compatibility_ui";
import { ModelToArtifactMappingUI } from "../../components/artifact-capabilities/model_to_artifact_mapping_ui";
import { CostProfileSearchPanel } from "../../components/cost-and-model-search/costprofile_schema_ui";
import { ModelCatalogUI } from "../../components/litellm-gateway/model_catalog_ui";

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
  /** Answers with this raw text instead of JSON (a malformed 200 -- the browser's real parser fails). */
  rawText?: string;
}

export type Kind = "catalog" | "mapping" | "compat" | "cost";

function stubFetch(routes: Route[]): void {
  (window as unknown as { __requests: string[] }).__requests = [];
  window.fetch = ((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const pathname = new URL(url).pathname;
    (window as unknown as { __requests: string[] }).__requests.push(`${method} ${pathname}`);
    for (const route of routes) {
      if (route.method !== method || !pathname.endsWith(route.path)) continue;
      if (route.pending) return new Promise(() => undefined);
      if (route.reject) return Promise.reject(new TypeError("Failed to fetch"));
      const status = route.status ?? 200;
      const text = route.rawText ?? JSON.stringify(route.body ?? {});
      return Promise.resolve(new Response(text, { status }));
    }
    return Promise.resolve(
      new Response(JSON.stringify({ detail: "not stubbed" }), { status: 404 }),
    );
  }) as typeof window.fetch;
}

const COMPONENTS = {
  catalog: ModelCatalogUI,
  mapping: ModelToArtifactMappingUI,
  compat: CapabilityCompatibilityUI,
  cost: CostProfileSearchPanel,
} as const;

function mount(kind: Kind, props: Record<string, unknown>) {
  createRoot(document.getElementById("root")!).render(
    createElement(COMPONENTS[kind] as never, props as never),
  );
}

(window as unknown as { UX05: unknown }).UX05 = { stubFetch, mount };
