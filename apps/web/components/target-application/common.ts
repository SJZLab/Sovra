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
  other: {
    en: "The request failed ({status}). Try again; if it keeps happening, contact your administrator.",
    ar: "فشل الطلب ({status}). حاول مرة أخرى، وإذا استمرت المشكلة فتواصل مع المسؤول.",
  },
  server: {
    en: "The service had a problem ({status}). Wait a minute, then try again.",
    ar: "واجهت الخدمة مشكلة ({status}). انتظر دقيقة ثم حاول مرة أخرى.",
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
      // The backend's `detail` is never shown: it is an internal code (`role_not_authorized`) or an
      // English developer sentence, neither of which a user can act on or read in Arabic.
      if (failure.status === 401) return failureWord("unauthenticated", locale);
      if (failure.status === 403) return failureWord("forbidden", locale);
      if (failure.status >= 500) return failureWord("server", locale, { status: failure.status });
      return failureWord("other", locale, { status: failure.status });
    }
  }
}

const SAFE_WORDS = {
  notFound: {
    en: "This manifest no longer exists, or you do not have access to it. Go back and choose another one.",
    ar: "لم يعد هذا البيان موجودًا، أو ليست لديك صلاحية الوصول إليه. ارجع واختر بيانًا آخر.",
  },
  conflict: {
    en: "The manifest is not in a state that allows this: it may be locked, changed elsewhere, or missing a required part. Reload to see the current version; your edits stay in the editor until you do.",
    ar: "البيان ليس في حالة تسمح بذلك: قد يكون مقفلًا أو تم تغييره من مكان آخر أو ينقصه جزء مطلوب. أعد التحميل لرؤية النسخة الحالية؛ تبقى تعديلاتك في المحرر إلى أن تفعل ذلك.",
  },
  invalid: {
    en: "The server rejected some of what was entered. Check the values you changed, then try again.",
    ar: "رفض الخادم بعض ما تم إدخاله. تحقق من القيم التي غيّرتها ثم حاول مرة أخرى.",
  },
  other: {
    en: "The request failed ({status}). Try again; if it keeps happening, contact your administrator.",
    ar: "فشل الطلب ({status}). حاول مرة أخرى، وإذا استمرت المشكلة فتواصل مع المسؤول.",
  },
} as const satisfies Record<string, CatalogueEntry>;

const safeWord = makeT(SAFE_WORDS);

/**
 * Like `failureMessage`, but never shows the backend's own `detail`: the manifest routes answer with
 * internal codes (`not_found`) and English developer sentences (`agent manifest <uuid> is 'locked' --
 * ...`) that are neither words a user can act on nor Arabic. Each status gets a localized sentence with
 * the step that can actually succeed; an unknown status gets a generic one with the status number.
 */
export function safeFailureMessage(failure: Failure, locale: Locale | string = "en"): string {
  if (failure.kind !== "http") return failureMessage(failure, locale);
  switch (failure.status) {
    case 401:
    case 403:
    case 502:
    case 503:
    case 504:
      return failureMessage({ kind: "http", status: failure.status, detail: null }, locale);
    case 404:
      return safeWord("notFound", locale);
    case 409:
      return safeWord("conflict", locale);
    case 400:
    case 422:
      return safeWord("invalid", locale);
    default:
      return failure.status >= 500
        ? failureMessage({ kind: "http", status: failure.status, detail: null }, locale)
        : safeWord("other", locale, { status: failure.status });
  }
}

const SELECTOR_WORDS = {
  githubRejected: {
    en: "GitHub rejected this token. Use a different token with read access to the repository, then try again.",
    ar: "رفض GitHub هذا الرمز. استخدم رمزًا آخر بصلاحية قراءة المستودع ثم حاول مرة أخرى.",
  },
  notFound: {
    en: "The project, connection or repository was not found, or this token cannot see it. Check that it exists and that you have access, then try again.",
    ar: "لم يتم العثور على المشروع أو الاتصال أو المستودع، أو لا يستطيع هذا الرمز رؤيته. تحقق من وجوده ومن صلاحياتك ثم حاول مرة أخرى.",
  },
  staleSnapshot: {
    en: "A newer snapshot of this connection exists, so this selection was not recorded. Reload the candidates to work on the latest snapshot, then choose again.",
    ar: "توجد لقطة أحدث لهذا الاتصال، لذلك لم يتم تسجيل هذا الاختيار. أعد تحميل المرشحين للعمل على أحدث لقطة ثم اختر مرة أخرى.",
  },
  connectionRevoked: {
    en: "This connection was revoked and can no longer be used. Create a new connection, then resolve the ref again.",
    ar: "تم إلغاء هذا الاتصال ولم يعد قابلًا للاستخدام. أنشئ اتصالًا جديدًا ثم حلّل المرجع مرة أخرى.",
  },
  refNotFound: {
    en: "That ref, or the repository or connection it belongs to, was not found or is not visible to this token. Check the ref name first, then the repository and the token's access.",
    ar: "لم يتم العثور على هذا المرجع أو المستودع أو الاتصال، أو أنه غير مرئي لهذا الرمز. تحقق من اسم المرجع أولًا ثم من المستودع وصلاحيات الرمز.",
  },
  candidatesNotFound: {
    en: "The connection or snapshot was not found, or you do not have access to it. Reload the page, or ask an administrator.",
    ar: "لم يتم العثور على الاتصال أو اللقطة، أو ليست لديك صلاحية الوصول إليها. أعد تحميل الصفحة أو اطلب المساعدة من مسؤول.",
  },
  rateLimited: {
    en: "GitHub is limiting requests right now. Wait a few minutes, then try again.",
    ar: "يحدّ GitHub من الطلبات حاليًا. انتظر بضع دقائق ثم حاول مرة أخرى.",
  },
  invalid: {
    en: "The request was not accepted. Check the token, the branch or ref, and the page you asked for, then try again.",
    ar: "لم يتم قبول الطلب. تحقق من الرمز والفرع أو المرجع والصفحة المطلوبة ثم حاول مرة أخرى.",
  },
} as const satisfies Record<string, CatalogueEntry>;

const selectorWord = makeT(SELECTOR_WORDS);

/**
 * The message for FTF-04A03 / 04A04 / 04A18, whose routes answer with internal codes
 * (`project_not_found`, `role_not_authorized`), English developer sentences (the stale-snapshot text
 * carries a snapshot UUID) and GitHub's own wording. None of that is shown: each status gets a
 * localized sentence with the step that can succeed. A 401 is GitHub rejecting the credential unless
 * the platform itself says `not_authenticated` (the user's own session ended).
 */
export type SelectorRoute = "repositories" | "branches" | "resolve" | "candidates" | "select";

export function selectorFailureMessage(
  failure: Failure,
  locale: Locale | string = "en",
  route: SelectorRoute = "repositories",
): string {
  if (failure.kind !== "http") return failureMessage(failure, locale);
  switch (failure.status) {
    case 401:
      return failure.detail === "not_authenticated"
        ? failureMessage(failure, locale)
        : selectorWord("githubRejected", locale);
    case 404:
      // The word depends on what the user was doing: an unknown ref is about the ref, and the
      // candidate routes have no token at all.
      if (route === "resolve") {
        return failure.detail === "project_not_found"
          ? selectorWord("notFound", locale)
          : selectorWord("refNotFound", locale);
      }
      if (route === "candidates" || route === "select") {
        return selectorWord("candidatesNotFound", locale);
      }
      return selectorWord("notFound", locale);
    case 409:
      // Only recording a selection can be stale; resolving a ref 409s when the connection was revoked.
      if (route === "select") return selectorWord("staleSnapshot", locale);
      if (route === "resolve") return selectorWord("connectionRevoked", locale);
      return failureMessage(failure, locale);
    case 429:
      return selectorWord("rateLimited", locale);
    case 400:
    case 422:
      return selectorWord("invalid", locale);
    default:
      return failureMessage(failure, locale);
  }
}
