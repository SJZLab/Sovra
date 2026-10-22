/** FTF-04A21 (manifest review) system strings in English and Arabic (FTF-UX03 r2). */
import { makeT, type CatalogueEntry } from "./common";

const S = {
  title: { en: "Agent manifest review", ar: "مراجعة بيان الوكيل" },
  loading: { en: "Loading manifest…", ar: "جارٍ تحميل البيان…" },
  retry: { en: "Try again", ar: "حاول مرة أخرى" },
  statusLabel: { en: "Status", ar: "الحالة" },
  versionLabel: { en: "Version", ar: "الإصدار" },
  hashLabel: { en: "Locked hash", ar: "بصمة القفل" },
  statusDraft: { en: "Draft", ar: "مسودة" },
  statusReviewRequired: { en: "In review", ar: "قيد المراجعة" },
  statusLocked: { en: "Locked", ar: "مقفل" },
  statusSuperseded: { en: "Superseded", ar: "تم استبداله" },
  readOnlyLocked: {
    en: "This manifest is locked and can no longer be edited.",
    ar: "هذا البيان مقفل ولا يمكن تعديله بعد الآن.",
  },
  readOnlySuperseded: {
    en: "This manifest was superseded by a newer one and can no longer be edited.",
    ar: "تم استبدال هذا البيان ببيان أحدث ولا يمكن تعديله بعد الآن.",
  },
  restored: {
    en: "Restored your unsaved edits from this browser. They are not saved to the server yet.",
    ar: "تمت استعادة تعديلاتك غير المحفوظة من هذا المتصفح. لم تُحفظ على الخادم بعد.",
  },
  modelRole: { en: "Current model role", ar: "دور النموذج الحالي" },
  modelRoleHelp: {
    en: "Pick the explicit role this model plays; a manifest cannot be locked without one.",
    ar: "اختر الدور الصريح الذي يؤديه هذا النموذج؛ لا يمكن قفل البيان بدونه.",
  },
  roleNotSet: { en: "Not set", ar: "غير محدد" },
  roleCurrentProduction: { en: "Current production", ar: "الإنتاج الحالي" },
  roleBaseline: { en: "Baseline", ar: "خط الأساس" },
  roleCandidate: { en: "Candidate", ar: "مرشح" },
  roleTeacher: { en: "Teacher", ar: "المعلّم" },
  roleJudge: { en: "Judge", ar: "المحكّم" },
  stateClean: { en: "No changes", ar: "بدون تغييرات" },
  stateUnsaved: { en: "Unsaved changes", ar: "تعديلات غير محفوظة" },
  stateInvalid: { en: "Invalid JSON", ar: "JSON غير صالح" },
  stateConflict: { en: "Conflict", ar: "تعارض" },
  invalidJson: {
    en: "This is not valid JSON. Fix the syntax, for example a missing quote or bracket, to be able to save.",
    ar: "هذا ليس JSON صالحًا. أصلح الصياغة، مثل علامة اقتباس أو قوس ناقص، لتتمكن من الحفظ.",
  },
  requiredToLock: { en: "(required to lock)", ar: "(مطلوب للقفل)" },
  overridden: {
    en: "Overridden by you; not backed by detected evidence until saved and reviewed.",
    ar: "تم تجاوزه بواسطتك؛ ولا تدعمه أدلة مكتشفة إلى أن يُحفظ ويُراجَع.",
  },
  diffHeading: {
    en: "Changes against the saved value",
    ar: "التغييرات مقارنة بالقيمة المحفوظة",
  },
  conflictBody: {
    en: "This field changed on the server after you started editing it. Choose which version to keep.",
    ar: "تغيّر هذا الحقل على الخادم بعد أن بدأت تعديله. اختر النسخة التي تريد الاحتفاظ بها.",
  },
  serverVersion: { en: "Server version", ar: "نسخة الخادم" },
  yourVersion: { en: "Your version", ar: "نسختك" },
  keepMine: { en: "Keep my version", ar: "الاحتفاظ بنسختي" },
  keepMineFor: { en: "Keep my version of {field}", ar: "الاحتفاظ بنسختي من {field}" },
  takeServer: { en: "Use the server's version", ar: "استخدام نسخة الخادم" },
  takeServerFor: {
    en: "Use the server's version of {field}",
    ar: "استخدام نسخة الخادم من {field}",
  },
  evidenceHeading: { en: "Evidence", ar: "الأدلة" },
  noEvidence: { en: "No recorded evidence", ar: "لا توجد أدلة مسجلة" },
  confidence: { en: "Confidence: {score}", ar: "الثقة: {score}" },
  confidenceNA: { en: "Confidence: not available", ar: "الثقة: غير متوفرة" },
  commit: { en: "commit", ar: "الإيداع" },
  evRepoFile: { en: "Repository file", ar: "ملف مستودع" },
  evRepoSymbol: { en: "Repository symbol", ar: "رمز مستودع" },
  evConfig: { en: "Configuration", ar: "إعدادات" },
  evTest: { en: "Test", ar: "اختبار" },
  evRuntimeProbe: { en: "Runtime probe", ar: "فحص وقت التشغيل" },
  evUserInput: { en: "Entered by a user", ar: "أدخله مستخدم" },
  saveDraft: { en: "Save draft", ar: "حفظ المسودة" },
  submitForReview: { en: "Submit for review", ar: "إرسال للمراجعة" },
  lockManifest: { en: "Lock manifest", ar: "قفل البيان" },
  whySaveReadOnly: {
    en: "A locked or superseded manifest cannot be edited",
    ar: "لا يمكن تعديل بيان مقفل أو مستبدل",
  },
  whySaveBlocked: {
    en: "Resolve the conflicts and invalid JSON first",
    ar: "عالج التعارضات وأخطاء JSON أولًا",
  },
  whySaveNothing: { en: "There are no unsaved changes", ar: "لا توجد تعديلات غير محفوظة" },
  whySubmitStatus: {
    en: "Only a draft can be submitted for review",
    ar: "يمكن إرسال المسودة فقط للمراجعة",
  },
  whySubmitUnsaved: {
    en: "Save your changes before submitting",
    ar: "احفظ تعديلاتك قبل الإرسال",
  },
  whyLockBlocked: {
    en: "Finish the items listed below first",
    ar: "أنجز العناصر المدرجة أدناه أولًا",
  },
  blockersHeading: {
    en: "Before this manifest can be locked:",
    ar: "قبل أن يمكن قفل هذا البيان:",
  },
  blockNeedsReview: {
    en: "Submit the manifest for review first.",
    ar: "أرسل البيان للمراجعة أولًا.",
  },
  blockConflict: {
    en: "{field}: unresolved conflict. Choose a version.",
    ar: "{field}: تعارض غير محسوم. اختر نسخة.",
  },
  blockInvalid: {
    en: "{field}: the JSON is not valid.",
    ar: "{field}: بنية JSON غير صالحة.",
  },
  blockUnsaved: {
    en: "{field}: has unsaved changes. Save the draft.",
    ar: "{field}: به تعديلات غير محفوظة. احفظ المسودة.",
  },
  blockRequired: {
    en: "{field}: required before locking.",
    ar: "{field}: مطلوب قبل القفل.",
  },
  blockBindingObject: {
    en: "The current model binding must be a JSON object.",
    ar: "يجب أن يكون ربط النموذج الحالي كائن JSON.",
  },
  blockBindingUnresolved: {
    en: "The current model binding is unresolved.",
    ar: "ربط النموذج الحالي غير محسوم.",
  },
  blockRole: {
    en: "Choose an explicit role for the current model.",
    ar: "اختر دورًا صريحًا للنموذج الحالي.",
  },
  saving: { en: "Saving draft…", ar: "جارٍ حفظ المسودة…" },
  submitting: { en: "Submitting for review…", ar: "جارٍ الإرسال للمراجعة…" },
  locking: { en: "Locking manifest…", ar: "جارٍ قفل البيان…" },
  savedOk: { en: "Draft saved.", ar: "تم حفظ المسودة." },
  submittedOk: { en: "Submitted for review.", ar: "تم الإرسال للمراجعة." },
  lockedOk: { en: "Manifest locked.", ar: "تم قفل البيان." },
  notBound: {
    en: "The server's reply does not match what was sent, so nothing was marked as done. Reload the page and check the values.",
    ar: "رد الخادم لا يطابق ما أُرسل، لذلك لم يُعتبر أي شيء منجزًا. أعد تحميل الصفحة وتحقق من القيم.",
  },
  fieldAgentName: { en: "Agent name", ar: "اسم الوكيل" },
  fieldAgentEntrypoint: { en: "Agent entrypoint", ar: "نقطة دخول الوكيل" },
  fieldModelBinding: { en: "Current model binding", ar: "ربط النموذج الحالي" },
  fieldSystemPromptRefs: { en: "System prompt references", ar: "مراجع موجّه النظام" },
  fieldToolContracts: { en: "Tool contracts", ar: "عقود الأدوات" },
  fieldInputContract: { en: "Input contract", ar: "عقد المدخلات" },
  fieldOutputContract: { en: "Output contract", ar: "عقد المخرجات" },
  fieldMandate: { en: "Mandate", ar: "التفويض" },
  fieldForbiddenActions: { en: "Forbidden actions", ar: "الإجراءات المحظورة" },
  fieldPermissionModel: { en: "Permission model", ar: "نموذج الصلاحيات" },
  fieldRequiredContext: { en: "Required context", ar: "السياق المطلوب" },
  fieldRuntimeDependencies: { en: "Runtime dependencies", ar: "تبعيات التشغيل" },
  fieldEvaluationHarness: { en: "Evaluation harness", ar: "إطار التقييم" },
  fieldTraceSource: { en: "Trace source contract", ar: "عقد مصدر التتبع" },
  fieldDeploymentContract: { en: "Deployment contract", ar: "عقد النشر" },
} as const satisfies Record<string, CatalogueEntry>;

export type Key04a21 = keyof typeof S;
export const t04a21 = makeT<Key04a21>(S);

/** Manifest field name -> its catalogue key. Anything not listed shows its own name. */
const FIELD_KEY: Record<string, Key04a21> = {
  agent_name: "fieldAgentName",
  agent_entrypoint_json: "fieldAgentEntrypoint",
  current_model_binding_json: "fieldModelBinding",
  system_prompt_refs_json: "fieldSystemPromptRefs",
  tool_contracts_json: "fieldToolContracts",
  input_contract_json: "fieldInputContract",
  output_contract_json: "fieldOutputContract",
  mandate_json: "fieldMandate",
  forbidden_actions_json: "fieldForbiddenActions",
  permission_model_json: "fieldPermissionModel",
  required_context_json: "fieldRequiredContext",
  runtime_dependencies_json: "fieldRuntimeDependencies",
  evaluation_harness_json: "fieldEvaluationHarness",
  trace_source_contract_json: "fieldTraceSource",
  deployment_contract_json: "fieldDeploymentContract",
};

export function fieldLabel(field: string, locale: string): string {
  return Object.hasOwn(FIELD_KEY, field) ? t04a21(FIELD_KEY[field]!, locale) : field;
}

const ROLE_KEY: Record<string, Key04a21> = {
  current_production: "roleCurrentProduction",
  baseline: "roleBaseline",
  candidate: "roleCandidate",
  teacher: "roleTeacher",
  judge: "roleJudge",
};

export function roleLabel(role: string, locale: string): string {
  return Object.hasOwn(ROLE_KEY, role) ? t04a21(ROLE_KEY[role]!, locale) : role;
}

const EVIDENCE_KEY: Record<string, Key04a21> = {
  repo_file: "evRepoFile",
  repo_symbol: "evRepoSymbol",
  config: "evConfig",
  test: "evTest",
  runtime_probe: "evRuntimeProbe",
  user_input: "evUserInput",
};

export function evidenceTypeLabel(type: string, locale: string): string {
  return Object.hasOwn(EVIDENCE_KEY, type) ? t04a21(EVIDENCE_KEY[type]!, locale) : type;
}
