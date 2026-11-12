/**
 * FTF-0516 (dataset issue resolution) en/ar catalogue (FTF-UX02 round 3, finding 4). Every system
 * string the screen shows lives here. The English value of every entry is byte-for-byte what the
 * pre-UX02 component rendered, so the certified `ftf_0516.spec.ts` and real-API suites keep passing
 * unchanged at the default `locale="en"`. Server-supplied identifiers (detector, category, reason
 * code, digests, ids, timestamps) are DATA: they are shown as-is inside an isolated `<bdi dir="ltr">`
 * and are never translated; the closed vocabularies (severity, state, action, role, and the known
 * detector categories) get Arabic words below.
 */

import { resolveLocale, type Locale } from "../ui";

export type CatalogueEntry = { readonly en: string; readonly ar: string };

const STRINGS = {
  sectionLabel: { en: "Dataset issue resolution", ar: "معالجة مشكلات مجموعة البيانات" },
  heading: { en: "Dataset quality issues", ar: "مشكلات جودة مجموعة البيانات" },
  intro: {
    en: "Load the latest quality report to review each issue and record a decision.",
    ar: "حمّل أحدث تقرير جودة لمراجعة كل مشكلة وتسجيل قرار بشأنها.",
  },
  loadButton: { en: "Load quality report", ar: "تحميل تقرير الجودة" },
  loadingButton: { en: "Loading...", ar: "جارٍ التحميل..." },
  loadingReport: { en: "Loading the quality report…", ar: "جارٍ تحميل تقرير الجودة…" },
  reloadButton: { en: "Reload report", ar: "إعادة تحميل التقرير" },
  loadMoreButton: { en: "Load more issues", ar: "تحميل المزيد من المشكلات" },

  emptyIssuesTitle: { en: "No issues to resolve", ar: "لا توجد مشكلات للمعالجة" },
  emptyIssuesWhy: {
    en: "The latest quality report lists no issues for this dataset.",
    ar: "لا يسرد أحدث تقرير جودة أي مشكلات لمجموعة البيانات هذه.",
  },
  emptyDecisions: { en: "No decisions have been recorded yet.", ar: "لم يُسجَّل أي قرار بعد." },

  // report
  reportVersion: { en: "Dataset version", ar: "إصدار مجموعة البيانات" },
  reportReport: { en: "Report", ar: "التقرير" },
  reportIssues: { en: "Issues", ar: "المشكلات" },
  reportBlocksSplit: { en: "Blocks split", ar: "يحظر التقسيم" },
  reportBlocksTraining: { en: "Blocks training", ar: "يحظر التدريب" },
  reportResolutionVersion: { en: "Resolution version", ar: "إصدار المعالجة" },
  reportValue: { en: "#{seq} {sha}", ar: "#{seq} {sha}" },
  issuesValue: {
    en: "{count} ({blocking} blocking, {warning} warning)",
    ar: "{count} ({blocking} حاجبة، {warning} تحذيرية)",
  },
  yes: { en: "yes", ar: "نعم" },
  no: { en: "no", ar: "لا" },
  notRecorded: { en: "not recorded", ar: "غير مسجَّل" },

  // issue line
  issuesList: { en: "Issues", ar: "المشكلات" },
  issueLine: {
    en: "{severity} {detector}/{category}: {reason} [{state}]",
    ar: "{severity} {detector}/{category}: {reason} [{state}]",
  },
  positionsLine: { en: " positions {list}", ar: " المواضع {list}" },
  none: { en: "none", ar: "لا شيء" },
  decisionLine: {
    en: " before {before} after {after} by {actor} ({role}): {reason}",
    ar: " قبل {before} بعد {after} بواسطة {actor} ({role}): {reason}",
  },
  reviewButton: { en: "Review {id}", ar: "مراجعة {id}" },

  // form
  formLabel: { en: "Resolve issue", ar: "معالجة المشكلة" },
  selectedIssue: {
    en: "Issue {id} ({severity}, evidence {sha})",
    ar: "المشكلة {id} ({severity}، الدليل {sha})",
  },
  alreadyDecided: {
    en: "Already {state}; a decision cannot be changed here.",
    ar: "الحالة الحالية: {state}؛ لا يمكن تغيير القرار من هنا.",
  },
  detectorFailure: {
    en: "A detector failure requires a rerun, not a decision.",
    ar: "فشل الكاشف يتطلب إعادة التشغيل لا اتخاذ قرار.",
  },
  actionLabel: { en: "Action", ar: "الإجراء" },
  positionsLegend: { en: "Positions to exclude", ar: "المواضع المراد استبعادها" },
  positionLabel: { en: "position {n}", ar: "الموضع {n}" },
  successorLabel: { en: "Successor dataset id", ar: "معرّف مجموعة البيانات اللاحقة" },
  successorHelper: {
    en: "The UUID of the later dataset version that replaces this one.",
    ar: "المعرّف (UUID) لإصدار مجموعة البيانات اللاحق الذي يحل محل هذا الإصدار.",
  },
  rationaleLabel: { en: "Rationale", ar: "المبرر" },
  rationaleHint: {
    en: "Required. Up to {max} characters; it is recorded with the decision.",
    ar: "مطلوب. حتى {max} حرف؛ يُسجَّل مع القرار.",
  },
  required: { en: "(required)", ar: "(مطلوب)" },
  problemsLabel: { en: "Why this cannot be submitted", ar: "سبب تعذّر الإرسال" },
  problemAction: {
    en: "This action is not available for this issue.",
    ar: "هذا الإجراء غير متاح لهذه المشكلة.",
  },
  problemRationaleRequired: { en: "A rationale is required.", ar: "المبرر مطلوب." },
  problemRationaleTooLong: {
    en: "The rationale must be at most {max} characters.",
    ar: "يجب ألا يتجاوز المبرر {max} حرف.",
  },
  problemPositions: {
    en: "Select at least one position to exclude.",
    ar: "اختر موضعًا واحدًا على الأقل للاستبعاد.",
  },
  problemSuccessor: {
    en: "A successor dataset id (UUID) is required.",
    ar: "معرّف مجموعة البيانات اللاحقة (UUID) مطلوب.",
  },
  submitButton: { en: "Record decision", ar: "تسجيل القرار" },
  submittingButton: { en: "Submitting...", ar: "جارٍ الإرسال..." },
  submittingStatus: { en: "Recording the decision…", ar: "جارٍ تسجيل القرار…" },

  // outcomes
  recordedHeadline: {
    en: "Decision recorded: {action} ({engine}) by {actor} as {role}.",
    ar: "تم تسجيل القرار: {action} ({engine}) بواسطة {actor} بصفة {role}.",
  },
  recordedEvidence: {
    en: "Before evidence {before}; after evidence {after}.",
    ar: "الدليل قبل القرار {before}؛ الدليل بعده {after}.",
  },
  recentHeading: { en: "Recent decisions", ar: "القرارات الأخيرة" },
  recentList: { en: "Recent decisions", ar: "القرارات الأخيرة" },
  recentLine: {
    en: "{action} ({engine}) issue {issue} before {before} after {after} by {actor} ({role}) at {at}: {reason}",
    ar: "{action} ({engine}) المشكلة {issue} قبل {before} بعد {after} بواسطة {actor} ({role}) في {at}: {reason}",
  },

  // errors (English = the pre-UX02 text, verbatim)
  malformed: { en: "malformed quality response", ar: "استجابة جودة غير صالحة" },
  mismatch: {
    en: "quality response does not match the request",
    ar: "استجابة الجودة لا تطابق الطلب",
  },
  resolutionMismatch: {
    en: "resolution response does not match the request",
    ar: "استجابة القرار لا تطابق الطلب",
  },
  requestFailed: { en: "request failed ({status})", ar: "فشل الطلب ({status})" },
  unknownDetail: {
    en: "The server refused this request.",
    ar: "رفض الخادم هذا الطلب.",
  },
  not_found: {
    en: "The dataset or issue was not found.",
    ar: "لم يتم العثور على مجموعة البيانات أو المشكلة.",
  },
  quality_report_not_found: {
    en: "No quality report has been recorded for this dataset yet.",
    ar: "لم يُسجَّل أي تقرير جودة لمجموعة البيانات هذه بعد.",
  },
  invalid_body: {
    en: "The server rejected the decision as malformed.",
    ar: "رفض الخادم القرار لأنه غير صالح.",
  },
  positions_not_in_issue: {
    en: "The selected positions are not part of this issue.",
    ar: "المواضع المحددة ليست جزءًا من هذه المشكلة.",
  },
  waive_not_permitted: {
    en: "Your role may not waive an issue.",
    ar: "لا يسمح دورك باستثناء مشكلة.",
  },
  report_version_conflict: {
    en: "The decisions changed since this screen was loaded. Reload and review again.",
    ar: "تغيّرت القرارات منذ تحميل هذه الشاشة. أعد التحميل وراجع من جديد.",
  },
  report_superseded: {
    en: "A newer quality report exists. Reload and review again.",
    ar: "يوجد تقرير جودة أحدث. أعد التحميل وراجع من جديد.",
  },
  issue_already_decided: {
    en: "This issue already has a decision.",
    ar: "لهذه المشكلة قرار مسجَّل بالفعل.",
  },
  waiver_not_allowed_for_blocking: {
    en: "A blocking issue cannot be waived.",
    ar: "لا يمكن استثناء مشكلة حاجبة.",
  },
  detector_failure_requires_rerun: {
    en: "A detector failure requires a rerun, not a decision.",
    ar: "فشل الكاشف يتطلب إعادة التشغيل لا اتخاذ قرار.",
  },
  evidence_mismatch: {
    en: "The issue evidence changed since this screen was loaded. Reload.",
    ar: "تغيّر دليل المشكلة منذ تحميل هذه الشاشة. أعد التحميل.",
  },
  successor_not_revalidated: {
    en: "The successor dataset has not been revalidated.",
    ar: "لم يُعاد التحقق من مجموعة البيانات اللاحقة.",
  },
  successor_not_later_version: {
    en: "The successor must be a later version of this dataset.",
    ar: "يجب أن تكون المجموعة اللاحقة إصدارًا أحدث من مجموعة البيانات هذه.",
  },
  idempotency_key_reused: {
    en: "That request key was already used for a different decision.",
    ar: "سبق استخدام مفتاح الطلب هذا لقرار مختلف.",
  },
  request_too_large: { en: "The request was too large.", ar: "كان الطلب كبيرًا جدًا." },
} as const satisfies Record<string, CatalogueEntry>;

export type Key0516 = keyof typeof STRINGS;
export const KEYS_0516 = Object.keys(STRINGS) as Key0516[];
export const STRINGS_0516: Record<Key0516, CatalogueEntry> = STRINGS;

/** The server `detail` codes this catalogue translates (the pre-UX02 `ERROR_MESSAGES` keys). */
export const SERVER_DETAIL_CODES: readonly Key0516[] = [
  "not_found",
  "quality_report_not_found",
  "invalid_body",
  "positions_not_in_issue",
  "waive_not_permitted",
  "report_version_conflict",
  "report_superseded",
  "issue_already_decided",
  "waiver_not_allowed_for_blocking",
  "detector_failure_requires_rerun",
  "evidence_mismatch",
  "successor_not_revalidated",
  "successor_not_later_version",
  "idempotency_key_reused",
  "request_too_large",
];

function fill(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : whole,
  );
}

export function t0516(
  key: Key0516,
  locale: Locale = "en",
  vars?: Record<string, string | number>,
): string {
  return fill(STRINGS[key][resolveLocale(locale)], vars);
}

/**
 * Closed vocabularies. English is the raw wire value (what the pre-UX02 screen showed); Arabic is a
 * word. An unknown value is shown as received (never dropped) in every locale.
 */
const WORDS = {
  severity: { blocking: "حاجبة", warning: "تحذير" },
  state: { open: "مفتوحة", resolved: "تمت المعالجة", waived: "مُستثناة" },
  action: {
    exclude: "استبعاد",
    correct: "تصحيح",
    new_version: "إصدار جديد",
    waive: "استثناء",
  },
  role: {
    ml_engineer: "مهندس تعلم آلي",
    workspace_admin: "مسؤول مساحة العمل",
    organization_admin: "مسؤول المؤسسة",
  },
  category: {
    record_schema: "مخطط السجل",
    tool_arguments: "معاملات الأداة",
    tokenization: "الترميز",
    exact_duplicate: "تكرار مطابق",
    normalized_duplicate: "تكرار بعد التطبيع",
    near_duplicate: "تكرار شبه مطابق",
    pii_secrets: "بيانات شخصية وأسرار",
    poisoning: "تسميم البيانات",
    detector_failure: "فشل الكاشف",
  },
} as const satisfies Record<string, Record<string, string>>;

export type WordKind = keyof typeof WORDS;

export function word(kind: WordKind, value: string, locale: Locale): string {
  if (resolveLocale(locale) === "en") return value;
  const table: Record<string, string> = WORDS[kind];
  return Object.hasOwn(table, value) ? table[value]! : value;
}

export const WORD_VALUES: { [K in WordKind]: string[] } = {
  severity: Object.keys(WORDS.severity),
  state: Object.keys(WORDS.state),
  action: Object.keys(WORDS.action),
  role: Object.keys(WORDS.role),
  category: Object.keys(WORDS.category),
};
