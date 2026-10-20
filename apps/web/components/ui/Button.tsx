import type { ButtonHTMLAttributes } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger";
  /** NN/g H5: an impossible action stays visible but disabled WITH the reason shown. */
  disabledReason?: string;
}

/** Primary actions name the result ("Create dataset"), never "Submit" (UX_STANDARD 3.2). */
export function Button({
  variant = "secondary",
  disabledReason,
  disabled,
  children,
  ...rest
}: ButtonProps) {
  const isDisabled = Boolean(disabled || disabledReason);
  const reasonId = disabledReason ? `${rest.id ?? "btn"}-reason` : undefined;
  return (
    <>
      <button
        type="button"
        {...rest}
        data-variant={variant}
        disabled={isDisabled}
        aria-describedby={reasonId}
        style={{
          minBlockSize: "var(--target-min)",
          minInlineSize: "var(--target-min)",
          ...rest.style,
        }}
      >
        {children}
      </button>
      {disabledReason ? (
        <span id={reasonId} data-testid="disabled-reason">
          {disabledReason}
        </span>
      ) : null}
    </>
  );
}
