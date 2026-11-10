// FTF-UX03 round 2, point 7 (§72.5 real-browser evidence). Client-side entry bundled in memory by
// apps/web/tests/ftf_ux03_browser.spec.ts and injected into a real Chromium page. Unlike the static
// UX01 harness page, this MOUNTS the five real target-application components so that loading, empty,
// populated, error, conflict and read-only states exist in a real layout engine. `fetch` is replaced
// by a route table the spec passes in; nothing here is a component stand-in.
import { createElement, type ComponentType } from "react";
import { createRoot } from "react-dom/client";

import { CandidateSelectionUI } from "../../components/target-application/candidate_selection_ui_no_auto_selection";
import { GitHubAccessibleRepositorySelector } from "../../components/target-application/github_accessible_repository_selector_ui";
import { GitHubBranchRefSelector } from "../../components/target-application/github_branch_ref_selector_immutable_sha_resolver";
import { MandateEditorAndConflictResolutionUI } from "../../components/target-application/mandate_editor_and_conflict_resolution_ui";
import { ManifestReviewEditUI } from "../../components/target-application/manifest_review_edit_ui_with_per_field_evidence";

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
  "04A03": GitHubAccessibleRepositorySelector as unknown as ComponentType<Record<string, unknown>>,
  "04A04": GitHubBranchRefSelector as unknown as ComponentType<Record<string, unknown>>,
  "04A18": CandidateSelectionUI as unknown as ComponentType<Record<string, unknown>>,
  "04A21": ManifestReviewEditUI as unknown as ComponentType<Record<string, unknown>>,
  "04A22": MandateEditorAndConflictResolutionUI as unknown as ComponentType<
    Record<string, unknown>
  >,
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

function mount(
  name: string,
  props: Record<string, unknown>,
  storageItems?: Record<string, string>,
) {
  const component = REGISTRY[name];
  if (!component) throw new Error(`unknown component ${name}`);
  const withStorage = storageItems
    ? {
        ...props,
        storage: {
          getItem: (key: string) => storageItems[key] ?? null,
          setItem: () => undefined,
          removeItem: () => undefined,
        },
      }
    : props;
  createRoot(document.getElementById("root")!).render(createElement(component, withStorage));
}

(window as unknown as { UX03: unknown }).UX03 = { stubFetch, mount };
