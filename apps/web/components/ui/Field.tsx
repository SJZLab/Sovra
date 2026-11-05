import type { InputHTMLAttributes } from "react";

import { dirFor, phrase, resolveLocale, type Locale } from "./phrases";
import { cleanText } from "./text";

export interface FieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  id: string;
  /** Visible label above the field; a placeholder is never a label (NN/g forms). */
  label: string;
  helper?: string;
  error?: string;
  /** Required or optional is always marked, consistently. */
  requirement: "required" | "optional";
  locale?: Locale;
  /**
   * Where the required/optional marker sits. `"label"` (the default) puts it inside the label text.
   * `"hint"` keeps the label text EXACTLY the caller's string and announces the marker through
   * `aria-describedby` instead -- for a screen whose pinned suites query the control by that exact
   * label (FTF-0516), without giving up Field's block label, target size and error wiring.
   */
  requirementPlacement?: "label" | "hint";
}

export function Field({
  id,
  label,
  helper,
  error,
  requirement,
  locale = "en",
  requirementPlacement = "label",
  "aria-describedby": callerDescribedBy,
  style: callerStyle,
  ...rest
}: FieldProps) {
  const trimmedError = cleanText(error);
  const helperId = helper ? `${id}-helper` : undefined;
  const errorId = trimmedError ? `${id}-error` : undefined;
  const markerId = requirementPlacement === "hint" ? `${id}-requirement` : undefined;
  const describedBy =
    [callerDescribedBy, markerId, helperId, errorId].filter(Boolean).join(" ") || undefined;
  const marker = (
    <span id={markerId}>
      {phrase(requirement === "required" ? "fieldRequired" : "fieldOptional", locale)}
    </span>
  );
  return (
    <div lang={resolveLocale(locale)} dir={dirFor(locale)}>
      {/* FTF-UX02 round-2 pre-review round 1 (LABELS-ABOVE-FIELDS): a block label forces the
          control onto its own line below it in real layout (NN/g forms) -- DOM order alone is
          not visual layout, since two inline-level elements still sit side by side. */}
      <label htmlFor={id} style={{ display: "block", marginBlockEnd: "var(--space-1)" }}>
        {requirementPlacement === "label" ? (
          <>
            {label} {marker}
          </>
        ) : (
          label
        )}
      </label>
      {requirementPlacement === "hint" ? marker : null}
      {helper ? <div id={helperId}>{helper}</div> : null}
      <input
        {...rest}
        id={id}
        aria-required={requirement === "required" ? true : undefined}
        aria-invalid={trimmedError ? true : undefined}
        aria-describedby={describedBy}
        // WCAG 2.2 2.5.8 (target size, AA): a bare `<input>` with no caller padding/height is
        // smaller than 24x24 CSS px in every browser's UA stylesheet (round-2 pre-review's own
        // block-label fix made this fail-RED on FTF-UX06's real-Chromium axe check, which this
        // Field is also used by — same `--target-min` already applied to Button).
        style={{ minBlockSize: "var(--target-min)", ...callerStyle }}
      />
      {trimmedError ? (
        <div id={errorId} role="alert" style={{ overflowWrap: "anywhere" }}>
          {trimmedError}
        </div>
      ) : null}
    </div>
  );
}
