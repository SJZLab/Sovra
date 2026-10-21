import { useId, type ButtonHTMLAttributes, type CSSProperties } from "react";

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
    border: "2px solid var(--color-action)",
  },
  danger: {
    background: "var(--color-danger)",
    color: "var(--color-action-text)",
    border: "2px solid var(--color-danger)",
  },
  secondary: {
    background: "transparent",
    color: "var(--color-text)",
    border: "2px solid var(--color-muted)",
  },
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
  const reason = disabledReason?.trim();
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
