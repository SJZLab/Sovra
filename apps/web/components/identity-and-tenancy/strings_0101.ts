import { resolveLocale, type Locale } from "../ui/phrases";

/** FTF-0101 login copy in both languages (standard system phrases, CLAUDE.md "Do without asking"). */
const STRINGS = {
  signIn: { en: "Sign in", ar: "تسجيل الدخول" },
  hint: {
    en: "You will be sent to your organization's identity provider to sign in, then returned here.",
    ar: "ستنتقل إلى مزود الهوية الخاص بمؤسستك لتسجيل الدخول، ثم تعود إلى هنا.",
  },
} as const satisfies Record<string, Record<Locale, string>>;

export function loginString(key: keyof typeof STRINGS, locale: string = "en"): string {
  return STRINGS[key][resolveLocale(locale)];
}
