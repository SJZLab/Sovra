/** Zero-width space/joiners, word joiner and BOM: they render as nothing but defeat `trim()`. */
const INVISIBLE = new RegExp("[\\u200b-\\u200d\\u2060\\ufeff]", "g");

/** Caller-supplied text with invisible characters removed and the ends trimmed; `""` means blank. */
export function cleanText(s: string | undefined): string {
  return (s ?? "").replace(INVISIBLE, "").trim();
}
