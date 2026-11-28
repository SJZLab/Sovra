// FTF-UX02 round 2, point 7 (§72.5 real-browser evidence). Client-side entry bundled in memory by
// apps/web/tests/ftf_ux02_browser.spec.ts and injected into a real Chromium page. Same approach as
// apps/web/tests/browser/ux03_entry.tsx (sibling slice, same shift): MOUNTS the real dataset-factory
// components so loading, empty, populated, preview, authorization, error and the abandon-confirmation
// dialog exist in a real layout engine -- in particular, the Dialog's focus TRAP and focus RETURN
// (FTF-UX02 round-2 finding 1), which jsdom's `document.activeElement` tracking does not reliably
// model for a Radix `FocusScope` (see ftf_0501_ui.spec.ts's own jsdom focus-trap test and its comment
// on why focus-RETURN is proven here instead). `fetch` is replaced by a route table the spec passes in;
// nothing here is a component stand-in.
import { createElement, type ComponentType } from "react";
import { createRoot } from "react-dom/client";

import { DatasetSourceSelectorPurposeAuthorizationUi } from "../../components/dataset-factory/dataset_source_selector_purpose_authorization_ui";
import { ResumableDatasetUploadUI } from "../../components/dataset-factory/resumable_dataset_upload_ui";

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
  "0500A": DatasetSourceSelectorPurposeAuthorizationUi as unknown as ComponentType<
    Record<string, unknown>
  >,
  "0501-UI": ResumableDatasetUploadUI as unknown as ComponentType<Record<string, unknown>>,
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

function seedLocalStorage(items?: Record<string, string>): void {
  if (!items) return;
  for (const [key, value] of Object.entries(items)) {
    window.localStorage.setItem(key, value);
  }
}

function mount(
  name: string,
  props: Record<string, unknown>,
  storageItems?: Record<string, string>,
) {
  window.localStorage.clear();
  seedLocalStorage(storageItems);
  const component = REGISTRY[name];
  if (!component) throw new Error(`unknown component ${name}`);
  createRoot(document.getElementById("root")!).render(createElement(component, props));
}

(window as unknown as { UX02: unknown }).UX02 = { stubFetch, mount };
