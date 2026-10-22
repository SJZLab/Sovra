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
}

export function Field({
  id,
  label,
  helper,
  error,
  requirement,
  locale = "en",
  "aria-describedby": callerDescribedBy,
  ...rest
}: FieldProps) {
  const trimmedError = cleanText(error);
  const helperId = helper ? `${id}-helper` : undefined;
  const errorId = trimmedError ? `${id}-error` : undefined;
  const describedBy = [callerDescribedBy, helperId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div lang={resolveLocale(locale)} dir={dirFor(locale)}>
      <label htmlFor={id}>
        {label}{" "}
        <span>
          {phrase(requirement === "required" ? "fieldRequired" : "fieldOptional", locale)}
        </span>
      </label>
      {helper ? <div id={helperId}>{helper}</div> : null}
      <input
        {...rest}
        id={id}
        aria-required={requirement === "required" ? true : undefined}
        aria-invalid={trimmedError ? true : undefined}
        aria-describedby={describedBy}
      />
      {trimmedError ? (
        <div id={errorId} role="alert" style={{ overflowWrap: "anywhere" }}>
          {trimmedError}
        </div>
      ) : null}
    </div>
  );
}
