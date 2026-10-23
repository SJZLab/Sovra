import { phrase, type Locale } from "./phrases";

/**
 * Text for a `fetch` or `response.json()` call that threw. A body that is not valid JSON surfaces
 * as a `SyntaxError` whose message quotes the parser and the start of the body; that text is never
 * shown (it is English, it is not for the user, and it echoes server content) -- the localized
 * malformed-response message is shown instead. Any other thrown error keeps the existing
 * "network error: <reason>" form, with a localized reason when the error carries none.
 */
export function networkErrorMessage(caught: unknown, locale: Locale): string {
  if (caught instanceof SyntaxError) return phrase("malformedResponseMessage", locale);
  const reason =
    caught instanceof Error && caught.message
      ? caught.message
      : phrase("networkErrorUnknown", locale);
  return `${phrase("networkErrorPrefix", locale)} ${reason}`;
}
