/**
 * FTF-UX03 round 2: helpers every target-application component shares so that failures, wording and
 * `lang`/`dir` behave identically (NN/g H4). Each component keeps its own string catalogue in a
 * sibling `strings_*.ts` file; this file owns only the mechanism.
 */
import { resolveLocale, type Locale } from "../ui";

export type CatalogueEntry = { readonly en: string; readonly ar: string };

/** `{name}` placeholders in a catalogue string are replaced from `vars`; nothing else is touched. */
export function fill(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : whole,
  );
}

/**
 * Build a lookup over one component's catalogue. A locale we do not ship renders English (the same
 * rule `ui/phrases.ts` applies), and only own keys are read so `toString` / `__proto__` cannot
 * resolve to an inherited function.
 */
export function makeT<K extends string>(catalogue: Record<K, CatalogueEntry>) {
  return (key: K, locale: Locale | string = "en", vars?: Record<string, string | number>) => {
    const entry: CatalogueEntry = catalogue[key];
    return fill(entry[resolveLocale(locale)], vars);
  };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** What went wrong with a request, kept structured until it is shown so the wording is localized. */
export type Failure =
  | { kind: "network" }
  | { kind: "malformed" }
  | { kind: "http"; status: number; detail: string | null };

export const NETWORK_FAILURE: Failure = { kind: "network" };
export const MALFORMED_FAILURE: Failure = { kind: "malformed" };

/** The backend's `{"detail": "<stable_code>"}` body when it has one; never throws. */
export async function failureFromResponse(response: Response): Promise<Failure> {
  let detail: string | null = null;
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === "string" && body.detail.trim() !== "") {
      detail = body.detail;
    }
  } catch {
    // an unreadable error body still yields a failure with its status
  }
  return { kind: "http", status: response.status, detail };
}

const FAILURE_WORDS = {
  network: {
    en: "Could not reach the server. Check your connection, then try again.",
    ar: "تعذّر الاتصال بالخادم. تحقق من اتصالك ثم حاول مرة أخرى.",
  },
  malformed: {
    en: "The server sent a reply this page cannot read. Try again; if it keeps happening, contact your administrator.",
    ar: "أرسل الخادم ردًا لا يمكن لهذه الصفحة قراءته. حاول مرة أخرى، وإذا استمرت المشكلة فتواصل مع المسؤول.",
  },
  unauthenticated: {
    en: "Your session has ended. Sign in again, then retry.",
    ar: "انتهت جلستك. سجّل الدخول مرة أخرى ثم أعد المحاولة.",
  },
  forbidden: {
    en: "You do not have access to this. Ask an organization administrator to grant it, then retry.",
    ar: "ليست لديك صلاحية الوصول إلى هذا. اطلب من مسؤول المؤسسة منحك الصلاحية ثم أعد المحاولة.",
  },
  server: {
    en: "The service had a problem ({status}). Wait a minute, then try again.",
    ar: "واجهت الخدمة مشكلة ({status}). انتظر دقيقة ثم حاول مرة أخرى.",
  },
  refused: {
    en: "The server refused the request: {detail}. Check what you entered, then try again.",
    ar: "رفض الخادم الطلب: {detail}. تحقق مما أدخلته ثم حاول مرة أخرى.",
  },
  refusedNoDetail: {
    en: "The request failed ({status}). Check what you entered, then try again.",
    ar: "فشل الطلب ({status}). تحقق مما أدخلته ثم حاول مرة أخرى.",
  },
} as const satisfies Record<string, CatalogueEntry>;

const failureWord = makeT(FAILURE_WORDS);

/** A sentence that says what happened AND what to do next (NN/g H9); never blank. */
export function failureMessage(failure: Failure, locale: Locale | string = "en"): string {
  switch (failure.kind) {
    case "network":
      return failureWord("network", locale);
    case "malformed":
      return failureWord("malformed", locale);
    case "http": {
      // The backend's own reason wins on a 4xx: a 401 from GitHub's credential check is not "your
      // session ended", and hiding the reason would send the user to fix the wrong thing.
      if (failure.status >= 500) return failureWord("server", locale, { status: failure.status });
      if (failure.detail) return failureWord("refused", locale, { detail: failure.detail });
      if (failure.status === 401) return failureWord("unauthenticated", locale);
      if (failure.status === 403) return failureWord("forbidden", locale);
      return failureWord("refusedNoDetail", locale, { status: failure.status });
    }
  }
}
