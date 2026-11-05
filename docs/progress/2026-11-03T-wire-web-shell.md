# Wiring: web shell and the first journey (2026-11-03)

Finding (real, from the code at main 7601335b): 30 of 30 backend routers are mounted in
apps/api/app/main.py, but 25 of 28 UI components under apps/web/components are imported by
nothing. apps/web had no page or route entry, so no screen could reach them.

Decision: the architecture already fixes the UI as Next.js + TypeScript (task packets FTF-0002,
FTF-0003, FTF-0004, FTF-0005, FTF-0006, FTF-R001, FTF-R017, FTF-H011). Next.js is therefore
adopted without an owner ask (default resolution rule). Version: next@14.2.34 (released
2025-12-11, React 18 compatible; 14.2.35 is too new for the two-week rule and is not used).

Done in this change:
- app/layout.tsx, app/page.tsx (front door at "/", Law 6), app/artifact-mapping/page.tsx
  (journey page that mounts ModelToArtifactMappingUI).
- Verified in a real Chromium: form on "/" -> /artifact-mapping?organizationId&connectionId&modelName
  -> "Load mapping" sends GET /api/v1/organizations/{org}/connections/litellm/{conn}/models/{model}/training-artifact
  (route defined in apps/api/app/litellm/model_catalog_mapping_routes.py). No API was running, so
  the component showed its network-error state.
- next build, typecheck and eslint pass.

Still unwired (24 components): see the list produced by the wiring map; each becomes its own
journey page.

## Revision after CI (2026-11-04)
- CI found critical and high advisories on next@14.2.34 (OSV gate, 15 findings). The 14.x line has no fixed release for them; the fixes start in 15.x. Pinned next@15.5.28 (released 2026-10-14).
- Next 15's App Router requires React 19. The repo is on React 18, and the peer range of 15.5.28 accepts 18.2+. Moved the two pages to the Pages Router (`pages/index.tsx`, `pages/artifact-mapping.tsx`) so React stays 18.3.1 with no framework-wide change.
- `next build` rewrites apps/web/tsconfig.json to `jsx: preserve`. Vite 8 then fails to parse TSX in the Vitest suites. `vitest.config.ts` sets `oxc.jsx.runtime = automatic`, so unit tests compile JSX regardless of that tsconfig value.
- Local checks: typecheck, eslint and Vitest unit suites pass (27 files pass; the 8 failing files are browser specs that need a Playwright browser binary this sandbox does not have, and CI installs its own).

## Second batch: five more journeys (2026-11-05)
- Journeys now wired, each mounted from "/" with its parameters: model-catalog (ModelCatalogUI),
  capability-compatibility (CapabilityCompatibilityUI), dataset-upload (ResumableDatasetUploadUI),
  requirements (RequirementsPanel), project-setup (ProjectSetupForm).
- One registry (apps/web/lib/journeys.ts) drives the front-door forms and the shared shell
  (apps/web/lib/JourneyPage.tsx). A journey needs NEXT_PUBLIC_API_BASE_URL and its parameters;
  without them it shows the reason, never a silent default.
- Browser check (Chromium, local build, no API running): all six journeys mount with zero page errors.
- Defect fixed: RequirementsPanel fired its loads with `void` and no catch, so an unreachable API
  became an unhandled rejection with no message shown (Law 17). The loads and the budget submit now
  catch network failures and show them in the panel's own error slots.
- Still unwired: CostProfileSearchPanel, OIDC login, GitHub target-application components, the
  dataset-issue and dataset-source components, the evaluation-case import, and the candidate and
  manifest review components. Each still needs its journey.
