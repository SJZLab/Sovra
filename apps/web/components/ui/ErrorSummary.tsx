import { useEffect, useId, useRef } from "react";

import { dirFor, phrase, type Locale } from "./phrases";
import { cleanText } from "./text";

export interface ErrorSummaryItem {
  fieldId: string;
  message: string;
}

/**
 * Focusable summary linking to each invalid field (NN/g H9). Takes focus on an explicit submit
 * signal: when the summary first appears, and each time the caller raises `submitCount` (one per
 * failed submit, so a repeat failure with the same errors still takes focus). It never keys on
 * the `errors` array identity: a caller that builds the list during render hands over a fresh
 * array on every keystroke, and re-focusing then would pull the user out of the field they just
 * moved to and drop their typing.
 */
export function ErrorSummary({
  errors,
  title,
  locale = "en",
  submitCount = 0,
}: {
  errors: ErrorSummaryItem[];
  title?: string;
  locale?: Locale;
  submitCount?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const hasErrors = errors.length > 0;
  useEffect(() => {
    if (hasErrors) ref.current?.focus();
    // Only the submit signal and the appear/disappear edge, never the array identity.
  }, [submitCount, hasErrors]);
  if (!hasErrors) return null;
  const resolvedTitle = cleanText(title) || phrase("errorSummaryTitle", locale);
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="alert"
      aria-labelledby={titleId}
      lang={locale}
      dir={dirFor(locale)}
    >
      <h2 id={titleId}>{resolvedTitle}</h2>
      <ul style={{ display: "grid", gap: "var(--space-2)" }}>
        {errors.map((e) => (
          <li key={e.fieldId}>
            <a
              href={`#${e.fieldId}`}
              style={{ display: "inline-block", paddingBlock: "4px" }}
              onClick={(ev) => {
                const target = document.getElementById(e.fieldId);
                if (target) {
                  ev.preventDefault();
                  target.focus();
                }
              }}
            >
              {e.message}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
