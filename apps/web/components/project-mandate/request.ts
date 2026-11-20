/**
 * FTF-UX04: the one way the project-mandate components talk to the API, so a transport failure, a
 * non-2xx status and an unreadable 2xx body each become a typed `Failure` instead of an unhandled
 * rejection or a fabricated success (Law 17; UI CHECKLIST d/e). A reply that is not a 2xx with a
 * JSON body is never treated as success; the caller still has to validate the body's shape.
 */
import {
  failureFromResponse,
  MALFORMED_FAILURE,
  NETWORK_FAILURE,
  type Failure,
} from "../target-application/common";

export type Outcome = { ok: true; body: unknown } | { ok: false; failure: Failure };

export async function requestJson(url: string, init: RequestInit): Promise<Outcome> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    return { ok: false, failure: NETWORK_FAILURE };
  }
  if (!response.ok) return { ok: false, failure: await failureFromResponse(response) };
  try {
    return { ok: true, body: await response.json() };
  } catch {
    return { ok: false, failure: MALFORMED_FAILURE };
  }
}

/** Order-independent structural equality for JSON values (the server's JSONB reorders object keys). */
export function jsonEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== "object") return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, index) => jsonEqual(item, b[index]));
  }
  const left = a as Record<string, unknown>;
  const right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  if (keys.length !== Object.keys(right).length) return false;
  return keys.every((key) => Object.hasOwn(right, key) && jsonEqual(left[key], right[key]));
}
