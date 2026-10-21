import { useEffect, useId, useRef } from "react";

import { dirFor, phrase, type Locale } from "./phrases";

export interface ErrorSummaryItem {
  fieldId: string;
  message: string;
}

/**
 * Focusable summary linking to each invalid field (NN/g H9). Takes focus on every failed
 * submit, even a repeat with the same error count — keyed on the errors array identity, which
 * a caller creates fresh on every submit attempt, not on its length.
 */
export function ErrorSummary({
  errors,
  title,
  locale = "en",
}: {
  errors: ErrorSummaryItem[];
  title?: string;
  locale?: Locale;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (errors.length > 0) ref.current?.focus();
    // Intentionally depends on the `errors` array identity, not its length: a second failed
    // submit with the same number of errors (but a fresh array) must re-take focus too.
  }, [errors]);
  if (errors.length === 0) return null;
  const resolvedTitle = title?.trim() || phrase("errorSummaryTitle", locale);
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
