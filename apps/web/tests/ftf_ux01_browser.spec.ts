// FTF-UX01 round 2, item 7 (Control-plane ruling, PR #898 return): the real §72.5 checks
// (viewport 1280/768/390, keyboard-only focus order and :focus-visible, 200% zoom) plus an
// automated accessibility scan now run against a REAL browser (the sandbox's/CI's pre-installed
// Chromium via Playwright), not jsdom's approximation. See harness_page.tsx for why this is a
// static server-rendered page rather than a hydrated one, and why that's still a faithful real-
// browser proof for exactly these checks.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import { buildHarnessHtml } from "./browser/harness_page";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
] as const;

describe("[UX01-BROWSER] real-Chromium §72.5 evidence", () => {
  let browser: Browser;

  beforeAll(async () => {
    browser = await chromium.launch();
  }, 60_000);

  afterAll(async () => {
    await browser.close();
  });

  async function openHarness(locale: "en" | "ar" = "en"): Promise<Page> {
    const page = await browser.newPage();
    await page.setContent(buildHarnessHtml(TOKENS_CSS, locale), { waitUntil: "load" });
    return page;
  }

  for (const vp of VIEWPORTS) {
    test(`[${vp.name}] no horizontal overflow at ${vp.width}x${vp.height} (§72.5)`, async () => {
      const page = await openHarness();
      try {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        const overflow = await page.evaluate(() => {
          const el = document.documentElement;
          return el.scrollWidth - el.clientWidth;
        });
        expect(overflow, `${vp.name} horizontal overflow`).toBeLessThanOrEqual(1);
      } finally {
        await page.close();
      }
    });
  }

  test("[zoom-200] no horizontal overflow at 1280x900 and 200% zoom (§72.5)", async () => {
    const page = await openHarness();
    try {
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.evaluate(() => {
        (document.documentElement.style as unknown as { zoom: string }).zoom = "2";
      });
      const overflow = await page.evaluate(() => {
        const el = document.documentElement;
        return el.scrollWidth - el.clientWidth;
      });
      expect(overflow, "200% zoom horizontal overflow").toBeLessThanOrEqual(1);
    } finally {
      await page.close();
    }
  });

  test("[keyboard] Tab reaches every interactive control and each stop shows a real :focus-visible outline", async () => {
    const page = await openHarness();
    try {
      await page.setViewportSize({ width: 1280, height: 900 });
      const interactiveCount = await page.evaluate(
        () => document.querySelectorAll("button, a[href], input, [tabindex]").length,
      );
      expect(interactiveCount).toBeGreaterThan(5);
      const stops: { tag: string; outlineWidth: string; outlineStyle: string }[] = [];
      for (let i = 0; i < interactiveCount + 2; i++) {
        await page.keyboard.press("Tab");
        const stop = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          if (!el || el === document.body) return null;
          const cs = getComputedStyle(el);
          return { tag: el.tagName, outlineWidth: cs.outlineWidth, outlineStyle: cs.outlineStyle };
        });
        if (stop) stops.push(stop);
      }
      // Every real stop (not body, not a dead end) shows a non-zero, non-"none" outline --
      // the tokens.css `:focus-visible` rule, not a browser default removed by some other rule.
      expect(stops.length).toBeGreaterThanOrEqual(interactiveCount);
      for (const s of stops) {
        expect(s.outlineStyle, `${s.tag} outline-style`).not.toBe("none");
        expect(Number.parseFloat(s.outlineWidth), `${s.tag} outline-width`).toBeGreaterThan(0);
      }
    } finally {
      await page.close();
    }
  });

  test("[keyboard-fault] a fault removing the focus-visible rule is caught (negative control)", async () => {
    const page = await browser.newPage();
    try {
      // Same harness, but with the common real-world regression that actually defeats this
      // check: a reset rule that strips every outline (removing tokens.css's own rule alone
      // is not enough to prove the point -- Chromium's UA stylesheet still outlines a <button>
      // on its own, so the fault that matters is the one every CSS-reset library ships).
      const faultCss = `${TOKENS_CSS}\n* { outline: none !important; }`;
      expect(faultCss).not.toBe(TOKENS_CSS);
      await page.setContent(buildHarnessHtml(faultCss, "en"), { waitUntil: "load" });
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.keyboard.press("Tab");
      const outline = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el) return null;
        const cs = getComputedStyle(el);
        return { outlineWidth: cs.outlineWidth, outlineStyle: cs.outlineStyle };
      });
      expect(outline).not.toBeNull();
      const isVisible =
        outline!.outlineStyle !== "none" && Number.parseFloat(outline!.outlineWidth) > 0;
      expect(isVisible, "fault must remove the visible outline").toBe(false);
    } finally {
      await page.close();
    }
  });

  test("[axe] automated accessibility scan reports no serious/critical violation", async () => {
    const page = await openHarness();
    try {
      await page.addScriptTag({ content: AXE_SOURCE });
      const results = await page.evaluate(async () => {
        // @ts-expect-error -- axe is injected globally by the script tag above.
        return await axe.run(document, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag22aa"] },
        });
      });
      const serious = (
        results as { violations: { impact: string; id: string }[] }
      ).violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
    } finally {
      await page.close();
    }
  });

  test("[axe-fault] a missing accessible name is caught by the same scan (negative control)", async () => {
    const page = await browser.newPage();
    try {
      await page.setContent(`<!doctype html><html><body><button></button></body></html>`, {
        waitUntil: "load",
      });
      await page.addScriptTag({ content: AXE_SOURCE });
      const results = await page.evaluate(async () => {
        // @ts-expect-error -- axe is injected globally by the script tag above.
        return await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a"] } });
      });
      const violations = (results as { violations: { id: string }[] }).violations;
      expect(violations.some((v) => v.id === "button-name")).toBe(true);
    } finally {
      await page.close();
    }
  });

  test("[rtl] Arabic locale sets dir=rtl and renders the Arabic phrases", async () => {
    const page = await openHarness("ar");
    try {
      const dir = await page.evaluate(() => document.documentElement.getAttribute("dir"));
      expect(dir).toBe("rtl");
      const hasArabicStatus = await page.evaluate(() =>
        document.body.textContent?.includes("قيد التشغيل"),
      );
      expect(hasArabicStatus).toBe(true);
    } finally {
      await page.close();
    }
  });
});
