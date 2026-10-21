import { useEffect, useId, useRef } from "react";

import { dirFor, phrase, resolveLocale, type Locale } from "./phrases";
import { cleanText } from "./text";

export interface ErrorSummaryItem {
  fieldId: string;
  message: string;
}

/**
 * Focusable summary linking to each invalid field (NN/g H9). Takes focus ONLY on an explicit submit
 * signal: when it mounts with errors, and each time the caller raises `submitCount` (one per failed
 * submit, in the same update that hands over the errors, so a repeat failure with the same errors
 * still takes focus). It never moves focus because `errors` changed identity (a caller that builds
 * the list during render hands over a fresh array on every keystroke) and never because the list
 * went from empty to non-empty without a submit (typing or tabbing out of a field that becomes
 * invalid): pulling the user out of the field they are in drops their typing. A caller that never
 * passes `submitCount` gets focus on mount only.
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
    // `ref` is null while there is nothing to show, so a submit with no errors focuses nothing.
    ref.current?.focus();
  }, [submitCount]);
  if (!hasErrors) return null;
  const resolvedTitle = cleanText(title) || phrase("errorSummaryTitle", locale);
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="alert"
      aria-labelledby={titleId}
      lang={resolveLocale(locale)}
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
