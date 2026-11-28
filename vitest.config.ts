import { defineConfig } from "vitest/config";

// FTF-0003: real Vitest unit-test harness for apps/web and packages/shared.
// passWithNoTests stays false (Vitest's own default) so an empty test run
// fails validation rather than silently reporting success.
export default defineConfig({
  // Next.js rewrites apps/web/tsconfig.json to jsx "preserve" on every build. Vitest must still
  // transform TSX to runnable JS, so JSX is compiled with the automatic runtime here (Vite 8 uses oxc).
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    include: [
      "apps/web/src/**/*.test.ts",
      "packages/shared/src/**/*.test.ts",
      "apps/web/tests/**/*.spec.ts",
    ],
    passWithNoTests: false,
    // The real-Chromium specs (*_browser.spec.ts) open several pages per test and wait up to 10 s for
    // each one; Vitest's 5 s default cut them off on a loaded CI runner (red gates on 2026-11-28).
    // A test that hangs still fails, at 30 s.
    testTimeout: 30_000,
    // FTF-UX01: the design tokens are read as text (`?raw`) so contrast is computed from the real file;
    // Vitest blanks every other stylesheet.
    css: { include: [/styles\/tokens\.css/] },
  },
});
