import type { InputHTMLAttributes } from "react";

export interface FieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  id: string;
  /** Visible label above the field; a placeholder is never a label (NN/g forms). */
  label: string;
  helper?: string;
  error?: string;
  /** Required or optional is always marked, consistently. */
  requirement: "required" | "optional";
}

export function Field({ id, label, helper, error, requirement, ...rest }: FieldProps) {
  const helperId = helper ? `${id}-helper` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [helperId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div>
      <label htmlFor={id}>
        {label} <span>{requirement === "required" ? "(required)" : "(optional)"}</span>
      </label>
      {helper ? <div id={helperId}>{helper}</div> : null}
      <input
        {...rest}
        id={id}
        aria-required={requirement === "required" ? true : undefined}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
      />
      {error ? (
        <div id={errorId} role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}
