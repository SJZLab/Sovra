/**
 * FTF-UX04: the English and Arabic system strings of the two project-mandate components
 * (FTF-0401 project form, FTF-0408 requirements panel) and the failure wording they share.
 * Standard system phrases only (CLAUDE.md "Do without asking"): labels, helper text, validation and
 * error messages, empty-state and loading text. Nothing here is brand, pricing or legal copy.
 */
import {
  failureMessage,
  makeT,
  type CatalogueEntry,
  type Failure,
} from "../target-application/common";
import type { Locale } from "../ui";

const WORDS = {
  // ---- FTF-0401 project form ---------------------------------------------------------------
  formTitleCreate: { en: "Create project", ar: "إنشاء مشروع" },
  formTitleEdit: { en: "Edit project", ar: "تعديل المشروع" },
  nameLabel: { en: "Project name", ar: "اسم المشروع" },
  nameHelper: {
    en: "Shown in lists and breadcrumbs. You can rename the project later.",
    ar: "يظهر في القوائم ومسار التصفح. يمكنك إعادة تسمية المشروع لاحقًا.",
  },
  nameRequired: { en: "Enter a project name.", ar: "أدخل اسم المشروع." },
  typeLabel: { en: "Project type", ar: "نوع المشروع" },
  typeHelper: {
    en: "An agentic product has an agent objective and needs a Target Application connection before it can go active. A generic project tunes a model only.",
    ar: "للمنتج الوكيلي هدف وكيل ويحتاج إلى اتصال بتطبيق مستهدف قبل أن يصبح نشطًا. المشروع العام يضبط نموذجًا فقط.",
  },
  typeLocked: {
    en: "The project type cannot be changed after the project is created.",
    ar: "لا يمكن تغيير نوع المشروع بعد إنشائه.",
  },
  typeGeneric: { en: "Generic model tuning", ar: "ضبط نموذج عام" },
  typeAgentic: { en: "Agentic product", ar: "منتج وكيلي" },
  objectiveLabel: { en: "Agent objective", ar: "هدف الوكيل" },
  objectiveHelper: {
    en: "One or two sentences on what the agent must do. It is the baseline the later steps are judged against.",
    ar: "جملة أو جملتان عمّا يجب أن يفعله الوكيل. وهو الأساس الذي تُقاس به الخطوات اللاحقة.",
  },
  objectiveRequired: {
    en: "Enter the agent objective: an agentic product needs one.",
    ar: "أدخل هدف الوكيل: المنتج الوكيلي يحتاج إلى هدف.",
  },
  descriptionLabel: { en: "Description", ar: "الوصف" },
  descriptionHelper: {
    en: "Optional context for your team.",
    ar: "سياق اختياري لفريقك.",
  },
  profileLabel: { en: "Security profile", ar: "ملف الأمان" },
  profileHelper: {
    en: "A project can be stricter than the organization default, never less strict.",
    ar: "يمكن أن يكون المشروع أكثر صرامة من الإعداد الافتراضي للمؤسسة، ولا يمكن أن يكون أقل صرامة.",
  },
  profileStandard: { en: "Standard", ar: "قياسي" },
  profileZeroEgress: { en: "Zero egress", ar: "بدون خروج بيانات" },
  profileAirGapped: { en: "Air-gapped", ar: "معزول تمامًا" },
  profileOther: { en: "{value} (not offered here)", ar: "{value} (غير متاح هنا)" },
  submitCreate: { en: "Create project", ar: "إنشاء المشروع" },
  submitSave: { en: "Save changes", ar: "حفظ التغييرات" },
  saveDraft: { en: "Save draft", ar: "حفظ المسودة" },
  saveDraftNeedsProject: {
    en: "Create the project first: a draft is saved onto an existing project.",
    ar: "أنشئ المشروع أولًا: تُحفظ المسودة على مشروع موجود.",
  },
  saving: { en: "Saving…", ar: "جارٍ الحفظ…" },
  savingDraft: { en: "Saving draft…", ar: "جارٍ حفظ المسودة…" },
  draftSaved: { en: "Draft saved", ar: "تم حفظ المسودة" },
  projectSaved: { en: "Changes saved", ar: "تم حفظ التغييرات" },
  projectCreated: { en: "Project created", ar: "تم إنشاء المشروع" },
  setupIncomplete: {
    en: "Setup incomplete: a Target Application connection and Agent Manifest are required before this project can go active.",
    ar: "الإعداد غير مكتمل: يلزم اتصال بتطبيق مستهدف وبيان وكيل قبل أن يصبح هذا المشروع نشطًا.",
  },
  archivedNotice: {
    en: "This project is archived and can no longer be edited.",
    ar: "هذا المشروع مؤرشف ولم يعد قابلًا للتعديل.",
  },
  draftFound: {
    en: "A saved draft with different values exists for this project.",
    ar: "توجد مسودة محفوظة بقيم مختلفة لهذا المشروع.",
  },
  draftRestore: { en: "Restore draft", ar: "استعادة المسودة" },
  draftRestored: {
    en: "Draft restored into the form. Nothing is saved until you save changes.",
    ar: "تمت استعادة المسودة في النموذج. لا يُحفظ شيء إلى أن تحفظ التغييرات.",
  },
  inputKept: { en: "Your entries are kept.", ar: "تم الاحتفاظ بما أدخلته." },

  // ---- FTF-0408 requirements panel ---------------------------------------------------------
  panelTitle: { en: "Project requirements", ar: "متطلبات المشروع" },
  securityHeading: { en: "Security profile", ar: "ملف الأمان" },
  securityEditNotice: {
    en: "Edited from the project setup form, not here.",
    ar: "يُعدَّل من نموذج إعداد المشروع، وليس من هنا.",
  },
  criticalHeading: { en: "Critical behavior requirements", ar: "متطلبات السلوك الحرج" },
  criticalCaption: {
    en: "Tools: declared requirement and runtime enforcement",
    ar: "الأدوات: المتطلب المعلن والتنفيذ وقت التشغيل",
  },
  colTool: { en: "Tool (side-effect class)", ar: "الأداة (فئة الأثر الجانبي)" },
  colDesired: { en: "Declared", ar: "المعلن" },
  colEnforced: { en: "Runtime", ar: "وقت التشغيل" },
  colConflict: { en: "Check", ar: "الفحص" },
  desiredCritical: { en: "Critical", ar: "حرجة" },
  desiredNonCritical: { en: "Non-critical", ar: "غير حرجة" },
  enforced: { en: "Enforced", ar: "منفَّذ" },
  notEnforced: { en: "Not enforced", ar: "غير منفَّذ" },
  consistent: { en: "Consistent", ar: "متسق" },
  conflict: {
    en: "Conflict: declared critical or requires confirmation, but not enforced at runtime.",
    ar: "تعارض: معلنة كحرجة أو تتطلب تأكيدًا، لكنها غير منفَّذة وقت التشغيل.",
  },
  requirementsLoading: { en: "Loading requirements…", ar: "جارٍ تحميل المتطلبات…" },
  requirementsEmptyTitle: {
    en: "No tools are defined for this project yet",
    ar: "لم يتم تعريف أي أدوات لهذا المشروع بعد",
  },
  requirementsEmptyWhy: {
    en: "Critical behavior requirements are read from the project's tool definitions. Define a tool to see whether it is enforced at runtime.",
    ar: "تُقرأ متطلبات السلوك الحرج من تعريفات أدوات المشروع. عرّف أداة لترى هل هي منفَّذة وقت التشغيل.",
  },
  requirementsEmptyAction: { en: "Reload", ar: "إعادة التحميل" },
  retry: { en: "Try again", ar: "حاول مرة أخرى" },
  requirementsLoadFailed: {
    en: "The requirements could not be loaded.",
    ar: "تعذّر تحميل المتطلبات.",
  },
  budgetHeading: { en: "Budget policy", ar: "سياسة الميزانية" },
  budgetLoading: { en: "Loading budget policy…", ar: "جارٍ تحميل سياسة الميزانية…" },
  budgetLoadFailed: {
    en: "The budget policy could not be loaded.",
    ar: "تعذّر تحميل سياسة الميزانية.",
  },
  budgetMissing: {
    en: "No budget policy was found for this project yet. Create one below.",
    ar: "لم يتم العثور على سياسة ميزانية لهذا المشروع بعد. أنشئ واحدة أدناه.",
  },
  rowVersion: { en: "Policy version", ar: "إصدار السياسة" },
  reserved: { en: "Reserved", ar: "المحجوز" },
  available: { en: "Available", ar: "المتاح" },
  totalLabel: { en: "Total amount", ar: "المبلغ الإجمالي" },
  totalHelper: {
    en: "A positive amount with up to 4 decimal places, for example 2500.50.",
    ar: "مبلغ موجب بحد أقصى 4 منازل عشرية، مثل 2500.50.",
  },
  totalRequired: { en: "Enter the total amount.", ar: "أدخل المبلغ الإجمالي." },
  totalInvalid: {
    en: "Enter a positive number with at most 4 decimal places, for example 2500.50.",
    ar: "أدخل رقمًا موجبًا بحد أقصى 4 منازل عشرية، مثل 2500.50.",
  },
  totalBelowReserved: {
    en: "The total cannot be less than the amount already reserved ({reserved} {currency}). Enter {reserved} or more.",
    ar: "لا يمكن أن يقل الإجمالي عن المبلغ المحجوز بالفعل ({reserved} {currency}). أدخل {reserved} أو أكثر.",
  },
  currencyLabel: { en: "Currency", ar: "العملة" },
  currencyLocked: {
    en: "The currency is fixed once the policy is created.",
    ar: "تُثبَّت العملة بمجرد إنشاء السياسة.",
  },
  scopesLegend: { en: "Scopes", ar: "النطاقات" },
  scopesHelper: {
    en: "Choose where this budget may be spent.",
    ar: "اختر الجهات التي يمكن صرف هذه الميزانية عليها.",
  },
  scopesRequired: { en: "Choose at least one scope.", ar: "اختر نطاقًا واحدًا على الأقل." },
  scopeTraining: { en: "Training", ar: "التدريب" },
  scopeEvaluation: { en: "Evaluation", ar: "التقييم" },
  scopeServing: { en: "Serving", ar: "التقديم" },
  budgetCreate: { en: "Create budget policy", ar: "إنشاء سياسة الميزانية" },
  budgetSave: { en: "Save changes", ar: "حفظ التغييرات" },
  budgetSaved: { en: "Budget policy saved", ar: "تم حفظ سياسة الميزانية" },
  budgetCreated: { en: "Budget policy created", ar: "تم إنشاء سياسة الميزانية" },
  budgetSaving: { en: "Saving budget policy…", ar: "جارٍ حفظ سياسة الميزانية…" },

  // ---- failures ---------------------------------------------------------------------------
  projectNotFound: {
    en: "This project no longer exists, or you do not have access to it. Go back to the project list and choose another one.",
    ar: "لم يعد هذا المشروع موجودًا، أو ليست لديك صلاحية الوصول إليه. ارجع إلى قائمة المشاريع واختر مشروعًا آخر.",
  },
  projectConflict: {
    en: "The project changed since you opened it, or it is archived. Reload to see the current version; your entries stay in the form until you do.",
    ar: "تغيّر المشروع منذ فتحته، أو أنه مؤرشف. أعد التحميل لرؤية النسخة الحالية؛ تبقى مدخلاتك في النموذج إلى أن تفعل ذلك.",
  },
  projectInvalid: {
    en: "The server did not accept some values. Check the name, objective and description, and that the security profile is not less strict than your organization's default, then try again.",
    ar: "لم يقبل الخادم بعض القيم. تحقق من الاسم والهدف والوصف ومن أن ملف الأمان ليس أقل صرامة من الإعداد الافتراضي لمؤسستك ثم حاول مرة أخرى.",
  },
  budgetNotFound: {
    en: "The project was not found, or you do not have access to it. Go back to the project list and choose another one.",
    ar: "لم يتم العثور على المشروع، أو ليست لديك صلاحية الوصول إليه. ارجع إلى قائمة المشاريع واختر مشروعًا آخر.",
  },
  budgetConflict: {
    en: "The budget policy changed since you opened it, or already exists. Reload to see the current policy; your entries stay in the form until you do.",
    ar: "تغيّرت سياسة الميزانية منذ فتحتها، أو أنها موجودة بالفعل. أعد التحميل لرؤية السياسة الحالية؛ تبقى مدخلاتك في النموذج إلى أن تفعل ذلك.",
  },
  budgetInvalid: {
    en: "The server did not accept some values. Check that the total is positive, is not below the reserved amount, and that at least one scope is chosen, then try again.",
    ar: "لم يقبل الخادم بعض القيم. تحقق من أن الإجمالي موجب وليس أقل من المبلغ المحجوز وأنه تم اختيار نطاق واحد على الأقل ثم حاول مرة أخرى.",
  },
  otherFailure: {
    en: "The request failed ({status}). Try again; if it keeps happening, contact your administrator.",
    ar: "فشل الطلب ({status}). حاول مرة أخرى، وإذا استمرت المشكلة فتواصل مع المسؤول.",
  },
  reload: { en: "Reload", ar: "إعادة التحميل" },
} as const satisfies Record<string, CatalogueEntry>;

export type MandateWord = keyof typeof WORDS;

/** Lookup over this feature's catalogue; an unshipped locale renders English. */
export const tm = makeT(WORDS);

export type MandateArea = "project" | "budget";

/**
 * The sentence for a failed request, never the backend's own `detail`: the project and budget routes
 * answer with internal codes (`not_found`, `project_not_found`) and English developer sentences that
 * carry ids (`project <uuid> has row_version 4, not the expected 3`), none of which a user can act on
 * or read in Arabic. Each status gets a localized sentence that says what happened and the step that
 * can succeed (NN/g H9). 401/403/5xx and transport failures reuse the shared wording.
 */
export function mandateFailureMessage(
  failure: Failure,
  area: MandateArea,
  locale: Locale | string = "en",
): string {
  if (failure.kind !== "http") return failureMessage(failure, locale);
  switch (failure.status) {
    case 401:
    case 403:
    case 502:
    case 503:
    case 504:
      return failureMessage({ kind: "http", status: failure.status, detail: null }, locale);
    case 404:
      return tm(area === "project" ? "projectNotFound" : "budgetNotFound", locale);
    case 409:
      return tm(area === "project" ? "projectConflict" : "budgetConflict", locale);
    case 400:
    case 422:
      return tm(area === "project" ? "projectInvalid" : "budgetInvalid", locale);
    default:
      return failure.status >= 500
        ? failureMessage({ kind: "http", status: failure.status, detail: null }, locale)
        : tm("otherFailure", locale, { status: failure.status });
  }
}
