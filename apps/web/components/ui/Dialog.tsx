import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { Button } from "./Button";
import { dirFor, phrase, resolveLocale, type Locale } from "./phrases";

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Visible heading (NN/g H9/H5: the specific consequence, not a generic "Are you sure?"). */
  title: string;
  /** The consequence text, wired as `aria-describedby` so assistive tech announces it with the title. */
  description: ReactNode;
  children: ReactNode;
  locale?: Locale;
  /** `alertdialog` (the default) for a destructive confirmation (NN/g H5); `dialog` otherwise. */
  role?: "dialog" | "alertdialog";
  "data-testid"?: string;
  /**
   * The element focus returns to on close. Radix's own default `onCloseAutoFocus` target only
   * works reliably when the opener is its own `Dialog.Trigger`; a caller that opens this dialog
   * from a plain button's `onClick` (every caller so far) passes `event.currentTarget` here
   * instead -- confirmed empirically (apps/web/tests/ftf_ux02_browser.spec.ts, real Chromium)
   * that without it, focus lands on `<body>` on close, not back on the opener.
   */
  restoreFocusTo?: HTMLElement | null;
}

/**
 * FTF-UX01 shared confirmation dialog on Radix `Dialog` primitives (section 72.1): a real focus
 * trap while open and focus RETURNED to the element that opened it on close (NN/g H3/WCAG 2.4.3),
 * Escape closes it, and the overlay/content never let focus escape to the page behind it.
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  locale = "en",
  role = "alertdialog",
  "data-testid": testId,
  restoreFocusTo,
}: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15, 23, 42, 0.5)",
          }}
        />
        <RadixDialog.Content
          role={role}
          aria-modal="true"
          lang={resolveLocale(locale)}
          dir={dirFor(locale)}
          data-testid={testId}
          onEscapeKeyDown={(event) => {
            // An alertdialog is still dismissible by Escape (NN/g H3): it only
            // changes nothing destructive, same as the explicit "keep" action.
            event.preventDefault();
            onOpenChange(false);
          }}
          onCloseAutoFocus={(event) => {
            // A caller that also unmounts the opener in the same update (e.g. a destructive
            // confirm that both closes this dialog and discards the section it lives in) hands
            // back a now-disconnected node -- `.focus()` on it is a silent no-op, so skip it and
            // let the caller's own post-close effect place focus instead of fighting it.
            if (restoreFocusTo && document.contains(restoreFocusTo)) {
              event.preventDefault();
              restoreFocusTo.focus();
            }
          }}
          style={{
            // FTF-UX02 r3 finding 1 (DIALOG-RTL-OFFSCREEN): centre with `inset: 0` + `margin: auto`
            // on a sized box. `insetInlineStart: 50%` + `translate(-50%)` resolves to `right: 50%`
            // in RTL while the translate always shifts left, drawing the dialog half off-screen.
            // Auto margins centre identically in both directions.
            position: "fixed",
            inset: 0,
            margin: "auto",
            inlineSize: "min(92vw, 480px)",
            blockSize: "fit-content",
            maxBlockSize: "90vh",
            overflowY: "auto",
            overflowWrap: "anywhere",
            boxSizing: "border-box",
            background: "var(--color-surface)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border)",
            borderRadius: "4px",
            padding: "var(--space-5)",
            display: "grid",
            gap: "var(--space-3)",
          }}
        >
          <RadixDialog.Title style={{ margin: 0 }}>
            {title}
          </RadixDialog.Title>
          <RadixDialog.Description style={{ margin: 0 }}>
            {description}
          </RadixDialog.Description>
          {children}
          <RadixDialog.Close asChild>
            <Button
              aria-label={phrase("cancel", locale)}
              data-testid="dialog-close"
              style={{ position: "absolute", insetInlineEnd: "8px", insetBlockStart: "8px" }}
            >
              {"×"}
            </Button>
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
