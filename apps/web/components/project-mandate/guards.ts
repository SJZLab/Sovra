/** FTF-UX04: the small type guards the strict reply parsers of both components are built from. */

export function isString(value: unknown): value is string {
  return typeof value === "string";
}

export function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

/** A string that is one of `set` (a closed vocabulary). Anything else, including a non-string, is out. */
export function isOneOf<T extends string>(set: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (set as readonly string[]).includes(value);
}
