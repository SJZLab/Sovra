import { defineConfig } from "vitest/config";

// FTF-0501-UI: real-API specs. They need a live API + PostgreSQL + S3, which
// tests/integration/test_ftf_0501_ui_real_api.py boots and passes in through
// the environment, so they are NOT part of the default vitest include
// (vitest.config.ts) and never run -- or skip -- without that harness.
export default defineConfig({
  test: {
    include: ["apps/web/tests/real_api/**/*.realapi.ts"],
    passWithNoTests: false,
    testTimeout: 180_000,
    hookTimeout: 60_000,
    fileParallelism: false,
  },
});
