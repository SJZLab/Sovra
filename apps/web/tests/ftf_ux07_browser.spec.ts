// FTF-UX07 round 2, point 1 (QUEUE 0d; UX_STANDARD.md §4 / FINE_TUNING_FACTORY_RELEASE_1.md §72.5):
// the real-Chromium evidence for the OIDC login entry point -- en/ar x light/dark x 1280/768/390/320,
// 200 % zoom, keyboard-only Tab with a visible :focus-visible outline, axe wcag2a..22aa with zero
// violations, and a realistic long custom label. The component has no data fetch, so a static
// server-rendered page (see tests/browser/harness_page.tsx for why that is faithful) is enough.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeAll, describe, expect, test } from "vitest";

import { OidcLoginButton } from "../components/identity-and-tenancy/oidc_login";

const TOKENS_CSS = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const AXE_SOURCE = readFileSync(resolve(__dirname, "../node_modules/axe-core/axe.min.js"), "utf8");

const API = "http://localhost:8000";
const LONG_LABEL =
  "Sign in with the single sign-on account issued by your organization's identity provider today";

const LOCALES = ["en", "ar"] as const;
const SCHEMES = ["light", "dark"] as const;
const VIEWPORTS = [
  { width: 1280, height: 900 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
  { width: 320, height: 640 },
] as const;

function html(locale: "en" | "ar", label?: string): string {
  const body = renderToStaticMarkup(
    createElement(
      "main",
      { style: { padding: "16px" } },
      createElement(OidcLoginButton, { apiBaseUrl: API, locale, label }),
    ),
  );
  return `<!doctype html>
<html lang="${locale}" dir="${locale === "ar" ? "rtl" : "ltr"}">
<head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>FTF-UX07 evidence page</title><style>${TOKENS_CSS}</style></head>
<body>${body}</body></html>`;
}

describe("[UX07-BROWSER] real-Chromium §72.5 evidence", () => {
  let browser: Browser;

  beforeAll(async () => {
    browser = await chromium.launch();
  }, 60_000);

  afterAll(async () => {
    await browser.close();
  });

  async function open(
    locale: "en" | "ar",
    scheme: "light" | "dark",
    vp: { width: number; height: number },
    label?: string,
  ): Promise<Page> {
    const page = await browser.newPage({ viewport: vp, colorScheme: scheme });
    await page.setContent(html(locale, label), { waitUntil: "load" });
    return page;
  }

  const overflow = (page: Page) =>
    page.evaluate(() => {
      const el = document.documentElement;
      return el.scrollWidth - el.clientWidth;
    });

  for (const locale of LOCALES) {
    for (const scheme of SCHEMES) {
      for (const vp of VIEWPORTS) {
        const name = `${locale}/${scheme}/${vp.width}`;

        test(`[${name}] no horizontal overflow with a long label; link and hint are visible in the viewport`, async () => {
          const page = await open(locale, scheme, vp, LONG_LABEL);
          try {
            expect(await overflow(page), `${name} overflow`).toBeLessThanOrEqual(1);
            for (const id of ["oidc-login-link", "oidc-login-hint"]) {
              const box = await page.getByTestId(id).evaluate((el) => {
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                return {
                  left: r.left,
                  right: r.right,
                  width: r.width,
                  height: r.height,
                  display: cs.display,
                  visibility: cs.visibility,
                  clientWidth: document.documentElement.clientWidth,
                  top: r.top,
                  opacity: Number.parseFloat(cs.opacity),
                  fontSize: Number.parseFloat(cs.fontSize),
                  pointerEvents: cs.pointerEvents,
                  clip: cs.clip,
                  clipPath: cs.clipPath,
                  textIndent: Number.parseFloat(cs.textIndent),
                  color: cs.color,
                  scrollW: el.scrollWidth,
                  clientW: (el as HTMLElement).clientWidth,
                  scrollH: el.scrollHeight,
                  clientH: (el as HTMLElement).clientHeight,
                  // Effective background: first non-transparent ancestor-or-self background.
                  bg: (() => {
                    for (let n: Element | null = el; n; n = n.parentElement) {
                      const b = getComputedStyle(n).backgroundColor;
                      if (b !== "rgba(0, 0, 0, 0)" && b !== "transparent") return b;
                    }
                    return "rgb(255, 255, 255)";
                  })(),
                };
              });
              expect(box.opacity, `${id} opacity`).toBe(1);
              expect(box.fontSize, `${id} font-size`).toBeGreaterThanOrEqual(12);
              expect(box.top, `${id} top edge in viewport`).toBeGreaterThanOrEqual(0);
              expect(box.pointerEvents, `${id} pointer-events`).not.toBe("none");
              expect(box.clip, `${id} clip`).toBe("auto");
              expect(box.clipPath, `${id} clip-path`).toBe("none");
              expect(box.textIndent, `${id} text-indent`).toBeGreaterThanOrEqual(0);
              expect(box.color, `${id} text colour is opaque`).not.toMatch(/rgba\(.*,\s*0\)$/);
              expect(box.color, `${id} colour differs from its background`).not.toBe(box.bg);
              expect(box.scrollW, `${id} content not clipped horizontally`).toBeLessThanOrEqual(
                box.clientW + 1,
              );
              expect(box.scrollH, `${id} content not clipped vertically`).toBeLessThanOrEqual(
                box.clientH + 1,
              );
              expect(box.display, `${id} display`).not.toBe("none");
              expect(box.visibility, `${id} visibility`).toBe("visible");
              expect(box.width, `${id} width`).toBeGreaterThan(0);
              expect(box.height, `${id} height`).toBeGreaterThan(0);
              expect(box.left, `${id} left edge in viewport`).toBeGreaterThanOrEqual(-1);
              expect(box.right, `${id} right edge in viewport`).toBeLessThanOrEqual(
                box.clientWidth + 1,
              );
            }
          } finally {
            await page.close();
          }
        });

        test(`[${name}] axe wcag2a..22aa reports zero violations`, async () => {
          const page = await open(locale, scheme, vp, LONG_LABEL);
          try {
            await page.addScriptTag({ content: AXE_SOURCE });
            const results = await page.evaluate(async () => {
              // @ts-expect-error -- axe is injected globally by the script tag above.
              return await axe.run(document, {
                runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
              });
            });
            const violations = (results as { violations: { id: string }[] }).violations;
            expect(violations, JSON.stringify(violations, null, 2)).toEqual([]);
          } finally {
            await page.close();
          }
        });
      }

      test(`[${locale}/${scheme}] keyboard-only: one Tab reaches the link and shows a visible :focus-visible outline`, async () => {
        const page = await open(locale, scheme, VIEWPORTS[0]);
        try {
          await page.keyboard.press("Tab");
          const stop = await page.evaluate(() => {
            const el = document.activeElement as HTMLElement | null;
            if (!el) return null;
            const cs = getComputedStyle(el);
            return {
              testid: el.getAttribute("data-testid"),
              style: cs.outlineStyle,
              width: Number.parseFloat(cs.outlineWidth),
              color: cs.outlineColor,
            };
          });
          expect(stop?.testid).toBe("oidc-login-link");
          expect(stop?.style).not.toBe("none");
          expect(stop?.width).toBeGreaterThanOrEqual(2);
        } finally {
          await page.close();
        }
      });

      test(`[${locale}/${scheme}] 200 % zoom: no horizontal overflow, link still fully visible`, async () => {
        const page = await open(locale, scheme, VIEWPORTS[0], LONG_LABEL);
        try {
          await page.evaluate(() => {
            (document.documentElement.style as unknown as { zoom: string }).zoom = "2";
          });
          expect(await overflow(page)).toBeLessThanOrEqual(1);
          await page.evaluate(() => {
            (document.documentElement.style as unknown as { zoom: string }).zoom = "4";
          });
          expect(await overflow(page), "400 % zoom overflow").toBeLessThanOrEqual(1);
        } finally {
          await page.close();
        }
      });
    }

    test(`[${locale}] lang and dir are set on the document region and the hint is announced as the link's description`, async () => {
      const page = await open(locale, "light", VIEWPORTS[0]);
      try {
        const info = await page.evaluate(() => {
          const root = document.querySelector("[data-testid=oidc-login]") as HTMLElement;
          const link = document.querySelector("[data-testid=oidc-login-link]") as HTMLElement;
          const hint = document.querySelector("[data-testid=oidc-login-hint]") as HTMLElement;
          return {
            lang: root.lang,
            dir: root.dir,
            describedBy: link.getAttribute("aria-describedby"),
            hintId: hint.id,
          };
        });
        expect(info.lang).toBe(locale);
        expect(info.dir).toBe(locale === "ar" ? "rtl" : "ltr");
        expect(info.describedBy).toBe(info.hintId);
      } finally {
        await page.close();
      }
    });
  }

  test("[blank-label] a blank label renders the default accessible name and axe link-name stays clean", async () => {
    const page = await open("en", "light", VIEWPORTS[0], "   ");
    try {
      await page.addScriptTag({ content: AXE_SOURCE });
      const results = await page.evaluate(async () => {
        // @ts-expect-error -- axe is injected globally by the script tag above.
        return await axe.run(document, { runOnly: { type: "rule", values: ["link-name"] } });
      });
      expect((results as { violations: unknown[] }).violations).toEqual([]);
      expect(await page.getByTestId("oidc-login-link").innerText()).toBe("Sign in");
    } finally {
      await page.close();
    }
  });
});
