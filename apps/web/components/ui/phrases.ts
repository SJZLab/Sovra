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
  // FTF-UX05 (models/capabilities/cost area).
  runProbeConfirmTitle: { en: "Run a real probe?", ar: "تشغيل فحص حقيقي؟" },
  runProbeConfirmBody: {
    en: "This sends a real request to the model provider and may incur cost. It cannot be undone.",
    ar: "سيُرسل هذا طلبًا حقيقيًا إلى مزوّد النموذج وقد يترتب عليه تكلفة. لا يمكن التراجع عن ذلك.",
  },
  confirmRunProbe: { en: "Run probe", ar: "تشغيل الفحص" },
  mappingThreeStepsNotice: {
    en: "The current mapping, the available artifacts and the change history each load separately. Use the three buttons below to load each one.",
    ar: "يتم تحميل الربط الحالي والمصنفات المتاحة وسجل التغييرات كل على حدة. استخدم الأزرار الثلاثة أدناه لتحميل كل منها.",
  },
  checkCompatibility: { en: "Check compatibility", ar: "فحص التوافق" },
  // "..." (three literal periods), byte-for-byte unchanged from the pre-UX05 English text, so the
  // certified FTF-0309 tests' exact-text queries (`getByText("Checking...")`) keep matching.
  checkingCompatibility: { en: "Checking...", ar: "جارٍ الفحص..." },

  // FTF-UX05 round 2 (2026-10-22): the container-level lang/dir on these four components wrapped
  // large amounts of hardcoded-English UI copy; every visible string the components themselves
  // author (not raw server data) is routed through this catalogue so locale="ar" is honest for
  // (nearly) all rendered text. Shared generic words first, then one block per component.
  select: { en: "Select", ar: "تحديد" },
  selected: { en: "Selected", ar: "محدد" },
  notRecordedText: { en: "not recorded", ar: "غير مسجَّل" },
  noneRecordedText: { en: "none recorded", ar: "لا يوجد مسجَّل" },
  allowedText: { en: "allowed", ar: "مسموح" },
  notAllowedText: { en: "not allowed", ar: "غير مسموح" },
  byPrefix: { en: "by", ar: "بواسطة" },
  atPrefix: { en: "at", ar: "في" },
  networkErrorPrefix: { en: "network error:", ar: "خطأ في الشبكة:" },
  requestFailedTemplate: { en: "request failed ({status})", ar: "فشل الطلب ({status})" },

  // FTF-0214 (model catalog).
  catalogLoadProjects: { en: "Load projects", ar: "تحميل المشاريع" },
  catalogNoProjectSelected: { en: "No project selected yet.", ar: "لم يتم تحديد مشروع بعد." },
  catalogStatusFilterLabel: { en: "Status filter", ar: "فلتر الحالة" },
  catalogMetadataFilterLabel: { en: "Metadata filter", ar: "فلتر البيانات الوصفية" },
  catalogFilterAll: { en: "All", ar: "الكل" },
  catalogStatusCallable: { en: "Callable", ar: "قابل للاستدعاء" },
  catalogStatusStale: { en: "Stale", ar: "قديم" },
  catalogStatusRemoved: { en: "Removed", ar: "مُزال" },
  catalogMetadataEnriched: { en: "Enriched", ar: "مُفصَّل" },
  catalogMetadataLimited: { en: "Limited", ar: "محدود" },
  catalogLoadCatalog: { en: "Load catalog", ar: "تحميل الكتالوج" },
  catalogConnectionPrefix: { en: "Connection:", ar: "الاتصال:" },
  catalogAccessPrefix: { en: "Access:", ar: "الوصول:" },
  catalogLastCheckPrefix: { en: "Last successful check:", ar: "آخر فحص ناجح:" },
  catalogNever: { en: "never", ar: "أبدًا" },
  catalogAnomalyPrefix: { en: "Anomaly:", ar: "شذوذ:" },
  catalogModelGroupsPrefix: { en: "Model groups:", ar: "مجموعات النماذج:" },
  catalogProbeKindLabel: { en: "Probe kind", ar: "نوع الفحص" },
  probeKindChat: { en: "Chat", ar: "محادثة" },
  probeKindStreaming: { en: "Streaming", ar: "بث" },
  probeKindToolCall: { en: "Tool call", ar: "استدعاء أداة" },
  probeKindStructuredOutput: { en: "Structured output", ar: "ناتج منظم" },
  tokensSuffix: { en: "tokens", ar: "رموز" },
  probeResultCostUnknown: { en: "cost unknown", ar: "التكلفة غير معروفة" },
  catalogSelectProjectFirst: { en: "select a project first", ar: "حدد مشروعًا أولًا" },
  catalogMalformedProjectListing: {
    en: "malformed project listing response",
    ar: "استجابة قائمة مشاريع غير صحيحة",
  },
  catalogMalformedCatalog: {
    en: "malformed catalog response",
    ar: "استجابة كتالوج غير صحيحة",
  },
  catalogMalformedProbe: { en: "malformed probe response", ar: "استجابة فحص غير صحيحة" },
  catalogNoEntriesTitle: { en: "No catalog entries", ar: "لا توجد عناصر في الكتالوج" },
  catalogNoEntriesWhy: {
    en: "This connection and filter combination returned no models. Try different filters or reload.",
    ar: "لم يُرجع هذا الاتصال مع مجموعة الفلاتر هذه أي نماذج. جرّب فلاترًا مختلفة أو أعد التحميل.",
  },

  // FTF-1301 (cost profile + cost-per-success search).
  costProfileTypeLabel: { en: "Type", ar: "النوع" },
  costProfileLabelLabel: { en: "Label", ar: "التسمية" },
  costProfileRateUnitLabel: { en: "Rate unit", ar: "وحدة السعر" },
  costProfileRateAmountLabel: { en: "Rate amount", ar: "قيمة السعر" },
  costProfileCurrencyLabel: { en: "Currency", ar: "العملة" },
  costProfileSourceLabel: { en: "Source", ar: "المصدر" },
  costProfileTypeProvider: { en: "Provider", ar: "مزوّد" },
  costProfileTypeLocalHardware: { en: "Local hardware", ar: "عتاد محلي" },
  rateUnitPerInputToken: { en: "Per input token", ar: "لكل رمز إدخال" },
  rateUnitPerOutputToken: { en: "Per output token", ar: "لكل رمز إخراج" },
  rateUnitPerGpuHour: { en: "Per GPU hour", ar: "لكل ساعة معالج رسومي" },
  rateUnitPerCpuHour: { en: "Per CPU hour", ar: "لكل ساعة معالج" },
  rateUnitPerRamGbHour: { en: "Per RAM GB-hour", ar: "لكل جيجابايت ذاكرة في الساعة" },
  rateUnitPerHour: { en: "Per hour", ar: "لكل ساعة" },
  rateUnitPerRequest: { en: "Per request", ar: "لكل طلب" },
  costProfileRecordRate: { en: "Record rate", ar: "تسجيل السعر" },
  costPerSuccessSearchHeading: { en: "Cost-per-success search", ar: "بحث التكلفة لكل نجاح" },
  costProfileLoading: { en: "Loading...", ar: "جارٍ التحميل..." },
  costProfileCheapestCertifiedPrefix: { en: "Cheapest certified rate:", ar: "أرخص سعر معتمد:" },
  costProfileNoCertifiedCheapestRate: {
    en: "No certified cheapest rate yet -- record and verify a rate to see one here.",
    ar: "لا يوجد أرخص سعر معتمد بعد -- سجّل واعتمد سعرًا لتظهر هنا.",
  },
  costProfileCertifiedCheapestEligible: {
    en: "Certified cheapest-eligible",
    ar: "معتمد ومؤهّل للأرخص",
  },
  costProfileVerify: { en: "Verify", ar: "اعتماد" },
  costProfileDisqualifiedExpired: {
    en: "Expired -- this rate is no longer current.",
    ar: "منتهي -- هذا السعر لم يعد ساريًا.",
  },
  costProfileDisqualifiedUnverified: {
    en: "Unverified -- nobody has independently confirmed this rate yet.",
    ar: "غير مُعتمد -- لم يؤكد أحد هذا السعر بشكل مستقل حتى الآن.",
  },
  costProfileDisqualifiedZeroProviderRate: {
    en: "A provider price of exactly zero is never treated as certified-free (a $0 token price is not local-compute-free).",
    ar: "لا يُعامل سعر المزوّد البالغ صفرًا تمامًا على أنه مجاني معتمد أبدًا (سعر رمز بقيمة 0 دولار ليس مجانيًا محليًا).",
  },
  costProfileMalformedSearchResponse: {
    en: "malformed cost-per-success search response",
    ar: "استجابة بحث تكلفة لكل نجاح غير صحيحة",
  },
  costProfileNoResultsTitle: { en: "No rates recorded yet", ar: "لم تُسجَّل أسعار بعد" },
  costProfileNoResultsWhy: {
    en: "Record a rate above to see it here, or reload if you expected results.",
    ar: "سجّل سعرًا أعلاه لتراه هنا، أو أعد التحميل إذا كنت تتوقع نتائج.",
  },
  costProfileReload: { en: "Reload search", ar: "إعادة تحميل البحث" },

  // FTF-0308 (model-to-artifact mapping).
  mappingLoadMapping: { en: "Load mapping", ar: "تحميل الربط" },
  mappingStateMapped: {
    en: "Mapped to a trainable artifact",
    ar: "مرتبط بمصنف قابل للتدريب",
  },
  mappingStateUnmapped: {
    en: "Not mapped: this alias has no explicit training artifact",
    ar: "غير مرتبط: لا يوجد مصنف تدريب محدد لهذا الاسم",
  },
  mappingCatalogVersionPrefix: { en: "Catalog version:", ar: "إصدار الكتالوج:" },
  mappingLastVerifiedPrefix: { en: "Last verified accessible:", ar: "آخر تحقق من الوصول:" },
  mappingLastChangedPrefix: { en: "Last changed:", ar: "آخر تغيير:" },
  mappingLoadArtifacts: { en: "Load artifacts", ar: "تحميل المصنفات" },
  mappingArtifactNotReady: {
    en: "not ready: cannot be mapped",
    ar: "غير جاهز: لا يمكن ربطه",
  },
  mappingNoArtifactSelected: { en: "No artifact selected yet.", ar: "لم يتم تحديد مصنف بعد." },
  mappingMapButton: { en: "Map artifact", ar: "ربط المصنف" },
  mappingRemoveButton: { en: "Remove mapping", ar: "إزالة الربط" },
  mappingConfirmRemoving: { en: "Removing this mapping", ar: "إزالة هذا الربط" },
  mappingConfirmChanging: { en: "Changing this mapping", ar: "تغيير هذا الربط" },
  mappingConfirmInvalidatesSuffix: {
    en: "invalidates related qualification: evidence recorded against catalog version {version} will no longer apply.",
    ar: "يُلغي التأهيل المرتبط: لن تنطبق الأدلة المسجَّلة مقابل إصدار الكتالوج {version} بعد الآن.",
  },
  mappingConfirmButton: { en: "Confirm change", ar: "تأكيد التغيير" },
  mappingLoadFirst: { en: "load the current mapping first", ar: "حمّل الربط الحالي أولًا" },
  mappingMalformedMapping: {
    en: "malformed mapping response",
    ar: "استجابة ربط غير صحيحة",
  },
  mappingMalformedArtifactListing: {
    en: "malformed artifact listing response",
    ar: "استجابة قائمة مصنفات غير صحيحة",
  },
  mappingMalformedChangeHistory: {
    en: "malformed change history response",
    ar: "استجابة سجل تغييرات غير صحيحة",
  },
  mappingNoticeChangedPrefix: {
    en: "Mapping changed: catalog version",
    ar: "تغيّر الربط: إصدار الكتالوج",
  },
  mappingNoticeChangedSuffix: {
    en: "Qualification evidence recorded against version {prev} no longer applies.",
    ar: "لا تنطبق أدلة التأهيل المسجَّلة مقابل الإصدار {prev} بعد الآن.",
  },
  mappingNoticeNoChange: {
    en: "No change: this alias already has that mapping.",
    ar: "لا تغيير: هذا الاسم مرتبط بهذا المصنف فعلًا.",
  },
  mappingNoChangesRecorded: { en: "No changes recorded.", ar: "لا توجد تغييرات مسجَّلة." },
  mappingLoadChangeHistory: { en: "Load change history", ar: "تحميل سجل التغييرات" },
  mappingErrorNotFound: {
    en: "The connection or artifact was not found.",
    ar: "لم يتم العثور على الاتصال أو المصنف.",
  },
  mappingErrorConnectionRevoked: {
    en: "The gateway connection is revoked; nothing was changed.",
    ar: "تم إلغاء اتصال البوابة؛ لم يتم تغيير أي شيء.",
  },
  mappingErrorModelNotAccessible: {
    en: "The gateway does not currently list this model alias; nothing was changed.",
    ar: "لا تعرض البوابة حاليًا اسم هذا النموذج؛ لم يتم تغيير أي شيء.",
  },
  mappingErrorArtifactNotReady: {
    en: "The artifact is not ready; nothing was changed.",
    ar: "المصنف غير جاهز؛ لم يتم تغيير أي شيء.",
  },
  mappingErrorCatalogVersionConflict: {
    en: "The mapping changed since it was loaded; reload it and review before retrying.",
    ar: "تغيّر الربط منذ تحميله؛ أعد تحميله وراجعه قبل إعادة المحاولة.",
  },
  mappingErrorGatewayUnavailable: {
    en: "The gateway could not be reached, so the alias could not be verified; nothing was changed.",
    ar: "تعذّر الوصول إلى البوابة، فلم يتم التحقق من الاسم؛ لم يتم تغيير أي شيء.",
  },
  mappingErrorInvalidModelName: {
    en: "The model name is not valid.",
    ar: "اسم النموذج غير صالح.",
  },
  artifactDetailArtifact: { en: "Artifact", ar: "المصنف" },
  artifactDetailSource: { en: "Source", ar: "المصدر" },
  artifactDetailModelFamily: { en: "Model family", ar: "عائلة النموذج" },
  artifactDetailPinnedRevision: { en: "Pinned revision", ar: "المراجعة المثبَّتة" },
  artifactDetailWeightsDigest: { en: "Weights digest", ar: "بصمة الأوزان" },
  artifactDetailLicense: { en: "License", ar: "الترخيص" },
  artifactDetailTrustRemoteCode: {
    en: "Trust remote code",
    ar: "الوثوق بالتعليمات البرمجية البعيدة",
  },
  artifactDetailRequiredPackages: { en: "Required packages", ar: "الحزم المطلوبة" },
  artifactDetailMinimumGpuProfile: {
    en: "Minimum GPU profile",
    ar: "أدنى مواصفات المعالج الرسومي",
  },
  artifactDetailStatus: { en: "Status", ar: "الحالة" },
  artifactLicenseAcceptedByPrefix: { en: "accepted by", ar: "تمت الموافقة من قبل" },
  mappingAriaLabel: { en: "Training artifact for {model}", ar: "مصنف التدريب لـ {model}" },

  // FTF-0309 (capability compatibility).
  compatibilityAriaLabel: { en: "Capability compatibility", ar: "توافق القدرات" },
  compatibilityHeadingPrefix: { en: "Compatibility:", ar: "التوافق:" },
  compatGatewayAccess: { en: "Gateway access", ar: "الوصول إلى البوابة" },
  compatCapability: { en: "Capability", ar: "القدرة" },
  compatRole: { en: "Role", ar: "الدور" },
  compatArtifact: { en: "Artifact", ar: "المصنف" },
  compatDiscoveryChecked: { en: "Discovery checked", ar: "تم فحص الاكتشاف" },
  compatToolCallProbe: { en: "Tool-call probe", ar: "فحص استدعاء الأداة" },
  compatNotRun: { en: "not run", ar: "لم يُشغَّل" },
  compatCheckedPrefix: { en: "checked", ar: "تم الفحص" },
  compatLicensePrefix: { en: "license", ar: "الترخيص" },
  compatWeightsPrefix: { en: "weights", ar: "الأوزان" },
  compatApproved: { en: "approved", ar: "مقبول" },
  compatNotApproved: { en: "not approved", ar: "غير مقبول" },
  dimensionMethodSupport: { en: "Method support", ar: "دعم الطريقة" },
  dimensionTestedVram: { en: "Tested VRAM", ar: "ذاكرة GPU المُختبرة" },
  dimensionChatTemplate: { en: "Chat template", ar: "قالب المحادثة" },
  compatReasonPrefix: { en: "reason:", ar: "السبب:" },
  compatNoEvidenceDate: {
    en: "no evidence date returned by the server",
    ar: "لم يُرجع الخادم تاريخ دليل",
  },
  compatDeclaredVramPrefix: {
    en: "Declared minimum GPU profile (declared, not tested):",
    ar: "أدنى مواصفات معالج رسومي معلنة (معلنة، غير مُختبرة):",
  },
  compatWhyBlockedLabel: { en: "Why training is blocked", ar: "سبب حظر التدريب" },
  compatNotTrainablePrefix: { en: "Not trainable:", ar: "غير قابل للتدريب:" },
  compatGatewayAccessIsPrefix: { en: "Gateway access is", ar: "الوصول إلى البوابة هو" },
  compatArtifactIsPrefix: { en: "Trainable artifact is", ar: "المصنف القابل للتدريب هو" },
  compatNotReadySuffix: { en: ", not ready", ar: "، غير جاهز" },
  compatLicenseNotApprovedBlocker: {
    en: "Artifact license is not approved",
    ar: "ترخيص المصنف غير مقبول",
  },
  compatIsWord: { en: "is", ar: "هي" },
  compatTrainButton: { en: "Train", ar: "تدريب" },
  compatTrainingRequestedPrefix: {
    en: "Training requested by you for",
    ar: "تم طلب التدريب من قِبلك لـ",
  },
  compatMalformedResponse: {
    en: "malformed capabilities response",
    ar: "استجابة قدرات غير صحيحة",
  },
  compatResponseMismatch: {
    en: "capabilities response does not match the request",
    ar: "استجابة القدرات لا تطابق الطلب",
  },
  compatNotFoundMessage: {
    en: "The connection, artifact or project was not found.",
    ar: "لم يتم العثور على الاتصال أو المصنف أو المشروع.",
  },
  compatGatewayUnavailableMessage: {
    en: "The gateway could not be reached, so compatibility could not be determined. No recommendation is made.",
    ar: "تعذّر الوصول إلى البوابة، فلم يتمكن من تحديد التوافق. لا يُقدَّم أي توصية.",
  },
} as const satisfies Record<string, Record<Locale, string>>;

/** `{name}` placeholders in a catalogue string are replaced from `vars`; nothing else is touched
 * (same mechanism `target-application/common.ts`'s `fill` already established for this repo). */
function fill(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : whole,
  );
}

export function phrase(
  key: keyof typeof PHRASES,
  locale: Locale = "en",
  vars?: Record<string, string | number>,
): string {
  // A locale arriving at runtime (an API value, a stored preference) may be one we do not ship:
  // fall back to English rather than render nothing.
  // Own keys only: "toString" / "constructor" / "__proto__" exist on every object.
  const words: Record<string, string> = PHRASES[key];
  const template = Object.hasOwn(words, locale) ? words[locale]! : words.en!;
  return fill(template, vars);
}

/** The language the text is actually rendered in: a locale we do not ship renders English, so
 * `lang` must say "en", not echo the unknown value. */
export function resolveLocale(locale: string): Locale {
  return locale === "ar" ? "ar" : "en";
}

export function dirFor(locale: Locale): "ltr" | "rtl" {
  return locale === "ar" ? "rtl" : "ltr";
}
