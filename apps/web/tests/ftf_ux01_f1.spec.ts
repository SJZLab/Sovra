// @vitest-environment jsdom
//
// FTF-UX01-F1 (follow-up on CERTIFIED FTF-UX01/FTF-UX05; found by the FTF-UX05 r3 verifier, #983).
// Direct unit tests for the shared error/focus library: AlertMessage, errors.ts, focus.ts and
// singleflight.ts. Each [UX01-F1-*] test was run RED against the pre-fix code.
import { createElement, useState, type ReactElement } from "react";

import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { AlertMessage } from "../components/ui/AlertMessage";
import { kindForStatus, networkError, uiError, type RecoveryKind } from "../components/ui/errors";
import { useRefocusAfter } from "../components/ui/focus";
import { PHRASES, phrase } from "../components/ui/phrases";
import { useSingleFlight } from "../components/ui/singleflight";

afterEach(() => cleanup());

const KINDS: RecoveryKind[] = [
  "retry",
  "verify-state",
  "permission",
  "signin",
  "conflict",
  "input",
  "rejected",
  "throttled",
];

// ---- (1) AlertMessage: a kind outside the union must not throw during render -------------------

test("[UX01-F1-1] a kind outside the union renders the retry recovery instead of throwing", () => {
  for (const bogus of ["toString", "constructor", "__proto__", "hasOwnProperty", "bogus", ""]) {
    cleanup();
    render(
      createElement(AlertMessage, {
        error: { message: "boom", kind: bogus as RecoveryKind },
        testId: "al",
      }),
    );
    expect(screen.getByTestId("al").textContent).toBe("boom");
    expect(screen.getByTestId("al-recovery").textContent).toBe(phrase("errorRecoveryStep", "en"));
  }
});

test("[UX01-F1-1b] every real kind keeps its own recovery sentence, in both locales", () => {
  const expected: Record<RecoveryKind, keyof typeof PHRASES> = {
    retry: "errorRecoveryStep",
    "verify-state": "recoveryVerifyState",
    permission: "recoveryPermission",
    signin: "recoverySignIn",
    rejected: "recoveryRejected",
    throttled: "recoveryThrottled",
    conflict: "recoveryConflict",
    input: "recoveryInput",
  };
  for (const locale of ["en", "ar"] as const) {
    for (const kind of KINDS) {
      cleanup();
      render(createElement(AlertMessage, { error: uiError("m", kind), locale, testId: "al" }));
      expect(screen.getByTestId("al-recovery").textContent).toBe(phrase(expected[kind], locale));
    }
  }
});

// ---- (2) networkError: no English pass-through under locale=ar --------------------------------

test("[UX01-F1-2] a non-fetch Error's English message is never shown under locale=ar", () => {
  const err = networkError(new Error("ECONNRESET while talking to upstream"), "ar");
  expect(err.message).toBe(
    `${phrase("networkErrorPrefix", "ar")} ${phrase("networkErrorUnknown", "ar")}`,
  );
  expect(err.message).not.toContain("ECONNRESET");
  expect(err.kind).toBe("retry");
});

test("[UX01-F1-2b] under locale=en the existing 'network error: <reason>' form is kept", () => {
  expect(networkError(new Error("ECONNRESET"), "en").message).toBe("network error: ECONNRESET");
  expect(networkError(new Error(""), "en").message).toBe("network error: unknown error");
  expect(networkError("not an error", "en").message).toBe("network error: unknown error");
});

test("[UX01-F1-2c] TypeError and SyntaxError stay localized and read/write-aware", () => {
  for (const locale of ["en", "ar"] as const) {
    expect(networkError(new TypeError("Failed to fetch"), locale).message).toBe(
      `${phrase("networkErrorPrefix", locale)} ${phrase("networkErrorUnreachable", locale)}`,
    );
    const read = networkError(new SyntaxError("Unexpected token < in JSON"), locale);
    expect(read).toEqual({ message: phrase("malformedResponseMessage", locale), kind: "retry" });
    const write = networkError(new SyntaxError("x"), locale, { write: true });
    expect(write).toEqual({
      message: phrase("malformedWriteResponseMessage", locale),
      kind: "verify-state",
    });
    expect(networkError(new TypeError("x"), locale, { write: true }).kind).toBe("verify-state");
    expect(networkError(new Error("x"), locale, { write: false }).kind).toBe("retry");
  }
});

// ---- (4) kindForStatus ------------------------------------------------------------------------

test("[UX01-F1-4] kindForStatus maps every status class to its recovery kind", () => {
  const table: Array<[number, RecoveryKind]> = [
    [400, "rejected"],
    [401, "signin"],
    [403, "permission"],
    [404, "rejected"],
    [409, "conflict"],
    [422, "rejected"],
    [429, "throttled"],
    [200, "retry"],
    [405, "retry"],
    [500, "retry"],
    [502, "retry"],
    [503, "retry"],
    [0, "retry"],
  ];
  for (const [status, kind] of table) expect(kindForStatus(status), String(status)).toBe(kind);
});

test("[UX01-F1-4b] uiError defaults to retry and keeps the message verbatim", () => {
  expect(uiError("m")).toEqual({ message: "m", kind: "retry" });
  expect(uiError("m", "input")).toEqual({ message: "m", kind: "input" });
});

// ---- (3) singleflight.ts ----------------------------------------------------------------------

test("[UX01-F1-3a] useSingleFlight refuses a second begin until end, then admits again", () => {
  const { result } = renderHook(() => useSingleFlight());
  expect(result.current.begin()).toBe(true);
  expect(result.current.begin()).toBe(false);
  expect(result.current.begin()).toBe(false);
  result.current.end();
  expect(result.current.begin()).toBe(true);
  result.current.end();
  result.current.end(); // idempotent
  expect(result.current.begin()).toBe(true);
});

test("[UX01-F1-3b] useSingleFlight keeps state across re-renders (a ref, not per-render)", () => {
  const { result, rerender } = renderHook(() => useSingleFlight());
  expect(result.current.begin()).toBe(true);
  rerender();
  expect(result.current.begin()).toBe(false);
});

// ---- (3) focus.ts -----------------------------------------------------------------------------

function RefocusHarness({
  showSecond = true,
  withFallback = false,
}: {
  showSecond?: boolean;
  withFallback?: boolean;
}): ReactElement {
  const [busy, setBusy] = useState(false);
  const [mounted, setMounted] = useState(showSecond);
  const [, setTicks] = useState(0);
  const arm = useRefocusAfter(busy);
  return createElement(
    "div",
    null,
    createElement("button", { id: "fb", "data-testid": "fb" }, "fallback"),
    mounted
      ? createElement(
          "button",
          {
            id: "go",
            "data-testid": "go",
            disabled: busy,
            onClick: (e: { currentTarget: HTMLElement }) => {
              arm(withFallback ? () => document.getElementById("fb") : undefined);
              e.currentTarget.blur(); // Chromium drops focus to <body> when the control disables
              setBusy(true);
            },
          },
          "go",
        )
      : null,
    createElement("button", { "data-testid": "finish", onClick: () => setBusy(false) }, "finish"),
    createElement(
      "button",
      { "data-testid": "start-unarmed", onClick: () => setBusy(true) },
      "start without arming",
    ),
    createElement(
      "button",
      {
        "data-testid": "arm-only",
        onClick: (e: { currentTarget: HTMLElement }) => {
          arm();
          e.currentTarget.blur();
        },
      },
      "arm only",
    ),
    createElement(
      "button",
      { "data-testid": "tick", onClick: () => setTicks((n) => n + 1) },
      "tick",
    ),
    createElement("button", { "data-testid": "unmount", onClick: () => setMounted(false) }, "um"),
  );
}

function focusAndArm(testId: string) {
  const el = screen.getByTestId(testId);
  el.focus();
  expect(document.activeElement).toBe(el);
  return el;
}

test("[UX01-F1-3c] focus returns to the control that had it once busy goes false and focus is on <body>", () => {
  render(createElement(RefocusHarness));
  const go = focusAndArm("go");
  act(() => go.click()); // arm + busy=true (button disables)
  expect(document.activeElement).toBe(document.body);
  act(() => screen.getByTestId("finish").click());
  expect(document.activeElement).toBe(go);
});

test("[UX01-F1-3d] it never steals focus: nothing happens when focus is elsewhere at that moment", () => {
  render(createElement(RefocusHarness));
  const go = focusAndArm("go");
  act(() => go.click());
  const finish = screen.getByTestId("finish");
  finish.focus();
  act(() => finish.click());
  expect(document.activeElement).toBe(finish);
});

test("[UX01-F1-3e] an unmounted control hands focus to the fallback; with no fallback nothing is focused", () => {
  const { unmount } = render(createElement(RefocusHarness, { withFallback: true }));
  let go = focusAndArm("go");
  act(() => go.click());
  act(() => screen.getByTestId("unmount").click());
  expect(document.activeElement).toBe(document.body);
  act(() => screen.getByTestId("finish").click());
  expect(document.activeElement).toBe(screen.getByTestId("fb"));
  unmount();

  render(createElement(RefocusHarness, { withFallback: false }));
  go = focusAndArm("go");
  act(() => go.click());
  act(() => screen.getByTestId("unmount").click());
  act(() => screen.getByTestId("finish").click());
  expect(document.activeElement).toBe(document.body);
});

test("[UX01-F1-3f] arming while <body> has focus arms nothing; the armed target is consumed once", () => {
  render(createElement(RefocusHarness));
  const go = screen.getByTestId("go");
  expect(document.activeElement).toBe(document.body);
  act(() => go.click()); // click() does not focus: arm sees <body> -> no target
  act(() => screen.getByTestId("finish").click());
  expect(document.activeElement).toBe(document.body);

  // consumed once: a second busy->false without re-arming does not refocus
  const go2 = focusAndArm("go");
  act(() => go2.click());
  act(() => screen.getByTestId("finish").click());
  expect(document.activeElement).toBe(go2);
  act(() => screen.getByTestId("fb").focus());
  document.body.focus();
  (document.activeElement as HTMLElement | null)?.blur();
  expect(document.activeElement).toBe(document.body);
  act(() => screen.getByTestId("finish").click()); // busy already false: no state change
  expect(document.activeElement).toBe(document.body);
});

test("[UX01-F1-3g] the armed target is consumed by one refocus: a later busy cycle that did not arm refocuses nothing", () => {
  render(createElement(RefocusHarness));
  const go = focusAndArm("go");
  act(() => go.click());
  act(() => screen.getByTestId("finish").click());
  expect(document.activeElement).toBe(go); // refocused once

  const start = screen.getByTestId("start-unarmed");
  start.focus();
  act(() => start.click()); // busy=true, nothing armed this cycle
  (document.activeElement as HTMLElement).blur();
  expect(document.activeElement).toBe(document.body);
  act(() => screen.getByTestId("finish").click());
  expect(document.activeElement).toBe(document.body); // a stale target must not pull focus back
});

test("[UX01-F1-3h] a still-connected control gets focus back even when a fallback was given", () => {
  render(createElement(RefocusHarness, { withFallback: true }));
  const go = focusAndArm("go");
  act(() => go.click());
  act(() => screen.getByTestId("finish").click());
  expect(document.activeElement).toBe(go);
  expect(document.activeElement).not.toBe(screen.getByTestId("fb"));
});

test("[UX01-F1-3i] only a busy true->false transition refocuses: an unrelated re-render with a target armed does nothing", () => {
  render(createElement(RefocusHarness));
  const armOnly = focusAndArm("arm-only");
  act(() => armOnly.click()); // armed on arm-only, focus dropped to <body>, busy never flipped
  expect(document.activeElement).toBe(document.body);
  act(() => screen.getByTestId("tick").click()); // re-render, busy unchanged (false)
  expect(document.activeElement).toBe(document.body);
});
