import { useId } from "react";

import { dirFor, phrase, type Locale } from "./phrases";

/**
 * A request-level error: announced (`role="alert"`) with the message itself isolated as data, and
 * a recovery step next to it so the user is never left at a dead end (NN/g error-message rules;
 * H9). The recovery step is a sibling, not part of the alert's text -- the alert's own text stays
 * exactly the message -- and the alert points at it with `aria-describedby`.
 */
export function AlertMessage({
  message,
  locale = "en",
  testId,
}: {
  message: string;
  locale?: Locale;
  testId?: string;
}) {
  const recoveryId = useId();
  return (
    <>
      <p
        role="alert"
        data-testid={testId}
        dir={dirFor(locale)}
        aria-describedby={recoveryId}
        style={{ overflowWrap: "anywhere" }}
      >
        <bdi>{message}</bdi>
      </p>
      <p
        id={recoveryId}
        data-testid={testId ? `${testId}-recovery` : undefined}
        dir={dirFor(locale)}
        style={{ overflowWrap: "anywhere" }}
      >
        {phrase("errorRecoveryStep", locale)}
      </p>
    </>
  );
}
