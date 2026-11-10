// FTF-UX01 round 3, point 2 (DARK-LINK-CONTRAST): the axe colour-contrast scan now also runs in
// DARK mode in a real Chromium, and the computed link colour is read from the layout engine --
// the round-2 scan ran light only, so a stylesheet with no link colour passed it.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import { buildHarnessHtml } from "./browser/harness_page";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");

type Violation = { impact: string; id: string; nodes: unknown[] };

describe("[UX01-R3-BROWSER] dark-mode evidence in real Chromium", () => {
  let browser: Browser;
  beforeAll(async () => {
    browser = await chromium.launch();
  }, 60_000);
  afterAll(async () => {
    await browser.close();
  });

  async function open(
    scheme: "light" | "dark",
    css = TOKENS_CSS,
    locale: "en" | "ar" = "en",
  ): Promise<Page> {
    const page = await browser.newPage({ colorScheme: scheme });
    await page.setContent(buildHarnessHtml(css, locale), { waitUntil: "load" });
    await page.addScriptTag({ content: AXE_SOURCE });
    return page;
  }
  async function contrastViolations(page: Page): Promise<Violation[]> {
    const results = await page.evaluate(async () => {
      // @ts-expect-error -- axe is injected globally by the script tag above.
      return await axe.run(document, { runOnly: { type: "rule", values: ["color-contrast"] } });
    });
    return (results as { violations: Violation[] }).violations;
  }

  for (const scheme of ["light", "dark"] as const) {
    test(`[axe-contrast-${scheme}] colour-contrast scan reports nothing in ${scheme} mode`, async () => {
      const page = await open(scheme);
      try {
        const v = await contrastViolations(page);
        expect(v, JSON.stringify(v, null, 2)).toEqual([]);
      } finally {
        await page.close();
      }
    });
  }

  test("[axe-contrast-dark-ar] and in dark mode with the Arabic locale", async () => {
    const page = await open("dark", TOKENS_CSS, "ar");
    try {
      const v = await contrastViolations(page);
      expect(v, JSON.stringify(v, null, 2)).toEqual([]);
    } finally {
      await page.close();
    }
  });

  test("[link-colour] the computed link colour follows the scheme and is never the browser default", async () => {
    const dark = await open("dark");
    const light = await open("light");
    try {
      const read = (p: Page) =>
        p.evaluate(() => getComputedStyle(document.querySelector("a")!).color);
      expect(await read(dark)).toBe("rgb(147, 197, 253)");
      expect(await read(light)).toBe("rgb(29, 78, 216)");
      expect(
        await dark.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
      ).toBe("light dark");
    } finally {
      await dark.close();
      await light.close();
    }
  });

  test("[axe-contrast-fault] the round-2 stylesheet (no link colour) is caught by the same dark scan", async () => {
    const round2 = TOKENS_CSS.replace(/a \{\s*color: var\(--color-link\);\s*\}/, "").replace(
      /^\s+color-scheme:[^;]+;$/gm,
      "",
    );
    expect(round2).not.toBe(TOKENS_CSS);
    expect(round2).toContain("@media (prefers-color-scheme: dark)");
    expect(round2).not.toMatch(/^\s+color-scheme:/m);
    const page = await open("dark", round2);
    try {
      const v = await contrastViolations(page);
      expect(v.some((x) => x.id === "color-contrast")).toBe(true);
    } finally {
      await page.close();
    }
  });
});
