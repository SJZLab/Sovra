import { defineConfig } from "vitest/config";

// FTF-0003: real Vitest unit-test harness for apps/web and packages/shared.
// passWithNoTests stays false (Vitest's own default) so an empty test run
// fails validation rather than silently reporting success.
export default defineConfig({
  test: {
    include: [
      "apps/web/src/**/*.test.ts",
      "packages/shared/src/**/*.test.ts",
      "apps/web/tests/**/*.spec.ts",
    ],
    passWithNoTests: false,
    // FTF-UX01: the design tokens are read as text (`?raw`) so contrast is computed from the real file;
    // Vitest blanks every other stylesheet.
    css: { include: [/styles\/tokens\.css/] },
  },
});
