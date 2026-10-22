/**
 * FTF-0602 (evaluation case editor/import) en/ar catalogue (FTF-UX06 round 1). Every system
 * string the component shows -- labels, hints, notices and error text -- lives here, so no
 * English can survive in an Arabic render and no raw code is a label. The English value of every
 * dynamic/error entry is copied byte-for-byte from the pre-UX06 component so the certified
 * `ftf_0602.spec.ts` suite's exact-substring assertions keep passing unchanged at the default
 * `locale="en"`.
 */

import { resolveLocale, type Locale } from "../ui";

export type { Locale };
export { resolveLocale };
export { dirFor } from "../ui";

export type CatalogueEntry = { readonly en: string; readonly ar: string };

/** `{name}` placeholders in a catalogue string are replaced from `vars`; nothing else is touched. */
export function fill(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : whole,
  );
}

const STRINGS = {
  heading: { en: "Evaluation cases", ar: "حالات التقييم" },
  loadButton: { en: "Load cases", ar: "تحميل الحالات" },
  loadingCases: { en: "Loading cases…", ar: "جارٍ تحميل الحالات…" },
  loadMoreButton: { en: "Load more", ar: "تحميل المزيد" },
  restrictedNotice: {
    en: "Case content for this final suite is restricted. No case content is shown.",
    ar: "محتوى الحالات لهذه المجموعة النهائية مقيَّد. لا يُعرض أي محتوى للحالات.",
  },
  emptyTitle: { en: "No cases yet", ar: "لا توجد حالات بعد" },
  emptyWhy: {
    en: "Import cases below, or ask an evaluator to add some.",
    ar: "استورد حالات أدناه، أو اطلب من أحد المقيِّمين إضافتها.",
  },
  importHeading: { en: "Import cases", ar: "استيراد الحالات" },
  importHelp: {
    en: "A JSON array or a JSON Lines file of 1 to {max} cases. Each case needs its own expected state, severity, category, language and group key. Import is all or nothing.",
    ar: "مصفوفة JSON أو ملف JSON Lines يضم من 1 إلى {max} حالة. يحتاج كل حالة إلى حالتها المتوقعة الخاصة ودرجة الخطورة والفئة واللغة ومفتاح المجموعة. الاستيراد كل شيء أو لا شيء.",
  },
  importFileLabel: { en: "Choose a file to import", ar: "اختر ملفًا للاستيراد" },
  importTextLabel: { en: "Cases to import", ar: "الحالات المطلوب استيرادها" },
  importButton: { en: "Import cases", ar: "استيراد الحالات" },
  importingCases: { en: "Importing cases…", ar: "جارٍ استيراد الحالات…" },
  fileReadError: { en: "The file could not be read.", ar: "تعذّرت قراءة الملف." },
  rowHint: { en: " (case_key: {key})", ar: " (مفتاح الحالة: {key})" },
  editHeading: { en: "Edit case", ar: "تعديل الحالة" },
  fieldCategory: { en: "Category", ar: "الفئة" },
  fieldSeverity: { en: "Severity", ar: "درجة الخطورة" },
  fieldCritical: { en: "Critical", ar: "حرِج" },
  fieldLanguage: { en: "Language", ar: "اللغة" },
  fieldGroupKey: { en: "Group key", ar: "مفتاح المجموعة" },
  fieldGrader: { en: "Grader", ar: "أداة التقييم" },
  fieldInputJson: { en: "Input (JSON)", ar: "المدخل (JSON)" },
  fieldExpectedJson: { en: "Expected state (JSON)", ar: "الحالة المتوقعة (JSON)" },
  fieldGraderConfigJson: { en: "Grader config (JSON)", ar: "إعدادات أداة التقييم (JSON)" },
  fieldWeight: { en: "Weight", ar: "الوزن" },
  jsonHint: { en: "Must be a JSON object, for example {}.", ar: "يجب أن يكون كائن JSON، مثل {}." },
  jsonInlineInvalid: {
    en: "This is not a valid JSON object; it will not be saved like this.",
    ar: "هذا ليس كائن JSON صالحًا؛ لن يُحفظ بهذا الشكل.",
  },
  saveButton: { en: "Save case", ar: "حفظ الحالة" },
  savingCase: { en: "Saving case…", ar: "جارٍ حفظ الحالة…" },
  criticalTag: { en: "critical", ar: "حرِج" },
  groupPrefix: { en: "group", ar: "مجموعة" },
  editButton: { en: "Edit", ar: "تعديل" },

  // ---- dynamic/error text: English copied verbatim from the pre-UX06 component ----
  errNotFound: {
    en: "The suite or case was not found.",
    ar: "لم يتم العثور على المجموعة أو الحالة.",
  },
  errSuiteLocked: {
    en: "The suite is locked; cases can no longer be changed.",
    ar: "المجموعة مقفلة؛ لا يمكن تغيير الحالات بعد الآن.",
  },
  errDuplicateCaseKey: {
    en: "A case with that case_key already exists; nothing was imported.",
    ar: "توجد حالة بهذا case_key مسبقًا؛ لم يتم استيراد أي شيء.",
  },
  errRoleNotAuthorized: { en: "Your role may not do this.", ar: "قد لا يسمح دورك بهذا." },
  errFinalRestricted: {
    en: "This is a final suite; its case content is restricted to evaluators and administrators.",
    ar: "هذه مجموعة نهائية؛ محتوى حالاتها مقيَّد بالمقيِّمين والمسؤولين.",
  },
  errValidationFailed: {
    en: "The request failed validation; nothing was saved.",
    ar: "فشل الطلب في التحقق؛ لم يُحفظ أي شيء.",
  },
  errInvalidRequest: {
    en: "The request was not valid; nothing was saved.",
    ar: "لم يكن الطلب صالحًا؛ لم يُحفظ أي شيء.",
  },
  errInvalidQuery: { en: "The page request was not valid.", ar: "لم يكن طلب الصفحة صالحًا." },
  errBodyTooLarge: {
    en: "The request is too large; nothing was saved.",
    ar: "الطلب كبير جدًا؛ لم يُحفظ أي شيء.",
  },
  errGeneric: { en: "request failed ({status})", ar: "فشل الطلب ({status})" },
  errNetwork: { en: "network error: {reason}", ar: "خطأ في الشبكة: {reason}" },
  errUnknownReason: { en: "unknown error", ar: "خطأ غير معروف" },
  errMalformedListing: {
    en: "malformed case listing response",
    ar: "استجابة قائمة الحالات غير سليمة",
  },
  errMalformedImport: { en: "malformed import response", ar: "استجابة الاستيراد غير سليمة" },
  errMalformedSave: { en: "malformed case response", ar: "استجابة الحالة غير سليمة" },

  noticeImported: { en: "Imported {count} case{plural}.", ar: "تم استيراد {count} حالة." },
  noticeSaved: { en: "Saved {key}.", ar: "تم حفظ {key}." },

  parseEmpty: {
    en: "Paste or choose a file with 1 to {max} cases.",
    ar: "ألصق أو اختر ملفًا يحتوي على 1 إلى {max} حالة.",
  },
  parseNotArray: { en: "The file is not a JSON array.", ar: "الملف ليس مصفوفة JSON." },
  parseNotJson: { en: "The file is not valid JSON.", ar: "الملف ليس JSON صالحًا." },
  parseLineInvalid: {
    en: "Line {line} is not valid JSON.",
    ar: "السطر {line} ليس JSON صالحًا.",
  },
  parseCountRange: {
    en: "An import takes 1 to {max} cases; this has {count}.",
    ar: "يستوعب الاستيراد من 1 إلى {max} حالة؛ هذا يحتوي على {count}.",
  },
  parseRowNotObject: { en: "Row {row} is not a JSON object.", ar: "الصف {row} ليس كائن JSON." },

  criticalBlockedImport: {
    en: "Row {rows}: a critical case needs an independent grader (tool or state check), not a model self-report; nothing was sent.",
    ar: "الصف {rows}: تحتاج الحالة الحرِجة إلى أداة تقييم مستقلة (أداة أو فحص حالة)، لا إلى تقرير ذاتي من النموذج؛ لم يُرسل أي شيء.",
  },
  criticalBlockedEdit: {
    en: "A critical case needs an independent grader (tool or state check), not a model self-report; nothing was sent.",
    ar: "تحتاج الحالة الحرِجة إلى أداة تقييم مستقلة (أداة أو فحص حالة)، لا إلى تقرير ذاتي من النموذج؛ لم يُرسل أي شيء.",
  },
  nothingChanged: { en: "Nothing was changed.", ar: "لم يتم تغيير أي شيء." },
  jsonFieldMustBeObject: {
    en: "{field} must be a JSON object.",
    ar: "يجب أن يكون {field} كائن JSON.",
  },
  expectedMustNotBeEmpty: {
    en: "expected_json must not be empty.",
    ar: "يجب ألا يكون expected_json فارغًا.",
  },
  weightMustBePositive: {
    en: "weight must be a number greater than 0.",
    ar: "يجب أن يكون weight عددًا أكبر من 0.",
  },
  rowWord: { en: "Row", ar: "الصف" },
  requestWord: { en: "Request", ar: "الطلب" },
} as const satisfies Record<string, CatalogueEntry>;

export function t0602(
  key: keyof typeof STRINGS,
  locale: Locale | string = "en",
  vars?: Record<string, string | number>,
): string {
  const entry: CatalogueEntry = STRINGS[key];
  const resolved = resolveLocale(locale);
  const template = Object.hasOwn(entry, resolved) ? entry[resolved] : entry.en;
  return fill(template, vars);
}
