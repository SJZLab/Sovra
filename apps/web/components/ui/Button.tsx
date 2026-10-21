import { useId, type ButtonHTMLAttributes, type CSSProperties } from "react";

import { cleanText } from "./text";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger";
  /** NN/g H5: an impossible action stays visible but disabled WITH the reason shown. */
  disabledReason?: string;
}

/** Each variant is visibly distinct (NN/g H4): primary fills with the action colour, danger
 * fills with the danger colour, secondary is an outline — never colour-only (the border/fill
 * difference itself, not hue alone, carries the distinction for colour-blind users). */
const VARIANT_STYLE: Record<NonNullable<ButtonProps["variant"]>, CSSProperties> = {
  primary: {
    background: "var(--color-action)",
    color: "var(--color-action-text)",
    borderWidth: "2px",
    borderStyle: "solid",
    borderColor: "var(--color-action)",
  },
  danger: {
    background: "var(--color-danger)",
    color: "var(--color-action-text)",
    borderWidth: "2px",
    borderStyle: "solid",
    borderColor: "var(--color-danger)",
  },
  secondary: {
    background: "transparent",
    color: "var(--color-text)",
    borderWidth: "2px",
    borderStyle: "solid",
    borderColor: "var(--color-muted)",
  },
};

/** A disabled button is visibly different without relying on colour or opacity alone (H4/H5):
 * a dashed border, the not-allowed cursor and a flat surface in place of the variant fill. */
const DISABLED_STYLE: CSSProperties = {
  background: "var(--color-surface-2)",
  color: "var(--color-muted)",
  borderStyle: "dashed",
  borderColor: "var(--color-muted)",
  cursor: "not-allowed",
};

/** Primary actions name the result ("Create dataset"), never "Submit" (UX_STANDARD 3.2). */
export function Button({
  variant = "secondary",
  disabledReason,
  disabled,
  children,
  id,
  "aria-describedby": callerDescribedBy,
  style,
  ...rest
}: ButtonProps) {
  const autoId = useId();
  const reason = cleanText(disabledReason);
  const isDisabled = Boolean(disabled || reason);
  const reasonId = reason ? `${id ?? autoId}-reason` : undefined;
  const describedBy = [callerDescribedBy, reasonId].filter(Boolean).join(" ") || undefined;
  return (
    <>
      <button
        type="button"
        {...rest}
        id={id}
        data-variant={variant}
        disabled={isDisabled}
        aria-describedby={describedBy}
        style={{
          minBlockSize: "var(--target-min)",
          minInlineSize: "var(--target-min)",
          ...VARIANT_STYLE[variant],
          ...(isDisabled ? DISABLED_STYLE : null),
          ...style,
        }}
      >
        {children}
      </button>
      {reason ? (
        <span id={reasonId} data-testid="disabled-reason">
          {reason}
        </span>
      ) : null}
    </>
  );
}
