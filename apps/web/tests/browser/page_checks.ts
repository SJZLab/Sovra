// FTF-UX04: the check families of the real-Chromium evidence harness (UX_STANDARD.md section 4 /
// FINE_TUNING_FACTORY_RELEASE_1.md section 72.5), shared by the UX04 browser spec. Same checks as
// the UX03 harness (ftf_ux03_browser.spec.ts), parameterized on the component root instead of a
// component name: axe (WCAG 2.2 AA tags), viewports 1280/768/390/320, 200 % zoom, RTL attributes,
// keyboard order with a visible, contrasting focus indicator, no trap, Enter and Space on a button.
import type { Page } from "@playwright/test";

export type Locale = "en" | "ar";
export type Scheme = "light" | "dark";

export const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
  { name: "narrow", width: 320, height: 640 },
] as const;
export const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

export async function axeProblems(page: Page): Promise<string[]> {
  const violations = (await page.evaluate(async (tags) => {
    // @ts-expect-error -- axe is injected globally by the script tag above.
    const r = await axe.run(document, { runOnly: { type: "tag", values: tags } });
    return r.violations.map(
      (v: {
        id: string;
        impact: string;
        nodes: { target: unknown; failureSummary?: string }[];
      }) => ({
        id: v.id,
        impact: v.impact,
        nodes: v.nodes
          .slice(0, 3)
          .map((n) => `${JSON.stringify(n.target)} ${n.failureSummary?.split("\n")[1] ?? ""}`),
      }),
    );
  }, AXE_TAGS)) as { id: string; impact: string; nodes: string[] }[];
  return violations.map((v) => `axe ${v.id} (${v.impact}): ${v.nodes.join(" | ")}`);
}

export async function overflowProblems(page: Page): Promise<string[]> {
  const problems: string[] = [];
  const measure = () =>
    page.evaluate(() => {
      const el = document.documentElement;
      return el.scrollWidth - el.clientWidth;
    });
  for (const vp of VIEWPORTS) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    const over = await measure();
    if (over > 1) problems.push(`overflow ${vp.name} ${vp.width}x${vp.height}: ${over}px`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => {
    (document.documentElement.style as unknown as { zoom: string }).zoom = "2";
  });
  const zoomed = await measure();
  if (zoomed > 1) problems.push(`overflow at 200% zoom: ${zoomed}px`);
  await page.evaluate(() => {
    (document.documentElement.style as unknown as { zoom: string }).zoom = "1";
  });
  return problems;
}

export async function rtlProblems(
  page: Page,
  rootTestId: string,
  locale: Locale,
): Promise<string[]> {
  const attrs = await page.evaluate((id) => {
    const root = document.querySelector(`[data-testid="${id}"]`);
    return root ? { dir: root.getAttribute("dir"), lang: root.getAttribute("lang") } : null;
  }, rootTestId);
  if (!attrs) return [`rtl: component root ${rootTestId} missing`];
  const want = { dir: locale === "ar" ? "rtl" : "ltr", lang: locale };
  const problems: string[] = [];
  if (attrs.dir !== want.dir) problems.push(`rtl: root dir=${attrs.dir}, want ${want.dir}`);
  if (attrs.lang !== want.lang) problems.push(`rtl: root lang=${attrs.lang}, want ${want.lang}`);
  if (locale === "ar") {
    const direction = await page.evaluate(
      (id) => getComputedStyle(document.querySelector(`[data-testid="${id}"]`)!).direction,
      rootTestId,
    );
    if (direction !== "rtl") problems.push(`rtl: computed direction=${direction}`);
  }
  return problems;
}

interface Stop {
  index: number;
  label: string;
  outlineStyle: string;
  outlineWidth: number;
  contrast: number;
}

export async function keyboardProblems(
  page: Page,
  noControlsExpected: boolean,
): Promise<{ problems: string[]; stops: number }> {
  const count = await page.evaluate(() => {
    const visible = (el: Element) => {
      const cs = getComputedStyle(el);
      return el.getClientRects().length > 0 && cs.visibility !== "hidden" && cs.display !== "none";
    };
    const list = Array.from(
      document.querySelectorAll<HTMLElement>(
        "button, a[href], input, textarea, select, [tabindex]",
      ),
    ).filter((el) => {
      if ((el as HTMLButtonElement).disabled || !visible(el)) return false;
      // A native control must be reachable by Tab even if a stray tabindex="-1" removed it from the
      // order; a non-native element (a focus target such as the error summary) only counts when it
      // opts in with tabindex >= 0.
      const native = el.matches("button, a[href], input, textarea, select");
      return native || el.tabIndex >= 0;
    });
    (window as unknown as { __tabList: HTMLElement[] }).__tabList = list;
    return list.length;
  });
  const problems: string[] = [];
  if (count === 0) {
    // A loading state offers nothing to operate; any other state with no control is a dead end.
    return {
      problems: noControlsExpected ? [] : ["keyboard: no focusable control in this state"],
      stops: 0,
    };
  }
  // Reset the sequential-focus starting point: a click in the state setup leaves it on that control.
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    document.body.setAttribute("tabindex", "-1");
    document.body.focus();
    document.body.removeAttribute("tabindex");
  });
  const stops: (Stop | null)[] = [];
  for (let i = 0; i < count; i++) {
    await page.keyboard.press("Tab");
    stops.push(
      await page.evaluate(() => {
        const list = (window as unknown as { __tabList: HTMLElement[] }).__tabList;
        const el = document.activeElement as HTMLElement | null;
        if (!el) return null;
        const index = list.indexOf(el);
        const cs = getComputedStyle(el);
        const parse = (c: string) => (c.match(/[\d.]+/g) ?? []).map(Number);
        const lum = ([r, g, b]: number[]) => {
          const f = (v: number) => {
            const s = (v ?? 0) / 255;
            return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
          };
          return 0.2126 * f(r!) + 0.7152 * f(g!) + 0.0722 * f(b!);
        };
        let bg: number[] = parse(getComputedStyle(document.body).backgroundColor);
        for (let n: HTMLElement | null = el.parentElement; n; n = n.parentElement) {
          const c = parse(getComputedStyle(n).backgroundColor);
          if ((c[3] ?? 1) > 0) {
            bg = c;
            break;
          }
        }
        const fg = parse(cs.outlineColor);
        const l1 = lum(fg);
        const l2 = lum(bg);
        const contrast = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        return {
          index,
          label: `${el.tagName.toLowerCase()}${el.getAttribute("data-testid") ? `[${el.getAttribute("data-testid")}]` : ""}`,
          outlineStyle: cs.outlineStyle,
          outlineWidth: Number.parseFloat(cs.outlineWidth),
          contrast,
        };
      }),
    );
  }
  stops.forEach((stop, i) => {
    if (!stop || stop.index !== i) {
      problems.push(
        `keyboard: Tab stop ${i} landed on ${stop ? `${stop.label} (list index ${stop.index})` : "nothing"}, want control ${i} in DOM order`,
      );
      return;
    }
    if (stop.outlineStyle === "none" || !(stop.outlineWidth > 0)) {
      problems.push(`focus indicator: ${stop.label} has no visible outline`);
    } else if (stop.contrast < 3) {
      problems.push(
        `focus indicator: ${stop.label} outline contrast ${stop.contrast.toFixed(2)} < 3:1`,
      );
    }
  });
  // No focus trap: one more Tab leaves the last control.
  if (count > 1) {
    await page.keyboard.press("Tab");
    const trapped = await page.evaluate(() => {
      const list = (window as unknown as { __tabList: HTMLElement[] }).__tabList;
      return document.activeElement === list[list.length - 1];
    });
    if (trapped) problems.push("keyboard: focus is trapped on the last control");
  }
  // Enter and Space activate a Button.
  const buttonIndex = await page.evaluate(() => {
    const list = (window as unknown as { __tabList: HTMLElement[] }).__tabList;
    return list.findIndex((el) => el.tagName === "BUTTON" && el.getAttribute("type") !== "submit");
  });
  if (buttonIndex >= 0) {
    await page.evaluate((i) => {
      const w = window as unknown as { __tabList: HTMLElement[]; __clicks: number };
      w.__clicks = 0;
      const target = w.__tabList[i]!;
      document.addEventListener(
        "click",
        (e) => {
          if (e.target instanceof Node && target.contains(e.target)) {
            w.__clicks += 1;
            e.preventDefault();
            e.stopPropagation();
          }
        },
        true,
      );
      target.focus();
    }, buttonIndex);
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");
    const clicks = await page.evaluate(() => (window as unknown as { __clicks: number }).__clicks);
    if (clicks !== 2)
      problems.push(`keyboard: Enter+Space activated the button ${clicks}x, want 2`);
  }
  return { problems, stops: count };
}

/**
 * No `[role="region"]` (the keyboard-scrollable table wrapper) needs a horizontal scrollbar at any
 * viewport: the page not overflowing is not enough if the table scrolls inside its own region, which
 * hides columns on a phone. User data wraps instead (WCAG 1.4.10 reflow, section 72.5).
 */
export async function regionScrollProblems(page: Page): Promise<string[]> {
  const problems: string[] = [];
  for (const vp of VIEWPORTS) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    const wide = await page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>('[role="region"]'))
        .filter((el) => el.scrollWidth - el.clientWidth > 1)
        .map(
          (el) =>
            `${el.getAttribute("aria-label") ?? "region"} ${el.scrollWidth - el.clientWidth}px`,
        ),
    );
    for (const w of wide) problems.push(`inner scroll ${vp.name} ${vp.width}px: ${w}`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  return problems;
}
