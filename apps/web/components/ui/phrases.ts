/** Standard system phrases in both languages (CLAUDE.md "Do without asking"; UX_STANDARD.md §3). */
export type Locale = "en" | "ar";

export const PHRASES = {
  abandonDraft: { en: "Abandon draft", ar: "تجاهل المسودة" },
  keepDraft: { en: "Keep draft", ar: "الاحتفاظ بالمسودة" },
  abandonDraftAction: {
    en: "Abandon this draft and start a new upload",
    ar: "تجاهل هذه المسودة وابدأ رفعًا جديدًا",
  },
  resumeBannerBeforeName: { en: "A previous upload of", ar: "رفع سابق لملف" },
  resumeBannerAfterName: {
    en: "is still in progress. Choose that same file to resume it.",
    ar: "لا يزال قيد التنفيذ. اختر نفس الملف لاستئنافه.",
  },
  abandonConfirmTitle: { en: "Abandon this draft?", ar: "هل تريد تجاهل هذه المسودة؟" },
  // FTF-UX02 round-2 finding 5 (CONFIRM-PROMISES-DISCARD): the prior wording ("the partly
  // uploaded file is discarded") implied the server-side upload session is aborted on confirm.
  // No route in this repository lets the UI abort it (apps/api/app/datasets/
  // direct_object_storage_upload_routes.py exposes create/read/part-urls/finalize only); already
  // -uploaded parts are only reclaimed later by the 24 h sweeper (upload_sweeper.py). Reworded to
  // describe only what confirming actually, honestly does (Control plane's stated fallback: abort
  // the real route if one exists, otherwise reword).
  abandonConfirmBody: {
    en: "This browser forgets your progress and a new upload starts from the beginning. This cannot be undone.",
    ar: "سينسى هذا المتصفح التقدم الذي أحرزته وسيبدأ رفع جديد من البداية. لا يمكن التراجع عن ذلك.",
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

  // FTF-UX02 round 2: FTF-0500A rebuilt on the UX01 components (NOT-ON-UX01 /
  // NOT-BILINGUAL findings). Raw backend enum values (source type, purpose,
  // security classification) are left as their literal API vocabulary, same
  // precedent as every other FTF-05xx closed-vocabulary dropdown in this
  // repository -- only the UI chrome around them is translated here.
  datasetSourceHeading: { en: "Dataset source connections", ar: "اتصالات مصادر البيانات" },
  datasetSourceLoading: {
    en: "Loading dataset source connections…",
    ar: "جارٍ تحميل اتصالات مصادر البيانات…",
  },
  newConnectionHeading: { en: "New connection", ar: "اتصال جديد" },
  sourceTypeLabel: { en: "Source type", ar: "نوع المصدر" },
  displayNameLabel: { en: "Display name", ar: "الاسم المعروض" },
  // FTF-UX02 round-2 pre-review round 1 (LABELS-ABOVE-FIELDS misclaimed): the label text used to
  // bake in "(optional)" on top of the one `Field` itself renders from `requirement`, producing a
  // doubled "Credential (optional) (optional)". The requirement marker is `Field`'s job alone.
  credentialLabel: { en: "Credential", ar: "بيانات الاعتماد" },
  retry: { en: "Retry", ar: "إعادة المحاولة" },
  requestFailedRetryable: {
    en: "Something went wrong loading this. You can retry.",
    ar: "حدث خطأ أثناء التحميل. يمكنك إعادة المحاولة.",
  },
  responseMalformedRetryable: {
    en: "The server's reply could not be read. You can retry.",
    ar: "تعذّرت قراءة رد الخادم. يمكنك إعادة المحاولة.",
  },
  networkErrorRetryable: {
    en: "A network problem occurred. You can retry.",
    ar: "حدث خطأ في الشبكة. يمكنك إعادة المحاولة.",
  },
  allowedPurposesLegend: { en: "Allowed purposes", ar: "الأغراض المسموح بها" },
  securityClassificationLabel: {
    en: "Security classification (privacy boundary)",
    ar: "تصنيف الأمان (حدود الخصوصية)",
  },
  selectPlaceholder: { en: "Select…", ar: "اختر…" },
  reviewButton: { en: "Review", ar: "مراجعة" },
  previewNotice: {
    en: "Review before creating. This previews exactly what will be submitted — it is not a preview of the external source's own records or byte counts, which no connector in this repository can validate yet.",
    ar: "راجع قبل الإنشاء. يعرض هذا تحديدًا ما سيتم إرساله — وليس معاينة لسجلات المصدر الخارجي نفسه أو عدد البايتات، التي لا يمكن لأي موصل في هذا المستودع التحقق منها بعد.",
  },
  previewSourceTypeLabel: { en: "Source type", ar: "نوع المصدر" },
  previewDisplayNameLabel: { en: "Display name", ar: "الاسم المعروض" },
  previewCredentialScopeLabel: { en: "Credentials scope", ar: "نطاق بيانات الاعتماد" },
  previewPurposesLabel: { en: "Allowed purposes", ar: "الأغراض المسموح بها" },
  previewPrivacyBoundaryLabel: { en: "Privacy boundary", ar: "حدود الخصوصية" },
  credentialProvided: { en: "credential provided", ar: "تم تقديم بيانات الاعتماد" },
  credentialNotProvided: { en: "no credential provided", ar: "لم تُقدَّم بيانات اعتماد" },
  backButton: { en: "Back", ar: "رجوع" },
  confirmCreateButton: { en: "Confirm and create", ar: "تأكيد وإنشاء" },
  authorizationHeading: { en: "Purpose authorization", ar: "تفويض الغرض" },
  purposeToCheckLabel: { en: "Purpose to authorize", ar: "الغرض المطلوب تفويضه" },
  checkAuthorizationButton: { en: "Check authorization", ar: "فحص التفويض" },
  authorizedResult: { en: "Authorized", ar: "مُفوَّض" },
  connectionStatusActive: { en: "Active", ar: "نشط" },
  connectionStatusRevoked: { en: "Revoked", ar: "مُلغى" },

  // FTF-UX02 round-2 pre-review round 2 (SERVER-DETAIL-ENGLISH-IN-AR): the backend's own `detail`
  // field is free-form English prose (dataset_source_connection_routes.py raises `str(exc)` or an
  // f-string, never a fixed code), not something safe to echo verbatim in Arabic. These phrases
  // back a frontend pattern-match translation of the known shapes that route actually returns;
  // anything unrecognised falls back to `detailUnexpectedRetryable` rather than raw English.
  detailProjectNotFound: { en: "The project could not be found.", ar: "تعذر العثور على المشروع." },
  detailConnectionNotFound: {
    en: "The connection could not be found.",
    ar: "تعذر العثور على الاتصال.",
  },
  detailSecretManagerUnavailable: {
    en: "Credential storage is not configured. Contact an administrator.",
    ar: "لم يتم تكوين تخزين بيانات الاعتماد. تواصل مع المسؤول.",
  },
  detailConnectionNotActive: {
    en: "This connection is no longer active, so no purpose can be authorized.",
    ar: "هذا الاتصال غير نشط، لذا لا يمكن تفويض أي غرض.",
  },
  // "{purpose}" is replaced with the raw closed-vocabulary purpose value (e.g. "training") --
  // never user-entered text, so no further escaping/<bdi> is needed around it.
  detailPurposeNotGranted: {
    en: "This connection is not granted for purpose: {purpose}",
    ar: "هذا الاتصال غير مُفوَّض للغرض: {purpose}",
  },
  // "{field}" is replaced with an already-translated field-label phrase.
  detailFieldRequiredTemplate: {
    en: "{field} is required.",
    ar: "{field} مطلوب.",
  },
  detailUnexpectedRetryable: {
    en: "An unexpected error occurred. You can retry.",
    ar: "حدث خطأ غير متوقع. يمكنك إعادة المحاولة.",
  },
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
