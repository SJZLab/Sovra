import { useEffect, useRef } from "react";

export interface ErrorSummaryItem {
  fieldId: string;
  message: string;
}

/** Focusable summary linking to each invalid field (NN/g H9). Takes focus when errors appear. */
export function ErrorSummary({
  errors,
  title = "There is a problem",
}: {
  errors: ErrorSummaryItem[];
  title?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (errors.length > 0) ref.current?.focus();
  }, [errors.length]);
  if (errors.length === 0) return null;
  return (
    <div ref={ref} tabIndex={-1} role="alert" aria-labelledby="error-summary-title">
      <h2 id="error-summary-title">{title}</h2>
      <ul>
        {errors.map((e) => (
          <li key={e.fieldId}>
            <a
              href={`#${e.fieldId}`}
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
