/** FTF-04A22 (mandate editor) en/ar catalogue (FTF-UX03 round 2). Every system string the editor
 * shows lives here, so no English can survive in an Arabic render and no raw code is a label. */
import { makeT, type CatalogueEntry } from "./common";

const STRINGS = {
  title: { en: "Mandate", ar: "التفويض" },
  loading: { en: "Loading mandate…", ar: "جارٍ تحميل التفويض…" },
  retry: { en: "Try again", ar: "إعادة المحاولة" },
  readOnly: {
    en: "This manifest is {status}, so its mandate can no longer be edited.",
    ar: "هذا البيان {status}، ولذلك لم يعد من الممكن تعديل التفويض.",
  },
  statusDraft: { en: "a draft", ar: "مسودة" },
  statusReviewRequired: { en: "awaiting review", ar: "بانتظار المراجعة" },
  statusLocked: { en: "locked", ar: "مقفل" },
  statusSuperseded: { en: "superseded", ar: "تم استبداله" },
  forbiddenTitle: { en: "Forbidden actions and conflicts", ar: "الإجراءات المحظورة والتعارضات" },
  forbiddenExplainer: {
    en: "Compare what the mandate forbids (desired) with what the repository's tools allow (observed) and what the runtime has been seen to block (enforced). An action is never shown as safe unless the runtime has actually been seen blocking it.",
    ar: "قارن بين ما يحظره التفويض (المطلوب) وما تسمح به أدوات المستودع (المرصود) وما شوهد التشغيل يحجبه فعلًا (المطبَّق). لا يُعرض أي إجراء على أنه آمن ما لم يُرصد حجبه فعلًا أثناء التشغيل.",
  },
  forbiddenEmptyTitle: {
    en: "No forbidden actions declared",
    ar: "لم يتم الإعلان عن أي إجراء محظور",
  },
  forbiddenEmptyWhy: {
    en: "Name the actions this application must never take, such as issuing a refund. Each one is checked against the repository's tools and the runtime.",
    ar: "حدّد الإجراءات التي يجب ألا ينفذها التطبيق أبدًا، مثل إصدار استرداد. يُفحص كل منها مقابل أدوات المستودع والتشغيل.",
  },
  forbiddenEmptyAction: { en: "Add a forbidden action", ar: "إضافة إجراء محظور" },
  forbiddenEmptyReadOnly: {
    en: "No forbidden actions were declared in this mandate.",
    ar: "لم يُعلن عن أي إجراء محظور في هذا التفويض.",
  },
  reasonLabel: { en: "Reason", ar: "السبب" },
  noReason: { en: "No reason given", ar: "لم يُذكر سبب" },
  // Conflict words: the badge shows the short phrase, the line under it says what to do.
  badgeNotFound: { en: "Not checked yet", ar: "لم يُفحص بعد" },
  badgeUndeclared: { en: "tool does not ask for confirmation", ar: "الأداة لا تطلب تأكيدًا" },
  badgeUnenforced: { en: "not yet seen blocked at runtime", ar: "لم يُرصد حجبه أثناء التشغيل بعد" },
  badgeEnforced: { en: "Enforced", ar: "مطبَّق" },
  noteNotFound: {
    en: "No matching tool has been discovered in this project yet, so this action cannot be checked.",
    ar: "لم تُكتشف أداة مطابقة في هذا المشروع بعد، ولذلك لا يمكن فحص هذا الإجراء.",
  },
  noteEnforced: { en: "Seen blocked at runtime.", ar: "شوهد حجبه أثناء التشغيل." },
  blockerUndeclared: {
    en: "the repository's tool definition does not require confirmation, so the mandate alone cannot declare this action safe",
    ar: "تعريف الأداة في المستودع لا يشترط التأكيد، ولذلك لا يكفي التفويض وحده للإعلان أن هذا الإجراء آمن",
  },
  blockerUnenforced: {
    en: "not yet seen blocked at runtime",
    ar: "لم يُرصد حجبه أثناء التشغيل بعد",
  },
  removeAction: { en: "Remove from mandate", ar: "إزالة من التفويض" },
  removeActionNamed: {
    en: "Remove from mandate: {name}",
    ar: "إزالة من التفويض: {name}",
  },
  addName: { en: "Action or tool name", ar: "اسم الإجراء أو الأداة" },
  addReason: { en: "Reason", ar: "السبب" },
  addButton: { en: "Add forbidden action", ar: "إضافة إجراء محظور" },
  addNameEmpty: { en: "Enter the name of the action or tool.", ar: "أدخل اسم الإجراء أو الأداة." },
  addNameDuplicate: {
    en: "“{name}” is already on the forbidden list. Enter a different name.",
    ar: "«{name}» موجود بالفعل في قائمة المحظورات. أدخل اسمًا مختلفًا.",
  },
  refresh: { en: "Refresh enforcement status", ar: "تحديث حالة التطبيق" },
  refreshing: { en: "Refreshing enforcement status…", ar: "جارٍ تحديث حالة التطبيق…" },
  refreshStale: {
    en: "The conflicts shown are the last known status.",
    ar: "التعارضات المعروضة هي آخر حالة معروفة.",
  },
  lockTitle: { en: "Lock readiness", ar: "جاهزية القفل" },
  lockReady: {
    en: "No unresolved mandate conflicts.",
    ar: "لا توجد تعارضات غير محلولة في التفويض.",
  },
  lockBlocked: {
    en: "Locking is blocked by {count} unresolved mandate conflict(s):",
    ar: "القفل متوقف بسبب {count} من التعارضات غير المحلولة في التفويض:",
  },
  save: { en: "Save mandate", ar: "حفظ التفويض" },
  saving: { en: "Saving…", ar: "جارٍ الحفظ…" },
  saveNothing: { en: "Change a field to enable saving.", ar: "غيّر حقلًا لتفعيل الحفظ." },
  saved: { en: "Mandate saved.", ar: "تم حفظ التفويض." },
  saveFailedKept: {
    en: "Your edits are still here and nothing was saved.",
    ar: "تعديلاتك ما زالت موجودة ولم يُحفظ شيء.",
  },
  unsaved: { en: "You have unsaved changes.", ar: "لديك تغييرات غير محفوظة." },
  discard: { en: "Discard edits", ar: "تجاهل التعديلات" },
  discardConfirm: {
    en: "Discard your unsaved edits? The mandate returns to the last saved version. This cannot be undone.",
    ar: "هل تريد تجاهل تعديلاتك غير المحفوظة؟ سيعود التفويض إلى آخر نسخة محفوظة. لا يمكن التراجع عن ذلك.",
  },
  discardKeep: { en: "Keep editing", ar: "متابعة التعديل" },
  // Field labels: people's words, never the snake_case key.
  business_purpose: { en: "Business purpose", ar: "الغرض من العمل" },
  tool_side_effect_confirmation_policy: {
    en: "Tool side-effect confirmation policy",
    ar: "سياسة تأكيد الآثار الجانبية للأدوات",
  },
  expected_outputs_and_schemas: {
    en: "Expected outputs and schemas",
    ar: "المخرجات والمخططات المتوقعة",
  },
  escalation_abstention_behavior: {
    en: "Escalation and abstention behavior",
    ar: "سلوك التصعيد والامتناع",
  },
  privacy_security_profile: { en: "Privacy and security profile", ar: "ملف الخصوصية والأمان" },
  allowed_responsibilities: { en: "Allowed responsibilities", ar: "المسؤوليات المسموح بها" },
  critical_behaviors: { en: "Critical behaviors", ar: "السلوكيات الحرجة" },
  required_tools: { en: "Required tools", ar: "الأدوات المطلوبة" },
  required_context_data_classes: {
    en: "Required context and data classes",
    ar: "السياق وفئات البيانات المطلوبة",
  },
  supported_languages: { en: "Supported languages", ar: "اللغات المدعومة" },
  critical_safety_reliability_gates: {
    en: "Critical safety and reliability gates",
    ar: "بوابات السلامة والموثوقية الحرجة",
  },
  latency_slo_ms: { en: "Latency target (milliseconds)", ar: "هدف زمن الاستجابة (بالمللي ثانية)" },
  throughput_concurrency_target: {
    en: "Throughput and concurrency target",
    ar: "هدف الإنتاجية والتزامن",
  },
  cost_per_success_ceiling: { en: "Cost-per-success ceiling", ar: "سقف التكلفة لكل نجاح" },
} as const satisfies Record<string, CatalogueEntry>;

export type MandateStringKey = keyof typeof STRINGS;
export const mandateT = makeT(STRINGS);
export const MANDATE_STRING_KEYS = Object.keys(STRINGS) as MandateStringKey[];
export const MANDATE_STRINGS: Record<MandateStringKey, CatalogueEntry> = STRINGS;
