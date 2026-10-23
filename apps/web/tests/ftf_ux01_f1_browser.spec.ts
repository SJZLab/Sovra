// FTF-UX01-F1 round 2 (verifier finding E3c): in real Chromium an aborted or timed-out `fetch`
// rejects with a DOMException that IS an `Error` (jsdom's is not, so no jsdom test reaches that
// branch). Under locale=ar its English message ("signal is aborted without reason", "The operation
// was aborted due to timeout") must never be shown: `networkError` gives the localized prefix and
// the localized unknown reason. It runs in CI inside the existing `vitest run` step.
import { resolve } from "node:path";

import { chromium, type Browser, type Page } from "@playwright/test";
import { build } from "vite";
import { afterAll, beforeAll, expect, test } from "vitest";

const ENTRY = resolve(__dirname, "browser/ux01_f1_entry.ts");

let browser: Browser;
let page: Page;

beforeAll(async () => {
  const result = await build({
    configFile: false,
    mode: "production",
    logLevel: "silent",
    root: resolve(__dirname, "../../.."),
    define: { "process.env.NODE_ENV": JSON.stringify("production") },
    build: {
      write: false,
      minify: false,
      lib: { entry: ENTRY, name: "UX01F1Bundle", formats: ["iife"], fileName: "ux01f1" },
    },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap(
    (r) => ("output" in r ? r.output : []) as { type: string; code?: string }[],
  );
  const bundle = outputs.find((o) => o.type === "chunk")?.code ?? "";
  expect(bundle.length, "the in-memory bundle must be built").toBeGreaterThan(100);
  browser = await chromium.launch();
  page = await browser.newPage();
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({ content: bundle });
}, 120_000);

afterAll(async () => {
  await browser?.close();
});

interface Probe {
  isError: boolean;
  name: string;
  ar: string;
  expected: string;
}

test("[UX01-F1-2e] Chromium abort and timeout DOMExceptions are localized under locale=ar", async () => {
  const probes = await page.evaluate(async (): Promise<Probe[]> => {
    const ui = (
      window as unknown as {
        __ux01f1: {
          networkError: (caught: unknown, locale: string) => { message: string };
          phrase: (key: string, locale: string) => string;
        };
      }
    ).__ux01f1;
    const expected = `${ui.phrase("networkErrorPrefix", "ar")} ${ui.phrase("networkErrorUnknown", "ar")}`;
    const aborted = new AbortController();
    aborted.abort();
    const abortErr = await fetch("http://127.0.0.1:9/never", { signal: aborted.signal }).catch(
      (e: unknown) => e,
    );
    const timeout = AbortSignal.timeout(1);
    await new Promise((r) => setTimeout(r, 30));
    const timeoutErr = await fetch("http://127.0.0.1:9/never", { signal: timeout }).catch(
      (e: unknown) => e,
    );
    const constructed = new DOMException("The operation was aborted.", "AbortError");
    return [abortErr, timeoutErr, constructed].map((caught) => ({
      isError: caught instanceof Error,
      name: (caught as { name?: string }).name ?? "",
      ar: ui.networkError(caught, "ar").message,
      expected,
    }));
  });
  expect(probes.map((p) => p.name)).toEqual(["AbortError", "TimeoutError", "AbortError"]);
  for (const probe of probes) {
    expect(probe.isError, `${probe.name} is an Error in Chromium`).toBe(true);
    expect(probe.ar).toBe(probe.expected);
    expect(probe.ar).not.toMatch(/[A-Za-z]/);
  }
});
