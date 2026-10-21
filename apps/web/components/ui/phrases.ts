/** Standard system phrases in both languages (CLAUDE.md "Do without asking"; UX_STANDARD.md §3). */
export type Locale = "en" | "ar";

export const PHRASES = {
  abandonDraft: { en: "Abandon draft", ar: "تجاهل المسودة" },
  keepDraft: { en: "Keep draft", ar: "الاحتفاظ بالمسودة" },
  abandonConfirmBody: {
    en: "Abandon this draft? The partly uploaded file is discarded and a new upload starts from the beginning. This cannot be undone.",
    ar: "هل تريد تجاهل هذه المسودة؟ سيتم التخلص من الملف المرفوع جزئيًا ويبدأ رفع جديد من البداية. لا يمكن التراجع عن ذلك.",
  },
  noConnectionsTitle: {
    en: "No dataset source connections yet",
    ar: "لا توجد اتصالات بمصادر البيانات بعد",
  },
  noConnectionsWhy: {
    en: "A connection tells Sovra where a dataset may be read from. Create one below to begin.",
    ar: "يحدد الاتصال المكان الذي يمكن قراءة مجموعة البيانات منه. أنشئ اتصالًا أدناه للبدء.",
  },
  uploadProgress: { en: "Upload progress", ar: "تقدم الرفع" },
  confidenceScore: { en: "Confidence score", ar: "درجة الثقة" },
  githubTokenHelp: {
    en: "Paste a GitHub App installation token or an organization-approved token with read access to repository metadata (read-only repository scope).",
    ar: "الصق رمز تثبيت تطبيق GitHub أو رمزًا معتمدًا من المؤسسة بصلاحية قراءة بيانات المستودعات (نطاق قراءة فقط).",
  },
  noRepositoriesTitle: {
    en: "No accessible repositories found",
    ar: "لم يتم العثور على مستودعات متاحة",
  },
  noRepositoriesWhy: {
    en: "GitHub does not give this token access to any repository. Check the token's scope and the installation's repository access, then load again.",
    ar: "لا يمنح GitHub هذا الرمز صلاحية الوصول إلى أي مستودع. تحقق من نطاق الرمز وصلاحيات التثبيت ثم أعد التحميل.",
  },
  noRepositoriesAction: {
    en: "Use a token with wider repository access.",
    ar: "استخدم رمزًا بصلاحية وصول أوسع إلى المستودعات.",
  },
  cancel: { en: "Cancel", ar: "إلغاء" },
  working: { en: "Working…", ar: "جارٍ التنفيذ…" },
  errorSummaryTitle: { en: "There is a problem", ar: "توجد مشكلة" },
  fieldRequired: { en: "(required)", ar: "(مطلوب)" },
  fieldOptional: { en: "(optional)", ar: "(اختياري)" },
  breadcrumbNav: { en: "Breadcrumb", ar: "مسار التصفح" },
  statusDraft: { en: "Draft", ar: "مسودة" },
  statusNotConnected: { en: "Not connected", ar: "غير متصل" },
  statusValidationRequired: { en: "Validation required", ar: "يتطلب التحقق" },
  statusBlocked: { en: "Blocked", ar: "محظور" },
  statusBlockedReasonUnavailable: {
    en: "Blocked — reason unavailable",
    ar: "محظور — السبب غير متوفر",
  },
  statusQueued: { en: "Queued", ar: "في الانتظار" },
  statusRunning: { en: "Running", ar: "قيد التشغيل" },
  statusFailed: { en: "Failed", ar: "فشل" },
  statusCancelled: { en: "Cancelled", ar: "ملغى" },
  statusQualified: { en: "Qualified for", ar: "مؤهّل لـ" },
  statusQualifiedBare: { en: "Qualified", ar: "مؤهّل" },
  statusUnknown: { en: "Unknown state", ar: "حالة غير معروفة" },
  statusProduction: { en: "Production", ar: "الإنتاج" },
} as const satisfies Record<string, Record<Locale, string>>;

export function phrase(key: keyof typeof PHRASES, locale: Locale = "en"): string {
  // A locale arriving at runtime (an API value, a stored preference) may be one we do not ship:
  // fall back to English rather than render nothing.
  // Own keys only: "toString" / "constructor" / "__proto__" exist on every object.
  const words: Record<string, string> = PHRASES[key];
  return Object.hasOwn(words, locale) ? words[locale]! : words.en!;
}

/** The language the text is actually rendered in: a locale we do not ship renders English, so
 * `lang` must say "en", not echo the unknown value. */
export function resolveLocale(locale: string): Locale {
  return locale === "ar" ? "ar" : "en";
}

export function dirFor(locale: Locale): "ltr" | "rtl" {
  return locale === "ar" ? "rtl" : "ltr";
}
