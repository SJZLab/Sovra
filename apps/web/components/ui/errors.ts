import { phrase, type Locale } from "./phrases";

/**
 * What the user should do next. Each kind has its own recovery sentence (`AlertMessage`): a retry
 * is wrong advice for a 403 (ask for access), a 409 (reload first), a missing selection (select
 * it), or a write whose outcome is unknown (check the current state before re-running it).
 */
export type RecoveryKind =
  | "retry"
  | "verify-state"
  | "permission"
  | "signin"
  | "conflict"
  | "input"
  | "rejected"
  | "throttled";

export interface UiError {
  message: string;
  kind: RecoveryKind;
}

export function uiError(message: string, kind: RecoveryKind = "retry"): UiError {
  return { message, kind };
}

/** The recovery kind a failed HTTP status implies. */
export function kindForStatus(status: number): RecoveryKind {
  if (status === 401) return "signin";
  if (status === 403) return "permission";
  if (status === 409) return "conflict";
  if (status === 429) return "throttled";
  if (status === 400 || status === 404 || status === 422) return "rejected";
  return "retry";
}

/**
 * Error for a `fetch` or `response.json()` call that threw.
 *
 * - A body that is not valid JSON surfaces as a `SyntaxError` whose message quotes the parser and
 *   the start of the body; that text is never shown (English, not for the user, echoes server
 *   content). For a read the localized malformed-response message says nothing was changed. For a
 *   WRITE the server answered 200, so the write (or a paid probe) very likely happened: the
 *   message says the result could not be read, and the recovery is to check the current state
 *   before running it again -- never "nothing was changed", never a blind retry.
 * - A `TypeError` is the browser's own fetch failure ("Failed to fetch" / "Load failed" /
 *   "NetworkError ..."), English text that differs per browser: a localized message replaces it.
 * - Any other thrown error keeps the existing "network error: <reason>" form, with a localized
 *   reason when the error carries none.
 */
export function networkError(
  caught: unknown,
  locale: Locale,
  options: { write?: boolean } = {},
): UiError {
  const write = options.write === true;
  const kind: RecoveryKind = write ? "verify-state" : "retry";
  if (caught instanceof SyntaxError) {
    return uiError(
      phrase(write ? "malformedWriteResponseMessage" : "malformedResponseMessage", locale),
      kind,
    );
  }
  const reason =
    caught instanceof TypeError
      ? phrase("networkErrorUnreachable", locale)
      : caught instanceof Error && caught.message
        ? caught.message
        : phrase("networkErrorUnknown", locale);
  return uiError(`${phrase("networkErrorPrefix", locale)} ${reason}`, kind);
}
