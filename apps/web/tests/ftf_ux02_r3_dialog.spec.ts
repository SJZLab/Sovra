// @vitest-environment jsdom
//
// FTF-UX02 round 3, finding 1 (QUEUE 0k): the shared UX01 Dialog gets its OWN unit tests (it was
// only exercised through FTF-0501-UI). Real-layout geometry (in-viewport, hit-testable, en/ar) is
// proven in real Chromium by ftf_ux02_r3_browser.spec.ts; this file pins the markup contract.
import { createElement, useState, type ReactElement } from "react";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import { Dialog } from "../components/ui/Dialog";

afterEach(() => cleanup());

function Harness({ locale }: { locale?: "en" | "ar" }): ReactElement {
  const [open, setOpen] = useState(true);
  return createElement(
    "div",
    null,
    createElement("button", { type: "button", "data-testid": "opener" }, "open"),
    createElement(
      Dialog,
      {
        open,
        onOpenChange: setOpen,
        title: "Abandon this draft?",
        description: "This cannot be undone.",
        locale,
        "data-testid": "dlg",
      },
      createElement("button", { type: "button" }, "Keep"),
    ),
  );
}

function dialog(): HTMLElement {
  return screen.getByTestId("dlg");
}

test("[UX02-R3-D1] declares aria-modal and an accessible name and description that resolve to real elements", () => {
  render(createElement(Harness));
  const dlg = dialog();
  expect(dlg.getAttribute("aria-modal")).toBe("true");
  const labelledBy = dlg.getAttribute("aria-labelledby");
  const describedBy = dlg.getAttribute("aria-describedby");
  expect(labelledBy, "aria-labelledby present").toBeTruthy();
  expect(describedBy, "aria-describedby present").toBeTruthy();
  // Advisory from the verifier: the describedby id must not dangle.
  expect(document.getElementById(labelledBy!)?.textContent).toBe("Abandon this draft?");
  expect(document.getElementById(describedBy!)?.textContent).toBe("This cannot be undone.");
});

test("[UX02-R3-D2] is centred without a direction-dependent offset (no translate(-50%) and no 50% inline-start)", () => {
  render(createElement(Harness, { locale: "ar" }));
  const style = dialog().getAttribute("style") ?? "";
  expect(style, "no translate shift (shifts left in RTL)").not.toMatch(/translate/i);
  expect(style, "no 50% inline offset (becomes right: 50% in RTL)").not.toMatch(
    /inset-inline-start:\s*50%/i,
  );
  expect(style).toMatch(/inset:\s*0/);
  expect(style).toMatch(/margin:\s*auto/);
});

test("[UX02-R3-D3] carries lang and dir for the locale, and an Arabic close label", () => {
  render(createElement(Harness, { locale: "ar" }));
  const dlg = dialog();
  expect(dlg.getAttribute("lang")).toBe("ar");
  expect(dlg.getAttribute("dir")).toBe("rtl");
  expect(screen.getByTestId("dialog-close").getAttribute("aria-label")).toBe("إلغاء");
});

test("[UX02-R3-D4] the close control is a UX01 Button (variant, minimum target) and closes the dialog", async () => {
  const user = userEvent.setup();
  render(createElement(Harness));
  const close = screen.getByTestId("dialog-close");
  expect(close.getAttribute("data-variant")).toBe("secondary");
  expect(close.getAttribute("style") ?? "").toMatch(/var\(--target-min\)/);
  await user.click(close);
  expect(screen.queryByTestId("dlg")).toBeNull();
});

test("[UX02-R3-D5] Escape closes the dialog", async () => {
  const user = userEvent.setup();
  render(createElement(Harness));
  await user.keyboard("{Escape}");
  expect(screen.queryByTestId("dlg")).toBeNull();
});
