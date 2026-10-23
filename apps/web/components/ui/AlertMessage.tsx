import { useId } from "react";

import type { UiError } from "./errors";
import { dirFor, phrase, type Locale } from "./phrases";

const RECOVERY_PHRASE = {
  retry: "errorRecoveryStep",
  "verify-state": "recoveryVerifyState",
  permission: "recoveryPermission",
  conflict: "recoveryConflict",
  input: "recoveryInput",
} as const;

/**
 * A request-level error: announced (`role="alert"`) with the message itself isolated as data, and
 * a recovery step that fits the failure next to it so the user is never left at a dead end (NN/g
 * error-message rules; H9). The recovery step is a sibling, not part of the alert's text -- the
 * alert's own text stays exactly the message -- and the alert points at it with
 * `aria-describedby`.
 */
export function AlertMessage({
  error,
  locale = "en",
  testId,
  focusable = false,
}: {
  error: UiError;
  locale?: Locale;
  testId?: string;
  /** Programmatically focusable (`tabIndex=-1`), for a caller that moves focus here on failure. */
  focusable?: boolean;
}) {
  const recoveryId = useId();
  return (
    <>
      <p
        role="alert"
        data-testid={testId}
        dir={dirFor(locale)}
        aria-describedby={recoveryId}
        tabIndex={focusable ? -1 : undefined}
        style={{ overflowWrap: "anywhere" }}
      >
        <bdi>{error.message}</bdi>
      </p>
      <p
        id={recoveryId}
        data-testid={testId ? `${testId}-recovery` : undefined}
        dir={dirFor(locale)}
        style={{ overflowWrap: "anywhere" }}
      >
        {phrase(RECOVERY_PHRASE[error.kind], locale)}
      </p>
    </>
  );
}
