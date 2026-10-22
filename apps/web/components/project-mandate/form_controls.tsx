/**
 * FTF-UX04: the two form controls the UX01 `Field` (a single-line `<input>`) does not cover, built
 * to the same contract so a project form reads as one system (NN/g H4): a visible label above the
 * control, "(required)" / "(optional)" always marked, helper text and the inline error associated
 * through `aria-describedby`, the error announced (`role="alert"`) and wrapped so an unbroken value
 * can never push the page sideways. Plus the status line shown after a successful save.
 */
import type { ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

import { dirFor, phrase, resolveLocale, type Locale } from "../ui";
import { cleanText } from "../ui/text";

interface ControlProps {
  id: string;
  label: string;
  helper?: string;
  error?: string;
  requirement: "required" | "optional";
  locale?: Locale;
}

function describedBy(
  caller: string | undefined,
  helperId: string | undefined,
  errorId: string | undefined,
): string | undefined {
  return [caller, helperId, errorId].filter(Boolean).join(" ") || undefined;
}

function Label({ id, label, requirement, locale }: ControlProps) {
  return (
    <label htmlFor={id}>
      {label}{" "}
      <span>{phrase(requirement === "required" ? "fieldRequired" : "fieldOptional", locale)}</span>
    </label>
  );
}

const WRAP = { overflowWrap: "anywhere" } as const;

export function TextAreaField({
  id,
  label,
  helper,
  error,
  requirement,
  locale = "en",
  "aria-describedby": callerDescribedBy,
  style,
  ...rest
}: ControlProps & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id">) {
  const trimmedError = cleanText(error);
  const helperId = helper ? `${id}-helper` : undefined;
  const errorId = trimmedError ? `${id}-error` : undefined;
  return (
    <div lang={resolveLocale(locale)} dir={dirFor(locale)}>
      <Label id={id} label={label} requirement={requirement} locale={locale} />
      {helper ? (
        <div id={helperId} style={WRAP}>
          {helper}
        </div>
      ) : null}
      <textarea
        {...rest}
        id={id}
        rows={rest.rows ?? 4}
        aria-required={requirement === "required" ? true : undefined}
        aria-invalid={trimmedError ? true : undefined}
        aria-describedby={describedBy(callerDescribedBy, helperId, errorId)}
        style={{ inlineSize: "100%", maxInlineSize: "100%", boxSizing: "border-box", ...style }}
      />
      {trimmedError ? (
        <div id={errorId} role="alert" style={WRAP}>
          {trimmedError}
        </div>
      ) : null}
    </div>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

export function SelectField({
  id,
  label,
  helper,
  error,
  requirement,
  locale = "en",
  options,
  lockedReason,
  disabled,
  "aria-describedby": callerDescribedBy,
  ...rest
}: ControlProps &
  Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> & {
    options: SelectOption[];
    /** NN/g H5: a control that cannot be changed says why, next to the control. */
    lockedReason?: string;
  }) {
  const trimmedError = cleanText(error);
  const reason = cleanText(lockedReason);
  const helperId = helper ? `${id}-helper` : undefined;
  const reasonId = reason ? `${id}-locked` : undefined;
  const errorId = trimmedError ? `${id}-error` : undefined;
  return (
    <div lang={resolveLocale(locale)} dir={dirFor(locale)}>
      <Label id={id} label={label} requirement={requirement} locale={locale} />
      {helper ? (
        <div id={helperId} style={WRAP}>
          {helper}
        </div>
      ) : null}
      <select
        {...rest}
        id={id}
        disabled={Boolean(disabled || reason)}
        aria-required={requirement === "required" ? true : undefined}
        aria-invalid={trimmedError ? true : undefined}
        aria-describedby={describedBy(
          callerDescribedBy,
          [helperId, reasonId].filter(Boolean).join(" ") || undefined,
          errorId,
        )}
        style={{ maxInlineSize: "100%", minBlockSize: "var(--target-min)" }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {reason ? (
        <div id={reasonId} data-testid={`${id}-locked-reason`} style={WRAP}>
          {reason}
        </div>
      ) : null}
      {trimmedError ? (
        <div id={errorId} role="alert" style={WRAP}>
          {trimmedError}
        </div>
      ) : null}
    </div>
  );
}

/**
 * A success or progress line. `role="status"` (polite): it confirms what the user just did without
 * pulling focus. The icon is decorative; the words carry the meaning (never colour or icon alone).
 * `testId` sits on the words only, so a test reads exactly the message.
 */
export function StatusLine({
  children,
  locale = "en",
  testId,
  icon = "✓",
}: {
  children: ReactNode;
  locale?: Locale;
  testId?: string;
  icon?: string;
}) {
  return (
    <div role="status" lang={resolveLocale(locale)} dir={dirFor(locale)}>
      <span aria-hidden="true">{icon}</span> <span data-testid={testId}>{children}</span>
    </div>
  );
}
